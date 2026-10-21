// plugin/vats: wire messages (round trip, malformed input, clamping of what a peer sends).
#include <cstring>
#include <limits>
#include "vats/vats_wire.h"
#include "minitest.h"

using namespace nvcoop::vats;

namespace {

Envelope roundTrip(const Envelope& e, bool* ok) {
    const Bytes b = encodeEnvelope(e);
    Envelope out;
    *ok = decodeEnvelope(b.data(), b.size(), out);
    return out;
}

TEST(vats_wire, every_message_round_trips) {
    bool ok = false;
    {
        Envelope e; e.src = 0; e.dest = 3; e.hops = 1;
        MConfig c;
        c.epoch = 9;
        c.s.realtime = true; c.s.partySlowMo = true; c.s.slowScale = 0.5f; c.s.slowMaxMs = 2500; c.s.slowCooldownMs = 7000; c.s.autoFire = false;
        c.s.autoFireDelayMs = 120; c.s.killCam = false; c.s.guardFriendly = false; c.s.perks = false;
        e.body = c;
        Envelope r = roundTrip(e, &ok);
        REQUIRE(ok);
        const MConfig* got = std::get_if<MConfig>(&r.body);
        REQUIRE(got != nullptr);
        CHECK(got->epoch == 9 && got->s == c.s);
        CHECK(r.src == 0 && r.dest == 3 && r.hops == 1);
    }
    {
        Envelope e; e.src = 2; e.dest = kNoPlayer;
        MPhase p; p.player = 2; p.mode = Mode::LimbSelect; p.limb = LimbId::LeftLeg; p.queued = 2; p.target = 77; p.seq = 12345;
        e.body = p;
        Envelope r = roundTrip(e, &ok);
        REQUIRE(ok);
        const MPhase* g = std::get_if<MPhase>(&r.body);
        REQUIRE(g != nullptr);
        CHECK(g->player == 2 && g->mode == Mode::LimbSelect && g->limb == LimbId::LeftLeg && g->queued == 2 && g->target == 77 && g->seq == 12345);
    }
    {
        Envelope e; e.src = 1;
        MAction a; a.shooter = 1; a.victim = 4242; a.limb = LimbId::Head; a.queued = 1; a.seq = 3;
        e.body = a;
        Envelope r = roundTrip(e, &ok);
        REQUIRE(ok);
        const MAction* g = std::get_if<MAction>(&r.body);
        REQUIRE(g != nullptr);
        CHECK(g->shooter == 1 && g->victim == 4242 && g->limb == LimbId::Head && g->queued == 1 && g->seq == 3);
    }
    {
        Envelope e; e.src = 0;
        MSlowMo m; m.g.epoch = 4; m.g.seq = 8; m.g.active = true; m.g.scale = 0.35f; m.g.remainingMs = 2750; m.g.cause = 1;
        e.body = m;
        Envelope r = roundTrip(e, &ok);
        REQUIRE(ok);
        const MSlowMo* g = std::get_if<MSlowMo>(&r.body);
        REQUIRE(g != nullptr);
        CHECK(g->g == m.g);
    }
    {
        Envelope e; e.src = 1;
        MPhase p; p.player = 1; p.mode = Mode::None; p.limb = LimbId::None;   // "no limb" travels as 255
        e.body = p;
        Envelope r = roundTrip(e, &ok);
        REQUIRE(ok);
        CHECK(std::get<MPhase>(r.body).limb == LimbId::None);
    }
}

TEST(vats_wire, the_datagram_starts_with_the_channel_byte_and_the_module_version) {
    Envelope e; e.src = 0; e.body = MPhase{};
    const Bytes b = encodeEnvelope(e);
    REQUIRE(b.size() > 3);
    CHECK(b[0] == kChannelVats);
    CHECK(b[1] == kVatsModuleWire);
    CHECK(kChannelVats == 0xB7);
}

TEST(vats_wire, trailing_bytes_a_wrong_channel_or_a_wrong_version_are_refused) {
    Envelope e; e.src = 0; e.body = MPhase{};
    Bytes b = encodeEnvelope(e);
    Envelope out;
    CHECK(decodeEnvelope(b.data(), b.size(), out));
    Bytes extra = b;
    extra.push_back(0x00);
    CHECK(!decodeEnvelope(extra.data(), extra.size(), out));
    Bytes ch = b; ch[0] = 0xCB;
    CHECK(!decodeEnvelope(ch.data(), ch.size(), out));
    Bytes ver = b; ver[1] = 2;
    CHECK(!decodeEnvelope(ver.data(), ver.size(), out));
    CHECK(!decodeEnvelope(nullptr, 0, out));
    CHECK(!decodeEnvelope(b.data(), 2, out));
}

TEST(vats_wire, every_truncation_of_every_message_is_refused_without_a_crash) {
    std::vector<Body> bodies;
    MConfig c; c.s.partySlowMo = true; bodies.push_back(c);
    MPhase p; p.player = 1; p.mode = Mode::Playback; p.limb = LimbId::Head; p.target = 5; p.seq = 7; bodies.push_back(p);
    MAction a; a.shooter = 1; a.victim = 9; a.limb = LimbId::Torso; a.seq = 2; bodies.push_back(a);
    MSlowMo s; s.g.active = true; s.g.scale = 0.4f; s.g.remainingMs = 1000; s.g.seq = 1; bodies.push_back(s);
    for (const Body& body : bodies) {
        Envelope e; e.src = 0; e.body = body;
        const Bytes full = encodeEnvelope(e);
        Envelope out;
        CHECK(decodeEnvelope(full.data(), full.size(), out));
        for (size_t n = 0; n < full.size(); ++n) CHECK_MSG(!decodeEnvelope(full.data(), n, out), bodyName(body) + std::string(" cut at ") + std::to_string(n));
    }
}

TEST(vats_wire, random_garbage_never_decodes_into_something_dangerous) {
    uint64_t x = 0x9E3779B97F4A7C15ull;
    auto next = [&]() { x ^= x << 13; x ^= x >> 7; x ^= x << 17; return x; };
    int decoded = 0;
    for (int i = 0; i < 4000; ++i) {
        Bytes b(static_cast<size_t>(3 + next() % 40));
        for (auto& v : b) v = static_cast<uint8_t>(next());
        b[0] = kChannelVats; b[1] = kVatsModuleWire;
        Envelope out;
        if (decodeEnvelope(b.data(), b.size(), out)) {
            ++decoded;
            if (const MPhase* p = std::get_if<MPhase>(&out.body)) CHECK(static_cast<int>(p->mode) <= 4);
            if (const MSlowMo* m = std::get_if<MSlowMo>(&out.body)) CHECK(m->g.scale == m->g.scale);   // not NaN
            if (const MConfig* c = std::get_if<MConfig>(&out.body)) CHECK(c->s.slowScale >= 0.10f && c->s.slowScale <= 0.90f && c->s.slowMaxMs <= 10000);
        }
    }
    CHECK(decoded >= 0);   // (most garbage fails; the point is that nothing above crashed and everything that decoded is in range)
}

TEST(vats_wire, a_modes_limb_or_float_out_of_range_is_refused) {
    Envelope e; e.src = 1;
    MPhase p; p.player = 1; p.mode = Mode::Playback;
    e.body = p;
    Bytes b = encodeEnvelope(e);
    // locate the mode byte: after channel, version, string table (1 byte, empty), src, dest, hops, kind, player
    Bytes bad = b;
    bad[2 + 1 + 4 + 1] = 9;   // mode 9
    Envelope out;
    CHECK(!decodeEnvelope(bad.data(), bad.size(), out));
    Bytes badLimb = b;
    badLimb[2 + 1 + 4 + 2] = 3;   // limb 3 is no limb
    CHECK(!decodeEnvelope(badLimb.data(), badLimb.size(), out));

    MSlowMo m; m.g.active = true; m.g.scale = std::numeric_limits<float>::quiet_NaN();
    Envelope e2; e2.src = 0; e2.body = m;
    const Bytes nb = encodeEnvelope(e2);
    CHECK(!decodeEnvelope(nb.data(), nb.size(), out));
}

TEST(vats_wire, a_peers_settings_are_clamped_when_they_are_decoded) {
    Envelope e; e.src = 0;
    MConfig c;
    c.s.slowScale = 0.5f; c.s.slowMaxMs = 3000;
    e.body = c;
    Bytes b = encodeEnvelope(e);
    // overwrite the encoded numbers with hostile ones by encoding them through a MConfig the writer does not clamp
    MConfig evil;
    evil.s.slowScale = 0.0f;
    evil.s.slowMaxMs = 1000000;
    evil.s.slowCooldownMs = 1000000000u;
    evil.s.autoFireDelayMs = 100000;
    Envelope e2; e2.src = 0; e2.body = evil;
    const Bytes eb = encodeEnvelope(e2);
    Envelope out;
    REQUIRE(decodeEnvelope(eb.data(), eb.size(), out));
    const MConfig* g = std::get_if<MConfig>(&out.body);
    REQUIRE(g != nullptr);
    CHECK(g->s.slowScale >= 0.10f);
    CHECK(g->s.slowMaxMs <= 10000);
    CHECK(g->s.slowCooldownMs <= 120000);
    CHECK(g->s.autoFireDelayMs <= 3000);
    (void)b;
}

}  // namespace
