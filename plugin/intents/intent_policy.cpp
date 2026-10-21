#include "intents/intent_policy.h"

#include <algorithm>
#include <cctype>
#include <cmath>
#include <cstdlib>
#include <map>
#include <sstream>

#include "fnvcoop_mut.h"

namespace nvcoop::intents {

const char* className(TargetClass c) {
    static const char* n[] = {"Unknown", "Door", "Activator", "Container", "Terminal", "Npc", "Creature", "Item", "Trigger", "Furniture", "Static"};
    static_assert(sizeof n / sizeof *n == static_cast<size_t>(TargetClass::Count_), "className: a TargetClass was added");
    return static_cast<size_t>(c) < sizeof n / sizeof *n ? n[static_cast<size_t>(c)] : "?";
}

bool opensPlayerMenu(TargetClass c) {
    switch (c) {
        case TargetClass::Container:
        case TargetClass::Terminal:
        case TargetClass::Npc:
        case TargetClass::Creature:
        case TargetClass::Item:
        case TargetClass::Unknown:   // we do not know what it would do: assume the worst
            return true;
        default:
            return false;
    }
}

float distance(const Vec3& a, const Vec3& b) {
    float dx = a.x - b.x, dy = a.y - b.y, dz = a.z - b.z;
    return std::sqrt(dx * dx + dy * dy + dz * dz);
}

// ---- guest ---------------------------------------------------------------------------------------------------------------------------

GuestDecision decideActivate(const Config& cfg, const TargetInfo& t) {
    GuestDecision d;
    if (!cfg.enable) { d.why = "intents off"; return d; }
    if (!t.loaded) { d.why = "not loaded"; return d; }
    TargetClass cls = t.cls;
    if ((cls == TargetClass::Npc || cls == TargetClass::Creature) && t.dead) cls = TargetClass::Container;   // a body is looted like a chest
    switch (cls) {
        case TargetClass::Item:
            if (!cfg.pickup) { d.why = "pickup off"; return d; }
            d.forward = true; d.kind = Kind::PickUp;
            d.suppressLocal = !FNV_MUT(302);   // MUTANT 302: the local pickup goes on too -> the item exists twice (guest keeps it, host grants it)
            d.why = "claim";
            return d;
        case TargetClass::Container:
            if (!cfg.container) { d.why = "container off"; return d; }
            d.forward = true; d.kind = Kind::ContainerOpen; d.suppressLocal = false; d.why = "container lock";
            return d;
        case TargetClass::Npc:
            if (!cfg.dialogue) { d.why = "dialogue off"; return d; }
            d.kind = Kind::DialogueStart; d.viaDialogue = true; d.why = "talk via 0xD1";
            return d;
        case TargetClass::Creature:
        case TargetClass::Trigger:
        case TargetClass::Static:
            d.why = "nothing to do";
            return d;
        case TargetClass::Terminal:
        case TargetClass::Door:
        case TargetClass::Activator:
        case TargetClass::Furniture:
        case TargetClass::Unknown:
        default:
            if (!cfg.activate) { d.why = "activate off"; return d; }
            d.forward = true; d.kind = Kind::Activate; d.suppressLocal = false; d.why = "script half on the host";
            return d;
    }
}

GuestDecision decideEvent(const Config& cfg, uint32_t mask, const TargetInfo& t) {
    GuestDecision d;
    if (!cfg.enable) { d.why = "intents off"; return d; }
    if (!t.loaded) { d.why = "not loaded"; return d; }
    if (mask & ev::OnTriggerEnter) {
        if (!cfg.trigger) { d.why = "trigger off"; return d; }
        d.forward = true; d.kind = Kind::TriggerEnter; d.suppressLocal = true; d.why = "trigger enter";
        return d;
    }
    if (mask & ev::OnTriggerLeave) {
        if (!cfg.trigger) { d.why = "trigger off"; return d; }
        d.forward = true; d.kind = Kind::TriggerLeave; d.suppressLocal = true; d.why = "trigger leave";
        return d;
    }
    if (mask & ev::OnHit) {
        if (!cfg.hit) { d.why = "hit off"; return d; }
        const bool actor = t.cls == TargetClass::Npc || t.cls == TargetClass::Creature;
        if (actor && !FNV_MUT(305)) { d.why = "actors: the combat channel"; return d; }   // MUTANT 305: actor hits are forwarded as well (double OnHit)
        d.forward = true; d.kind = Kind::Hit; d.suppressLocal = false; d.why = "hit";
        return d;
    }
    d.why = "event not forwarded";   // OnTrigger (every frame inside a volume), OnAdd / OnOpen / OnClose (covered by the container flow) ...
    return d;
}

// ---- host ----------------------------------------------------------------------------------------------------------------------------

float reachOf(const Config& cfg, Kind k) {
    switch (k) {
        case Kind::Activate: return cfg.reachActivate;
        case Kind::PickUp: return cfg.reachPickup;
        case Kind::TriggerEnter:
        case Kind::TriggerLeave: return cfg.reachTrigger;
        case Kind::ContainerOpen:
        case Kind::ContainerClose: return cfg.reachContainer;
        case Kind::TerminalUse: return cfg.reachTerminal;
        case Kind::Hit: return cfg.reachHit;
        default: return 0.f;
    }
}

bool kindEnabled(const Config& cfg, Kind k) {
    switch (k) {
        case Kind::Activate: return cfg.activate;
        case Kind::TriggerEnter:
        case Kind::TriggerLeave: return cfg.trigger;
        case Kind::PickUp: return cfg.pickup;
        case Kind::Hit: return cfg.hit;
        case Kind::DialogueStart: return cfg.dialogue;
        case Kind::TerminalUse: return cfg.terminal;
        case Kind::ContainerOpen:
        case Kind::ContainerClose: return cfg.container;
        default: return false;
    }
}

bool standInAllowed(const Config& cfg, Kind k, TargetClass cls) {
    if (cfg.requirePuppet || !cfg.standIn) return false;
    switch (k) {
        case Kind::PickUp:           // direct grant: no inventory of any actor is touched
        case Kind::TriggerEnter:
        case Kind::TriggerLeave:
        case Kind::Hit:
        case Kind::TerminalUse:      // runs the entry's result script; opens nothing
        case Kind::ContainerClose:
        case Kind::DialogueStart:
            return true;
        case Kind::Activate:
        case Kind::ContainerOpen:    // the script half is the only thing that needs an actor, and Activate would open the host's menu
            if (FNV_MUT(304)) return true;   // MUTANT 304: the host's player activates containers / terminals / NPCs for a guest -> host UI pops up
            return cfg.standInUnsafe || !opensPlayerMenu(cls);
        default:
            return false;
    }
}

// ---- ini -----------------------------------------------------------------------------------------------------------------------------

namespace {

std::string lower(std::string s) {
    for (char& c : s) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    return s;
}
std::string trim(const std::string& s) {
    size_t a = 0, b = s.size();
    while (a < b && std::isspace(static_cast<unsigned char>(s[a]))) ++a;
    while (b > a && std::isspace(static_cast<unsigned char>(s[b - 1]))) --b;
    return s.substr(a, b - a);
}
bool parseBool(const std::string& v0, bool* out) {
    std::string v = lower(trim(v0));
    if (v == "1" || v == "true" || v == "on" || v == "yes") { *out = true; return true; }
    if (v == "0" || v == "false" || v == "off" || v == "no") { *out = false; return true; }
    return false;
}
bool parseNum(const std::string& v0, double* out) {
    std::string v = trim(v0);
    if (v.empty()) return false;
    char* end = nullptr;
    double d = std::strtod(v.c_str(), &end);
    if (!end || *end != 0 || !std::isfinite(d)) return false;
    *out = d;
    return true;
}

}  // namespace

bool readConfig(const IniGetter& get, Config* out, std::string* err) {
    Config c = *out;
    auto bad = [&](const char* key, const char* what) {
        if (err) *err = std::string("[intents] ") + key + ": " + what;
        return false;
    };
    auto B = [&](const char* key, bool* dst) {
        std::string v = get(key, "");
        if (v.empty()) return true;
        return parseBool(v, dst) ? true : bad(key, "must be 0/1");
    };
    auto F = [&](const char* key, float* dst, double lo, double hi) {
        std::string v = get(key, "");
        if (v.empty()) return true;
        double d = 0;
        if (!parseNum(v, &d)) return bad(key, "must be a number");
        *dst = static_cast<float>(std::min(hi, std::max(lo, d)));
        return true;
    };
    auto U = [&](const char* key, uint32_t* dst, double lo, double hi) {
        std::string v = get(key, "");
        if (v.empty()) return true;
        double d = 0;
        if (!parseNum(v, &d)) return bad(key, "must be a number");
        *dst = static_cast<uint32_t>(std::min(hi, std::max(lo, d)));
        return true;
    };
    bool ok = B("enable", &c.enable) && B("activate", &c.activate) && B("trigger", &c.trigger) && B("pickup", &c.pickup) &&
              B("terminal", &c.terminal) && B("container", &c.container) && B("hit", &c.hit) && B("dialogue", &c.dialogue) && B("dialogue_results", &c.dialogueResults) &&
              B("standin", &c.standIn) && B("standin_unsafe", &c.standInUnsafe) && B("require_puppet", &c.requirePuppet) &&
              B("check_reach", &c.checkReach) && B("save_gate", &c.saveGate) && F("reach_activate", &c.reachActivate, 1, 100000) && F("reach_pickup", &c.reachPickup, 1, 100000) &&
              F("reach_trigger", &c.reachTrigger, 1, 100000) && F("reach_container", &c.reachContainer, 1, 100000) &&
              F("reach_terminal", &c.reachTerminal, 1, 100000) && F("reach_hit", &c.reachHit, 1, 100000) && U("rate_burst", &c.rateBurst, 1, 1000) &&
              F("rate_per_sec", &c.ratePerSec, 0.1, 1000) && U("lock_lease_ms", &c.lockLeaseMs, 1000, 3600000) &&
              U("trigger_cooldown_ms", &c.triggerCooldownMs, 0, 600000) && U("pending_timeout_ms", &c.pendingTimeoutMs, 500, 600000) &&
              U("coalesce_ms", &c.coalesceMs, 0, 10000) && U("claim_memory", &c.claimMemory, 16, 1000000);
    if (!ok) return false;
    std::string rf = get("redirect_file", "");
    if (!rf.empty()) c.redirectFile = trim(rf);
    *out = c;
    return true;
}

bool parseConfig(const std::string& iniText, Config* out, std::string* err) {
    std::map<std::string, std::string> kv;
    std::istringstream in(iniText);
    std::string line, section;
    while (std::getline(in, line)) {
        line = trim(line);
        if (line.empty() || line[0] == ';' || line[0] == '#') continue;
        if (line[0] == '[') {
            size_t e = line.find(']');
            section = lower(e == std::string::npos ? line.substr(1) : line.substr(1, e - 1));
            continue;
        }
        if (section != "intents") continue;
        size_t eq = line.find('=');
        if (eq == std::string::npos) continue;
        std::string v = trim(line.substr(eq + 1));
        size_t semi = v.find(';');   // trailing comment
        if (semi != std::string::npos) v = trim(v.substr(0, semi));
        kv[lower(trim(line.substr(0, eq)))] = v;
    }
    return readConfig([&](const char* key, const char* def) {
        auto it = kv.find(lower(key));
        return it == kv.end() ? std::string(def) : it->second;
    }, out, err);
}

}  // namespace nvcoop::intents
