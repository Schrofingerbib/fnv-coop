// The social channel's datagrams: every message round-trips, anything else is refused, never a crash (twins 880..884).
#include <cmath>
#include <cstdlib>
#include "util.h"

using namespace social_test;

namespace {

std::vector<Body> everyBody() {
    std::vector<Body> v;
    v.push_back(MCrime{crime(1, CrimeKind::Murder, 1, faction(1))});
    v.push_back(MDetect{sees(4, faction(1), 2)});
    BountyUpdate u; u.faction = faction(1); u.st.bounty = 25; u.st.attack = true; u.st.rev = 2;
    v.push_back(MBounty{{u}, true});
    AlarmOrder a; a.id = 3; a.faction = faction(1); a.cell = 5; a.where = {1, 2, 3}; a.criminal = 1; a.untilMs = 123456;
    v.push_back(MAlarm{a});
    cc::HostilityEffect h; h.id = 77; h.kind = cc::HostilityKind::SetEnemy; h.factionA = playerFaction(); h.factionB = faction(1); h.cause = 1;
    v.push_back(MHostility{h});
    KillReport k; k.deathId = 9; k.victim.who = cc::Entity::actor(5); k.victim.xp = 50; k.victim.karma = KarmaClass::Good; k.killer = cc::Entity::player(1); k.reporter = 1;
    v.push_back(MKill{k});
    Companion c; c.npc = npc(2); c.owner = 1; c.state.rev = 3;
    v.push_back(MRoster{{c}});
    CompanionCmd cmd; cmd.id = 8; cmd.from = 1; cmd.npc = npc(2); cmd.kind = CmdKind::Wait;
    v.push_back(MCmd{cmd});
    v.push_back(MCmdNack{8, CmdReason::NotOwner});
    CompanionState st; st.mode = FollowMode::Wait; st.rev = 4;
    v.push_back(MState{npc(2), st});
    InvDelta d; d.txn = 5; d.npc = npc(2); d.item = item(1); d.delta = 3;
    v.push_back(MInv{d});
    InvFull f; f.npc = npc(2); f.items = {{item(1), 3}};
    v.push_back(MInvFull{f});
    v.push_back(MInvAsk{npc(2)});
    SocialSettings s; s.rev = 2; s.xpMode = cc::XpMode::PartyShare;
    v.push_back(MSettings{s});
    v.push_back(MDigest{0x1122334455667788ull, 0x99, 6});
    v.push_back(MResync{0});
    return v;
}

}  // namespace

TEST(wire, every_message_round_trips) {
    const std::vector<Body> all = everyBody();
    CHECK(all.size() == static_cast<size_t>(Kind::Count_) - 1);
    for (const Body& b : all) {
        Envelope e;
        e.src = 1; e.dest = kHostPlayer; e.body = b;
        Bytes wire = encodeEnvelope(e);
        REQUIRE(!wire.empty());
        CHECK(wire[0] == kChannelSocial && wire[1] == kSocialWireVersion);
        Envelope back;
        CHECK_MSG(decodeEnvelope(wire.data(), wire.size(), back), bodyName(b));
        CHECK(back.src == 1 && back.dest == kHostPlayer && back.body.index() == b.index());
        CHECK(encodeEnvelope(back) == wire);   // canonical: re-encoding gives the same bytes
        CHECK(static_cast<size_t>(kindOf(b)) == b.index() + 1);
    }
}

TEST(wire, fields_survive) {
    Envelope e;
    e.src = 2; e.dest = kNoPlayer;
    e.body = MState{npc(7), CompanionState{FollowMode::Wait, Stance::Passive, Range::Far, 11}};
    Bytes w = encodeEnvelope(e);
    Envelope b;
    REQUIRE(decodeEnvelope(w.data(), w.size(), b));
    const MState* s = std::get_if<MState>(&b.body);
    REQUIRE(s);
    CHECK(s->npc == npc(7) && s->st.mode == FollowMode::Wait && s->st.stance == Stance::Passive && s->st.range == Range::Far && s->st.rev == 11);
    CHECK(b.src == 2 && b.dest == kNoPlayer);
}

TEST(wire, wrong_channel_version_trailing_bytes_and_bad_slots_are_refused) {   // twins 880, 881, 882, 883
    Envelope e;
    e.src = 1; e.dest = kHostPlayer; e.body = MResync{0};
    Bytes w = encodeEnvelope(e);
    Envelope out;
    CHECK(decodeEnvelope(w.data(), w.size(), out));
    Bytes ch = w; ch[0] = 0xCB;
    CHECK(!decodeEnvelope(ch.data(), ch.size(), out));
    Bytes ver = w; ver[1] = static_cast<uint8_t>(kSocialWireVersion + 1);
    CHECK(!decodeEnvelope(ver.data(), ver.size(), out));
    Bytes trail = w; trail.push_back(0);
    CHECK(!decodeEnvelope(trail.data(), trail.size(), out));
    Envelope badSlot = e;
    badSlot.src = 9;
    Bytes bs = encodeEnvelope(badSlot);
    CHECK(!decodeEnvelope(bs.data(), bs.size(), out));
    Envelope badDest = e;
    badDest.dest = 12;
    Bytes bd = encodeEnvelope(badDest);
    CHECK(!decodeEnvelope(bd.data(), bd.size(), out));
    CHECK(!decodeEnvelope(nullptr, 0, out));
    CHECK(!decodeEnvelope(w.data(), 2, out));
}

TEST(wire, a_position_that_is_not_a_number_never_travels) {   // (the Archive refuses non finite floats itself: no twin of ours)
    CrimeEvent ev = crime(1, CrimeKind::Assault, 1, faction(1));
    ev.where.x = std::nanf("");
    Envelope e;
    e.src = 1; e.dest = kHostPlayer; e.body = MCrime{ev};
    CHECK(encodeEnvelope(e).empty());
    // ... and a hand-made datagram that carries one is dropped by the receiver
    ev.where.x = 1.0f;
    e.body = MCrime{ev};
    Bytes w = encodeEnvelope(e);
    REQUIRE(!w.empty());
    // find the 1.0f (0x3F800000 little endian) and replace it with a NaN (0x7FC00000)
    bool patched = false;
    for (size_t i = 0; i + 4 <= w.size(); ++i) {
        if (w[i] == 0x00 && w[i + 1] == 0x00 && w[i + 2] == 0x80 && w[i + 3] == 0x3F) { w[i + 2] = 0xC0; w[i + 3] = 0x7F; patched = true; break; }
    }
    REQUIRE(patched);
    Envelope out;
    CHECK(!decodeEnvelope(w.data(), w.size(), out));
    // settings radius
    SocialSettings s;
    s.nearRadius = INFINITY;
    e.body = MSettings{s};
    CHECK(encodeEnvelope(e).empty());
}

TEST(wire, truncation_bit_flips_and_garbage_never_crash) {
    const std::vector<Body> all = everyBody();
    uint64_t rng = 0x9E3779B97F4A7C15ull;
    auto next = [&]() { rng ^= rng << 13; rng ^= rng >> 7; rng ^= rng << 17; return rng; };
    size_t decoded = 0, tried = 0;
    for (const Body& b : all) {
        Envelope e;
        e.src = 1; e.dest = kHostPlayer; e.body = b;
        Bytes w = encodeEnvelope(e);
        for (size_t cut = 0; cut < w.size(); ++cut) {   // every truncation is refused
            Envelope o;
            CHECK(!decodeEnvelope(w.data(), cut, o));
        }
        for (int i = 0; i < 300; ++i) {                 // flipped bits: either refused or a valid message, never a crash
            Bytes m = w;
            m[next() % m.size()] ^= static_cast<uint8_t>(1u << (next() % 8));
            Envelope o;
            ++tried;
            if (decodeEnvelope(m.data(), m.size(), o)) { ++decoded; Bytes again = encodeEnvelope(o); (void)again; }
        }
    }
    for (int i = 0; i < 3000; ++i) {                    // random garbage behind a valid header
        Bytes g(2 + next() % 200);
        for (auto& x : g) x = static_cast<uint8_t>(next());
        g[0] = kChannelSocial; g[1] = kSocialWireVersion;
        Envelope o;
        ++tried;
        if (decodeEnvelope(g.data(), g.size(), o)) ++decoded;
    }
    CHECK(tried > 3000);
    std::printf("    wire fuzz: %zu tried, %zu decoded\n", tried, decoded);
}
