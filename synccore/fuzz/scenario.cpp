// Scenario generator + JSON (de)serialization for the co-op fuzzer.
#include <algorithm>
#include <cctype>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include "fuzz.h"
#include "fnvcoop/rng.h"

namespace fnvcoop::fuzz {

static const char* kOpNames[] = {"move", "hit", "pickup", "drop", "spawn", "activate", "join", "leave", "save", "load", "link", "stale", "corrupt", "recruit", "container", "projectile", "explosion", "limbhit", "simulkill"};

const char* opName(OpKind k) { return kOpNames[static_cast<int>(k)]; }
bool opFromName(const std::string& s, OpKind& out) {
    for (int i = 0; i < static_cast<int>(OpKind::Count); ++i)
        if (s == kOpNames[i]) { out = static_cast<OpKind>(i); return true; }
    return false;
}

Scenario generate(uint64_t seed, int machines, uint32_t durationMs, const std::string& target) {
    Rng r(seed * 0x9E3779B97F4A7C15ull + 77);
    Scenario s;
    s.seed = seed;
    s.machines = std::max(2, std::min(4, machines));
    s.durationMs = durationMs;
    s.link.minLatencyMs = r.range(0, 60);
    s.link.maxLatencyMs = s.link.minLatencyMs + r.range(0, 200);
    s.link.loss = r.chance(0.3) ? 0.0 : r.uni() * 0.35;
    s.link.duplicate = r.chance(0.4) ? 0.0 : r.uni() * 0.25;
    s.link.corrupt = r.chance(0.6) ? 0.0 : r.uni() * 0.03;

    auto add = [&](uint32_t t, OpKind k, int m, int a = 0, int b = 0, int c = 0, int d = 0) {
        Op o; o.t = t; o.kind = k; o.m = m; o.a = a; o.b = b; o.c = c; o.d = d;
        s.ops.push_back(o);
    };
    // Encounters first so there are actors to fight over.
    int early = static_cast<int>(r.range(2, 4));
    for (int i = 0; i < early; ++i) add(r.range(50, 1500), OpKind::Spawn, static_cast<int>(r.range(0, s.machines - 1)), static_cast<int>(r.range(0, 4)));

    // Rate: about 25 ops per virtual second spread over the weights.
    uint32_t count = std::max<uint32_t>(10, durationMs / 40);
    struct W { OpKind k; int w; };
    static const W weights[] = {{OpKind::Move, 30}, {OpKind::Hit, 13}, {OpKind::Pickup, 10}, {OpKind::Drop, 4}, {OpKind::Spawn, 6},
                                {OpKind::Activate, 6}, {OpKind::Join, 5}, {OpKind::Leave, 5}, {OpKind::Save, 1}, {OpKind::Load, 1},
                                {OpKind::SetLink, 3}, {OpKind::Stale, 8}};
    int total = 0;
    for (auto& w : weights) total += w.w;
    for (uint32_t i = 0; i < count; ++i) {
        uint32_t t = r.range(0, durationMs - 1);
        int pick = static_cast<int>(r.range(0, total - 1));
        OpKind k = OpKind::Move;
        for (auto& w : weights) { if (pick < w.w) { k = w.k; break; } pick -= w.w; }
        int m = static_cast<int>(r.range(0, s.machines - 1));
        switch (k) {
            case OpKind::Move: add(t, k, m, r.range(0, 1000), r.range(0, 1000), r.chance(0.15) ? r.range(0, 2) : 0); break;
            case OpKind::Hit: add(t, k, m, r.range(0, 15), r.range(5, 60)); break;
            case OpKind::Pickup: add(t, k, m, r.range(0, 15)); break;
            case OpKind::Drop: add(t, k, m, r.range(0, 7), r.range(0, 1000), r.range(0, 1000), r.range(0, 2)); break;
            case OpKind::Spawn: add(t, k, m, r.range(0, 4)); break;
            case OpKind::Activate: add(t, k, m, r.range(0, 3)); break;
            case OpKind::Join: case OpKind::Leave: add(t, k, 1 + static_cast<int>(r.range(0, s.machines - 2))); break;
            case OpKind::Save: case OpKind::Load: add(t, k, 0); break;
            case OpKind::SetLink: add(t, k, 0, r.range(0, 80), r.range(80, 400), r.range(0, 450), r.range(0, 300)); break;
            case OpKind::Stale: add(t, k, static_cast<int>(r.range(0, s.machines - 1)), static_cast<int>(r.range(0, s.machines - 1)), r.range(0, 31), r.range(0, 1)); break;
            default: break;
        }
    }
    // Pickup races: two or three players reach for the same item within one link round trip. Random pickups almost never collide, so without these
    // a host that grants every claim (pickup_no_arbitration) goes unnoticed. A separate Rng keeps the ops above identical to older versions.
    {
        Rng rr(seed * 0xD1B54A32D192ED03ull + 4242);
        const uint32_t races = std::max<uint32_t>(2, durationMs / 1500);
        for (uint32_t i = 0; i < races; ++i) {
            const uint32_t t = rr.range(0, durationMs - 1);
            const int idx = static_cast<int>(rr.range(0, 15));
            const int racers = std::min<int>(s.machines, 2 + static_cast<int>(rr.range(0, 1)));
            const int first = static_cast<int>(rr.range(0, s.machines - 1));
            for (int k = 0; k < racers; ++k) add(std::min(durationMs - 1, t + static_cast<uint32_t>(rr.range(0, 25))), OpKind::Pickup, (first + k) % s.machines, idx);
        }
    }
    if (target == "em") {
        // Extras that only the engine-model target understands. Each exists because a bug class of the model is invisible without it: the generic ops almost
        // never hit a guest's silent corruption, a lock race, a story NPC, a replayed drop request or ownership message, a companion, or a link slower than
        // the host's handoff timeout. Again a separate Rng, so the ops above are the same as without them.
        Rng rx(seed * 0xA24BAED4963EE407ull + 31337);
        const uint32_t sec = std::max<uint32_t>(1, durationMs / 1000);
        auto at = [&](uint32_t lo = 0) { return std::min(durationMs - 1, lo + static_cast<uint32_t>(rx.range(0, durationMs - 1 - std::min(lo, durationMs - 2)))); };
        auto guest = [&] { return 1 + static_cast<int>(rx.range(0, s.machines - 2)); };
        for (uint32_t i = 0; i < std::max<uint32_t>(2, sec / 3); ++i) add(at(), OpKind::Corrupt, guest(), static_cast<int>(rx.range(0, 31)), 0, static_cast<int>(rx.range(0, 3)));
        for (uint32_t i = 0; i < std::max<uint32_t>(1, sec / 5); ++i) add(at(), OpKind::Recruit, 0, static_cast<int>(rx.range(0, 15)), static_cast<int>(rx.range(0, s.machines - 1)));
        for (uint32_t i = 0; i < sec; ++i) add(at(), OpKind::Stale, static_cast<int>(rx.range(0, s.machines - 1)), static_cast<int>(rx.range(0, s.machines - 1)),
                                             static_cast<int>(rx.range(0, 31)), static_cast<int>(rx.range(0, 1)), 1 + static_cast<int>(rx.range(0, 1)));
        // container lock races: two machines open the same container together, then both try to take from it, then both close
        for (uint32_t i = 0; i < std::max<uint32_t>(1, sec / 3); ++i) {
            const uint32_t t = at();
            const int slot = static_cast<int>(rx.range(0, 15)), first = static_cast<int>(rx.range(0, s.machines - 1));
            for (int k = 0; k < std::min(2, s.machines); ++k) {
                const int mm = (first + k) % s.machines;
                const uint32_t j = static_cast<uint32_t>(rx.range(0, 20));
                add(std::min(durationMs - 1, t + j), OpKind::Container, mm, slot, 0, 1);
                add(std::min(durationMs - 1, t + 300 + j), OpKind::Container, mm, slot, 1, 1);
                add(std::min(durationMs - 1, t + 600 + j), OpKind::Container, mm, slot, 3, 1);
            }
        }
        // strikes: three hits of 60 on one actor, spaced so the attacker's step reaches the victim's owner first (story NPCs must survive, others die)
        for (uint32_t i = 0; i < std::max<uint32_t>(1, sec / 2); ++i) {
            const uint32_t t = at();
            const int who = static_cast<int>(rx.range(0, s.machines - 1)), slot = static_cast<int>(rx.range(0, 15)), named = rx.chance(0.5) ? 1 : 2;
            add(t, OpKind::Hit, who, slot, 0, named);   // the attacker steps next to the victim first (damage 0): its position has to reach the victim's owner before the hits
            for (int k = 0; k < 3; ++k) add(std::min(durationMs - 1, t + 700 + 250 * static_cast<uint32_t>(k)), OpKind::Hit, who, slot, 60, named);
        }
        // a host load right after a save, then replays of messages from before it
        if (rx.chance(0.5)) {
            const uint32_t t = at(1000);
            add(t > 300 ? t - 300 : 0, OpKind::Save, 0);
            add(t, OpKind::Load, 0);
            for (int k = 0; k < 3; ++k) add(std::min(durationMs - 1, t + 100 + 150 * static_cast<uint32_t>(k)), OpKind::Stale, static_cast<int>(rx.range(0, s.machines - 1)),
                                           static_cast<int>(rx.range(0, s.machines - 1)), k, 0, 3);
        }
        // a window in which the link is slower than the host's handoff timeout (1 s), with the host walking into a guest's cell inside it: the host commits the
        // handoff by timeout and starts driving the actors before the guest has heard of the Transfer
        uint32_t windowFrom = 0, windowTo = 0;   // the slow-link window below (virtual ms), so the combat ops can stay out of it
        if (durationMs > 6000 && rx.chance(0.8)) {
            const uint32_t t = 2200 + static_cast<uint32_t>(rx.range(0, durationMs - 5800));   // the whole window, and a second of aftermath, fits inside the scenario
            windowFrom = t - 2100; windowTo = t + 4200;
            const int g = guest();
            for (uint32_t k = 0; k < 14; ++k) {   // the guest stays connected and in cell 2 (the random leaves and moves would walk it away)
                add(t - 2100 + 150 * k, OpKind::Join, g);
                add(t - 2000 + 150 * k, OpKind::Move, g, 500, 500, 2);
            }
            add(t, OpKind::SetLink, 0, 900, 1500, 0, 0);
            for (uint32_t k = 0; k < 20; ++k) add(std::min(durationMs - 1, t + 300 + 150 * k), OpKind::Move, 0, 500, 500, 2);   // the host walks in and stays
            add(std::min(durationMs - 1, t + 3500), OpKind::SetLink, 0, 10, 60, 0, 0);
        }
        // a guest picks something up, drops it, and the drop request is replayed to the host
        for (uint32_t i = 0; i < std::max<uint32_t>(1, sec / 2); ++i) {
            const uint32_t t = at();
            const int who = guest();
            add(t, OpKind::Pickup, who, static_cast<int>(rx.range(0, 15)));
            add(std::min(durationMs - 1, t + 300), OpKind::Drop, who, 0, static_cast<int>(rx.range(0, 1000)), static_cast<int>(rx.range(0, 1000)), static_cast<int>(rx.range(0, 2)));
            add(std::min(durationMs - 1, t + 500), OpKind::Stale, 0, who, 0, 0, 4);
            add(std::min(durationMs - 1, t + 700), OpKind::Stale, 0, who, 0, 1, 4);
        }
        // late replays of old ownership assignments to the guests (nothing republishes the actors afterwards, so a guest that adopts one stays wrong)
        for (uint32_t i = 0; i < 4; ++i)
            add(durationMs - 1 - static_cast<uint32_t>(rx.range(0, durationMs / 5)), OpKind::Stale, 1 + static_cast<int>(rx.range(0, s.machines - 2)), 0, static_cast<int>(rx.range(0, 31)), 0, 5);

        // combat-core: projectiles (a visual copy must never hurt), explosions (damage through each victim's owner), limb hits (crippling by accumulated damage,
        // across handoffs) and simultaneous kills (one death, even with a story kill racing it). Once more a separate Rng, so every op above stays the same.
        Rng rc(seed * 0x94D049BB133111EBull + 1618);
        // Combat sessions are the even seeds. A fight kills the actors some older bug classes need to be seen (a forced commit needs a living actor in the guest's cell, a
        // small drift needs a walking one), so the odd seeds keep exactly the sessions they always had and the even ones add the fights.
        const bool combatSession = (seed % 2) == 0;
        // The slow-link window above exists to expose a forced commit that starts a second AI driver; a fight that kills the actors it needs would hide that
        // bug, so combat ops are moved out of the window (to its tail, or to its head when there is no tail).
        auto atc = [&] {
            uint32_t t = static_cast<uint32_t>(rc.range(0, durationMs - 1));
            if (windowTo > windowFrom && t >= windowFrom && t < windowTo) {
                if (windowTo + 400 < durationMs) t = windowTo + (t - windowFrom) % (durationMs - windowTo);
                else if (windowFrom > 400) t = t % windowFrom;
            }
            return std::min(t, durationMs - 1);
        };
        auto any = [&] { return static_cast<int>(rc.range(0, s.machines - 1)); };
        for (uint32_t i = 0; combatSession && i < std::max<uint32_t>(2, sec / 2); ++i) {
            add(atc(), OpKind::Projectile, any(), static_cast<int>(rc.range(0, 15)), static_cast<int>(rc.range(10, 50)), rc.chance(0.4) ? 1 : 0, static_cast<int>(rc.range(500, 1200)));
            add(atc(), OpKind::Explosion, any(), static_cast<int>(rc.range(0, 15)), static_cast<int>(rc.range(150, 450)), static_cast<int>(rc.range(20, 100)));
        }
        for (uint32_t i = 0; combatSession && i < std::max<uint32_t>(2, sec / 2); ++i) {
            const uint32_t t = atc();
            const int who = any(), limb = static_cast<int>(rc.range(1, 5)), dmg = static_cast<int>(rc.range(8, 20));
            for (uint32_t k = 0; k < 4; ++k) add(std::min(durationMs - 1, t + 250 * k), OpKind::LimbHit, who, 0, dmg, limb);
        }
        for (uint32_t i = 0; combatSession && i < std::max<uint32_t>(2, sec / 3); ++i) add(atc(), OpKind::SimulKill, any(), 0, any(), rc.chance(0.5) ? 1 : 0);
    }
    std::stable_sort(s.ops.begin(), s.ops.end(), [](const Op& a, const Op& b) { return a.t < b.t; });
    return s;
}

// ---------------------------------------------------------------- JSON
std::string toJson(const Scenario& s) {
    std::string o;
    char buf[256];
    std::snprintf(buf, sizeof buf, "{\n \"format\": \"simfuzz-1\",\n \"seed\": %llu,\n \"machines\": %d,\n \"durationMs\": %u,\n",
                  static_cast<unsigned long long>(s.seed), s.machines, s.durationMs);
    o += buf;
    std::snprintf(buf, sizeof buf, " \"link\": {\"minLatencyMs\": %u, \"maxLatencyMs\": %u, \"loss\": %.6f, \"duplicate\": %.6f, \"corrupt\": %.6f},\n",
                  s.link.minLatencyMs, s.link.maxLatencyMs, s.link.loss, s.link.duplicate, s.link.corrupt);
    o += buf;
    if (!s.profile.empty()) o += " \"profile\": \"" + s.profile + "\",\n";
    o += " \"target\": \"" + s.target + "\",\n";
    if (!s.expect.empty()) o += " \"expect\": \"" + s.expect + "\",\n";
    o += " \"mutations\": [";
    for (size_t i = 0; i < s.bugs.size(); ++i) { if (i) o += ", "; o += "\"" + s.bugs[i] + "\""; }
    o += "],\n \"ops\": [\n";
    for (size_t i = 0; i < s.ops.size(); ++i) {
        const Op& p = s.ops[i];
        std::snprintf(buf, sizeof buf, "  {\"t\": %u, \"op\": \"%s\", \"m\": %d, \"a\": %d, \"b\": %d, \"c\": %d, \"d\": %d}%s\n",
                      p.t, opName(p.kind), p.m, p.a, p.b, p.c, p.d, i + 1 < s.ops.size() ? "," : "");
        o += buf;
    }
    o += " ]\n}\n";
    return o;
}

namespace {
struct J {
    enum T { Null, Num, Str, Arr, Obj } t = Null;
    double n = 0;
    std::string s;
    std::vector<J> a;
    std::vector<std::pair<std::string, J>> o;
    const J* get(const char* k) const { for (auto& kv : o) if (kv.first == k) return &kv.second; return nullptr; }
};
struct Parser {
    const std::string& s;
    size_t i = 0;
    std::string err;
    explicit Parser(const std::string& text) : s(text) {}
    void ws() { while (i < s.size() && std::isspace(static_cast<unsigned char>(s[i]))) ++i; }
    bool fail(const char* m) { if (err.empty()) err = std::string(m) + " at " + std::to_string(i); return false; }
    bool value(J& v) {
        ws();
        if (i >= s.size()) return fail("eof");
        char c = s[i];
        if (c == '{') {
            v.t = J::Obj; ++i; ws();
            if (i < s.size() && s[i] == '}') { ++i; return true; }
            for (;;) {
                ws(); J k; if (!value(k) || k.t != J::Str) return fail("key");
                ws(); if (i >= s.size() || s[i] != ':') return fail("colon"); ++i;
                J x; if (!value(x)) return false;
                v.o.emplace_back(k.s, std::move(x));
                ws(); if (i < s.size() && s[i] == ',') { ++i; continue; }
                if (i < s.size() && s[i] == '}') { ++i; return true; }
                return fail("object");
            }
        }
        if (c == '[') {
            v.t = J::Arr; ++i; ws();
            if (i < s.size() && s[i] == ']') { ++i; return true; }
            for (;;) {
                J x; if (!value(x)) return false;
                v.a.push_back(std::move(x));
                ws(); if (i < s.size() && s[i] == ',') { ++i; continue; }
                if (i < s.size() && s[i] == ']') { ++i; return true; }
                return fail("array");
            }
        }
        if (c == '"') {
            v.t = J::Str; ++i;
            while (i < s.size() && s[i] != '"') { if (s[i] == '\\' && i + 1 < s.size()) ++i; v.s += s[i++]; }
            if (i >= s.size()) return fail("string");
            ++i; return true;
        }
        if (c == '-' || std::isdigit(static_cast<unsigned char>(c))) {
            char* e = nullptr; v.t = J::Num; v.n = std::strtod(s.c_str() + i, &e);
            if (e == s.c_str() + i) return fail("number");
            i = static_cast<size_t>(e - s.c_str()); return true;
        }
        if (s.compare(i, 4, "true") == 0) { v.t = J::Num; v.n = 1; i += 4; return true; }
        if (s.compare(i, 5, "false") == 0) { v.t = J::Num; v.n = 0; i += 5; return true; }
        if (s.compare(i, 4, "null") == 0) { i += 4; return true; }
        return fail("value");
    }
};
}  // namespace

bool fromJson(const std::string& text, Scenario& out, std::string* err) {
    Parser p(text);
    J root;
    if (!p.value(root) || root.t != J::Obj) { if (err) *err = "bad json: " + p.err; return false; }
    const J* f = root.get("format");
    if (!f || f->s != "simfuzz-1") { if (err) *err = "format is not simfuzz-1"; return false; }
    Scenario s;
    auto num = [&](const J& o, const char* k, double dflt) { const J* v = o.get(k); return v && v->t == J::Num ? v->n : dflt; };
    s.seed = static_cast<uint64_t>(num(root, "seed", 1));
    s.machines = static_cast<int>(num(root, "machines", 3));
    s.durationMs = static_cast<uint32_t>(num(root, "durationMs", 20000));
    if (const J* l = root.get("link")) {
        s.link.minLatencyMs = static_cast<uint32_t>(num(*l, "minLatencyMs", 0));
        s.link.maxLatencyMs = static_cast<uint32_t>(num(*l, "maxLatencyMs", 0));
        s.link.loss = num(*l, "loss", 0); s.link.duplicate = num(*l, "duplicate", 0); s.link.corrupt = num(*l, "corrupt", 0);
    }
    if (const J* pf = root.get("profile")) s.profile = pf->s;
    if (const J* tg = root.get("target")) s.target = tg->s;
    if (const J* ex = root.get("expect")) s.expect = ex->s;
    if (const J* m = root.get("mutations")) for (auto& x : m->a) s.bugs.push_back(x.s);
    const J* ops = root.get("ops");
    if (!ops || ops->t != J::Arr) { if (err) *err = "no ops array"; return false; }
    for (auto& oj : ops->a) {
        Op op;
        const J* n = oj.get("op");
        if (!n || !opFromName(n->s, op.kind)) { if (err) *err = "unknown op"; return false; }
        op.t = static_cast<uint32_t>(num(oj, "t", 0));
        op.m = static_cast<int>(num(oj, "m", 0)); op.a = static_cast<int>(num(oj, "a", 0)); op.b = static_cast<int>(num(oj, "b", 0));
        op.c = static_cast<int>(num(oj, "c", 0)); op.d = static_cast<int>(num(oj, "d", 0));
        s.ops.push_back(op);
    }
    std::stable_sort(s.ops.begin(), s.ops.end(), [](const Op& a, const Op& b) { return a.t < b.t; });
    out = std::move(s);
    return true;
}

std::string fnvHex(const std::string& str) {
    uint64_t h = 1469598103934665603ull;
    for (unsigned char c : str) { h ^= c; h *= 1099511628211ull; }
    char b[20];
    std::snprintf(b, sizeof b, "%08llx", static_cast<unsigned long long>(h & 0xFFFFFFFFull));
    return b;
}

}  // namespace fnvcoop::fuzz
