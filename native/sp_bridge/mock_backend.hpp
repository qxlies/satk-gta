// mock_backend.hpp - a synthetic SAAP/1 backend with no game, for the host-side test server.
//
// MIT License. Copyright (c) 2026 satk contributors. Header-only, C++14.
//
// It mirrors enough of the SAAP mock world (satk/saap/mock.py) to pass the conformance cases
// whose caps it advertises - over the real C++ Server and real TCP framing - so the ASI's
// transport, auth, envelope and JSON are proven without gta_sa.exe. It is NOT compiled into the
// shipped ASI; the game uses sp_backend instead.

#ifndef SAAP_MOCK_BACKEND_HPP
#define SAAP_MOCK_BACKEND_HPP

#include <cmath>
#include <cstdio>
#include <string>
#include <vector>

#include "saap_json.hpp"
#include "saap_png.hpp"
#include "saap_server.hpp"
#include "saap_sha256.hpp"

namespace saap {

class MockBackend : public Backend {
public:
    MockBackend() {
        pos_[0] = 2495; pos_[1] = -1720; pos_[2] = 60;
        look_[0] = 2495; look_[1] = -1670; look_[2] = 15;
        time_ = "12:00";
    }

    json::Value describe() override {
        json::Value r = json::Value::object();
        r.set("role", json::Value(std::string("mock")));
        r.set("impl", json::Value(std::string("satk-sp-mock")));
        r.set("impl_version", json::Value(std::string("0.1.0")));
        json::Value build = json::Value::object();
        build.set("id", json::Value(std::string("satk-sp-mock-1")));
        r.set("build", build);
        json::Value caps = json::Value::array();
        const char* cs[] = {"core", "camera", "world.settle", "capture", "capture.size", "capture.depth",
                            "pick", "entity.query", "entity.inspect", "env", "log", "console"};
        for (auto c : cs) caps.push(json::Value(std::string(c)));
        r.set("caps", caps);
        json::Value game = json::Value::object();
        game.set("version", json::Value(std::string("1.0us")));
        r.set("game", game);
        json::Value world = json::Value::object();
        world.set("units", json::Value(std::string("m")));
        world.set("up", json::Value(std::string("z")));
        r.set("world", world);
        json::Value vp = json::Value::object();
        vp.set("w", json::Value((std::int64_t)800));
        vp.set("h", json::Value((std::int64_t)600));
        r.set("viewport", vp);
        return r;
    }

    std::uint32_t frame() override { return frame_; }

    bool call(const std::string& m, const json::Value& p, json::Value& result, Error& err) override {
        ++frame_;
        if (m == "ping") { result = json::Value::object(); result.set("t_ms", json::Value(now_ms())); return true; }
        if (m == "status") return status(result);
        if (m == "quit") { request_quit(); result = json::Value::object(); return true; }
        if (m == "camera.get") { result = camera_result(); return true; }
        if (m == "camera.set") return camera_set(p, result, err);
        if (m == "camera.release") { result = json::Value::object(); return true; }
        if (m == "world.settle") return settle(result);
        if (m == "capture") return capture(p, result, err);
        if (m == "pick") return pick(p, result, err);
        if (m == "raycast") return raycast(p, result, err);
        if (m == "entity.query") return entity_query(p, result, err);
        if (m == "entity.inspect") return entity_inspect(p, result, err);
        if (m == "env.get") { result = env_result(); return true; }
        if (m == "env.set") return env_set(p, result, err);
        if (m == "log.poll") return log_poll(p, result);
        if (m == "console.exec") return console_exec(p, result, err);
        err = Error::make("UNKNOWN_METHOD", "no such method: " + m);
        return false;
    }

private:
    json::Value vec3(const double* v) const {
        json::Value a = json::Value::array();
        for (int i = 0; i < 3; ++i) a.push(json::Value(v[i]));
        return a;
    }
    static void read_vec3(const json::Value& v, double* out) {
        for (int i = 0; i < 3 && i < (int)v.size(); ++i) out[i] = v.arr()[i].as_double();
    }

    json::Value pose_value() const {
        json::Value po = json::Value::object();
        po.set("pos", vec3(pos_));
        po.set("look", vec3(look_));
        po.set("fov_h_deg", json::Value(fov_));
        return po;
    }

    json::Value camera_result() const {
        json::Value r = json::Value::object();
        r.set("pose", pose_value());
        r.set("fov_h_deg", json::Value(fov_));
        r.set("near", json::Value(0.1));
        r.set("far", json::Value(3000.0));
        r.set("rev", json::Value((std::int64_t)cam_rev_));
        return r;
    }

    json::Value env_result() const {
        json::Value e = json::Value::object();
        e.set("time", json::Value(time_));
        e.set("weather", json::Value((std::int64_t)weather_));
        e.set("weather_b", json::Value((std::int64_t)weather_));
        e.set("blend", json::Value(0.0));
        e.set("freeze", json::Value(freeze_));
        return e;
    }

    bool status(json::Value& result) {
        result = json::Value::object();
        result.set("frame", json::Value((std::int64_t)frame_));
        result.set("fps", json::Value(60.0));
        result.set("pose", pose_value());
        result.set("env", env_result());
        json::Value streaming = json::Value::object();
        streaming.set("pending", json::Value((std::int64_t)0));
        result.set("streaming", streaming);
        json::Value rev = json::Value::object();
        rev.set("scene", json::Value((std::int64_t)scene_rev_));
        rev.set("camera", json::Value((std::int64_t)cam_rev_));
        result.set("rev", rev);
        return true;
    }

    // pose with pos + (look | ypr); fills pos_/look_ on success.
    bool apply_pose(const json::Value& pose, Error& err, double* out_pos, double* out_look) {
        if (!pose.is_object() || !check::is_vec3(pose["pos"])) {
            err = Error::make("BAD_PARAMS", "pose.pos must be a vec3"); return false;
        }
        const bool has_look = pose.has("look");
        const bool has_ypr = pose.has("ypr");
        if (has_look == has_ypr) {  // exactly one
            err = Error::make("BAD_PARAMS", "pose needs exactly one of look / ypr"); return false;
        }
        read_vec3(pose["pos"], out_pos);
        if (has_look) {
            if (!check::is_vec3(pose["look"])) { err = Error::make("BAD_PARAMS", "pose.look must be a vec3"); return false; }
            read_vec3(pose["look"], out_look);
        } else {
            const json::Value& y = pose["ypr"];
            if (!y.is_array() || y.size() != 3) { err = Error::make("BAD_PARAMS", "pose.ypr must be 3 numbers"); return false; }
            const double yaw = y.arr()[0].as_double() * 3.14159265358979 / 180.0;
            const double pitch = y.arr()[1].as_double() * 3.14159265358979 / 180.0;
            const double fx = -std::sin(yaw) * std::cos(pitch);
            const double fy = std::cos(yaw) * std::cos(pitch);
            const double fz = std::sin(pitch);
            out_look[0] = out_pos[0] + fx * 10.0;
            out_look[1] = out_pos[1] + fy * 10.0;
            out_look[2] = out_pos[2] + fz * 10.0;
        }
        return true;
    }

    bool camera_set(const json::Value& p, json::Value& result, Error& err) {
        if (!p.is_object() || !p.has("pose")) { err = Error::make("BAD_PARAMS", "pose is required"); return false; }
        double pos[3], look[3];
        if (!apply_pose(p["pose"], err, pos, look)) return false;
        if (p.has("expect_rev")) {
            const std::int64_t want = p["expect_rev"].as_int(-1);
            if (want != (std::int64_t)cam_rev_) {
                err = Error::make("REVISION", "stale expect_rev");
                err.data = json::Value::object();
                err.data.set("rev", json::Value((std::int64_t)cam_rev_));
                return false;
            }
        }
        for (int i = 0; i < 3; ++i) { pos_[i] = pos[i]; look_[i] = look[i]; }
        if (p.has("fov_h_deg")) fov_ = p["fov_h_deg"].as_double(fov_);
        else if (p["pose"].has("fov_h_deg")) fov_ = p["pose"]["fov_h_deg"].as_double(fov_);
        ++cam_rev_;
        result = json::Value::object();
        result.set("pose", pose_value());
        result.set("rev", json::Value((std::int64_t)cam_rev_));
        if (p.has("stream") && p["stream"].as_string() != "none")
            result.set("settled", json::Value(true));
        return true;
    }

    bool settle(json::Value& result) {
        result = json::Value::object();
        result.set("settled", json::Value(true));
        result.set("frames", json::Value((std::int64_t)1));
        result.set("pending", json::Value((std::int64_t)0));
        return true;
    }

    bool capture(const json::Value& p, json::Value& result, Error& err) {
        if (!p.is_object() || !p.has("path_prefix") || !p["path_prefix"].is_string()) {
            err = Error::make("BAD_PARAMS", "path_prefix is required"); return false;
        }
        int w = 800, h = 600;
        if (p.has("w")) w = (int)p["w"].as_int(w);
        if (p.has("h")) h = (int)p["h"].as_int(h);
        bool want_depth = false;
        if (p.has("layers")) {
            for (const auto& l : p["layers"].arr()) {
                const std::string s = l.as_string();
                if (s == "color") continue;
                else if (s == "depth") want_depth = true;
                else { err = Error::make("UNSUPPORTED", "layer not supported: " + s);
                       err.data = json::Value::object(); err.data.set("capability", json::Value("capture." + s)); return false; }
            }
        }
        const std::string prefix = p["path_prefix"].as_string();
        // colour PNG (synthetic gradient)
        std::vector<std::uint8_t> rgb((std::size_t)w * h * 3);
        for (int y = 0; y < h; ++y)
            for (int x = 0; x < w; ++x) {
                std::size_t o = ((std::size_t)y * w + x) * 3;
                rgb[o] = (std::uint8_t)(x * 255 / (w > 1 ? w - 1 : 1));
                rgb[o + 1] = (std::uint8_t)(y * 255 / (h > 1 ? h - 1 : 1));
                rgb[o + 2] = 128;
            }
        std::string pngdata = png::encode_rgb(rgb.data(), w, h);
        const std::string color_path = prefix + ".png";
        if (!write_file(color_path, pngdata.data(), pngdata.size())) {
            err = Error::make("INTERNAL", "cannot write " + color_path); return false;
        }
        json::Value files = json::Value::object();
        files.set("color", json::Value(color_path));
        if (want_depth) {
            std::vector<float> depth((std::size_t)w * h, 50.0f);
            const std::string depth_path = prefix + ".depth.f32";
            if (!write_file(depth_path, depth.data(), depth.size() * sizeof(float))) {
                err = Error::make("INTERNAL", "cannot write " + depth_path); return false;
            }
            files.set("depth", json::Value(depth_path));
        }
        result = json::Value::object();
        result.set("files", files);
        result.set("w", json::Value((std::int64_t)w));
        result.set("h", json::Value((std::int64_t)h));
        result.set("pose", pose_value());
        result.set("settled", json::Value(true));
        result.set("pending", json::Value((std::int64_t)0));
        result.set("frames_waited", json::Value((std::int64_t)0));
        result.set("sha256", json::Value(Sha256::of(pngdata)));
        return true;
    }

    json::Value entity_ref(int idx) const {
        json::Value e = json::Value::object();
        char ref[8]; std::snprintf(ref, sizeof ref, "m%d", idx);
        e.set("ref", json::Value(std::string(ref)));
        e.set("kind", json::Value(std::string("building")));
        e.set("model_id", json::Value((std::int64_t)(17613 + idx)));
        e.set("model_name", json::Value(std::string("mock_model")));
        double pos[3] = {2489.3 + idx * 5.0, -1668.5, 12.3};
        e.set("pos", vec3(pos));
        json::Value src = json::Value::object();
        src.set("kind", json::Value(std::string("model_pos")));
        e.set("src", src);
        return e;
    }

    bool pick(const json::Value& p, json::Value& result, Error& err) {
        if (!p.is_object() || !p["points"].is_array() || p["points"].size() < 1) {
            err = Error::make("BAD_PARAMS", "points must be a non-empty array"); return false;
        }
        json::Value hits = json::Value::array();
        for (const auto& pt : p["points"].arr()) {
            double px = 0, py = 0;
            if (pt.is_array() && pt.size() == 2) { px = pt.arr()[0].as_double(); py = pt.arr()[1].as_double(); }
            json::Value hit = json::Value::object();
            hit.set("px", json::Value(px));
            hit.set("py", json::Value(py));
            hit.set("hit", json::Value(true));
            double pos[3] = {2489.3, -1668.5, 12.8};
            double nrm[3] = {0, 0, 1};
            hit.set("pos", vec3(pos));
            hit.set("normal", vec3(nrm));
            hit.set("dist", json::Value(71.4));
            hit.set("entity", entity_ref(1));
            hits.push(hit);
        }
        result = json::Value::object();
        result.set("hits", hits);
        return true;
    }

    bool raycast(const json::Value& p, json::Value& result, Error& err) {
        if (!p.is_object() || !check::is_vec3(p["from"]) || !check::is_vec3(p["to"])) {
            err = Error::make("BAD_PARAMS", "from and to must be vec3"); return false;
        }
        result = json::Value::object();
        result.set("hit", json::Value(true));
        double pos[3] = {2489.3, -1668.5, 12.8};
        double nrm[3] = {0, 0, 1};
        result.set("pos", vec3(pos));
        result.set("normal", vec3(nrm));
        result.set("dist", json::Value(47.2));
        result.set("entity", entity_ref(1));
        return true;
    }

    bool entity_query(const json::Value& p, json::Value& result, Error& err) {
        if (!p.is_object() || !check::is_vec3(p["center"]) || !p["r"].is_number()) {
            err = Error::make("BAD_PARAMS", "center (vec3) and r are required"); return false;
        }
        int start = 0;
        if (p.has("cursor")) start = (int)std::strtol(p["cursor"].as_string().c_str(), nullptr, 10);
        int limit = 50;
        if (p.has("limit")) limit = (int)p["limit"].as_int(limit);
        const int ntotal = 3;
        json::Value items = json::Value::array();
        int i = start;
        for (; i < ntotal && (int)items.size() < limit; ++i) items.push(entity_ref(i + 1));
        result = json::Value::object();
        result.set("items", items);
        result.set("total", json::Value((std::int64_t)ntotal));
        if (i < ntotal) { char cur[8]; std::snprintf(cur, sizeof cur, "%d", i); result.set("next", json::Value(std::string(cur))); }
        return true;
    }

    bool entity_inspect(const json::Value& p, json::Value& result, Error& err) {
        const std::string ref = p["ref"].as_string();
        if (ref.size() < 2 || ref[0] != 'm') { err = Error::make("NOT_FOUND", "unknown ref: " + ref); return false; }
        const int idx = (int)std::strtol(ref.c_str() + 1, nullptr, 10);
        if (idx < 1 || idx > 3) { err = Error::make("NOT_FOUND", "unknown ref: " + ref); return false; }
        result = json::Value::object();
        result.set("entity", entity_ref(idx));
        json::Value model = json::Value::object();
        model.set("id", json::Value((std::int64_t)(17613 + idx)));
        model.set("name", json::Value(std::string("mock_model")));
        model.set("draw", json::Value(299.0));
        result.set("model", model);
        json::Value lod = json::Value::object();
        lod.set("children", json::Value::array());
        result.set("lod", lod);
        return true;
    }

    bool env_set(const json::Value& p, json::Value& result, Error& err) {
        if (!p.is_object() || p.size() == 0) { err = Error::make("BAD_PARAMS", "env.set needs at least one field"); return false; }
        if (p.has("time")) {
            const std::string t = p["time"].as_string();
            if (!valid_time(t)) { err = Error::make("BAD_PARAMS", "time must be HH:MM"); return false; }
            time_ = t;
        }
        if (p.has("weather")) weather_ = (int)p["weather"].as_int(weather_);
        if (p.has("freeze")) freeze_ = p["freeze"].as_bool(freeze_);
        ++scene_rev_;
        result = env_result();
        return true;
    }

    bool log_poll(const json::Value& p, json::Value& result) {
        (void)p;
        result = json::Value::object();
        result.set("items", json::Value::array());
        result.set("next_seq", json::Value((std::int64_t)0));
        result.set("dropped", json::Value((std::int64_t)0));
        return true;
    }

    bool console_exec(const json::Value& p, json::Value& result, Error& err) {
        if (!p.has("line") || !p["line"].is_string() || p["line"].as_string().empty()) {
            err = Error::make("BAD_PARAMS", "line is required"); return false;
        }
        result = json::Value::object();
        result.set("accepted", json::Value(false));
        json::Value out = json::Value::array();
        out.push(json::Value(std::string("single-player console is not available")));
        result.set("output", out);
        return true;
    }

    static bool valid_time(const std::string& t) {
        if (t.size() != 5 || t[2] != ':') return false;
        for (int i : {0, 1, 3, 4}) if (t[i] < '0' || t[i] > '9') return false;
        int hh = (t[0] - '0') * 10 + (t[1] - '0');
        int mm = (t[3] - '0') * 10 + (t[4] - '0');
        return hh <= 23 && mm <= 59;
    }

    static bool write_file(const std::string& path, const void* data, std::size_t n) {
        FILE* f = std::fopen(path.c_str(), "wb");
        if (!f) return false;
        const bool ok = n == 0 || std::fwrite(data, 1, n, f) == n;
        std::fclose(f);
        return ok;
    }

    double pos_[3], look_[3];
    double fov_ = 70.0;
    std::string time_;
    int weather_ = 0;
    bool freeze_ = false;
    std::uint32_t frame_ = 0;
    std::uint32_t cam_rev_ = 0;
    std::uint32_t scene_rev_ = 0;
};

}  // namespace saap

#endif  // SAAP_MOCK_BACKEND_HPP
