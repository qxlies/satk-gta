// saap_json.hpp - a tiny, self-contained JSON value, parser and writer for satk_sp.asi.
//
// MIT License. Copyright (c) 2026 satk contributors.
//
// Header-only, C++14, no dependencies beyond the standard library. SAAP/1 keeps this MIT ASI
// free of the MTA fork's GPL code and of any third-party JSON library (SAAP-v1.md section 10
// leaves JSON to the host; the single-player bridge hosts it here). The subset is exactly what
// the SAAP methods need: objects, arrays, strings (with \uXXXX and surrogate pairs), numbers
// (int and double), booleans and null. Numbers round-trip losslessly for the values SAAP uses.
//
// This is not a fast or fully RFC-8259 compliant parser; it is small and correct for the frames
// SAAP exchanges (payloads <= 1 MiB). Depth is bounded to stop malicious nesting.

#ifndef SAAP_JSON_HPP
#define SAAP_JSON_HPP

#include <cstdint>
#include <cstdio>
#include <map>
#include <memory>
#include <string>
#include <vector>

namespace saap {
namespace json {

enum class Type { Null, Bool, Int, Double, String, Array, Object };

class Value;
using Array = std::vector<Value>;
using Object = std::map<std::string, Value>;

static const int kMaxDepth = 64;

class Value {
public:
    Value() : t_(Type::Null) {}
    Value(std::nullptr_t) : t_(Type::Null) {}
    Value(bool b) : t_(Type::Bool), b_(b) {}
    Value(int i) : t_(Type::Int), i_((std::int64_t)i) {}
    Value(std::int64_t i) : t_(Type::Int), i_(i) {}
    Value(std::uint32_t i) : t_(Type::Int), i_((std::int64_t)i) {}
    Value(double d) : t_(Type::Double), d_(d) {}
    Value(const char* s) : t_(Type::String), s_(s) {}
    Value(const std::string& s) : t_(Type::String), s_(s) {}
    Value(const Array& a) : t_(Type::Array), a_(new Array(a)) {}
    Value(const Object& o) : t_(Type::Object), o_(new Object(o)) {}

    Value(const Value& v) { copy(v); }
    Value& operator=(const Value& v) { if (this != &v) { clear(); copy(v); } return *this; }
    ~Value() { clear(); }

    Type type() const { return t_; }
    bool is_object() const { return t_ == Type::Object; }
    bool is_array() const { return t_ == Type::Array; }
    bool is_string() const { return t_ == Type::String; }
    bool is_number() const { return t_ == Type::Int || t_ == Type::Double; }
    bool is_int() const { return t_ == Type::Int; }
    bool is_bool() const { return t_ == Type::Bool; }
    bool is_null() const { return t_ == Type::Null; }

    bool as_bool(bool d = false) const { return t_ == Type::Bool ? b_ : d; }
    std::int64_t as_int(std::int64_t d = 0) const {
        if (t_ == Type::Int) return i_;
        if (t_ == Type::Double) return (std::int64_t)d_;
        return d;
    }
    double as_double(double d = 0.0) const {
        if (t_ == Type::Double) return d_;
        if (t_ == Type::Int) return (double)i_;
        return d;
    }
    const std::string& as_string(const std::string& d = empty_str()) const { return t_ == Type::String ? s_ : d; }

    const Array& arr() const { return a_ ? *a_ : empty_arr(); }
    const Object& obj() const { return o_ ? *o_ : empty_obj(); }

    // Object accessors (return a Null Value when absent / not an object).
    bool has(const std::string& k) const { return t_ == Type::Object && o_ && o_->count(k) != 0; }
    const Value& operator[](const std::string& k) const {
        static const Value nil;
        if (t_ != Type::Object || !o_) return nil;
        auto it = o_->find(k);
        return it == o_->end() ? nil : it->second;
    }

    // Builders.
    static Value object() { Value v; v.t_ = Type::Object; v.o_ = new Object(); return v; }
    static Value array() { Value v; v.t_ = Type::Array; v.a_ = new Array(); return v; }
    void set(const std::string& k, const Value& val) {
        if (t_ != Type::Object) { clear(); t_ = Type::Object; o_ = new Object(); }
        (*o_)[k] = val;
    }
    void push(const Value& val) {
        if (t_ != Type::Array) { clear(); t_ = Type::Array; a_ = new Array(); }
        a_->push_back(val);
    }
    std::size_t size() const { return t_ == Type::Array ? arr().size() : (t_ == Type::Object ? obj().size() : 0); }

    std::string dump() const { std::string out; write(out); return out; }

private:
    static const std::string& empty_str() { static const std::string e; return e; }
    static const Array& empty_arr() { static const Array e; return e; }
    static const Object& empty_obj() { static const Object e; return e; }

    void clear() {
        if (t_ == Type::Array) delete a_;
        else if (t_ == Type::Object) delete o_;
        a_ = nullptr; o_ = nullptr; t_ = Type::Null;
    }
    void copy(const Value& v) {
        t_ = v.t_;
        switch (t_) {
        case Type::Bool: b_ = v.b_; break;
        case Type::Int: i_ = v.i_; break;
        case Type::Double: d_ = v.d_; break;
        case Type::String: s_ = v.s_; break;
        case Type::Array: a_ = new Array(*v.a_); break;
        case Type::Object: o_ = new Object(*v.o_); break;
        default: break;
        }
    }

    void write(std::string& out) const;

    Type t_;
    bool b_ = false;
    std::int64_t i_ = 0;
    double d_ = 0.0;
    std::string s_;
    Array* a_ = nullptr;
    Object* o_ = nullptr;
};

// --- writer -----------------------------------------------------------------------------------

inline void write_escaped(std::string& out, const std::string& s) {
    static const char* hex = "0123456789abcdef";
    out += '"';
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
            if (c < 0x20) { out += "\\u00"; out += hex[c >> 4]; out += hex[c & 0xF]; }
            else out += (char)c;  // UTF-8 bytes pass through
        }
    }
    out += '"';
}

inline void write_number_double(std::string& out, double d) {
    // SAAP coordinates use up to a few decimals; %.6g keeps them compact and lossless enough.
    char buf[40];
    std::snprintf(buf, sizeof buf, "%.6g", d);
    // Guard against "inf"/"nan" which are not JSON: SAAP never sends them, but be safe.
    for (char* p = buf; *p; ++p) {
        if (*p == 'i' || *p == 'n' || *p == 'I' || *p == 'N') { out += "0"; return; }
    }
    out += buf;
}

inline void Value::write(std::string& out) const {
    switch (t_) {
    case Type::Null: out += "null"; break;
    case Type::Bool: out += b_ ? "true" : "false"; break;
    case Type::Int: { char b[24]; std::snprintf(b, sizeof b, "%lld", (long long)i_); out += b; break; }
    case Type::Double: write_number_double(out, d_); break;
    case Type::String: write_escaped(out, s_); break;
    case Type::Array: {
        out += '[';
        bool first = true;
        for (const auto& v : *a_) { if (!first) out += ','; first = false; v.write(out); }
        out += ']';
        break;
    }
    case Type::Object: {
        out += '{';
        bool first = true;
        for (const auto& kv : *o_) { if (!first) out += ','; first = false; write_escaped(out, kv.first); out += ':'; kv.second.write(out); }
        out += '}';
        break;
    }
    }
}

// --- parser -----------------------------------------------------------------------------------

class Parser {
public:
    explicit Parser(const std::string& s) : s_(s), n_(s.size()) {}

    bool parse(Value& out) {
        p_ = 0;
        depth_ = 0;
        skip_ws();
        if (!value(out)) return false;
        skip_ws();
        return p_ == n_;  // no trailing garbage
    }

private:
    bool value(Value& out) {
        if (depth_ > kMaxDepth) return false;
        if (p_ >= n_) return false;
        const char c = s_[p_];
        switch (c) {
        case '{': return object(out);
        case '[': return array(out);
        case '"': { std::string str; if (!string(str)) return false; out = Value(str); return true; }
        case 't': return literal("true", Value(true), out);
        case 'f': return literal("false", Value(false), out);
        case 'n': return literal("null", Value(nullptr), out);
        default: return number(out);
        }
    }

    bool literal(const char* lit, const Value& v, Value& out) {
        const std::size_t len = std::char_traits<char>::length(lit);
        if (p_ + len > n_ || s_.compare(p_, len, lit) != 0) return false;
        p_ += len;
        out = v;
        return true;
    }

    bool number(Value& out) {
        const std::size_t start = p_;
        bool is_double = false;
        if (p_ < n_ && (s_[p_] == '-')) ++p_;
        while (p_ < n_ && s_[p_] >= '0' && s_[p_] <= '9') ++p_;
        if (p_ < n_ && s_[p_] == '.') { is_double = true; ++p_; while (p_ < n_ && s_[p_] >= '0' && s_[p_] <= '9') ++p_; }
        if (p_ < n_ && (s_[p_] == 'e' || s_[p_] == 'E')) {
            is_double = true; ++p_;
            if (p_ < n_ && (s_[p_] == '+' || s_[p_] == '-')) ++p_;
            while (p_ < n_ && s_[p_] >= '0' && s_[p_] <= '9') ++p_;
        }
        if (p_ == start || (p_ == start + 1 && s_[start] == '-')) return false;
        const std::string tok = s_.substr(start, p_ - start);
        if (is_double) { out = Value(std::strtod(tok.c_str(), nullptr)); }
        else {
            errno = 0;
            char* end = nullptr;
            long long v = std::strtoll(tok.c_str(), &end, 10);
            if (errno != 0) out = Value(std::strtod(tok.c_str(), nullptr));
            else out = Value((std::int64_t)v);
        }
        return true;
    }

    bool string(std::string& out) {
        if (s_[p_] != '"') return false;
        ++p_;
        while (p_ < n_) {
            const char c = s_[p_++];
            if (c == '"') return true;
            if (c == '\\') {
                if (p_ >= n_) return false;
                const char e = s_[p_++];
                switch (e) {
                case '"': out += '"'; break;
                case '\\': out += '\\'; break;
                case '/': out += '/'; break;
                case 'b': out += '\b'; break;
                case 'f': out += '\f'; break;
                case 'n': out += '\n'; break;
                case 'r': out += '\r'; break;
                case 't': out += '\t'; break;
                case 'u': { if (!unicode(out)) return false; break; }
                default: return false;
                }
            } else {
                out += c;
            }
        }
        return false;  // unterminated
    }

    bool hex4(unsigned& cp) {
        if (p_ + 4 > n_) return false;
        cp = 0;
        for (int i = 0; i < 4; ++i) {
            const char c = s_[p_++];
            cp <<= 4;
            if (c >= '0' && c <= '9') cp |= (unsigned)(c - '0');
            else if (c >= 'a' && c <= 'f') cp |= (unsigned)(c - 'a' + 10);
            else if (c >= 'A' && c <= 'F') cp |= (unsigned)(c - 'A' + 10);
            else return false;
        }
        return true;
    }

    bool unicode(std::string& out) {
        unsigned cp = 0;
        if (!hex4(cp)) return false;
        if (cp >= 0xD800 && cp <= 0xDBFF) {  // high surrogate; expect a low one
            if (p_ + 2 > n_ || s_[p_] != '\\' || s_[p_ + 1] != 'u') return false;
            p_ += 2;
            unsigned lo = 0;
            if (!hex4(lo) || lo < 0xDC00 || lo > 0xDFFF) return false;
            cp = 0x10000 + (((cp - 0xD800) << 10) | (lo - 0xDC00));
        }
        // encode cp as UTF-8
        if (cp < 0x80) out += (char)cp;
        else if (cp < 0x800) { out += (char)(0xC0 | (cp >> 6)); out += (char)(0x80 | (cp & 0x3F)); }
        else if (cp < 0x10000) {
            out += (char)(0xE0 | (cp >> 12)); out += (char)(0x80 | ((cp >> 6) & 0x3F)); out += (char)(0x80 | (cp & 0x3F));
        } else {
            out += (char)(0xF0 | (cp >> 18)); out += (char)(0x80 | ((cp >> 12) & 0x3F));
            out += (char)(0x80 | ((cp >> 6) & 0x3F)); out += (char)(0x80 | (cp & 0x3F));
        }
        return true;
    }

    bool array(Value& out) {
        ++p_; ++depth_;
        out = Value::array();
        skip_ws();
        if (p_ < n_ && s_[p_] == ']') { ++p_; --depth_; return true; }
        for (;;) {
            Value v;
            skip_ws();
            if (!value(v)) return false;
            out.push(v);
            skip_ws();
            if (p_ >= n_) return false;
            if (s_[p_] == ',') { ++p_; continue; }
            if (s_[p_] == ']') { ++p_; --depth_; return true; }
            return false;
        }
    }

    bool object(Value& out) {
        ++p_; ++depth_;
        out = Value::object();
        skip_ws();
        if (p_ < n_ && s_[p_] == '}') { ++p_; --depth_; return true; }
        for (;;) {
            skip_ws();
            if (p_ >= n_ || s_[p_] != '"') return false;
            std::string key;
            if (!string(key)) return false;
            skip_ws();
            if (p_ >= n_ || s_[p_] != ':') return false;
            ++p_;
            skip_ws();
            Value v;
            if (!value(v)) return false;
            out.set(key, v);
            skip_ws();
            if (p_ >= n_) return false;
            if (s_[p_] == ',') { ++p_; continue; }
            if (s_[p_] == '}') { ++p_; --depth_; return true; }
            return false;
        }
    }

    void skip_ws() {
        while (p_ < n_) {
            const char c = s_[p_];
            if (c == ' ' || c == '\t' || c == '\n' || c == '\r') ++p_;
            else break;
        }
    }

    const std::string& s_;
    std::size_t n_;
    std::size_t p_ = 0;
    int depth_ = 0;
};

inline bool parse(const std::string& text, Value& out) { Parser p(text); return p.parse(out); }

}  // namespace json
}  // namespace saap

#endif  // SAAP_JSON_HPP
