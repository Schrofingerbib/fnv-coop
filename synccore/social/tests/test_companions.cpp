// Companions: the book, caps, owner-only commands, the wheel, inventory, teammate flags, the party (twins 801..829).
#include "util.h"

using namespace social_test;

TEST(companions, a_companion_belongs_to_one_recruiter) {   // twins 803, 805
    CompanionBook b;
    CHECK(b.recruit(npc(1), 1, CompanionKind::Humanoid).ok);
    CHECK(b.ownerOf(npc(1)) == 1);
    // the same owner again: fine, nothing changes
    CompanionBook::Change same = b.applyEffect(recruitFx(npc(1), 1));
    CHECK(!same.changed);
    RecruitVerdict v = b.recruit(npc(1), 2, CompanionKind::Humanoid);   // another player cannot recruit it
    CHECK(!v.ok && v.reason == RecruitReason::OwnedByOther);
    CHECK(b.ownerOf(npc(1)) == 1);
    // a committed fact (the host judged it) does take it over, and tells who lost it
    CompanionBook::Change ch = b.applyEffect(recruitFx(npc(1), 2));
    CHECK(ch.changed && ch.from == 1 && ch.to == 2);
    CHECK(b.ownerOf(npc(1)) == 2);
    CHECK(b.find(npc(1))->state.rev == 0 && b.find(npc(1))->state.mode == FollowMode::Follow);   // a new recruiter starts fresh
}

TEST(companions, caps_per_owner_per_kind_and_for_the_party) {   // twins 801, 802, 827, 828
    CompanionBook b;   // 1 humanoid + 1 critter per owner, 4 for the party
    CHECK(b.recruit(npc(1), 1, CompanionKind::Humanoid).ok);
    RecruitVerdict v = b.recruit(npc(2), 1, CompanionKind::Humanoid);
    CHECK(!v.ok && v.reason == RecruitReason::OwnerCap);
    CHECK(b.recruit(npc(3), 1, CompanionKind::Critter).ok);                      // the critter slot is separate
    CHECK(!b.recruit(npc(4), 1, CompanionKind::Critter).ok);
    CHECK(b.recruit(npc(5), 2, CompanionKind::Humanoid).ok);
    CHECK(b.recruit(npc(6), 2, CompanionKind::Critter).ok);
    CHECK(b.total() == 4);
    RecruitVerdict p = b.recruit(npc(7), 3, CompanionKind::Humanoid);
    CHECK(!p.ok && p.reason == RecruitReason::PartyCap);
    CHECK(!b.recruit(npc(8), kNoPlayer, CompanionKind::Humanoid).ok);
    CHECK(!b.recruit(npc(8), 9, CompanionKind::Humanoid).ok);
    // overCap sees a breach that arrived as a committed fact (a save / rejoin): a second humanoid for player 1
    CompanionBook c;
    c.applyEffect(recruitFx(npc(1), 1, 0));
    c.applyEffect(recruitFx(npc(2), 1, 0));
    CHECK(c.overCap(1));
    CompanionBook d;
    d.applyEffect(recruitFx(npc(1), 1, 0));
    d.applyEffect(recruitFx(npc(2), 1, 1));
    CHECK(!d.overCap(1));
}

TEST(companions, the_critter_cap_is_its_own_number) {   // twin 827
    CompanionBook b(FollowerCaps{1, 2, 4});
    CHECK(b.recruit(npc(1), 1, CompanionKind::Critter).ok);
    CHECK(b.recruit(npc(2), 1, CompanionKind::Critter).ok);
    CHECK(!b.recruit(npc(3), 1, CompanionKind::Critter).ok);
    CHECK(b.recruit(npc(4), 1, CompanionKind::Humanoid).ok);
    CHECK(!b.recruit(npc(5), 1, CompanionKind::Humanoid).ok);
}

TEST(companions, a_dismiss_and_a_leaver_release_the_companions) {   // twins 804, 806, 807
    CompanionBook b;
    b.applyEffect(recruitFx(npc(1), 1, 0));
    b.applyEffect(recruitFx(npc(2), 1, 1));
    b.applyEffect(recruitFx(npc(3), 2, 0));
    CompanionBook::Change d = b.applyEffect(dismissFx(npc(3), 2));
    CHECK(d.changed && d.from == 2 && d.to == kNoPlayer);
    CHECK(!b.isCompanion(npc(3)));
    CHECK(!b.dismiss(npc(1), 2));                 // a stranger cannot dismiss it
    CHECK(b.isCompanion(npc(1)));
    std::vector<FormRef> gone = b.playerLeft(1);
    CHECK(gone.size() == 2);
    CHECK(b.total() == 0);
    b.applyEffect(recruitFx(npc(4), 2, 0));
    CHECK(b.dismiss(npc(4), 0));                  // the host may
    CHECK(!b.dismiss(npc(4), 0));
}

TEST(companions, state_is_revisioned_and_the_digest_sees_everything) {   // twins 808, 825
    CompanionBook a, b;
    a.applyEffect(recruitFx(npc(1), 1));
    b.applyEffect(recruitFx(npc(1), 1));
    CHECK(a.digest() == b.digest());
    CompanionState s;
    s.mode = FollowMode::Wait; s.rev = 2;
    CHECK(a.setState(npc(1), s));
    CHECK(a.digest() != b.digest());
    CompanionState older = s; older.mode = FollowMode::Follow; older.rev = 1;
    CHECK(!a.setState(npc(1), older));            // a late, older update must not win
    CHECK(a.find(npc(1))->state.mode == FollowMode::Wait);
    CHECK(!a.setState(npc(1), s));                // equal rev: nothing to do
    CHECK(!a.setState(npc(9), s));                // unknown NPC
    // the owner is part of the digest
    CompanionBook c, d;
    c.applyEffect(recruitFx(npc(1), 1));
    d.applyEffect(recruitFx(npc(1), 2));
    CHECK(c.digest() != d.digest());
}

TEST(companions, a_resync_replaces_the_table) {   // twin 826
    CompanionBook host, client;
    host.applyEffect(recruitFx(npc(1), 1));
    client.applyEffect(recruitFx(npc(1), 1));
    client.applyEffect(recruitFx(npc(2), 2));     // dismissed on the host while the client was away
    client.replaceRoster(host.roster());
    CHECK(client.total() == 1 && !client.isCompanion(npc(2)));
    CHECK(client.digest() == host.digest());
}

TEST(companions, commands_are_owner_only) {   // twins 809, 810, 811, 812, 813, 814
    CompanionBook b;
    b.applyEffect(recruitFx(npc(1), 1));
    CommandJudge j;
    CompanionCmd c;
    c.id = (1ull << 48) | 1; c.from = 1; c.npc = npc(1); c.kind = CmdKind::Wait;
    CmdVerdict v = j.judge(c, 1, 1000, b);
    CHECK(v.apply && v.stateChanged && v.newState.mode == FollowMode::Wait && v.newState.rev == 1);
    // the stranger
    CompanionCmd bad = c; bad.id = (2ull << 48) | 1; bad.from = 2;
    v = j.judge(bad, 2, 2000, b);
    CHECK(!v.apply && v.reason == CmdReason::NotOwner);
    // a forged author
    CompanionCmd forged = c; forged.id = (2ull << 48) | 2; forged.from = 1;
    v = j.judge(forged, 2, 3000, b);
    CHECK(!v.apply && v.reason == CmdReason::BadSender);
    // the same command twice
    v = j.judge(c, 1, 4000, b);
    CHECK(!v.apply && v.reason == CmdReason::Duplicate);
    // not a companion
    CompanionCmd nc = c; nc.id = 77; nc.npc = npc(50);
    v = j.judge(nc, 1, 5000, b);
    CHECK(!v.apply && v.reason == CmdReason::NotACompanion);
    // arguments
    CompanionCmd st = c; st.id = 78; st.kind = CmdKind::SetStance; st.arg = 9;
    v = j.judge(st, 1, 6000, b);
    CHECK(!v.apply && v.reason == CmdReason::BadArg);
    st.id = 79; st.arg = 2;
    v = j.judge(st, 1, 7000, b);
    CHECK(v.apply && v.newState.stance == Stance::Aggressive && v.stateChanged);
    // the flood
    CompanionCmd f1 = c; f1.id = 80; f1.kind = CmdKind::Follow;
    CompanionCmd f2 = c; f2.id = 81; f2.kind = CmdKind::Wait;
    CHECK(j.judge(f1, 1, 9000, b).apply);
    v = j.judge(f2, 1, 9010, b);
    CHECK(!v.apply && v.reason == CmdReason::TooFast);
    CHECK(j.judge(f2, 1, 9500, b).apply);   // a refused command did not spend its id
    // dismiss
    CompanionCmd dm = c; dm.id = 82; dm.kind = CmdKind::Dismiss;
    v = j.judge(dm, 1, 20000, b);
    CHECK(v.apply && v.dismiss && !v.stateChanged);
    // actions change no state
    CompanionCmd w = c; w.id = 83; w.kind = CmdKind::OpenWheel;
    v = j.judge(w, 1, 30000, b);
    CHECK(v.apply && !v.stateChanged);
    // a command that changes nothing keeps the revision
    CompanionBook b2;
    b2.applyEffect(recruitFx(npc(1), 1));
    CommandJudge j2;
    CompanionCmd fo = c; fo.id = 90; fo.kind = CmdKind::Follow;
    v = j2.judge(fo, 1, 1000, b2);
    CHECK(v.apply && !v.stateChanged && v.newState.rev == 0);
}

TEST(companions, the_wheel_opens_only_for_the_owner) {   // twin 815
    CompanionBook b;
    b.applyEffect(recruitFx(npc(1), 1));
    ActivationVerdict own = gateActivation(b, 1, npc(1), ForeignPolicy::Deny);
    CHECK(own.action == ActivateAction::OwnerOpens && !own.suppress());
    ActivationVerdict other = gateActivation(b, 2, npc(1), ForeignPolicy::Deny);
    CHECK(other.action == ActivateAction::DenyForeign && other.suppress() && other.owner == 1);
    CHECK(gateActivation(b, 2, npc(1), ForeignPolicy::Allow).action == ActivateAction::AllowForeign);
    CHECK(gateActivation(b, 2, npc(99), ForeignPolicy::Deny).action == ActivateAction::NotACompanion);
}

TEST(companions, inventory_sampling_makes_idempotent_deltas) {   // twins 819, 829
    CompanionBook b;
    b.applyEffect(recruitFx(npc(1), 1));
    CompanionInventory ownerSide, host;
    uint64_t counter = 0;
    ItemCounts t0 = {{item(1), 5}, {item(2), 1}};
    CHECK(ownerSide.sample(npc(1), t0, 1, counter).empty());   // the first sample seeds the baseline
    ItemCounts t1 = {{item(1), 3}, {item(3), 7}};              // 2 of item 1 given away, item 2 gone, item 3 added
    std::vector<InvDelta> d = ownerSide.sample(npc(1), t1, 1, counter);
    CHECK(d.size() == 3);
    CHECK(ownerSide.sample(npc(1), t1, 1, counter).empty());   // nothing changed since
    // the host first learns the full table, then the deltas
    uint64_t hc = 0;
    InvFull f0; f0.npc = npc(1); f0.items = {{item(1), 5}, {item(2), 1}};
    host.replace(f0, 1, hc);
    for (const InvDelta& x : d) {
        InvVerdict v = host.judge(x, 1, b);
        CHECK(v.apply);
        host.apply(x);
    }
    CHECK(host.count(npc(1), item(1)) == 3 && host.count(npc(1), item(2)) == 0 && host.count(npc(1), item(3)) == 7);
    // a mirror that missed everything gets the full table and derives exactly what to change
    CompanionInventory mirror;
    mirror.replace(f0, 1, hc);
    std::vector<InvDelta> fix = mirror.replace(host.fullOf(npc(1)), 1, hc);
    int changes = 0;
    for (const InvDelta& x : fix) { ++changes; (void)x; }
    CHECK(changes == 3);                                        // item 1 -2, item 2 -1, item 3 +7
    CHECK(mirror.digestOf(npc(1)) == host.digestOf(npc(1)));
    // an empty table clears the mirror
    InvFull empty; empty.npc = npc(1);
    CHECK(mirror.replace(empty, 1, hc).size() == 2);   // item 1 and item 3 go
    CHECK(mirror.count(npc(1), item(3)) == 0);
}

TEST(companions, only_the_owner_changes_the_companions_inventory_once_and_within_range) {   // twins 816, 817, 818
    CompanionBook b;
    b.applyEffect(recruitFx(npc(1), 1));
    CompanionInventory host;
    InvDelta add; add.txn = (1ull << 48) | 1; add.npc = npc(1); add.item = item(1); add.delta = 4;
    CHECK(host.judge(add, 1, b).apply);
    host.apply(add);
    InvVerdict dup = host.judge(add, 1, b);
    CHECK(!dup.apply && dup.reason == InvReason::Duplicate);
    InvDelta steal = add; steal.txn = (2ull << 48) | 1; steal.delta = -4;
    InvVerdict no = host.judge(steal, 2, b);
    CHECK(!no.apply && no.reason == InvReason::NotOwner);
    CHECK(host.count(npc(1), item(1)) == 4);
    InvDelta base = add; base.txn = (1ull << 48) | 6; base.item = item(9); base.delta = -1;   // a base item given away: a negative change is fine
    CHECK(host.judge(base, 1, b).apply);
    InvDelta over = add; over.txn = (1ull << 48) | 2; over.delta = kMaxItemCount + 1;
    InvVerdict neg = host.judge(over, 1, b);
    CHECK(!neg.apply && neg.reason == InvReason::OutOfRange && neg.resync);
    InvDelta zero = add; zero.txn = (1ull << 48) | 3; zero.delta = 0;
    CHECK(host.judge(zero, 1, b).reason == InvReason::ZeroDelta);
    InvDelta other = add; other.txn = (1ull << 48) | 4; other.npc = npc(77);
    CHECK(host.judge(other, 1, b).reason == InvReason::NotACompanion);
    InvDelta take = add; take.txn = (1ull << 48) | 5; take.delta = -4;
    CHECK(host.judge(take, 1, b).apply);
    host.apply(take);
    CHECK(host.count(npc(1), item(1)) == 0);
    CHECK(host.fullOf(npc(1)).items.empty());
}

TEST(companions, teammate_flags_follow_the_book) {   // twins 820, 821
    CompanionBook b;
    b.applyEffect(recruitFx(npc(1), 0));   // the host's own
    b.applyEffect(recruitFx(npc(2), 1));   // a guest's: on the HOST's machine the engine flagged it when the script ran
    std::set<FormRef> flagged = {npc(2), npc(9)};   // npc 9: a leftover nobody owns
    std::set<FormRef> loaded = {npc(1), npc(2)};
    TeammateFix fix = reconcileTeammates(b, 0, flagged, loaded);
    CHECK(fix.unset.size() == 2);
    CHECK(fix.set.size() == 1 && fix.set[0] == npc(1));
    // the guest's machine
    TeammateFix g = reconcileTeammates(b, 1, {npc(2)}, {npc(2)});
    CHECK(g.empty());
    TeammateFix g2 = reconcileTeammates(b, 1, {}, {npc(2)});
    CHECK(g2.set.size() == 1 && g2.set[0] == npc(2));
    // a companion that is not loaded here cannot be flagged
    TeammateFix g3 = reconcileTeammates(b, 1, {}, {});
    CHECK(g3.empty());
}

TEST(companions, the_party_does_not_fight_itself) {   // twins 822, 823, 824
    const Combatant p1 = Combatant::player(1), p2 = Combatant::player(2), c1 = Combatant::companion(1), c2 = Combatant::companion(2), foe = Combatant::other();
    CHECK(companionHitPolicy(p1, c1, false) == HitPolicy::Neutralize);   // your own companion
    CHECK(companionHitPolicy(p1, c2, false) == HitPolicy::Neutralize);   // somebody else's
    CHECK(companionHitPolicy(c1, p2, false) == HitPolicy::Neutralize);
    CHECK(companionHitPolicy(c1, c2, false) == HitPolicy::Neutralize);
    CHECK(companionHitPolicy(p1, p2, false) == HitPolicy::Neutralize);
    CHECK(companionHitPolicy(p1, c2, true) == HitPolicy::Allow);         // friendly fire on
    CHECK(companionHitPolicy(foe, c1, false) == HitPolicy::Allow);       // enemies hurt companions
    CHECK(companionHitPolicy(c1, foe, false) == HitPolicy::Allow);       // companions hurt enemies
    CHECK(companionHitPolicy(p1, foe, false) == HitPolicy::Allow);
    CHECK(c1.responsible() == 1 && foe.responsible() == kNoPlayer);
    CHECK(!vatsTargetAllowed(c2) && !vatsTargetAllowed(p2) && vatsTargetAllowed(foe));
}

TEST(companions, wire_round_trips) {
    Companion c; c.npc = npc(3); c.owner = 2; c.kind = CompanionKind::Critter; c.state.mode = FollowMode::Wait; c.state.stance = Stance::Passive; c.state.range = Range::Far; c.state.rev = 9;
    Companion c2 = roundTrip(c);
    CHECK(c2.npc == c.npc && c2.owner == 2 && c2.kind == CompanionKind::Critter && c2.state == c.state);
    CompanionCmd m; m.id = 5; m.from = 1; m.npc = npc(4); m.kind = CmdKind::SetRange; m.arg = 2;
    CompanionCmd m2 = roundTrip(m);
    CHECK(m2.id == 5 && m2.kind == CmdKind::SetRange && m2.arg == 2 && m2.npc == npc(4));
    InvDelta d; d.txn = 7; d.npc = npc(1); d.item = item(2); d.delta = -3;
    InvDelta d2 = roundTrip(d);
    CHECK(d2.txn == 7 && d2.delta == -3 && d2.item == item(2));
    InvFull f; f.npc = npc(1); f.items = {{item(1), 2}, {item(2), 3}};
    InvFull f2 = roundTrip(f);
    CHECK(f2.items.size() == 2 && f2.items[1].second == 3);
}
