#include "areadigest/area_config.h"

#include <algorithm>
#include <cctype>
#include <cstdio>
#include <cstdlib>
#include "fnvcoop_mut.h"

namespace nvcoop::areadigest {

bool parseBool(const std::string& v) {
    size_t a = 0, b = v.size();
    while (a < b && std::isspace(static_cast<unsigned char>(v[a]))) ++a;
    while (b > a && std::isspace(static_cast<unsigned char>(v[b - 1]))) --b;
    std::string t;
    for (size_t i = a; i < b; ++i) t += static_cast<char>(std::tolower(static_cast<unsigned char>(v[i])));
    return t == "1" || t == "true" || t == "on" || t == "yes";
}

namespace {

struct Reader {
    const IniGetter& get;
    std::string warn;

    bool flag(const char* key, bool def) {
        const std::string v = get("areadigest", key, def ? "1" : "0");
        if (v.empty()) return def;
        return parseBool(v);
    }
    double number(const char* key, double def, double lo, double hi) {
        const std::string v = get("areadigest", key, "");
        if (v.empty()) return def;
        char* end = nullptr;
        const double x = std::strtod(v.c_str(), &end);
        if (end == v.c_str() || (end && *end != '\0' && !std::isspace(static_cast<unsigned char>(*end)))) {
            warn += std::string("areadigest ") + key + "='" + v + "' is not a number, using " + std::to_string(def) + "\n";
            return def;
        }
        if (FNV_MUT(62)) return x;    // twin 62: values are not clamped
        if (x < lo || x > hi) {
            const double c = std::min(hi, std::max(lo, x));
            warn += std::string("areadigest ") + key + "=" + v + " is outside " + std::to_string(lo) + ".." + std::to_string(hi) + ", using " + std::to_string(c) + "\n";
            return c;
        }
        return x;
    }
};

}  // namespace

AreaSettings readSettings(const IniGetter& get) {
    AreaSettings s;
    if (FNV_MUT(61)) { s.enabled = false; return s; }   // twin 61: the ini is ignored
    Reader r{get, {}};
    s.enabled = FNV_MUT(63) ? true : r.flag("enabled", false);   // twin 63: on by default
    fnvcoop::areadigest::Config& c = s.cfg;
    c.intervalMs = static_cast<uint32_t>(r.number("intervalMs", c.intervalMs, 500, 30000));
    c.settleMs = static_cast<uint32_t>(r.number("settleMs", c.settleMs, 200, 10000));
    c.smoothMetres = static_cast<float>(r.number("smoothMetres", c.smoothMetres, 0.5, 50));
    c.snapMetres = static_cast<float>(r.number("snapMetres", c.snapMetres, static_cast<double>(c.smoothMetres) + 0.5, 500));
    c.snapMetres = std::max(c.snapMetres, c.smoothMetres + 0.5f);   // a default snap distance must also stay above a raised smooth distance
    c.posBucketMetres = c.smoothMetres;          // the digest sees every error the smooth rule would act on
    c.hpTolerance = static_cast<float>(r.number("hpTolerance", c.hpTolerance, 0.5, 100));
    c.hpBucket = std::max(c.hpBucket, c.hpTolerance * 2.0f);   // the bucket stays wider than the tolerance: a straddle is never a heal
    c.healBudgetPerMin = static_cast<uint32_t>(r.number("healBudgetPerMin", c.healBudgetPerMin, 0, 5000));
    s.maxActors = static_cast<size_t>(r.number("maxActors", static_cast<double>(s.maxActors), 20, 2000));
    s.actors = r.flag("actors", true);
    s.containers = r.flag("containers", true);
    s.objects = r.flag("objects", true);
    s.story = r.flag("story", true);
    c.compareStory = s.story;
    s.warnings = r.warn;
    return s;
}

std::string describe(const AreaSettings& s) {
    char b[320];
    std::snprintf(b, sizeof b, "enabled=%d interval=%u settle=%u smooth=%.1fm snap=%.1fm hpTol=%.1f budget=%u/min maxActors=%zu actors=%d containers=%d objects=%d story=%d",
                  s.enabled ? 1 : 0, s.cfg.intervalMs, s.cfg.settleMs, s.cfg.smoothMetres, s.cfg.snapMetres, s.cfg.hpTolerance, s.cfg.healBudgetPerMin, s.maxActors,
                  s.actors ? 1 : 0, s.containers ? 1 : 0, s.objects ? 1 : 0, s.story ? 1 : 0);
    return b;
}

}  // namespace nvcoop::areadigest
