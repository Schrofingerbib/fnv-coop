// plugin/combat: the call-site patcher on a fake image holding the REAL bytes of FalloutNV.exe 1.4.0.525 at the six call sites and the function entry.
#include <cstring>
#include <map>
#include "combat/call_patch.h"
#include "combat/combat_addrs.h"
#include "combat/combat_sync.h"
#include "minitest.h"

using namespace nvcoop::combat;

namespace {

struct FakeImage : nvhooks::MemoryOps {
    std::map<uint32_t, uint8_t> mem;
    bool failWriteAt = false;
    uint32_t failAddr = 0;
    void put(uint32_t a, const uint8_t* b, size_t n) { for (size_t i = 0; i < n; ++i) mem[a + static_cast<uint32_t>(i)] = b[i]; }
    bool read(nvhooks::Addr a, void* out, size_t n) override {
        for (size_t i = 0; i < n; ++i) {
            auto it = mem.find(a + static_cast<uint32_t>(i));
            if (it == mem.end()) return false;
            static_cast<uint8_t*>(out)[i] = it->second;
        }
        return true;
    }
    bool write(nvhooks::Addr a, const void* in, size_t n) override {
        if (failWriteAt && a <= failAddr && failAddr < a + n) return false;
        for (size_t i = 0; i < n; ++i) mem[a + static_cast<uint32_t>(i)] = static_cast<const uint8_t*>(in)[i];
        return true;
    }
    nvhooks::Addr allocExec(size_t) override { return 0; }
    void freeExec(nvhooks::Addr, size_t) override {}
    uint32_t rel(uint32_t site) { uint8_t b[5] = {}; read(site, b, 5); uint32_t r; std::memcpy(&r, b + 1, 4); return r; }
};

FakeImage realImage() {
    FakeImage m;
    m.put(addr::kApplyHit, addr::kApplyHitPrologue, 16);
    for (size_t i = 0; i < addr::kApplyHitCallerCount; ++i) m.put(addr::kApplyHitCallers[i].site, addr::kSiteBytes[i], 5);
    return m;
}
constexpr uint32_t kThunk = 0x10001000;

int installAll(CallSitePatches& p) {
    return p.install(addr::kApplyHitCallers, addr::kApplyHitCallerCount, addr::kApplyHit, addr::kApplyHitPrologue, 16, kThunk);
}

TEST(combat_patch, the_recorded_site_bytes_really_call_the_hit_funnel) {
    for (size_t i = 0; i < addr::kApplyHitCallerCount; ++i) {
        uint32_t rel;
        std::memcpy(&rel, addr::kSiteBytes[i] + 1, 4);
        CHECK_MSG(addr::kSiteBytes[i][0] == 0xE8, "site " + std::to_string(i));
        CHECK_MSG(rel == relTo(addr::kApplyHitCallers[i].site, addr::kApplyHit), "site " + std::to_string(i));
    }
    CHECK(addr::kApplyHitCallerCount == 6);
}

TEST(combat_patch, all_six_sites_are_redirected_and_restored_byte_for_byte) {
    FakeImage m = realImage();
    FakeImage before = m;
    {
        CallSitePatches p(m);
        CHECK(installAll(p) == 6);
        CHECK(p.prologueOk());
        for (const CallSite& s : addr::kApplyHitCallers) CHECK(m.rel(s.site) == relTo(s.site, kThunk));
        for (const SiteResult& r : p.results()) CHECK(r.status == SiteStatus::Patched);
        CHECK(p.audit() == 0);
        CHECK(p.uninstall() == 6);
        CHECK(p.installed() == 0);
        CHECK(p.tampered() == 0);
    }
    CHECK(m.mem == before.mem);   // the opcode byte and the entry were never touched, the displacements are back
}

TEST(combat_patch, the_destructor_restores_whatever_is_still_installed) {
    FakeImage m = realImage();
    FakeImage before = m;
    {
        CallSitePatches p(m);
        installAll(p);
        CHECK(m.mem != before.mem);
    }
    CHECK(m.mem == before.mem);
}

TEST(combat_patch, another_exe_build_writes_nothing) {
    FakeImage m = realImage();
    m.mem[addr::kApplyHit + 3] ^= 0xFF;   // the function entry is different
    FakeImage before = m;
    CallSitePatches p(m);
    CHECK(installAll(p) == 0);
    CHECK(!p.prologueOk());
    CHECK(!p.lastError().empty());
    CHECK(m.mem == before.mem);
    for (const SiteResult& r : p.results()) CHECK(r.status == SiteStatus::Refused);
}

TEST(combat_patch, a_site_another_mod_already_changed_is_left_alone_and_the_rest_is_still_patched) {
    FakeImage m = realImage();
    m.mem[addr::kApplyHitCallers[2].site + 2] ^= 0x10;   // someone else's detour on site 2
    CallSitePatches p(m);
    CHECK(installAll(p) == 5);
    CHECK(p.results()[2].status == SiteStatus::WrongBytes);
    CHECK(m.rel(addr::kApplyHitCallers[2].site) != relTo(addr::kApplyHitCallers[2].site, kThunk));
    CHECK(m.rel(addr::kApplyHitCallers[3].site) == relTo(addr::kApplyHitCallers[3].site, kThunk));
}

TEST(combat_patch, a_site_chained_over_by_another_mod_is_not_restored_and_is_counted) {
    FakeImage m = realImage();
    CallSitePatches p(m);
    installAll(p);
    const uint32_t site = addr::kApplyHitCallers[4].site;
    const uint32_t theirs = 0x12345678;
    m.write(site + 1, &theirs, 4);   // a later mod re-pointed the call at its own code
    CHECK(p.audit() == 1);
    CHECK(p.uninstall() == 5);
    CHECK(p.tampered() == 1);
    CHECK(m.rel(site) == theirs);    // their patch survives
}

TEST(combat_patch, a_failed_write_is_reported_and_leaves_the_site_as_it_was) {
    FakeImage m = realImage();
    m.failWriteAt = true;
    m.failAddr = addr::kApplyHitCallers[1].site + 2;
    CallSitePatches p(m);
    CHECK(installAll(p) == 5);
    CHECK(p.results()[1].status == SiteStatus::WriteFailed);
    CHECK(m.rel(addr::kApplyHitCallers[1].site) == relTo(addr::kApplyHitCallers[1].site, addr::kApplyHit));
}

TEST(combat_patch, installing_twice_does_not_stack_patches) {
    FakeImage m = realImage();
    FakeImage before = m;
    CallSitePatches p(m);
    installAll(p);
    CHECK(installAll(p) == 6);
    p.uninstall();
    CHECK(m.mem == before.mem);
}

TEST(combat_patch, unreadable_memory_is_refused_not_guessed) {
    FakeImage m;   // nothing mapped
    CallSitePatches p(m);
    CHECK(installAll(p) == 0);
    CHECK(m.mem.empty());
}

TEST(combat_patch, limb_mapping_follows_the_engine_body_part_table) {
    // 0x1196DAC categories in the exe: 0 torso, 1,2 head, 3,4 left arm, 5,6 right arm, 7..12 legs, 13 brain (head), 14 none
    CHECK(limbOfLocation(0) == cc::Limb::Torso);
    CHECK(limbOfLocation(1) == cc::Limb::Head);
    CHECK(limbOfLocation(2) == cc::Limb::Head);
    CHECK(limbOfLocation(13) == cc::Limb::Head);
    CHECK(limbOfLocation(3) == cc::Limb::LeftArm);
    CHECK(limbOfLocation(6) == cc::Limb::RightArm);
    CHECK(limbOfLocation(8) == cc::Limb::LeftLeg);
    CHECK(limbOfLocation(11) == cc::Limb::RightLeg);
    CHECK(limbOfLocation(14) == cc::Limb::Torso);
    CHECK(limbOfLocation(-1) == cc::Limb::Torso);
    CHECK(limbOfLocation(99) == cc::Limb::Torso);
}

}  // namespace
