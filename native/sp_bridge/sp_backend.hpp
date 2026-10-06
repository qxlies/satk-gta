// sp_backend.hpp - the game Backend for satk_sp.asi: SAAP/1 methods on the real SP engine.
//
// MIT License. Copyright (c) 2026 satk contributors. Header-only, C++14, x86 (game-only at run).
//
// Implements the SAAP/1 methods the single-player bridge serves, plus the `sp.*` extension
// methods for the "hands" the frozen SAAP method set does not cover (teleport, spawn, hud),
// announced through the `sp.control` capability (SAAP-v1.md section 7: extensions via caps). The
// transport, auth and dispatch are in saap_server; this class is pure game behaviour. All methods
// run on the main thread inside the per-frame pump, so engine calls are safe.
//
// Capture writes only under the out-root given at construction (SATK_AGENT_OUT_ROOT); a path
// outside it is BAD_PARAMS, mirroring the native Ariane endpoint.

#ifndef SP_BACKEND_HPP
#define SP_BACKEND_HPP

#include <algorithm>
#include <cctype>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <deque>
#include <string>
#include <vector>

#include "saap_json.hpp"
#include "saap_server.hpp"
#include "sp_capture.hpp"
#include "sp_engine.hpp"

namespace sp {

using saap::Error;
namespace J = saap::json;

class SpBackend : public saap::Backend {
public:
    explicit SpBackend(const std::string& out_root) : out_root_(normalize(out_root)) {}

    void log(const char* stream, const char* level, const std::string& msg) {
        LogItem it;
        it.seq = ++log_seq_;
        it.t = now_ms();
        it.stream = stream;
        it.level = level;
        it.msg = msg;
        log_.push_back(it);
        while (log_.size() > 512) { log_.pop_front(); ++log_dropped_base_; }
    }

    double now_ms() override {
        return (double)engine::frame_counter() * 1000.0 / 30.0;  // approximate game clock
    }
    std::uint32_t frame() override { return engine::frame_counter(); }

    J::Value describe() override {
        J::Value r = J::Value::object();
        r.set("role", J::Value(std::string("sp")));
        r.set("impl", J::Value(std::string("satk-sp")));
        r.set("impl_version", J::Value(std::string("0.1.0")));
        J::Value build = J::Value::object();
        build.set("id", J::Value(std::string("satk-sp-") + addr::kGameVersion));
        r.set("build", build);
        J::Value caps = J::Value::array();
        const char* cs[] = {"core", "camera", "world.settle", "capture", "pick", "entity.query",
                            "entity.inspect", "env", "log", "console", "sp.control"};
        for (auto c : cs) caps.push(J::Value(std::string(c)));
        r.set("caps", caps);
        J::Value game = J::Value::object();
        game.set("version", J::Value(std::string(addr::kGameVersion)));
        r.set("game", game);
        J::Value world = J::Value::object();
        world.set("units", J::Value(std::string("m")));
        world.set("up", J::Value(std::string("z")));
        r.set("world", world);
        if (IDirect3DDevice9* dev = capture::device()) {
            IDirect3DSurface9* rt = nullptr;
            if (SUCCEEDED(dev->GetRenderTarget(0, &rt)) && rt) {
                D3DSURFACE_DESC d{}; rt->GetDesc(&d); rt->Release();
                J::Value vp = J::Value::object();
                vp.set("w", J::Value((std::int64_t)d.Width));
                vp.set("h", J::Value((std::int64_t)d.Height));
                r.set("viewport", vp);
            }
        }
        return r;
    }

    bool call(const std::string& m, const J::Value& p, J::Value& result, Error& err) override {
        if (m == "ping") { result = J::Value::object(); result.set("t_ms", J::Value(now_ms())); return true; }
        if (m == "status") return status(result);
        if (m == "quit") { request_quit(); result = J::Value::object(); return true; }
        if (m == "camera.get") { result = camera_result(); return true; }
        if (m == "camera.set") return camera_set(p, result, err);
        if (m == "camera.release") { engine::restore_camera(); result = J::Value::object(); return true; }
        if (m == "world.settle") return settle(p, result);
        if (m == "capture") return capture(p, result, err);
        if (m == "pick") return pick(p, result, err);
        if (m == "raycast") return raycast(p, result, err);
        if (m == "entity.query") return entity_query(p, result, err);
        if (m == "entity.inspect") return entity_inspect(p, result, err);
        if (m == "env.get") { result = env_result(); return true; }
        if (m == "env.set") return env_set(p, result, err);
        if (m == "log.poll") return log_poll(p, result);
        if (m == "console.exec") return console_exec(p, result, err);
        // --- sp.* extension methods (the "hands") ---
        if (m == "sp.teleport") return sp_teleport(p, result, err);
        if (m == "sp.spawn") return sp_spawn(p, result, err);
        if (m == "sp.hud") return sp_hud(p, result, err);
        err = Error::make("UNKNOWN_METHOD", "no such method: " + m);
        return false;
    }

private:
    struct LogItem { std::uint32_t seq; double t; std::string stream, level, msg; };

    static J::Value vec3(const engine::Vec3& v) {
        J::Value a = J::Value::array();
        a.push(J::Value((double)v.x)); a.push(J::Value((double)v.y)); a.push(J::Value((double)v.z));
        return a;
    }
    static engine::Vec3 read_vec3(const J::Value& v) {
        engine::Vec3 o;
        if (v.is_array() && v.size() == 3) {
            o.x = (float)v.arr()[0].as_double(); o.y = (float)v.arr()[1].as_double(); o.z = (float)v.arr()[2].as_double();
        }
        return o;
    }

    engine::Vec3 cam_look_point() {
        engine::Vec3 s = engine::cam_source(), f = engine::cam_front();
        return engine::Vec3{s.x + f.x * 10.0f, s.y + f.y * 10.0f, s.z + f.z * 10.0f};
    }

    J::Value pose_value() {
        J::Value po = J::Value::object();
        po.set("pos", vec3(engine::cam_source()));
        po.set("look", vec3(cam_look_point()));
        po.set("fov_h_deg", J::Value((double)engine::cam_fov()));
        return po;
    }

    J::Value camera_result() {
        J::Value r = J::Value::object();
        r.set("pose", pose_value());
        r.set("fov_h_deg", J::Value((double)engine::cam_fov()));
        r.set("near", J::Value(0.1));
        r.set("far", J::Value(3000.0));
        r.set("rev", J::Value((std::int64_t)cam_rev_));
        return r;
    }

    J::Value env_result() {
        J::Value e = J::Value::object();
        char t[6]; std::snprintf(t, sizeof t, "%02d:%02d", engine::clock_hours(), engine::clock_minutes());
        e.set("time", J::Value(std::string(t)));
        e.set("weather", J::Value((std::int64_t)engine::weather_now()));
        e.set("freeze", J::Value(engine::frozen()));
        return e;
    }

    bool status(J::Value& result) {
        result = J::Value::object();
        result.set("frame", J::Value((std::int64_t)engine::frame_counter()));
        result.set("pose", pose_value());
        result.set("env", env_result());
        J::Value streaming = J::Value::object();
        streaming.set("pending", J::Value((std::int64_t)0));
        result.set("streaming", streaming);
        J::Value rev = J::Value::object();
        rev.set("scene", J::Value((std::int64_t)scene_rev_));
        rev.set("camera", J::Value((std::int64_t)cam_rev_));
        result.set("rev", rev);
        return true;
    }

    bool parse_pose(const J::Value& pose, Error& err, engine::Vec3& src, engine::Vec3& look, double& fov) {
        if (!pose.is_object() || !saap::check::is_vec3(pose["pos"])) {
            err = Error::make("BAD_PARAMS", "pose.pos must be a vec3"); return false;
        }
        const bool has_look = pose.has("look"), has_ypr = pose.has("ypr");
        if (has_look == has_ypr) { err = Error::make("BAD_PARAMS", "pose needs exactly one of look / ypr"); return false; }
        src = read_vec3(pose["pos"]);
        if (has_look) {
            if (!saap::check::is_vec3(pose["look"])) { err = Error::make("BAD_PARAMS", "pose.look must be a vec3"); return false; }
            look = read_vec3(pose["look"]);
        } else {
            const J::Value& y = pose["ypr"];
            if (!y.is_array() || y.size() != 3) { err = Error::make("BAD_PARAMS", "pose.ypr must be 3 numbers"); return false; }
            const double yaw = y.arr()[0].as_double() * 3.14159265358979 / 180.0;
            const double pitch = y.arr()[1].as_double() * 3.14159265358979 / 180.0;
            look.x = src.x + (float)(-std::sin(yaw) * std::cos(pitch)) * 10.0f;
            look.y = src.y + (float)(std::cos(yaw) * std::cos(pitch)) * 10.0f;
            look.z = src.z + (float)(std::sin(pitch)) * 10.0f;
        }
        fov = pose.has("fov_h_deg") ? pose["fov_h_deg"].as_double(70.0) : (double)engine::cam_fov();
        return true;
    }

    bool camera_set(const J::Value& p, J::Value& result, Error& err) {
        if (!p.is_object() || !p.has("pose")) { err = Error::make("BAD_PARAMS", "pose is required"); return false; }
        engine::Vec3 src, look; double fov;
        if (!parse_pose(p["pose"], err, src, look, fov)) return false;
        if (p.has("expect_rev") && p["expect_rev"].as_int(-1) != (std::int64_t)cam_rev_) {
            err = Error::make("REVISION", "stale expect_rev");
            err.data = J::Value::object(); err.data.set("rev", J::Value((std::int64_t)cam_rev_));
            return false;
        }
        if (p.has("fov_h_deg")) fov = p["fov_h_deg"].as_double(fov);
        engine::set_fixed_camera(src, look, (float)fov);
        ++cam_rev_;
        result = J::Value::object();
        result.set("pose", pose_value());
        result.set("rev", J::Value((std::int64_t)cam_rev_));
        if (p.has("stream") && p["stream"].as_string() != "none") {
            engine::stream_around(src);
            result.set("settled", J::Value(true));
        }
        return true;
    }

    bool settle(const J::Value& p, J::Value& result) {
        (void)p;
        engine::stream_around(engine::cam_source());
        result = J::Value::object();
        result.set("settled", J::Value(true));
        result.set("frames", J::Value((std::int64_t)1));
        result.set("pending", J::Value((std::int64_t)0));
        return true;
    }

    bool capture(const J::Value& p, J::Value& result, Error& err) {
        if (!p.is_object() || !p["path_prefix"].is_string()) { err = Error::make("BAD_PARAMS", "path_prefix is required"); return false; }
        const std::string prefix = p["path_prefix"].as_string();
        if (!inside_out_root(prefix)) {
            err = Error::make("BAD_PARAMS", "path_prefix must be inside the out-root");
            err.data = J::Value::object(); err.data.set("out_root", J::Value(out_root_));
            return false;
        }
        if (p.has("layers")) {
            for (const auto& l : p["layers"].arr()) {
                const std::string s = l.as_string();
                if (s != "color") {
                    err = Error::make("UNSUPPORTED", "only the color layer is available in single player");
                    err.data = J::Value::object(); err.data.set("capability", J::Value("capture." + s));
                    return false;
                }
            }
        }
        const std::string color = prefix + ".png";
        capture::Result cr = capture::to_png(color);
        if (!cr.ok) { err = Error::make("NOT_READY", cr.error, true); return false; }
        J::Value files = J::Value::object();
        files.set("color", J::Value(color));
        result = J::Value::object();
        result.set("files", files);
        result.set("w", J::Value((std::int64_t)cr.w));
        result.set("h", J::Value((std::int64_t)cr.h));
        result.set("pose", pose_value());
        result.set("settled", J::Value(true));
        result.set("pending", J::Value((std::int64_t)0));
        result.set("sha256", J::Value(cr.sha256));
        return true;
    }

    // ray through a window/capture pixel using the camera basis and a (horizontal) FOV.
    bool make_ray(double px, double py, int w, int h, double fov_h_deg, engine::Vec3& from, engine::Vec3& dir) {
        engine::Vec3 s = engine::cam_source(), f = engine::cam_front(), u = engine::cam_up();
        // right = f x u
        engine::Vec3 right{f.y * u.z - f.z * u.y, f.z * u.x - f.x * u.z, f.x * u.y - f.y * u.x};
        const double tan_h = std::tan(fov_h_deg * 3.14159265358979 / 360.0);
        const double aspect = h > 0 ? (double)w / (double)h : 1.3333;
        const double ndc_x = ((px + 0.5) / (w > 0 ? w : 1)) * 2.0 - 1.0;
        const double ndc_y = 1.0 - ((py + 0.5) / (h > 0 ? h : 1)) * 2.0;
        const double sx = ndc_x * tan_h;
        const double sy = ndc_y * (tan_h / aspect);
        engine::Vec3 d{(float)(f.x + right.x * sx + u.x * sy), (float)(f.y + right.y * sx + u.y * sy),
                       (float)(f.z + right.z * sx + u.z * sy)};
        const double len = std::sqrt((double)d.x * d.x + (double)d.y * d.y + (double)d.z * d.z);
        if (len < 1e-6) return false;
        from = s;
        dir = engine::Vec3{(float)(d.x / len), (float)(d.y / len), (float)(d.z / len)};
        return true;
    }

    bool line_of_sight(const engine::Vec3& a, const engine::Vec3& b, engine::Vec3& hit, engine::Vec3& normal,
                       void*& out_ent) {
        std::uint8_t colpoint[0x2C] = {0};
        out_ent = nullptr;
        using Fn = bool(__cdecl*)(const engine::Vec3&, const engine::Vec3&, void*, void**,
                                  bool, bool, bool, bool, bool, bool, bool, bool);
        bool ok = reinterpret_cast<Fn>(addr::CWorld_ProcessLineOfSight)(
            a, b, colpoint, &out_ent, true, true, true, true, true, false, false, false);
        if (ok) {
            hit = *reinterpret_cast<engine::Vec3*>(colpoint + 0x00);      // CColPoint::m_vecPoint
            normal = *reinterpret_cast<engine::Vec3*>(colpoint + 0x10);   // CColPoint::m_vecNormal
        }
        return ok;
    }

    J::Value entity_ref_value(void* ent) {
        J::Value e = J::Value::object();
        const int t = engine::entity_type(ent);
        const char* kind = t == 1 ? "building" : t == 2 ? "vehicle" : t == 3 ? "ped" : t == 4 ? "object" : "dummy";
        char ref[20]; std::snprintf(ref, sizeof ref, "%c%p", kind[0], ent);
        e.set("ref", J::Value(std::string(ref)));
        e.set("kind", J::Value(std::string(kind)));
        e.set("model_id", J::Value((std::int64_t)engine::entity_model(ent)));
        e.set("pos", vec3(engine::entity_pos(ent)));
        J::Value src = J::Value::object();
        src.set("kind", J::Value(std::string("runtime")));
        src.set("type", J::Value(std::string(kind)));
        e.set("src", src);
        return e;
    }

    bool pick(const J::Value& p, J::Value& result, Error& err) {
        if (!p.is_object() || !p["points"].is_array() || p["points"].size() < 1) {
            err = Error::make("BAD_PARAMS", "points must be a non-empty array"); return false;
        }
        int w = 0, h = 0; double fov = engine::cam_fov();
        if (p.has("w")) w = (int)p["w"].as_int(0);
        if (p.has("h")) h = (int)p["h"].as_int(0);
        if (p.has("pose") && p["pose"].has("fov_h_deg")) fov = p["pose"]["fov_h_deg"].as_double(fov);
        if (w == 0 || h == 0) { if (IDirect3DDevice9* dev = capture::device()) { IDirect3DSurface9* rt=nullptr;
            if (SUCCEEDED(dev->GetRenderTarget(0,&rt)) && rt) { D3DSURFACE_DESC d{}; rt->GetDesc(&d); rt->Release();
                if (w==0) w=(int)d.Width; if (h==0) h=(int)d.Height; } } }
        if (w == 0) w = 800; if (h == 0) h = 600;
        J::Value hits = J::Value::array();
        for (const auto& pt : p["points"].arr()) {
            double px = pt.is_array() && pt.size() == 2 ? pt.arr()[0].as_double() : 0;
            double py = pt.is_array() && pt.size() == 2 ? pt.arr()[1].as_double() : 0;
            J::Value hit = J::Value::object();
            hit.set("px", J::Value(px)); hit.set("py", J::Value(py));
            engine::Vec3 from, dir;
            if (make_ray(px, py, w, h, fov, from, dir)) {
                engine::Vec3 to{from.x + dir.x * 3000.0f, from.y + dir.y * 3000.0f, from.z + dir.z * 3000.0f};
                engine::Vec3 hp, nrm; void* ent = nullptr;
                if (line_of_sight(from, to, hp, nrm, ent)) {
                    hit.set("hit", J::Value(true));
                    hit.set("pos", vec3(hp)); hit.set("normal", vec3(nrm));
                    const double d = std::sqrt((double)(hp.x-from.x)*(hp.x-from.x)+(double)(hp.y-from.y)*(hp.y-from.y)+(double)(hp.z-from.z)*(hp.z-from.z));
                    hit.set("dist", J::Value(d));
                    if (ent) hit.set("entity", entity_ref_value(ent));
                } else { hit.set("hit", J::Value(false)); }
            } else { hit.set("hit", J::Value(false)); }
            hits.push(hit);
        }
        result = J::Value::object();
        result.set("hits", hits);
        return true;
    }

    bool raycast(const J::Value& p, J::Value& result, Error& err) {
        if (!p.is_object() || !saap::check::is_vec3(p["from"]) || !saap::check::is_vec3(p["to"])) {
            err = Error::make("BAD_PARAMS", "from and to must be vec3"); return false;
        }
        engine::Vec3 a = read_vec3(p["from"]), b = read_vec3(p["to"]);
        engine::Vec3 hp, nrm; void* ent = nullptr;
        result = J::Value::object();
        if (line_of_sight(a, b, hp, nrm, ent)) {
            result.set("hit", J::Value(true));
            result.set("pos", vec3(hp)); result.set("normal", vec3(nrm));
            const double d = std::sqrt((double)(hp.x-a.x)*(hp.x-a.x)+(double)(hp.y-a.y)*(hp.y-a.y)+(double)(hp.z-a.z)*(hp.z-a.z));
            result.set("dist", J::Value(d));
            if (ent) result.set("entity", entity_ref_value(ent));
        } else { result.set("hit", J::Value(false)); }
        return true;
    }

    bool entity_query(const J::Value& p, J::Value& result, Error& err) {
        if (!p.is_object() || !saap::check::is_vec3(p["center"]) || !p["r"].is_number()) {
            err = Error::make("BAD_PARAMS", "center (vec3) and r are required"); return false;
        }
        engine::Vec3 c = read_vec3(p["center"]);
        const double r = p["r"].as_double();
        const int limit = p.has("limit") ? (int)p["limit"].as_int(50) : 50;
        const int start = p.has("cursor") ? (int)std::strtol(p["cursor"].as_string().c_str(), nullptr, 10) : 0;

        struct Hit { void* ent; double d; };
        std::vector<Hit> found;
        const struct { std::uint32_t ptr; std::uint32_t stride; } pools[] = {
            {addr::ms_pVehiclePool_ptr, addr::ms_pVehiclePool_stride},
            {addr::ms_pPedPool_ptr, addr::ms_pPedPool_stride},
            {addr::ms_pObjectPool_ptr, addr::ms_pObjectPool_stride},
            {addr::ms_pBuildingPool_ptr, addr::ms_pBuildingPool_stride},
            {addr::ms_pDummyPool_ptr, addr::ms_pDummyPool_stride},
        };
        for (const auto& pl : pools) {
            engine::Pool pv;
            if (!engine::pool_view(pl.ptr, pv)) continue;
            for (int i = 0; i < pv.size; ++i) {
                if (!engine::slot_used(pv, i)) continue;
                void* ent = engine::pool_object(pv, i, pl.stride);
                engine::Vec3 ep = engine::entity_pos(ent);
                const double dx = ep.x - c.x, dy = ep.y - c.y;
                const double dist = std::sqrt(dx * dx + dy * dy);
                if (dist <= r) found.push_back({ent, dist});
                if (found.size() > 4096) break;  // safety cap
            }
        }
        std::sort(found.begin(), found.end(), [](const Hit& a, const Hit& b) { return a.d < b.d; });
        J::Value items = J::Value::array();
        int i = start;
        for (; i < (int)found.size() && (int)items.size() < limit; ++i) items.push(entity_ref_value(found[i].ent));
        result = J::Value::object();
        result.set("items", items);
        result.set("total", J::Value((std::int64_t)found.size()));
        if (i < (int)found.size()) { char cur[16]; std::snprintf(cur, sizeof cur, "%d", i); result.set("next", J::Value(std::string(cur))); }
        return true;
    }

    bool entity_inspect(const J::Value& p, J::Value& result, Error& err) {
        const std::string ref = p["ref"].as_string();
        void* ent = nullptr;
        if (ref.size() > 1) ent = (void*)std::strtoull(ref.c_str() + 1, nullptr, 16);
        if (!ent) { err = Error::make("NOT_FOUND", "unknown ref: " + ref); return false; }
        result = J::Value::object();
        result.set("entity", entity_ref_value(ent));
        J::Value model = J::Value::object();
        model.set("id", J::Value((std::int64_t)engine::entity_model(ent)));
        result.set("model", model);
        J::Value lod = J::Value::object();
        lod.set("children", J::Value::array());
        result.set("lod", lod);
        return true;
    }

    bool env_set(const J::Value& p, J::Value& result, Error& err) {
        if (!p.is_object() || p.size() == 0) { err = Error::make("BAD_PARAMS", "env.set needs at least one field"); return false; }
        if (p.has("time")) {
            const std::string t = p["time"].as_string();
            if (t.size() != 5 || t[2] != ':') { err = Error::make("BAD_PARAMS", "time must be HH:MM"); return false; }
            int hh = (t[0]-'0')*10 + (t[1]-'0'), mm = (t[3]-'0')*10 + (t[4]-'0');
            if (hh < 0 || hh > 23 || mm < 0 || mm > 59) { err = Error::make("BAD_PARAMS", "time out of range"); return false; }
            engine::set_clock(hh, mm);
        }
        if (p.has("weather")) engine::force_weather((int)p["weather"].as_int(0));
        if (p.has("freeze")) engine::set_freeze(p["freeze"].as_bool(false));
        ++scene_rev_;
        result = env_result();
        return true;
    }

    bool log_poll(const J::Value& p, J::Value& result) {
        std::int64_t since = p.has("since") ? p["since"].as_int(0) : 0;
        int maxn = p.has("max") ? (int)p["max"].as_int(100) : 100;
        J::Value items = J::Value::array();
        for (const auto& it : log_) {
            if ((std::int64_t)it.seq <= since) continue;
            if ((int)items.size() >= maxn) break;
            J::Value o = J::Value::object();
            o.set("seq", J::Value((std::int64_t)it.seq));
            o.set("t", J::Value(it.t));
            o.set("stream", J::Value(it.stream));
            o.set("level", J::Value(it.level));
            o.set("msg", J::Value(it.msg));
            items.push(o);
        }
        result = J::Value::object();
        result.set("items", items);
        result.set("next_seq", J::Value((std::int64_t)log_seq_));
        result.set("dropped", J::Value((std::int64_t)log_dropped_base_));
        return true;
    }

    bool console_exec(const J::Value& p, J::Value& result, Error& err) {
        if (!p.has("line") || !p["line"].is_string() || p["line"].as_string().empty()) {
            err = Error::make("BAD_PARAMS", "line is required"); return false;
        }
        result = J::Value::object();
        result.set("accepted", J::Value(false));
        J::Value out = J::Value::array();
        out.push(J::Value(std::string("single-player has no in-game console; use the sp.* methods")));
        result.set("output", out);
        return true;
    }

    // --- sp.* extension methods ---

    bool sp_teleport(const J::Value& p, J::Value& result, Error& err) {
        if (!p.is_object() || !saap::check::is_vec3(p["pos"])) { err = Error::make("BAD_PARAMS", "pos (vec3) is required"); return false; }
        engine::Vec3 pos = read_vec3(p["pos"]);
        void* veh = engine::player_vehicle();
        void* subject = veh ? veh : engine::player_ped();
        if (!subject) { err = Error::make("NOT_READY", "no player ped yet", true); return false; }
        engine::stream_around(pos);
        engine::teleport_entity(subject, pos, p.has("reset") ? p["reset"].as_bool(false) : false);
        log("console", "info", "sp.teleport");
        result = J::Value::object();
        result.set("pos", vec3(pos));
        result.set("in_vehicle", J::Value(veh != nullptr));
        return true;
    }

    bool sp_spawn(const J::Value& p, J::Value& result, Error& err) {
        if (!p.is_object() || !p["model"].is_number() || !saap::check::is_vec3(p["pos"])) {
            err = Error::make("BAD_PARAMS", "model (int) and pos (vec3) are required"); return false;
        }
        const int model = (int)p["model"].as_int(0);
        const std::string kind = p.has("kind") ? p["kind"].as_string() : "object";
        engine::Vec3 pos = read_vec3(p["pos"]);
        void* ent = nullptr;
        if (kind == "vehicle") ent = engine::spawn_vehicle(model, pos);
        else if (kind == "ped") ent = engine::spawn_ped(p.has("ped_type") ? (int)p["ped_type"].as_int(4) : 4, model, pos);
        else if (kind == "object") ent = engine::spawn_object(model, pos);
        else { err = Error::make("BAD_PARAMS", "kind must be vehicle, ped or object"); return false; }
        if (!ent) { err = Error::make("NOT_READY", "spawn failed (model not loadable?)", true); return false; }
        log("console", "info", "sp.spawn " + kind);
        result = entity_ref_value(ent);
        return true;
    }

    bool sp_hud(const J::Value& p, J::Value& result, Error& err) {
        (void)err;
        if (p.has("on")) engine::set_hud(p["on"].as_bool(true));
        if (p.has("radar")) engine::set_radar_hidden(!p["radar"].as_bool(true));
        result = J::Value::object();
        result.set("hud", J::Value(engine::hud_on()));
        return true;
    }

    // --- out-root path check ---

    static std::string normalize(const std::string& s) {
        std::string o = s;
        for (char& c : o) { if (c == '\\') c = '/'; c = (char)std::tolower((unsigned char)c); }
        while (!o.empty() && o.back() == '/') o.pop_back();
        return o;
    }
    bool inside_out_root(const std::string& path) const {
        if (out_root_.empty()) return false;
        const std::string n = normalize(path);
        if (n.size() <= out_root_.size()) return n == out_root_;
        return n.compare(0, out_root_.size(), out_root_) == 0 && n[out_root_.size()] == '/';
    }

    std::string out_root_;
    std::uint32_t cam_rev_ = 0;
    std::uint32_t scene_rev_ = 0;
    std::deque<LogItem> log_;
    std::uint32_t log_seq_ = 0;
    std::uint32_t log_dropped_base_ = 0;
};

}  // namespace sp

#endif  // SP_BACKEND_HPP
