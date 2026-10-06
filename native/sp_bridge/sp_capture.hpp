// sp_capture.hpp - D3D9 back-buffer capture for satk_sp.asi (game-only at runtime).
//
// MIT License. Copyright (c) 2026 satk contributors. Header-only, C++14, x86. Needs d3d9.h from
// the Windows SDK (linked with d3d9.lib). Grabs the current render target into a system-memory
// surface, converts it to 8-bit RGB and writes a PNG with saap_png. Returns the SHA-256 of the
// PNG bytes (SAAP `capture.sha256`).

#ifndef SP_CAPTURE_HPP
#define SP_CAPTURE_HPP

#include <cstdint>
#include <cstdio>
#include <string>
#include <vector>

#include <d3d9.h>

#include "saap_png.hpp"
#include "saap_sha256.hpp"
#include "sp_addresses.hpp"

#pragma comment(lib, "d3d9.lib")

namespace sp {
namespace capture {

struct Result {
    bool ok = false;
    int w = 0, h = 0;
    std::string sha256;
    std::string error;
};

inline IDirect3DDevice9* device() {
    using Fn = IDirect3DDevice9*(__cdecl*)();
    return reinterpret_cast<Fn>(addr::RwD3D9GetCurrentD3DDevice)();
}

// Capture the current back buffer to `<prefix>.png`. Returns .ok and the dimensions/hash.
inline Result to_png(const std::string& png_path) {
    Result r;
    IDirect3DDevice9* dev = device();
    if (!dev) { r.error = "no D3D9 device (device lost or not yet created)"; return r; }

    IDirect3DSurface9* rt = nullptr;
    if (FAILED(dev->GetRenderTarget(0, &rt)) || !rt) { r.error = "GetRenderTarget failed"; return r; }

    D3DSURFACE_DESC desc{};
    rt->GetDesc(&desc);
    const int w = (int)desc.Width, h = (int)desc.Height;

    IDirect3DSurface9* sys = nullptr;
    if (FAILED(dev->CreateOffscreenPlainSurface(desc.Width, desc.Height, desc.Format,
                                                D3DPOOL_SYSTEMMEM, &sys, nullptr)) || !sys) {
        rt->Release();
        r.error = "CreateOffscreenPlainSurface failed";
        return r;
    }
    if (FAILED(dev->GetRenderTargetData(rt, sys))) {
        sys->Release(); rt->Release();
        r.error = "GetRenderTargetData failed";
        return r;
    }

    D3DLOCKED_RECT lr{};
    if (FAILED(sys->LockRect(&lr, nullptr, D3DLOCK_READONLY))) {
        sys->Release(); rt->Release();
        r.error = "LockRect failed";
        return r;
    }

    std::vector<std::uint8_t> rgb((std::size_t)w * h * 3);
    const std::uint8_t* src = (const std::uint8_t*)lr.pBits;
    // X8R8G8B8 / A8R8G8B8: little-endian bytes are B,G,R,(X/A).
    const bool bgra = (desc.Format == D3DFMT_X8R8G8B8 || desc.Format == D3DFMT_A8R8G8B8);
    for (int y = 0; y < h; ++y) {
        const std::uint8_t* row = src + (std::size_t)y * lr.Pitch;
        for (int x = 0; x < w; ++x) {
            std::size_t o = ((std::size_t)y * w + x) * 3;
            if (bgra) {
                rgb[o] = row[x * 4 + 2];      // R
                rgb[o + 1] = row[x * 4 + 1];  // G
                rgb[o + 2] = row[x * 4 + 0];  // B
            } else {
                // best-effort for other formats: assume 4 bytes, take first three as-is
                rgb[o] = row[x * 4 + 0];
                rgb[o + 1] = row[x * 4 + 1];
                rgb[o + 2] = row[x * 4 + 2];
            }
        }
    }
    sys->UnlockRect();
    sys->Release();
    rt->Release();

    std::string png = saap::png::encode_rgb(rgb.data(), w, h);
    FILE* f = std::fopen(png_path.c_str(), "wb");
    if (!f) { r.error = "cannot open " + png_path; return r; }
    const bool wrote = std::fwrite(png.data(), 1, png.size(), f) == png.size();
    std::fclose(f);
    if (!wrote) { r.error = "cannot write " + png_path; return r; }

    r.ok = true;
    r.w = w; r.h = h;
    r.sha256 = saap::Sha256::of(png);
    return r;
}

}  // namespace capture
}  // namespace sp

#endif  // SP_CAPTURE_HPP
