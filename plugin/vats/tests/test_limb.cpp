// plugin/vats: the public VATS API (vats_api.h) on a scripted engine: the state a bot reads and the actions that choose a limb / target / fire.
#include "harness.h"
#include "vats/vats_api.h"

using namespace nvcoop::vats;
using namespace vtest;

namespace {

constexpr uint32_t kRaider = 0x1000, kGhoul = 0x2000;

FakeEngine inVats(Mode mode = Mode::TargetSelect) {
    FakeEngine e;
    e.enter({humanoid(kRaider), humanoid(kGhoul, false)}, kRaider, mode);
    return e;
}

TEST(vats_limb, the_view_lists_the_selected_targets_parts_in_engine_order_with_the_highlight) {
    FakeEngine e = inVats();
    const VatsView v = readView(e);
    CHECK(v.mode == Mode::TargetSelect && v.menuOpen);
    CHECK(v.targetRef == kRaider && v.targetCount == 2);
    REQUIRE(v.limbs.size() == 6);
    CHECK(v.limbs[0].limb == LimbId::Torso && v.limbs[0].highlighted);   // torso is the default part
    CHECK(v.limbs[1].limb == LimbId::Head && v.limbs[1].index == 1);
    CHECK(v.highlighted == LimbId::Torso);
    CHECK(v.remembered == LimbId::Torso);
    CHECK(v.actionPoints == 60 && v.maxActionPoints == 100);
}

TEST(vats_limb, outside_vats_the_view_is_empty_even_if_the_engine_lists_are_stale) {
    FakeEngine e = inVats();
    e.m = Mode::None;
    const VatsView v = readView(e);
    CHECK(v.limbs.empty() && v.targetCount == 0 && v.highlighted == LimbId::None);
}

TEST(vats_limb, selecting_a_limb_writes_the_engines_highlight_and_reads_it_back) {
    FakeEngine e = inVats();
    ActionResult r = selectLimb(e, LimbId::Head);
    CHECK(r.ok);
    CHECK(r.limb == LimbId::Head && r.targetRef == kRaider);
    CHECK(e.commits == 1);
    CHECK(readView(e).highlighted == LimbId::Head);
    for (LimbId l : {LimbId::LeftArm, LimbId::RightArm, LimbId::LeftLeg, LimbId::RightLeg, LimbId::Torso}) {
        r = selectLimb(e, l);
        CHECK_MSG(r.ok && r.limb == l, toString(l));
    }
}

TEST(vats_limb, selecting_the_highlighted_limb_again_touches_nothing) {
    FakeEngine e = inVats();
    CHECK(selectLimb(e, LimbId::Torso).ok);
    CHECK(e.commits == 0);
}

TEST(vats_limb, a_limb_the_target_does_not_have_is_refused_with_the_list_of_what_it_has) {
    FakeEngine e = inVats();
    e.select(kGhoul);                                  // this target has no head part
    const ActionResult r = selectLimb(e, LimbId::Head);
    CHECK(!r.ok);
    CHECK(r.reason.find("no selectable head") != std::string::npos);
    CHECK(r.reason.find("torso") != std::string::npos);
    CHECK(e.commits == 0);
    CHECK(readView(e).highlighted == LimbId::Torso);   // nothing moved
}

TEST(vats_limb, it_is_refused_outside_selection_without_a_target_and_for_nonsense) {
    FakeEngine e = inVats(Mode::Playback);
    CHECK(!selectLimb(e, LimbId::Head).ok);            // playback
    CHECK(e.commits == 0);
    FakeEngine off;
    CHECK(!selectLimb(off, LimbId::Head).ok);          // not in VATS at all
    CHECK(off.commits == 0);
    FakeEngine none = inVats();
    none.selected = 0;
    CHECK(!selectLimb(none, LimbId::Head).ok);
    FakeEngine bad = inVats();
    CHECK(!selectLimb(bad, LimbId::None).ok);
    CHECK(!selectLimb(bad, static_cast<LimbId>(40)).ok);
    FakeEngine lost = inVats();
    lost.selected = 0xDEAD;                            // selected actor not in the target list
    CHECK(!selectLimb(lost, LimbId::Head).ok);
}

TEST(vats_limb, an_engine_that_refuses_or_reverts_the_write_is_reported_not_believed) {
    FakeEngine refuse = inVats();
    refuse.refuseCommit = true;
    ActionResult r = selectLimb(refuse, LimbId::Head);
    CHECK(!r.ok);
    CHECK(r.reason.find("refused") != std::string::npos);
    FakeEngine revert = inVats();
    revert.revertCommit = true;
    r = selectLimb(revert, LimbId::Head);
    CHECK(!r.ok);
    CHECK(r.reason.find("did not keep") != std::string::npos);
    CHECK(r.limb == LimbId::Torso);                    // the result says what the engine actually holds
}

TEST(vats_limb, selecting_a_target_cycles_the_engines_own_action_and_verifies_each_step) {
    FakeEngine e;
    e.enter({humanoid(1), humanoid(2), humanoid(3)}, 1);
    ActionResult r = selectTarget(e, 3);
    CHECK(r.ok && r.targetRef == 3);
    CHECK(e.cycles == 2);
    r = selectTarget(e, 3);
    CHECK(r.ok && e.cycles == 2);                      // already there: no cycling
    CHECK(!selectTarget(e, 99).ok);                    // not a target
    CHECK(!selectTarget(e, 0).ok);
    FakeEngine off;
    CHECK(!selectTarget(off, 3).ok);
}

TEST(vats_limb, a_target_change_that_does_not_move_the_selection_is_a_failure) {
    FakeEngine e;
    e.enter({humanoid(1), humanoid(2)}, 1);
    e.cycleNoMove = true;
    const ActionResult r = selectTarget(e, 2);
    CHECK(!r.ok);
    CHECK(r.reason.find("did not change") != std::string::npos);
    CHECK(e.cycles == 1);                              // it gave up at once instead of spinning
}

TEST(vats_limb, the_step_limit_ends_a_walk_that_never_arrives) {
    FakeEngine e;
    e.enter({humanoid(1), humanoid(2), humanoid(3), humanoid(4)}, 1);
    const ActionResult r = selectTarget(e, 4, 2);      // needs 3 steps
    CHECK(!r.ok);
    CHECK(e.cycles == 2);
}

TEST(vats_limb, fire_starts_the_playback_only_with_something_queued) {
    FakeEngine e = inVats();
    CHECK(!fire(e).ok);                                // nothing queued
    CHECK(e.accepts == 0);
    e.queueAction();
    CHECK(fire(e).ok);
    CHECK(e.accepts == 1 && e.m == Mode::Playback);
    CHECK(!fire(e).ok);                                // already playing
    FakeEngine refuse = inVats();
    refuse.queueAction();
    refuse.refuseAccept = true;
    CHECK(!fire(refuse).ok);
}

TEST(vats_limb, cancel_pops_every_queued_action) {
    FakeEngine e = inVats();
    e.queued = 3;
    CHECK(cancelAll(e).ok);
    CHECK(e.queued == 0 && e.unclicks == 3);
    FakeEngine stuck = inVats();
    stuck.queued = 2;
    stuck.refuseUnclick = true;
    CHECK(!cancelAll(stuck).ok);
}

TEST(vats_limb, the_json_view_carries_what_a_bot_needs) {
    FakeEngine e = inVats(Mode::LimbSelect);
    selectLimb(e, LimbId::RightArm);
    const std::string j = toJson(readView(e));
    CHECK(j.find("\"modeName\":\"limb_select\"") != std::string::npos);
    CHECK(j.find("\"limb\":\"right_arm\"") != std::string::npos);
    CHECK(j.find("\"limbAv\":28") != std::string::npos);
    CHECK(j.find("\"target\":4096") != std::string::npos);
    CHECK(j.find("\"limbs\":[{") != std::string::npos);
    CHECK(j.find("\"highlighted\":true") != std::string::npos);
}

}  // namespace
