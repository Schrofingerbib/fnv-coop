// Kill XP attribution and host-authoritative hostility (mutation twins 351..362).
#include "fake_world.h"

using namespace combat_test;

namespace {
int32_t total(const std::vector<XpAward>& a) { int32_t t = 0; for (auto& x : a) t += x.xp; return t; }
int32_t xpOf(const std::vector<XpAward>& a, PlayerId p) { for (auto& x : a) if (x.slot == p) return x.xp; return -1; }
KillRecord kill(uint64_t id, Entity killer, int32_t xp = 100, PlayerId recruiter = kNoPlayer) {
    KillRecord k;
    k.deathId = id; k.victim = Entity::actor(100); k.killer = killer; k.xp = xp; k.killerRecruiter = recruiter;
    return k;
}
}  // namespace

TEST(xp, killing_blow_pays_the_killer_only) {   // twin 351
    CombatRules r = defaultRules();
    std::vector<XpAward> a = attributeKillXp(kill(1, Entity::player(1)), XpMode::KillingBlow, {0, 1, 2}, r);
    REQUIRE(a.size() == 1);
    CHECK(a[0].slot == 1 && a[0].xp == 100);
    // killed by an npc or the environment: nobody earns it
    CHECK(attributeKillXp(kill(2, Entity::actor(101)), XpMode::KillingBlow, {0, 1}, r).empty());
    CHECK(attributeKillXp(kill(3, Entity()), XpMode::PartyShare, {0, 1}, r).empty());
    CHECK(attributeKillXp(kill(4, Entity::player(1), 0), XpMode::KillingBlow, {0, 1}, r).empty());
}

TEST(xp, a_companions_kill_goes_to_its_player) {   // twin 352
    CombatRules r = defaultRules();
    std::vector<XpAward> a = attributeKillXp(kill(1, Entity::actor(101), 80, 1), XpMode::KillingBlow, {0, 1}, r);
    REQUIRE(a.size() == 1);
    CHECK(a[0].slot == 1 && a[0].xp == 80);
    std::vector<XpAward> party = attributeKillXp(kill(2, Entity::actor(101), 80, 1), XpMode::PartyShare, {0, 1}, r);
    CHECK(total(party) == 80);
}

TEST(xp, party_share_splits_exactly) {   // twins 353, 354
    CombatRules r = defaultRules();
    std::vector<XpAward> a = attributeKillXp(kill(1, Entity::player(2), 100), XpMode::PartyShare, {0, 1, 2}, r);
    REQUIRE(a.size() == 3);
    CHECK(total(a) == 100);              // nothing lost, nothing invented
    CHECK(xpOf(a, 2) == 34);             // the remainder goes to the killer
    CHECK(xpOf(a, 0) == 33 && xpOf(a, 1) == 33);
    // killer not in the party list (left the session): remainder to the lowest slot
    std::vector<XpAward> b = attributeKillXp(kill(2, Entity::player(5), 100), XpMode::PartyShare, {1, 0, 1, 0}, r);   // duplicates ignored
    REQUIRE(b.size() == 2);
    CHECK(total(b) == 100 && xpOf(b, 0) == 50 && xpOf(b, 1) == 50);
    // a single player gets all of it
    CHECK(total(attributeKillXp(kill(3, Entity::player(0), 17), XpMode::PartyShare, {0}, r)) == 17);
    // empty party: the killer
    std::vector<XpAward> solo = attributeKillXp(kill(4, Entity::player(1), 40), XpMode::PartyShare, {}, r);
    CHECK(solo.size() == 1 && solo[0].slot == 1 && solo[0].xp == 40);
}

TEST(xp, one_death_awards_once) {   // twin 355
    CombatRules r = defaultRules();
    XpLedger l;
    CHECK(total(l.award(kill(9, Entity::player(1)), XpMode::KillingBlow, {0, 1}, r)) == 100);
    CHECK(l.award(kill(9, Entity::player(1)), XpMode::KillingBlow, {0, 1}, r).empty());   // the death message arrived again
    CHECK(l.award(kill(9, Entity::player(0)), XpMode::KillingBlow, {0, 1}, r).empty());   // ... or a second machine also reported it, with another killer
    CHECK(total(l.award(kill(10, Entity::player(1)), XpMode::KillingBlow, {0, 1}, r)) == 100);
}

namespace {
FormRef faction(uint32_t id) { return FormRef("falloutnv.esm", id); }
CrimeReport crime(PlayerId who, FormRef victimFaction, bool witnessed = true, bool victimHostile = false) {
    CrimeReport c;
    c.criminal = who; c.victim = Entity::actor(100); c.victimFaction = victimFaction; c.criminalFaction = faction(0x1000);
    c.witnessed = witnessed; c.victimWasHostile = victimHostile; c.reporter = who;
    return c;
}
}  // namespace

TEST(hostility, only_the_host_decides) {   // twin 357
    HostilityBook guest, host;
    CHECK(guest.onCrime(crime(1, faction(0x2000)), 1, 1).empty());   // a guest's own book never creates effects
    std::vector<HostilityEffect> fx = host.onCrime(crime(1, faction(0x2000)), 0, 1);
    REQUIRE(fx.size() == 1);
    CHECK(fx[0].kind == HostilityKind::SetEnemy && fx[0].factionA == faction(0x1000) && fx[0].factionB == faction(0x2000) && fx[0].cause == 1);
    CHECK(host.hostile(faction(0x2000), faction(0x1000)));   // order does not matter
    CHECK(!guest.hostile(faction(0x2000), faction(0x1000)));
}

TEST(hostility, who_counts_as_a_crime) {   // twins 358, 360
    HostilityBook b;
    CHECK(b.onCrime(crime(1, faction(0x2000), true, true), 0, 1).empty());     // attacking something already hostile
    CHECK(b.onCrime(crime(1, faction(0x2000), false), 0, 1).empty());          // nobody saw it
    CHECK(b.onCrime(crime(1, FormRef()), 0, 1).empty());                       // no faction involved
    CHECK(b.onCrime(crime(1, faction(0x1000)), 0, 1).empty());                 // own faction against itself
    CHECK(b.onCrime(crime(200, faction(0x2000)), 0, 1).empty());               // not a player slot
    CHECK(!b.hostile(faction(0x1000), faction(0x2000)));
    CHECK(b.onCrime(crime(1, faction(0x2000)), 0, 3).size() == 1);             // a witness on any machine counts
}

TEST(hostility, escalation_is_idempotent_and_peace_undoes_it) {   // twins 359, 361
    HostilityBook b;
    CHECK(b.onCrime(crime(1, faction(0x2000)), 0, 1).size() == 1);
    CHECK(b.onCrime(crime(0, faction(0x2000)), 0, 0).empty());   // already enemies: a second crime changes nothing
    CHECK(b.onCrime(crime(1, faction(0x2000)), 0, 1).empty());
    std::vector<HostilityEffect> peace = b.makePeace(faction(0x1000), faction(0x2000), 0, 0);
    REQUIRE(peace.size() == 1);
    CHECK(peace[0].kind == HostilityKind::SetAlly);
    CHECK(!b.hostile(faction(0x1000), faction(0x2000)));
    CHECK(b.makePeace(faction(0x1000), faction(0x2000), 0, 0).empty());   // nothing left to undo
    CHECK(b.makePeace(faction(0x1000), faction(0x2000), 1, 1).empty());   // only the host
    // after peace the next crime escalates again, with a NEW effect id (not mistaken for the replay of the first)
    std::vector<HostilityEffect> again = b.onCrime(crime(1, faction(0x2000)), 0, 1);
    REQUIRE(again.size() == 1);
    HostilityBook other;
    std::vector<HostilityEffect> first = other.onCrime(crime(1, faction(0x2000)), 0, 1);
    REQUIRE(first.size() == 1);
    CHECK(first[0].id != again[0].id);
}

TEST(hostility, clients_apply_host_effects_once) {   // twins 356, 362
    HostilityBook host, client;
    std::vector<HostilityEffect> fx = host.onCrime(crime(1, faction(0x2000)), 0, 1);
    REQUIRE(fx.size() == 1);
    CHECK(!client.accept(fx[0], 1));                // a guest cannot make war
    CHECK(!client.hostile(faction(0x1000), faction(0x2000)));
    CHECK(client.accept(fx[0], 0));
    CHECK(client.hostile(faction(0x1000), faction(0x2000)));
    CHECK(!client.accept(fx[0], 0));                // a replay is dropped
    std::vector<HostilityEffect> peace = host.makePeace(faction(0x1000), faction(0x2000), 0, 0);
    REQUIRE(peace.size() == 1);
    CHECK(client.accept(peace[0], 0));
    CHECK(!client.hostile(faction(0x1000), faction(0x2000)));
    CHECK(!client.accept(fx[0], 0));                // the old war message, replayed after the peace, stays dropped
    CHECK(!client.hostile(faction(0x1000), faction(0x2000)));
}

TEST(hostility, console_lines) {
    HostilityEffect e;
    e.kind = HostilityKind::SetEnemy;
    CHECK(consoleLineFor(e, "VaultCityFaction", "RaidersFaction") == "SetEnemy VaultCityFaction RaidersFaction");
    e.kind = HostilityKind::SetAlly;
    CHECK(consoleLineFor(e, "A", "B") == "SetAlly A B");
    e.kind = HostilityKind::StopCombat;
    CHECK(consoleLineFor(e, "", "") == "StopCombat");
}
