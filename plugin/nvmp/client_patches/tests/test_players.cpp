// Tests for nvmp.players: signature resolution on the REAL 9.4 client.dll image (mapped, never executed) and the
// enumeration logic on a fake NV:MP object graph with real x86 thiscall function pointers.
// Mutation twin: -DPLAYERS_MUTANT_SWAP_SLOT (reads the wrong game-ref slot for the locality) must fail.
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iterator>
#include <string>
#include <vector>

#include "../../sigscan.h"
#include "../players.h"

using namespace nvmp;
using namespace nvmp::client;

static int g_fail = 0, g_run = 0;
#define CHECK(c)                                                                      \
    do {                                                                              \
        ++g_run;                                                                      \
        if (!(c)) { ++g_fail; std::printf("  FAIL line %d: %s\n", __LINE__, #c); }    \
    } while (0)

static std::vector<uint8_t> g_image;
static bool g_have = false;

static void LoadReal() {
    const char* env = std::getenv("NVMP_CLIENT_DLL");
    std::string path = env ? env : "third_party/nvmp/nvmp/client.dll";
    std::ifstream f(path, std::ios::binary);
    if (!f) return;
    std::vector<uint8_t> file((std::istreambuf_iterator<char>(f)), std::istreambuf_iterator<char>());
    g_have = MapPeFileToImage(file, g_image);
}

static void TestResolve() {
    if (!g_have) { std::printf("  (real client.dll not found, skipped)\n"); return; }
    ResolvedPlayers r = ResolvePlayers(g_image.data(), g_image.size());
    CHECK(r.ok());
    CHECK(r.layoutCheckRva == 0x3DD09 && r.getComponentRva == 0x3E9C0 && r.getGameRefRva == 0x44890);
    CHECK(r.managerGlobal == 0x102E6C08);
    // an update that changes a layout constant (the 0x3DC component-owner offset) must be refused, not guessed
    std::vector<uint8_t> img = g_image;
    ScanResult u = ScanImage(img.data(), img.size(), "8B 90 DC 03 00 00 BF 88 03 00 00 8B CF C7 44 24 10 60 03 00 00 80 BA AE 01 00 00 00");
    CHECK(u.ok());
    img[u.offset + 2] = 0xE0;
    ResolvedPlayers bad = ResolvePlayers(img.data(), img.size());
    CHECK(!bad.ok() && bad.error.find("layout") != std::string::npos);
    // GetComponent's body changed
    img = g_image;
    img[0x3E9C0 + 3] ^= 0xFF;
    CHECK(!ResolvePlayers(img.data(), img.size()).ok());
    // GetGameRef's body changed
    img = g_image;
    img[0x44890 + 1] ^= 0xFF;
    CHECK(!ResolvePlayers(img.data(), img.size()).ok());
}

#if defined(_M_IX86)
namespace fake {
constexpr size_t kChar = 0x400;
static const uint32_t* __fastcall Key(void* self, void*) { return (const uint32_t*)((char*)self + 0x300); }
static const char* __fastcall Name(void* self, void*) { return (const char*)((char*)self + 0x310); }
static void* __fastcall Component(void* self, void*, const uint32_t* key) { return *key == 0xD9813A11 ? *(void**)((char*)self + 0x320) : nullptr; }
static void* __fastcall GameRef(void* slot, void*) { return *(void**)slot; }   // the fake slot's first dword is the reference pointer

struct World {
    std::vector<char*> mem;
    uint32_t vtbl[0x30] = {};
    uintptr_t mgrVar = 0, playerVar = 0;
    char* Alloc(size_t n) { char* p = (char*)std::calloc(1, n); mem.push_back(p); return p; }
    ~World() { for (auto p : mem) std::free(p); }
    World() { vtbl[1] = (uint32_t)(uintptr_t)&Key; vtbl[0x24 / 4] = (uint32_t)(uintptr_t)&Name; }
    char* Ref(uint32_t refId) { char* r = Alloc(0x40); *(uint32_t*)(r + 0xC) = refId; return r; }
    // owner=true: local owner copy; ref may be null
    char* Char(uint32_t id, const char* name, bool owner, char* ref) {
        char* c = Alloc(kChar);
        *(uint32_t*)c = (uint32_t)(uintptr_t)vtbl;
        *(uint32_t*)(c + 8) = owner ? 0x80000000u : 0;
        c[0x1AE] = owner ? 0 : 1;
        *(uint32_t*)(c + 0x300) = id;
        std::strcpy(c + 0x310, name);
        char* comp = Alloc(0x500);
        *(char**)(c + 0x320) = comp;
        *(char**)(comp + 0x3DC) = c;                                       // component -> owner object
        *(char**)(comp + (owner ? 0x360 : 0x388)) = ref;                   // the slot NV:MP reads for this locality
        *(char**)(comp + (owner ? 0x388 : 0x360)) = (char*)0xBAD00000;     // the other slot must never be read
        return c;
    }
    // manager -> [0x54] sentinel; circular list of nodes {next, prev, key, obj}
    void Build(const std::vector<char*>& chars, char* thePlayer) {
        char* mgr = Alloc(0x100);
        char* sentinel = Alloc(0x14);
        *(char**)(mgr + 0x54) = sentinel;
        char* prev = sentinel;
        for (char* c : chars) {
            char* n = Alloc(0x14);
            *(char**)prev = n;
            *(char**)(n + 4) = prev;
            *(char**)(n + 0xC) = c;
            prev = n;
        }
        *(char**)prev = sentinel;
        mgrVar = (uintptr_t)mgr;
        playerVar = (uintptr_t)thePlayer;
    }
};
}  // namespace fake

static PlayersNatives Natives(fake::World& w) {
    PlayersNatives n;
    n.getComponent = reinterpret_cast<decltype(n.getComponent)>(&fake::Component);   // fastcall(self, unused, key) is stack-compatible with thiscall(self, key)
    n.getGameRef = reinterpret_cast<decltype(n.getGameRef)>(&fake::GameRef);
    n.managerGlobal = (uintptr_t)&w.mgrVar;
    n.thePlayerGlobal = (uintptr_t)&w.playerVar;
    return n;
}

static void TestEnumerate() {
    fake::World w;
    char* me = w.Ref(0x00014);       // the host's own player ref (g_thePlayer)
    char* p1 = w.Ref(0x01000ABC);    // ghost actor of guest 1
    w.Build({w.Char(7, "HostPlayer", true, me), w.Char(11, "GuestOne", false, p1), w.Char(12, "Guest\"Two", false, nullptr)}, me);
    bool faulted = true;
    auto ps = EnumeratePlayers(Natives(w), PlayersLayout{}, &faulted);
    CHECK(!faulted && ps.size() == 3);
    if (ps.size() == 3) {
        CHECK(ps[0].netId == 7 && ps[0].name == "HostPlayer" && ps[0].isLocal && ps[0].hasRef && ps[0].refId == 0x14);
        CHECK(ps[1].netId == 11 && ps[1].name == "GuestOne" && !ps[1].isLocal && ps[1].hasRef && ps[1].refId == 0x01000ABC);   // mutation twin: wrong slot
        CHECK(ps[2].netId == 12 && !ps[2].isLocal && !ps[2].hasRef && ps[2].refId == 0);
    }
    std::string j = PlayersJson(ps);
    CHECK(j.find("{\"netId\":11,\"name\":\"GuestOne\",\"refId\":\"0x01000ABC\",\"isLocal\":false}") != std::string::npos);
    CHECK(j.find("\"name\":\"Guest\\\"Two\",\"refId\":null") != std::string::npos);
    CHECK(PlayersJson({}) == "[]");

    // the local player is also recognised through g_thePlayer when the owner predicate says "ghost"
    fake::World w2;
    char* me2 = w2.Ref(0x14);
    w2.Build({w2.Char(1, "Me", false, me2)}, me2);
    auto ps2 = EnumeratePlayers(Natives(w2), PlayersLayout{}, nullptr);
    CHECK(ps2.size() == 1 && ps2[0].isLocal);

    // no manager yet (not connected) => empty and no fault
    uintptr_t nullMgr = 0;
    PlayersNatives n3 = Natives(w);
    n3.managerGlobal = (uintptr_t)&nullMgr;
    faulted = true;
    CHECK(EnumeratePlayers(n3, PlayersLayout{}, &faulted).empty() && !faulted);

    // a broken list link => partial result + fault flag, no crash
    fake::World w3;
    w3.Build({w3.Char(5, "Ok", false, nullptr)}, nullptr);
    char* firstNode = *(char**)(*(char**)(w3.mgrVar + 0x54));
    *(char**)firstNode = (char*)0x00000010;
    faulted = false;
    auto ps4 = EnumeratePlayers(Natives(w3), PlayersLayout{}, &faulted);
    CHECK(ps4.size() == 1 && ps4[0].netId == 5 && faulted);

    // more characters than the cap
    fake::World w5;
    std::vector<char*> many;
    for (uint32_t i = 0; i < 100; ++i) many.push_back(w5.Char(i, "x", false, nullptr));
    w5.Build(many, nullptr);
    CHECK(EnumeratePlayers(Natives(w5), PlayersLayout{}, nullptr).size() == 64);
}
#else
static void TestEnumerate() { std::printf("  (x64 build: enumeration is x86-only, skipped)\n"); }
#endif

static PlayerInfo P(uint32_t id, bool local, uint32_t ref, bool has = true) {
    PlayerInfo p;
    p.netId = id;
    p.isLocal = local;
    p.refId = ref;
    p.hasRef = has;
    p.name = "p" + std::to_string(id);
    return p;
}

static void TestSlotSelection() {
    std::vector<PlayerInfo> ps = {P(7, true, 0x14), P(11, false, 0x0100AAAA), P(12, false, 0x0100BBBB), P(13, false, 0, false)};
    CHECK(SelectGhostRef(ps, 0, {}) == 0);                        // slot 0 is the host: no ghost
    CHECK(SelectGhostRef(ps, 1, {}) == 0x0100AAAA);               // join order among remote characters
    CHECK(SelectGhostRef(ps, 2, {}) == 0x0100BBBB);
    CHECK(SelectGhostRef(ps, 3, {}) == 0);                        // third guest connected but ghost not spawned yet
    CHECK(SelectGhostRef(ps, 4, {}) == 0);                        // no such guest
    CHECK(SelectGhostRef({}, 1, {}) == 0);                        // nobody connected
    // explicit binding wins and removes that player from the join-order pool
    std::vector<SlotBindings> b = {{1, 12}};
    CHECK(SelectGhostRef(ps, 1, b) == 0x0100BBBB);
    CHECK(SelectGhostRef(ps, 2, b) == 0x0100AAAA);                // first unbound remote (11)
    CHECK(SelectGhostRef(ps, 3, b) == 0);                         // 13: unbound but no ref
    CHECK(SelectGhostRef(ps, 1, {{1, 99}}) == 0);                 // bound player not connected
    CHECK(SelectGhostRef(ps, 1, {{1, 7}}) == 0);                  // the local player is never a ghost
}

int main() {
    LoadReal();
    struct { const char* n; void (*f)(); } tests[] = {
        {"Players: signatures on the real 9.4 image", TestResolve},
        {"Players: enumeration on a fake object graph (mutation twin)", TestEnumerate},
        {"Ghost ref per coop slot (mutation twin)", TestSlotSelection},
    };
    for (auto& t : tests) {
        int before = g_fail;
        std::printf("[ RUN ] %s\n", t.n);
        t.f();
        std::printf("[%s] %s\n", g_fail == before ? "  OK " : "FAIL ", t.n);
    }
    std::printf("\n%d checks, %d failed%s\n", g_run, g_fail, g_have ? "" : " (real client.dll NOT found)");
    return g_fail ? 1 : 0;
}
