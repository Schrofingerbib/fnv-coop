// End-to-end lossy-link tests. Pipeline (all virtual time, no sockets):
//   owner(1) --o2h--> host(0) --h2c--> client(2)        acks travel back on h2o and c2h
// Every link is a chaos::Impairment (synccore/chaos/impair.h), so loss, bursts, jitter, reordering, duplication, bandwidth
// limits and corruption come from the same model the rest of the project uses. Mid-run the actor changes owner
// (epoch 1 -> 2, new machine); optionally the former owner keeps publishing (ghost) or a bystander forges updates (rogue).
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <queue>
#include <string>
#include "impair.h"
#include "minitest.h"
#include "scenarios.h"

using namespace live_test;
namespace chaos = fnvcoop::chaos;

namespace {

constexpr PlayerId kOwner2 = 3;
constexpr NetId kActor = 500;

struct Opts {
    const char* profile = "lan";
    uint64_t seed = 1;
    bool ghost = false;       // former owner keeps publishing garbage at the old epoch after the handoff
    bool rogue = false;       // a bystander forges updates at the current epoch
    bool partition = false;   // 2 s total partition
    bool corrupt = false;     // 3% of datagrams get one flipped bit
    bool handoff = true;
};

struct Report {
    bool ok = true;
    std::string why;
    uint64_t hostAccepted = 0, clientAccepted = 0, clientNeedBaseline = 0, hostNeedBaseline = 0, clientStale = 0, hostStale = 0, hostRejected = 0;
    uint64_t snapped = 0, smoothing = 0, holding = 0, extrap = 0, malformedAtClient = 0;
    float maxStep = 0, maxEasedStep = 0;
    double ownerBytesPerSnapshot = 0, fullBytesPerSnapshot = 0;
    uint64_t sentFull = 0, sentDelta = 0;
    void fail(const std::string& m) { if (ok) { ok = false; why = m; } }
};

struct Wire { double at; uint64_t order; int link; PlayerId from, to; Bytes data;
    bool operator>(const Wire& o) const { return at != o.at ? at > o.at : order > o.order; } };

ActorSnapshot truthAt(uint32_t t, OwnerEpoch epoch, uint32_t seq) {
    const double R = 1500.0, v = 300.0;
    double tm = t < 22000 ? t : 22000;   // the actor stops at 22 s
    double ang = v / R * tm / 1000.0;
    ActorSnapshot s = mk(kActor, epoch, seq, t, static_cast<float>(R * std::cos(ang)), static_cast<float>(R * std::sin(ang)), 0);
    bool moving = t < 22000;
    s.vel = moving ? Vec3{static_cast<float>(-v * std::sin(ang)), static_cast<float>(v * std::cos(ang)), 0} : Vec3{};
    s.rot = {0, static_cast<float>(ang + 1.5707963), 0};
    s.anim.locomotion = moving ? 2 : 0;
    s.anim.weaponDrawn = (t / 3000) % 2 == 1;
    s.ai.alert = static_cast<uint8_t>((t / 5000) % 4);
    int hp = 100 - static_cast<int>(t / 1000);
    if (t >= 23000) hp = 77;
    s.avDeltas = {{0, hp}, {1, 50 + static_cast<int32_t>(25.0 * std::sin(tm / 700.0))}, {2, 30}};
    return s;
}

Report run(const Opts& o) {
    Report rep;
    chaos::Params pr = chaos::profile(o.profile);
    pr.seed = o.seed;
    if (o.corrupt) pr.corrupt = 0.03;
    chaos::Impairment link[4] = {chaos::Impairment(pr, "o2h"), chaos::Impairment(pr, "h2o"), chaos::Impairment(pr, "h2c"), chaos::Impairment(pr, "c2h")};
    chaos::Params clean = chaos::profile("lan");
    std::priority_queue<Wire, std::vector<Wire>, std::greater<Wire>> q;
    uint64_t order = 0;
    auto send = [&](int l, double t, PlayerId from, PlayerId to, const Bytes& data) {
        auto v = link[l].decide(t, static_cast<uint32_t>(data.size()));
        for (double d : v.delaysMs) {
            Bytes b = data;
            if (v.corrupt) link[l].flipBit(b);
            q.push({t + d, order++, l, from, to, std::move(b)});
        }
    };

    SnapshotSender ownerTx[2];           // [0] machine 1, [1] machine 3 (new owner)
    SeqAllocator ownerSeq[2];
    SnapshotSender ghostTx, rogueTx;
    SeqAllocator ghostSeq, rogueSeq;
    SnapshotReceiver hostRx;
    SnapshotSender hostTx;
    ReceiverConfig crc; crc.checkSenderIsOwner = false;
    SnapshotReceiver clientRx(crc);
    InterpBuffer ib;
    hostRx.setAuthority(kActor, 1, kOwner);
    clientRx.setAuthority(kActor, 1, kOwner);

    std::map<std::pair<OwnerEpoch, uint32_t>, ActorSnapshot> truth;
    bool haveClient = false;
    ActorSnapshot clientLast, hostLast;
    OwnerEpoch lastEpochC = 0; uint32_t lastSeqC = 0, lastSeqH = 0; OwnerEpoch lastEpochH = 0;
    float prevX = 0; bool prevValid = false;
    uint64_t ownerPackets = 0, ownerBytes = 0, ownerSnaps = 0, fullBytesSum = 0;

    auto checkAgainstTruth = [&](const char* who, const ActorSnapshot& snap, const std::vector<AvValue>& avAll) {
        auto it = truth.find({snap.ownerEpoch, snap.seq});
        if (it == truth.end()) { rep.fail(std::string(who) + " applied a snapshot nobody real published (epoch " + std::to_string(snap.ownerEpoch) + " seq " + std::to_string(snap.seq) + ")"); return; }
        ActorSnapshot want = it->second, got = snap;
        want.avDeltas.clear(); got.avDeltas.clear();
        if (!sameSnap(got, want)) rep.fail(std::string(who) + " reconstructed a wrong state at seq " + std::to_string(snap.seq));
        if (!(avAll == it->second.avDeltas)) rep.fail(std::string(who) + " AV set differs at seq " + std::to_string(snap.seq));
    };

    const uint32_t kEnd = 26000;
    for (uint32_t t = 0; t <= kEnd; t += 2) {
        if (t == 20000) for (auto& l : link) l.setParams(clean);
        if (o.partition) { bool on = t >= 12000 && t < 14000; for (auto& l : link) l.partition(on); }
        if (o.handoff && t == 10000) {
            // The host decides the new owner and tells everyone (Commit): reliable, so modelled as instant here.
            hostRx.setAuthority(kActor, 2, kOwner2);
            clientRx.setAuthority(kActor, 2, kOwner2);
        }
        // Deliveries.
        while (!q.empty() && q.top().at <= t) {
            Wire w = q.top(); q.pop();
            switch (w.link) {
                case 0: {   // to host
                    auto res = hostRx.receive(w.from, w.data.data(), w.data.size());
                    for (auto& r : res) {
                        if (r.verdict == Verdict::Accepted) {
                            ++rep.hostAccepted;
                            if (r.snap.ownerEpoch < lastEpochH || (r.snap.ownerEpoch == lastEpochH && r.snap.seq <= lastSeqH && lastSeqH != 0)) rep.fail("host applied out of order");
                            lastEpochH = r.snap.ownerEpoch; lastSeqH = r.snap.seq;
                            checkAgainstTruth("host", r.snap, r.avAll);
                            hostLast = r.snap;
                            ActorSnapshot relay = r.snap;
                            relay.avDeltas = r.avAll;
                            if (hostTx.queue(kClient, relay, t))
                                for (auto& pk : hostTx.flush(kClient, t)) send(2, t, kHost, kClient, pk);
                        } else if (r.verdict == Verdict::StaleEpoch || r.verdict == Verdict::StaleSeq) ++rep.hostStale;
                        else if (r.verdict == Verdict::NeedBaseline) ++rep.hostNeedBaseline;
                        else ++rep.hostRejected;
                    }
                    break;
                }
                case 1:   // ack to an owner machine
                    if (w.to == kOwner) ownerTx[0].onAckPacket(kHost, w.data.data(), w.data.size());
                    else if (w.to == kOwner2) ownerTx[1].onAckPacket(kHost, w.data.data(), w.data.size());
                    break;
                case 2: {   // to client
                    auto res = clientRx.receive(w.from, w.data.data(), w.data.size());
                    for (auto& r : res) {
                        if (r.verdict == Verdict::Accepted) {
                            ++rep.clientAccepted;
                            if (r.snap.ownerEpoch < lastEpochC || (r.snap.ownerEpoch == lastEpochC && r.snap.seq <= lastSeqC && lastSeqC != 0)) rep.fail("client applied out of order");
                            lastEpochC = r.snap.ownerEpoch; lastSeqC = r.snap.seq;
                            checkAgainstTruth("client", r.snap, r.avAll);
                            clientLast = r.snap; clientLast.avDeltas = r.avAll; haveClient = true;
                            ib.push(r.snap, t);
                        } else if (r.verdict == Verdict::NeedBaseline) ++rep.clientNeedBaseline;
                        else if (r.verdict == Verdict::Malformed) ++rep.malformedAtClient;
                        else ++rep.clientStale;
                    }
                    break;
                }
                case 3: hostTx.onAckPacket(kClient, w.data.data(), w.data.size()); break;
                default: break;
            }
        }
        // The real owner publishes at 20 Hz.
        if (t % 50 == 0 && t > 0) {
            int idx = (o.handoff && t >= 10000) ? 1 : 0;
            OwnerEpoch ep = idx == 1 ? 2 : 1;
            PlayerId id = idx == 1 ? kOwner2 : kOwner;
            ActorSnapshot s = truthAt(t, ep, ownerSeq[idx].next(kActor, ep));
            truth[{ep, s.seq}] = normalized(s);
            ownerSnaps++;
            if (ownerTx[idx].queue(kHost, s, t)) {
                for (auto& pk : ownerTx[idx].flush(kHost, t)) { send(0, t, id, kHost, pk); ++ownerPackets; ownerBytes += pk.size(); }
            }
            { SnapshotSender fresh; fresh.queue(kHost, s, t); for (auto& pk : fresh.flush(kHost, t)) fullBytesSum += pk.size(); }
            if (o.ghost && o.handoff && t >= 10000 && t < 20000) {
                ActorSnapshot g = truthAt(t, 1, ghostSeq.next(kActor, 1) + 100000);
                g.pos.x += 99999;
                if (ghostTx.queue(kHost, g, t)) for (auto& pk : ghostTx.flush(kHost, t)) send(0, t, kOwner, kHost, pk);
            }
            if (o.rogue && o.handoff && t >= 10000 && t < 20000) {
                ActorSnapshot g = truthAt(t, 2, rogueSeq.next(kActor, 2) + 100000);
                g.pos.y -= 99999;
                if (rogueTx.queue(kHost, g, t)) for (auto& pk : rogueTx.flush(kHost, t)) send(0, t, kClient, kHost, pk);
            }
        }
        // Acks at 20 Hz, offset from the publish tick (t steps by 2 ms, so the offset must be even).
        if (t % 50 == 26) {
            Bytes a1 = hostRx.takeAcks(kOwner); if (!a1.empty()) send(1, t, kHost, kOwner, a1);
            Bytes a3 = hostRx.takeAcks(kOwner2); if (!a3.empty()) send(1, t, kHost, kOwner2, a3);
            Bytes ac = clientRx.takeAcks(kHost); if (!ac.empty()) send(3, t, kClient, kHost, ac);
        }
        // Client frames.
        if (t % 16 == 0 && haveClient) {
            InterpOut po = ib.sample(t);
            if (prevValid) {
                float st = std::fabs(po.pos.x - prevX);
                rep.maxStep = std::max(rep.maxStep, st);
                if (!po.snapped && po.state == InterpState::Smoothing) rep.maxEasedStep = std::max(rep.maxEasedStep, st);
            }
            prevX = po.pos.x; prevValid = true;
            switch (po.state) {
                case InterpState::Snapped: ++rep.snapped; break;
                case InterpState::Smoothing: ++rep.smoothing; break;
                case InterpState::Holding: ++rep.holding; break;
                case InterpState::Extrapolating: ++rep.extrap; break;
                default: break;
            }
            if (po.state == InterpState::Holding || po.state == InterpState::Extrapolating) {
                float speed = std::sqrt(clientLast.vel.x * clientLast.vel.x + clientLast.vel.y * clientLast.vel.y);
                float d = distance(po.pos, clientLast.pos);
                // never farther from the newest snapshot than the 250 ms cap plus the 2 m dead band
                // (the puppet may still be catching up from an older position)
                if (d > speed * 0.25f + 2.0f * 70.0f + 300.0f * 0.35f) rep.fail("puppet strayed beyond the extrapolation cap");
            }
        }
    }

    // Convergence after the heal.
    if (!haveClient) { rep.fail("client never received anything"); return rep; }
    if (clientLast.ownerEpoch != (o.handoff ? 2u : 1u)) rep.fail("client ended on the wrong epoch");
    // The last real snapshot the owner published is the one with the highest seq at the final epoch.
    auto lastTruth = truth.rbegin()->second;
    ActorSnapshot a = clientLast, b = lastTruth;
    a.avDeltas.clear(); b.avDeltas.clear();
    if (!sameSnap(a, b) && !(a.seq < b.seq && a.cell == b.cell && a.pos == b.pos && a.rot == b.rot && a.vel == b.vel && a.anim == b.anim && a.ai == b.ai)) {
        // allowed: the final identical-state snapshots were skipped as idle; state must still match
        rep.fail("client did not converge to the owner's final state");
    }
    if (!(clientLast.avDeltas == lastTruth.avDeltas)) rep.fail("client AV values did not converge");
    InterpOut fo = ib.sample(kEnd);
    if (distance(fo.pos, lastTruth.pos) > 0.5f * 70.0f) rep.fail("puppet did not settle on the owner's position");
    // A sender that deltas against the newest acked snapshot never references a baseline the receiver lacks (except after
    // very long ack outages, which the resync request repairs). More than a few percent means the baseline logic is broken.
    {
        bool harsh = std::string(o.profile) == "hell" || o.partition;
        uint64_t nb = rep.hostNeedBaseline + rep.clientNeedBaseline, acc = rep.hostAccepted + rep.clientAccepted;
        if (nb * (harsh ? 10 : 50) > acc) rep.fail("too many snapshots referenced a baseline the receiver did not have (" + std::to_string(nb) + " of " + std::to_string(acc) + ")");
    }
    if (o.ghost && rep.hostStale == 0) rep.fail("ghost traffic was never rejected as stale");
    if (o.rogue && rep.hostRejected == 0) rep.fail("rogue traffic was never rejected");
    rep.ownerBytesPerSnapshot = ownerSnaps ? static_cast<double>(ownerBytes) / static_cast<double>(ownerSnaps) : 0;
    rep.fullBytesPerSnapshot = ownerSnaps ? static_cast<double>(fullBytesSum) / static_cast<double>(ownerSnaps) : 0;
    rep.sentFull = ownerTx[0].stats().fullSent + ownerTx[1].stats().fullSent;
    rep.sentDelta = ownerTx[0].stats().deltaSent + ownerTx[1].stats().deltaSent;
    (void)ownerPackets;
    return rep;
}

void show(const char* name, const Opts& o, const Report& r) {
    std::printf("    %-28s seed %llu: host %llu client %llu needBase %llu stale %llu snaps %llu smooth %llu hold %llu extrap %llu maxStep %.1f bytes/snap %.1f (full %.1f) full/delta %llu/%llu %s\n",
                name, static_cast<unsigned long long>(o.seed), static_cast<unsigned long long>(r.hostAccepted), static_cast<unsigned long long>(r.clientAccepted),
                static_cast<unsigned long long>(r.clientNeedBaseline), static_cast<unsigned long long>(r.clientStale), static_cast<unsigned long long>(r.snapped),
                static_cast<unsigned long long>(r.smoothing), static_cast<unsigned long long>(r.holding), static_cast<unsigned long long>(r.extrap), r.maxStep,
                r.ownerBytesPerSnapshot, r.fullBytesPerSnapshot, static_cast<unsigned long long>(r.sentFull), static_cast<unsigned long long>(r.sentDelta),
                r.ok ? "ok" : r.why.c_str());
}

bool verbose() { const char* v = std::getenv("LIVE_VERBOSE"); return v && *v; }

}  // namespace

TEST(lossy, clean_link_is_exact_and_smooth) {
    Opts o; o.profile = "lan";
    Report r = run(o);
    if (verbose()) show("lan", o, r);
    CHECK_MSG(r.ok, r.why);
    CHECK(r.snapped == 0);
    CHECK(r.clientNeedBaseline == 0);
    CHECK(r.maxStep < 12.0f);
    CHECK(r.ownerBytesPerSnapshot < 0.7 * r.fullBytesPerSnapshot);
}

TEST(lossy, every_profile_converges_without_stale_application) {
    const char* names[] = {"lan", "good_wifi", "bad_wifi", "mobile_tether", "transatlantic", "hell"};
    for (const char* n : names) {
        for (uint64_t seed = 1; seed <= 6; ++seed) {
            Opts o; o.profile = n; o.seed = seed;
            Report r = run(o);
            if (verbose()) show(n, o, r);
            CHECK_MSG(r.ok, std::string(n) + " seed " + std::to_string(seed) + ": " + r.why);
        }
    }
}

TEST(lossy, puppet_stays_smooth_on_realistic_links) {
    const char* names[] = {"good_wifi", "bad_wifi", "mobile_tether", "transatlantic"};
    for (const char* n : names) {
        for (uint64_t seed = 1; seed <= 4; ++seed) {
            Opts o; o.profile = n; o.seed = seed;
            Report r = run(o);
            CHECK_MSG(r.ok, r.why);
            CHECK_MSG(r.snapped == 0, std::string(n) + " seed " + std::to_string(seed) + " snapped " + std::to_string(r.snapped));
            // never a pop above the 2 m smoothing threshold in a single 16 ms frame (140 units) plus the frame's own travel
            CHECK_MSG(r.maxStep < 150.0f, std::string(n) + " seed " + std::to_string(seed) + " maxStep " + std::to_string(r.maxStep));
        }
    }
}

TEST(lossy, hell_link_never_pops_more_than_the_snap_limit) {
    for (uint64_t seed = 1; seed <= 6; ++seed) {
        Opts o; o.profile = "hell"; o.seed = seed;
        Report r = run(o);
        if (verbose()) show("hell", o, r);
        CHECK_MSG(r.ok, r.why);
        // A long outage can leave the puppet more than 10 m behind; then a snap is the specified answer. Without a
        // snap, no single frame may move the puppet by more than the snap distance.
        if (r.snapped == 0) CHECK_MSG(r.maxStep < 10.0f * 70.0f + 20.0f, "maxStep " + std::to_string(r.maxStep));
        // Smoothing keeps each eased frame small: the biggest non-snap frame stays well below the 2 m threshold times 2.
        CHECK_MSG(r.maxEasedStep < 2.0f * 2.0f * 70.0f, "maxEasedStep " + std::to_string(r.maxEasedStep));
    }
}

TEST(lossy, ghost_owner_after_handoff_is_rejected) {
    for (uint64_t seed = 1; seed <= 4; ++seed) {
        Opts o; o.profile = "bad_wifi"; o.seed = seed; o.ghost = true;
        Report r = run(o);
        CHECK_MSG(r.ok, r.why);
    }
}

TEST(lossy, forged_updates_from_a_bystander_are_rejected) {
    for (uint64_t seed = 1; seed <= 4; ++seed) {
        Opts o; o.profile = "good_wifi"; o.seed = seed; o.rogue = true;
        Report r = run(o);
        CHECK_MSG(r.ok, r.why);
    }
}

TEST(lossy, partition_holds_then_recovers) {
    for (uint64_t seed = 1; seed <= 4; ++seed) {
        Opts o; o.profile = "bad_wifi"; o.seed = seed; o.partition = true;
        Report r = run(o);
        if (verbose()) show("partition", o, r);
        CHECK_MSG(r.ok, r.why);
        CHECK(r.holding > 0);
    }
}

TEST(lossy, corrupted_datagrams_are_dropped_not_applied) {
    for (uint64_t seed = 1; seed <= 4; ++seed) {
        Opts o; o.profile = "bad_wifi"; o.seed = seed; o.corrupt = true;
        Report r = run(o);
        CHECK_MSG(r.ok, r.why);
    }
}

TEST(lossy, no_handoff_run_also_converges) {
    Opts o; o.profile = "mobile_tether"; o.handoff = false;
    Report r = run(o);
    CHECK_MSG(r.ok, r.why);
}

// ---- lossy twins: the same run with one known bug switched on must be flagged ------------------------------------
#define LOSSY_TWIN(name, flag, ...) \
    TEST(twin, name) { LiveFaults f; f.flag = true; LiveFaultScope s(f); Opts o; __VA_ARGS__; Report r = run(o); CHECK(!r.ok); }

LOSSY_TWIN(lossy_stale_seq, acceptStaleSeq, o.profile = "bad_wifi"; o.seed = 2)
LOSSY_TWIN(lossy_stale_epoch, acceptStaleEpoch, o.profile = "good_wifi"; o.ghost = true)
LOSSY_TWIN(lossy_non_owner, acceptNonOwner, o.profile = "good_wifi"; o.rogue = true)
LOSSY_TWIN(lossy_delta_from_latest, deltaFromLatest, o.profile = "bad_wifi"; o.seed = 3)
LOSSY_TWIN(lossy_drop_av, dropAvChanges, o.profile = "lan")
LOSSY_TWIN(lossy_no_extrap_cap, noExtrapolationCap, o.profile = "bad_wifi"; o.partition = true)
