// test_server_main.cpp - a console SAAP/1 server with the mock backend (no game).
//
// MIT License. Copyright (c) 2026 satk contributors.
//
// Built by `satk sp selftest`; the Python side then runs the SAAP conformance cases against it
// over TCP (satk.saap.conformance.SaapDriver), so the real C++ transport/framing/auth/dispatch is
// exercised without gta_sa.exe. It listens on 127.0.0.1, prints "PORT <n>" and "TOKEN <t>" so the
// driver can connect, and pumps until `quit`, the idle timeout, or a line on stdin.
//
// Usage: test_server [--port N] [--token HEX] [--insecure] [--idle-ms N]

#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <thread>

#include "mock_backend.hpp"
#include "saap_server.hpp"

static std::string arg_value(int argc, char** argv, const char* name, const char* def) {
    for (int i = 1; i < argc - 1; ++i)
        if (std::strcmp(argv[i], name) == 0) return argv[i + 1];
    return def;
}
static bool has_flag(int argc, char** argv, const char* name) {
    for (int i = 1; i < argc; ++i) if (std::strcmp(argv[i], name) == 0) return true;
    return false;
}

int main(int argc, char** argv) {
    const int port = std::atoi(arg_value(argc, argv, "--port", "0").c_str());
    std::string token = arg_value(argc, argv, "--token", "");
    const bool insecure = has_flag(argc, argv, "--insecure");
    const long idle_ms = std::atol(arg_value(argc, argv, "--idle-ms", "60000").c_str());
    if (token.empty()) {
        // deterministic 64-hex default for insecure/dev use
        token = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";
    }

    saap::MockBackend backend;
    saap::Server server(backend);
    std::string why;
    if (!server.listen((std::uint16_t)port, token, insecure, why)) {
        std::fprintf(stderr, "listen failed: %s\n", why.c_str());
        return 2;
    }
    std::printf("PORT %u\n", (unsigned)server.port());
    std::printf("TOKEN %s\n", token.c_str());
    std::fflush(stdout);

    // Watchdog: a generous absolute cap so an orphaned test server cannot linger. The Python
    // harness finishes the conformance run in well under a second and then sends `quit`.
    const auto start = std::chrono::steady_clock::now();
    while (!backend.wants_quit()) {
        server.pump();
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
        if (idle_ms > 0 && std::chrono::steady_clock::now() - start > std::chrono::milliseconds(idle_ms)) break;
    }
    server.close();
    return 0;
}
