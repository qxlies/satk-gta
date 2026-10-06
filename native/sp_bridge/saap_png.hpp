// saap_png.hpp - a minimal 8-bit RGB PNG writer for satk_sp.asi captures.
//
// MIT License. Copyright (c) 2026 satk contributors. Header-only, C++14, standard library only.
// Uses stored (uncompressed) zlib blocks so there is no compression dependency; the files are a
// little larger but perfectly valid PNGs (SAAP capture `color` layer: 8-bit RGB). CRC-32 and
// Adler-32 are computed inline.

#ifndef SAAP_PNG_HPP
#define SAAP_PNG_HPP

#include <cstdint>
#include <string>
#include <vector>

namespace saap {
namespace png {

inline std::uint32_t crc32(const std::uint8_t* data, std::size_t n, std::uint32_t crc = 0xFFFFFFFFu) {
    static std::uint32_t table[256];
    static bool init = false;
    if (!init) {
        for (std::uint32_t i = 0; i < 256; ++i) {
            std::uint32_t c = i;
            for (int k = 0; k < 8; ++k) c = (c & 1) ? (0xEDB88320u ^ (c >> 1)) : (c >> 1);
            table[i] = c;
        }
        init = true;
    }
    for (std::size_t i = 0; i < n; ++i) crc = table[(crc ^ data[i]) & 0xFF] ^ (crc >> 8);
    return crc;
}

inline void put_be32(std::string& out, std::uint32_t v) {
    out += (char)(v >> 24); out += (char)(v >> 16); out += (char)(v >> 8); out += (char)v;
}

inline void chunk(std::string& out, const char* type, const std::string& data) {
    put_be32(out, (std::uint32_t)data.size());
    std::string td(type, 4);
    td += data;
    out.append(td);
    std::uint32_t c = crc32((const std::uint8_t*)td.data(), td.size()) ^ 0xFFFFFFFFu;
    put_be32(out, c);
}

// rgb: w*h*3 bytes, row-major from the top-left. Returns the PNG bytes.
inline std::string encode_rgb(const std::uint8_t* rgb, int w, int h) {
    std::string out;
    out.append("\x89PNG\r\n\x1a\n", 8);
    std::string ihdr;
    put_be32(ihdr, (std::uint32_t)w);
    put_be32(ihdr, (std::uint32_t)h);
    ihdr += (char)8;   // bit depth
    ihdr += (char)2;   // colour type 2 = truecolour RGB
    ihdr += (char)0;   // compression
    ihdr += (char)0;   // filter
    ihdr += (char)0;   // interlace
    chunk(out, "IHDR", ihdr);

    // raw scanlines with filter byte 0 per row
    std::string raw;
    raw.reserve((std::size_t)(w * 3 + 1) * h);
    for (int y = 0; y < h; ++y) {
        raw += (char)0;
        raw.append((const char*)(rgb + (std::size_t)y * w * 3), (std::size_t)w * 3);
    }

    // zlib stream with stored blocks
    std::string z;
    z += (char)0x78; z += (char)0x01;  // zlib header (no compression / default)
    std::size_t pos = 0;
    const std::size_t total = raw.size();
    while (true) {
        std::size_t block = total - pos;
        bool last = true;
        if (block > 0xFFFF) { block = 0xFFFF; last = false; }
        z += (char)(last ? 1 : 0);
        std::uint16_t len = (std::uint16_t)block;
        std::uint16_t nlen = (std::uint16_t)~len;
        z += (char)(len & 0xFF); z += (char)(len >> 8);
        z += (char)(nlen & 0xFF); z += (char)(nlen >> 8);
        z.append(raw, pos, block);
        pos += block;
        if (last) break;
    }
    // adler-32 of raw
    std::uint32_t a = 1, b = 0;
    for (std::size_t i = 0; i < raw.size(); ++i) { a = (a + (std::uint8_t)raw[i]) % 65521; b = (b + a) % 65521; }
    put_be32(z, (b << 16) | a);
    chunk(out, "IDAT", z);
    chunk(out, "IEND", std::string());
    return out;
}

}  // namespace png
}  // namespace saap

#endif  // SAAP_PNG_HPP
