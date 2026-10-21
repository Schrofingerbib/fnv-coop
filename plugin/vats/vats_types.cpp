#include "vats/vats_types.h"

#include <algorithm>
#include <cctype>
#include <cmath>

#include "../../tools/mut/fnvcoop_mut.h"

namespace nvcoop::vats {

const char* toString(Mode m) {
    switch (m) {
        case Mode::None: return "none";
        case Mode::TargetSelect: return "target_select";
        case Mode::LimbSelect: return "limb_select";
        case Mode::Mode3: return "mode3";
        case Mode::Playback: return "playback";
    }
    return "?";
}

bool validLimb(int v) { return v >= 25 && v <= 31; }
LimbId limbFromAv(int v) { return validLimb(v) ? static_cast<LimbId>(v) : LimbId::None; }

const char* toString(LimbId l) {
    switch (l) {
        case LimbId::Head: return "head";
        case LimbId::Torso: return "torso";
        case LimbId::LeftArm: return "left_arm";
        case LimbId::RightArm: return "right_arm";
        case LimbId::LeftLeg: return "left_leg";
        case LimbId::RightLeg: return "right_leg";
        case LimbId::Brain: return "brain";
        case LimbId::None: return "none";
    }
    return "?";
}

LimbId limbFromName(const std::string& in) {
    std::string s;
    for (char c : in) {
        if (c == ' ' || c == '_' || c == '-') continue;
        s.push_back(static_cast<char>(std::tolower(static_cast<unsigned char>(c))));
    }
    if (s.empty()) return LimbId::None;
    if (s == "head") return LimbId::Head;
    if (s == "torso" || s == "body" || s == "chest") return LimbId::Torso;
    if (s == "leftarm" || s == "larm") return LimbId::LeftArm;
    if (s == "rightarm" || s == "rarm") return LimbId::RightArm;
    if (s == "leftleg" || s == "lleg") return LimbId::LeftLeg;
    if (s == "rightleg" || s == "rleg") return LimbId::RightLeg;
    if (s == "brain") return LimbId::Brain;
    if (std::isdigit(static_cast<unsigned char>(s[0])) && s.size() <= 2) return limbFromAv(std::atoi(s.c_str()));
    return LimbId::None;
}

uint8_t combatLimbOf(LimbId l) {
    switch (l) {
        case LimbId::Torso: return 0;
        case LimbId::Head: return 1;
        case LimbId::LeftArm: return 2;
        case LimbId::RightArm: return 3;
        case LimbId::LeftLeg: return 4;
        case LimbId::RightLeg: return 5;
        default: return 255;
    }
}

const char* toString(WeaponClass c) {
    switch (c) {
        case WeaponClass::Gun: return "gun";
        case WeaponClass::Melee: return "melee";
        case WeaponClass::Explosive: return "explosive";
        case WeaponClass::Energy: return "energy";
        default: return "?";
    }
}

WeaponClass weaponClassFromName(const std::string& in, bool* ok) {
    std::string s;
    for (char c : in) s.push_back(static_cast<char>(std::tolower(static_cast<unsigned char>(c))));
    if (ok) *ok = true;
    if (s == "gun" || s == "ballistic") return WeaponClass::Gun;
    if (s == "melee" || s == "unarmed") return WeaponClass::Melee;
    if (s == "explosive" || s == "grenade") return WeaponClass::Explosive;
    if (s == "energy" || s == "laser" || s == "plasma") return WeaponClass::Energy;
    if (ok) *ok = false;
    return WeaponClass::Gun;
}

Settings sanitized(Settings s) {
    auto clampF = [](float v, float lo, float hi) { return std::isfinite(v) ? std::min(hi, std::max(lo, v)) : lo; };
    auto clampU = [](uint32_t v, uint32_t lo, uint32_t hi) { return std::min(hi, std::max(lo, v)); };
    const float rawScale = s.slowScale;
    s.slowScale = clampF(s.slowScale, 0.10f, 0.90f);
    // 802: a hostile ini / peer could ask for a scale that freezes the world: the lower bound must hold
    if (FNV_MUT(802)) s.slowScale = std::isfinite(rawScale) ? rawScale : 0.35f;
    // 801: ... or for a slow-mo window of any length: the cap must hold
    if (!FNV_MUT(801)) s.slowMaxMs = clampU(s.slowMaxMs, 500, 10000);
    s.slowCooldownMs = clampU(s.slowCooldownMs, 0, 120000);
    s.autoFireDelayMs = clampU(s.autoFireDelayMs, 0, 3000);
    return s;
}

}  // namespace nvcoop::vats
