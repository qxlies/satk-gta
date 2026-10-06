// saap_server.hpp - a game-independent SAAP/1 TCP server for satk_sp.asi (and its host-side test).
//
// MIT License. Copyright (c) 2026 satk contributors. Header-only, C++14, Windows (winsock2).
//
// This is the transport + envelope + auth + dispatch layer, with no dependency on gta_sa.exe:
// it talks framing through saap_frame.hpp, JSON through saap_json.hpp, and routes every request
// to a Backend. The same object serves the real ASI (pumped once per game frame on the main
// thread) and the console test harness (pumped in a loop), so the protocol is tested without the
// game. Game behaviour lives entirely behind the Backend interface (sp_backend.* / mock_backend).
//
// Model (SAAP-v1.md sections 1-5): 127.0.0.1 only; one request in flight per connection; the
// first request on a connection must be `hello` with a valid token (constant-time compare), else
// AUTH and the connection is closed; bad frames/JSON -> PROTOCOL and close; unknown method ->
// UNKNOWN_METHOD (connection kept). pump() is non-blocking and processes at most one request per
// connection per call, so a frame-hook budget stays small.

#ifndef SAAP_SERVER_HPP
#define SAAP_SERVER_HPP

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <winsock2.h>
#include <ws2tcpip.h>

#include <cstdint>
#include <ctime>
#include <string>
#include <vector>

#include "saap_frame.hpp"
#include "saap_json.hpp"

#pragma comment(lib, "ws2_32.lib")

namespace saap {

struct Error {
    std::string code;     // AUTH, PROTOCOL, UNKNOWN_METHOD, BAD_PARAMS, UNSUPPORTED, NOT_READY,
                          // BUSY, TIMEOUT, NOT_FOUND, REVISION, INTERNAL
    std::string message;
    bool retryable = false;
    json::Value data = json::Value::object();

    static Error make(const char* c, const std::string& m, bool retry = false) {
        Error e; e.code = c; e.message = m; e.retryable = retry; return e;
    }
};

// What the server needs from the game (or the mock). All calls run on the pump thread.
class Backend {
public:
    virtual ~Backend() {}

    // hello result body (role, impl, impl_version, build, caps, game, world, viewport); the server
    // adds `saap` and `limits`. caps must list every capability this endpoint serves.
    virtual json::Value describe() = 0;

    // Handle a method (not `hello`). Return true and fill `result`, or return false and fill
    // `err`. `quit` should set `result = {}` and request shutdown via wants_quit().
    virtual bool call(const std::string& method, const json::Value& params,
                      json::Value& result, Error& err) = 0;

    // Called once per pump, before reading sockets (log capture, streaming settle bookkeeping).
    virtual void on_frame() {}

    // Monotonic clock in milliseconds (for ping/meta). Default: steady wall clock.
    virtual double now_ms() { return (double)clock() * 1000.0 / (double)CLOCKS_PER_SEC; }

    // Endpoint frame counter for `meta.frame` (default 0 = unknown).
    virtual std::uint32_t frame() { return 0; }

    bool wants_quit() const { return quit_; }
    void request_quit() { quit_ = true; }

private:
    bool quit_ = false;
};

// Light param helpers backends can use for BAD_PARAMS checks.
namespace check {
inline bool is_vec3(const json::Value& v) {
    if (!v.is_array() || v.size() != 3) return false;
    for (const auto& e : v.arr()) if (!e.is_number()) return false;
    return true;
}
inline bool require_object(const json::Value& params, Error& err) {
    if (!params.is_object()) { err = Error::make("BAD_PARAMS", "params must be an object"); return false; }
    return true;
}
}  // namespace check

class Server {
public:
    explicit Server(Backend& backend) : backend_(backend) {}
    ~Server() { close(); }

    // Bind 127.0.0.1:port (0 = ephemeral). token: required unless insecure (tests only).
    bool listen(std::uint16_t port, const std::string& token, bool insecure, std::string& why) {
        if (!insecure && !token_shape_ok(token)) { why = "token must be 32..256 bytes without CR/LF"; return false; }
        token_ = token;
        insecure_ = insecure;
        WSADATA wsa;
        if (!wsa_started_ && WSAStartup(MAKEWORD(2, 2), &wsa) != 0) { why = "WSAStartup failed"; return false; }
        wsa_started_ = true;
        listener_ = ::socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
        if (listener_ == INVALID_SOCKET) { why = "socket() failed"; return false; }
        BOOL yes = TRUE;
        setsockopt(listener_, SOL_SOCKET, SO_REUSEADDR, (const char*)&yes, sizeof yes);
        sockaddr_in a{};
        a.sin_family = AF_INET;
        a.sin_addr.s_addr = htonl(INADDR_LOOPBACK);  // 127.0.0.1 only
        a.sin_port = htons(port);
        if (::bind(listener_, (sockaddr*)&a, sizeof a) == SOCKET_ERROR) { why = "bind() failed"; close(); return false; }
        if (::listen(listener_, 8) == SOCKET_ERROR) { why = "listen() failed"; close(); return false; }
        sockaddr_in got{};
        int glen = sizeof got;
        if (getsockname(listener_, (sockaddr*)&got, &glen) == 0) port_ = ntohs(got.sin_port);
        set_nonblocking(listener_);
        return true;
    }

    std::uint16_t port() const { return port_; }

    // Non-blocking: accept new connections, read ready ones, handle at most one request each.
    void pump() {
        if (listener_ == INVALID_SOCKET) return;
        backend_.on_frame();
        accept_new();
        for (std::size_t i = 0; i < conns_.size();) {
            Conn& c = conns_[i];
            if (!service(c)) { drop(i); } else { ++i; }
        }
    }

    void close() {
        for (auto& c : conns_) if (c.sock != INVALID_SOCKET) closesocket(c.sock);
        conns_.clear();
        if (listener_ != INVALID_SOCKET) { closesocket(listener_); listener_ = INVALID_SOCKET; }
    }

private:
    struct Conn {
        SOCKET sock = INVALID_SOCKET;
        FrameReader reader{kMaxRequest};
        bool authed = false;
        bool closing = false;  // error sent; drain then drop
        double close_deadline = 0.0;
    };

    static void set_nonblocking(SOCKET s) { u_long nb = 1; ioctlsocket(s, FIONBIO, &nb); }

    void accept_new() {
        for (;;) {
            SOCKET s = ::accept(listener_, nullptr, nullptr);
            if (s == INVALID_SOCKET) break;
            set_nonblocking(s);
            BOOL one = TRUE;
            setsockopt(s, IPPROTO_TCP, TCP_NODELAY, (const char*)&one, sizeof one);
            Conn c;
            c.sock = s;
            conns_.push_back(std::move(c));
        }
    }

    void drop(std::size_t i) {
        if (conns_[i].sock != INVALID_SOCKET) closesocket(conns_[i].sock);
        conns_.erase(conns_.begin() + i);
    }

    // Returns false when the connection should be dropped.
    bool service(Conn& c) {
        char buf[16384];
        for (;;) {
            int n = recv(c.sock, buf, sizeof buf, 0);
            if (n > 0) {
                if (c.closing) continue;  // draining before close
                c.reader.feed(buf, (std::size_t)n);
            } else if (n == 0) {
                return false;  // peer closed
            } else {
                const int e = WSAGetLastError();
                if (e == WSAEWOULDBLOCK) break;
                return false;  // error
            }
            if (c.reader.failed()) break;
        }
        if (c.closing) {
            // give the client a moment to read our error frame, then drop
            return backend_.now_ms() < c.close_deadline;
        }
        if (c.reader.failed()) {
            send_error(c, "", Error::make("PROTOCOL", "bad frame"));
            begin_close(c);
            return true;
        }
        std::string payload;
        FrameReader::Status st = c.reader.next(payload);
        if (st == FrameReader::kFrame) {
            handle_request(c, payload);
            return !c.closing ? true : true;  // closing handled on next pump
        }
        if (st == FrameReader::kNeedMore) return true;
        // kTooLarge / kEmpty
        send_error(c, "", Error::make("PROTOCOL", "oversize or empty frame"));
        begin_close(c);
        return true;
    }

    void begin_close(Conn& c) {
        c.closing = true;
        c.close_deadline = backend_.now_ms() + 2000.0;
        shutdown(c.sock, SD_SEND);
    }

    void handle_request(Conn& c, const std::string& payload) {
        json::Value env;
        if (!json::parse(payload, env) || !env.is_object()) {
            send_error(c, "", Error::make("PROTOCOL", "payload is not a JSON object"));
            begin_close(c);
            return;
        }
        const json::Value& saap = env["saap"];
        if (!(saap.is_int() && saap.as_int() == 1)) {
            send_error(c, "", Error::make("PROTOCOL", "missing or wrong 'saap' version"));
            begin_close(c);
            return;
        }
        const json::Value& idv = env["id"];
        if (!idv.is_string() || idv.as_string().empty() || idv.as_string().size() > 64) {
            send_error(c, "", Error::make("PROTOCOL", "missing or invalid 'id'"));
            begin_close(c);
            return;
        }
        const std::string id = idv.as_string();
        const json::Value& methv = env["method"];
        if (!methv.is_string() || methv.as_string().empty() || methv.as_string().size() > 64) {
            send_error(c, id, Error::make("PROTOCOL", "missing or invalid 'method'"));
            begin_close(c);
            return;
        }
        const std::string method = methv.as_string();
        json::Value params = env.has("params") ? env["params"] : json::Value::object();
        if (env.has("params") && !params.is_object()) {
            send_error(c, id, Error::make("BAD_PARAMS", "'params' must be an object"));
            return;
        }

        if (!c.authed) {
            if (method != "hello") { send_error(c, id, Error::make("AUTH", "first request must be hello")); begin_close(c); return; }
            const json::Value& tok = params["token"];
            const std::string given = tok.as_string();
            const bool ok = insecure_ ? token_shape_ok(given) : (token_shape_ok(given) && token_equal(token_, given));
            if (!ok) { send_error(c, id, Error::make("AUTH", "bad token")); begin_close(c); return; }
            c.authed = true;
            send_result(c, id, hello_result());
            return;
        }
        if (method == "hello") { send_result(c, id, hello_result()); return; }

        json::Value result;
        Error err;
        if (backend_.call(method, params, result, err)) {
            send_result(c, id, result);
            if (backend_.wants_quit()) { /* let the response flush; host loop checks wants_quit */ }
        } else {
            if (err.code.empty()) err = Error::make("INTERNAL", "backend returned no error");
            send_error(c, id, err);
        }
    }

    json::Value hello_result() {
        json::Value r = backend_.describe();
        if (!r.is_object()) r = json::Value::object();
        r.set("saap", json::Value((std::int64_t)1));
        json::Value limits = json::Value::object();
        limits.set("max_request", json::Value((std::int64_t)kMaxRequest));
        limits.set("max_response", json::Value((std::int64_t)kMaxResponse));
        r.set("limits", limits);
        return r;
    }

    void send_result(Conn& c, const std::string& id, const json::Value& result) {
        json::Value resp = json::Value::object();
        resp.set("saap", json::Value((std::int64_t)1));
        resp.set("id", json::Value(id));
        resp.set("ok", json::Value(true));
        resp.set("result", result.is_null() ? json::Value::object() : result);
        resp.set("meta", meta());
        send_payload(c, resp.dump());
    }

    void send_error(Conn& c, const std::string& id, const Error& err) {
        json::Value e = json::Value::object();
        e.set("code", json::Value(err.code));
        e.set("message", json::Value(err.message));
        e.set("retryable", json::Value(err.retryable));
        e.set("data", err.data.is_null() ? json::Value::object() : err.data);
        json::Value resp = json::Value::object();
        resp.set("saap", json::Value((std::int64_t)1));
        resp.set("id", json::Value(id));
        resp.set("ok", json::Value(false));
        resp.set("error", e);
        resp.set("meta", meta());
        send_payload(c, resp.dump());
    }

    json::Value meta() {
        json::Value m = json::Value::object();
        m.set("frame", json::Value((std::int64_t)backend_.frame()));
        return m;
    }

    void send_payload(Conn& c, const std::string& payload) {
        std::string frame;
        if (!encode_frame(payload, frame)) return;  // oversize response: drop silently
        std::size_t off = 0;
        while (off < frame.size()) {
            int n = send(c.sock, frame.data() + off, (int)(frame.size() - off), 0);
            if (n == SOCKET_ERROR) {
                if (WSAGetLastError() == WSAEWOULDBLOCK) continue;  // small frames: spin briefly
                return;
            }
            off += (std::size_t)n;
        }
    }

    Backend& backend_;
    SOCKET listener_ = INVALID_SOCKET;
    std::uint16_t port_ = 0;
    std::string token_;
    bool insecure_ = false;
    bool wsa_started_ = false;
    std::vector<Conn> conns_;
};

}  // namespace saap

#endif  // SAAP_SERVER_HPP
