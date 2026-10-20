// Adapter (T1a): turns T2a's IEngineObserver callbacks into flight-recorder records, one FlightRecorder per machine.
// Header only; include it where an enginemodel Cluster / Machine is driven (the fuzzer, tests). The model itself does not
// depend on the recorder.
//
//   fnvcoop::flight::EngineFlightObserver obs;
//   cluster.setObserver(&obs);
//   ... run ...
//   obs.dumpAll("out/simfuzz/fail");      // writes fail.m0.nvfr, fail.m1.nvfr, ...
#pragma once
#include <map>
#include <memory>
#include <string>
#include "flight/flight.h"
#include "fnvcoop/engine/observer.h"

namespace fnvcoop::flight {

class EngineFlightObserver : public engine::IEngineObserver {
public:
    explicit EngineFlightObserver(size_t ringBytes = 32u << 20) : ringBytes_(ringBytes) {}

    FlightRecorder& machine(PlayerId self) {
        auto it = recs_.find(self);
        if (it != recs_.end()) return *it->second.rec;
        Slot& s = recs_[self];
        Config c;
        c.slot = self;
        c.role = self == kHostPlayer ? kRoleHost : kRoleClient;
        c.label = "sim-m" + std::to_string(self);
        c.ringBytes = ringBytes_;
        s.rec.reset(new FlightRecorder(c));
        uint64_t* now = &s.nowMs;   // map nodes are stable
        s.rec->setClockSource([now] { return *now * 1000; });
        s.rec->setWallStart(0);
        return *s.rec;
    }

    // Writes <base>.m<slot>.nvfr for every machine seen. Returns the number of files written.
    int dumpAll(const std::string& base, uint8_t reason = kDumpManual) {
        int n = 0;
        for (auto& [self, s] : recs_) if (s.rec->dump(base + ".m" + std::to_string(self) + ".nvfr", reason)) ++n;
        return n;
    }

    void onSend(PlayerId self, uint64_t nowMs, PeerId to, uint8_t channel, const Bytes& env) override { at(self, nowMs).netOut(to, env, channel); }
    void onRecv(PlayerId self, uint64_t nowMs, PeerId from, uint8_t, const Bytes& env) override { at(self, nowMs).netIn(from, env); }
    void onDrop(PlayerId self, uint64_t nowMs, PeerId from, const char* reason) override {
        at(self, nowMs).marker(kMarkNote, "dropped datagram from peer " + std::to_string(from) + ": " + (reason ? reason : ""));
    }
    void onOwnership(PlayerId self, uint64_t nowMs, const OwnMsg& m, ApplyResult r) override {
        FlightRecorder& rec = at(self, nowMs);
        OwnershipEvent e;
        e.actor = m.netId;
        e.epoch = m.epoch;
        e.fromOwner = m.prevOwner == kNoPlayer ? kNoSlot : m.prevOwner;
        e.toOwner = m.owner == kNoPlayer ? kNoSlot : m.owner;
        if (r == ApplyResult::Stale) e.op = kOwnRejectStale;
        else if (r == ApplyResult::Duplicate) e.op = kOwnRejectEcho;
        else if (m.kind == OwnMsg::Grant) e.op = kOwnGrant;
        else if (m.kind == OwnMsg::Release) e.op = kOwnRelease;
        else if (m.kind == OwnMsg::Transfer || m.kind == OwnMsg::Commit) e.op = kOwnTransfer;
        else { rec.marker(kMarkNote, "ownership ack for actor " + std::to_string(m.netId) + " epoch " + std::to_string(m.epoch)); return; }
        rec.ownership(e);
    }
    void onActorUpdate(PlayerId self, uint64_t nowMs, engine::NetId id, PlayerId from, OwnerEpoch epoch, UpdateVerdict v) override {
        if (v == UpdateVerdict::Accept) return;
        OwnershipEvent e;
        e.actor = id;
        e.epoch = epoch;
        e.fromOwner = from == kNoPlayer ? kNoSlot : from;
        e.toOwner = self;
        e.op = v == UpdateVerdict::EchoOfSelf ? kOwnRejectEcho : (v == UpdateVerdict::NotOwner || v == UpdateVerdict::NotYetActive) ? kOwnRejectNonOwner : kOwnRejectStale;
        at(self, nowMs).ownership(e);
    }
    void onEffectApplied(PlayerId self, uint64_t nowMs, uint64_t txnSeq, const Effect& e) override {
        IEffectSink::Context ctx;
        ctx.seq = txnSeq;
        at(self, nowMs).applied(e, ctx);
    }
    void onDigest(PlayerId self, uint64_t nowMs, engine::Category cat, uint64_t hash) override {
        DigestEvent d;
        d.category = static_cast<uint8_t>(cat);
        d.seq = nowMs;   // the model digests on a shared virtual clock: same time = same logical point
        d.hash = hash;
        at(self, nowMs).digest(d);
    }
    void onRepair(PlayerId self, uint64_t nowMs, PlayerId target, engine::Category cat, size_t keys) override {
        at(self, nowMs).marker(kMarkNote, "repair of slot " + std::to_string(target) + " category " + std::to_string(static_cast<int>(cat)) + ", " + std::to_string(keys) + " keys");
    }

private:
    struct Slot { std::unique_ptr<FlightRecorder> rec; uint64_t nowMs = 0; };
    FlightRecorder& at(PlayerId self, uint64_t nowMs) {
        FlightRecorder& r = machine(self);
        Slot& s = recs_[self];
        s.nowMs = nowMs;
        r.setFrame(static_cast<uint32_t>(nowMs / 16));
        return r;
    }
    size_t ringBytes_;
    std::map<PlayerId, Slot> recs_;
};

}  // namespace fnvcoop::flight
