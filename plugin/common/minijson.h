// Minimal JSON value/parser/writer for nvbridge. Game-free, header-only, C++17.
#pragma once
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <string>
#include <utility>
#include <vector>

namespace nvcoop::json {

class Value {
public:
    enum Type { Null, Bool, Number, String, Array, Object };
    using Member = std::pair<std::string, Value>;

    Value() = default;
    Value(std::nullptr_t) {}
    Value(bool b) : type_(Bool), b_(b) {}
    Value(int v) : type_(Number), n_(v) {}
    Value(int64_t v) : type_(Number), n_(static_cast<double>(v)) {}
    Value(uint64_t v) : type_(Number), n_(static_cast<double>(v)) {}
    Value(double v) : type_(Number), n_(v) {}
    Value(const char* s) : type_(String), s_(s) {}
    Value(std::string s) : type_(String), s_(std::move(s)) {}

    static Value array() { Value v; v.type_ = Array; return v; }
    static Value object() { Value v; v.type_ = Object; return v; }

    Type type() const { return type_; }
    bool isNull() const { return type_ == Null; }
    bool isObject() const { return type_ == Object; }
    bool isString() const { return type_ == String; }
    bool isNumber() const { return type_ == Number; }

    bool asBool(bool d = false) const { return type_ == Bool ? b_ : d; }
    double asNumber(double d = 0) const { return type_ == Number ? n_ : d; }
    int64_t asInt(int64_t d = 0) const { return type_ == Number ? static_cast<int64_t>(n_) : d; }
    const std::string& asString() const { return s_; }
    std::string asString(const std::string& d) const { return type_ == String ? s_ : d; }

    // Object access. set() replaces an existing key.
    Value& set(const std::string& k, Value v) {
        if (type_ != Object) { *this = object(); }
        for (auto& m : obj_) if (m.first == k) { m.second = std::move(v); return *this; }
        obj_.emplace_back(k, std::move(v));
        return *this;
    }
    const Value* find(const std::string& k) const {
        if (type_ != Object) return nullptr;
        for (auto& m : obj_) if (m.first == k) return &m.second;
        return nullptr;
    }
    const Value& get(const std::string& k) const {
        static const Value nul;
        const Value* v = find(k);
        return v ? *v : nul;
    }
    const std::vector<Member>& members() const { return obj_; }

    // Array access.
    Value& push(Value v) {
        if (type_ != Array) { *this = array(); }
        arr_.push_back(std::move(v));
        return *this;
    }
    const std::vector<Value>& items() const { return arr_; }

    std::string dump() const { std::string o; write(o); return o; }

    void write(std::string& o) const {
        switch (type_) {
        case Null: o += "null"; break;
        case Bool: o += b_ ? "true" : "false"; break;
        case Number: {
            char buf[40];
            if (std::isfinite(n_) && n_ == std::floor(n_) && std::fabs(n_) < 9e15)
                std::snprintf(buf, sizeof buf, "%lld", static_cast<long long>(n_));
            else if (std::isfinite(n_))
                std::snprintf(buf, sizeof buf, "%.9g", n_);
            else
                std::snprintf(buf, sizeof buf, "null");
            o += buf;
            break;
        }
        case String: writeString(o, s_); break;
        case Array:
            o += '[';
            for (size_t i = 0; i < arr_.size(); ++i) { if (i) o += ','; arr_[i].write(o); }
            o += ']';
            break;
        case Object:
            o += '{';
            for (size_t i = 0; i < obj_.size(); ++i) {
                if (i) o += ',';
                writeString(o, obj_[i].first);
                o += ':';
                obj_[i].second.write(o);
            }
            o += '}';
            break;
        }
    }

    static void writeString(std::string& o, const std::string& s) {
        o += '"';
        for (unsigned char c : s) {
            switch (c) {
            case '"': o += "\\\""; break;
            case '\\': o += "\\\\"; break;
            case '\n': o += "\\n"; break;
            case '\r': o += "\\r"; break;
            case '\t': o += "\\t"; break;
            default:
                if (c < 0x20) { char b[8]; std::snprintf(b, sizeof b, "\\u%04x", c); o += b; }
                else o += static_cast<char>(c);
            }
        }
        o += '"';
    }

private:
    Type type_ = Null;
    bool b_ = false;
    double n_ = 0;
    std::string s_;
    std::vector<Value> arr_;
    std::vector<Member> obj_;
};

class Parser {
public:
    static bool parse(const std::string& text, Value& out, std::string& err) {
        Parser p(text);
        p.ws();
        if (!p.value(out, 0)) { err = p.err_.empty() ? "parse error" : p.err_; return false; }
        p.ws();
        if (p.i_ != text.size()) { err = "trailing characters"; return false; }
        return true;
    }

private:
    explicit Parser(const std::string& t) : t_(t) {}
    const std::string& t_;
    size_t i_ = 0;
    std::string err_;

    void ws() { while (i_ < t_.size() && (t_[i_] == ' ' || t_[i_] == '\t' || t_[i_] == '\n' || t_[i_] == '\r')) ++i_; }
    bool fail(const char* m) { if (err_.empty()) err_ = std::string(m) + " at " + std::to_string(i_); return false; }
    bool lit(const char* s) {
        size_t n = std::char_traits<char>::length(s);
        if (t_.compare(i_, n, s) != 0) return fail("bad literal");
        i_ += n;
        return true;
    }
    bool value(Value& v, int depth) {
        if (depth > 64) return fail("too deep");
        if (i_ >= t_.size()) return fail("unexpected end");
        char c = t_[i_];
        if (c == '{') return object(v, depth);
        if (c == '[') return array(v, depth);
        if (c == '"') { std::string s; if (!str(s)) return false; v = Value(std::move(s)); return true; }
        if (c == 't') { if (!lit("true")) return false; v = Value(true); return true; }
        if (c == 'f') { if (!lit("false")) return false; v = Value(false); return true; }
        if (c == 'n') { if (!lit("null")) return false; v = Value(); return true; }
        return number(v);
    }
    bool number(Value& v) {
        const char* b = t_.c_str() + i_;
        char* e = nullptr;
        double d = std::strtod(b, &e);
        if (e == b) return fail("bad value");
        i_ += static_cast<size_t>(e - b);
        v = Value(d);
        return true;
    }
    static void utf8(std::string& o, unsigned cp) {
        if (cp < 0x80) o += static_cast<char>(cp);
        else if (cp < 0x800) { o += static_cast<char>(0xC0 | (cp >> 6)); o += static_cast<char>(0x80 | (cp & 0x3F)); }
        else if (cp < 0x10000) { o += static_cast<char>(0xE0 | (cp >> 12)); o += static_cast<char>(0x80 | ((cp >> 6) & 0x3F)); o += static_cast<char>(0x80 | (cp & 0x3F)); }
        else { o += static_cast<char>(0xF0 | (cp >> 18)); o += static_cast<char>(0x80 | ((cp >> 12) & 0x3F)); o += static_cast<char>(0x80 | ((cp >> 6) & 0x3F)); o += static_cast<char>(0x80 | (cp & 0x3F)); }
    }
    bool hex4(unsigned& cp) {
        if (i_ + 4 > t_.size()) return fail("bad \\u");
        cp = 0;
        for (int k = 0; k < 4; ++k) {
            char h = t_[i_++];
            cp <<= 4;
            if (h >= '0' && h <= '9') cp |= h - '0';
            else if (h >= 'a' && h <= 'f') cp |= h - 'a' + 10;
            else if (h >= 'A' && h <= 'F') cp |= h - 'A' + 10;
            else return fail("bad \\u");
        }
        return true;
    }
    bool str(std::string& o) {
        ++i_;
        while (i_ < t_.size()) {
            char c = t_[i_++];
            if (c == '"') return true;
            if (c != '\\') { o += c; continue; }
            if (i_ >= t_.size()) break;
            char e = t_[i_++];
            switch (e) {
            case '"': o += '"'; break;
            case '\\': o += '\\'; break;
            case '/': o += '/'; break;
            case 'b': o += '\b'; break;
            case 'f': o += '\f'; break;
            case 'n': o += '\n'; break;
            case 'r': o += '\r'; break;
            case 't': o += '\t'; break;
            case 'u': {
                unsigned cp;
                if (!hex4(cp)) return false;
                if (cp >= 0xD800 && cp < 0xDC00 && t_.compare(i_, 2, "\\u") == 0) {
                    i_ += 2;
                    unsigned lo;
                    if (!hex4(lo)) return false;
                    cp = 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00);
                }
                utf8(o, cp);
                break;
            }
            default: return fail("bad escape");
            }
        }
        return fail("unterminated string");
    }
    bool array(Value& v, int depth) {
        ++i_;
        v = Value::array();
        ws();
        if (i_ < t_.size() && t_[i_] == ']') { ++i_; return true; }
        for (;;) {
            Value e;
            ws();
            if (!value(e, depth + 1)) return false;
            v.push(std::move(e));
            ws();
            if (i_ >= t_.size()) return fail("unterminated array");
            if (t_[i_] == ',') { ++i_; continue; }
            if (t_[i_] == ']') { ++i_; return true; }
            return fail("expected , or ]");
        }
    }
    bool object(Value& v, int depth) {
        ++i_;
        v = Value::object();
        ws();
        if (i_ < t_.size() && t_[i_] == '}') { ++i_; return true; }
        for (;;) {
            ws();
            if (i_ >= t_.size() || t_[i_] != '"') return fail("expected key");
            std::string k;
            if (!str(k)) return false;
            ws();
            if (i_ >= t_.size() || t_[i_] != ':') return fail("expected :");
            ++i_;
            ws();
            Value e;
            if (!value(e, depth + 1)) return false;
            v.set(k, std::move(e));
            ws();
            if (i_ >= t_.size()) return fail("unterminated object");
            if (t_[i_] == ',') { ++i_; continue; }
            if (t_[i_] == '}') { ++i_; return true; }
            return fail("expected , or }");
        }
    }
};

inline bool parse(const std::string& text, Value& out, std::string& err) { return Parser::parse(text, out, err); }

} // namespace nvcoop::json
