// plugin/vats: VatsSync on a cluster of machines (host + clients, in-memory network, scripted engines).
#include "harness.h"

using namespace nvcoop::vats;
using namespace vtest;

namespace {

constexpr uint32_t kRaider = 0x1000, kGhoul = 0x2000, kPuppet = 0x3000;

Settings slow() {
    Settings s;
    s.partySlowMo = true;
    s.slowScale = 0.35f;
    s.slowMaxMs = 3000;
    s.slowCooldownMs = 10000;
    return s;
}

int countScale(const Node& n, float scale) {
    int c = 0;
    for (const auto& p : n.eng.scaleCalls) if (p.first == scale) ++c;
    return c;
}
float lastScale(const Node& n) { return n.eng.scaleCalls.empty() ? 1.0f : n.eng.scaleCalls.back().first; }

void enterVats(Node& n, uint32_t sel = kRaider, Mode m = Mode::TargetSelect) { n.eng.enter({humanoid(kRaider), humanoid(kGhoul), humanoid(kPuppet)}, sel, m); }

// ---------------------------------------------------------------------------------------------------------------- settings
TEST(vats_sync, the_hosts_settings_win_on_every_client) {
    Settings hs = slow();
    hs.slowScale = 0.4f;
    hs.autoFireDelayMs = 111;
    hs.killCam = false;
    Cluster c(3, hs);
    for (int i = 1; i < 3; ++i) {
        CHECK_MSG(c.at(i).sync->settings() == sanitized(hs), std::to_string(i));
        CHECK(c.at(i).sync->stats().configsApplied == 1);
    }
}

TEST(vats_sync, the_host_changes_the_settings_at_run_time_and_every_client_follows_at_once) {
    Cluster c(3, Settings{});
    CHECK(c.at(1).sync->settings().killCam);
    Settings s = c.host().sync->settings();
    s.killCam = false;
    s.partySlowMo = true;
    CHECK(c.host().sync->setSettings(s, c.now));
    c.pump();                                          // no tick needed: the host sent them at once
    for (int i = 0; i < 3; ++i) {
        CHECK_MSG(!c.at(i).sync->settings().killCam && c.at(i).sync->settings().partySlowMo, "machine " + std::to_string(i));
    }
    CHECK(!c.host().sync->setSettings(s, c.now));      // nothing changed: nothing sent
    c.advance(100);
    CHECK(c.at(1).eng.cameraShort);                    // and the kill-cam setting took effect on the client
}

TEST(vats_sync, a_client_cannot_change_the_settings_not_even_for_itself) {
    Cluster c(2, slow());
    Settings s = c.at(1).sync->settings();
    s.partySlowMo = false;
    CHECK(!c.at(1).sync->setSettings(s, c.now));
    CHECK(c.at(1).sync->settings().partySlowMo);
}

TEST(vats_sync, a_clients_config_message_cannot_change_the_hosts_settings) {
    Cluster c(2, slow());
    Envelope e;
    e.src = 1; e.dest = 0;
    MConfig m;
    m.s = slow();
    m.s.realtime = false;
    m.s.partySlowMo = false;
    e.body = m;
    const Bytes b = encodeEnvelope(e);
    c.host().sync->receive(1, b.data(), b.size(), c.now);
    CHECK(c.host().sync->settings().realtime);
    CHECK(c.host().sync->settings().partySlowMo);
    CHECK(c.host().sync->stats().rejected == 1);
}

TEST(vats_sync, the_host_repeats_its_settings_for_late_or_confused_clients) {
    Cluster c(2, slow());
    const size_t before = c.sentOfKind(Kind::Config);
    c.advance(11000, 250);
    CHECK(c.sentOfKind(Kind::Config) >= before + 2);
}

// ---------------------------------------------------------------------------------------------------------------- phases and relay
TEST(vats_sync, a_clients_vats_mode_reaches_the_host_and_the_other_clients) {
    Cluster c(3, slow());
    enterVats(c.at(1));
    c.advance(200);
    CHECK(c.host().sync->modeOf(1) == Mode::TargetSelect);
    CHECK(c.at(2).sync->modeOf(1) == Mode::TargetSelect);
    CHECK(c.at(1).sync->modeOf(1) == Mode::TargetSelect);
    c.at(1).eng.leave();
    c.advance(200);
    CHECK(c.host().sync->modeOf(1) == Mode::None);
    CHECK(c.at(2).sync->modeOf(1) == Mode::None);
}

TEST(vats_sync, the_host_announces_its_own_vats_too) {
    Cluster c(2, slow());
    enterVats(c.host());
    c.advance(200);
    CHECK(c.at(1).sync->modeOf(0) == Mode::TargetSelect);
}

TEST(vats_sync, the_mode_is_repeated_as_a_heartbeat_while_it_lasts) {
    Cluster c(2, Settings{});
    enterVats(c.at(1));
    c.advance(3500, 100);
    CHECK(c.at(1).sync->stats().phasesSent >= 3);
}

TEST(vats_sync, a_client_can_only_speak_for_itself) {
    Cluster c(3, slow());
    Envelope e;
    e.src = 1; e.dest = kNoPlayer;
    MPhase p;
    p.player = 2;                      // client 1 claims that player 2 entered VATS
    p.mode = Mode::TargetSelect;
    e.body = p;
    const Bytes b = encodeEnvelope(e);
    c.host().sync->receive(1, b.data(), b.size(), c.now);
    c.advance(200);
    CHECK(c.host().sync->modeOf(2) == Mode::None);
    CHECK(c.host().sync->stats().spoofed == 1);
    CHECK(countScale(c.host(), 0.35f) == 0);       // and it started no slow-mo window
}

TEST(vats_sync, the_author_of_a_client_message_is_the_transport_peer_not_the_claimed_src) {
    Cluster c(3, slow());
    Envelope e;
    e.src = 2; e.dest = kNoPlayer;     // arrives from slot 1 but claims to be slot 2
    MPhase p;
    p.player = 2;
    p.mode = Mode::TargetSelect;
    e.body = p;
    const Bytes b = encodeEnvelope(e);
    c.host().sync->receive(1, b.data(), b.size(), c.now);
    CHECK(c.host().sync->modeOf(2) == Mode::None);
    CHECK(c.host().sync->stats().spoofed >= 1);
}

TEST(vats_sync, relayed_messages_stop_at_the_hop_limit) {
    Cluster c(3, slow());
    Envelope e;
    e.src = 1; e.dest = kNoPlayer; e.hops = kMaxHops;
    MPhase p;
    p.player = 1;
    p.mode = Mode::LimbSelect;
    e.body = p;
    const Bytes b = encodeEnvelope(e);
    const size_t before = c.wire.log.size();
    c.host().sync->receive(1, b.data(), b.size(), c.now);
    CHECK(c.wire.log.size() == before);            // nothing was forwarded
    CHECK(c.host().sync->stats().hopLimit == 1);
}

TEST(vats_sync, a_clients_queued_action_is_seen_by_the_others_and_not_replayed_to_its_author) {
    Cluster c(3, Settings{});
    c.at(1).eng.netIds[kRaider] = 55;
    enterVats(c.at(1));
    c.advance(100);
    c.at(1).eng.queueAction();
    c.advance(100);                                // (auto fire waits 350 ms, so the action is still just queued)
    CHECK(c.at(1).sync->stats().actionsQueued == 1);
    CHECK(c.host().sync->stats().actionsReceived == 1);
    CHECK(c.at(2).sync->stats().actionsReceived == 1);
    CHECK(c.at(1).sync->stats().actionsReceived == 0);
}

TEST(vats_sync, garbage_on_the_channel_is_counted_and_ignored) {
    Cluster c(2, slow());
    const uint8_t junk[] = {0xB7, 0x01, 0xFF, 0xFF, 0xFF};
    c.host().sync->receive(1, junk, sizeof junk, c.now);
    c.at(1).sync->receive(0, junk, sizeof junk, c.now);
    CHECK(c.host().sync->stats().undecodable == 1);
    CHECK(c.at(1).sync->stats().undecodable == 1);
}

TEST(vats_sync, a_client_only_listens_to_the_host) {
    Cluster c(3, slow());
    Envelope e;
    e.src = 0; e.dest = 2;
    MSlowMo m;
    m.g.epoch = 7; m.g.seq = 99; m.g.active = true; m.g.scale = 0.2f; m.g.remainingMs = 3000;
    e.body = m;
    const Bytes b = encodeEnvelope(e);
    c.at(2).sync->receive(1, b.data(), b.size(), c.now);   // from another client: refused
    CHECK(!c.at(2).sync->partySlowActive());
    CHECK(c.at(2).sync->stats().rejected == 1);
    c.at(2).sync->receive(0, b.data(), b.size(), c.now);   // from the host: applied
    CHECK(c.at(2).sync->partySlowActive());
    Cluster h(2, slow());
    h.host().sync->receive(1, b.data(), b.size(), h.now);  // a client cannot grant slow-mo to the host
    CHECK(!h.host().sync->partySlowActive());
}

// ---------------------------------------------------------------------------------------------------------------- party slow-mo
TEST(vats_sync, party_slow_mo_slows_every_machine_together_then_restores_the_scale) {
    Cluster c(3, slow());
    enterVats(c.at(1));
    c.advance(400);
    for (int i = 0; i < 3; ++i) {
        CHECK_MSG(countScale(c.at(i), 0.35f) == 1, "machine " + std::to_string(i));
        CHECK_MSG(c.at(i).sync->partySlowActive() && c.at(i).sync->partyScale() == 0.35f, "machine " + std::to_string(i));
        CHECK_MSG(c.at(i).sync->policyInput().partySlowActive, "machine " + std::to_string(i));
    }
    c.advance(3500);                               // the window is capped at 3 s although the player is still in VATS
    for (int i = 0; i < 3; ++i) {
        CHECK_MSG(lastScale(c.at(i)) == 1.0f, "machine " + std::to_string(i));
        CHECK_MSG(!c.at(i).sync->partySlowActive(), "machine " + std::to_string(i));
        CHECK_MSG(countScale(c.at(i), 0.35f) == 1, "set once, not on every frame");
    }
}

TEST(vats_sync, the_window_ends_with_the_last_vats_user_and_a_cooldown_blocks_the_next) {
    Cluster c(2, slow());
    enterVats(c.at(1));
    c.advance(400);
    CHECK(countScale(c.host(), 0.35f) == 1);
    c.at(1).eng.leave();
    c.advance(400);
    CHECK(lastScale(c.host()) == 1.0f && lastScale(c.at(1)) == 1.0f);
    enterVats(c.at(1));                             // enters again at once: the cooldown is on
    c.advance(600);
    CHECK(countScale(c.host(), 0.35f) == 1);        // still just the first window
    CHECK(c.host().sync->slowMoStats().blockedByCooldown >= 1);
    c.at(1).eng.leave();
    c.advance(11000, 250);
    enterVats(c.at(1));
    c.advance(600);
    CHECK(countScale(c.host(), 0.35f) == 2);        // the cooldown has passed
}

TEST(vats_sync, with_party_slow_mo_off_nobodys_time_changes) {
    Cluster c(3, Settings{});
    enterVats(c.at(1));
    c.advance(1000);
    for (int i = 0; i < 3; ++i) CHECK_MSG(c.at(i).eng.scaleCalls.empty(), "machine " + std::to_string(i));
}

TEST(vats_sync, the_host_vats_entry_also_starts_a_window) {
    Cluster c(2, slow());
    enterVats(c.host());
    c.advance(400);
    CHECK(countScale(c.host(), 0.35f) == 1 && countScale(c.at(1), 0.35f) == 1);
}

TEST(vats_sync, a_client_who_loses_the_host_goes_back_to_normal_time_and_its_own_settings) {
    Cluster c(2, slow());
    enterVats(c.host());
    c.advance(400);
    REQUIRE(c.at(1).sync->partySlowActive());
    CHECK(c.at(1).sync->settings().partySlowMo);
    c.at(1).sync->hostLost();
    CHECK(!c.at(1).sync->partySlowActive());
    CHECK(lastScale(c.at(1)) == 1.0f);
    CHECK(!c.at(1).sync->settings().partySlowMo);   // the client's own (default) settings again
}

TEST(vats_sync, a_silent_host_cannot_leave_the_party_slowed) {
    Cluster c(2, slow());
    enterVats(c.host());
    c.advance(400);
    REQUIRE(c.at(1).sync->partySlowActive());
    c.wire.drop = true;                              // the host stops being heard
    c.advance(5000);
    CHECK(!c.at(1).sync->partySlowActive());
    CHECK(lastScale(c.at(1)) == 1.0f);
}

TEST(vats_sync, shutdown_puts_the_time_scale_back) {
    Cluster c(2, slow());
    enterVats(c.host());
    c.advance(400);
    REQUIRE(lastScale(c.at(1)) == 0.35f);
    c.at(1).sync->shutdown();
    CHECK(lastScale(c.at(1)) == 1.0f);
    CHECK(c.at(1).eng.scaleCalls.back().second);     // at once, not ramped
}

TEST(vats_sync, a_client_joining_during_a_window_is_slowed_at_once) {
    Cluster c(2, slow());
    enterVats(c.host());
    c.advance(400);
    auto nd = std::make_unique<Node>();
    nd->net.wire = &c.wire; nd->net.me = 2;
    Config cfg; cfg.self = 2; cfg.isHost = false;
    nd->sync.reset(new VatsSync(cfg, nd->eng, nd->net));
    c.nodes.push_back(std::move(nd));
    c.host().net.clients.push_back(2);
    c.host().sync->playerJoined(2, c.now);
    c.pump();
    c.advance(100);
    CHECK(c.at(2).sync->partySlowActive());
    CHECK(c.at(2).sync->settings().partySlowMo);
}

TEST(vats_sync, a_player_who_leaves_inside_vats_ends_the_window) {
    Cluster c(3, slow());
    enterVats(c.at(1));
    c.advance(400);
    REQUIRE(c.host().sync->partySlowActive());
    c.host().net.clients.erase(std::remove(c.host().net.clients.begin(), c.host().net.clients.end(), PlayerId{1}), c.host().net.clients.end());
    c.host().sync->playerLeft(1, c.now);
    c.advance(200);
    CHECK(!c.host().sync->partySlowActive());
    CHECK(lastScale(c.at(2)) == 1.0f);
}

TEST(vats_sync, something_else_changing_the_scale_during_a_window_is_corrected) {
    Cluster c(2, slow());
    enterVats(c.host());
    c.advance(400);
    REQUIRE(c.at(1).sync->partySlowActive());
    c.at(1).eng.scaleTgt = 1.0f;                     // another system reset the multiplier
    c.advance(600);
    CHECK(c.at(1).sync->stats().scaleReasserts >= 1);
    CHECK(c.at(1).eng.scaleTgt == 0.35f);
}

TEST(vats_sync, a_refused_scale_change_is_counted_and_retried) {
    Cluster c(1, slow());
    c.host().eng.refuseSetScale = true;
    enterVats(c.host());
    c.advance(300);
    CHECK(c.host().sync->stats().scaleFailed >= 1);
    c.host().eng.refuseSetScale = false;
    c.advance(100);
    CHECK(lastScale(c.host()) == 0.35f);
}

// ---------------------------------------------------------------------------------------------------------------- real time: auto fire, guard, kill cam
TEST(vats_sync, a_queued_action_starts_its_playback_after_the_delay_exactly_once) {
    Settings s;
    s.autoFireDelayMs = 350;
    Cluster c(1, s);
    enterVats(c.host());
    c.advance(100);
    c.host().eng.queueAction();
    c.advance(150);
    CHECK(c.host().eng.accepts == 0);                // 150 ms since the action: the player may still queue another
    c.advance(300);
    CHECK(c.host().eng.accepts == 1);
    CHECK(c.host().eng.m == Mode::Playback);
    c.advance(1000);
    CHECK(c.host().eng.accepts == 1);
    CHECK(c.host().sync->stats().autoFires == 1);
}

TEST(vats_sync, a_second_action_inside_the_delay_postpones_the_shot) {
    Settings s;
    s.autoFireDelayMs = 350;
    Cluster c(1, s);
    enterVats(c.host());
    c.advance(100);
    c.host().eng.queueAction();
    c.advance(250);
    c.host().eng.queueAction();
    c.advance(250);
    CHECK(c.host().eng.accepts == 0);
    c.advance(200);
    CHECK(c.host().eng.accepts == 1);
}

TEST(vats_sync, auto_fire_respects_its_switch_and_real_time) {
    Settings off;
    off.autoFire = false;
    Cluster a(1, off);
    enterVats(a.host());
    a.advance(100);
    a.host().eng.queueAction();
    a.advance(1000);
    CHECK(a.host().eng.accepts == 0);
    Settings vanilla;
    vanilla.realtime = false;
    Cluster b(1, vanilla);
    enterVats(b.host());
    b.advance(100);
    b.host().eng.queueAction();
    b.advance(1000);
    CHECK(b.host().eng.accepts == 0);
}

TEST(vats_sync, auto_fire_does_not_touch_the_playback_or_an_empty_queue) {
    Settings s;
    s.autoFireDelayMs = 0;
    Cluster c(1, s);
    enterVats(c.host());
    c.advance(500);
    CHECK(c.host().eng.accepts == 0);                // nothing queued
    c.host().eng.m = Mode::Playback;
    c.host().eng.queued = 2;
    c.advance(500);
    CHECK(c.host().eng.accepts == 0);                // already playing
}

TEST(vats_sync, a_refused_accept_is_retried_after_another_delay) {
    Settings s;
    s.autoFireDelayMs = 200;
    Cluster c(1, s);
    enterVats(c.host());
    c.advance(100);
    c.host().eng.queueAction();
    c.host().eng.refuseAccept = true;
    c.advance(300);
    CHECK(c.host().sync->stats().autoFireRefused >= 1);
    CHECK(c.host().eng.accepts <= 3);                // not a call on every frame
    c.host().eng.refuseAccept = false;
    c.advance(400);
    CHECK(c.host().sync->stats().autoFires == 1);
}

TEST(vats_sync, the_accept_is_sent_once_even_if_the_engine_does_not_leave_selection) {
    Settings s;
    s.autoFireDelayMs = 0;
    Cluster c(1, s);
    c.host().eng.acceptStartsPlayback = false;       // the engine accepted but is still in selection (e.g. a second queue slot)
    enterVats(c.host());
    c.advance(100);
    c.host().eng.queueAction();
    c.advance(1000);
    CHECK(c.host().eng.accepts == 1);
}

TEST(vats_sync, a_grant_is_not_applied_where_real_time_is_off) {
    Settings hs = slow();
    hs.realtime = false;                              // vanilla VATS: the engine's own time handling stays
    Cluster c(2, hs);
    Envelope e;
    e.src = 0; e.dest = 1;
    MSlowMo m;
    m.g.epoch = 7; m.g.seq = 5; m.g.active = true; m.g.scale = 0.35f; m.g.remainingMs = 2000;
    e.body = m;
    const Bytes b = encodeEnvelope(e);
    c.at(1).sync->receive(0, b.data(), b.size(), c.now);
    c.advance(300);
    CHECK(c.at(1).eng.scaleCalls.empty());
}

TEST(vats_sync, a_party_member_is_skipped_as_a_vats_target) {
    Cluster c(1, Settings{});
    c.host().eng.party.insert(kRaider);
    enterVats(c.host(), kRaider);
    c.advance(100);
    CHECK(c.host().eng.selected != kRaider);         // cycled on to the next target
    CHECK(c.host().sync->stats().guardSkips == 1);
}

TEST(vats_sync, a_party_member_as_the_only_target_is_not_cycled_every_frame) {
    Cluster c(1, Settings{});
    c.host().eng.party.insert(kRaider);
    c.host().eng.enter({humanoid(kRaider)}, kRaider);
    c.advance(1000, 10);
    CHECK(c.host().sync->stats().guardBlocked >= 1);
    CHECK(c.host().eng.cycles <= 5);                 // ~250 ms cooldown over 1 s
}

TEST(vats_sync, an_action_queued_at_a_party_member_is_taken_back_and_never_fired) {
    Settings s;
    s.autoFireDelayMs = 0;
    Cluster c(1, s);
    c.host().eng.enter({humanoid(kRaider)}, kRaider);
    c.advance(100);
    c.host().eng.party.insert(kRaider);              // (the selected target turns out to be a party member)
    c.host().eng.queueAction();
    c.advance(500);
    CHECK(c.host().eng.accepts == 0);
    CHECK(c.host().eng.queued == 0 && c.host().sync->stats().guardCancelled == 1);
}

TEST(vats_sync, the_guard_respects_its_switch) {
    Settings s;
    s.guardFriendly = false;
    Cluster c(1, s);
    c.host().eng.party.insert(kRaider);
    enterVats(c.host(), kRaider);
    c.advance(500);
    CHECK(c.host().eng.selected == kRaider && c.host().eng.cycles == 0);
}

TEST(vats_sync, the_kill_cam_setting_shortens_the_cuts_and_restores_them) {
    Settings s;
    s.killCam = false;
    Cluster c(1, s);
    c.advance(100);
    CHECK(c.host().eng.cameraShort);
    CHECK(c.host().eng.cameraCalls == 1);
    c.advance(500);
    CHECK(c.host().eng.cameraCalls == 1);            // once, not every frame
    c.host().sync->shutdown();
    CHECK(!c.host().eng.cameraShort);
    Cluster d(1, Settings{});
    d.advance(300);
    CHECK(d.host().eng.cameraCalls == 0);            // default: the engine's camera is left alone
}

// ---------------------------------------------------------------------------------------------------------------- perks
void killInVats(Cluster& c, uint32_t victim, bool decidedHere, bool fromNetwork) {
    Node& n = c.host();
    enterVats(n, victim);
    n.eng.select(victim);
    c.advance(100);
    n.eng.queueAction();
    c.advance(500);                                   // auto fire starts the playback
    REQUIRE(n.eng.m == Mode::Playback);
    n.sync->onActorDied(victim, decidedHere, fromNetwork, c.now);
}

TEST(vats_sync, grim_reapers_sprint_refunds_ap_for_a_kill_decided_on_another_machine) {
    Cluster c(1, Settings{});
    c.host().eng.perks.insert("GrimReapersSprint");
    c.host().eng.ap = 40;
    killInVats(c, kRaider, false, true);
    CHECK(c.host().eng.ap == 60);
    CHECK(c.host().sync->stats().grsRefunds == 1 && c.host().sync->stats().grsApGiven == 20);
    c.host().sync->onActorDied(kRaider, false, true, c.now);   // the same death reported again
    CHECK(c.host().eng.ap == 60);
}

TEST(vats_sync, no_perk_no_refund_and_native_kills_are_left_to_the_engine) {
    Cluster a(1, Settings{});
    a.host().eng.ap = 40;
    killInVats(a, kRaider, false, true);
    CHECK(a.host().eng.ap == 40);
    CHECK(a.host().sync->stats().grsNoPerk == 1);
    Cluster b(1, Settings{});
    b.host().eng.perks.insert("GrimReapersSprint");
    b.host().eng.ap = 40;
    killInVats(b, kRaider, true, false);
    CHECK(b.host().eng.ap == 40);
    CHECK(b.host().sync->stats().vatsKillsNative == 1);
}

TEST(vats_sync, the_perk_rework_can_be_switched_off_by_the_host) {
    Settings s;
    s.perks = false;
    Cluster c(1, s);
    c.host().eng.perks.insert("GrimReapersSprint");
    c.host().eng.ap = 40;
    killInVats(c, kRaider, false, true);
    CHECK(c.host().eng.ap == 40);
}

TEST(vats_sync, the_stranger_is_the_player_during_the_players_playback_only) {
    Cluster c(1, Settings{});
    const uint32_t stranger = 0xFF123456;
    CHECK(c.host().sync->attackerFor(stranger, c.now) == stranger);   // not in VATS
    enterVats(c.host());
    c.advance(100);
    c.host().eng.queueAction();
    c.advance(500);
    REQUIRE(c.host().eng.m == Mode::Playback);
    CHECK(c.host().sync->attackerFor(stranger, c.now) == 0x14);
    c.host().eng.netIds[stranger] = 9;                                // a synced NPC
    CHECK(c.host().sync->attackerFor(stranger, c.now) == stranger);
    c.host().eng.netIds.erase(stranger);
    c.host().eng.party.insert(stranger);
    CHECK(c.host().sync->attackerFor(stranger, c.now) == stranger);
}

TEST(vats_sync, policy_input_follows_the_engine_and_the_grant) {
    Cluster c(2, slow());
    PolicyInput in = c.at(1).sync->policyInput();
    CHECK(in.sessionActive && in.settings.realtime && in.mode == Mode::None && !in.vatsMenuOpen && !in.partySlowActive);
    enterVats(c.at(1), kRaider, Mode::LimbSelect);
    in = c.at(1).sync->policyInput();
    CHECK(in.mode == Mode::LimbSelect && in.vatsMenuOpen);
    CHECK(!menuModeForWorld(in, true));
    c.advance(400);
    in = c.at(1).sync->policyInput();
    CHECK(in.partySlowActive && in.partyScale == 0.35f);
    CHECK(scaleForVatsRequest(in, 0.0001f) == 0.35f);
}

TEST(vats_sync, status_json_is_valid_enough_for_the_proof_tool) {
    Cluster c(2, slow());
    enterVats(c.host());
    c.advance(400);
    const std::string j = c.host().sync->statusJson(c.now);
    CHECK(j.front() == '{' && j.back() == '}');
    CHECK(j.find("\"role\":\"host\"") != std::string::npos);
    CHECK(j.find("\"partySlow\":{\"active\":true") != std::string::npos);
    CHECK(j.find("\"autoFires\":") != std::string::npos);
    CHECK(j.find("\"grsRefunds\":") != std::string::npos);
    int depth = 0;
    for (char ch : j) { if (ch == '{') ++depth; if (ch == '}') --depth; CHECK(depth >= 0); }
    CHECK(depth == 0);
}

}  // namespace
