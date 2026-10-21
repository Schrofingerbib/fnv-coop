// plugin/world_time tests (game-free). Groups: clock, wire, broker, objects, save, session (rig of 1..4 machines), storm.
// The FNV_MUT twins 101..166 of plugin/world_time must each fail this file (tools/mut/run_twins.py, plugin\world_time\run_tests.cmd).
#include <cstdio>
#include <cstring>

#include "minitest.h"
#include "wt_rig.h"
#include "wt_objects.h"
#include "wt_save.h"

using namespace nvwt;
using namespace wt_test;

namespace {

GameTime at(double hour, double days = 0.0, int month = 10, int day = 23) {
    GameTime t;
    t.year = 2281; t.month = month; t.day = day; t.hour = hour; t.daysPassed = days;
    return t;
}
FormRef mk(uint32_t id) { return FormRef("falloutnv.esm", id); }
bool approx(double a, double b, double eps = 0.01) { return std::fabs(a - b) <= eps; }

// ---------------------------------------------------------------------------------------------------------------------------- clock
TEST(clock, calendar_rolls_over_month_and_year) {
    GameTime t = at(23.0, 10.0, 10, 31);
    GameTime r = advanceBy(t, 120);   // 23:00 Oct 31 + 2 h
    CHECK(r.month == 11);
    CHECK(r.day == 1);
    CHECK(approx(r.hour, 1.0));
    CHECK(approx(r.daysPassed, 10.0 + 120.0 / 1440.0, 1e-9));
    GameTime y = advanceBy(at(23.5, 0, 12, 31), 60);
    CHECK(y.year == 2282 && y.month == 1 && y.day == 1);
    GameTime back = advanceBy(at(0.5, 0, 3, 1), -60);   // backwards over the month border
    CHECK(back.month == 2 && back.day == 28 && approx(back.hour, 23.5));
    GameTime same = advanceBy(at(9.0), 0);
    CHECK(same == at(9.0));
}

TEST(clock, drift_wraps_at_midnight_with_either_days_passed_meaning) {
    // continuous GameDaysPassed
    CHECK(approx(deltaMinutes(at(0.5, 11.0 + 0.5 / 24), at(23.5, 10.0 + 23.5 / 24)), 60.0, 1e-6));
    CHECK(approx(deltaMinutes(at(23.5, 10.0 + 23.5 / 24), at(0.5, 11.0 + 0.5 / 24)), -60.0, 1e-6));
    // whole-day GameDaysPassed (changes at midnight only)
    CHECK(approx(deltaMinutes(at(0.5, 11.0), at(23.5, 10.0)), 60.0, 1e-6));
    // days passed never updated at all
    CHECK(approx(deltaMinutes(at(0.5, 0.0), at(23.5, 0.0)), 60.0, 1e-6));
    // a whole day apart
    CHECK(approx(deltaMinutes(at(9.0, 12.0), at(9.0, 10.0)), 2880.0, 1e-6));
    CHECK(approx(deltaMinutes(at(9.0, 10.0), at(9.0, 10.0)), 0.0));
}

TEST(clock, authority_sends_first_then_periodic_beacons) {
    ClockAuthority a;
    auto b0 = a.tick(1000, at(9.0), 30);
    REQUIRE(b0.has_value());
    CHECK(b0->jump);   // the first beacon of a host process is a jump
    CHECK(b0->seq == 1);
    GameTime t = at(9.0);
    int sent = 1;
    for (uint64_t ms = 1100; ms <= 31000; ms += 100) {
        t = advanceBy(t, 0.1 * 30 / 60.0);
        if (auto b = a.tick(ms, t, 30)) { ++sent; CHECK(!b->jump); }
    }
    CHECK(sent >= 6 && sent <= 8);   // one per 5 s over 30 s
}

TEST(clock, authority_flags_leaps_and_loads_but_not_a_paused_clock) {
    ClockAuthority a;
    GameTime t = at(9.0);
    a.tick(1000, t, 30);
    t = advanceBy(t, 0.05);
    auto quiet = a.tick(1100, t, 30);
    CHECK(!quiet.has_value());
    // script: set GameHour to 14 (a 5 h forward leap)
    auto leap = a.tick(1200, at(14.0), 30);
    REQUIRE(leap.has_value());
    CHECK(leap->jump);
    // a host load going backwards
    auto back = a.tick(1300, at(7.0), 30);
    REQUIRE(back.has_value());
    CHECK(back->jump);
    // a paused clock (menu): no movement for 3 s is not a jump
    auto paused = a.tick(4300, at(7.0), 30);
    CHECK(!paused.has_value() || !paused->jump);
}

TEST(clock, beacon_rate_is_zero_while_the_host_clock_was_paused) {
    ClockAuthority a;
    GameTime t = at(9.0);
    a.tick(1000, t, 30);               // first beacon
    auto b = a.tick(6000, t, 30);      // 5 s later, clock did not move (host in a menu)
    REQUIRE(b.has_value());
    CHECK(!b->jump);
    CHECK(approx(b->rate, 0.0, 1e-6));
    GameTime t2 = advanceBy(t, 5.0 * 30 / 60.0);   // a running clock over the next 5 s
    auto c = a.tick(11000, t2, 30);
    REQUIRE(c.has_value());
    CHECK(approx(c->rate, 0.5, 0.01));
}

TEST(clock, noteSkip_bumps_epoch_and_forces_a_jump) {
    ClockAuthority a;
    a.tick(1000, at(9.0), 30);
    Beacon b = a.noteSkip(1500, at(17.0), 30);
    CHECK(b.jump);
    CHECK(b.skipEpoch == 1);
    CHECK(a.skipEpoch() == 1);
    CHECK(approx(b.time.hour, 17.0));
    a.noteLoaded();
    auto l = a.tick(1600, at(2.0), 30);
    REQUIRE(l.has_value());
    CHECK(l->jump);
}

TEST(clock, follower_leaves_small_drift_and_snaps_big_drift) {
    ClockFollower f;
    Beacon b;
    b.seq = 1; b.time = at(9.0); b.rate = 0.5f; b.timeScale = 30;
    f.onBeacon(b, 1000);
    // 1 s later the host is at 9:00 + 0.5 min; the client is 1 min ahead: within tolerance
    CHECK(!f.tick(2000, advanceBy(at(9.0), 1.5)).has_value());
    // 10 minutes ahead: snap to the extrapolated host time
    auto o = f.tick(2000, advanceBy(at(9.0), 10.5));
    REQUIRE(o.has_value());
    CHECK(approx(o->driftMinutes, 10.0, 0.01));
    CHECK(approx(deltaMinutes(o->to, at(9.0)), 0.5, 0.01));
    CHECK(!o->hard);
    f.noteApplied(2000);
    // a second soft snap inside the gap is held back
    CHECK(!f.tick(2500, advanceBy(at(9.0), 30)).has_value());
    CHECK(f.tick(3100, advanceBy(at(9.0), 30)).has_value());
}

TEST(clock, follower_hard_snaps_on_a_jump_beacon_even_inside_the_tolerance) {
    ClockFollower f;
    Beacon b;
    b.seq = 1; b.time = at(9.0); b.rate = 0.5f; b.timeScale = 30; b.jump = true;
    f.onBeacon(b, 1000);
    auto o = f.tick(1000, advanceBy(at(9.0), 1.2));   // 1.2 min off: inside the 2.5 min tolerance, but a jump says "trust me"
    REQUIRE(o.has_value());
    CHECK(o->hard);
    // consumed: the next tick does not order it again
    f.noteApplied(1000);
    CHECK(!f.tick(1100, at(9.0)).has_value());
}

TEST(clock, follower_ignores_an_old_reordered_beacon_and_a_dead_host) {
    ClockFollower f;
    Beacon b5; b5.seq = 5; b5.time = at(12.0); b5.rate = 0; b5.timeScale = 30;
    Beacon b4; b4.seq = 4; b4.time = at(8.0); b4.rate = 0; b4.timeScale = 30;
    f.onBeacon(b5, 1000);
    f.onBeacon(b4, 1010);
    CHECK(approx(f.beacon().time.hour, 12.0));
    // a restarted host counts from 1 again and says so with a jump
    Beacon r; r.seq = 1; r.time = at(6.0); r.rate = 0; r.timeScale = 30; r.jump = true;
    f.onBeacon(r, 1020);
    CHECK(approx(f.beacon().time.hour, 6.0));
    // 25 s without a beacon: no snaps from stale information
    CHECK(!f.tick(26020, at(20.0)).has_value());
}

TEST(clock, follower_does_not_run_ahead_of_a_paused_host) {
    ClockFollower f;
    Beacon b; b.seq = 1; b.time = at(9.0); b.rate = 0; b.timeScale = 30;
    f.onBeacon(b, 1000);
    GameTime e = f.expectedAt(11000);   // 10 s later, rate 0
    CHECK(approx(deltaMinutes(e, at(9.0)), 0.0, 1e-9));
}

TEST(clock, advance_time_is_applied_once_whichever_arrives_first) {
    // effect first, then the beacon that carries the epoch
    ClockFollower f;
    Beacon b; b.seq = 1; b.time = at(9.0); b.rate = 0.5f; b.timeScale = 30;
    f.onBeacon(b, 1000);
    f.onAdvanceTime(60, 1100);
    auto o = f.tick(1100, at(9.05));
    REQUIRE(o.has_value());
    CHECK(o->advance);
    CHECK(approx(deltaMinutes(o->to, at(9.05)), 60.0, 0.01));
    f.noteApplied(1100);
    Beacon b2; b2.seq = 2; b2.time = at(10.0); b2.rate = 0.5f; b2.timeScale = 30; b2.skipEpoch = 1; b2.jump = true;
    f.onBeacon(b2, 1200);
    auto o2 = f.tick(1200, at(10.05));
    CHECK(!o2.has_value() || !o2->advance);   // the local advance already happened

    // beacon first, then the effect: the effect must NOT advance again
    ClockFollower g;
    Beacon c; c.seq = 1; c.time = at(9.0); c.rate = 0.5f; c.timeScale = 30;
    g.onBeacon(c, 1000);
    Beacon c2; c2.seq = 2; c2.time = at(10.0); c2.rate = 0.5f; c2.timeScale = 30; c2.skipEpoch = 1; c2.jump = true;
    g.onBeacon(c2, 1100);
    g.onAdvanceTime(60, 1150);
    auto p = g.tick(1200, at(10.0));
    CHECK(!p.has_value() || !p->advance);
}

// ---------------------------------------------------------------------------------------------------------------------------- wire
Msg sampleOf(int i) {
    switch (i) {
        case 0: { Beacon b; b.seq = 77; b.time = at(13.25, 41.5, 11, 9); b.rate = 0.5f; b.timeScale = 30; b.skipEpoch = 3; b.jump = true; b.partyTravel = true; return b; }
        case 1: { SkipRequest s; s.reqId = 9; s.kind = ReqKind::Sleep; s.minutes = 480; return s; }
        case 2: { TravelRequest t; t.reqId = 4; t.marker = mk(0x1234); return t; }
        case 3: { Verdict v; v.reqId = 5; v.kind = ReqKind::Travel; v.granted = false; v.reason = Reason::Combat; v.minutes = 0; v.party = false; return v; }
        case 4: { TravelGo g; g.reqId = 6; g.marker = mk(0xABCDE); g.mover = 2; g.party = true; return g; }
        case 5: return Hello();
        default: { Status st; st.inCombat = true; return st; }
    }
}

TEST(wire, every_message_round_trips) {
    for (int i = 0; i < 7; ++i) {
        Msg in = sampleOf(i);
        Bytes b = encode(in);
        REQUIRE(b.size() > 7);
        CHECK(b[0] == kChannelWorldTime);
        Msg out;
        CHECK(decode(b, &out) == DecodeError::Ok);
        CHECK(in.index() == out.index());
        CHECK(in == out);
    }
}

TEST(wire, every_single_bit_flip_and_truncation_is_rejected) {
    for (int i = 0; i < 7; ++i) {
        Bytes b = encode(sampleOf(i));
        int accepted = 0;
        for (size_t byte = 0; byte < b.size(); ++byte)
            for (int bitn = 0; bitn < 8; ++bitn) {
                Bytes c = b;
                c[byte] = static_cast<uint8_t>(c[byte] ^ (1u << bitn));
                Msg out;
                if (decode(c, &out) == DecodeError::Ok) ++accepted;
            }
        CHECK(accepted == 0);
        int shortOk = 0;
        for (size_t n = 0; n < b.size(); ++n) {
            Msg out;
            if (decode(b.data(), n, &out) == DecodeError::Ok) ++shortOk;
        }
        CHECK(shortOk == 0);
    }
}

// Re-seal a message after editing its body so only the semantic checks are left to reject it.
Bytes reseal(Bytes b) {
    b.resize(b.size() - 4);
    uint32_t c = crc32Ieee(b.data(), b.size());
    for (int k = 0; k < 4; ++k) b.push_back(static_cast<uint8_t>(c >> (8 * k)));
    return b;
}

TEST(wire, version_trailing_bytes_and_ranges_are_checked) {
    Bytes b = encode(sampleOf(1));
    Bytes v = b; v[1] = static_cast<uint8_t>(kWireVersion + 1);
    Msg out;
    CHECK(decode(reseal(v), &out) == DecodeError::BadVersion);
    Bytes extra = b; extra.insert(extra.end() - 4, 0x55);   // one stray byte before the checksum
    CHECK(decode(reseal(extra), &out) == DecodeError::Malformed);
    Bytes magic = b; magic[0] = 0x7D;
    CHECK(decode(reseal(magic), &out) == DecodeError::BadMagic);
    Bytes type = b; type[2] = 99;
    CHECK(decode(reseal(type), &out) == DecodeError::BadType);

    Beacon bad; bad.seq = 1; bad.time = at(99.0); bad.timeScale = 30;
    CHECK(decode(encode(bad), &out) == DecodeError::Malformed);
    Beacon badMonth; badMonth.seq = 1; badMonth.time = at(5.0); badMonth.time.month = 40;
    CHECK(decode(encode(badMonth), &out) == DecodeError::Malformed);
    Beacon nan; nan.seq = 1; nan.time = at(5.0); nan.rate = std::nanf("");
    CHECK(decode(encode(nan), &out) == DecodeError::Malformed);

    SkipRequest zero; zero.reqId = 1; zero.kind = ReqKind::Wait; zero.minutes = 0;
    CHECK(decode(encode(zero), &out) == DecodeError::Malformed);
    SkipRequest huge; huge.reqId = 1; huge.kind = ReqKind::Wait; huge.minutes = 5000000;
    CHECK(decode(encode(huge), &out) == DecodeError::Malformed);
    TravelRequest noMarker; noMarker.reqId = 1;
    CHECK(decode(encode(noMarker), &out) == DecodeError::Malformed);
    TravelGo badMover; badMover.reqId = 1; badMover.marker = mk(5); badMover.mover = 9;
    CHECK(decode(encode(badMover), &out) == DecodeError::Malformed);
}

// ---------------------------------------------------------------------------------------------------------------------------- broker
struct BrokerRig {
    uint32_t joined = 0x7;   // host + two clients
    uint32_t fighting = 0;
    bool tether = false, locked = false;
    std::vector<uint32_t> prompts;
    std::vector<Resolved> done;
    uint32_t nextPrompt = 50;
    bool failAsk = false;
    uint32_t p0() const { return prompts.empty() ? 0u : prompts[0]; }   // 0 when nothing was asked: the test fails on its own check, not by crashing
    BrokerConfig cfg;
    std::unique_ptr<ConsentBroker> b;
    BrokerRig() { init(); }
    void init() {
        PartyView pv;
        pv.joined = [this]() { return joined; };
        pv.inCombat = [this]() { return fighting; };
        pv.tethered = [this]() { return tether; };
        pv.travelLocked = [this]() { return locked; };
        b = std::make_unique<ConsentBroker>(cfg, pv,
            [this](PlayerId, ReqKind, const std::string&, uint32_t) -> uint32_t {
                if (failAsk) return 0;
                prompts.push_back(nextPrompt);
                return nextPrompt++;
            },
            [this](const Resolved& r) { done.push_back(r); });
    }
};

TEST(broker, rest_is_asked_then_granted_when_everybody_agrees) {
    BrokerRig r;
    Outcome o = r.b->request(1, ReqKind::Sleep, 480, FormRef(), 1000);
    CHECK(o.kind == Outcome::Asked);
    CHECK(r.prompts.size() == 1);
    CHECK(r.done.empty());
    CHECK(r.b->onPromptResult(r.p0(), true, false, 2000));
    REQUIRE(r.done.size() == 1);
    CHECK(r.done[0].granted);
    CHECK(r.done[0].minutes == 480);
    CHECK(r.done[0].initiator == 1);
    CHECK(!r.b->busy());
}

TEST(broker, a_lone_player_is_never_asked) {
    BrokerRig r;
    r.joined = 0x1;
    Outcome o = r.b->request(0, ReqKind::Wait, 60, FormRef(), 1000);
    CHECK(o.kind == Outcome::Granted);
    CHECK(r.prompts.empty());
    CHECK(r.done.empty());   // synchronous answers never call the resolver
}

TEST(broker, a_no_denies_and_starts_a_cooldown) {
    BrokerRig r;
    r.b->request(1, ReqKind::Wait, 60, FormRef(), 1000);
    r.b->onPromptResult(r.p0(), false, false, 2000);
    REQUIRE(r.done.size() == 1);
    CHECK(!r.done[0].granted);
    CHECK(r.done[0].reason == Reason::Declined);
    Outcome again = r.b->request(2, ReqKind::Sleep, 60, FormRef(), 3000);   // Wait and Sleep share the cooldown
    CHECK(again.kind == Outcome::Denied);
    CHECK(again.reason == Reason::Cooldown);
    Outcome later = r.b->request(2, ReqKind::Sleep, 60, FormRef(), 2000 + 8001);
    CHECK(later.kind == Outcome::Asked);
}

TEST(broker, a_timeout_is_a_refusal_not_a_grant) {
    BrokerRig r;
    r.b->request(1, ReqKind::Wait, 60, FormRef(), 1000);
    r.b->onPromptResult(r.p0(), true, true, 50000);   // timedOut wins over any confirm flag
    REQUIRE(r.done.size() == 1);
    CHECK(!r.done[0].granted);
    CHECK(r.done[0].reason == Reason::Timeout);
}

TEST(broker, one_question_at_a_time) {
    BrokerRig r;
    CHECK(r.b->request(1, ReqKind::Wait, 60, FormRef(), 1000).kind == Outcome::Asked);
    Outcome second = r.b->request(2, ReqKind::Sleep, 120, FormRef(), 1100);
    CHECK(second.kind == Outcome::Denied);
    CHECK(second.reason == Reason::Busy);
    CHECK(r.prompts.size() == 1);
    // the first one still resolves normally
    r.b->onPromptResult(r.p0(), true, false, 1200);
    REQUIRE(r.done.size() == 1);
    CHECK(r.done[0].initiator == 1);
}

TEST(broker, rest_is_refused_while_anyone_fights_and_rechecked_at_grant_time) {
    BrokerRig r;
    r.fighting = 0x4;
    Outcome o = r.b->request(1, ReqKind::Wait, 60, FormRef(), 1000);
    CHECK(o.kind == Outcome::Denied);
    CHECK(o.reason == Reason::Combat);
    CHECK(r.prompts.empty());
    r.fighting = 0;
    CHECK(r.b->request(1, ReqKind::Wait, 60, FormRef(), 1100).kind == Outcome::Asked);
    r.fighting = 0x1;   // a fight breaks out while the party is answering
    r.b->onPromptResult(r.p0(), true, false, 1500);
    REQUIRE(r.done.size() == 1);
    CHECK(!r.done[0].granted);
    CHECK(r.done[0].reason == Reason::Combat);
}

TEST(broker, a_player_who_joined_after_the_question_was_asked_voids_the_consent) {
    BrokerRig r;
    r.b->request(1, ReqKind::Sleep, 480, FormRef(), 1000);
    r.joined = 0xF;   // slot 3 joined
    r.b->onPromptResult(r.p0(), true, false, 1500);
    REQUIRE(r.done.size() == 1);
    CHECK(!r.done[0].granted);
    CHECK(r.done[0].reason == Reason::PartyChanged);
}

TEST(broker, caps_and_validation) {
    BrokerRig r;
    CHECK(r.b->request(1, ReqKind::Wait, 12 * 60 + 1, FormRef(), 1000).reason == Reason::TooLong);
    CHECK(r.b->request(1, ReqKind::Wait, 0, FormRef(), 1000).reason == Reason::Invalid);
    r.joined = 0x1;
    CHECK(r.b->request(1, ReqKind::Wait, 60, FormRef(), 1000).reason == Reason::NotJoined);   // slot 1 is not in the party
    BrokerRig f;
    f.failAsk = true;
    CHECK(f.b->request(1, ReqKind::Wait, 60, FormRef(), 1000).reason == Reason::Invalid);   // a prompt that cannot be raised is a refusal
}

TEST(broker, travel_is_free_by_default_party_mode_and_tether_make_it_a_vote) {
    BrokerRig r;
    Outcome free = r.b->request(1, ReqKind::Travel, 0, mk(0x77), 1000);
    CHECK(free.kind == Outcome::Granted);
    CHECK(!free.party);
    r.tether = true;
    Outcome tethered = r.b->request(1, ReqKind::Travel, 0, mk(0x77), 1100);
    CHECK(tethered.kind == Outcome::Asked);
    CHECK(tethered.party);
    r.b->onPromptResult(r.p0(), true, false, 1200);
    REQUIRE(r.done.size() == 1);
    CHECK(r.done[0].granted);
    CHECK(r.done[0].party);
    CHECK(r.done[0].marker == mk(0x77));
    r.tether = false;
    BrokerRig p;
    p.cfg.travel = TravelMode::Party;
    p.init();
    CHECK(p.b->request(2, ReqKind::Travel, 0, mk(0x88), 1000).kind == Outcome::Asked);
}

TEST(broker, travel_refuses_combat_and_quest_locks) {
    BrokerRig r;
    r.locked = true;
    CHECK(r.b->request(1, ReqKind::Travel, 0, mk(1), 1000).reason == Reason::Locked);
    r.locked = false;
    r.fighting = 0x2;   // the traveller fights
    CHECK(r.b->request(1, ReqKind::Travel, 0, mk(1), 1000).reason == Reason::Combat);
    r.fighting = 0x4;   // somebody else fights: free travel is the traveller's own business ...
    CHECK(r.b->request(1, ReqKind::Travel, 0, mk(1), 1000).kind == Outcome::Granted);
    r.tether = true;    // ... but a party trip is not
    CHECK(r.b->request(1, ReqKind::Travel, 0, mk(1), 1000).reason == Reason::Combat);
}

TEST(broker, the_asker_leaving_cancels_and_a_late_answer_is_swallowed) {
    BrokerRig r;
    r.b->request(1, ReqKind::Wait, 60, FormRef(), 1000);
    r.b->onPlayerLeft(1, 1500);
    REQUIRE(r.done.size() == 1);
    CHECK(r.done[0].reason == Reason::Left);
    CHECK(!r.b->busy());
    CHECK(r.b->onPromptResult(r.p0(), true, false, 2000));   // ours, but already resolved: swallowed, no second resolve
    CHECK(r.done.size() == 1);
    CHECK(r.b->stats().lateAnswers == 1);
}

TEST(broker, a_prompt_layer_that_never_answers_times_out_and_its_late_answer_is_ignored) {
    BrokerRig r;
    r.b->request(1, ReqKind::Wait, 60, FormRef(), 1000);
    r.b->tick(1000 + 45000 + 4000);
    CHECK(r.done.empty());
    r.b->tick(1000 + 45000 + 5000);
    REQUIRE(r.done.size() == 1);
    CHECK(r.done[0].reason == Reason::Timeout);
    CHECK(r.b->onPromptResult(r.p0(), true, false, 60000));
    CHECK(r.done.size() == 1);
    CHECK(!r.b->onPromptResult(9999, true, false, 60000));   // a prompt that is not ours
}

// ---------------------------------------------------------------------------------------------------------------------------- objects
ObjObs door(uint32_t id, int openState, bool locked = false, int level = 0, bool destroyed = false) {
    ObjObs o;
    o.ref = mk(id); o.kind = ObjKind::Door; o.openState = openState; o.locked = locked; o.lockLevel = level; o.destroyed = destroyed;
    return o;
}
ObjObs chest(uint32_t id, bool locked, int level = 0, bool destroyed = false) {
    ObjObs o;
    o.ref = mk(id); o.kind = ObjKind::Container; o.locked = locked; o.lockLevel = level; o.destroyed = destroyed;
    return o;
}

ObjectWatcher::Config debounced() {
    ObjectWatcher::Config c;
    c.settleMs = 100;
    return c;
}

TEST(objects, host_first_sight_is_silent_and_a_native_door_open_is_sent_once) {
    ObjectWatcher w(Role::Host, debounced());
    CHECK(w.observeHost(door(1, 3), 1000).empty());       // loaded closed: baseline, not a change
    CHECK(w.observeHost(door(1, 3), 1200).empty());
    CHECK(w.observeHost(door(1, 2), 1300).empty());       // opening: transitional
    CHECK(w.observeHost(door(1, 2), 1700).empty());       // still opening 400 ms later: still nothing
    auto first = w.observeHost(door(1, 1), 1800);         // settled open: pending starts
    CHECK(first.empty());
    auto sent = w.observeHost(door(1, 1), 1950);          // stays open past the settle time
    REQUIRE(sent.size() == 1);
    CHECK(sent[0].mask == fnvcoop::kObjOpen);
    CHECK(sent[0].open);
    CHECK(sent[0].ref == mk(1));
    CHECK(w.observeHost(door(1, 1), 2100).empty());       // not again
    CHECK(w.observeHost(door(1, 4), 2200).empty());       // closing
    CHECK(w.observeHost(door(1, 3), 2300).empty());
    auto closed = w.observeHost(door(1, 3), 2500);
    REQUIRE(closed.size() == 1);
    CHECK(closed[0].mask == fnvcoop::kObjOpen);
    CHECK(!closed[0].open);
}

TEST(objects, with_no_settle_time_the_first_settled_sighting_is_sent) {
    ObjectWatcher w(Role::Host);
    w.observeHost(door(3, 3), 1000);
    CHECK(w.observeHost(door(3, 4), 1100).empty());
    CHECK(w.observeHost(door(3, 1), 1200).size() == 1);
}

TEST(objects, a_flapping_door_is_debounced_and_rate_limited) {
    ObjectWatcher w(Role::Host, debounced());
    w.observeHost(door(2, 3), 1000);
    int sent = 0;
    for (int i = 0; i < 40; ++i) sent += static_cast<int>(w.observeHost(door(2, i % 2 ? 1 : 3), 1100 + i * 30).size());   // flips every 30 ms
    CHECK(sent == 0);   // never stays put for the settle time
    // a stable change right after another change still respects the per-object gap
    w.observeHost(door(2, 1), 5000);
    auto a = w.observeHost(door(2, 1), 5150);
    REQUIRE(a.size() == 1);
    w.observeHost(door(2, 3), 5160);
    auto b = w.observeHost(door(2, 3), 5270);   // settled, but only 120 ms after the last send
    CHECK(b.empty());
    auto c = w.observeHost(door(2, 3), 5320);
    CHECK(c.size() == 1);
}

TEST(objects, lock_changes_carry_the_pre_image_and_destroyed_is_sticky) {
    ObjectWatcher w(Role::Host);
    w.observeHost(chest(10, true, 50), 1000);
    auto picked = w.observeHost(chest(10, false), 1200);
    REQUIRE(picked.size() == 1);
    CHECK(picked[0].mask == fnvcoop::kObjLock);
    CHECK(!picked[0].locked);
    CHECK(picked[0].hasBefore);
    CHECK(picked[0].beforeLocked);
    CHECK(picked[0].beforeLevel == 50);
    auto relocked = w.observeHost(chest(10, true, 75), 1500);
    REQUIRE(relocked.size() == 1);
    CHECK(relocked[0].locked);
    CHECK(relocked[0].level == 75);
    CHECK(relocked[0].beforeLevel == 0);
    auto boom = w.observeHost(chest(10, true, 75, true), 1800);
    REQUIRE(boom.size() == 1);
    CHECK(boom[0].mask == fnvcoop::kObjDestroyed);
    CHECK(boom[0].destroyed);
    CHECK(w.observeHost(chest(10, true, 75, false), 2100).empty());   // destroyed does not un-destroy
}

TEST(objects, a_script_captured_change_is_not_sent_a_second_time_by_the_poll) {
    ObjectWatcher w(Role::Host);
    w.observeHost(chest(11, false), 1000);
    fnvcoop::ObjectState s;
    s.ref = mk(11); s.mask = fnvcoop::kObjLock; s.locked = true; s.level = 30;   // L5 captured `Lock 30`
    w.onAuthoritative(s);
    CHECK(w.observeHost(chest(11, true, 30), 1200).empty());
    CHECK(w.stats().echoSuppressed == 1);
    // and a later native change is still seen
    auto next = w.observeHost(chest(11, false), 1500);
    CHECK(next.size() == 1);
}

TEST(objects, kinds_only_carry_the_fields_they_have) {
    CHECK((trackedMask(ObjKind::Door, 1) & fnvcoop::kObjOpen) != 0);
    CHECK((trackedMask(ObjKind::Door, 0) & fnvcoop::kObjOpen) == 0);   // no open state readable
    CHECK((trackedMask(ObjKind::Container, 1) & fnvcoop::kObjOpen) == 0);
    CHECK((trackedMask(ObjKind::Terminal, 0) & fnvcoop::kObjLock) != 0);
    CHECK((trackedMask(ObjKind::Terminal, 0) & fnvcoop::kObjDestroyed) == 0);
    CHECK((trackedMask(ObjKind::Activator, 1) & fnvcoop::kObjOpen) != 0);
    CHECK((trackedMask(ObjKind::Other, 0) & fnvcoop::kObjDestroyed) != 0);
}

TEST(objects, a_client_heals_only_what_the_host_said_and_only_after_the_grace_period) {
    ObjectWatcher w(Role::Client);
    fnvcoop::ObjectState told;
    told.ref = mk(20); told.mask = fnvcoop::kObjLock | fnvcoop::kObjOpen; told.locked = true; told.level = 25; told.open = false;
    w.onAuthoritative(told);
    // an object the host never mentioned is never touched
    CHECK(!w.observeGuest(chest(99, false), 1000).has_value());
    CHECK(!w.observeGuest(chest(99, false), 9000).has_value());
    // the client's chest is unlocked although the host locked it
    ObjObs c = door(20, 3, false, 0);
    CHECK(!w.observeGuest(c, 1000).has_value());              // grace period: the activate round trip may still be on its way
    CHECK(!w.observeGuest(c, 2000).has_value());
    auto heal = w.observeGuest(c, 2600);
    REQUIRE(heal.has_value());
    CHECK((heal->mask & fnvcoop::kObjLock) != 0);
    CHECK(heal->locked);
    CHECK(heal->level == 25);
    CHECK((heal->mask & fnvcoop::kObjOpen) == 0);             // the door is closed, as the host said
    // not retried every frame
    CHECK(!w.observeGuest(c, 2700).has_value());
    CHECK(!w.observeGuest(c, 3500).has_value());
    // once it agrees, the timer resets
    CHECK(!w.observeGuest(door(20, 3, true, 25), 3600).has_value());
    CHECK(w.stats().healed == 1);
}

TEST(objects, a_client_opens_what_the_host_opened_and_ignores_transitional_states) {
    ObjectWatcher w(Role::Client);
    fnvcoop::ObjectState told;
    told.ref = mk(21); told.mask = fnvcoop::kObjOpen; told.open = true;
    w.onAuthoritative(told);
    CHECK(!w.observeGuest(door(21, 4), 1000).has_value());
    CHECK(!w.observeGuest(door(21, 4), 9000).has_value());   // closing: never healed while in transition
    CHECK(!w.observeGuest(door(21, 3), 9100).has_value());
    auto heal = w.observeGuest(door(21, 3), 10700);
    REQUIRE(heal.has_value());
    CHECK(heal->mask == fnvcoop::kObjOpen);
    CHECK(heal->open);
}

TEST(objects, a_heal_that_does_not_take_is_not_retried_every_frame) {
    ObjectWatcher::Config c;
    c.healGraceMs = 0;
    c.healGapMs = 1500;
    ObjectWatcher w(Role::Client, c);
    fnvcoop::ObjectState told;
    told.ref = mk(22); told.mask = fnvcoop::kObjLock; told.locked = true; told.level = 10;
    w.onAuthoritative(told);
    ObjObs open = chest(22, false);
    w.observeGuest(open, 1000);   // grace 0: the first sighting may heal at once; what matters is the next second
    int healed = 0;
    for (int i = 0; i < 20; ++i) healed += w.observeGuest(open, 1100 + i * 50).has_value() ? 1 : 0;   // one second of frames
    CHECK(healed == 0);
    CHECK(w.observeGuest(open, 3000).has_value());
}

TEST(objects, authoritative_snapshot_lists_only_what_the_host_told) {
    ObjectWatcher w(Role::Client);
    fnvcoop::ObjectState a; a.ref = mk(30); a.mask = fnvcoop::kObjDestroyed; a.destroyed = true;
    fnvcoop::ObjectState b; b.ref = mk(31); b.mask = fnvcoop::kObjLock; b.locked = true; b.level = 5;
    w.onAuthoritative(a);
    w.onAuthoritative(b);
    auto snap = w.authoritative();
    CHECK(snap.size() == 2);
    ObjectWatcher h(Role::Host);
    h.observeHost(chest(32, false), 1000);   // first sight only: not authoritative
    CHECK(h.authoritative().empty());
}

// ---------------------------------------------------------------------------------------------------------------------------- save
TEST(save, clients_never_autosave_the_host_follows_save_suppress) {
    SaveContext off;   // no session
    CHECK(decideSave(SaveKind::Auto, off) == SaveVerdict::Allow);
    SaveContext host; host.inSession = true; host.role = Role::Host;
    CHECK(decideSave(SaveKind::Auto, host) == SaveVerdict::Allow);
    CHECK(decideSave(SaveKind::Manual, host) == SaveVerdict::Allow);
    SaveContext cl; cl.inSession = true; cl.role = Role::Client; cl.joined = true;
    CHECK(decideSave(SaveKind::Auto, cl) == SaveVerdict::DenyQuiet);
    CHECK(decideSave(SaveKind::Force, cl) == SaveVerdict::DenyQuiet);
    CHECK(decideSave(SaveKind::System, cl) == SaveVerdict::DenyQuiet);
    CHECK(decideSave(SaveKind::Manual, cl) == SaveVerdict::Allow);   // the player's own character save
    CHECK(decideSave(SaveKind::Quick, cl) == SaveVerdict::Allow);
    cl.syncing = true;
    CHECK(decideSave(SaveKind::Manual, cl) == SaveVerdict::DenyNotify);
    host.saveSuppressed = true;
    CHECK(decideSave(SaveKind::Auto, host) == SaveVerdict::DenyQuiet);
    CHECK(decideSave(SaveKind::Manual, host) == SaveVerdict::DenyNotify);
    CHECK(decideSave(SaveKind::Quick, host) == SaveVerdict::DenyNotify);
}

TEST(save, engine_autosave_switches_follow_the_role_and_the_story_flag) {
    SaveContext c;
    CHECK(!engineAutosavesOff(c));
    c.inSession = true; c.role = Role::Host;
    CHECK(!engineAutosavesOff(c));
    c.saveSuppressed = true;
    CHECK(engineAutosavesOff(c));
    c.saveSuppressed = false; c.role = Role::Client;
    CHECK(engineAutosavesOff(c));
}

TEST(save, the_guard_emits_ini_lines_only_on_a_change_and_restores_the_players_values) {
    SaveGuard g;
    g.setOriginal(true, true, false, true);   // the player had bSaveOnRest off
    SaveContext host; host.inSession = true;
    CHECK(g.update(host).empty());            // a host with nothing special: untouched
    SaveContext cl; cl.inSession = true; cl.role = Role::Client; cl.joined = true;
    auto off = g.update(cl);
    REQUIRE(off.size() == 4);
    for (auto& l : off) CHECK(l.find(" 0") == l.size() - 2);
    CHECK(off[0].find("SetINISetting \"bSaveOnTravel:GamePlay\"") == 0);
    CHECK(g.update(cl).empty());              // no repeat
    CHECK(g.autosavesOff());
    auto on = g.update(host);                 // left the session
    REQUIRE(on.size() == 4);
    CHECK(on[0].back() == '1');
    CHECK(on[2].find("bSaveOnRest") != std::string::npos);
    CHECK(on[2].back() == '0');               // the player's own setting comes back, not a blanket "on"
    g.update(cl);
    auto r = g.restore();
    CHECK(r.size() == 4);
    CHECK(g.restore().empty());
}

// ---------------------------------------------------------------------------------------------------------------------------- session
int countKind(const std::vector<fnvcoop::Effect>& v) {
    int n = 0;
    for (auto& e : v) if (std::holds_alternative<fnvcoop::AdvanceTime>(e)) ++n;
    return n;
}

TEST(session, clients_converge_on_the_host_clock_and_keep_following) {
    Rig r(3);
    r.at(1).eng.t = at(15.0, 5.0);   // a client that loaded an old save
    r.at(2).eng.t = advanceBy(startTime(), 30);
    r.run(3000);
    CHECK(std::fabs(r.driftToHost(1)) < 1.0);
    CHECK(std::fabs(r.driftToHost(2)) < 1.0);
    CHECK(r.at(1).s->stats().snaps >= 1);
    r.run(60000);
    CHECK(std::fabs(r.driftToHost(1)) < 3.0);
    CHECK(std::fabs(r.driftToHost(2)) < 3.0);
    CHECK(r.host().s->stats().beaconsSent >= 10);
}

TEST(session, a_host_script_jump_reaches_everybody_at_once) {
    Rig r(2);
    r.run(3000);
    r.host().eng.t = at(2.0, 200.0, 12, 24);   // set GameHour / GameDay by script
    r.run(400);
    CHECK(std::fabs(r.driftToHost(1)) < 1.0);
    CHECK(r.at(1).eng.t.month == 12);
}

TEST(session, a_paused_host_pulls_the_clients_back_instead_of_being_outrun) {
    Rig r(2);
    r.run(3000);
    r.host().eng.paused = true;   // the host sits in the Pip-Boy
    r.run(40000);
    CHECK(std::fabs(r.driftToHost(1)) < 4.0);   // the client never gets far ahead (rate 0 in the beacons)
}

TEST(session, a_client_hello_is_answered_with_a_beacon_even_before_any_player_event) {
    Rig r(2);
    r.run(500);
    CHECK(r.at(1).s->follower().haveBeacon());
}

TEST(session, wait_asked_by_a_client_is_granted_by_the_party_and_everybody_jumps_once) {
    Rig r(3);
    r.run(2000);
    const GameTime before = r.host().eng.t;
    CHECK(!r.at(1).s->onLocalRest(ReqKind::Wait, 120, r.now));   // vetoed: the host decides
    CHECK(r.at(1).s->awaitingAnswer());
    r.run(300);
    REQUIRE(r.prompts.size() == 1);
    CHECK(r.prompts[0].initiator == 1);
    CHECK(r.prompts[0].text.find("Wait for 2 hours") == 0);
    CHECK(r.answer(0, true));
    r.run(1500);
    CHECK(countKind(r.host().recorded) == 1);
    CHECK(!r.at(1).s->awaitingAnswer());
    for (size_t i = 0; i < 3; ++i) {
        CHECK(approx(deltaMinutes(r.at(i).eng.t, before), 120.0 + 3.5 * 30 / 60.0 * 1.0, 3.0));   // 120 min plus the ~3.5 s that passed
    }
    CHECK(std::fabs(r.driftToHost(1)) < 2.0);
    CHECK(std::fabs(r.driftToHost(2)) < 2.0);
    CHECK(r.host().s->stats().skipsExecuted == 1);
    CHECK(r.host().s->authority().skipEpoch() == 1);
    CHECK(r.at(1).s->follower().epochSeen() == 1);   // the skip beacon (not just the periodic one) reached the clients
    CHECK(r.at(2).s->follower().epochSeen() == 1);
    CHECK(r.at(1).eng.notes.size() >= 1);
    CHECK(r.at(1).eng.notes.back() == "Time passes.");
}

TEST(session, a_no_changes_nothing_and_the_asker_is_told) {
    Rig r(3);
    r.run(1000);
    const GameTime before = r.host().eng.t;
    r.at(2).s->onLocalRest(ReqKind::Sleep, 480, r.now);
    r.run(300);
    REQUIRE(r.prompts.size() == 1);
    CHECK(r.answer(0, false));
    r.run(600);
    CHECK(countKind(r.host().recorded) == 0);
    CHECK(deltaMinutes(r.host().eng.t, before) < 1.5);
    REQUIRE(!r.at(2).eng.notes.empty());
    CHECK(r.at(2).eng.notes.back() == reasonText(Reason::Declined));
    CHECK(!r.at(2).s->awaitingAnswer());
    // and an immediate retry is refused by the cooldown, with an answer
    r.at(2).eng.notes.clear();
    r.at(2).s->onLocalRest(ReqKind::Sleep, 480, r.now);
    r.run(500);
    REQUIRE(!r.at(2).eng.notes.empty());
    CHECK(r.at(2).eng.notes.back() == reasonText(Reason::Cooldown));
    CHECK(r.prompts.size() == 1);
}

TEST(session, rest_during_a_fight_is_refused_for_the_host_and_for_clients) {
    Rig r(2);
    r.run(500);
    r.combat = 0x2;
    CHECK(!r.host().s->onLocalRest(ReqKind::Wait, 60, r.now));
    REQUIRE(!r.host().eng.notes.empty());
    CHECK(r.host().eng.notes.back() == reasonText(Reason::Combat));
    r.at(1).s->onLocalRest(ReqKind::Wait, 60, r.now);
    r.run(500);
    REQUIRE(!r.at(1).eng.notes.empty());
    CHECK(r.at(1).eng.notes.back() == reasonText(Reason::Combat));
    CHECK(r.prompts.empty());
}

TEST(session, the_host_asking_to_sleep_asks_the_party_and_clients_follow) {
    Rig r(2);
    r.run(500);
    CHECK(!r.host().s->onLocalRest(ReqKind::Sleep, 480, r.now));
    REQUIRE(r.prompts.size() == 1);
    CHECK(r.prompts[0].initiator == 0);
    const GameTime before = r.host().eng.t;
    r.answer(0, true);
    r.run(1500);
    CHECK(deltaMinutes(r.host().eng.t, before) > 479.0);
    CHECK(std::fabs(r.driftToHost(1)) < 2.0);
    CHECK(r.host().eng.notes.size() == 1);
    CHECK(r.host().eng.notes[0] == "Time passes.");
}

TEST(session, a_lone_host_just_sleeps_natively) {
    Rig r(1);
    r.run(200);
    CHECK(r.host().s->onLocalRest(ReqKind::Sleep, 480, r.now));
    CHECK(r.prompts.empty());
}

TEST(session, a_duplicated_request_is_asked_about_once_and_a_late_duplicate_is_silent) {
    NetOpts o; o.dupPct = 100;
    Rig r(2, TravelMode::Free, o);
    r.run(500);
    r.at(1).s->onLocalRest(ReqKind::Wait, 60, r.now);
    r.run(400);
    CHECK(r.prompts.size() == 1);
    CHECK(r.host().s->stats().duplicateRequests >= 1);
    r.answer(0, true);
    r.run(1500);
    CHECK(countKind(r.host().recorded) == 1);
    // no stray "Busy" message from the duplicate after the grant
    for (auto& n : r.at(1).eng.notes) CHECK(n != reasonText(Reason::Busy));
}

TEST(session, a_verdict_for_an_old_request_does_not_answer_a_new_one) {
    Rig r(2);
    r.run(500);
    r.at(1).s->onLocalRest(ReqKind::Wait, 60, r.now);
    r.run(300);
    r.answer(0, false);
    r.run(400);
    // client asks again after the cooldown; an old duplicate verdict (reqId 1) arrives while request 2 is open
    r.run(9000);
    r.at(1).s->onLocalRest(ReqKind::Wait, 60, r.now);
    CHECK(r.at(1).s->awaitingAnswer());
    Verdict old; old.reqId = 1; old.kind = ReqKind::Wait; old.granted = false; old.reason = Reason::Declined;
    r.at(1).s->onPacket(0, encode(old), r.now);
    CHECK(r.at(1).s->awaitingAnswer());
}

TEST(session, a_lost_answer_does_not_lock_the_client_out_forever) {
    SessionConfig cfg; cfg.requestTimeoutMs = 5000;
    Rig r(2, TravelMode::Free, NetOpts(), cfg);
    r.run(500);
    r.at(1).s->onLocalRest(ReqKind::Wait, 60, r.now);
    CHECK(r.at(1).s->awaitingAnswer());
    r.run(6000);   // nobody ever answers the prompt
    CHECK(!r.at(1).s->awaitingAnswer());
    CHECK(r.at(1).s->stats().requestsTimedOut == 1);
}

TEST(session, free_travel_needs_no_question_party_travel_moves_everybody) {
    Rig f(3);
    f.run(500);
    CHECK(f.at(1).s->onLocalTravel(mk(0x500), f.now));   // proceeds natively: no message, no prompt
    CHECK(f.prompts.empty());
    CHECK(f.host().s->onLocalTravel(mk(0x500), f.now));

    Rig p(3, TravelMode::Party);
    p.run(500);
    CHECK(p.at(1).s->partyTravel());
    CHECK(!p.at(1).s->onLocalTravel(mk(0x501), p.now));
    p.run(300);
    REQUIRE(p.prompts.size() == 1);
    CHECK(p.prompts[0].kind == ReqKind::Travel);
    p.answer(0, true);
    p.run(500);
    for (size_t i = 0; i < 3; ++i) {
        REQUIRE(p.at(i).eng.traveled.size() == 1);
        CHECK(p.at(i).eng.traveled[0] == mk(0x501));
    }
}

TEST(session, a_declined_party_trip_moves_nobody) {
    Rig p(3, TravelMode::Party);
    p.run(500);
    p.at(2).s->onLocalTravel(mk(0x502), p.now);
    p.run(300);
    p.answer(0, false);
    p.run(500);
    for (size_t i = 0; i < 3; ++i) CHECK(p.at(i).eng.traveled.empty());
    REQUIRE(!p.at(2).eng.notes.empty());
    CHECK(p.at(2).eng.notes.back() == reasonText(Reason::Declined));
}

TEST(session, a_story_tether_turns_free_travel_into_a_vote) {
    Rig r(2);
    r.run(500);
    r.tether = true;
    CHECK(!r.at(1).s->onLocalTravel(mk(0x503), r.now));
    r.run(300);
    REQUIRE(r.prompts.size() == 1);
    r.answer(0, true);
    r.run(500);
    CHECK(r.host().eng.traveled.size() == 1);
    CHECK(r.at(1).eng.traveled.size() == 1);
}

TEST(session, the_host_traveling_in_party_mode_takes_the_clients_too) {
    Rig p(2, TravelMode::Party);
    p.run(500);
    CHECK(!p.host().s->onLocalTravel(mk(0x504), p.now));
    REQUIRE(p.prompts.size() == 1);
    p.answer(0, true);
    p.run(500);
    CHECK(p.host().eng.traveled.size() == 1);
    CHECK(p.at(1).eng.traveled.size() == 1);
}

TEST(session, requests_from_the_wrong_side_or_from_strangers_are_ignored) {
    Rig r(2);
    r.run(500);
    SkipRequest s; s.reqId = 1; s.kind = ReqKind::Wait; s.minutes = 60;
    r.host().s->onPacket(5, encode(s), r.now);     // slot 5 is not a joined client
    CHECK(r.prompts.empty());
    CHECK(r.host().s->stats().unexpected == 1);
    Beacon b; b.seq = 99; b.time = at(3.0); b.rate = 0; b.timeScale = 30; b.jump = true;
    r.at(1).s->onPacket(1, encode(b), r.now);      // a beacon claiming to come from another client
    CHECK(r.at(1).s->stats().unexpected == 1);
    r.at(1).s->onPacket(0, Bytes{1, 2, 3}, r.now);  // garbage
    CHECK(r.at(1).s->stats().badPackets == 1);
}

TEST(session, a_client_that_leaves_while_asking_does_not_leave_the_party_stuck) {
    Rig r(3);
    r.run(500);
    r.at(1).s->onLocalRest(ReqKind::Sleep, 480, r.now);
    r.run(300);
    REQUIRE(r.prompts.size() == 1);
    r.at(1).joined = false;
    r.host().s->onPlayerLeft(1, r.now);
    r.answer(0, true);   // the answer arrives after the asker is gone
    r.run(500);
    CHECK(countKind(r.host().recorded) == 0);
    // somebody else can ask right away (the cooldown only follows a "no")
    r.at(2).s->onLocalRest(ReqKind::Wait, 60, r.now);
    r.run(300);
    CHECK(r.prompts.size() == 2);
}

TEST(session, a_host_load_makes_the_clients_follow_the_loaded_clock) {
    Rig r(2);
    r.run(3000);
    r.host().eng.t = at(5.0, 2.0, 9, 3);
    r.host().s->onHostLoaded();
    r.run(500);
    CHECK(std::fabs(r.driftToHost(1)) < 1.0);
    CHECK(r.at(1).eng.t.month == 9);
}

TEST(session, a_client_in_a_fight_blocks_rest_and_the_report_is_refreshed_and_expires) {
    Rig r(3);
    bool fighting = true;
    r.at(2).s.reset();   // rebuild client 2 with a combat source
    {
        SessionEnv env;
        env.engine = &r.at(2).eng;
        env.send = [&r](PlayerId, Bytes d) { r.push(2, 0, std::move(d)); };
        env.localInCombat = [&fighting]() { return fighting; };
        r.at(2).s = std::make_unique<WorldTimeSession>(Role::Client, static_cast<PlayerId>(2), SessionConfig(), env);
    }
    r.run(2000);
    CHECK(r.host().s->reportedCombat() == 0x4);
    r.run(30000);   // a long fight: the periodic refresh keeps the report alive
    CHECK(r.host().s->reportedCombat() == 0x4);
    r.at(1).s->onLocalRest(ReqKind::Wait, 60, r.now);
    r.run(400);
    CHECK(r.prompts.empty());
    REQUIRE(!r.at(1).eng.notes.empty());
    CHECK(r.at(1).eng.notes.back() == reasonText(Reason::Combat));
    fighting = false;
    r.run(2000);
    CHECK(r.host().s->reportedCombat() == 0);
    // a client that vanished in combat does not block rest forever
    fighting = true;
    r.run(1500);
    CHECK(r.host().s->reportedCombat() == 0x4);
    r.at(2).joined = false;   // no more reports (the session object stays, nothing is delivered)
    r.run(6000);
    CHECK(r.host().s->reportedCombat() == 0);
}

TEST(session, joining_and_hello_each_force_a_beacon_on_their_own) {
    Rig r(2);
    r.run(1000);
    const uint32_t base = r.host().s->stats().beaconsSent;
    r.host().s->onPlayerJoined(1, r.now);
    r.host().s->frame(r.now + 10);
    CHECK(r.host().s->stats().beaconsSent == base + 1);
    r.host().s->onPacket(1, encode(Hello()), r.now + 20);
    r.host().s->frame(r.now + 30);
    CHECK(r.host().s->stats().beaconsSent == base + 2);
}

TEST(session, a_single_travellers_go_order_does_not_move_the_other_clients) {
    Rig r(3);
    r.run(500);
    TravelGo go; go.reqId = 1; go.marker = mk(0x600); go.mover = 2; go.party = false;
    r.at(1).s->onPacket(0, encode(go), r.now);
    r.at(2).s->onPacket(0, encode(go), r.now);
    CHECK(r.at(1).eng.traveled.empty());
    REQUIRE(r.at(2).eng.traveled.size() == 1);
    CHECK(r.at(2).eng.traveled[0] == mk(0x600));
    go.party = true;
    r.at(1).s->onPacket(0, encode(go), r.now);
    CHECK(r.at(1).eng.traveled.size() == 1);
}

TEST(session, a_joining_client_gets_the_clock_at_once) {
    Rig r(2);
    r.at(1).joined = false;   // not there yet
    r.run(8000);
    r.at(1).joined = true;
    r.at(1).eng.t = at(23.0, 99.0);
    r.host().s->onPlayerJoined(1, r.now);
    r.run(300);
    CHECK(std::fabs(r.driftToHost(1)) < 1.0);
}

// ---------------------------------------------------------------------------------------------------------------------------- storm
TEST(storm, lossy_duplicating_links_still_converge_and_every_request_resolves_once) {
    for (uint64_t seed = 1; seed <= 12; ++seed) {
        NetOpts o; o.lossPct = 15; o.dupPct = 15; o.jitterMs = 120; o.seed = seed;
        Rig r(4, TravelMode::Free, o);
        r.run(6000);
        int granted = 0, asked = 0;
        for (int round = 0; round < 6; ++round) {
            const size_t who = 1 + static_cast<size_t>((seed + static_cast<uint64_t>(round)) % 3);
            const size_t before = r.prompts.size();
            r.at(who).s->onLocalRest(round % 2 ? ReqKind::Sleep : ReqKind::Wait, 60, r.now);
            r.run(2500);
            if (r.prompts.size() > before) {
                ++asked;
                if ((seed + static_cast<uint64_t>(round)) % 3 != 0) { r.answer(before, true); ++granted; }
                else r.answer(before, false);
            }
            r.run(15000);   // past the cooldown
        }
        r.run(20000);
        CHECK(countKind(r.host().recorded) <= granted);
        CHECK(r.host().s->stats().skipsExecuted <= static_cast<uint32_t>(granted));
        for (size_t i = 1; i < 4; ++i) CHECK_MSG(std::fabs(r.driftToHost(i)) < 6.0, "seed " + std::to_string(seed) + " client " + std::to_string(i));
        CHECK(asked >= 1);
    }
}

}  // namespace
