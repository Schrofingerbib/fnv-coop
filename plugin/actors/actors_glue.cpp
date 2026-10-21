// Plugin glue for plugin/actors (x86, compiled into nvcoop.dll next to the coop module). See actor_glue.h.
#include "actors/actor_glue.h"
#include <chrono>
#include <memory>
#include "actors/owner_rpc.h"
#include "coop_runtime.h"

namespace nvcoop::actors {

namespace {

// No engine behind it: nothing is ever "loaded", so ActorSync keeps its network records and the ownership table but makes no engine call.
class NullActorPort : public IActorPort {
public:
    bool loaded(NetId) override { return false; }
    bool cellLoaded(CellId) override { return false; }
    bool sample(NetId, live::ActorSnapshot&) override { return false; }
    bool pose(NetId, live::ActualPose&) override { return false; }
    void drive(NetId, const live::InterpOut&) override {}
    void applyAv(NetId, const std::vector<live::AvValue>&) override {}
    void place(NetId, const live::ActorSnapshot&) override {}
    void setAi(NetId, bool) override {}
    void setProtected(NetId, bool) override {}
    bool spawnStandIn(const ActorMeta&, const live::ActorSnapshot&) override { return false; }
    void removeStandIn(NetId) override {}
    void setBase(NetId, const FormRef&) override {}
    void kill(NetId) override {}
    void revive(NetId) override {}
};

NullActorPort g_null;
IActorPort* g_port = &g_null;
std::unique_ptr<ActorRuntime> g_runtime;
IActorPort* g_runtimePort = nullptr;
std::function<void(SyncConfig&)> g_configurer;

uint64_t nowMs() {
    return static_cast<uint64_t>(std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now().time_since_epoch()).count());
}

}  // namespace

void setGamePort(IActorPort* port) {
    g_port = port ? port : &g_null;
    if (g_runtime && g_runtimePort != g_port) { setActive(nullptr); g_runtime.reset(); }   // rebuilt with the new port on the next frame
}

void setSyncConfigurer(std::function<void(SyncConfig&)> f) { g_configurer = std::move(f); }
uint64_t glueSessionGeneration() { return g_runtime ? g_runtime->rebuilds() : 0; }

bool glueRunning() { return g_runtime && g_runtime->sync(); }
ActorSync* glueSync() { return g_runtime ? g_runtime->sync() : nullptr; }

void glueFrame(nvcoop::coop::Runtime* rt) {
    FrameInfo f;
    if (rt && rt->running()) {
        f.active = true;
        f.mux = rt->mux();
        f.nowMs = nowMs();
        if (auto* host = rt->host()) {
            f.isHost = true;
            f.self = fnvcoop::kHostPlayer;
            for (const auto& cv : host->clients()) {
                ClientLink l;
                l.slot = cv.slot; l.peer = cv.peer; l.joined = cv.joined;
                f.clients.push_back(l);
            }
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
        g_runtime.reset(new ActorRuntime(*g_port));
        g_runtimePort = g_port;
        if (g_configurer) g_configurer(g_runtime->config());
        setActive(g_runtime.get());
    }
    g_runtime->frame(f);
}

bool glueOwnerRpc(const std::string& paramsJson, std::string& out) {
    return ownerRpc(g_runtime ? g_runtime->sync() : nullptr, paramsJson, out);
}

}  // namespace nvcoop::actors
