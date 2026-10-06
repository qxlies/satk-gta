// dllmain.cpp - satk_sp.asi entry point (x86). MIT License (c) 2026 satk contributors.
//
// An ASI is a renamed DLL the Mod Loader / ASI loader LoadLibrary's into gta_sa.exe (or a DLL
// injected by `satk sp start --inject`). On attach it installs the per-frame pump and the SAAP
// server (sp_hooks.cpp). gta_sa.exe 1.0 US has a fixed image base (no ASLR), so the static
// addresses are valid; install() verifies the hook site before patching and otherwise stays
// silent, so loading into a wrong build does nothing.

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>

namespace sp { void install(); }

BOOL APIENTRY DllMain(HMODULE module, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(module);
        sp::install();
    }
    return TRUE;
}
