// Plugin glue for plugin/spawn (x86, compiled into nvcoop.dll next to the coop module). See spawn_glue.h.
#include "spawn/spawn_glue.h"
#include <chrono>
#include <memory>
#include "coop_runtime.h"
#include "fnvcoop/wire.h"
#include "spawn/spawn_rpc.h"

namespace nvcoop::spawn {

namespace {

// No engine behind it: nothing is ever loaded or rolled; the table is still mirrored and answered by coop.spawn.
class NullSpawnEngine : public ISpawnEngine {
public:
    bool describe(RefId, RefInfo&) override { return false; }
    RefId refOfNet(NetId) override { return 0; }
    bool setHeld(RefId, bool) override { return false; }
    bool deleteRef(RefId) override { return false; }
    bool rebase(RefId, const FormRef&) override { return false; }
    bool kill(RefId) override { return false; }
    void bindCreated(RefId, NetId) override {}
    bool isCreatedBound(NetId) override { return false; }
    bool netOfCreated(RefId, NetId&) override { return false; }
    bool createFromSpawnRef(const fnvcoop::SpawnRef&) override { return false; }
};

NullSpawnEngine g_null;
ISpawnEngine* g_engine = &g_null;
ISpawnEngine* g_runtimeEngine = nullptr;
std::unique_ptr<SpawnRuntime> g_runtime;
std::function<uint64_t()> g_gameClock;
std::function<bool(RefId)> g_ignore;
SyncConfig g_cfg;

uint64_t nowMs() {
    return static_cast<uint64_t>(std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now().time_since_epoch()).count());
}

}  // namespace

void setEngine(ISpawnEngine* eng) {
    g_engine = eng ? eng : &g_null;
    if (g_runtime && g_runtimeEngine != g_engine) { setActive(nullptr); g_runtime.reset(); }   // rebuilt with the new engine on the next frame
}

void setGameClock(std::function<uint64_t()> f) { g_gameClock = std::move(f); }
void setIgnore(std::function<bool(RefId)> f) {
    g_ignore = std::move(f);
    if (g_runtime) g_runtime->setIgnore(g_ignore);
}
SyncConfig& config() { return g_cfg; }

bool glueRunning() { return g_runtime && g_runtime->sync(); }
SpawnSync* glueSync() { return g_runtime ? g_runtime->sync() : nullptr; }

void glueFrame(nvcoop::coop::Runtime* rt) {
    FrameInfo f;
    if (rt && rt->running()) {
        f.active = true;
        f.mux = rt->mux();
        f.nowMs = nowMs();
        f.gameMs = g_gameClock ? g_gameClock() : f.nowMs;
        if (auto* host = rt->host()) {
            f.isHost = true;
            f.self = fnvcoop::kHostPlayer;
            for (const auto& cv : host->clients()) {
                ClientLink l;
                l.slot = cv.slot; l.peer = cv.peer; l.joined = cv.joined;
                f.clients.push_back(l);
            }
            f.commit = [host](PlayerId origin, const fnvcoop::SpawnRef& s) {
                fnvcoop::Cause c;
                c.kind = fnvcoop::CauseKind::Script;
                std::vector<fnvcoop::Effect> fx;
                fx.push_back(fnvcoop::Effect(s));
                host->commit(origin, c, std::move(fx));
            };
        } else if (auto* cl = rt->client()) {
            f.isHost = false;
            f.self = cl->joined() ? cl->slot() : fnvcoop::kNoPlayer;
            f.hostLinkUp = cl->joined() && !cl->baselineActive();
        } else {
            f.active = false;
        }
    }
    if (!g_runtime) {
        if (!f.active) return;
        g_runtime.reset(new SpawnRuntime(*g_engine, g_cfg));
        g_runtimeEngine = g_engine;
        if (g_ignore) g_runtime->setIgnore(g_ignore);
        setActive(g_runtime.get());
    }
    g_runtime->frame(f);
}

bool glueRpc(const std::string& paramsJson, std::string& out) {
    return spawnRpc(g_runtime ? g_runtime->sync() : nullptr, paramsJson, out);
}

bool createdIdResolver(RefId ref, NetId& netId, fnvcoop::FormRef& wireRef) {
    SpawnSync* s = glueSync();
    return s && s->resolveCreated(ref, netId, wireRef);
}
void onPlacedLoaded(RefId ref) { if (SpawnSync* s = glueSync()) s->onPlacedLoaded(ref); }
void onActorDied(NetId id) { if (SpawnSync* s = glueSync()) s->onMemberDied(id); }
void onCellReset(CellId cell) { if (SpawnSync* s = glueSync()) s->onCellReset(cell); }
void onHostSpawnEffect(const sp::SiteDef& def, const fnvcoop::SpawnRef& sr) { if (SpawnSync* s = glueSync()) s->onHostSpawnEffect(def, sr); }
void onSiteCellLoaded(CellId cell) { if (SpawnSync* s = glueSync()) s->onSiteCellLoaded(cell); }
void onWorldLoaded() { if (SpawnSync* s = glueSync()) s->purge(); }
void setPlayerPose(PlayerId slot, bool present, CellId cell, const sp::Vec3& pos) {
    SpawnSync* s = glueSync();
    if (!s) return;
    sp::PlayerLoc l;
    l.present = present; l.cell = cell; l.pos = pos;
    s->setPlayerLoc(slot, l);
}

fnvcoop::Bytes saveState() {
    SpawnSync* s = glueSync();
    return s ? s->saveState() : fnvcoop::Bytes();
}
bool loadState(const fnvcoop::Bytes& b) {
    SpawnSync* s = glueSync();
    return s && s->loadState(b);
}

}  // namespace nvcoop::spawn
