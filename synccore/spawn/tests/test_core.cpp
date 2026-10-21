// synccore/spawn core tests (game-free). Groups: ids, wire, registry, view, tracker, storm.
// Every FNV_MUT twin of synccore/spawn (spawn.cpp, spawn_wire.cpp) must make at least one of these fail.
#include <algorithm>
#include <cstring>
#include <set>
#include "fnvcoop/rng.h"
#include "fnvcoop/wire.h"
#include "th.h"

using namespace spt;

// ---- ids ----------------------------------------------------------------------------------------------------------------------
TEST(ids, site_ids_are_stable_and_distinct) {
    const SiteId a = siteIdOf(SiteKind::Marker, F(0x1000), 5);
    CHECK(a != 0);
    CHECK(a == siteIdOf(SiteKind::Marker, F(0x1000), 5));
    CHECK(a == siteIdOf(SiteKind::Marker, FormRef("FalloutNV.esm", 0x1000), 5));   // plugin names compare lower-cased
    CHECK(a != siteIdOf(SiteKind::Encounter, F(0x1000), 5));
    CHECK(a != siteIdOf(SiteKind::Marker, F(0x1001), 5));
    CHECK(a != siteIdOf(SiteKind::Marker, F(0x1000), 6));
    CHECK(a != siteIdOf(SiteKind::Marker, F(0x1000, "dlc.esm"), 5));
    CHECK(ambientSiteId(5, 100, 100) == ambientSiteId(5, 900, 1900));     // same grid square
    CHECK(ambientSiteId(5, 100, 100) != ambientSiteId(5, 2100, 100));
    CHECK(ambientSiteId(5, -1, 0) != ambientSiteId(5, 1, 0));             // negative coordinates floor, not truncate
}

TEST(ids, issuer_hands_out_unique_ids_in_the_issued_range) {
    RangeIssuer is;
    std::set<NetId> seen;
    for (int i = 0; i < 1000; ++i) {
        NetId id = is.next();
        CHECK(isIssuedId(id) && isCreatedId(id));
        CHECK(seen.insert(id).second);
    }
    is.raiseFloor(0xFFC01000u);
    CHECK(is.next() > 0xFFC01000u);
    is.raiseFloor(0xFF000123u);   // an ordinary engine id is not an issued id: ignored
    CHECK(is.peek() > 0xFFC01000u);
    is.raiseFloor(0xFFFFFFFEu);
    CHECK(is.next() == 0);        // exhausted: 0, never a repeat
    CHECK(is.next() == 0);
}

// ---- wire ---------------------------------------------------------------------------------------------------------------------
namespace {
RollReport sampleReport() {
    SiteDef d = mkDef(SiteKind::Encounter, 0x2222, 7, true, V(10, 20, 30), 48);
    return mkReport(d, 2, 3, 9, {created(0x300, 1, 0, V(1, 2, 3)), placed(0x4000, 0x301, 2)}, 0x302);
}
SiteMsg sampleSite() {
    SiteMsg s;
    s.def = mkDef(SiteKind::Marker, 0x55, 3);
    s.state = SiteState::Cleared; s.generation = 4; s.rev = 11; s.none = false; s.pick = F(0x9); s.clearedAtGame = 123456789012ull; s.respawnAtGame = 987654321098ull;
    Member m = created(0x70, 5, 0xFFC00001u, V(4, 5, 6)); m.dead = true;
    s.members = {m, placed(0x600, 0x71)};
    return s;
}
std::vector<Envelope> sampleEnvelopes() {
    std::vector<Envelope> v;
    Envelope e;
    e.src = 2; e.body = MRoll{sampleReport()}; v.push_back(e);
    VerdictMsg vm; vm.site = 5; vm.rollId = 9; vm.verdict = Verdict::AlreadySpawned; vm.epoch = 3; vm.owner = 1; vm.generation = 2; vm.pick = F(4); vm.members = sampleSite().members;
    e.src = 0; e.body = MVerdict{vm}; v.push_back(e);
    e.body = MSite{sampleSite()}; v.push_back(e);
    OwnMsg om; om.kind = OwnMsg::Commit; om.netId = 77; om.epoch = 4; om.owner = 1; om.prevOwner = 0;
    e.body = MOwn{om}; v.push_back(e);
    e.src = 1; e.body = MDigest{0x1122334455667788ull, 12}; v.push_back(e);
    e.body = MResync{1}; v.push_back(e);
    return v;
}
bool sameBody(const Body& a, const Body& b) { return a.index() == b.index() && encodeEnvelope(Envelope{0, a}) == encodeEnvelope(Envelope{0, b}); }
}  // namespace

TEST(wire, every_message_round_trips) {
    for (const Envelope& e : sampleEnvelopes()) {
        std::vector<uint8_t> bytes = encodeEnvelope(e);
        REQUIRE(!bytes.empty());
        CHECK(bytes[0] == kChannelSpawn);
        Envelope back;
        CHECK(decodeEnvelope(bytes.data(), bytes.size(), back));
        CHECK(back.src == e.src);
        CHECK(sameBody(back.body, e.body));
    }
    // field-level check on the big one
    Envelope e; e.src = 2; e.body = MRoll{sampleReport()};
    std::vector<uint8_t> b = encodeEnvelope(e);
    Envelope back;
    REQUIRE(decodeEnvelope(b.data(), b.size(), back));
    const RollReport& r = std::get<MRoll>(back.body).r;
    CHECK(r.def == sampleReport().def);
    CHECK(r.roller == 2 && r.epoch == 3 && r.rollId == 9 && !r.none && r.pick == F(0x302));
    REQUIRE(r.members.size() == 2);
    CHECK(r.members[0].created && r.members[0].base == F(0x300) && r.members[0].pos == V(1, 2, 3));
    CHECK(!r.members[1].created && r.members[1].netId == 0x4000);
}

TEST(wire, truncation_and_corruption_never_crash_or_half_decode) {
    for (const Envelope& e : sampleEnvelopes()) {
        const std::vector<uint8_t> bytes = encodeEnvelope(e);
        for (size_t n = 0; n < bytes.size(); ++n) {
            Envelope out;
            CHECK(!decodeEnvelope(bytes.data(), n, out));   // every strict prefix is rejected
        }
        // single byte flips: must not crash; whatever decodes must re-encode to something that decodes again
        for (size_t i = 0; i < bytes.size(); ++i) {
            std::vector<uint8_t> c = bytes;
            c[i] = static_cast<uint8_t>(c[i] ^ 0x5A);
            Envelope out;
            if (decodeEnvelope(c.data(), c.size(), out)) {
                std::vector<uint8_t> again = encodeEnvelope(out);
                Envelope out2;
                CHECK(again.empty() || decodeEnvelope(again.data(), again.size(), out2));
            }
        }
    }
}

TEST(wire, random_garbage_is_rejected_without_crashing) {
    fnvcoop::Rng rng(42);
    int accepted = 0;
    for (int i = 0; i < 3000; ++i) {
        std::vector<uint8_t> d(rng.range(0, 60));
        for (auto& b : d) b = static_cast<uint8_t>(rng.range(0, 255));
        if (!d.empty() && (i & 1)) d[0] = kChannelSpawn;
        Envelope out;
        if (decodeEnvelope(d.data(), d.size(), out)) ++accepted;
    }
    CHECK(accepted < 30);   // a few short valid-looking ones (Resync, Digest) are fine
}

TEST(wire, trailing_bytes_and_oversized_groups_are_refused) {
    Envelope e; e.src = 1; e.body = MResync{1};
    std::vector<uint8_t> b = encodeEnvelope(e);
    Envelope out;
    CHECK(decodeEnvelope(b.data(), b.size(), out));
    b.push_back(0xEE);
    CHECK(!decodeEnvelope(b.data(), b.size(), out));      // twin 19: trailing garbage accepted
    RollReport r = sampleReport();
    r.members.clear();
    for (size_t i = 0; i < kMaxMembers; ++i) r.members.push_back(placed(0x9000 + static_cast<NetId>(i), 0x10));
    Envelope big; big.src = 2; big.body = MRoll{r};
    CHECK(!encodeEnvelope(big).empty());
    r.members.push_back(placed(0x9999, 0x10));
    big.body = MRoll{r};
    CHECK(encodeEnvelope(big).empty());                    // twin 19: the length bound is gone
}

// ---- registry -----------------------------------------------------------------------------------------------------------------
namespace {
// host + client 1 in the same cell: the host is preferred. Client 1 alone: client 1 owns.
Reg twoPlayers(CellId hostCell, CellId clientCell) {
    Reg g;
    g.join(1);
    g.r.setPlayerLoc(0, loc(hostCell));
    g.r.setPlayerLoc(1, loc(clientCell));
    return g;
}
}  // namespace

TEST(registry, the_owner_rolls_and_gets_issued_ids) {
    Reg g = twoPlayers(9, 5);
    SiteDef d = mkDef(SiteKind::Marker, 0x1000, 5);
    VerdictMsg v = g.roll(mkReport(d, 1, 0, 1, {created(0x77, 11), created(0x78, 12)}, 0x70));
    REQUIRE(v.verdict == Verdict::Accept);
    CHECK(v.owner == 1);
    REQUIRE(v.members.size() == 2);
    CHECK(isIssuedId(v.members[0].netId) && isIssuedId(v.members[1].netId));
    CHECK(v.members[0].netId != v.members[1].netId);
    CHECK(v.members[0].handle == 11 && v.members[1].handle == 12);   // handles come back so the roller can adopt its own refs
    CHECK(g.r.stateOf(d.id) == SiteState::Spawned);
    REQUIRE(g.out.spawns.size() == 2);
    CHECK(g.out.spawns[0].origin == 1);
    CHECK(g.out.spawns[0].spawn.id == v.members[0].netId && g.out.spawns[0].spawn.base == F(0x77) && !g.out.spawns[0].spawn.leveled);
    CHECK(g.out.spawns[0].spawn.cell == d.cellForm);
    REQUIRE(g.out.sites.size() == 1);
    CHECK(g.out.sites[0].state == SiteState::Spawned && g.out.sites[0].generation == 1 && g.out.sites[0].members.size() == 2);
    SiteId s = 0;
    CHECK(g.r.siteOfMember(v.members[0].netId, s) && s == d.id);
}

TEST(registry, a_host_roll_keeps_the_host_engine_id) {
    Reg g = twoPlayers(5, 9);
    SiteDef d = mkDef(SiteKind::Encounter, 0x1001, 5);
    VerdictMsg v = g.roll(mkReport(d, 0, 0, 1, {created(0x77, 1, 0xFF001234u)}), 0);
    REQUIRE(v.verdict == Verdict::Accept);
    REQUIRE(v.members.size() == 1);
    CHECK(v.members[0].netId == 0xFF001234u);                 // identity: the id the host's engine gave the ref (twin 8: re-issued)
    REQUIRE(g.out.spawns.size() == 1);
    CHECK(g.out.spawns[0].spawn.id == 0xFF001234u);
    // a host report with an id from the issued range, or not a created id at all, is not believable
    VerdictMsg bad1 = g.roll(mkReport(mkDef(SiteKind::Encounter, 0x1002, 5), 0, 0, 2, {created(0x77, 1, 0xFFC00005u)}), 0);
    CHECK(bad1.verdict == Verdict::BadReport);
    VerdictMsg bad2 = g.roll(mkReport(mkDef(SiteKind::Encounter, 0x1003, 5), 0, 0, 3, {created(0x77, 1, 0x00123456u)}), 0);
    CHECK(bad2.verdict == Verdict::BadReport);
}

TEST(registry, two_clients_never_get_the_same_issued_id) {
    Reg g;
    g.join(1); g.join(2);
    g.r.setPlayerLoc(0, loc(99)); g.r.setPlayerLoc(1, loc(5)); g.r.setPlayerLoc(2, loc(6));
    std::set<NetId> ids;
    for (int i = 0; i < 20; ++i) {
        const PlayerId p = static_cast<PlayerId>(1 + (i & 1));
        const CellId cell = p == 1 ? 5 : 6;
        VerdictMsg v = g.roll(mkReport(mkDef(SiteKind::Marker, 0x3000 + i, cell), p, 0, 1 + i, {created(0x77, 1), created(0x78, 2)}));
        REQUIRE(v.verdict == Verdict::Accept);
        for (auto& m : v.members) CHECK(ids.insert(m.netId).second);
    }
    CHECK(ids.size() == 40);
}

TEST(registry, only_the_owner_rolls) {
    Reg g = twoPlayers(5, 5);   // both here: the host is preferred
    SiteDef d = mkDef(SiteKind::Marker, 0x1000, 5);
    VerdictMsg v = g.roll(mkReport(d, 1, 0, 1, {created(0x77, 1)}));
    CHECK(v.verdict == Verdict::NotOwner);
    CHECK(v.owner == 0);
    CHECK(g.r.stateOf(d.id) == SiteState::Unrolled);
    CHECK(g.out.spawns.empty());
    VerdictMsg ok = g.roll(mkReport(d, 0, 0, 1, {created(0x77, 1, 0xFF000010u)}));
    CHECK(ok.verdict == Verdict::Accept);
}

TEST(registry, a_stale_epoch_roll_is_refused) {
    Reg g = twoPlayers(5, 5);
    SiteDef d = mkDef(SiteKind::Marker, 0x1000, 5);
    // make the ownership move twice: host -> client -> host
    g.roll(mkReport(d, 0, 0, 1, {created(0x77, 1, 0xFF000010u)}), 0);   // registers the site, host owns at epoch e1
    const OwnerEpoch e1 = g.r.epochOf(d.id);
    g.r.setPlayerLoc(0, loc(77));
    g.tick(2000);
    CHECK(g.r.ownerOf(d.id) == 1);
    g.r.setPlayerLoc(0, loc(5));
    g.tick(2000);
    CHECK(g.r.ownerOf(d.id) == 0);
    const OwnerEpoch e3 = g.r.epochOf(d.id);
    CHECK(e3 > e1 + 1);
    // the same player owns it again, but a roll that carries the old tenure's epoch must not count
    g.r.forceRespawn(d.id, g.out);   // nothing to respawn yet
    SiteRegistry::Out dummy;
    g.r.onMemberDied(0xFF000010u, g.wall, dummy);   // clear the site so a new roll would otherwise be fine
    g.r.setGameTime(g.r.gameTime() + 80ull * 3600 * 1000);
    g.tick(0);
    VerdictMsg st = g.roll(mkReport(d, 0, e1, 2, {created(0x77, 1, 0xFF000011u)}), 0);
    CHECK(st.verdict == Verdict::StaleEpoch);                 // twin 2: accepted
    CHECK(st.epoch == e3 && st.owner == 0);
    VerdictMsg fut = g.roll(mkReport(d, 0, e3 + 5, 3, {created(0x77, 1, 0xFF000012u)}), 0);
    CHECK(fut.verdict == Verdict::StaleEpoch);
    VerdictMsg ok = g.roll(mkReport(d, 0, e3, 4, {created(0x77, 1, 0xFF000013u)}), 0);
    CHECK(ok.verdict == Verdict::Accept);
}

TEST(registry, a_replayed_roll_spawns_nothing_twice) {
    Reg g = twoPlayers(9, 5);
    SiteDef d = mkDef(SiteKind::Marker, 0x1000, 5);
    RollReport rep = mkReport(d, 1, 0, 1, {created(0x77, 1), created(0x78, 2)});
    VerdictMsg a = g.roll(rep);
    REQUIRE(a.verdict == Verdict::Accept);
    const uint64_t committed = g.r.stats().spawnsCommitted;
    VerdictMsg b = g.roll(rep);
    CHECK(b.verdict == Verdict::Accept);                       // the same answer again (twin 3: AlreadySpawned)
    REQUIRE(b.members.size() == 2);
    CHECK(b.members[0].netId == a.members[0].netId && b.members[1].netId == a.members[1].netId);
    CHECK(g.out.spawns.empty() && g.out.sites.empty());
    CHECK(g.r.stats().spawnsCommitted == committed);
    CHECK(g.r.stats().accepted == 1 && g.r.stats().duplicates == 1);
}

TEST(registry, a_second_roll_for_a_live_site_shows_the_first) {
    Reg g = twoPlayers(9, 5);
    SiteDef d = mkDef(SiteKind::Marker, 0x1000, 5);
    VerdictMsg a = g.roll(mkReport(d, 1, 0, 1, {created(0x77, 1)}, 0x70));
    REQUIRE(a.verdict == Verdict::Accept);
    VerdictMsg b = g.roll(mkReport(d, 1, 0, 2, {created(0x99, 5)}, 0x71));   // the owner reloaded the cell and its engine rolled again
    CHECK(b.verdict == Verdict::AlreadySpawned);               // twin 4
    REQUIRE(b.members.size() == 1);
    CHECK(b.members[0].netId == a.members[0].netId && b.members[0].base == F(0x77));
    CHECK(b.pick == F(0x70));
    CHECK(g.out.spawns.empty());
}

TEST(registry, reports_that_cannot_be_true_are_refused) {
    Reg g = twoPlayers(9, 5);
    SiteDef d = mkDef(SiteKind::Marker, 0x1000, 5);
    CHECK(g.roll(mkReport(d, 1, 0, 0, {created(0x77, 1)})).verdict == Verdict::BadReport);             // rollId 0
    CHECK(g.roll(mkReport(d, 1, 0, 1, {})).verdict == Verdict::BadReport);                             // nothing and not "none"
    RollReport none = mkReport(d, 1, 0, 2, {created(0x77, 1)});
    none.none = true;
    CHECK(g.roll(none).verdict == Verdict::BadReport);                                                 // "none" with members
    std::vector<Member> many;
    for (size_t i = 0; i < kMaxMembers + 1; ++i) many.push_back(created(0x77, static_cast<uint32_t>(i + 1)));
    CHECK(g.roll(mkReport(d, 1, 0, 3, many)).verdict == Verdict::BadReport);                           // too many
    CHECK(g.roll(mkReport(d, 1, 0, 4, {placed(0x500, 1), placed(0x500, 2)})).verdict == Verdict::BadReport);   // same id twice
    CHECK(g.roll(mkReport(d, 1, 0, 5, {placed(0, 1)})).verdict == Verdict::BadReport);                 // placed ref without an id
    CHECK(g.roll(mkReport(d, 1, 0, 6, {placed(0xFF000001u, 1)})).verdict == Verdict::BadReport);       // placed refs are never in the created range
    SiteDef noid = d; noid.id = 0;
    CHECK(g.roll(mkReport(noid, 1, 0, 7, {created(0x77, 1)})).verdict == Verdict::BadReport);
    CHECK(g.r.stateOf(d.id) == SiteState::Unrolled);
    CHECK(g.r.stats().accepted == 0);
}

TEST(registry, a_roll_in_somebody_elses_name_is_refused) {
    Reg g;
    g.join(1); g.join(2);
    g.r.setPlayerLoc(0, loc(99)); g.r.setPlayerLoc(1, loc(5)); g.r.setPlayerLoc(2, loc(5));
    SiteDef d = mkDef(SiteKind::Marker, 0x1000, 5);
    // client 2 sends a report that says it is client 1 (the owner by distance order)
    VerdictMsg v = g.roll(mkReport(d, 1, 0, 1, {created(0x77, 1)}), 2);
    CHECK(v.verdict == Verdict::BadReport);                    // twin 17
    CHECK(g.r.stateOf(d.id) == SiteState::Unrolled);
    CHECK(g.r.stats().spoofed == 1);
}

TEST(registry, a_cleared_exterior_site_respawns_after_its_game_time) {
    Reg g = twoPlayers(5, 9);
    SiteDef d = mkDef(SiteKind::Marker, 0x1000, 5);
    VerdictMsg a = g.roll(mkReport(d, 0, 0, 1, {created(0x77, 1, 0xFF000001u), created(0x78, 2, 0xFF000002u)}), 0);
    REQUIRE(a.verdict == Verdict::Accept);
    g.out.clear();
    g.r.setGameTime(1000);
    g.r.onMemberDied(0xFF000001u, g.wall, g.out);
    CHECK(g.out.sites.empty());                                // one of two died: the site is still spawned (twin 11)
    CHECK(g.r.stateOf(d.id) == SiteState::Spawned);
    g.r.onMemberDied(0xFF000001u, g.wall, g.out);              // a replayed death changes nothing
    CHECK(g.out.sites.empty());
    g.r.onMemberDied(0xFF000002u, g.wall, g.out);
    CHECK(g.r.stateOf(d.id) == SiteState::Cleared);            // twin 10
    REQUIRE(g.out.sites.size() == 1);
    CHECK(g.out.sites[0].state == SiteState::Cleared && g.out.sites[0].clearedAtGame == 1000);
    CHECK(g.out.sites[0].respawnAtGame == 1000 + 72ull * 3600 * 1000);
    CHECK(g.roll(mkReport(d, 0, 0, 2, {created(0x77, 1, 0xFF000003u)}), 0).verdict == Verdict::NotDue);   // twin 6
    g.r.setGameTime(1000 + 72ull * 3600 * 1000 - 1);
    g.out.clear(); g.tick(300);
    CHECK(g.r.stateOf(d.id) == SiteState::Cleared && g.out.sites.empty());
    g.r.setGameTime(1000 + 72ull * 3600 * 1000);
    g.out.clear(); g.tick(300);
    CHECK(g.r.stateOf(d.id) == SiteState::Respawnable);
    CHECK(g.out.sites.size() == 1 && g.out.sites[0].state == SiteState::Respawnable);
    VerdictMsg again = g.roll(mkReport(d, 0, 0, 3, {created(0x77, 1, 0xFF000004u)}), 0);
    CHECK(again.verdict == Verdict::Accept);
    CHECK(g.out.sites.size() == 1 && g.out.sites[0].generation == 2);
    SiteId old = 0;
    CHECK(!g.r.siteOfMember(0xFF000001u, old));                // the dead generation's ids left the index
}

TEST(registry, a_per_site_respawn_time_overrides_the_default) {
    Reg g = twoPlayers(5, 9);
    SiteDef d = mkDef(SiteKind::Marker, 0x1000, 5, false, V(), 2);   // 2 game hours
    g.roll(mkReport(d, 0, 0, 1, {created(0x77, 1, 0xFF000001u)}), 0);
    g.r.setGameTime(500);
    g.r.onMemberDied(0xFF000001u, g.wall, g.out);
    SiteMsg m;
    REQUIRE(g.r.siteMsg(d.id, m));
    CHECK(m.respawnAtGame == 500 + 2ull * 3600 * 1000);
}

TEST(registry, a_cleared_interior_stays_cleared) {
    Reg g = twoPlayers(5, 9);
    SiteDef d = mkDef(SiteKind::Placed, 0x1000, 5, true);
    g.roll(mkReport(d, 0, 0, 1, {placed(0x500, 0x77)}), 0);
    g.r.setGameTime(10);
    g.r.onMemberDied(0x500, g.wall, g.out);
    CHECK(g.r.stateOf(d.id) == SiteState::Cleared);
    SiteMsg m;
    REQUIRE(g.r.siteMsg(d.id, m));
    CHECK(m.respawnAtGame == 0);                               // never
    g.r.setGameTime(10ull * 365 * 24 * 3600 * 1000);
    g.tick(1000);
    CHECK(g.r.stateOf(d.id) == SiteState::Cleared);
    VerdictMsg v = g.roll(mkReport(d, 0, 0, 2, {placed(0x500, 0x78)}), 0);
    CHECK(v.verdict == Verdict::ClearedPermanent);             // twin 5
    REQUIRE(v.members.size() == 1);
    CHECK(v.members[0].dead);
    g.out.clear();
    g.r.onCellReset(5, g.out);
    CHECK(g.r.stateOf(d.id) == SiteState::Cleared && g.out.sites.empty());   // twin 20: a cell reset does not bring an interior back
    g.r.forceRespawn(d.id, g.out);                             // a quest script can
    CHECK(g.r.stateOf(d.id) == SiteState::Respawnable);
    CHECK(g.roll(mkReport(d, 0, 0, 3, {placed(0x500, 0x78)}), 0).verdict == Verdict::Accept);
}

TEST(registry, a_host_cell_reset_makes_cleared_exteriors_respawnable_now) {
    Reg g = twoPlayers(5, 9);
    SiteDef d = mkDef(SiteKind::Marker, 0x1000, 5);
    SiteDef other = mkDef(SiteKind::Marker, 0x1001, 6);
    g.roll(mkReport(d, 0, 0, 1, {created(0x77, 1, 0xFF000001u)}), 0);
    g.r.setPlayerLoc(0, loc(6));
    g.roll(mkReport(other, 0, 0, 2, {created(0x77, 1, 0xFF000002u)}), 0);
    g.r.onMemberDied(0xFF000001u, g.wall, g.out);
    g.r.onMemberDied(0xFF000002u, g.wall, g.out);
    g.out.clear();
    g.r.onCellReset(5, g.out);
    CHECK(g.r.stateOf(d.id) == SiteState::Respawnable);
    CHECK(g.r.stateOf(other.id) == SiteState::Cleared);        // other cell
    CHECK(g.out.sites.size() == 1);
}

TEST(registry, a_nothing_roll_is_shared_like_any_other) {
    Reg g = twoPlayers(9, 5);
    SiteDef d = mkDef(SiteKind::Marker, 0x1000, 5);
    RollReport none = mkReport(d, 1, 0, 1, {});
    none.none = true;
    VerdictMsg v = g.roll(none);
    CHECK(v.verdict == Verdict::Accept);
    CHECK(g.r.stateOf(d.id) == SiteState::Cleared);            // twin 9
    CHECK(g.out.spawns.empty());
    // another machine's engine rolled something: it does not count
    g.join(2);
    g.r.setPlayerLoc(2, loc(5));
    CHECK(g.roll(mkReport(d, 1, 0, 2, {created(0x77, 1)})).verdict == Verdict::NotDue);
}

TEST(registry, ambient_rolls_next_to_each_other_are_one_encounter) {
    Reg g = twoPlayers(9, 5);
    SiteDef a = mkDef(SiteKind::Ambient, 0, 5, false, V(2040, 100));
    a.anchor = FormRef(); a.id = ambientSiteId(5, 2040, 100);
    SiteDef b = a; b.pos = V(2060, 100); b.id = ambientSiteId(5, 2060, 100);   // across the grid line
    REQUIRE(a.id != b.id);
    VerdictMsg v1 = g.roll(mkReport(a, 1, 0, 1, {created(0x77, 1)}));
    REQUIRE(v1.verdict == Verdict::Accept);
    VerdictMsg v2 = g.roll(mkReport(b, 1, 0, 2, {created(0x77, 2)}));
    CHECK(v2.verdict == Verdict::AlreadySpawned);              // twin 18
    CHECK(v2.site == a.id);
    CHECK(g.r.siteCount() == 1);
    // far away is another encounter
    SiteDef c = a; c.pos = V(2040 + 5000, 100); c.id = ambientSiteId(5, c.pos.x, c.pos.y);
    CHECK(g.roll(mkReport(c, 1, 0, 3, {created(0x77, 3)})).verdict == Verdict::Accept);
    CHECK(g.r.siteCount() == 2);
}

TEST(registry, two_sites_cannot_claim_the_same_actor) {
    Reg g = twoPlayers(5, 9);
    SiteDef a = mkDef(SiteKind::Placed, 0x1000, 5), b = mkDef(SiteKind::Placed, 0x1001, 5);
    CHECK(g.roll(mkReport(a, 0, 0, 1, {placed(0x500, 0x77)}), 0).verdict == Verdict::Accept);
    CHECK(g.roll(mkReport(b, 0, 0, 2, {placed(0x500, 0x77)}), 0).verdict == Verdict::BadReport);   // twin 25
    CHECK(g.r.stateOf(b.id) == SiteState::Unrolled);
}

TEST(registry, site_ownership_follows_the_players_and_commits_at_once) {
    Reg g = twoPlayers(5, 5);
    SiteDef d = mkDef(SiteKind::Marker, 0x1000, 5);
    g.roll(mkReport(d, 0, 0, 1, {created(0x77, 1, 0xFF000001u)}), 0);
    CHECK(g.r.ownerOf(d.id) == 0);
    const OwnerEpoch e1 = g.r.epochOf(d.id);
    g.r.setPlayerLoc(0, loc(77));                              // the host walks away; client 1 is still here
    g.out.clear();
    g.tick(2000);
    CHECK(g.r.ownerOf(d.id) == 1);                             // twin 22: still waiting for an Ack nobody sends
    CHECK(g.r.epochOf(d.id) > e1);
    bool sawTransfer = false, sawCommit = false;
    for (auto& m : g.out.own) { if (m.netId == d.id && m.kind == OwnMsg::Transfer) sawTransfer = true; if (m.netId == d.id && m.kind == OwnMsg::Commit) sawCommit = true; }
    CHECK(sawTransfer && sawCommit);
    // the old owner cannot roll any more; the new one can (after the first generation is cleared)
    g.r.setGameTime(1);
    g.r.onMemberDied(0xFF000001u, g.wall, g.out);
    g.r.setGameTime(80ull * 3600 * 1000);
    g.tick(100);
    CHECK(g.roll(mkReport(d, 0, 0, 2, {created(0x77, 1, 0xFF000002u)}), 0).verdict == Verdict::NotOwner);
    CHECK(g.roll(mkReport(d, 1, 0, 1, {created(0x77, 1)}), 1).verdict == Verdict::Accept);
}

TEST(registry, a_roll_proves_the_cell_is_loaded_and_the_proof_survives_pose_updates) {
    Reg g;
    g.join(1); g.join(2);
    g.r.setPlayerLoc(0, loc(99));
    g.r.setPlayerLoc(1, locBlind(9));                          // both clients stand in cell 9 ...
    g.r.setPlayerLoc(2, locBlind(9));
    SiteDef d = mkDef(SiteKind::Marker, 0x1000, 5);
    CHECK(g.roll(mkReport(d, 1, 0, 1, {created(0x77, 1)})).verdict == Verdict::Accept);   // ... but both have cell 5 loaded (client 1 rolled first)
    CHECK(g.roll(mkReport(mkDef(SiteKind::Marker, 0x1001, 5), 2, 0, 1, {created(0x77, 1)})).verdict == Verdict::NotOwner);   // client 2 proves it too (refused: client 1 is first)
    g.r.setPlayerLoc(1, locBlind(9));                          // the avatar layer keeps reporting poses: the proof must not be wiped
    g.r.setPlayerLoc(2, locBlind(9));
    g.tick(300);
    CHECK(g.r.ownerOf(d.id) == 1);
    g.out.clear();
    g.r.playerLeft(1, g.wall, g.out);
    CHECK(g.r.ownerOf(d.id) == 2);                             // twin 28: nobody is eligible any more, the site is left without an owner
}

TEST(registry, a_player_leaving_releases_and_reassigns_their_sites) {
    Reg g = twoPlayers(5, 5);
    g.r.setPlayerLoc(0, loc(99));                              // host elsewhere, client 1 owns
    SiteDef d = mkDef(SiteKind::Marker, 0x1000, 5);
    g.roll(mkReport(d, 1, 0, 1, {created(0x77, 1)}));
    CHECK(g.r.ownerOf(d.id) == 1);
    g.join(2);
    g.r.setPlayerLoc(2, loc(5));
    g.out.clear();
    g.r.playerLeft(1, g.wall, g.out);
    CHECK(g.r.ownerOf(d.id) == 2);
    CHECK(!g.out.own.empty());
    CHECK(g.r.stateOf(d.id) == SiteState::Spawned);            // the site's state survives its owner
}

TEST(registry, a_script_spawn_is_tracked_as_a_site) {
    Reg g = twoPlayers(5, 9);
    SiteDef d = mkDef(SiteKind::Script, 0x7777, 5);
    fnvcoop::SpawnRef sp;
    sp.id = 0xFF000020u; sp.base = F(0x80); sp.cell = d.cellForm; sp.x = 1; sp.y = 2; sp.z = 3;
    g.out.clear();
    g.r.noteScriptSpawn(d, sp, g.wall, g.out);
    CHECK(g.r.stateOf(d.id) == SiteState::Spawned);
    CHECK(g.out.spawns.empty());                               // the effect already went out
    g.r.noteScriptSpawn(d, sp, g.wall, g.out);                 // the same capture again
    SiteMsg m;
    REQUIRE(g.r.siteMsg(d.id, m));
    CHECK(m.members.size() == 1 && m.generation == 1);
    sp.id = 0xFF000021u;
    g.r.noteScriptSpawn(d, sp, g.wall, g.out);
    REQUIRE(g.r.siteMsg(d.id, m));
    CHECK(m.members.size() == 2 && m.generation == 1);         // the group grows within a generation
    g.r.onMemberDied(0xFF000020u, g.wall, g.out);
    g.r.onMemberDied(0xFF000021u, g.wall, g.out);
    CHECK(g.r.stateOf(d.id) == SiteState::Cleared);
    // another machine's roll of that same encounter does not count while it is cleared
    CHECK(g.roll(mkReport(d, 0, 0, 1, {created(0x80, 1, 0xFF000022u)}), 0).verdict == Verdict::NotDue);
}

TEST(registry, the_join_table_carries_every_rolled_site_and_the_owners) {
    Reg g = twoPlayers(5, 9);
    SiteDef a = mkDef(SiteKind::Marker, 0x1000, 5), b = mkDef(SiteKind::Placed, 0x1001, 5, true), c = mkDef(SiteKind::Marker, 0x1002, 5);
    g.roll(mkReport(a, 0, 0, 1, {created(0x77, 1, 0xFF000001u)}), 0);
    g.roll(mkReport(b, 0, 0, 2, {placed(0x500, 0x77)}), 0);
    g.r.onMemberDied(0x500, g.wall, g.out);                    // b is cleared
    g.roll(mkReport(c, 0, 0, 3, {created(0x77, 1, 0xFF000003u)}), 0);
    SiteRegistry::Out snap;
    g.r.snapshot(snap);
    CHECK(snap.sites.size() == 3);                             // twin 23: the cleared one is missing
    CHECK(!snap.own.empty());
    SiteView v(1);
    for (auto& s : snap.sites) v.apply(s);
    for (auto& o : snap.own) v.apply(o);
    CHECK(v.digest() == g.r.digest());
    CHECK(v.stateOf(b.id) == SiteState::Cleared);
    CHECK(v.ownerOf(a.id) == 0);
}

TEST(registry, the_digest_tells_states_apart) {
    Reg g = twoPlayers(5, 9);
    SiteDef a = mkDef(SiteKind::Marker, 0x1000, 5);
    g.roll(mkReport(a, 0, 0, 1, {created(0x77, 1, 0xFF000001u)}), 0);
    const uint64_t spawned = g.r.digest();
    g.r.onMemberDied(0xFF000001u, g.wall, g.out);
    CHECK(g.r.digest() != spawned);                            // twin 21: Cleared hashes like Spawned
}

TEST(registry, save_and_load_keep_the_table_and_never_reuse_an_id) {
    Reg g = twoPlayers(9, 5);
    SiteDef a = mkDef(SiteKind::Marker, 0x1000, 5), b = mkDef(SiteKind::Placed, 0x1001, 5, true);
    VerdictMsg va = g.roll(mkReport(a, 1, 0, 1, {created(0x77, 1), created(0x78, 2)}));
    g.roll(mkReport(b, 1, 0, 2, {placed(0x500, 0x77)}));
    g.r.setGameTime(777);
    g.r.onMemberDied(0x500, g.wall, g.out);
    // a refused roll burns the ids it issued while it was checked (member 3 collides with site b's actor): the counter moved on anyway
    SiteDef burn = mkDef(SiteKind::Marker, 0x2001, 5);
    CHECK(g.roll(mkReport(burn, 1, 0, 3, {created(0x77, 1), created(0x78, 2), placed(0x500, 0x79)})).verdict == Verdict::BadReport);
    const NetId counter = g.r.issuer().peek();
    CHECK(counter > std::max(va.members[0].netId, va.members[1].netId) + 1);
    const uint64_t before = g.r.digest();
    fnvcoop::Archive w = fnvcoop::Archive::writer();
    g.r.save(w);
    std::vector<uint8_t> bytes = w.finish();
    REQUIRE(!bytes.empty());
    Reg h = twoPlayers(9, 5);
    fnvcoop::Archive rd = fnvcoop::Archive::reader(bytes.data(), bytes.size());
    REQUIRE(h.r.load(rd));
    CHECK(h.r.issuer().peek() >= counter);
    CHECK(h.r.digest() == before);
    CHECK(h.r.stateOf(b.id) == SiteState::Cleared && h.r.stateOf(a.id) == SiteState::Spawned);
    CHECK(h.r.gameTime() == 777);
    SiteMsg mb;
    REQUIRE(h.r.siteMsg(b.id, mb));
    CHECK(mb.def.interior && mb.respawnAtGame == 0);
    SiteId s = 0;
    CHECK(h.r.siteOfMember(va.members[0].netId, s) && s == a.id);
    // a fresh roll on a new site must not get an id the old timeline already used
    h.r.setPlayerLoc(1, loc(5));
    VerdictMsg vn = h.roll(mkReport(mkDef(SiteKind::Marker, 0x2000, 5), 1, 0, 1, {created(0x77, 1)}));
    REQUIRE(vn.verdict == Verdict::Accept);
    CHECK(vn.members[0].netId >= counter);                     // twin 24: the counter restarted at the base, so ids of the old timeline come back
    // truncated / corrupt saves fail cleanly
    for (size_t n = 0; n + 1 < bytes.size(); n += 7) {
        Reg x;
        fnvcoop::Archive trunc = fnvcoop::Archive::reader(bytes.data(), n);
        CHECK(!x.r.load(trunc));
    }
}

TEST(registry, a_duplicate_older_than_the_verdict_cache_is_dropped_not_re_run) {
    Config cfg;
    cfg.verdictCacheKeep = 8;
    Reg g(cfg);
    g.join(1);
    g.r.setPlayerLoc(0, loc(99)); g.r.setPlayerLoc(1, loc(5));
    SiteDef d = mkDef(SiteKind::Marker, 0x1000, 5);
    RollReport first = mkReport(d, 1, 0, 1, {created(0x77, 1)});
    REQUIRE(g.roll(first).verdict == Verdict::Accept);
    for (int i = 0; i < 12; ++i)                                 // 12 more rolls push the first verdict out of the 8-deep cache
        g.roll(mkReport(mkDef(SiteKind::Marker, 0x4000 + i, 5), 1, 0, static_cast<uint32_t>(2 + i), {created(0x77, 1)}));
    SiteMsg m;
    REQUIRE(g.r.siteMsg(d.id, m));
    g.r.onMemberDied(m.members[0].netId, g.wall, g.out);        // the first site is cleared ...
    g.r.setGameTime(80ull * 3600 * 1000);
    g.tick(100);
    CHECK(g.r.stateOf(d.id) == SiteState::Respawnable);         // ... and due: a re-run of the old roll would now be accepted
    const uint64_t accepted = g.r.stats().accepted;
    VerdictMsg late = g.roll(first);
    CHECK(late.verdict == Verdict::BadReport);                  // twin 27
    CHECK(g.r.stats().accepted == accepted && g.out.spawns.empty());
}

// ---- view ---------------------------------------------------------------------------------------------------------------------
namespace {
SiteMsg siteMsg(const SiteDef& d, SiteState st, uint32_t gen, uint32_t rev, std::vector<Member> ms, uint32_t pick = 0) {
    SiteMsg m;
    m.def = d; m.state = st; m.generation = gen; m.rev = rev; m.members = std::move(ms);
    if (pick) m.pick = F(pick);
    return m;
}
}  // namespace

TEST(view, newer_records_replace_older_ones_and_replays_change_nothing) {
    SiteView v(1);
    SiteDef d = mkDef(SiteKind::Marker, 0x1000, 5);
    CHECK(v.apply(siteMsg(d, SiteState::Spawned, 1, 1, {placed(0x500, 1)})) == fnvcoop::ApplyResult::Applied);
    CHECK(v.apply(siteMsg(d, SiteState::Spawned, 1, 1, {placed(0x500, 1)})) == fnvcoop::ApplyResult::Duplicate);
    CHECK(v.apply(siteMsg(d, SiteState::Cleared, 1, 2, {placed(0x500, 1)})) == fnvcoop::ApplyResult::Applied);
    CHECK(v.apply(siteMsg(d, SiteState::Spawned, 1, 1, {placed(0x500, 1)})) == fnvcoop::ApplyResult::Stale);
    CHECK(v.stateOf(d.id) == SiteState::Cleared);              // twin 12: the old record won
    CHECK(v.stats().sitesStale == 1 && v.stats().sitesDuplicate == 1);
}

TEST(view, a_local_roll_is_judged_where_the_answer_is_already_known) {
    SiteView v(1);
    SiteDef d = mkDef(SiteKind::Marker, 0x1000, 5);
    SiteDef perm = mkDef(SiteKind::Placed, 0x1001, 5, true);
    RollReport r = mkReport(d, 1, 0, 0, {created(0x77, 1)});
    CHECK(v.localRoll(r).kind == SiteView::LocalKind::Send);                 // unknown site: ask the host
    v.apply(siteMsg(d, SiteState::Spawned, 1, 1, {created(0x77, 1, 0xFFC00001u)}, 0x70));
    SiteView::LocalDecision a = v.localRoll(r);
    CHECK(a.kind == SiteView::LocalKind::Adopt && a.why == Verdict::AlreadySpawned);
    CHECK(a.site.pick == F(0x70) && a.site.members.size() == 1);
    v.apply(siteMsg(d, SiteState::Cleared, 1, 2, {created(0x77, 1, 0xFFC00001u)}));
    SiteView::LocalDecision s = v.localRoll(r);
    CHECK(s.kind == SiteView::LocalKind::Suppress && s.why == Verdict::NotDue);   // twin 13
    v.apply(siteMsg(perm, SiteState::Cleared, 1, 1, {placed(0x500, 1)}));
    CHECK(v.localRoll(mkReport(perm, 1, 0, 0, {placed(0x500, 2)})).why == Verdict::ClearedPermanent);
    v.apply(siteMsg(d, SiteState::Respawnable, 1, 3, {created(0x77, 1, 0xFFC00001u)}));
    CHECK(v.localRoll(r).kind == SiteView::LocalKind::Send);                 // respawn due: the owner's roll counts, ask the host
}

TEST(view, directives_say_what_a_placed_ref_must_look_like) {
    SiteView v(1);
    SiteDef d = mkDef(SiteKind::Placed, 0x1000, 5);
    Member alive = placed(0x500, 0x77), dead = placed(0x501, 0x78);
    dead.dead = true;
    v.apply(siteMsg(d, SiteState::Spawned, 1, 1, {alive, dead, created(0x79, 3, 0xFFC00002u)}));
    std::vector<Directive> ds = v.directivesFor(d.id);
    REQUIRE(ds.size() == 2);                                   // created members arrive as SpawnRef effects, not directives
    CHECK(ds[0].kind == Directive::Rebase && ds[0].netId == 0x500 && ds[0].base == F(0x77));
    CHECK(ds[1].kind == Directive::EnsureDead && ds[1].netId == 0x501);
    v.apply(siteMsg(d, SiteState::Cleared, 1, 2, {alive, dead}));
    ds = v.directivesFor(d.id);
    REQUIRE(ds.size() == 2);
    CHECK(ds[0].kind == Directive::EnsureDead && ds[1].kind == Directive::EnsureDead);
    CHECK(v.directivesFor(0xDEAD).empty());
    CHECK(v.sitesInCell(5).size() == 1 && v.sitesInCell(6).empty());
}

TEST(view, ownership_messages_apply_idempotently) {
    SiteView v(1);
    OwnMsg g; g.kind = OwnMsg::Grant; g.netId = 77; g.epoch = 2; g.owner = 1;
    CHECK(v.apply(g) == fnvcoop::ApplyResult::Applied);
    CHECK(v.iOwn(77) && v.epochOf(77) == 2);
    CHECK(v.apply(g) == fnvcoop::ApplyResult::Duplicate);
    OwnMsg old = g; old.epoch = 1; old.owner = 0;
    CHECK(v.apply(old) == fnvcoop::ApplyResult::Stale);
    CHECK(v.iOwn(77));
}

// ---- tracker ------------------------------------------------------------------------------------------------------------------
namespace {
VerdictMsg verdictFor(const RollReport& r, Verdict v, std::vector<Member> ms = {}, PlayerId owner = 0, OwnerEpoch ep = 1) {
    VerdictMsg m;
    m.site = r.def.id; m.rollId = r.rollId; m.verdict = v; m.owner = owner; m.epoch = ep; m.members = std::move(ms);
    return m;
}
int count(const std::vector<Action>& a, Action::Kind k) { int n = 0; for (auto& x : a) if (x.kind == k) ++n; return n; }
const Action* findAct(const std::vector<Action>& a, Action::Kind k, uint32_t handle) { for (auto& x : a) if (x.kind == k && x.handle == handle) return &x; return nullptr; }
const std::function<bool(NetId)> kNone = [](NetId) { return false; };
}  // namespace

TEST(tracker, an_accepted_roll_binds_the_issued_ids_and_shows_the_actors) {
    RollTracker t(1);
    SiteDef d = mkDef(SiteKind::Marker, 0x1000, 5);
    RollReport r = mkReport(d, 0, 0, 0, {created(0x77, 11), created(0x78, 12), placed(0x500, 0x79, 13)});
    const uint32_t id = t.begin(r, 100);
    CHECK(id == r.rollId && r.roller == 1 && id != 0 && t.pending() == 1);
    std::vector<Member> vm = r.members;
    vm[0].netId = 0xFFC00001u; vm[1].netId = 0xFFC00002u;
    std::vector<Action> a = t.onVerdict(verdictFor(r, Verdict::Accept, vm), kNone, 110);
    CHECK(t.pending() == 0);
    REQUIRE(count(a, Action::Bind) == 2);
    CHECK(count(a, Action::Show) == 3);
    CHECK(findAct(a, Action::Bind, 11)->netId == 0xFFC00001u && findAct(a, Action::Bind, 12)->netId == 0xFFC00002u);
    CHECK(count(a, Action::Drop) == 0);
    // the same verdict again is ignored
    CHECK(t.onVerdict(verdictFor(r, Verdict::Accept, vm), kNone, 120).empty());
    CHECK(t.stats().unknownVerdicts == 1);
}

TEST(tracker, a_spawnref_that_beat_the_verdict_makes_our_copy_the_extra_one) {
    RollTracker t(1);
    SiteDef d = mkDef(SiteKind::Marker, 0x1000, 5);
    RollReport r = mkReport(d, 0, 0, 0, {created(0x77, 11)});
    t.begin(r, 0);
    std::vector<Member> vm = r.members;
    vm[0].netId = 0xFFC00001u;
    std::vector<Action> a = t.onVerdict(verdictFor(r, Verdict::Accept, vm), [](NetId id) { return id == 0xFFC00001u; }, 10);
    CHECK(count(a, Action::Drop) == 1 && count(a, Action::Bind) == 0);       // twin 14
}

TEST(tracker, a_refused_roll_is_turned_off) {
    RollTracker t(1);
    SiteDef d = mkDef(SiteKind::Marker, 0x1000, 5);
    for (Verdict why : {Verdict::NotOwner, Verdict::AlreadySpawned, Verdict::NotDue, Verdict::ClearedPermanent, Verdict::BadReport}) {
        RollReport r = mkReport(d, 0, 0, 0, {created(0x77, 11), created(0x78, 12)});
        t.begin(r, 0);
        std::vector<Action> a = t.onVerdict(verdictFor(r, why), kNone, 10);
        CHECK(count(a, Action::Drop) == 2);                                    // twin 15
        CHECK(t.pending() == 0);
    }
}

TEST(tracker, a_refused_placed_ref_follows_the_shared_result) {
    RollTracker t(1);
    SiteDef d = mkDef(SiteKind::Placed, 0x1000, 5);
    RollReport r = mkReport(d, 0, 0, 0, {placed(0x500, 0x77, 21), placed(0x501, 0x78, 22)});
    t.begin(r, 0);
    Member shared = placed(0x500, 0x99), corpse = placed(0x501, 0x98);
    corpse.dead = true;
    std::vector<Action> a = t.onVerdict(verdictFor(r, Verdict::AlreadySpawned, {shared, corpse}), kNone, 5);
    REQUIRE(findAct(a, Action::Rebase, 21));
    CHECK(findAct(a, Action::Rebase, 21)->base == F(0x99));
    CHECK(findAct(a, Action::EnsureDead, 22));
    CHECK(count(a, Action::Show) == 2);
    CHECK(count(a, Action::Drop) == 0);                                        // a placed ref is never deleted
}

TEST(tracker, a_placed_ref_waits_for_the_owner_and_follows_the_owners_record) {
    RollTracker t(1);
    SiteDef d = mkDef(SiteKind::Placed, 0x1000, 5);
    RollReport r = mkReport(d, 0, 0, 0, {placed(0x500, 0x77, 21), placed(0x501, 0x78, 22)});
    t.begin(r, 0);
    std::vector<Action> a = t.onVerdict(verdictFor(r, Verdict::NotOwner), kNone, 5);
    CHECK(a.empty());                                                          // stays hidden
    CHECK(t.waiting() == 2);
    // the owner's record: 0x500 rolled to base 0x99, 0x501 is not part of its result
    std::vector<Action> b = t.onSite(siteMsg(d, SiteState::Spawned, 1, 1, {placed(0x500, 0x99)}), 50);
    CHECK(findAct(b, Action::Rebase, 21) && findAct(b, Action::Rebase, 21)->base == F(0x99));
    CHECK(findAct(b, Action::Show, 21));
    CHECK(!findAct(b, Action::Show, 22));                                      // not in the owner's roll: stays hidden
    CHECK(t.waiting() == 0);
    // a record for a different site changes nothing
    RollReport r2 = mkReport(d, 0, 0, 0, {placed(0x600, 0x77, 31)});
    t.begin(r2, 0);
    t.onVerdict(verdictFor(r2, Verdict::NotOwner), kNone, 5);
    CHECK(t.onSite(siteMsg(mkDef(SiteKind::Placed, 0x9999, 5), SiteState::Spawned, 1, 1, {placed(0x600, 1)}), 50).empty());
    CHECK(t.waiting() == 1);
}

TEST(tracker, a_placed_ref_nobody_answers_for_is_shown_after_the_timeout) {
    RollTracker t(1);
    SiteDef d = mkDef(SiteKind::Placed, 0x1000, 5);
    RollReport r = mkReport(d, 0, 0, 0, {placed(0x500, 0x77, 21)});
    t.begin(r, 0);
    t.onVerdict(verdictFor(r, Verdict::NotOwner), kNone, 5);
    CHECK(t.tick(1000).empty());                                               // starts the clock
    CHECK(t.tick(5000).empty());
    std::vector<Action> a = t.tick(1000 + 12000);
    CHECK(count(a, Action::Show) == 1);                                        // twin 26: hidden for good
    CHECK(t.waiting() == 0);
}

TEST(tracker, a_stale_epoch_from_the_current_owner_rerolls_under_the_new_epoch) {
    RollTracker t(1);
    SiteDef d = mkDef(SiteKind::Marker, 0x1000, 5);
    RollReport r = mkReport(d, 0, 2, 0, {created(0x77, 11)});
    const uint32_t first = t.begin(r, 0);
    std::vector<Action> a = t.onVerdict(verdictFor(r, Verdict::StaleEpoch, {}, 1, 5), kNone, 10);
    REQUIRE(a.size() == 1 && a[0].kind == Action::Resend);
    CHECK(a[0].report.epoch == 5 && a[0].report.rollId != first && a[0].report.members.size() == 1);
    CHECK(t.pending() == 1);
    CHECK(count(a, Action::Drop) == 0);                                        // the local actor stays held while the retry is out
    // the retry itself is stale again, then once more: at most maxStaleRetries re-rolls, then it is turned off
    RollReport r2 = a[0].report;
    a = t.onVerdict(verdictFor(r2, Verdict::StaleEpoch, {}, 1, 6), kNone, 20);
    REQUIRE(a.size() == 1 && a[0].kind == Action::Resend);
    RollReport r3 = a[0].report;
    a = t.onVerdict(verdictFor(r3, Verdict::StaleEpoch, {}, 1, 7), kNone, 30);
    CHECK(count(a, Action::Drop) == 1 && count(a, Action::Resend) == 0);
    CHECK(t.pending() == 0);
    // a stale epoch where somebody else owns the site is just a refusal
    RollReport r4 = mkReport(d, 0, 2, 0, {created(0x77, 11)});
    t.begin(r4, 0);
    a = t.onVerdict(verdictFor(r4, Verdict::StaleEpoch, {}, 0, 5), kNone, 10);
    CHECK(count(a, Action::Drop) == 1 && count(a, Action::Resend) == 0);
}

TEST(tracker, a_refusal_for_a_non_owner_is_final) {
    RollTracker t(1);
    SiteDef d = mkDef(SiteKind::Marker, 0x1000, 5);
    RollReport r = mkReport(d, 0, 0, 0, {created(0x77, 11)});
    t.begin(r, 0);
    std::vector<Action> a = t.onVerdict(verdictFor(r, Verdict::NotOwner, {}, 0, 1), kNone, 10);
    CHECK(count(a, Action::Resend) == 0 && t.pending() == 0);                  // twin 16: sent again and again
}

TEST(tracker, silent_rolls_are_resent_then_given_up) {
    RollTracker t(1);
    SiteDef d = mkDef(SiteKind::Marker, 0x1000, 5);
    RollReport r = mkReport(d, 0, 0, 0, {created(0x77, 11), placed(0x500, 0x78, 12)});
    t.begin(r, 1000);
    CHECK(t.tick(1500).empty());
    std::vector<Action> a = t.tick(2600);
    REQUIRE(a.size() == 1 && a[0].kind == Action::Resend);
    CHECK(a[0].report.rollId == r.rollId);                                     // same rollId: the host answers a replay with the same verdict
    CHECK(t.tick(2700).empty());
    a = t.tick(1000 + 12000);
    CHECK(count(a, Action::Drop) == 1 && count(a, Action::Show) == 1);         // created: gone, placed: shown as the engine made it
    CHECK(t.pending() == 0 && t.stats().gaveUp == 1);
}

TEST(tracker, a_lost_link_releases_everything_that_was_held) {
    RollTracker t(1);
    SiteDef d = mkDef(SiteKind::Marker, 0x1000, 5);
    RollReport r = mkReport(d, 0, 0, 0, {created(0x77, 11), created(0x78, 12)});
    t.begin(r, 0);
    RollReport p = mkReport(mkDef(SiteKind::Placed, 0x1001, 5), 0, 0, 0, {placed(0x500, 0x78, 21)});
    t.begin(p, 0);
    t.onVerdict(verdictFor(p, Verdict::NotOwner), kNone, 1);
    std::vector<Action> a = t.abandon();
    CHECK(count(a, Action::Show) == 3 && count(a, Action::Drop) == 0);
    CHECK(t.pending() == 0 && t.waiting() == 0);
}

// ---- storm: random ops against the registry with invariants after every step ----------------------------------------------------
namespace {
struct Known { RollReport rep; PlayerId from = 0; VerdictMsg verdict; };
}

TEST(storm, the_registry_keeps_its_invariants_under_random_traffic) {
    uint64_t totalAccepted = 0, totalRejected = 0;
    for (uint64_t seed = 1; seed <= 40; ++seed) {
        fnvcoop::Rng rng(seed);
        Config cfg;
        Reg g(cfg);
        g.join(1); g.join(2); g.join(3);
        std::vector<SiteDef> sites;
        for (int i = 0; i < 12; ++i) {
            const SiteKind k = static_cast<SiteKind>(rng.range(0, 3));
            const CellId cell = static_cast<CellId>(rng.range(1, 4));
            sites.push_back(mkDef(k, 0x1000 + i, cell, rng.chance(0.3), V(static_cast<float>(rng.range(0, 3000)), static_cast<float>(rng.range(0, 3000)))));
        }
        SiteView view(kNoPlayer);
        std::set<NetId> allIssued, allHostIds;
        std::vector<Known> history;
        std::map<PlayerId, uint32_t> nextRoll;
        std::set<SiteId> forced;
        uint32_t hostIdCounter = 0xFF000100u, placedCounter = 0x2000;
        std::map<SiteId, std::vector<NetId>> placedOf;
        for (auto& s : sites) if (s.kind == SiteKind::Placed) { for (int k = 0; k < 2; ++k) placedOf[s.id].push_back(placedCounter++); }
        auto feed = [&]() {
            for (auto& o : g.out.own) view.apply(o);
            for (auto& s : g.out.sites) view.apply(s);
        };
        for (int step = 0; step < 1500; ++step) {
            g.out.clear();
            const uint32_t op = rng.range(0, 99);
            if (op < 12) {                                                  // a player moves
                const PlayerId p = static_cast<PlayerId>(rng.range(0, 3));
                if (rng.chance(0.1)) g.r.setPlayerLoc(p, PlayerLoc());
                else g.r.setPlayerLoc(p, loc(static_cast<CellId>(rng.range(1, 5)), rng.chance(0.3) ? std::vector<CellId>{static_cast<CellId>(rng.range(1, 5))} : std::vector<CellId>{}));
            } else if (op < 22) {                                           // time passes
                g.r.setGameTime(g.r.gameTime() + rng.range(0, 40) * 3600ull * 1000ull);
                g.tick(rng.range(100, 3000));
            } else if (op < 70) {                                           // a roll (honest, stale, replayed, spoofed)
                if (!history.empty() && rng.chance(0.15)) {                 // replay an old report exactly
                    const Known& k = history[rng.range(0, static_cast<uint32_t>(history.size() - 1))];
                    g.out.clear();
                    const uint64_t acceptedBefore = g.r.stats().accepted;
                    g.r.onRoll(k.rep, k.from, g.wall, g.out);
                    CHECK(g.out.spawns.empty());                                          // a replay never spawns anything
                    CHECK(g.out.sites.empty());
                    CHECK(g.r.stats().accepted == acceptedBefore);
                    feed();
                    continue;
                }
                const SiteDef& d = sites[rng.range(0, static_cast<uint32_t>(sites.size() - 1))];
                const PlayerId from = static_cast<PlayerId>(rng.range(0, 3));
                RollReport rep;
                rep.def = d;
                rep.roller = rng.chance(0.03) ? static_cast<PlayerId>((from + 1) % 4) : from;   // occasionally spoofed
                const OwnerEpoch ep = g.r.epochOf(d.id);
                switch (rng.range(0, 5)) { case 0: rep.epoch = 0; break; case 1: rep.epoch = ep > 1 ? ep - 1 : ep + 2; break; default: rep.epoch = ep ? ep : 0; }
                rep.rollId = ++nextRoll[from];
                const uint32_t n = rng.chance(0.1) ? 0 : rng.range(1, 3);
                rep.none = n == 0;
                for (uint32_t i = 0; i < n; ++i) {
                    if (d.kind == SiteKind::Placed) rep.members.push_back(placed(placedOf[d.id][i % 2], 0x70 + i, 100 + i));
                    else if (from == kHostPlayer) rep.members.push_back(created(0x70 + i, 100 + i, hostIdCounter++));
                    else rep.members.push_back(created(0x70 + i, 100 + i));
                }
                const SiteState pre = g.r.stateOf(d.id);
                SiteMsg preMsg; const bool had = g.r.siteMsg(d.id, preMsg);
                bool liveBefore = false;
                if (had) for (auto& m : preMsg.members) if (!m.dead) liveBefore = true;
                g.out.clear();
                g.r.onRoll(rep, from, g.wall, g.out);
                Verdict vd = Verdict::Count_;
                VerdictMsg vmsg;
                for (auto& v : g.out.verdicts) if (v.to == from) { vd = v.msg.verdict; vmsg = v.msg; }
                REQUIRE(vd != Verdict::Count_);
                history.push_back({rep, from, vmsg});
                if (vd == Verdict::Accept) {
                    ++totalAccepted;
                    CHECK(rep.roller == from);
                    CHECK(g.r.ownerOf(vmsg.site) == from);                          // I4: only the owner's roll counts
                    CHECK(!(pre == SiteState::Spawned && liveBefore));              // I5: never a second live generation
                    CHECK(!(pre == SiteState::Cleared && d.interior && !forced.count(d.id)));   // I8: cleared interiors stay cleared
                    for (auto& m : vmsg.members) {
                        if (m.created) {
                            if (from == kHostPlayer) CHECK(allHostIds.insert(m.netId).second);
                            else { CHECK(isIssuedId(m.netId)); CHECK(allIssued.insert(m.netId).second); }
                        }
                    }
                    CHECK(g.out.spawns.size() == static_cast<size_t>(std::count_if(vmsg.members.begin(), vmsg.members.end(), [](const Member& m) { return m.created; })));
                } else {
                    ++totalRejected;
                    CHECK(g.out.spawns.empty());
                }
            } else if (op < 85) {                                           // a member dies
                if (!sites.empty()) {
                    const SiteDef& d = sites[rng.range(0, static_cast<uint32_t>(sites.size() - 1))];
                    SiteMsg m;
                    if (g.r.siteMsg(d.id, m) && !m.members.empty()) g.r.onMemberDied(m.members[rng.range(0, static_cast<uint32_t>(m.members.size() - 1))].netId, g.wall, g.out);
                }
            } else if (op < 90) {
                g.r.onCellReset(static_cast<CellId>(rng.range(1, 4)), g.out);
            } else if (op < 92) {
                const SiteDef& d = sites[rng.range(0, static_cast<uint32_t>(sites.size() - 1))];
                if (g.r.stateOf(d.id) == SiteState::Cleared) forced.insert(d.id);
                g.r.forceRespawn(d.id, g.out);
            } else if (op < 94) {                                           // host save + load
                const uint64_t d0 = g.r.digest();
                fnvcoop::Archive w = fnvcoop::Archive::writer();
                g.r.save(w);
                std::vector<uint8_t> bytes = w.finish();
                REQUIRE(!bytes.empty());
                fnvcoop::Archive rd = fnvcoop::Archive::reader(bytes.data(), bytes.size());
                REQUIRE(g.r.load(rd));
                history.clear();                                            // a host load starts a new timeline: clients rejoin, old reports are never replayed
                CHECK(g.r.digest() == d0);
                view.clear();
                SiteRegistry::Out snap;
                g.r.snapshot(snap);
                for (auto& s : snap.sites) view.apply(s);
                for (auto& o : snap.own) view.apply(o);
            } else {
                g.tick(rng.range(0, 600));
            }
            feed();
            // ---- invariants after every step ----
            std::set<NetId> ids;
            for (auto& d : sites) {
                SiteMsg m;
                if (!g.r.siteMsg(d.id, m)) continue;
                bool alive = false;
                for (auto& mem : m.members) {
                    CHECK(ids.insert(mem.netId).second);                        // I1: an actor belongs to one site
                    SiteId owner = 0;
                    CHECK(g.r.siteOfMember(mem.netId, owner) && owner == d.id);
                    if (!mem.dead) alive = true;
                }
                if (m.state == SiteState::Spawned) CHECK(alive);                // I3
                if (m.state == SiteState::Cleared && !m.none) CHECK(!alive);
                if (m.state == SiteState::Cleared && d.interior) CHECK(m.respawnAtGame == 0);
            }
        }
        // the view that heard every broadcast agrees with the host (it may know fewer unrolled sites, never different rolled ones)
        CHECK(view.digest() == g.r.digest());
    }
    CHECK(totalAccepted > 100);
    CHECK(totalRejected > 100);
}
