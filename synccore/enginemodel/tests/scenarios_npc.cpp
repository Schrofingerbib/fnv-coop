// Regression scenarios for the NPC-sync bugs the fuzzer found in the engine model (docs/tools/T2a-engine.md, "NPC sync fixes").
// Each is shared with a mutation twin (test_twins.cpp) that re-introduces the bug and expects the scenario to fail.
#include <algorithm>
#include <cmath>
#include "scenarios.h"
#include "fnvcoop/wire.h"
#include "minitest.h"

using namespace fnvcoop;
using namespace fnvcoop::engine;

namespace fnvcoop::testing {

namespace {
void note(std::string* why, const std::string& s) { if (why && why->size() < 900) *why += s + "; "; }

ActorState createdActor(NetId id, CellId cell, const Vec3& pos) {
    ActorState a;
    a.id = id; a.ref = createdRef(id); a.created = true; a.cell = cell; a.pos = pos; a.hostile = false; a.enabled = true;
    return a;
}
}  // namespace

// Digest keys of created actors (0xFF......) used to be read as player slots, so repairing one overwrote a player's own state with the
// host's copy (a client that had just been hit went back to full health, and its newer vitals sequence was lost), and an actor that only a
// client had could never be removed. Here a client's player state is ahead of the host's (its vitals are "in flight") while the host holds
// a created actor the client lacks and the client holds one the host lacks: the heal must add the first, remove the second and leave the
// player alone.
bool createdActorRepairKeepsPlayerState(uint64_t seed, std::string* why) {
    bool ok = true;
    auto expect = [&](bool cond, const std::string& what) { if (!cond) { ok = false; note(why, what); } };
    Cluster c(baseConfig(seed, 2));
    c.run(1500);
    World& hw = c.host().world();
    World& cw = c.machine(1).world();
    const CellId cell = hw.cellAt(1, 1);
    const Vec3 at = hw.cellCenter(cell);
    // 0xFF0000F9 and 0xFF0000FA: the low byte modulo kMaxSlots (the old slot decoding) is player slot 1 and 2; far above the counter of created ids
    const NetId hostOnly = kCreatedIdBase | 0xF9u, clientOnly = kCreatedIdBase | 0xFAu;
    hw.putActor(createdActor(hostOnly, cell, at));
    cw.putActor(createdActor(clientOnly, cell, at));
    PlayerState& me = cw.player(1);
    me.health = 70; me.vitalsSeq += 1000;   // the client took damage; the host has not heard yet
    PlayerState& hostCopy = hw.player(1);
    const int32_t hostCopyHealth = hostCopy.health;
    c.run(6000);
    expect(c.totalRepairs() >= 1, "the missing created actor was never repaired in");
    expect(cw.findActor(hostOnly) != nullptr, "the host's created actor never reached the client");
    expect(cw.findActor(clientOnly) == nullptr, "an actor only the client has was never removed");
    expect(cw.player(1).health == 70, "the client's own health was overwritten by a repair (hp " + std::to_string(cw.player(1).health) + ")");
    expect(cw.player(1).vitalsSeq >= 1000, "the client's own vitals sequence was overwritten by a repair");
    expect(hw.player(1).health == hostCopyHealth, "the host's copy of the client changed");
    expect(cw.player(1).present && hw.player(1).present, "a player slot was removed by a repair of a created actor");
    return ok;
}


// A forced commit (the host does not wait for a slow old owner) used to start the new owner's AI while the old owner, which had not heard of
// the Transfer yet, was still driving the actor: two machines simulated one NPC. Here the host's messages to the old owner (a guest) take
// 2.6 s, longer than the host's handoff timeout, then the host walks into the guest's cell and takes the actor over.
bool forcedCommitNeverDrivesTwice(uint64_t seed, std::string* why) {
    bool ok = true;
    auto expect = [&](bool cond, const std::string& what) { if (!cond) { ok = false; note(why, what); } };
    Cluster c(baseConfig(seed, 2));
    c.run(2000);
    const World& hw = c.host().world();
    const CellId hostCell = hw.player(0).cell;
    NetId id = 0;
    CellId far = 0;
    for (auto& kv : hw.actors()) {   // a live actor in a cell the host is neither in nor next to
        const ActorState& a = kv.second;
        auto cd = hw.cells().find(a.cell);
        if (a.dead || !a.enabled || cd == hw.cells().end() || cd->second.interior || a.cell == hostCell || hw.adjacent(hostCell, a.cell)) continue;
        id = a.id; far = a.cell; break;
    }
    if (!id) { note(why, "no actor far from the host"); return false; }
    c.machine(1).move(far, hw.cellCenter(far), 0);
    c.run(3000);
    expect(c.host().view().ownerOf(id) == 1, "setup: the guest does not own the actor");
    if (!ok) return false;
    c.net().setShaper([](PeerId from, PeerId to, uint64_t, Bytes&) { return std::vector<uint32_t>{from == 0 && to == 1 ? 2600u : 15u}; });
    c.machine(0).move(far, hw.cellCenter(far), 0);
    bool overlap = false;
    for (int i = 0; i < 700; ++i) {   // 7 s in 10 ms steps, the two-owners oracle on every step
        c.run(10);
        if (!c.checkAlways().empty()) overlap = true;
    }
    expect(!overlap, "the actor was AI-driven on two machines");
    c.net().setShaper(nullptr);
    expect(c.host().view().ownerOf(id) == 0, "the host never took the actor over");
    c.run(1500);
    const ActorState* a = c.host().world().findActor(id);
    expect(a && a->aiOn, "the host never started driving the actor after the old owner stopped");
    expect(c.quiesce(30000), "cluster never became quiet");
    c.checkAlways();
    c.checkQuiescent();
    for (auto& v : c.allViolations()) { ok = false; note(why, v.what); }
    return ok;
}


// Same forced commit, but the NEW owner is a guest. The old owner's last state (sent when it finally hears of the handoff) carries the old epoch,
// so the host rejects it and used to drop it: the new owner never learned that the old one had stopped and kept its AI off until the safety
// timeout (8 s), a frozen NPC. The host now passes a previous owner's final state on to the new owner.
bool forcedCommitGuestNewOwnerStartsPromptly(uint64_t seed, std::string* why) {
    bool ok = true;
    auto expect = [&](bool cond, const std::string& what) { if (!cond) { ok = false; note(why, what); } };
    Cluster c(baseConfig(seed, 3));
    c.run(2000);
    const World& hw = c.host().world();
    const CellId hostCell = hw.player(0).cell;
    NetId id = 0;
    CellId far = 0;
    Vec3 where;
    for (auto& kv : hw.actors()) {   // a live actor in a cell neither the host nor machine 2 is in or next to
        const ActorState& a = kv.second;
        auto cd = hw.cells().find(a.cell);
        if (a.dead || !a.enabled || cd == hw.cells().end() || cd->second.interior || a.cell == hostCell || hw.adjacent(hostCell, a.cell)) continue;
        id = a.id; far = a.cell; where = a.pos; break;
    }
    if (!id) { note(why, "no actor far from the host"); return false; }
    c.machine(1).move(far, hw.cellCenter(far), 0);
    c.run(3000);
    expect(c.host().view().ownerOf(id) == 1, "setup: guest 1 does not own the actor");
    if (!ok) return false;
    // the host's messages to guest 1 take 2.6 s (longer than the host's handoff timeout); guest 2 walks up to the actor, much closer than guest 1
    c.net().setShaper([](PeerId from, PeerId to, uint64_t, Bytes&) { return std::vector<uint32_t>{from == 0 && to == 1 ? 2600u : 15u}; });
    c.machine(2).move(far, c.machine(1).world().findActor(id) ? c.machine(1).world().findActor(id)->pos : where, 0);
    uint64_t waited = 0;
    while (waited < 6000 && c.host().view().ownerOf(id) != 2) { c.run(10); waited += 10; }
    expect(c.host().view().ownerOf(id) == 2, "setup: the host never handed the actor to guest 2");
    if (!ok) return false;
    c.run(5000);   // far past the old owner's final state (2.6 s) and well below the 8 s safety timeout
    const ActorState* a2 = c.machine(2).world().findActor(id);
    expect(a2 && a2->aiOn, "the new guest owner is still waiting for the old owner's final state");
    c.net().setShaper(nullptr);
    expect(c.quiesce(30000), "cluster never became quiet");
    c.checkAlways();
    c.checkQuiescent();
    for (auto& v : c.allViolations()) { ok = false; note(why, v.what); }
    return ok;
}

// Digest reports travel unreliably, so the link may deliver one twice (or an old one late). The host repairs a key only after seeing the
// same mismatch in two separate reports ("stable": both sides have stopped moving); a duplicate of one report used to count as the second,
// so a difference that was about to close by itself got a repair. Reports are numbered now, and a copy or an older one is ignored.
bool duplicatedDigestIsNotAStableMismatch(uint64_t seed, std::string* why) {
    bool ok = true;
    auto expect = [&](bool cond, const std::string& what) { if (!cond) { ok = false; note(why, what); } };
    Cluster c(baseConfig(seed, 2));
    c.run(1500);
    if (!c.quiesce(30000)) { note(why, "setup: cluster never became quiet"); return false; }
    const uint64_t before = c.totalRepairs();
    // a report from the guest that disagrees with the host about one object (what a guest that is still catching up would send)
    MDigest d;
    d.cat = static_cast<uint8_t>(Category::Objects);
    auto keys = c.machine(1).world().keyHashes(Category::Objects);
    for (auto& kv : keys) d.keys.push_back(kv);
    if (d.keys.empty()) { note(why, "setup: no objects"); return false; }
    d.keys.front().second ^= 0x5555;
    d.total = 1469598103934665603ull;
    for (auto& kv : d.keys) { d.total = hash64(&kv.first, sizeof kv.first, d.total); d.total = hash64(&kv.second, sizeof kv.second, d.total); }
    auto send = [&](uint32_t seq) {
        Envelope e;
        e.sess = c.host().sess(); e.src = 1; e.dest = kHostPlayer;
        d.seq = seq;
        e.body = d;
        c.inject(0, 1, encode(e));
    };
    send(7000000);
    send(7000000);   // the link delivered the same report twice
    send(6999999);   // and an older one arrived late
    expect(c.totalRepairs() == before, "a duplicated / reordered digest report was taken as a second observation and caused a repair");
    send(7000001);   // a real second report, later: now the mismatch is stable and must be repaired
    c.run(500);
    expect(c.totalRepairs() > before, "a genuinely repeated mismatch was not repaired");
    return ok;
}

// A guest's copy of an actor that differs from the host's by less than a digest bucket (a few units of position, a few points of health) must still
// heal: an owner that leaves while its actor walks leaves the host and the other guests with different last positions, and nobody republishes them.
// The digests compare exactly now (the fault digestBucketsPositions brings back 256-unit position buckets and tens of health, which hide it).
bool smallDriftIsHealed(uint64_t seed, std::string* why) {
    bool ok = true;
    auto expect = [&](bool cond, const std::string& what) { if (!cond) { ok = false; note(why, what); } };
    Cluster c(baseConfig(seed, 2));
    c.run(1500);
    if (!c.quiesce(30000)) { note(why, "setup: cluster never became quiet"); return false; }   // AI stays frozen from here on: nothing republishes
    World& hw = c.host().world();
    World& cw = c.machine(1).world();
    NetId id = 0;
    for (auto& kv : hw.actors()) {
        const ActorState& a = kv.second;
        if (a.dead || !a.enabled || a.health != 100 || c.host().view().ownerOf(a.id) == 1) continue;   // a guest's own actors are not overwritten from the host
        id = a.id; break;
    }
    if (!id) { note(why, "setup: no suitable actor"); return false; }
    ActorState& drift = cw.actors().at(id);
    const float bx = std::floor(drift.pos.x / 256.0f);
    drift.pos.x += std::floor((drift.pos.x + 3.0f) / 256.0f) == bx ? 3.0f : -3.0f;   // stays inside its 256-unit bucket
    drift.health -= 3;                                                              // and inside its tens
    const uint64_t before = c.totalRepairs();
    c.run(7000);
    const ActorState* host = hw.findActor(id);
    const ActorState* guest = cw.findActor(id);
    expect(host && guest && host->pos == guest->pos && host->health == guest->health, "a sub-bucket difference was never healed");
    expect(c.totalRepairs() > before, "no repair was made");
    return ok;
}

// An actor that died before a guest joined is no longer tracked by the ownership service, but the host's table keeps the epoch of its last Release. The full state
// used to leave that out, so a joiner knew nothing of the actor and took a late copy of an older assignment (a reordered Grant from before the death) for news: it
// then believed a corpse had an owner and its table never matched the host's again (found by the fuzzer: hell profile, seed 300, a join right before the end).
bool joinerKeepsReleasedEpochs(uint64_t seed, std::string* why) {
    bool ok = true;
    auto expect = [&](bool cond, const std::string& what) { if (!cond) { ok = false; note(why, what); } };
    Cluster c(baseConfig(seed, 2));
    c.run(1500);
    Machine& host = c.host();
    NetId id = 0;
    OwnerEpoch grantedAt = 0;
    for (auto& kv : host.world().actors()) {   // a living actor the host drives
        const ActorState& a = kv.second;
        if (a.dead || !a.enabled || a.named || host.view().ownerOf(a.id) != kHostPlayer) continue;
        id = a.id; grantedAt = host.view().epochOf(a.id); break;
    }
    if (!id) { note(why, "setup: no actor owned by the host"); return false; }
    expect(host.killByStory(id), "setup: the story kill was refused");
    c.run(800);
    const ActorState* corpse = host.world().findActor(id);
    expect(corpse && corpse->dead, "setup: the actor did not die");
    expect(host.view().ownerOf(id) == kNoPlayer && host.view().epochOf(id) > grantedAt, "setup: the host's table does not show the release");
    if (!ok) return false;
    c.leave(1);
    c.run(300);
    c.join(1);
    c.run(2500);
    expect(c.machine(1).synced(), "setup: the joiner never synced");
    OwnMsg old;   // the assignment the actor had while it lived, arriving late
    old.kind = OwnMsg::Grant; old.netId = id; old.owner = kHostPlayer; old.epoch = grantedAt;
    Envelope e;
    e.sess = host.sess(); e.src = kHostPlayer; e.dest = 1; e.body = MOwn{old};
    c.inject(1, kHostPlayer, encode(e));
    c.run(600);
    expect(c.machine(1).view().ownerOf(id) == kNoPlayer, "the joiner gave the dead actor an owner again from a late old assignment");
    expect(c.machine(1).view().digest() == host.view().digest(), "the joiner's ownership table differs from the host's");
    expect(c.quiesce(30000), "cluster never became quiet");
    c.checkAlways();
    c.checkQuiescent();
    for (auto& v : c.allViolations()) { ok = false; note(why, v.what); }
    return ok;
}

}  // namespace fnvcoop::testing

namespace {
using namespace fnvcoop::testing;
TEST(engine, joiner_keeps_the_epoch_of_an_actor_that_died_before_it_came) {
    std::string why;
    const bool ok = joinerKeepsReleasedEpochs(1, &why);
    CHECK_MSG(ok, why);
}
TEST(engine, joiner_keeps_released_epochs_seed_2) {
    std::string why;
    const bool ok = joinerKeepsReleasedEpochs(2, &why);
    CHECK_MSG(ok, why);
}
TEST(engine, repair_of_a_created_actor_never_touches_a_player_slot) {
    std::string why;
    const bool ok = createdActorRepairKeepsPlayerState(1, &why);
    CHECK_MSG(ok, why);
}
TEST(engine, repair_of_a_created_actor_seed_2) {
    std::string why;
    const bool ok = createdActorRepairKeepsPlayerState(2, &why);
    CHECK_MSG(ok, why);
}
TEST(engine, forced_commit_never_has_two_machines_driving_one_actor) {
    std::string why;
    const bool ok = forcedCommitNeverDrivesTwice(1, &why);
    CHECK_MSG(ok, why);
}
TEST(engine, forced_commit_never_drives_twice_seed_2) {
    std::string why;
    const bool ok = forcedCommitNeverDrivesTwice(2, &why);
    CHECK_MSG(ok, why);
}
TEST(engine, forced_commit_guest_new_owner_starts_promptly) {
    std::string why;
    const bool ok = forcedCommitGuestNewOwnerStartsPromptly(1, &why);
    CHECK_MSG(ok, why);
}
TEST(engine, forced_commit_guest_new_owner_seed_2) {
    std::string why;
    const bool ok = forcedCommitGuestNewOwnerStartsPromptly(2, &why);
    CHECK_MSG(ok, why);
}
TEST(engine, duplicated_or_reordered_digest_report_is_not_a_second_observation) {
    std::string why;
    const bool ok = duplicatedDigestIsNotAStableMismatch(1, &why);
    CHECK_MSG(ok, why);
}
TEST(engine, duplicated_digest_report_seed_2) {
    std::string why;
    const bool ok = duplicatedDigestIsNotAStableMismatch(2, &why);
    CHECK_MSG(ok, why);
}
TEST(engine, small_position_and_health_drift_is_healed) {
    std::string why;
    const bool ok = smallDriftIsHealed(1, &why);
    CHECK_MSG(ok, why);
}
TEST(engine, small_drift_is_healed_seed_2) {
    std::string why;
    const bool ok = smallDriftIsHealed(2, &why);
    CHECK_MSG(ok, why);
}
TEST(world, player_key_range_is_exactly_the_player_slots) {
    CHECK(isPlayerKey(playerKey(0)));
    CHECK(isPlayerKey(playerKey(kMaxSlots - 1)));
    CHECK(!isPlayerKey(playerKey(kMaxSlots)));
    CHECK(!isPlayerKey(kCreatedIdBase | 1u));
    CHECK(!isPlayerKey(kCreatedIdBase | 0xF9u));
    CHECK(!isPlayerKey(0x1000u));
    for (uint32_t s = 0; s < kMaxSlots; ++s) CHECK(playerSlotOfKey(playerKey(s)) == s);
}
}  // namespace
