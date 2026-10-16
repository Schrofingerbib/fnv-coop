// Maps NV:MP remote players to the local ghost actor references that represent them (NV:MP 9.4 client.dll).
//
// Static RE result (see internal lane notes, round 6):
//   character manager   global pointer at client.dll RVA 0x2E6C08 (found through a signature, never hard-coded)
//                       mgr+0x54 = list sentinel, node+0xC = GameNetCharacter*  (same layout as the server)
//   per character       vtable[1]()      -> pointer to the identity struct, first dword = net id
//                       vtable[0x24/4]() -> const char* display name (the %s NV:MP prints in its logs)
//                       +0x1AE (u8) / +8 (bit31): owner predicate, local owner <=> byte==0 && bit31 set
//                       GetComponent(chr, &0xD9813A11) -> comp; owner = *(comp+0x3DC);
//                       slot = comp + (owner.byte[0x1AE] ? 0x388 : 0x360); GetGameRef(slot) -> TESObjectREFR*; refId = *(ref+0xC)
// Natives (GetComponent, GetGameRef) are called as NV:MP itself does; they are located by signature. Everything
// that touches memory goes through function pointers / raw reads supplied by the caller, so the enumeration logic is
// unit-tested on a fake object graph and the signatures on the mapped 9.4 image.
#pragma once
#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

namespace nvmp::client {

struct PlayerInfo {
    uint32_t netId = 0;
    std::string name;
    uint32_t refId = 0;        // 0 when the game reference is not resolved (not in the host's loaded area)
    bool hasRef = false;
    bool isLocal = false;
    uintptr_t netObject = 0;   // GameNetCharacter*
    uintptr_t gameRef = 0;     // TESObjectREFR* (0 if unresolved)
};

// Offsets inside NV:MP's objects (9.4). Overridable so the fake-graph tests can use the same code path.
struct PlayersLayout {
    uint32_t listOffset = 0x54;        // manager -> list sentinel
    uint32_t nodeObject = 0x0C;        // list node -> GameNetCharacter*
    uint32_t vtKey = 1;                // vtable index returning the identity struct
    uint32_t vtName = 0x24 / 4;        // vtable index returning the name
    uint32_t localityByte = 0x1AE;
    uint32_t flagsDword = 0x08;        // bit31 set on owner copies
    uint32_t componentKey = 0xD9813A11;
    uint32_t componentOwner = 0x3DC;
    uint32_t slotGhost = 0x388;        // slot offset when owner.byte[localityByte] != 0
    uint32_t slotOwner = 0x360;        // slot offset when it is 0
    uint32_t formRefId = 0x0C;         // TESForm::refID
    size_t maxPlayers = 64;
};

// x86 thiscall natives of client.dll, as function pointers (null => not resolved).
struct PlayersNatives {
#if defined(_M_IX86)
    void* (__thiscall* getComponent)(void* obj, const uint32_t* key) = nullptr;
    void* (__thiscall* getGameRef)(void* slot) = nullptr;
#else
    void* getComponent = nullptr;   // x64 builds are for host tests of the resolver only
    void* getGameRef = nullptr;
#endif
    uintptr_t managerGlobal = 0;    // address of the global holding the character manager pointer
    uintptr_t thePlayerGlobal = 0;  // FalloutNV.exe g_thePlayer (0x011DEA3C on 1.4.0.525)
};

constexpr uintptr_t kThePlayerGlobal_1_4_0_525 = 0x011DEA3C;

// Walks the manager list. Every read is SEH-guarded; a fault stops the walk and sets *faulted.
std::vector<PlayerInfo> EnumeratePlayers(const PlayersNatives& n, const PlayersLayout& L, bool* faulted = nullptr);

// Which ghost reference stands for coop slot `slot` (0 = host/local, 1.. = guests).
// Order of precedence: an explicit binding slot -> netId; otherwise join order = list order of the NON-local characters
// that are not bound to another slot (the character list is insertion-ordered). Returns 0 when unknown / not spawned.
struct SlotBindings { uint8_t slot; uint32_t netId; };
uint32_t SelectGhostRef(const std::vector<PlayerInfo>& ps, uint8_t slot, const std::vector<SlotBindings>& bindings);

// JSON array text: [{"netId":..,"name":"..","refId":"0x..." | null,"isLocal":bool}, ...]
std::string PlayersJson(const std::vector<PlayerInfo>& ps);

// ---- resolution on a mapped image -------------------------------------------------------------
struct ResolvedPlayers {
    uint32_t getComponentRva = 0;
    uint32_t getGameRefRva = 0;
    uint32_t managerGlobal = 0;   // absolute address as stored in code (already relocated in-process)
    uint32_t layoutCheckRva = 0;  // use-site that proves the 0x3DC/0x1AE/0x360/0x388/key constants
    std::string error;            // empty on success
    bool ok() const { return error.empty(); }
};
ResolvedPlayers ResolvePlayers(const uint8_t* image, size_t imageSize);

}  // namespace nvmp::client
