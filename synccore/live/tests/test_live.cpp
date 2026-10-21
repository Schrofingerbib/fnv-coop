// live-core unit tests: codec, receiver rules, interest management, channel mapping.
#include <cstdlib>
#include "minitest.h"
#include "scenarios.h"
#include "fnvcoop/transport.h"
#include "fnvcoop/wire.h"

using namespace live_test;

namespace {
uint64_t rngState = 0x1234567;
uint32_t rnd() { rngState = rngState * 6364136223846793005ULL + 1442695040888963407ULL; return static_cast<uint32_t>(rngState >> 33); }
float rf(float lo, float hi) { return lo + (hi - lo) * (static_cast<float>(rnd() & 0xFFFF) / 65535.0f); }
}  // namespace

// ---- codec ------------------------------------------------------------------------------------------------------
TEST(codec, full_snapshot_round_trips) {
    Pair p;
    ActorSnapshot s = mk(5, 3, 1, 123456, 1234.5f, -987.25f, 55.125f);
    s.rot = {0.3f, 4.0f, 6.0f};
    s.vel = {120.5f, -30.25f, 0.5f};
    s.anim = {4, true, true, false, true, true};
    s.ai = {77, 0xABCDEF, 3};
    s.avDeltas = {{0, 95}, {7, -3}, {40, 1000000}};
    auto r = p.send(s);
    REQUIRE(r.size() == 1);
    CHECK(r[0].verdict == Verdict::Accepted);
    CHECK(sameSnap(r[0].snap, normalized(s)));
    // quantization stays within half a step
    CHECK(std::fabs(r[0].snap.pos.x - s.pos.x) <= kPosStep / 2 + 1e-4f);
    CHECK(std::fabs(r[0].snap.vel.y - s.vel.y) <= kVelStep / 2 + 1e-4f);
    CHECK(std::fabs(r[0].snap.rot.y - s.rot.y) <= 6.2831853f / 65536.0f);
}

TEST(codec, random_stream_round_trips_with_deltas) {
    Pair p;
    ActorSnapshot cur = mk(6, 1, 0, 1000, 100, 100, 0);
    std::map<uint8_t, int32_t> avs;
    int accepted = 0;
    for (uint32_t i = 1; i <= 400; ++i) {
        cur.seq = i; cur.tick = 1000 + i * 50;
        if (rnd() % 3) { cur.pos.x += rf(-20, 20); cur.pos.y += rf(-20, 20); }
        if (rnd() % 5 == 0) cur.pos.z = rf(-500, 500);
        if (rnd() % 4 == 0) cur.rot.y = rf(-10, 10);
        if (rnd() % 4 == 0) cur.vel = {rf(-600, 600), rf(-600, 600), rf(-50, 50)};
        if (rnd() % 7 == 0) cur.anim = {static_cast<uint8_t>(rnd() % 9), (rnd() & 1) != 0, (rnd() & 1) != 0, (rnd() & 1) != 0, (rnd() & 1) != 0, (rnd() & 1) != 0};
        if (rnd() % 5 == 0) {   // schema v2 extension
            cur.anim.action = static_cast<uint8_t>(rnd() % 245); cur.anim.actionSeq = static_cast<uint8_t>(rnd() % 256);
            cur.anim.dead = (rnd() % 8) == 0; cur.anim.knocked = (rnd() & 1) != 0; cur.anim.swim = (rnd() & 1) != 0;
            cur.anim.talk = (rnd() & 1) != 0; cur.anim.block = (rnd() & 1) != 0; cur.anim.ragdoll = (rnd() & 1) != 0;
        }
        if (rnd() % 9 == 0) cur.ai = {rnd() % 5, rnd() % 100000, static_cast<uint8_t>(rnd() % 4)};
        if (rnd() % 11 == 0) cur.cell = rnd() % 50;
        cur.avDeltas.clear();
        if (rnd() % 6 == 0) { uint8_t a = static_cast<uint8_t>(rnd() % 8); int32_t v = static_cast<int32_t>(rnd() % 2000) - 500; avs[a] = v; cur.avDeltas.push_back({a, v}); }
        auto r = p.send(cur, /*deliver=*/rnd() % 4 != 0);   // 25% lost
        for (auto& x : r) {
            REQUIRE(x.verdict == Verdict::Accepted);
            ++accepted;
            ActorSnapshot want = normalized(cur);
            want.avDeltas = x.snap.avDeltas;   // changes only: checked separately below
            CHECK(sameSnap(x.snap, want));
        }
    }
    CHECK(accepted > 250);
    CHECK(p.rx.stats().needBaseline == 0);
    CHECK(p.tx.stats().deltaSent > 300);
}

TEST(codec, delta_uses_acked_baseline) { CHECK(deltaUsesAckedBaseline()); }
TEST(codec, delta_is_small) { CHECK(deltaIsSmall()); }
TEST(codec, av_survives_loss) { CHECK(avSurvivesLoss()); }
TEST(codec, idle_skipped_then_heartbeat) { CHECK(idleSkippedThenHeartbeat()); }
TEST(codec, resync_after_receiver_loss) { CHECK(resyncAfterReceiverLoss()); }

TEST(codec, changed_avs_only_on_decode) {
    Pair p;
    auto s1 = mk(8, 1, 1, 1000); s1.avDeltas = {{0, 100}, {1, 50}};
    auto s2 = mk(8, 1, 2, 1050); s2.avDeltas = {{0, 100}, {1, 40}};
    p.send(s1);
    auto r = p.send(s2);
    REQUIRE(r.size() == 1);
    REQUIRE(r[0].snap.avDeltas.size() == 1);
    CHECK(r[0].snap.avDeltas[0] == (AvValue{1, 40}));
}

TEST(codec, quantize_is_idempotent) {
    for (int i = 0; i < 200; ++i) {
        ActorSnapshot s = mk(9, 1, 1, rnd(), rf(-1e5f, 1e5f), rf(-1e5f, 1e5f), rf(-1e4f, 1e4f));
        s.rot = {rf(-20, 20), rf(-20, 20), rf(-20, 20)};
        s.vel = {rf(-900, 900), rf(-900, 900), rf(-900, 900)};
        ActorSnapshot n = normalized(s);
        CHECK(sameSnap(n, normalized(n)));
    }
}

TEST(codec, nonfinite_and_huge_values_do_not_break_the_encoder) {
    Pair p;
    ActorSnapshot s = mk(30, 1, 1, 1000, std::nanf(""), INFINITY, 1e30f);
    auto r = p.send(s);
    REQUIRE(r.size() == 1);
    CHECK(r[0].verdict == Verdict::Accepted);
    CHECK(std::isfinite(r[0].snap.pos.x) && std::isfinite(r[0].snap.pos.y) && std::isfinite(r[0].snap.pos.z));
}

TEST(codec, packets_respect_the_size_budget) {
    SenderConfig sc; sc.maxPacketBytes = 200;
    SnapshotSender tx(sc);
    SnapshotReceiver rx;
    for (NetId id = 1; id <= 60; ++id) {
        auto s = mk(id, 1, 1, 1000, 100.0f * id, 5, 6);
        s.avDeltas = {{0, 100}, {1, 50}, {2, 25}};
        tx.queue(kHost, s, 1000);
    }
    auto pkts = tx.flush(kHost, 1000);
    CHECK(pkts.size() > 5);
    size_t got = 0;
    for (auto& pk : pkts) {
        CHECK(pk.size() <= 200);
        for (auto& r : rx.receive(kOwner, pk.data(), pk.size())) { CHECK(r.verdict == Verdict::Accepted); ++got; }
    }
    CHECK(got == 60);
}

TEST(codec, corrupt_and_truncated_datagrams_are_rejected_not_fatal) {
    Pair p;
    auto s = mk(31, 1, 1, 1000, 1, 2, 3);
    s.avDeltas = {{0, 5}};
    p.tx.queue(kHost, s, 1000);
    Bytes pkt = p.tx.flush(kHost, 1000)[0];
    // every single-bit flip is caught by the CRC
    for (size_t i = 0; i < pkt.size() * 8; ++i) {
        Bytes b = pkt; b[i / 8] ^= static_cast<uint8_t>(1u << (i % 8));
        SnapshotReceiver rx;
        auto r = rx.receive(kOwner, b.data(), b.size());
        CHECK(r.size() == 1 && r[0].verdict == Verdict::Malformed);
    }
    // every truncation
    for (size_t n = 0; n < pkt.size(); ++n) {
        SnapshotReceiver rx;
        auto r = rx.receive(kOwner, pkt.data(), n);
        CHECK(r.size() == 1 && r[0].verdict == Verdict::Malformed);
    }
    // random garbage with a valid CRC must not crash or be applied blindly: the structure checks hold
    for (int i = 0; i < 3000; ++i) {
        Bytes b(1 + rnd() % 60);
        for (auto& c : b) c = static_cast<uint8_t>(rnd());
        b[0] = static_cast<uint8_t>(MsgKind::SnapshotBatch);
        uint32_t c = fnvcoop::crc32(b.data(), b.size());
        for (int k = 0; k < 4; ++k) b.push_back(static_cast<uint8_t>(c >> (8 * k)));
        SnapshotReceiver rx;
        auto r = rx.receive(kOwner, b.data(), b.size());
        for (auto& x : r) CHECK(std::isfinite(x.snap.pos.x) && std::isfinite(x.snap.pos.y) && std::isfinite(x.snap.pos.z));
    }
}

TEST(codec, ack_packets_survive_garbage) {
    SnapshotSender tx;
    tx.queue(kHost, mk(32, 1, 1, 1000), 1000);
    for (int i = 0; i < 2000; ++i) {
        Bytes b(rnd() % 40);
        for (auto& c : b) c = static_cast<uint8_t>(rnd());
        tx.onAckPacket(kHost, b.data(), b.size());
    }
    CHECK(tx.ackedSeq(kHost, 32) == 0);
}

TEST(codec, seq_allocator_restarts_per_epoch) {
    SeqAllocator a;
    CHECK(a.next(1, 1) == 1);
    CHECK(a.next(1, 1) == 2);
    CHECK(a.next(2, 1) == 1);
    CHECK(a.next(1, 2) == 1);
    CHECK(a.next(1, 2) == 2);
}

TEST(codec, sender_ignores_older_epoch_and_old_seq) {
    SnapshotSender tx;
    CHECK(tx.queue(kHost, mk(33, 2, 5, 1000), 1000));
    CHECK(!tx.queue(kHost, mk(33, 1, 99, 1050), 1050));   // older owner
    CHECK(!tx.queue(kHost, mk(33, 2, 5, 1100), 1100));    // same seq
    CHECK(!tx.queue(kHost, mk(33, 2, 4, 1100), 1100));    // older seq
    CHECK(!tx.queue(kHost, mk(33, 2, 0, 1100), 1100));    // seq 0 is invalid
    CHECK(tx.queue(kHost, mk(33, 3, 1, 1150), 1150));     // newer epoch restarts
}

TEST(codec, receiver_table_is_bounded) {
    ReceiverConfig rc; rc.maxActors = 50;
    SnapshotSender tx;
    SnapshotReceiver rx(rc);
    size_t accepted = 0, malformed = 0;
    for (NetId id = 1; id <= 200; ++id) {
        tx.queue(kHost, mk(id, 1, 1, 1000, 1), 1000);
        for (auto& pk : tx.flush(kHost, 1000))
            for (auto& r : rx.receive(kOwner, pk.data(), pk.size())) { if (r.verdict == Verdict::Accepted) ++accepted; else if (r.verdict == Verdict::Malformed) ++malformed; }
    }
    CHECK(accepted == 50);
    CHECK(malformed == 150);
}

// ---- receiver ---------------------------------------------------------------------------------------------------
TEST(receiver, stale_seq_rejected) { CHECK(staleSeqRejected()); }
TEST(receiver, stale_epoch_rejected) { CHECK(staleEpochRejected()); }
TEST(receiver, new_epoch_restarts_seq) { CHECK(epochRestartsSeq()); }
TEST(receiver, wrong_owner_rejected) { CHECK(wrongOwnerRejected()); }
TEST(receiver, authority_floor_rejects_older_epoch) { CHECK(authorityFloorRejects()); }

TEST(receiver, client_relay_mode_does_not_check_sender) {
    ReceiverConfig rc; rc.checkSenderIsOwner = false;
    Pair p(rc);
    p.rx.setAuthority(34, 2, kOwner);
    auto r = p.send(mk(34, 2, 1, 1000));   // arrives "from" kOwner in Pair; use a different sender below
    CHECK(r.size() == 1 && r[0].verdict == Verdict::Accepted);
    auto s = mk(34, 2, 2, 1050, 9);
    REQUIRE(p.tx.queue(kHost, s, 1050));
    auto pk = p.tx.flush(kHost, 1050);
    REQUIRE(pk.size() == 1);
    auto r2 = p.rx.receive(kClient, pk[0].data(), pk[0].size());
    CHECK(r2.size() == 1 && r2[0].verdict == Verdict::Accepted);
}

TEST(receiver, host_refuses_a_higher_epoch_from_a_non_owner) { CHECK(forgedFutureEpochRejected()); }

// (client / relay mode only: the host records every epoch before the new owner publishes, so it never needs this allowance)
TEST(receiver, higher_epoch_is_provisionally_accepted_before_the_commit_arrives) {
    ReceiverConfig rc; rc.checkSenderIsOwner = false;
    Pair p(rc);
    p.rx.setAuthority(35, 1, kOwner);
    p.send(mk(35, 1, 1, 1000));
    // new owner (kClient) publishes at epoch 2 before the host's Commit(2) reached us
    SnapshotSender tx2;
    tx2.queue(kHost, mk(35, 2, 1, 1100), 1100);
    auto pk = tx2.flush(kHost, 1100);
    auto r = p.rx.receive(kClient, pk[0].data(), pk[0].size());
    CHECK(r.size() == 1 && r[0].verdict == Verdict::Accepted);
    // now the former owner is stale
    auto old = p.send(mk(35, 1, 2, 1150, 9));
    CHECK(old.size() == 1 && old[0].verdict == Verdict::StaleEpoch);
}

TEST(receiver, older_authority_message_never_overwrites_newer) {
    Pair p;
    p.rx.setAuthority(36, 5, kClient);
    p.rx.setAuthority(36, 3, kOwner);   // a replayed old Grant
    auto r = p.send(mk(36, 5, 1, 1000));   // sent by kOwner in Pair; epoch 5 owner is kClient
    CHECK(r.size() == 1 && r[0].verdict == Verdict::WrongOwner);
}

TEST(receiver, duplicate_datagram_is_reacked_not_reapplied) {
    Pair p;
    auto s = mk(37, 1, 1, 1000, 4);
    p.tx.queue(kHost, s, 1000);
    Bytes pk = p.tx.flush(kHost, 1000)[0];
    auto a = p.rx.receive(kOwner, pk.data(), pk.size());
    CHECK(a.size() == 1 && a[0].verdict == Verdict::Accepted);
    p.rx.takeAcks(kOwner);   // the ack is lost
    auto b = p.rx.receive(kOwner, pk.data(), pk.size());
    CHECK(b.size() == 1 && b[0].verdict == Verdict::StaleSeq);
    Bytes ack = p.rx.takeAcks(kOwner);
    CHECK(!ack.empty());
    CHECK(p.tx.onAckPacket(kHost, ack.data(), ack.size()));
    CHECK(p.tx.ackedSeq(kHost, 37) == 1);
}

TEST(receiver, ack_advances_the_sender_baseline) {
    Pair p;
    CHECK(p.tx.ackedSeq(kHost, 38) == 0);
    p.send(mk(38, 1, 1, 1000, 1));
    CHECK(p.tx.ackedSeq(kHost, 38) == 1);
    p.send(mk(38, 1, 2, 1050, 2), true, false);   // delivered, ack not pumped
    CHECK(p.tx.ackedSeq(kHost, 38) == 1);
    p.pumpAcks();
    CHECK(p.tx.ackedSeq(kHost, 38) == 2);
}

TEST(receiver, ack_for_an_old_epoch_is_ignored) {
    Pair p;
    p.send(mk(39, 1, 1, 1000));
    Bytes ack;
    {
        // an ack that names epoch 1 arrives after the sender moved to epoch 2
        p.tx.queue(kHost, mk(39, 2, 1, 1100), 1100);
        p.tx.flush(kHost, 1100);
        SnapshotReceiver rx2;
        SnapshotSender tx1;
        tx1.queue(kHost, mk(39, 1, 1, 1000), 1000);
        auto pk = tx1.flush(kHost, 1000);
        rx2.receive(kOwner, pk[0].data(), pk[0].size());
        ack = rx2.takeAcks(kOwner);
    }
    p.tx.onAckPacket(kHost, ack.data(), ack.size());
    CHECK(p.tx.ackedSeq(kHost, 39) == 0);
}

// ---- interest ---------------------------------------------------------------------------------------------------
TEST(interest, rates_are_20_and_5_hz) { CHECK(ratesAre20And5()); }
TEST(interest, rates_hold_at_60fps_polling) { CHECK(ratesHoldAtFrameRate()); }

TEST(interest, tiers_by_cell_and_distance) {
    InterestManager im;
    Observer o; o.player = kClient; o.cell = 1; o.pos = {0, 0, 0}; o.adjacent = {2};
    im.setObserver(o);
    im.setActor({1, 1, {100, 0, 0}, kOwner});
    im.setActor({2, 1, {5000, 0, 0}, kOwner});
    im.setActor({3, 2, {0, 0, 0}, kOwner});
    im.setActor({4, 9, {0, 0, 0}, kOwner});
    im.setActor({5, 1, {100, 0, 0}, kClient});   // owned by the observer itself
    CHECK(im.tierOf(kClient, 1) == Tier::Near);
    CHECK(im.tierOf(kClient, 2) == Tier::Far);
    CHECK(im.tierOf(kClient, 3) == Tier::Far);
    CHECK(im.tierOf(kClient, 4) == Tier::None);
    CHECK(im.tierOf(kClient, 5) == Tier::None);
    CHECK(im.tierOf(kClient, 999) == Tier::None);
    CHECK(im.tierOf(77, 1) == Tier::None);
}

TEST(interest, hysteresis_prevents_flapping_at_the_border) {
    InterestManager im;
    Observer o; o.player = kClient; o.cell = 1; o.pos = {0, 0, 0};
    im.setObserver(o);
    InterestConfig c;
    float r = c.nearRadius;
    im.setActor({1, 1, {r - 10, 0, 0}, kOwner});
    CHECK(im.tierOf(kClient, 1) == Tier::Near);
    int flips = 0;
    Tier prev = Tier::Near;
    for (int i = 0; i < 100; ++i) {   // jitter around the radius, inside the hysteresis band
        float x = r + ((i % 2) ? 30.0f : -30.0f);
        im.setActor({1, 1, {x, 0, 0}, kOwner});
        Tier t = im.tierOf(kClient, 1);
        if (t != prev) ++flips;
        prev = t;
    }
    CHECK(flips == 0);
    im.setActor({1, 1, {r * 1.3f, 0, 0}, kOwner});
    CHECK(im.tierOf(kClient, 1) == Tier::Far);
    im.setActor({1, 1, {r + 10, 0, 0}, kOwner});
    CHECK(im.tierOf(kClient, 1) == Tier::Far);   // coming back needs to be inside the plain radius
    im.setActor({1, 1, {r - 10, 0, 0}, kOwner});
    CHECK(im.tierOf(kClient, 1) == Tier::Near);
}

TEST(interest, leaving_and_reentering_sends_at_once) {
    InterestManager im;
    Observer o; o.player = kClient; o.cell = 1; o.pos = {0, 0, 0};
    im.setObserver(o);
    im.setActor({1, 1, {10, 0, 0}, kOwner});
    CHECK(im.collect(kClient, 0).size() == 1);
    CHECK(im.collect(kClient, 10).empty());
    o.cell = 5;
    im.setObserver(o);
    CHECK(im.collect(kClient, 20).empty());   // unrelated cell now
    o.cell = 1;
    im.setObserver(o);
    CHECK(im.collect(kClient, 30).size() == 1);   // due immediately, not after the rest of the old period
}

TEST(interest, removal_forgets_links) {
    InterestManager im;
    Observer o; o.player = kClient; o.cell = 1;
    im.setObserver(o);
    im.setActor({1, 1, {10, 0, 0}, kOwner});
    CHECK(im.collect(kClient, 0).size() == 1);
    im.removeActor(1);
    CHECK(im.collect(kClient, 1000).empty());
    im.setActor({1, 1, {10, 0, 0}, kOwner});
    CHECK(im.collect(kClient, 1001).size() == 1);
    im.removeObserver(kClient);
    CHECK(im.collect(kClient, 3000).empty());
}

TEST(interest, per_observer_schedules_are_independent) {
    InterestManager im;
    Observer a; a.player = 2; a.cell = 1;
    Observer b; b.player = 3; b.cell = 1;
    im.setObserver(a); im.setObserver(b);
    im.setActor({1, 1, {10, 0, 0}, kOwner});
    CHECK(im.collect(2, 0).size() == 1);
    CHECK(im.collect(3, 20).size() == 1);   // b's own first send, unaffected by a's schedule
    CHECK(im.collect(2, 30).empty());
    CHECK(im.collect(2, 50).size() == 1);
    CHECK(im.collect(3, 60).empty());       // b is due at 70
    CHECK(im.collect(3, 70).size() == 1);
}

// ---- channel mapping --------------------------------------------------------------------------------------------
TEST(channel, snapshots_unreliable_events_and_ownership_reliable) {
    CHECK(channelFor(MsgKind::SnapshotBatch) == Channel::Unreliable);
    CHECK(channelFor(MsgKind::SnapshotAck) == Channel::Unreliable);
    CHECK(channelFor(MsgKind::Ownership) == Channel::Reliable);
    CHECK(channelFor(MsgKind::Event) == Channel::Reliable);
}

TEST(channel, numbers_match_the_transport) {
    CHECK(enetChannel(Channel::Reliable) == 0);
    CHECK(enetChannel(Channel::Unreliable) == 1);
    CHECK(static_cast<uint8_t>(fnvcoop::ITransport::Channel::Reliable) == enetChannel(Channel::Reliable));
    CHECK(static_cast<uint8_t>(fnvcoop::ITransport::Channel::Unreliable) == enetChannel(Channel::Unreliable));
}

TEST(channel, snapshots_flow_over_the_in_memory_transport_with_loss_and_duplicates) {
    fnvcoop::InMemoryNetwork net(5);
    fnvcoop::LinkParams lp; lp.minLatencyMs = 5; lp.maxLatencyMs = 40; lp.loss = 0.1; lp.duplicate = 0.2;
    net.setLink(lp);
    auto* a = net.endpoint(1);
    auto* b = net.endpoint(2);
    SnapshotSender tx;
    SnapshotReceiver rx;
    SeqAllocator seq;
    uint32_t last = 0;
    uint64_t now = 0;
    int accepted = 0;
    for (int i = 0; i < 300; ++i) {
        now += 50;
        auto s = mk(40, 1, seq.next(40, 1), static_cast<uint32_t>(now), 3.0f * i);
        if (tx.queue(2, s, static_cast<uint32_t>(now)))
            for (auto& pk : tx.flush(2, static_cast<uint32_t>(now)))
                a->sendEx(2, pk, static_cast<fnvcoop::ITransport::Channel>(enetChannel(channelFor(MsgKind::SnapshotBatch))));
        net.advance(now);
        fnvcoop::PeerId from; fnvcoop::Bytes data;
        while (b->poll(from, data)) {
            for (auto& r : rx.receive(static_cast<PlayerId>(from), data.data(), data.size())) {
                if (r.verdict == Verdict::Accepted) {
                    ++accepted;
                    CHECK(serialLess(last, r.snap.seq));   // never applied out of order
                    last = r.snap.seq;
                }
            }
        }
        Bytes ack = rx.takeAcks(1);
        if (!ack.empty()) b->sendEx(1, ack, fnvcoop::ITransport::Channel::Unreliable);
        net.advance(now);
        while (a->poll(from, data)) tx.onAckPacket(2, data.data(), data.size());
    }
    CHECK(accepted > 150);
}
