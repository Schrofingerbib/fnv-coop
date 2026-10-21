#include "avatar_runtime.h"

#include <algorithm>
#include <cstdio>

#include "../live_bridge.h"
#include "fnvcoop_mut.h"

// Mutation twins in this file (tests/test_engine.cpp must fail for each):
// 60 loopback offset ignored            61 remote snapshots all go to the first remote   62 game events ignored
// 63 local publishing during a load     64 pose set before the first snapshot arrived    65 engine position not fed back to the buffer
// 66 status lacks the tagged count      67 equipment not forwarded to the loopback       68 loopback off leaves the puppet
// 69 local identity ignored             70 every snapshot is handed to the sink twice   71 a save that never reports back keeps puppets away for ever

namespace nvcoop::avatar::engine {

namespace {
EngineOptions MakeEngineOptions(const RuntimeConfig& c) {
    EngineOptions o = c.engine;
    for (const TemplateEntry& t : c.puppet.templates)
        if (t.base && std::find(o.taggedBases.begin(), o.taggedBases.end(), t.base) == o.taggedBases.end()) o.taggedBases.push_back(t.base);
    if (c.puppet.defaultTemplate && std::find(o.taggedBases.begin(), o.taggedBases.end(), c.puppet.defaultTemplate) == o.taggedBases.end())
        o.taggedBases.push_back(c.puppet.defaultTemplate);
    return o;
}
std::string Hex8(uint32_t v) {
    char b[16];
    std::snprintf(b, sizeof b, "0x%08X", v);
    return b;
}
}  // namespace

AvatarRuntime::AvatarRuntime(IGameBackend& be, RuntimeConfig cfg)
    : be_(be), cfg_(std::move(cfg)), eng_(be, MakeEngineOptions(cfg_)), pm_(eng_, cfg_.puppet), sampler_(cfg_.sampler), equip_(cfg_.equipPollMs) {}

void AvatarRuntime::SetLog(std::function<void(const std::string&)> log) {
    log_ = log;
    pm_.SetLog(std::move(log));
}

void AvatarRuntime::SetLocalIdentity(live::NetId netId, live::OwnerEpoch epoch) {
    netId_ = netId;
    if (!FNV_MUT(69)) sampler_.SetIdentity(netId, epoch);
}

// ---- remote players --------------------------------------------------------------------------------------------------

bool AvatarRuntime::AddRemote(Slot slot, const Appearance& a) {
    if (!pm_.AddPlayer(slot, a)) return false;
    remotes_[slot] = std::make_unique<Remote>(cfg_.interp);   // a re-added slot (reconnect) starts with an empty buffer
    return true;
}

void AvatarRuntime::RemoveRemote(Slot slot) {
    pm_.RemovePlayer(slot);
    remotes_.erase(slot);
}

bool AvatarRuntime::SetRemoteAppearance(Slot slot, const Appearance& a) {
    if (!remotes_.count(slot)) return false;
    return pm_.SetAppearance(slot, a);   // the interpolation buffer keeps running: the (new) puppet gets its pose on the next Tick
}

bool AvatarRuntime::ReadLocalAppearance(Appearance& out) { return be_.ReadAppearance(kPlayerRef, out); }

void AvatarRuntime::OnRemoteSnapshot(Slot slot, const live::ActorSnapshot& s, uint32_t arrivalMs) {
    auto it = remotes_.find(slot);
    if (FNV_MUT(61) && !remotes_.empty()) it = remotes_.begin();
    if (it == remotes_.end()) {
        ++droppedRemote_;
        return;
    }
    it->second->buf.push(s, arrivalMs);
    ++remoteSnaps_;
}

void AvatarRuntime::OnRemoteEquipment(Slot slot, const std::vector<EquipEntry>& wanted) { pm_.SetEquipment(slot, wanted); }

// ---- loopback ----------------------------------------------------------------------------------------------------------

bool AvatarRuntime::SetLoopback(bool on) {
    if (on == loopback_) return true;
    const Slot slot = cfg_.loopbackSlot;
    if (on) {
        if (slot == cfg_.puppet.localSlot) return false;
        Appearance a;
        be_.ReadAppearance(kPlayerRef, a);   // best effort: the template decides race / sex
        a.name = a.name.empty() ? "loopback" : a.name;
        if (!AddRemote(slot, a)) return false;
        if (!equip_.published().empty()) pm_.SetEquipment(slot, equip_.published());
        loopback_ = true;
        return true;
    }
    loopback_ = false;
    if (!FNV_MUT(68)) RemoveRemote(slot);
    return true;
}

// ---- per frame -----------------------------------------------------------------------------------------------------------

void AvatarRuntime::PublishLocal(uint32_t nowMs) {
    if (loading_ && !FNV_MUT(63)) return;
    live::ActorSnapshot s;
    if (sampler_.Sample(be_, nowMs, s)) {
        lastSnap_ = s;
        haveSnap_ = true;
        if (snapSink_) {
            snapSink_(s);
            if (FNV_MUT(70)) snapSink_(s);
        }
        if (loopback_) {
            auto it = remotes_.find(cfg_.loopbackSlot);
            if (it != remotes_.end()) {
                live::ActorSnapshot l = s;
                if (!FNV_MUT(60)) {
                    l.pos.x += cfg_.loopbackOffset.x;
                    l.pos.y += cfg_.loopbackOffset.y;
                    l.pos.z += cfg_.loopbackOffset.z;
                }
                it->second->buf.push(l, nowMs);
            }
        }
    }
    std::vector<EquipEntry> eq;
    if (equip_.Poll(be_, nowMs, eq)) {
        if (equipSink_) equipSink_(eq);
        if (loopback_ && !FNV_MUT(67)) pm_.SetEquipment(cfg_.loopbackSlot, eq);
    }
}

void AvatarRuntime::DriveRemotes(uint32_t nowMs) {
    for (auto& kv : remotes_) {
        const Slot slot = kv.first;
        Remote& r = *kv.second;
        CellId c = 0;
        Vec3 p;
        live::ActualPose ap;
        bool haveActual = !FNV_MUT(65) && pm_.ActualPose(slot, c, p);
        if (haveActual) {
            ap.cell = c;
            ap.pos = ToLive(p);
        }
        live::InterpOut o = r.buf.sample(nowMs, haveActual ? &ap : nullptr);
        if (o.state == live::InterpState::Empty && !FNV_MUT(64)) continue;   // nothing received yet: no puppet at a made-up place
        pm_.SetPose(slot, FromInterp(o));
        ++posesFed_;
        r.everSampled = true;
    }
}

void AvatarRuntime::Tick(uint32_t nowMs) {
    lastTick_ = nowMs;
    if (saveHeld_ && !FNV_MUT(71) && static_cast<int32_t>(nowMs - saveHeldAt_) >= static_cast<int32_t>(cfg_.saveHoldMs)) {
        Log("avatar: no SaveEnd after " + std::to_string(cfg_.saveHoldMs) + " ms, puppets may return");
        saveHeld_ = false;
        pm_.OnSaveEnd();
    }
    PublishLocal(nowMs);
    DriveRemotes(nowMs);
    pm_.Tick(nowMs);
}

void AvatarRuntime::OnEvent(GameEvent e) {
    if (FNV_MUT(62)) return;
    switch (e) {
        case GameEvent::SaveBegin:
            pm_.OnSaveBegin();
            saveHeld_ = true;
            saveHeldAt_ = lastTick_;
            break;
        case GameEvent::SaveEnd:
            saveHeld_ = false;
            pm_.OnSaveEnd();
            break;
        case GameEvent::LoadBegin:
            loading_ = true;
            pm_.OnLoadBegin();
            sampler_.Reset();
            equip_.Reset();
            for (auto& kv : remotes_) kv.second->buf.reset();   // positions from the old world mean nothing
            break;
        case GameEvent::LoadEnd:
            loading_ = false;
            pm_.OnLoadEnd();
            sampler_.Reset();
            break;
        case GameEvent::NewGame:
        case GameEvent::ExitToMenu:
            // the world is replaced without a Pre / PostLoad pair: drop every ref without touching the engine, purge when it is back
            pm_.OnLoadBegin();
            pm_.OnLoadEnd();
            sampler_.Reset();
            equip_.Reset();
            for (auto& kv : remotes_) kv.second->buf.reset();
            loading_ = false;
            break;
    }
}

// ---- status ----------------------------------------------------------------------------------------------------------------

std::string AvatarRuntime::StatusJson() {
    std::string s = pm_.StatusJson();
    if (!s.empty() && s.back() == '}') s.pop_back();
    char buf[400];
    std::snprintf(buf, sizeof buf, ",\"v\":1,\"enabled\":true,\"loopback\":%s,\"loopbackSlot\":%d,\"worldEpoch\":%u,\"remoteSnapshots\":%llu,\"droppedRemote\":%llu,\"posesFed\":%llu",
                  loopback_ ? "true" : "false", (int)cfg_.loopbackSlot, (unsigned)be_.WorldEpoch(), (unsigned long long)remoteSnaps_,
                  (unsigned long long)droppedRemote_, (unsigned long long)posesFed_);
    s += buf;
    if (!FNV_MUT(66)) s += ",\"tagged\":" + std::to_string(eng_.FindTaggedPuppets().size());
    s += ",\"engine\":" + eng_.StatsJson();
    s += ",\"local\":" + LocalJson();
    s += "}";
    return s;
}

std::string AvatarRuntime::LocalJson() {
    const LocalFrame& f = sampler_.lastFrame();
    const Vec3 v = sampler_.lastVelocity();
    const auto& c = sampler_.counters();
    char buf[700];
    std::snprintf(buf, sizeof buf,
                  "{\"have\":%s,\"cell\":\"%s\",\"pos\":[%.1f,%.1f,%.1f],\"rot\":[%.3f,%.3f,%.3f],\"vel\":[%.1f,%.1f,%.1f],\"loco\":%d,"
                  "\"weaponDrawn\":%s,\"sneak\":%s,\"jump\":%s,\"reload\":%s,\"aim\":%s,\"animReadable\":%s,\"published\":%llu,\"notReady\":%llu,"
                  "\"teleports\":%llu,\"cellChanges\":%llu,\"seq\":%u}",
                  f.valid ? "true" : "false", Hex8(f.cell).c_str(), f.pos.x, f.pos.y, f.pos.z, f.rot.x, f.rot.y, f.rot.z, v.x, v.y, v.z,
                  (int)sampler_.lastLoco(), f.anim.weaponDrawn ? "true" : "false", f.anim.sneak ? "true" : "false",
                  f.anim.jump ? "true" : "false", f.anim.reload ? "true" : "false", f.anim.aim ? "true" : "false",
                  f.animReadable ? "true" : "false", (unsigned long long)c.published, (unsigned long long)c.notReady,
                  (unsigned long long)c.teleports, (unsigned long long)c.cellChanges, haveSnap_ ? (unsigned)lastSnap_.seq : 0u);
    return buf;
}

}  // namespace nvcoop::avatar::engine
