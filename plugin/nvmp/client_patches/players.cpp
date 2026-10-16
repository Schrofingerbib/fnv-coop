#include "players.h"

#include <cstdio>
#include <cstring>

#include "../sigscan.h"

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>

namespace nvmp::client {

namespace {
// SEH-guarded raw reads (own functions: no C++ objects with destructors in a __try body).
bool RdU32(uintptr_t a, uint32_t* out) {
    __try { *out = *(volatile const uint32_t*)a; return true; } __except (EXCEPTION_EXECUTE_HANDLER) { return false; }
}
bool RdU8(uintptr_t a, uint8_t* out) {
    __try { *out = *(volatile const uint8_t*)a; return true; } __except (EXCEPTION_EXECUTE_HANDLER) { return false; }
}
bool RdPtr(uintptr_t a, uintptr_t* out) {
    __try { *out = *(volatile const uintptr_t*)a; return true; } __except (EXCEPTION_EXECUTE_HANDLER) { return false; }
}
bool RdVt(uintptr_t obj, uint32_t idx, uintptr_t* fn) {
    uintptr_t vt = 0;
    if (!RdPtr(obj, &vt) || !vt) return false;
    return RdPtr(vt + idx * sizeof(uintptr_t), fn) && *fn;
}

#if defined(_M_IX86)
typedef const uint32_t*(__thiscall* KeyFn)(void*);
typedef const char*(__thiscall* NameFn)(void*);
bool CallKey(uintptr_t fn, uintptr_t obj, uint32_t* id) {
    __try {
        const uint32_t* k = ((KeyFn)fn)((void*)obj);
        if (!k) return false;
        *id = *k;
        return true;
    } __except (EXCEPTION_EXECUTE_HANDLER) { return false; }
}
bool CallName(uintptr_t fn, uintptr_t obj, char* buf, size_t cap) {
    __try {
        const char* s = ((NameFn)fn)((void*)obj);
        if (!s) return false;
        size_t i = 0;
        for (; i + 1 < cap && s[i]; ++i) buf[i] = (s[i] >= 0x20 && s[i] < 0x7F) ? s[i] : '?';
        buf[i] = 0;
        return true;
    } __except (EXCEPTION_EXECUTE_HANDLER) { return false; }
}
bool CallGameRef(const PlayersNatives& n, const PlayersLayout& L, uintptr_t chr, uintptr_t* ref) {
    __try {
        if (!n.getComponent || !n.getGameRef) return false;
        uint32_t key = L.componentKey;
        void* comp = n.getComponent((void*)chr, &key);
        if (!comp) return false;
        uintptr_t owner = *(volatile uintptr_t*)((uintptr_t)comp + L.componentOwner);
        if (!owner) return false;
        uint8_t loc = *(volatile uint8_t*)(owner + L.localityByte);
#ifdef PLAYERS_MUTANT_SWAP_SLOT
        void* slot = (void*)((uintptr_t)comp + (loc ? L.slotOwner : L.slotGhost));   // mutation twin: wrong slot per locality
#else
        void* slot = (void*)((uintptr_t)comp + (loc ? L.slotGhost : L.slotOwner));
#endif
        *ref = (uintptr_t)n.getGameRef(slot);
        return true;
    } __except (EXCEPTION_EXECUTE_HANDLER) { return false; }
}
#endif
}  // namespace

std::vector<PlayerInfo> EnumeratePlayers(const PlayersNatives& n, const PlayersLayout& L, bool* faulted) {
    std::vector<PlayerInfo> out;
    if (faulted) *faulted = false;
#if defined(_M_IX86)
    uintptr_t mgr = 0, sentinel = 0, node = 0, thePlayer = 0;
    if (!n.managerGlobal || !RdPtr(n.managerGlobal, &mgr) || !mgr) return out;     // not connected / no manager yet
    if (n.thePlayerGlobal) RdPtr(n.thePlayerGlobal, &thePlayer);
    if (!RdPtr(mgr + L.listOffset, &sentinel) || !sentinel || !RdPtr(sentinel, &node)) { if (faulted) *faulted = true; return out; }
    for (size_t guard = 0; node && node != sentinel && out.size() < L.maxPlayers && guard < 1024; ++guard) {
        uintptr_t chr = 0;
        if (!RdPtr(node + L.nodeObject, &chr)) { if (faulted) *faulted = true; break; }
        if (chr) {
            PlayerInfo p;
            p.netObject = chr;
            uintptr_t fn = 0;
            if (RdVt(chr, L.vtKey, &fn)) CallKey(fn, chr, &p.netId);
            char nm[96] = {};
            if (RdVt(chr, L.vtName, &fn) && CallName(fn, chr, nm, sizeof nm)) p.name = nm;
            uint8_t loc = 0xFF;
            uint32_t flags = 0;
            bool ownerCopy = RdU8(chr + L.localityByte, &loc) && RdU32(chr + L.flagsDword, &flags) && loc == 0 && (flags >> 31) != 0;
            uintptr_t ref = 0;
            if (CallGameRef(n, L, chr, &ref) && ref) {
                uint32_t id = 0;
                if (RdU32(ref + L.formRefId, &id)) { p.gameRef = ref; p.refId = id; p.hasRef = true; }
            }
            p.isLocal = ownerCopy || (p.gameRef && p.gameRef == thePlayer);
            out.push_back(std::move(p));
        }
        if (!RdPtr(node, &node)) { if (faulted) *faulted = true; break; }
    }
#else
    (void)n; (void)L;
#endif
    return out;
}

uint32_t SelectGhostRef(const std::vector<PlayerInfo>& ps, uint8_t slot, const std::vector<SlotBindings>& bindings) {
    if (slot == 0) return 0;   // the host is the local player: no ghost
    auto isBound = [&](uint32_t id) {
        for (const auto& b : bindings) if (b.netId == id) return true;
        return false;
    };
    for (const auto& b : bindings) {
        if (b.slot != slot) continue;
        for (const auto& p : ps)
            if (!p.isLocal && p.netId == b.netId) return p.hasRef ? p.refId : 0;
        return 0;   // bound player not connected (yet)
    }
    // Unbound slots take the unbound remote characters in join order: rank = 1 + the unbound slots below this one.
    uint32_t rank = 1;
    for (uint32_t s = 1; s < slot; ++s) {
        bool bound = false;
        for (const auto& b : bindings) if (b.slot == s) bound = true;
        if (!bound) ++rank;
    }
    uint32_t k = 0;
    for (const auto& p : ps) {
#ifdef PLAYERS_MUTANT_SWAP_SLOT
        if (isBound(p.netId)) continue;   // mutation twin: the local player counts as a guest
#else
        if (p.isLocal || isBound(p.netId)) continue;
#endif
        if (++k == rank) return p.hasRef ? p.refId : 0;
    }
    return 0;
}

std::string PlayersJson(const std::vector<PlayerInfo>& ps) {
    std::string j = "[";
    for (size_t i = 0; i < ps.size(); ++i) {
        const PlayerInfo& p = ps[i];
        if (i) j += ",";
        std::string nm;
        for (char c : p.name) { if (c == '"' || c == '\\') nm += '\\'; nm += c; }
        char buf[64];
        j += "{\"netId\":" + std::to_string(p.netId) + ",\"name\":\"" + nm + "\",\"refId\":";
        if (p.hasRef) { std::snprintf(buf, sizeof buf, "\"0x%08X\"", p.refId); j += buf; } else j += "null";
        j += std::string(",\"isLocal\":") + (p.isLocal ? "true" : "false") + "}";
    }
    return j + "]";
}

// exact-position pattern match (wildcards allowed)
static bool MatchAt(const uint8_t* p, const char* pat) {
    Pattern pt;
    if (!ParsePattern(pat, pt)) return false;
    for (size_t i = 0; i < pt.size(); ++i)
        if ((p[i] & pt.mask[i]) != pt.bytes[i]) return false;
    return true;
}

ResolvedPlayers ResolvePlayers(const uint8_t* image, size_t size) {
    ResolvedPlayers r;
    // 1. the use-site that proves the layout constants and contains the call to GetComponent
    static const char kUse[] =
        "8D 44 24 18 C7 44 24 18 11 3A 81 D9 50 8B CF E8 ?? ?? ?? ?? 85 C0 0F 84 ?? ?? ?? ?? 8B 90 DC 03 00 00 BF 88 03 00 00 8B CF C7 44 "
        "24 10 60 03 00 00 80 BA AE 01 00 00 00 0F 44 4C 24 10 03 C8 E8 ?? ?? ?? ?? 89";
    ScanResult u = ScanImage(image, size, kUse);
    if (!u.ok()) { r.error = std::string("layout use-site: ") + ToString(u.status); return r; }
    r.layoutCheckRva = (uint32_t)u.offset;
    int32_t relC = 0, relG = 0;
    std::memcpy(&relC, image + u.offset + 16, 4);        // call GetComponent
    std::memcpy(&relG, image + u.offset + 64, 4);        // call GetGameRef (E8 at +63)
    int64_t comp = (int64_t)u.offset + 20 + relC, gref = (int64_t)u.offset + 68 + relG;
    if (comp <= 0 || gref <= 0 || (size_t)comp + 40 > size || (size_t)gref + 40 > size) { r.error = "call targets out of range"; return r; }
    r.getComponentRva = (uint32_t)comp;
    r.getGameRefRva = (uint32_t)gref;
    // 2. both targets must look like what we expect (GetComponent: FNV-1a hash loop over a map at +0x144; GetGameRef: slot resolver)
    if (!MatchAt(image + comp, "55 8B EC 83 EC 10 53 8B C1 89 45 F8 56 8B 75 08 57 8D B8 44 01 00 00 8B 1E 0F B6 C3 35 C5 9D 1C 81")) {
        r.error = "GetComponent prologue differs";
        return r;
    }
    if (!MatchAt(image + gref, "56 8B F1 57 8B 46 08 85 C0 74 40 6A 00 68 ?? ?? ?? ?? 68 ?? ?? ?? ?? 6A 00 50 E8 ?? ?? ?? ?? 83 C4 04 BF FB 43 EC 00")) {
        r.error = "GetGameRef prologue differs";
        return r;
    }
    // 3. character manager global: first instruction of the list walker (`mov eax,[global]` at +7)
    ScanResult m = ScanImage(image, size, "55 8B EC 83 EC 0C A1 ?? ?? ?? ?? 89 45 F4 56 57 8B 40 54 8B F9 88 55 FF 8B 30 3B F0 74");
    if (!m.ok()) { r.error = std::string("character manager walker: ") + ToString(m.status); return r; }
    std::memcpy(&r.managerGlobal, image + m.offset + 7, 4);
    return r;
}

}  // namespace nvmp::client
