// Dialogue (0xD1) over a real session: late join, result-txn link, initiator disconnect. Uses the L8 dialogue stack read-write only
// through its public headers (plugin/dialogue/include) on a second, reliable+ordered network, next to the lossy story network.
#include <memory>
#include "fnvcoop/session.h"
#include "minitest.h"
#include "rejoin_rig.h"

using namespace fnvcoop;
using namespace rejoin;

namespace {
const FormRef kNpc = Q(0x104C0F);
const FormRef kTopic = Q(0x105560);

void startTalk(Rig& r, size_t ci) { r.cl[ci]->dn->requestStart(kNpc, uint32_t(r.now)); }
void pickTopic(Rig& r, size_t ci, uint32_t infoId, bool goodbye) {
    r.cl[ci]->dn->pick(kTopic, Q(infoId), 0, goodbye, {dlg::Line{"line", "v.ogg", 0, 50}}, {}, uint32_t(r.now));
}
int countEv(const std::vector<dlg::Event>& v, dlg::Event::Kind k) { int n = 0; for (auto& e : v) n += e.kind == k; return n; }
bool noDuplicateItems(Rig& r, std::string* why) {
    for (size_t i = 0; i < r.cl.size(); ++i)
        for (auto& kv : r.cl[i]->sink.items)
            if (kv.second > 1) { if (why) *why = "duplicate item " + std::to_string(kv.first) + " on client " + std::to_string(i); return false; }
    return true;
}

bool lateJoinDuringDialogue(uint64_t seed, std::string* why) {
    auto fail = [&](const char* w) { if (why) *why = w; return false; };
    Rig r(seed, true);
    r.addClient(1); r.addClient(2);
    r.joinOnly(0); r.run(1200);
    startTalk(r, 0); r.run(400);
    if (r.cl[0]->dn->client().role() != dlg::ClientDialogue::Role::Initiator) return fail("no initiator");
    pickTopic(r, 0, 0x105583, false); r.run(300);                    // a line is playing (End script pending)
    const dlg::ConvId conv = r.cl[0]->dn->client().conv();
    r.joinOnly(1); r.run(3000);                                      // client 2 joins in the middle
    if (!r.cl[1]->cs->joined()) return fail("late joiner not joined");
    auto& late = r.cl[1]->dn->client();
    if (late.role() != dlg::ClientDialogue::Role::Spectator || late.conv() != conv) return fail("late joiner did not become a spectator of the running conversation");
    if (late.history().empty()) return fail("late joiner did not get the line being played");
    r.cl[0]->dn->lineDone(uint32_t(r.now)); r.run(400);
    if (countEv(r.cl[0]->ev, dlg::Event::TopicsReady) != 1) return fail("initiator topics not ready");
    pickTopic(r, 0, 0x105584, true); r.run(300);
    r.cl[0]->dn->lineDone(uint32_t(r.now)); r.run(800);
    if (countEv(r.cl[1]->ev, dlg::Event::CloseMenu) != 1) return fail("late joiner not told the conversation closed");
    if (late.role() != dlg::ClientDialogue::Role::Idle) return fail("late joiner stuck as spectator");
    if (r.hostDn->host()->open() != 0 || r.hostDn->host()->npcBusy(kNpc)) return fail("host kept the conversation/lock");
    // two txns per step (begin, end); the late joiner joined after step 1 began, so it must have step 2's rewards, never twice
    if (r.cl[1]->sink.items[0x5000 + 2 * 2] != 1 || r.cl[1]->sink.items[0x5000 + 2 * 2 + 1] != 1) return fail("late joiner missed step 2 rewards");
    return noDuplicateItems(r, why);
}

// The End-script txn of the previous step must be applied on the initiator before its next topic list is built.
bool resultLinkHolds(uint64_t seed, std::string* why) {
    auto fail = [&](const char* w) { if (why) *why = w; return false; };
    Rig r(seed, true, 10);
    r.addClient(1); r.addClient(2);
    r.setStoryLatency(150, 260, 0.0);                                // story is much slower than the dialogue channel
    r.joinAll(); r.run(3000);
    startTalk(r, 0); r.run(200);
    for (uint32_t step = 1; step <= 3; ++step) {
        pickTopic(r, 0, 0x105580 + step, false); r.run(100);
        r.cl[0]->dn->lineDone(uint32_t(r.now));
        size_t before = r.cl[0]->appliedAtTopics.size();
        for (int t = 0; t < 400 && r.cl[0]->appliedAtTopics.size() == before; ++t) r.run(5);
        if (r.cl[0]->appliedAtTopics.size() != before + 1) return fail("topics never became ready");
        const uint64_t endSeq = r.dlgRuns.back();                    // the End-phase txn of this step
        if (r.cl[0]->appliedAtTopics.back() < endSeq) return fail("topics ready before the result txn was applied locally");
    }
    r.cl[0]->dn->leave(uint32_t(r.now)); r.run(2000);
    return noDuplicateItems(r, why);
}

// Story link dead while the dialogue channel works: the gate times out instead of hanging forever.
bool resultLinkTimesOut(std::string* why) {
    auto fail = [&](const char* w) { if (why) *why = w; return false; };
    Rig r(5, true, 10);
    r.addClient(1);
    r.joinAll(); r.run(1500);
    startTalk(r, 0); r.run(200);
    r.story.setConnected(r.cl[0]->peer, false);
    pickTopic(r, 0, 0x105581, false); r.run(100);
    r.cl[0]->dn->lineDone(uint32_t(r.now)); r.run(3000);
    bool timed = false;
    for (auto& e : r.cl[0]->ev) timed |= e.kind == dlg::Event::TopicsReady && e.timedOut;
    if (!timed) return fail("gate did not time out");
    return true;
}

bool initiatorDisconnect(uint64_t seed, std::string* why) {
    auto fail = [&](const char* w) { if (why) *why = w; return false; };
    Rig r(seed, true);
    r.addClient(1); r.addClient(2);
    r.joinAll(); r.run(2500);
    if (!r.allLive()) return fail("not live");
    startTalk(r, 0); r.run(400);
    pickTopic(r, 0, 0x105583, false); r.run(300);
    const dlg::ConvId conv = r.cl[0]->dn->client().conv();
    r.hostDn->controlLock(conv, 0x0F, true, uint32_t(r.now)); r.run(100);
    if (!r.hostDn->host()->controlsLocked(conv)) return fail("control lock not taken");
    const size_t runsBefore = r.dlgRuns.size();                      // Begin ran
    r.drop(0);                                                       // initiator vanishes mid-line
    r.run(300);
    if (r.hostDn->host()->open() != 0) return fail("conversation still open on the host");
    if (r.hostDn->host()->npcBusy(kNpc)) return fail("NPC lock leaked");
    if (r.hostDn->host()->controlsLocked(conv)) return fail("control lock leaked");
    if (r.dlgRuns.size() != runsBefore + 1 || r.dlgRunKeys.back().second != dlg::Phase::End) return fail("pending end script was dropped");
    if (countEv(r.hostEv, dlg::Event::CloseMenu) != 1) return fail("host did not close its menu");
    if (countEv(r.cl[1]->ev, dlg::Event::CloseMenu) != 1 || r.cl[1]->dn->client().role() != dlg::ClientDialogue::Role::Idle) return fail("spectator not released");
    // the spectator can talk to the NPC right away
    r.cl[1]->dn->requestStart(kNpc, uint32_t(r.now)); r.run(300);
    if (r.cl[1]->dn->client().role() != dlg::ClientDialogue::Role::Initiator) return fail("NPC not free for the other player");
    r.cl[1]->dn->leave(uint32_t(r.now)); r.run(300);
    // the initiator comes back: its stale role is cleared, the menu is closed, and it can start again
    r.reconnect(0); r.run(2500);
    r.cl[0]->dn->resync(uint32_t(r.now));
    if (countEv(r.cl[0]->ev, dlg::Event::CloseMenu) != 1) return fail("returning initiator not told to close its menu");
    if (r.cl[0]->dn->client().role() != dlg::ClientDialogue::Role::Idle) return fail("returning initiator keeps a stale role");
    startTalk(r, 0); r.run(400);
    if (r.cl[0]->dn->client().role() != dlg::ClientDialogue::Role::Initiator) return fail("returning initiator cannot talk");
    r.cl[0]->dn->leave(uint32_t(r.now)); r.run(1500);
    if (!r.allLive()) return fail("not live at the end");
    for (auto& c : r.cl) if (c->cs->state() != r.host.state()) return fail("diverged");
    return noDuplicateItems(r, why);
}
// The NPC dies mid-line while the initiator is still connected: its control lock must be released and its menu closed.
bool npcDeathReleasesControls(uint64_t seed, std::string* why) {
    auto fail = [&](const char* w) { if (why) *why = w; return false; };
    Rig r(seed, true);
    r.addClient(1); r.addClient(2);
    r.joinAll(); r.run(2500);
    startTalk(r, 0); r.run(400);
    pickTopic(r, 0, 0x105583, false); r.run(300);
    const dlg::ConvId conv = r.cl[0]->dn->client().conv();
    r.hostDn->controlLock(conv, 0x0F, true, uint32_t(r.now)); r.run(200);
    r.hostDn->npcDied(kNpc, uint32_t(r.now)); r.run(400);
    int locked = 0, released = 0;
    for (auto& e : r.cl[0]->ev) if (e.kind == dlg::Event::ControlLock) { auto& c = std::get<dlg::Control>(e.msg); (c.locked ? locked : released)++; }
    if (locked != 1 || released != 1) return fail("initiator controls not released when the conversation closed");
    if (countEv(r.cl[0]->ev, dlg::Event::CloseMenu) != 1 || r.cl[0]->dn->client().role() != dlg::ClientDialogue::Role::Idle) return fail("initiator menu not closed");
    if (r.hostDn->host()->npcBusy(kNpc)) return fail("NPC lock leaked");
    return noDuplicateItems(r, why);
}
}  // namespace

TEST(rejoin, npc_death_mid_dialogue_releases_initiator_controls) {
    for (uint64_t seed = 1; seed <= 3; ++seed) { std::string why; CHECK_MSG(npcDeathReleasesControls(seed, &why), "seed " + std::to_string(seed) + ": " + why); }
}
TEST(rejoin, late_joiner_during_active_dialogue_becomes_spectator) {
    for (uint64_t seed = 1; seed <= 4; ++seed) { std::string why; CHECK_MSG(lateJoinDuringDialogue(seed, &why), "seed " + std::to_string(seed) + ": " + why); }
}
TEST(rejoin, dialogue_result_txn_link_holds_topics_until_story_applied) {
    for (uint64_t seed = 1; seed <= 3; ++seed) { std::string why; CHECK_MSG(resultLinkHolds(seed, &why), "seed " + std::to_string(seed) + ": " + why); }
    std::string why;
    CHECK_MSG(resultLinkTimesOut(&why), why);
}
TEST(rejoin, initiator_disconnect_mid_dialogue_releases_locks_and_closes_menus) {
    for (uint64_t seed = 1; seed <= 4; ++seed) { std::string why; CHECK_MSG(initiatorDisconnect(seed, &why), "seed " + std::to_string(seed) + ": " + why); }
}

#define DLG_TWIN(name, flag, scenario)                                                       \
    TEST(twin, name) {                                                                       \
        dlg::MutationScope m([] { dlg::Mutations x; x.flag = true; return x; }());           \
        bool ok = true;                                                                      \
        for (uint64_t seed = 1; seed <= 3 && ok; ++seed) { std::string why; ok = scenario; } \
        CHECK(!ok);                                                                          \
    }
DLG_TWIN(rejoin_dlg_no_join_catchup, noJoinCatchUp, lateJoinDuringDialogue(seed, &why))
DLG_TWIN(rejoin_dlg_ignore_result_link, ignoreResultLink, resultLinkHolds(seed, &why))
DLG_TWIN(rejoin_dlg_drop_end_on_close, dropEndOnClose, initiatorDisconnect(seed, &why))
DLG_TWIN(rejoin_dlg_leak_lock_on_close, leakLockOnClose, initiatorDisconnect(seed, &why))
DLG_TWIN(rejoin_dlg_skip_control_release, skipControlRelease, npcDeathReleasesControls(seed, &why))
DLG_TWIN(rejoin_dlg_no_resync, noResync, initiatorDisconnect(seed, &why))
DLG_TWIN(rejoin_dlg_replay_duplicate_pick, replayDuplicatePick, lateJoinDuringDialogue(seed, &why))
