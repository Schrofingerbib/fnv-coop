// EngineAdapter over a fake IActorEngine: every ISpawnEngine call becomes the right engine call, and the hooks fill what IActorEngine lacks.
#include "minitest.h"
#include "spawn/engine_adapter.h"

using namespace nvcoop::spawn;
namespace ae = nvcoop::actors::engine;

namespace {

class FakeActors : public ae::IActorEngine {
public:
    std::map<ae::RefId, ae::ActorRaw> raws;
    std::map<uint32_t, FormRef> forms;
    std::map<uint32_t, ae::CellInfo> cells;
    std::vector<std::string> log;

    bool worldReady() override { return true; }
    uint32_t worldEpoch() override { return 1; }
    ae::RefId playerRef() override { return 0x14; }
    bool read(ae::RefId ref, ae::ActorRaw& out) override {
        auto it = raws.find(ref);
        if (it == raws.end()) return false;
        out = it->second;
        return true;
    }
    bool actorValue(ae::RefId, uint8_t, float&) override { return false; }
    void loadedActors(std::vector<ae::RefId>&, size_t) override {}
    bool cellLoaded(nvcoop::actors::CellId) override { return true; }
    bool cellInfo(nvcoop::actors::CellId c, ae::CellInfo& out) override {
        auto it = cells.find(c);
        if (it == cells.end()) return false;
        out = it->second;
        return true;
    }
    bool toFormRef(uint32_t id, FormRef& out) override {
        auto it = forms.find(id);
        if (it == forms.end()) return false;
        out = it->second;
        return true;
    }
    uint32_t fromFormRef(const FormRef&) override { return 0; }
    ae::RefId anchorInCell(nvcoop::actors::CellId, nvcoop::actors::live::Vec3&) override { return 0; }
    ae::RefId placeAtMe(ae::RefId, uint32_t) override { return 0; }
    bool setPos(ae::RefId, const nvcoop::actors::live::Vec3&, uint8_t) override { return true; }
    bool setYaw(ae::RefId, float) override { return true; }
    bool moveToCell(ae::RefId, nvcoop::actors::CellId, const nvcoop::actors::live::Vec3&, float) override { return true; }
    bool setRestrained(ae::RefId, bool) override { return true; }
    bool stopCombat(ae::RefId) override { return true; }
    bool startCombat(ae::RefId, ae::RefId) override { return true; }
    bool evaluatePackage(ae::RefId) override { return true; }
    bool setEssential(ae::RefId, bool) override { return true; }
    bool forceAv(ae::RefId, uint8_t, float) override { return true; }
    bool killActor(ae::RefId r) override { log.push_back("kill:" + std::to_string(r)); return true; }
    bool resurrect(ae::RefId) override { return true; }
    bool setDisabled(ae::RefId r, bool d) override { log.push_back(std::string(d ? "disable:" : "enable:") + std::to_string(r)); return true; }
    bool markForDelete(ae::RefId r) override { log.push_back("delete:" + std::to_string(r)); return true; }
};

ae::ActorRaw raw(ae::RefId ref, uint32_t base, uint32_t cell) {
    ae::ActorRaw a;
    a.valid = true; a.ref = ref; a.baseId = base; a.cell = cell; a.pos = {1, 2, 3};
    return a;
}

}  // namespace

TEST(adapter, describe_fills_the_ref_info_from_the_engine_and_the_hooks) {
    FakeActors fe;
    fe.forms[0x200] = FormRef("falloutnv.esm", 0x777);          // base
    fe.forms[0x300] = FormRef("falloutnv.esm", 0xC1);           // cell
    fe.forms[0x4000] = FormRef("falloutnv.esm", 0x1234);        // the placed ref itself
    ae::CellInfo ci; ci.known = true; ci.exterior = false;
    fe.cells[0x300] = ci;
    fe.raws[0x4000] = raw(0x4000, 0x200, 0x300);
    fe.raws[0xFF000005u] = raw(0xFF000005u, 0x200, 0x300);
    AdapterHooks h;
    h.placedNetId = [](RefId ref, const FormRef& w) { return ref == 0x4000 && w.localId == 0x1234 ? 0xABCDu : 0u; };
    h.extras = [](RefId ref, FormRef& lev, FormRef& zone) { if (ref == 0x4000) { lev = FormRef("falloutnv.esm", 0x7000); zone = FormRef("falloutnv.esm", 0x8000); } };
    EngineAdapter a(fe, h);
    RefInfo i;
    REQUIRE(a.describe(0x4000, i));
    CHECK(i.valid && !i.created && i.base == FormRef("falloutnv.esm", 0x777) && i.cell == 0x300 && i.cellForm == FormRef("falloutnv.esm", 0xC1));
    CHECK(i.interior);
    CHECK(i.wireRef == FormRef("falloutnv.esm", 0x1234) && i.netId == 0xABCDu);
    CHECK(i.leveledFrom.localId == 0x7000 && i.encounterZone.localId == 0x8000);
    CHECK(i.pos.x == 1 && i.pos.y == 2 && i.pos.z == 3);
    REQUIRE(a.describe(0xFF000005u, i));
    CHECK(i.created && i.netId == 0 && i.wireRef.plugin.empty());       // a created ref has no wire identity of its own
    // gone, deleted, the player and unknown refs are not described
    fe.raws[0x5000] = raw(0x5000, 0x200, 0x300); fe.raws[0x5000].deleted = true;
    fe.raws[0x14] = raw(0x14, 0x200, 0x300); fe.raws[0x14].isPlayer = true;
    CHECK(!a.describe(0x5000, i) && !a.describe(0x14, i) && !a.describe(0x9999, i) && !a.describe(0, i));
}

TEST(adapter, every_action_is_the_matching_engine_call) {
    FakeActors fe;
    AdapterHooks h;
    std::vector<std::string> hookCalls;
    h.rebase = [&](RefId r, const FormRef& b) { hookCalls.push_back("rebase:" + std::to_string(r) + ":" + std::to_string(b.localId)); return true; };
    h.bindCreated = [&](RefId r, NetId n) { hookCalls.push_back("bind:" + std::to_string(r) + ":" + std::to_string(n)); };
    h.isCreatedBound = [](NetId n) { return n == 7; };
    h.netOfCreated = [](RefId r, NetId& n) { if (r == 9) { n = 77; return true; } return false; };
    h.createFromSpawnRef = [&](const fnvcoop::SpawnRef& s) { hookCalls.push_back("make:" + std::to_string(s.id)); return true; };
    h.refOfNet = [](NetId n) { return n == 5 ? 55u : 0u; };
    EngineAdapter a(fe, h);
    CHECK(a.setHeld(10, true) && a.setHeld(10, false));
    CHECK(a.deleteRef(11));
    CHECK(a.kill(12));
    CHECK((fe.log == std::vector<std::string>{"disable:10", "enable:10", "delete:11", "kill:12"}));
    CHECK(!a.setHeld(0, true) && !a.deleteRef(0) && !a.kill(0));          // ref 0 is nothing
    fe.log.clear();
    CHECK(a.rebase(13, FormRef("falloutnv.esm", 0x99)));
    CHECK((fe.log == std::vector<std::string>{"enable:13"}));             // never left hidden
    a.bindCreated(14, 99);
    CHECK(a.isCreatedBound(7) && !a.isCreatedBound(8));
    NetId n = 0;
    CHECK(a.netOfCreated(9, n) && n == 77 && !a.netOfCreated(10, n));
    fnvcoop::SpawnRef s; s.id = 31;
    CHECK(a.createFromSpawnRef(s));
    CHECK(a.refOfNet(5) == 55 && a.refOfNet(6) == 0);
    CHECK((hookCalls == std::vector<std::string>{"rebase:13:153", "bind:14:99", "make:31"}));
}

TEST(adapter, missing_hooks_mean_unsupported_never_a_crash) {
    FakeActors fe;
    EngineAdapter a(fe, AdapterHooks());
    CHECK(!a.rebase(1, FormRef("falloutnv.esm", 1)));                     // TODO-RE: the base swap has no verified call
    CHECK((fe.log == std::vector<std::string>{"enable:1"}));
    NetId n = 0;
    CHECK(!a.isCreatedBound(1) && !a.netOfCreated(1, n) && !a.createFromSpawnRef(fnvcoop::SpawnRef()) && a.refOfNet(1) == 0);
    a.bindCreated(1, 2);
    RefInfo i;
    fe.raws[3] = raw(3, 0x200, 0);
    REQUIRE(a.describe(3, i));
    CHECK(i.leveledFrom.plugin.empty() && i.encounterZone.plugin.empty() && i.netId == 0);
}
