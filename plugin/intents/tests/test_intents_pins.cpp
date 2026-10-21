// Engine pins of the intents module (engine/intents_addrs.h): a second, hand-copied set of every byte, spec validity, a dry install on a fake memory
// image, and (when an reference FalloutNV.exe is at hand) a comparison with the real image. Twin 325 corrupts the hand copy.
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iterator>
#include <map>
#include <vector>

#include "intents/engine/intents_addrs.h"
#include "fnvcoop_mut.h"
#include "minitest.h"

namespace addr = nvcoop::intents::addr;

namespace {

struct Pin { const char* name; unsigned addr; unsigned char bytes[16]; };

// Hand-checked against <reference exe> (FalloutNV.exe 1.4.0.525), 2026-10-21.
Pin kHooks[] = {
    {"CanSaveNow", 0x850FE0, {0x55, 0x8b, 0xec, 0x83, 0xec, 0x14, 0x89, 0x4d, 0xf0, 0x83, 0x3d, 0x3c, 0xea, 0x1d, 0x01, 0x00}},
    {"TerminalEntryRun", 0x501830, {0x55, 0x8b, 0xec, 0x51, 0x89, 0x4d, 0xfc, 0x8b, 0x4d, 0xfc, 0x83, 0xc1, 0x10, 0xe8, 0xfe, 0xf0}},
};
Pin kCalls[] = {
    {"MarkScriptEvent", 0x5AC750, {0x55, 0x8b, 0xec, 0x83, 0xec, 0x08, 0xc6, 0x45, 0xff, 0x00, 0x83, 0x7d, 0x0c, 0x00, 0x74, 0x2a}},
    {"QueueUIMessage", 0x7052F0, {0x55, 0x8b, 0xec, 0x6a, 0xff, 0x68, 0x38, 0xc3, 0xee, 0x00, 0x64, 0xa1, 0x00, 0x00, 0x00, 0x00}},
    {"CloseAllMenusCore", 0x7037E0, {0x55, 0x8b, 0xec, 0xe8, 0x28, 0x3a, 0xdb, 0xff, 0x85, 0xc0, 0x74, 0x1f, 0xe8, 0x1f, 0x3a, 0xdb}},
    {"TopMenuId", 0x714F00, {0x55, 0x8b, 0xec, 0x83, 0xec, 0x08, 0x89, 0x4d, 0xf8, 0xc7, 0x45, 0xfc, 0x00, 0x00, 0x00, 0x00}},
};

struct FakeMem : nvhooks::MemoryOps {
    std::map<nvhooks::Addr, uint8_t> mem;
    nvhooks::Addr next = 0x20000000;
    void put(nvhooks::Addr a, const unsigned char* b, size_t n) { for (size_t i = 0; i < n; ++i) mem[a + i] = b[i]; }
    bool read(nvhooks::Addr a, void* out, size_t n) override {
        for (size_t i = 0; i < n; ++i) {
            auto it = mem.find(a + i);
            if (it == mem.end()) return false;
            static_cast<uint8_t*>(out)[i] = it->second;
        }
        return true;
    }
    bool write(nvhooks::Addr a, const void* in, size_t n) override { put(a, static_cast<const uint8_t*>(in), n); return true; }
    nvhooks::Addr allocExec(size_t n) override { nvhooks::Addr a = next; next += (nvhooks::Addr)((n + 15) & ~15u); return a; }
    void freeExec(nvhooks::Addr, size_t) override {}
};

}  // namespace

TEST(intents_pins, generated_header_equals_the_hand_copy) {
    if (FNV_MUT(325)) kHooks[0].bytes[5] ^= 1;   // MUTANT 325: the hand copy drifts from the generated header
    const nvhooks::HookSpec* hooks[] = {&addr::CanSaveNow, &addr::TerminalEntryRun};
    for (size_t i = 0; i < 2; ++i) {
        CHECK(std::strcmp(hooks[i]->name, kHooks[i].name) == 0);
        CHECK(hooks[i]->target == kHooks[i].addr);
        CHECK(hooks[i]->expectedLen == 16 && std::memcmp(hooks[i]->expected, kHooks[i].bytes, 16) == 0);
    }
    const addr::CallSpec* calls[] = {&addr::MarkScriptEvent, &addr::QueueUIMessage, &addr::CloseAllMenusCore, &addr::TopMenuId};
    for (size_t i = 0; i < 4; ++i) {
        CHECK(std::strcmp(calls[i]->name, kCalls[i].name) == 0);
        CHECK(calls[i]->addr == kCalls[i].addr);
        CHECK(std::memcmp(calls[i]->bytes, kCalls[i].bytes, 16) == 0);
    }
    CHECK(addr::kAllHooks[0] == &addr::CanSaveNow && addr::kAllHooks[1] == &addr::TerminalEntryRun);
    CHECK(sizeof addr::kAllCalls / sizeof *addr::kAllCalls == 4);
    CHECK(addr::kContainerMenu == 0x3F0 && addr::kTerminalMenu == 0x421 && addr::InterfaceManagerPtr == 0x11D8A80);
}

TEST(intents_pins, specs_are_installable_and_refuse_a_foreign_exe) {
    for (const nvhooks::HookSpec* h : addr::kAllHooks) {
        std::string why;
        CHECK_MSG(nvhooks::specIsValid(*h, &why), std::string(h->name) + ": " + why);
        CHECK(h->retBytes == 4 && h->stolen >= 5 && h->stolen <= 16);   // both are `__thiscall` with one stack argument: ret 4
        FakeMem m;
        m.put(h->target, h->expected, 16);
        nvhooks::HookRegistry reg(m);
        nvhooks::Addr tramp = 0;
        CHECK(reg.acquire(*h, 0x30000000, &tramp) == nvhooks::PatchStatus::Ok && tramp != 0);
        uint8_t first = 0;
        m.read(h->target, &first, 1);
        CHECK(first == 0xE9);                                            // patched with a jump
        CHECK(reg.release(*h) == nvhooks::PatchStatus::Restored);
        uint8_t back[16] = {};
        m.read(h->target, back, 16);
        CHECK(std::memcmp(back, h->expected, 16) == 0);                  // and put back byte for byte
        FakeMem other;
        unsigned char wrong[16];
        std::memcpy(wrong, h->expected, 16);
        wrong[4] ^= 0x40;
        other.put(h->target, wrong, 16);
        nvhooks::HookRegistry reg2(other);
        CHECK(reg2.acquire(*h, 0x30000000, &tramp) == nvhooks::PatchStatus::PrologueMismatch);   // a different exe: nothing written
    }
}

TEST(intents_pins, real_exe_when_available) {
    const char* env = std::getenv("NVI_EXE");
    std::string path = env && *env ? env : "<reference exe>";
    std::ifstream f(path, std::ios::binary);
    if (!f) { std::printf("    (no reference exe at %s: pins not compared with the image)\n", path.c_str()); return; }
    std::vector<unsigned char> d((std::istreambuf_iterator<char>(f)), std::istreambuf_iterator<char>());
    auto u32 = [&](size_t o) { uint32_t v = 0; std::memcpy(&v, &d[o], 4); return v; };
    auto u16 = [&](size_t o) { uint16_t v = 0; std::memcpy(&v, &d[o], 2); return v; };
    REQUIRE(d.size() > 0x400);
    size_t e = u32(0x3C), nsec = u16(e + 6), opt = u16(e + 20);
    auto rd = [&](unsigned a, unsigned char* out) {
        for (size_t i = 0; i < nsec; ++i) {
            size_t o = e + 24 + opt + i * 40;
            uint32_t vs = u32(o + 8), va = u32(o + 12) + 0x400000u, rs = u32(o + 16), rp = u32(o + 20);
            if (a >= va && a < va + (vs > rs ? vs : rs) && rp + (a - va) + 16 <= d.size()) { std::memcpy(out, &d[rp + (a - va)], 16); return true; }
        }
        return false;
    };
    for (const Pin& p : kHooks) { unsigned char got[16] = {}; CHECK(rd(p.addr, got) && std::memcmp(got, p.bytes, 16) == 0); }
    for (const Pin& p : kCalls) { unsigned char got[16] = {}; CHECK(rd(p.addr, got) && std::memcmp(got, p.bytes, 16) == 0); }
}
