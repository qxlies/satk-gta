// Self-test of saap_frame.hpp (MIT). Built and run by `satk saap cpp-selftest` with cl.exe
// /std:c++14 (toolset v143) and /std:c++latest (v145). Usage: saap_frame_selftest <temp-dir>
#ifndef _CRT_SECURE_NO_WARNINGS
#define _CRT_SECURE_NO_WARNINGS  // the test reads files back with fopen
#endif
#include "saap_frame.hpp"

#include <cstdio>
#include <cstring>
#include <string>

static int g_checks = 0;
static int g_failed = 0;

#define CHECK(cond)                                                                         \
    do {                                                                                    \
        ++g_checks;                                                                         \
        if (!(cond)) {                                                                      \
            ++g_failed;                                                                     \
            std::printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond);                     \
        }                                                                                   \
    } while (0)

static std::string read_file(const std::string &path) {
    std::string out;
    std::FILE *f = std::fopen(path.c_str(), "rb");
    if (!f)
        return "<missing>";
    char buf[4096];
    std::size_t n;
    while ((n = std::fread(buf, 1, sizeof buf, f)) > 0)
        out.append(buf, n);
    std::fclose(f);
    return out;
}

int main(int argc, char **argv) {
    using saap::FrameReader;

    // header encoding (big-endian)
    unsigned char h[4];
    saap::put_u32be(h, 0x01020304u);
    CHECK(h[0] == 1 && h[1] == 2 && h[2] == 3 && h[3] == 4);
    CHECK(saap::get_u32be(h) == 0x01020304u);

    // encode_frame
    const std::string req = "{\"saap\":1,\"id\":\"r1\",\"method\":\"ping\"}";
    std::string frame;
    CHECK(saap::encode_frame(req, frame, saap::kMaxRequest));
    CHECK(frame.size() == req.size() + 4);
    CHECK(saap::get_u32be((const unsigned char *)frame.data()) == req.size());
    std::string untouched = "x";
    CHECK(!saap::encode_frame("", untouched));
    CHECK(untouched == "x");
    CHECK(!saap::encode_frame(std::string(saap::kMaxRequest + 1, 'a'), untouched, saap::kMaxRequest));
    CHECK(saap::encode_frame(std::string(saap::kMaxRequest, 'a'), untouched, saap::kMaxRequest));

    // reader: byte by byte, then two frames in one feed
    {
        FrameReader rd(saap::kMaxRequest);
        std::string payload;
        for (std::size_t i = 0; i + 1 < frame.size(); ++i) {
            rd.feed(&frame[i], 1);
            CHECK(rd.next(payload) == FrameReader::kNeedMore);
        }
        CHECK(rd.missing() == 1);
        rd.feed(&frame[frame.size() - 1], 1);
        CHECK(rd.next(payload) == FrameReader::kFrame);
        CHECK(payload == req);
        CHECK(rd.buffered() == 0);
        std::string two = frame + frame;
        rd.feed(two.data(), two.size());
        CHECK(rd.next(payload) == FrameReader::kFrame && payload == req);
        CHECK(rd.next(payload) == FrameReader::kFrame && payload == req);
        CHECK(rd.next(payload) == FrameReader::kNeedMore);
    }
    // reader: oversize header fails at once without buffering the payload
    {
        FrameReader rd(saap::kMaxRequest);
        unsigned char big[4];
        saap::put_u32be(big, (std::uint32_t)saap::kMaxRequest + 1u);
        rd.feed((const char *)big, 4);
        std::string payload;
        CHECK(rd.failed());
        CHECK(rd.next(payload) == FrameReader::kTooLarge);
        rd.feed("abc", 3);
        CHECK(rd.buffered() == 0);
        rd.reset();
        CHECK(!rd.failed());
    }
    // reader: zero-length frame
    {
        FrameReader rd;
        const char zero[4] = {0, 0, 0, 0};
        rd.feed(zero, 4);
        std::string payload;
        CHECK(rd.next(payload) == FrameReader::kEmpty);
    }
    // reader: response limit
    {
        FrameReader rd(saap::kMaxResponse);
        unsigned char hdr[4];
        saap::put_u32be(hdr, (std::uint32_t)saap::kMaxResponse);
        rd.feed((const char *)hdr, 4);
        CHECK(!rd.failed());
        CHECK(rd.announced() == saap::kMaxResponse);
    }

    // tokens
    const std::string tok(64, 'a');
    CHECK(saap::token_shape_ok(tok));
    CHECK(!saap::token_shape_ok(std::string(31, 'a')));
    CHECK(saap::token_shape_ok(std::string(256, 'a')));
    CHECK(!saap::token_shape_ok(std::string(257, 'a')));
    CHECK(!saap::token_shape_ok(std::string(40, 'a') + "\n"));
    CHECK(saap::token_equal(tok, tok));
    CHECK(!saap::token_equal(tok, std::string(63, 'a')));
    CHECK(!saap::token_equal(tok, std::string(63, 'a') + "b"));
    CHECK(!saap::token_equal(tok, ""));

    // JSON escaping
    CHECK(saap::json_escape("a\"b\\c\n\x01") == "a\\\"b\\\\c\\n\\u0001");
    CHECK(saap::json_escape("C:/ws/GTA") == "C:/ws/GTA");

    // atomic descriptor write
    if (argc > 1) {
        const std::string path = std::string(argv[1]) + "/saap_selftest_descriptor.json";
        const std::string d1 = "{\"protocol\":\"saap/1\",\"port\":1}";
        const std::string d2 = "{\"protocol\":\"saap/1\",\"port\":2}";
        CHECK(saap::write_file_atomic(path, d1));
        CHECK(read_file(path) == d1);
        CHECK(saap::write_file_atomic(path, d2));
        CHECK(read_file(path) == d2);
        std::remove(path.c_str());
        CHECK(!saap::write_file_atomic(std::string(argv[1]) + "/no-such-dir/x/descriptor.json", d1));
    }

    std::printf("saap_frame selftest: %d checks, %d failed (__cplusplus=%ld)\n", g_checks, g_failed,
                (long)__cplusplus);
    return g_failed == 0 ? 0 : 1;
}
