// Randomised sessions (2 to 4 machines, lossy / duplicating / jittery links, owner churn) with injected corruptions. Oracles:
//   * a session without a corruption has ZERO heals and no bug lines,
//   * after the corruptions every copy equals its authority (actors: the owner's copy within the tolerances; containers / objects: the host's),
//   * a corruption is healed at most once (no double apply), an owner's own copy is never touched by a heal,
//   * after convergence nothing is healed any more (quiescence = 0 heals).
// Seeds are fixed: a failure names the seed and reproduces exactly.
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <string>
#include "th.h"

using namespace adt;

namespace {

struct Plan {
    uint64_t seed = 0;
    int machines = 2;
    int actors = 8, boxes = 3, objs = 3;
    bool lossy = false;
    int corruptions = 0;
};

Plan planFor(uint64_t seed, int corruptions) {
    Rng rng(seed * 7919 + 13);
    Plan p;
    p.seed = seed;
    p.machines = 2 + static_cast<int>(rng.range(0, 2));
    p.actors = 6 + static_cast<int>(rng.range(0, 8));
    p.boxes = 2 + static_cast<int>(rng.range(0, 3));
    p.objs = 2 + static_cast<int>(rng.range(0, 3));
    p.lossy = seed % 3 == 1;
    p.corruptions = corruptions;
    return p;
}

void build(Rig& r, const Plan& p, Rng& rng) {
    for (int i = 0; i < p.actors; ++i) {
        const PlayerId owner = static_cast<PlayerId>(rng.range(0, static_cast<uint32_t>(p.machines - 1)));
        r.addActor(1000 + i, 0x100 + (i % 2), V(float(rng.range(0, 4000)), float(rng.range(0, 4000)), float(rng.range(0, 200))), owner,
                   float(40 + rng.range(0, 60)));
    }
    for (int i = 0; i < p.boxes; ++i) r.addState(Cat::Containers, 2000 + i, 0x100, rng.range(1, 1000));
    for (int i = 0; i < p.objs; ++i) r.addState(Cat::Objects, 3000 + i, 0x101, rng.range(1, 8));
}

PlayerId ownerOfKey(Rig& r, Key k) {
    OwnerView v;
    r.host().actors.owner(k, v);
    return v.owner;
}

// The change the normal sync layer would make: the owner changes, everybody else gets it a moment later.
void churn(Rig& r, const Plan& p, Rng& rng, const std::set<Key>* skip = nullptr) {
    const Key k = 1000 + rng.range(0, static_cast<uint32_t>(p.actors - 1));
    if (skip && skip->count(k)) return;
    const RawActor& a = r.m[ownerOfKey(r, k)]->actors.a[k];   // the owner's copy is the base
    RawActor n = a;
    n.pos = V(a.pos.x + float(rng.range(0, 600)) - 300.0f, a.pos.y + float(rng.range(0, 600)) - 300.0f, a.pos.z);
    n.hp = std::max(5.0f, a.hp - float(rng.range(0, 15)));
    for (auto& m : r.m) m->actors.a[k] = n;
}

bool same(const RawActor& a, const RawActor& b, const Config& c) {
    if (a.cell != b.cell || a.alive != b.alive) return false;
    if (a.alive) {
        if (std::fabs(a.hp - b.hp) > c.hpTolerance) return false;
        if (distance(a.pos, b.pos) > c.smoothMetres * c.unitsPerMetre) return false;
    }
    return true;
}

struct Outcome {
    bool ok = true;
    std::string why;
    uint64_t healsApplied = 0;
    size_t injected = 0;
};

Outcome oneSession(const Plan& p) {
    Outcome o;
    auto fail = [&](const std::string& w) { if (o.ok) { o.ok = false; o.why = w; } };
    Rng rng(p.seed);
    Config cfg;
    Rig r(p.machines, cfg);
    if (p.lossy) {
        r.bus.loss = 0.08;
        r.bus.dup = 0.1;
        r.bus.jitter = 60;
    }
    r.bus.latency = 10 + rng.range(0, 60);
    build(r, p, rng);
    r.run(6000);

    // phase 1: a healthy session with owner churn heals nothing
    for (int i = 0; i < 40; ++i) {
        churn(r, p, rng);
        r.run(500);
    }
    r.run(5000);
    if (r.healsAll() != 0) fail("healthy phase healed " + std::to_string(r.healsAll()));
    for (auto& m : r.m) if (!m->sync->bugs().empty()) fail("bug line in the healthy phase");

    // phase 2: inject corruptions on copies that are NOT the authority
    std::set<Key> corrupted;
    std::set<std::pair<Cat, Key>> corruptedStates;
    for (int c = 0; c < p.corruptions; ++c) {
        const int what = static_cast<int>(rng.range(0, 3));
        if (what <= 1) {
            const Key k = 1000 + rng.range(0, static_cast<uint32_t>(p.actors - 1));
            if (corrupted.count(k)) continue;
            const PlayerId ow = ownerOfKey(r, k);
            std::vector<int> others;
            for (int i = 0; i < p.machines; ++i) if (i != ow) others.push_back(i);
            RawActor& x = r.m[static_cast<size_t>(others[rng.range(0, static_cast<uint32_t>(others.size() - 1))])]->actors.a[k];
            if (what == 0) x.hp = std::max(1.0f, x.hp - 30.0f - float(rng.range(0, 20)));   // 30 to 50 hp off
            else x.pos = V(x.pos.x + 280.0f + float(rng.range(0, 1200)), x.pos.y, x.pos.z);   // 4 m to 21 m off
            corrupted.insert(k);
        } else {
            const Cat cat = what == 2 ? Cat::Containers : Cat::Objects;
            const Key k = cat == Cat::Containers ? 2000 + rng.range(0, static_cast<uint32_t>(p.boxes - 1)) : 3000 + rng.range(0, static_cast<uint32_t>(p.objs - 1));
            if (corruptedStates.count({cat, k})) continue;
            const int machine = 1 + static_cast<int>(rng.range(0, static_cast<uint32_t>(p.machines - 2)));   // never the host: it is the authority
            (cat == Cat::Containers ? r.m[static_cast<size_t>(machine)]->cont : r.m[static_cast<size_t>(machine)]->obj).a[k].state += 7;
            corruptedStates.insert({cat, k});
        }
    }
    o.injected = corrupted.size() + corruptedStates.size();

    // the session goes on: more churn while the heals happen
    for (int i = 0; i < 20; ++i) {
        churn(r, p, rng, &corrupted);
        r.run(1000);
    }
    r.run(p.lossy ? 240000 : 90000);

    // convergence
    for (int i = 0; i < p.actors; ++i) {
        const Key k = 1000 + i;
        const RawActor& truth = r.m[ownerOfKey(r, k)]->actors.a[k];
        for (auto& m : r.m) if (!same(truth, m->actors.a[k], cfg)) fail("actor " + std::to_string(k) + " did not converge on machine " + std::to_string(m->slot));
    }
    for (int i = 0; i < p.boxes; ++i) for (auto& m : r.m) if (m->cont.a[2000 + i].state != r.host().cont.a[2000 + i].state) fail("container state did not converge");
    for (int i = 0; i < p.objs; ++i) for (auto& m : r.m) if (m->obj.a[3000 + i].state != r.host().obj.a[3000 + i].state) fail("object state did not converge");

    // an owner's copy is never rewritten by a heal: it only changed through churn (which the test applied to everybody)
    for (int i = 0; i < p.actors; ++i) {
        const PlayerId ow = ownerOfKey(r, 1000 + i);
        if (r.m[ow]->actors.healsByKey.count(1000 + i)) fail("an owner was healed (actor " + std::to_string(1000 + i) + ")");
    }

    // no double apply: each injected corruption is corrected at most once
    for (auto& m : r.m) for (size_t c = 0; c < kCats; ++c) o.healsApplied += m->sync->counters().cat[c].healsApplied;
    if (o.healsApplied > o.injected) fail("healed " + std::to_string(o.healsApplied) + " times for " + std::to_string(o.injected) + " corruptions");

    // quiescence: nothing is healed any more
    const uint64_t before = r.healsAll();
    r.run(30000);
    if (r.healsAll() != before) fail("heals after convergence");
    return o;
}

}  // namespace

TEST(storm, healthy_sessions_heal_nothing) {
    int bad = 0;
    for (uint64_t seed = 1; seed <= 24; ++seed) {
        Outcome o = oneSession(planFor(seed, 0));
        if (!o.ok) { std::printf("    seed %llu: %s\n", static_cast<unsigned long long>(seed), o.why.c_str()); ++bad; }
        CHECK(o.healsApplied == 0);
    }
    CHECK(bad == 0);
}

TEST(storm, corruptions_converge_to_their_authority_and_are_healed_once) {
    int bad = 0;
    uint64_t healed = 0;
    for (uint64_t seed = 1; seed <= 36; ++seed) {
        Outcome o = oneSession(planFor(seed, 1 + static_cast<int>(seed % 4)));
        if (!o.ok) { std::printf("    seed %llu: %s\n", static_cast<unsigned long long>(seed), o.why.c_str()); ++bad; }
        healed += o.healsApplied;
    }
    CHECK(bad == 0);
    CHECK(healed > 20);          // the sessions did get corrupted and the heals did happen
}

// ---- cost -----------------------------------------------------------------------------------------------------------------------------
// Wall-clock numbers of a game-free run on whatever machine this is (fake sources, no engine reads): printed for the docs, asserted only against
// a limit that is far above anything reasonable, so a loaded PC does not turn this into a flaky test.
TEST(perf, a_round_with_hundreds_of_actors_is_cheap) {
    Rig r(3);
    for (Key k = 1; k <= 400; ++k) r.addActor(k, 0x100 + static_cast<CellId>(k % 4), V(float(k) * 10, float(k % 13) * 50, 0), static_cast<PlayerId>(k % 3), 50.0f + float(k % 50));
    for (Key k = 0; k < 60; ++k) r.addState(Cat::Containers, 2000 + k, 0x100 + static_cast<CellId>(k % 4), static_cast<uint32_t>(k * 3));
    for (Key k = 0; k < 60; ++k) r.addState(Cat::Objects, 3000 + k, 0x100 + static_cast<CellId>(k % 4), static_cast<uint32_t>(k));
    r.run(6000);
    using Clock = std::chrono::steady_clock;
    // steady state: 10 rounds
    auto t0 = Clock::now();
    r.run(20000);
    const double steadyMs = std::chrono::duration<double, std::milli>(Clock::now() - t0).count() / 10.0;
    // a round with 40 wrong actors: digest, diff, heals
    for (Key k = 1; k <= 400; k += 10) r.cl(1).actors.a[k].hp = 5;
    r.run(4000);
    t0 = Clock::now();
    r.run(8000);
    const double healMs = std::chrono::duration<double, std::milli>(Clock::now() - t0).count() / 4.0;
    std::printf("    cost (3 machines, 400 actors + 120 refs, fake sources): %.3f ms per 2 s round steady, %.3f ms per round while healing 40 keys\n", steadyMs, healMs);
    CHECK(steadyMs < 100.0 && healMs < 200.0);
    // size of one client digest
    uint64_t before = r.bus.sent;
    r.run(2000);
    CHECK(r.bus.sent - before <= 3);        // two clients: one digest each per round (plus at most one answer)
    int c = 0;
    r.bus.hook = [&](Msg& m) { if (m.from == 1 && c == 0) { c = static_cast<int>(m.data.size()); } return true; };
    r.run(2100);
    std::printf("    digest datagram of client 1: %d bytes (400 actors in 4 cells, 60 containers, 60 objects)\n", c);
    CHECK(c > 0 && c < 600);
    r.bus.hook = nullptr;
}
