// EngineFlightObserver (T1a adapter for T2a's IEngineObserver): a lossless 3-player model session must leave one valid trace
// per machine. The twin runs the same session with the observer not attached and must find nothing.
#include <cstdio>
#include "flight/engine_observer.h"
#include "fnvcoop/engine/cluster.h"
#include "scenarios.h"
#include "../../tests/minitest.h"

using namespace fnvcoop;
using namespace fnvcoop::engine;

namespace {

bool session(bool attach) {
    flight::EngineFlightObserver obs;
    Cluster c(testing::baseConfig(5, 3));
    if (attach) c.setObserver(&obs);
    c.run(1500);
    const ActorState* a = nullptr;
    for (auto& kv : c.host().world().actors()) if (kv.second.hostile && !kv.second.dead) { a = &kv.second; break; }
    if (!a) return false;
    const NetId id = a->id;
    const CellId cell = a->cell;
    c.machine(1).move(cell, c.host().world().cellCenter(cell), 0);
    c.run(800);
    c.machine(1).attackActor(id, 30, Limb::Torso, c.machine(1).world().findActor(id)->pos, 1500);
    c.machine(0).setStage(FormRef("falloutnv.esm", 0x500001u), 3);
    c.run(2500);
    c.quiesce();
    int n = obs.dumpAll("out_flight_obs");
    if (n != 3) return false;   // not attached: nothing to dump
    bool ok = true;
    for (int m = 0; m < 3; ++m) {
        flight::Trace t;
        std::string f = "out_flight_obs.m" + std::to_string(m) + ".nvfr";
        if (!flight::readTrace(f, t)) { ok = false; continue; }
        std::remove(f.c_str());
        size_t out = 0, in = 0, own = 0, dig = 0, applied = 0;
        for (auto& r : t.records) {
            out += r.type == flight::kNetOut; in += r.type == flight::kNetIn; own += r.type == flight::kOwnership;
            dig += r.type == flight::kDigest; applied += r.type == flight::kTxnApplied;
        }
        if (t.header.slot != m || out == 0 || in == 0) ok = false;
        if (m == 0 && own == 0) ok = false;
        if (m != 0 && applied == 0) ok = false;
        (void)dig;
    }
    return ok;
}

}  // namespace

TEST(flightem, three_machine_session_leaves_one_trace_each) { CHECK(session(true)); }
TEST(twin, observer_not_attached) { CHECK(!session(false)); }
