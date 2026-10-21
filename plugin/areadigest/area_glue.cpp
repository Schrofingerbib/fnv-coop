// Plugin glue for plugin/areadigest (x86, compiled into nvcoop.dll next to the coop module). See area_glue.h.
#include "areadigest/area_glue.h"

#include <chrono>
#include <memory>
#include "areadigest/area_service.h"
#include "common/nvcoop_api.h"
#include "coop_runtime.h"
#ifdef NVCOOP_WITH_FLIGHT
#include "flight/flight_service.h"
#endif
#ifdef NVCOOP_AREADIGEST_FACADE
#include "areadigest/engine_facade.h"
#endif

namespace nvcoop::areadigest {

namespace {

AreaSettings g_set;
bool g_configured = false;
std::unique_ptr<AreaService> g_svc;
nvcoop::coop::Runtime* g_rt = nullptr;     // updated every frame; the hooks below read the session through it
CellOfRef g_cellOf;

uint64_t nowMs() {
    return static_cast<uint64_t>(std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now().time_since_epoch()).count());
}

fnvcoop::flight::FlightRecorder* recorder() {
#ifdef NVCOOP_WITH_FLIGHT
    return nvcoop::flight::service().recorder();
#else
    return nullptr;
#endif
}

bool sessionWorld(ShadowStorySource::World& w) {
    if (!g_rt) return false;
    if (auto* h = g_rt->host()) {
        w.state = &h->state();
        w.seq = h->log().head();
        w.epoch = h->epoch();
        return true;
    }
    if (auto* c = g_rt->client()) {
        if (!c->joined() || c->baselineActive()) return false;   // mid-join the shadow is not a comparable state
        w.state = &c->state();
        w.seq = c->applied();
        w.epoch = c->worldEpoch();
        return true;
    }
    return false;
}

ServiceInputs makeInputs() {
    ServiceInputs in;
#ifdef NVCOOP_AREADIGEST_FACADE
    in.actors = engineFacade();
#endif
    in.world = sessionWorld;
    in.repair = [](fnvcoop::PlayerId slot) {
        if (g_rt && g_rt->host()) g_rt->host()->requestDigest(slot);
    };
    in.repairCount = []() -> uint64_t { return g_rt && g_rt->host() ? g_rt->host()->stats().desyncRepairs : 0; };
    in.cellOf = g_cellOf;
    in.flight = recorder();
    return in;
}

}  // namespace

void glueConfigure(const IniGetter& get) {
    g_set = readSettings(get);
    g_configured = true;
    nvcoop::Log("areadigest: %s", describe(g_set).c_str());
    if (!g_set.warnings.empty()) nvcoop::Log("areadigest: %s", g_set.warnings.c_str());
}

bool glueEnabled() { return g_configured && g_set.enabled; }
const AreaSettings& glueSettings() { return g_set; }
void setCellOfRef(CellOfRef f) { g_cellOf = std::move(f); }

void glueFrame(nvcoop::coop::Runtime* rt) {
    if (!glueEnabled()) return;
    g_rt = rt;
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
    if (!g_svc) {
        if (!f.active) return;
        g_svc.reset(new AreaService(g_set, makeInputs()));
        setActive(&g_svc->runtime());
    }
    g_svc->frame(f);
}

bool glueRunning() { return g_svc && g_svc->sync(); }

std::string glueStatusJson() {
    if (!glueEnabled()) return "{\"enabled\":false}";
    if (!g_svc) return "{\"enabled\":true,\"running\":false,\"settings\":\"" + describe(g_set) + "\"}";
    return g_svc->statusJson(nowMs());
}

bool glueExportFlight(std::string& out) {
    if (!g_svc) { out = "area digest is not running"; return false; }
    if (!recorder()) { out = "flight recorder is not running"; return false; }
    g_svc->exportFlight(nowMs());
    out = "{\"exported\":true}";
    return true;
}

void glueWorldLoaded() {
    if (g_svc) { setActive(nullptr); g_svc.reset(); }   // rebuilt on the next session frame: trackers, smoothing jobs and cases start over
}

void glueShutdown() {
    setActive(nullptr);
    g_svc.reset();
    g_rt = nullptr;
}

}  // namespace nvcoop::areadigest
