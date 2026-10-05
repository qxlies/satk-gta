// saap_frame.hpp — SAAP/1 framing helpers (San Andreas Agent Protocol, tools/proto/SAAP-v1.md).
//
// MIT License. Copyright (c) 2026 satk contributors.
//
// Permission is hereby granted, free of charge, to any person obtaining a copy of this software
// and associated documentation files (the "Software"), to deal in the Software without
// restriction, including without limitation the rights to use, copy, modify, merge, publish,
// distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the
// Software is furnished to do so, subject to the following conditions: The above copyright
// notice and this permission notice shall be included in all copies or substantial portions of
// the Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND.
//
// Header-only, C++14, no dependencies beyond the standard library (plus <windows.h> for the
// atomic file replace on Windows). Vendored into the Ariane fork and the MTA fork.
// JSON parsing is left to the host (nlohmann/json, json-c, ...).
//
// What it provides:
//   * limits (kMaxRequest = 1 MiB, kMaxResponse = 8 MiB) and the 4-byte big-endian header;
//   * saap::FrameReader — incremental, allocation-bounded reader for non-blocking sockets:
//     feed() whatever recv() returned, take frames out with next();
//   * saap::encode_frame() — header + payload, refusing empty/oversize payloads;
//   * saap::token_shape_ok() / saap::token_equal() — token rules and constant-time compare;
//   * saap::write_file_atomic() — write a descriptor file via temp file + rename/replace;
//   * saap::json_escape() — minimal JSON string escaping for hand-written replies.
//
// Typical endpoint loop (main thread, zero-timeout select):
//
//   saap::FrameReader rd(saap::kMaxRequest);
//   char buf[16384]; int n = recv(s, buf, sizeof buf, 0);
//   if (n > 0) rd.feed(buf, (size_t)n);
//   std::string payload;
//   switch (rd.next(payload)) {
//     case saap::FrameReader::kFrame:    handle(payload); break;
//     case saap::FrameReader::kNeedMore: break;
//     case saap::FrameReader::kTooLarge: reply_error("PROTOCOL"); linger_close(s); break;
//     case saap::FrameReader::kEmpty:    reply_error("PROTOCOL"); linger_close(s); break;
//   }
//
// linger_close (SAAP-v1.md §1): shutdown(s, SD_SEND), then keep recv()-ing and discarding on
// later ticks (<= 4 MiB, <= 2 s, until 0.25 s of silence or EOF), then closesocket(s). A plain
// closesocket() with unread bytes (the body of an oversize frame) sends RST and the client
// never sees the PROTOCOL reply.

#ifndef SAAP_FRAME_HPP
#define SAAP_FRAME_HPP

#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <string>

#ifdef _WIN32
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#define SAAP_UNDEF_LEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#define SAAP_UNDEF_NOMINMAX
#endif
#include <windows.h>
#ifdef SAAP_UNDEF_LEAN
#undef WIN32_LEAN_AND_MEAN
#undef SAAP_UNDEF_LEAN
#endif
#ifdef SAAP_UNDEF_NOMINMAX
#undef NOMINMAX
#undef SAAP_UNDEF_NOMINMAX
#endif
#else
#include <cstdlib>
#include <unistd.h>
#endif

namespace saap {

static const int kVersion = 1;
static const std::size_t kHeaderSize = 4;
static const std::size_t kMaxRequest = 1024u * 1024u;       // 1 MiB
static const std::size_t kMaxResponse = 8u * 1024u * 1024u; // 8 MiB
static const std::size_t kMinToken = 32;
static const std::size_t kMaxToken = 256;

// --- header -----------------------------------------------------------------------------------

inline void put_u32be(unsigned char out[4], std::uint32_t n) {
    out[0] = (unsigned char)((n >> 24) & 0xFFu);
    out[1] = (unsigned char)((n >> 16) & 0xFFu);
    out[2] = (unsigned char)((n >> 8) & 0xFFu);
    out[3] = (unsigned char)(n & 0xFFu);
}

inline std::uint32_t get_u32be(const unsigned char in[4]) {
    return ((std::uint32_t)in[0] << 24) | ((std::uint32_t)in[1] << 16) |
           ((std::uint32_t)in[2] << 8) | (std::uint32_t)in[3];
}

// Header + payload. Returns false (out untouched) for an empty payload or one above `limit`.
inline bool encode_frame(const std::string &payload, std::string &out, std::size_t limit = kMaxResponse) {
    if (payload.empty() || payload.size() > limit || payload.size() > 0xFFFFFFFFu)
        return false;
    unsigned char h[4];
    put_u32be(h, (std::uint32_t)payload.size());
    std::string frame;
    frame.reserve(kHeaderSize + payload.size());
    frame.append((const char *)h, kHeaderSize);
    frame.append(payload);
    out.swap(frame);
    return true;
}

// --- incremental reader -----------------------------------------------------------------------

class FrameReader {
public:
    enum Status { kNeedMore = 0, kFrame = 1, kTooLarge = 2, kEmpty = 3 };

    explicit FrameReader(std::size_t limit = kMaxRequest) : limit_(limit), want_(0), failed_(false) {}

    // Append received bytes. A bad header fails the reader at once, so memory stays bounded by
    // one header + `limit` payload bytes (+ one more frame, which protocol-abiding clients never
    // send: one request in flight per connection). Stop reading after an error status.
    void feed(const char *data, std::size_t n) {
        if (failed_ || n == 0)
            return;
        buf_.append(data, n);
        if (buf_.size() >= kHeaderSize) {
            const std::uint32_t a = get_u32be((const unsigned char *)buf_.data());
            if (a == 0)
                fail(kEmpty);
            else if ((std::size_t)a > limit_ || buf_.size() > 2 * (kHeaderSize + limit_))
                fail(kTooLarge);
        }
    }

    // Takes the next complete frame payload. After kTooLarge/kEmpty the reader stays failed:
    // answer PROTOCOL and close the connection.
    Status next(std::string &payload) {
        if (failed_)
            return status_;
        if (buf_.size() < kHeaderSize)
            return kNeedMore;
        const std::uint32_t n = get_u32be((const unsigned char *)buf_.data());
        if (n == 0)
            return fail(kEmpty);
        if ((std::size_t)n > limit_)
            return fail(kTooLarge);
        want_ = (std::size_t)n;
        if (buf_.size() < kHeaderSize + want_)
            return kNeedMore;
        payload.assign(buf_, kHeaderSize, want_);
        buf_.erase(0, kHeaderSize + want_);
        want_ = 0;
        return kFrame;
    }

    // Bytes still needed for the current frame (0 when unknown/complete). Useful to size recv().
    std::size_t missing() const {
        if (buf_.size() < kHeaderSize)
            return kHeaderSize - buf_.size();
        const std::uint32_t n = get_u32be((const unsigned char *)buf_.data());
        const std::size_t total = kHeaderSize + (std::size_t)n;
        return buf_.size() >= total ? 0 : total - buf_.size();
    }

    // Announced length of the frame being read (0 if the header is incomplete).
    std::uint32_t announced() const {
        return buf_.size() < kHeaderSize ? 0 : get_u32be((const unsigned char *)buf_.data());
    }

    std::size_t buffered() const { return buf_.size(); }
    bool failed() const { return failed_; }
    void reset() { buf_.clear(); want_ = 0; failed_ = false; }

private:
    Status fail(Status s) {
        failed_ = true;
        status_ = s;
        buf_.clear();
        return s;
    }

    std::string buf_;
    std::size_t limit_;
    std::size_t want_;
    bool failed_;
    Status status_ = kNeedMore;
};

// --- tokens -----------------------------------------------------------------------------------

// 32..256 bytes, no CR/LF (SAAP-v1.md §4).
inline bool token_shape_ok(const std::string &token) {
    if (token.size() < kMinToken || token.size() > kMaxToken)
        return false;
    for (std::size_t i = 0; i < token.size(); ++i)
        if (token[i] == '\r' || token[i] == '\n')
            return false;
    return true;
}

// Constant-time comparison: time depends only on the length of `expected`.
inline bool token_equal(const std::string &expected, const std::string &given) {
    volatile unsigned int diff = (unsigned int)(expected.size() ^ given.size());
    for (std::size_t i = 0; i < expected.size(); ++i) {
        const unsigned char g = i < given.size() ? (unsigned char)given[i] : 0u;
        diff |= (unsigned int)((unsigned char)expected[i] ^ g);
    }
    return diff == 0;
}

// --- JSON string escaping -----------------------------------------------------------------------

inline std::string json_escape(const std::string &s) {
    static const char *hex = "0123456789abcdef";
    std::string out;
    out.reserve(s.size() + 8);
    for (std::size_t i = 0; i < s.size(); ++i) {
        const unsigned char c = (unsigned char)s[i];
        switch (c) {
        case '"': out += "\\\""; break;
        case '\\': out += "\\\\"; break;
        case '\b': out += "\\b"; break;
        case '\f': out += "\\f"; break;
        case '\n': out += "\\n"; break;
        case '\r': out += "\\r"; break;
        case '\t': out += "\\t"; break;
        default:
            if (c < 0x20) {
                out += "\\u00";
                out += hex[c >> 4];
                out += hex[c & 0xF];
            } else {
                out += (char)c;
            }
        }
    }
    return out;
}

// --- atomic descriptor write ------------------------------------------------------------------

// Writes `data` to `path` via "<path>.<pid>.tmp" + replace. UTF-8 paths. Returns false on error
// (the temp file is removed).
inline bool write_file_atomic(const std::string &path, const std::string &data) {
#ifdef _WIN32
    const std::string tmp = path + "." + std::to_string((unsigned long)GetCurrentProcessId()) + ".tmp";
    int wn = MultiByteToWideChar(CP_UTF8, 0, tmp.c_str(), -1, NULL, 0);
    int pn = MultiByteToWideChar(CP_UTF8, 0, path.c_str(), -1, NULL, 0);
    if (wn <= 0 || pn <= 0)
        return false;
    std::wstring wtmp((std::size_t)wn, L'\0'), wpath((std::size_t)pn, L'\0');
    MultiByteToWideChar(CP_UTF8, 0, tmp.c_str(), -1, &wtmp[0], wn);
    MultiByteToWideChar(CP_UTF8, 0, path.c_str(), -1, &wpath[0], pn);
    HANDLE h = CreateFileW(wtmp.c_str(), GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (h == INVALID_HANDLE_VALUE)
        return false;
    DWORD written = 0;
    BOOL ok = data.empty() ? TRUE : WriteFile(h, data.data(), (DWORD)data.size(), &written, NULL);
    ok = ok && written == (DWORD)data.size() && FlushFileBuffers(h);
    CloseHandle(h);
    if (!ok || !MoveFileExW(wtmp.c_str(), wpath.c_str(), MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH)) {
        DeleteFileW(wtmp.c_str());
        return false;
    }
    return true;
#else
    const std::string tmp = path + "." + std::to_string((unsigned long)getpid()) + ".tmp";
    std::FILE *f = std::fopen(tmp.c_str(), "wb");
    if (!f)
        return false;
    const bool ok = std::fwrite(data.data(), 1, data.size(), f) == data.size() && std::fflush(f) == 0;
    std::fclose(f);
    if (!ok || std::rename(tmp.c_str(), path.c_str()) != 0) {
        std::remove(tmp.c_str());
        return false;
    }
    return true;
#endif
}

} // namespace saap

#endif // SAAP_FRAME_HPP
