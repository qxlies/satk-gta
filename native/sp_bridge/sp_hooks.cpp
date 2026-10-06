// sp_hooks.cpp - install the main-thread pump and own the SAAP server (satk_sp.asi, x86).
//
// MIT License. Copyright (c) 2026 satk contributors.
//
// The ASI attaches to one call instruction: `call CGame::Process` inside Idle (the plugin-sdk
// gameProcessEvent site for 1.0 US, sp_addresses.hpp). It rewrites that one relative call to a
// trampoline that runs the original CGame::Process and then pumps the SAAP server once - so every
// SAAP request is handled on the game's main thread, with a tiny per-frame budget and no extra
// thread touching the engine. The server listens on 127.0.0.1 with the launcher's token; without
// a token it does not listen at all (SAAP-v1.md section 4).

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>

#include <cstdint>
#include <cstdio>
#include <ctime>
#include <string>

#include "saap_frame.hpp"
#include "saap_json.hpp"
#include "saap_server.hpp"
#include "sp_addresses.hpp"
#include "sp_backend.hpp"

namespace sp {

namespace {
SpBackend* g_backend = nullptr;
saap::Server* g_server = nullptr;
std::uint32_t g_original_process = 0;  // absolute address of the original call target
bool g_installed = false;

std::string env(const char* name) {
    char buf[4096];
    DWORD n = GetEnvironmentVariableA(name, buf, sizeof buf);
    return (n > 0 && n < sizeof buf) ? std::string(buf, n) : std::string();
}

std::string iso_now() {
    std::time_t t = std::time(nullptr);
    std::tm g{};
#if defined(_MSC_VER)
    gmtime_s(&g, &t);
#else
    g = *std::gmtime(&t);
#endif
    char b[32];
    std::snprintf(b, sizeof b, "%04d-%02d-%02dT%02d:%02d:%02dZ",
                  g.tm_year + 1900, g.tm_mon + 1, g.tm_mday, g.tm_hour, g.tm_min, g.tm_sec);
    return b;
}

// The trampoline: original per-frame tick, then one non-blocking SAAP pump.
void __cdecl hooked_process() {
    reinterpret_cast<void(__cdecl*)()>(g_original_process)();
    if (g_server) g_server->pump();
}

bool patch_call_site() {
    const std::uint8_t* site = reinterpret_cast<const std::uint8_t*>(addr::gameProcessEvent);
    if (site[0] != 0xE8) return false;  // not a `call rel32`: wrong exe / already patched
    const std::int32_t rel = *reinterpret_cast<const std::int32_t*>(site + 1);
    const std::uint32_t target = addr::gameProcessEvent + 5 + (std::uint32_t)rel;
    if (target != addr::gameProcessEvent_target) return false;  // not CGame::Process: refuse
    g_original_process = target;
    const std::int32_t new_rel = (std::int32_t)((std::uint32_t)&hooked_process - (addr::gameProcessEvent + 5));
    DWORD old = 0;
    LPVOID at = (LPVOID)(addr::gameProcessEvent + 1);
    if (!VirtualProtect(at, 4, PAGE_EXECUTE_READWRITE, &old)) return false;
    *reinterpret_cast<std::int32_t*>(at) = new_rel;
    VirtualProtect(at, 4, old, &old);
    FlushInstructionCache(GetCurrentProcess(), (LPVOID)addr::gameProcessEvent, 5);
    return true;
}

void write_descriptor(std::uint16_t port) {
    const std::string path = env("SATK_AGENT_DESCRIPTOR");
    if (path.empty()) return;
    namespace J = saap::json;
    J::Value d = J::Value::object();
    d.set("protocol", J::Value(std::string("saap/1")));
    d.set("role", J::Value(std::string("sp")));
    d.set("impl", J::Value(std::string("satk-sp")));
    d.set("impl_version", J::Value(std::string("0.1.0")));
    d.set("pid", J::Value((std::int64_t)GetCurrentProcessId()));
    d.set("port", J::Value((std::int64_t)port));
    J::Value caps = g_backend->describe()["caps"];
    d.set("caps", caps);
    d.set("started_at", J::Value(iso_now()));
    char exe[MAX_PATH]; DWORD n = GetModuleFileNameA(nullptr, exe, sizeof exe);
    if (n > 0 && n < sizeof exe) d.set("exe", J::Value(std::string(exe, n)));
    saap::write_file_atomic(path, d.dump());
}
}  // namespace

void install() {
    if (g_installed) return;
    const std::string token = env("SATK_AGENT_TOKEN");
    const bool insecure = env("SATK_AGENT_INSECURE") == "1";
    if (token.empty() && !insecure) return;  // no token: do not listen at all
    const std::string out_root = env("SATK_AGENT_OUT_ROOT");
    std::uint16_t port = 0;
    const std::string ps = env("SATK_AGENT_PORT");
    if (!ps.empty()) port = (std::uint16_t)std::strtoul(ps.c_str(), nullptr, 10);

    g_backend = new SpBackend(out_root);
    g_server = new saap::Server(*g_backend);
    std::string why;
    if (!g_server->listen(port, token, insecure, why)) return;  // bind failed: stay silent
    if (!patch_call_site()) return;  // wrong exe: do not claim to be running
    write_descriptor(g_server->port());
    g_backend->log("console", "info", std::string("satk_sp listening on 127.0.0.1"));
    g_installed = true;
}

}  // namespace sp
