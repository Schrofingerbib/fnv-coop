// plugin/intents: the two hook points plugin/companions plugs into (HostDeps::allowActivation / interacted, FULL-SYNC-PLAN 3.10 / 3.4). The hooks here are
// fakes (HostRig::allowActivation / ::interactions); the real ones (nvcoop::social::glueAllowActivation / glueGuestInteraction) are wired in
// engine/intents_module.cpp (and in coop_module.cpp's v1 executor) under NVCOOP_WITH_COMPANIONS.
// Runtime twins 327..329 (intent_host.cpp): 327 the question is never asked, 328 the host is never told, 329 it is told of refused interactions too.
#include "host_rig.h"
#include "intents/intent_service.h"
#include "minitest.h"

using namespace nvcoop::intents;
using namespace nvcoop::intents::testing;

namespace {

TEST(intents_social, a_refused_activation_is_skipped_and_nothing_of_it_runs) {
    Config c = testConfig();
    c.dialogue = true;
    HostRig h(c);
    h.add(0x500, TargetClass::Door);                           // stands for "somebody's companion" to the fake policy: the wheel / use belongs to slot 1
    h.add(0xA00, TargetClass::Npc);
    h.eng.positions[0x1001] = {0, 0, 0};
    h.eng.positions[0x1002] = {0, 0, 0};
    std::vector<std::pair<PlayerId, FormRef>> asked;
    h.allowActivation = [&](PlayerId slot, const FormRef& t) { asked.emplace_back(slot, t); return slot == 1; };

    const Verdict denied = h.run(2, Kind::Activate, R(0x500), 1);
    CHECK(denied.result == Result::Denied);
    CHECK(h.eng.calls.empty());                                // the engine was never asked to activate it
    CHECK(h.commits.empty());                                  // and no script effect was captured
    CHECK(h.host->stats().activationsRefused == 1);
    CHECK(h.host->stats().executed == 0 && h.host->stats().rejected[static_cast<size_t>(Result::Denied)] == 1);
    CHECK(h.interactions.empty());                             // a refused interaction is not a crime
    REQUIRE(h.sent.size() == 1);
    Verdict back;
    CHECK(decode(h.sent[0].second, nullptr, &back) == WireType::Verdict && h.sent[0].first == 2 && back.result == Result::Denied);   // the guest hears "no"

    CHECK(h.run(1, Kind::Activate, R(0x500), 1).result == Result::Executed);   // its owner may
    CHECK(h.eng.calls.size() == 1);

    // talking to it is the wheel
    CHECK(h.run(2, Kind::DialogueStart, R(0xA00), 2).result == Result::Denied);
    CHECK(h.dialogues.empty());
    CHECK(h.run(1, Kind::DialogueStart, R(0xA00), 2).result == Result::Executed);
    CHECK(h.dialogues.size() == 1 && h.dialogues[0].second == 1);

    // the story-channel stand-in path asks too
    CHECK(h.host->runLegacy(2, fnvcoop::IntentKind::ActivateRef, R(0x500)) == Result::Denied);
    CHECK(h.eng.calls.size() == 1);
    CHECK(h.host->runLegacy(1, fnvcoop::IntentKind::ActivateRef, R(0x500)) == Result::Executed);
    CHECK(h.eng.calls.size() == 2);
    CHECK(h.host->stats().activationsRefused == 3);

    // only activations are asked about: a take or a container is not a companion
    const size_t before = asked.size();
    h.item(0x700, 0xF, 5);
    h.add(0x501, TargetClass::Container);
    CHECK(h.run(2, Kind::PickUp, R(0x700), 3).result == Result::Executed);
    CHECK(h.run(2, Kind::ContainerOpen, R(0x501), 4).result == Result::Executed);
    CHECK(asked.size() == before);
}

TEST(intents_social, an_executed_activation_or_pickup_is_reported_with_the_kind_of_interaction) {
    HostRig h;
    h.add(0x500, TargetClass::Door);
    h.add(0x501, TargetClass::Container);
    h.item(0x700, 0xF, 25);
    h.item(0x701, 0xF, 3);
    h.eng.onCall = [&](const FakeEngine::Call& c) { if (c.what == "disable") h.hub.record(fnvcoop::DisableRef{c.target}); };

    CHECK(h.run(1, Kind::PickUp, R(0x700), 1).result == Result::Executed);
    REQUIRE(h.interactions.size() == 1);
    CHECK(std::get<0>(h.interactions[0]) == 1 && std::get<1>(h.interactions[0]) && std::get<2>(h.interactions[0]) == R(0x700));

    CHECK(h.run(1, Kind::Activate, R(0x500), 2).result == Result::Executed);   // a lever, a door
    REQUIRE(h.interactions.size() == 2);
    CHECK(!std::get<1>(h.interactions[1]) && std::get<2>(h.interactions[1]) == R(0x500));

    CHECK(h.run(1, Kind::ContainerOpen, R(0x501), 3).result == Result::Executed);   // a chest (locked or owned ones are what the judge looks at)
    REQUIRE(h.interactions.size() == 3);
    CHECK(!std::get<1>(h.interactions[2]) && std::get<2>(h.interactions[2]) == R(0x501));

    CHECK(h.host->runLegacy(2, fnvcoop::IntentKind::ActivateRef, R(0x701)) == Result::Executed);   // a legacy "activate" on a world item is a take
    REQUIRE(h.interactions.size() == 4);
    CHECK(std::get<0>(h.interactions[3]) == 2 && std::get<1>(h.interactions[3]) && std::get<2>(h.interactions[3]) == R(0x701));
    CHECK(h.host->stats().interactionsReported == 4);

    // what did not run is not reported
    CHECK(h.run(2, Kind::PickUp, R(0x700), 4).result == Result::Taken);        // somebody else got there first
    CHECK(h.run(1, Kind::Activate, R(0xDEAD), 5).result == Result::NotLoaded);
    CHECK(h.run(1, Kind::Hit, R(0x500), 6).result == Result::Disabled);
    CHECK(h.interactions.size() == 4);
}

TEST(intents_social, shared_storage_and_failed_runs_are_not_reported) {
    HostRig h;
    h.add(0x12AB34, TargetClass::Container);
    h.add(0x500, TargetClass::Door);
    ContainerRedirect r;
    std::string err;
    REQUIRE(r.load(R"({"redirects":[{"target":"falloutnv.esm:12AB34","slots":{"1":"mymod.esp:000801"}}]})", &err));
    h.build(r);
    CHECK(h.run(1, Kind::ContainerOpen, R(0x12AB34), 1).hasRedirect);          // the player's own storage: nobody's property is touched
    h.eng.activateOk = false;
    CHECK(h.run(1, Kind::Activate, R(0x500), 2).result == Result::Failed);     // the engine refused: nothing happened
    CHECK(h.interactions.empty());
}

}  // namespace
