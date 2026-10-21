// Kill rewards: XP by host setting, karma, paid once, never twice to a slot the engine already paid (twins 861..870).
#include <cmath>
#include "util.h"

using namespace social_test;

namespace {
KillReport kill(uint64_t id, cc::Entity killer, int32_t xp = 100, KarmaClass kc = KarmaClass::Unknown) {
    KillReport k;
    k.deathId = id; k.victim.who = cc::Entity::actor(100); k.victim.xp = xp; k.victim.karma = kc; k.victim.faction = faction(1); k.killer = killer;
    k.reporter = 0;
    return k;
}
int32_t xpOf(const KillRewards& r, PlayerId p) { for (auto& g : r.xp) if (g.slot == p) return g.xp; return -1; }
int32_t xpTotal(const KillRewards& r) { int32_t t = 0; for (auto& g : r.xp) t += g.xp; return t; }
}  // namespace

TEST(rewards, killing_blow_pays_the_killer_and_a_companions_kill_its_recruiter) {
    RewardLedger l;
    SocialSettings s;
    KillRewards r = l.settle(kill(1, cc::Entity::player(1)), s, {0, 1, 2}, 0x07);
    CHECK(r.settled && r.xp.size() == 1 && xpOf(r, 1) == 100 && r.responsible == 1);
    KillReport comp = kill(2, cc::Entity::actor(300));
    comp.killerRecruiter = 2;
    KillRewards c = l.settle(comp, s, {0, 1, 2}, 0x07);
    CHECK(c.xp.size() == 1 && xpOf(c, 2) == 100 && c.responsible == 2);
    // an NPC or the environment killed it: nobody earns it
    KillRewards n = l.settle(kill(3, cc::Entity::actor(301)), s, {0, 1, 2}, 0x07);
    CHECK(!n.settled && n.xp.empty() && n.responsible == kNoPlayer);
    KillRewards e = l.settle(kill(4, cc::Entity()), s, {0, 1, 2}, 0x07);
    CHECK(!e.settled);
    // a victim worth nothing pays nothing; a player's death is no kill
    CHECK(!l.settle(kill(5, cc::Entity::player(1), 0), s, {0, 1}, 0x03).settled);
    KillReport pk = kill(6, cc::Entity::player(1));
    pk.victim.who = cc::Entity::player(2);
    CHECK(!l.settle(pk, s, {0, 1, 2}, 0x07).settled);
}

TEST(rewards, the_host_setting_chooses_party_share) {   // twin 870
    RewardLedger l;
    SocialSettings s;
    s.xpMode = cc::XpMode::PartyShare;
    KillRewards r = l.settle(kill(1, cc::Entity::player(2)), s, {0, 1, 2}, 0x07);
    CHECK(xpTotal(r) == 100 && r.xp.size() == 3 && xpOf(r, 2) == 34 && xpOf(r, 0) == 33 && xpOf(r, 1) == 33);
    // only the players present share: player 1 is far away
    KillRewards f = l.settle(kill(2, cc::Entity::player(2)), s, {0, 2}, 0x07);
    CHECK(xpTotal(f) == 100 && xpOf(f, 1) == -1 && xpOf(f, 0) == 50 && xpOf(f, 2) == 50);
}

TEST(rewards, one_death_settles_once) {   // twin 861
    RewardLedger l;
    SocialSettings s;
    CHECK(l.settle(kill(9, cc::Entity::player(1)), s, {0, 1}, 0x03).settled);
    CHECK(!l.settle(kill(9, cc::Entity::player(1)), s, {0, 1}, 0x03).settled);   // the death message arrived again
    CHECK(!l.settle(kill(9, cc::Entity::player(0)), s, {0, 1}, 0x03).settled);   // a second machine reported it with another killer
    CHECK(l.seen(9) && l.settledCount() == 1);
    CHECK(l.settle(kill(10, cc::Entity::player(1)), s, {0, 1}, 0x03).settled);
    l.reset();
    CHECK(l.settle(kill(9, cc::Entity::player(1)), s, {0, 1}, 0x03).settled);      // a new world
}

TEST(rewards, what_the_engine_already_paid_is_not_paid_again) {   // twins 862, 865
    RewardLedger l;
    SocialSettings s;
    KillReport k = kill(1, cc::Entity::player(1), 100, KarmaClass::Good);
    k.nativeMask = 0x02;   // player 1's own machine killed it natively: its engine paid XP and moved its karma
    KillRewards r = l.settle(k, s, {0, 1, 2}, 0x07);
    CHECK(r.xp.empty() && r.xpNative == 100);
    CHECK(r.karma == -3 && r.karmaTo == 0x05);   // the OTHER players still get the shared karma
    CHECK(r.settled);
    // party share: the native slot's part is not paid again, the others' parts are
    SocialSettings ps;
    ps.xpMode = cc::XpMode::PartyShare;
    KillReport p = kill(2, cc::Entity::player(2));
    p.nativeMask = 0x04;
    KillRewards pr = l.settle(p, ps, {0, 1, 2}, 0x07);
    CHECK(pr.xp.size() == 2 && xpOf(pr, 0) == 33 && xpOf(pr, 1) == 33 && pr.xpNative == 34);
}

TEST(rewards, karma_follows_the_victim_and_self_defence_is_free) {   // twins 863, 864
    RewardLedger l;
    SocialSettings s;
    KillRewards good = l.settle(kill(1, cc::Entity::player(1), 0, KarmaClass::Good), s, {0, 1}, 0x07);
    CHECK(good.karma == -3 && good.karmaTo == 0x07 && good.settled);   // shared by the SharePolicy: every player
    KillReport hostileGood = kill(2, cc::Entity::player(1), 0, KarmaClass::Good);
    hostileGood.victim.hostileToParty = true;
    CHECK(l.settle(hostileGood, s, {0, 1}, 0x07).karma == 0);          // it was shooting at you
    KillReport hostileEvil = kill(3, cc::Entity::player(1), 0, KarmaClass::VeryEvil);
    hostileEvil.victim.hostileToParty = true;
    KillRewards evil = l.settle(hostileEvil, s, {0, 1}, 0x07);
    CHECK(evil.karma == 2 && evil.settled);                            // killing evil still pays
    SocialSettings personal;
    personal.shareKarma = false;
    KillRewards mine = l.settle(kill(4, cc::Entity::player(1), 0, KarmaClass::VeryGood), personal, {0, 1}, 0x07);
    CHECK(mine.karma == -5 && mine.karmaTo == 0x02);                   // only the killer
    SocialSettings off;
    off.karmaOnKills = false;
    CHECK(l.settle(kill(5, cc::Entity::player(1), 0, KarmaClass::Good), off, {0, 1}, 0x07).karma == 0);
    CHECK(l.settle(kill(6, cc::Entity::player(1), 0, KarmaClass::Unknown), s, {0, 1}, 0x07).karma == 0);
    CHECK(l.settle(kill(7, cc::Entity::actor(9), 0, KarmaClass::Good), s, {0, 1}, 0x07).karma == 0);   // nobody answerable
    KillReport viaCompanion = kill(8, cc::Entity::actor(9), 0, KarmaClass::Neutral);
    viaCompanion.killerRecruiter = 1;
    CHECK(l.settle(viaCompanion, s, {0, 1}, 0x07).karma == -1);
}

TEST(rewards, presence_by_scope) {   // twins 868, 869
    std::vector<PlayerPlace> ps(5);
    ps[0] = {0, true, 1, {0, 0, 0}};
    ps[1] = {1, true, 1, {100, 0, 0}};
    ps[2] = {2, true, 2, {0, 0, 0}};
    ps[3] = {3, true, 1, {9000, 0, 0}};
    ps[4] = {4, false, 1, {0, 0, 0}};
    cc::Vec3 at{0, 0, 0};
    CHECK(presentPlayers(XpScope::AllConnected, ps, 1, at, 6000) == (std::vector<PlayerId>{0, 1, 2, 3}));
    CHECK(presentPlayers(XpScope::SameCell, ps, 1, at, 6000) == (std::vector<PlayerId>{0, 1, 3}));
    CHECK(presentPlayers(XpScope::Near, ps, 1, at, 6000) == (std::vector<PlayerId>{0, 1}));
    ps.push_back({1, true, 1, {0, 0, 0}});   // a duplicate slot
    CHECK(presentPlayers(XpScope::Near, ps, 1, at, 6000) == (std::vector<PlayerId>{0, 1}));
}

TEST(rewards, effects_skip_the_hosts_own_slot_and_share_reputation_by_policy) {   // twins 866, 867
    KillRewards r;
    r.settled = true;
    r.xp = {{0, 10}, {1, 20}};
    r.karma = -3;
    r.karmaTo = 0x07;
    std::vector<Effect> fx = rewardEffects(r, 0x01);   // slot 0 is the host: its engine applies its own part
    REQUIRE(fx.size() == 2);
    const auto* xp = std::get_if<ModActorValue>(&fx[0]);
    REQUIRE(xp);
    CHECK(xp->av == kAvExperience && xp->delta == 20 && xp->to == Recipient::slot(1));
    const auto* k = std::get_if<ModKarma>(&fx[1]);
    REQUIRE(k);
    CHECK(k->delta == -3 && k->to == Recipient::slots(0x06));
    // reputation
    std::vector<RepChange> rep = {{faction(1), 0, 2}};
    std::vector<Effect> shared = reputationEffects(rep, 1, true, 0x07, 0x00, 0x01);
    REQUIRE(shared.size() == 1);
    CHECK(std::get<ModReputation>(shared[0]).to == Recipient::slots(0x06) && std::get<ModReputation>(shared[0]).infamy == 2);
    std::vector<Effect> personal = reputationEffects(rep, 1, false, 0x07, 0x00, 0x01);
    REQUIRE(personal.size() == 1);
    CHECK(std::get<ModReputation>(personal[0]).to == Recipient::slots(0x02));
    CHECK(reputationEffects(rep, 1, false, 0x07, 0x02, 0x01).empty());   // the criminal's own engine already did it
    CHECK(reputationEffects({}, 1, true, 0x07, 0, 0).empty());
    // nothing to deliver: no effects
    KillRewards none;
    CHECK(rewardEffects(none, 0).empty());
}

TEST(rewards, wire_round_trips) {
    SocialSettings s;
    s.rev = 4; s.xpMode = cc::XpMode::PartyShare; s.scope = XpScope::Near; s.nearRadius = 1234; s.shareKarma = false; s.karmaOnKills = true; s.crime = false;
    s.friendlyFire = true; s.foreignCompanions = ForeignPolicy::Allow; s.caps = FollowerCaps{2, 3, 6}; s.karma.delta[3] = 7;
    SocialSettings s2 = roundTrip(s);
    CHECK(s2 == s);
    SocialSettings bad = s;
    bad.nearRadius = std::nanf("");
    CHECK(roundTrip(bad).rev == 0);   // refused: the default comes back
    KillReport k = kill(77, cc::Entity::player(1), 55, KarmaClass::Evil);
    k.killerRecruiter = 2; k.contributors = {0, 1}; k.nativeMask = 3; k.cell = 12; k.where = {1, 2, 3}; k.reporter = 1; k.victim.named = true; k.victim.hostileToParty = true;
    KillReport k2 = roundTrip(k);
    CHECK(k2.deathId == 77 && k2.victim.xp == 55 && k2.victim.karma == KarmaClass::Evil && k2.killerRecruiter == 2 && k2.contributors.size() == 2 && k2.nativeMask == 3 &&
          k2.cell == 12 && k2.where == k.where && k2.reporter == 1 && k2.victim.named && k2.victim.hostileToParty && k2.victim.faction == faction(1) && k2.killer == cc::Entity::player(1));
    KillReport badK = k;
    badK.contributors = {9};
    CHECK(roundTrip(badK).deathId == 0);
}
