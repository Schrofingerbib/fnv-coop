#include "spawn/spawn_rpc.h"
#include <cstdio>
#include <cstdlib>
#include "common/minijson.h"

namespace nvcoop::spawn {

namespace {
using nvcoop::json::Value;

bool parseId(const Value& v, SiteId& out) {
    if (v.isNumber()) { out = static_cast<SiteId>(v.asInt()); return true; }
    if (!v.isString()) return false;
    const std::string& s = v.asString();
    if (s.empty()) return false;
    char* end = nullptr;
    const unsigned long long x = std::strtoull(s.c_str(), &end, (s.size() > 1 && s[0] == '0' && (s[1] == 'x' || s[1] == 'X')) ? 16 : 10);
    if (!end || *end) return false;
    out = static_cast<SiteId>(x);
    return true;
}

Value siteValue(const SiteInfo& s) {
    Value v = Value::object();
    v.set("known", s.known);
    v.set("site", static_cast<int64_t>(s.site));
    if (!s.known) return v;
    v.set("state", sp::toString(s.state));
    v.set("owner", static_cast<int>(s.owner));
    v.set("epoch", static_cast<int64_t>(s.epoch));
    v.set("gen", static_cast<int64_t>(s.generation));
    v.set("mine", s.mine);
    v.set("interior", s.interior);
    v.set("members", static_cast<int64_t>(s.members));
    v.set("dead", static_cast<int64_t>(s.dead));
    return v;
}

Value statsValue(const Stats& s) {
    Value v = Value::object();
    v.set("rollsLocal", static_cast<int64_t>(s.rollsLocal));
    v.set("rollsHeld", static_cast<int64_t>(s.rollsHeld));
    v.set("rollsSuppressed", static_cast<int64_t>(s.rollsSuppressed));
    v.set("rollsAdopted", static_cast<int64_t>(s.rollsAdopted));
    v.set("sent", static_cast<int64_t>(s.sent));
    v.set("received", static_cast<int64_t>(s.received));
    v.set("undecodable", static_cast<int64_t>(s.undecodable));
    v.set("spoofed", static_cast<int64_t>(s.spoofed));
    v.set("digestsSent", static_cast<int64_t>(s.digestsSent));
    v.set("digestMismatch", static_cast<int64_t>(s.digestMismatch));
    v.set("healsSent", static_cast<int64_t>(s.healsSent));
    v.set("bound", static_cast<int64_t>(s.bound));
    v.set("shown", static_cast<int64_t>(s.shown));
    v.set("dropped", static_cast<int64_t>(s.dropped));
    v.set("rebased", static_cast<int64_t>(s.rebased));
    v.set("rebaseFailed", static_cast<int64_t>(s.rebaseFailed));
    v.set("madeDead", static_cast<int64_t>(s.madeDead));
    v.set("hostCopies", static_cast<int64_t>(s.hostCopies));
    v.set("spawnCommits", static_cast<int64_t>(s.spawnCommits));
    v.set("unresolvedSeen", static_cast<int64_t>(s.unresolvedSeen));
    v.set("detectedGroups", static_cast<int64_t>(s.detectedGroups));
    return v;
}

std::string hex64(uint64_t x) {
    char b[24];
    std::snprintf(b, sizeof b, "%016llx", static_cast<unsigned long long>(x));
    return b;
}
}  // namespace

std::string siteJson(const SiteInfo& s) { return siteValue(s).dump(); }
std::string statsJson(const Stats& s) { return statsValue(s).dump(); }

bool spawnRpc(const SpawnSync* sync, const std::string& paramsJson, std::string& out) {
    if (!sync) {
        out = "{\"unsupported\":true,\"reason\":\"spawn sync is not running (no coop session)\"}";
        return true;
    }
    Value p;
    std::string err;
    if (!nvcoop::json::parse(paramsJson.empty() ? "{}" : paramsJson, p, err)) { out = "bad params: " + err; return false; }
    if (const Value* s = p.find("site")) {
        SiteId id = 0;
        if (!parseId(*s, id)) { out = "bad site (a number or \"0x...\")"; return false; }
        out = siteJson(sync->siteInfo(id));
        return true;
    }
    if (p.get("list").asBool(false)) {
        const size_t max = static_cast<size_t>(p.get("max").asInt(50));
        Value v = Value::object();
        Value arr = Value::array();
        const std::vector<SiteInfo> all = sync->sites();
        size_t n = 0;
        for (const auto& s : all) {
            if (n++ >= max) break;
            arr.push(siteValue(s));
        }
        v.set("sites", arr);
        v.set("total", static_cast<int64_t>(all.size()));
        out = v.dump();
        return true;
    }
    Value v = Value::object();
    v.set("self", static_cast<int>(sync->self()));
    v.set("host", sync->isHost());
    v.set("link", sync->linkUp());
    v.set("sites", static_cast<int64_t>(sync->view().siteCount()));
    v.set("pending", static_cast<int64_t>(sync->pendingRolls()));
    v.set("waiting", static_cast<int64_t>(sync->waitingPlaced()));
    v.set("candidates", static_cast<int64_t>(sync->candidates()));
    v.set("digest", hex64(sync->digest()));
    v.set("stats", statsValue(sync->stats()));
    const sp::TrackerStats& t = sync->trackerStats();
    Value tv = Value::object();
    tv.set("begun", static_cast<int64_t>(t.begun));
    tv.set("accepted", static_cast<int64_t>(t.accepted));
    tv.set("rejected", static_cast<int64_t>(t.rejected));
    tv.set("resent", static_cast<int64_t>(t.resent));
    tv.set("retried", static_cast<int64_t>(t.retried));
    tv.set("gaveUp", static_cast<int64_t>(t.gaveUp));
    tv.set("droppedBound", static_cast<int64_t>(t.droppedBound));
    v.set("tracker", tv);
    if (const sp::SiteRegistry* r = sync->registry()) {
        const sp::RegistryStats& s = r->stats();
        Value rv = Value::object();
        rv.set("rolls", static_cast<int64_t>(s.rolls));
        rv.set("accepted", static_cast<int64_t>(s.accepted));
        rv.set("rejected", static_cast<int64_t>(s.rejected));
        rv.set("duplicates", static_cast<int64_t>(s.duplicates));
        rv.set("spoofed", static_cast<int64_t>(s.spoofed));
        rv.set("notOwner", static_cast<int64_t>(s.notOwner));
        rv.set("alreadySpawned", static_cast<int64_t>(s.alreadySpawned));
        rv.set("clearedPermanent", static_cast<int64_t>(s.clearedPermanent));
        rv.set("notDue", static_cast<int64_t>(s.notDue));
        rv.set("idsIssued", static_cast<int64_t>(s.idsIssued));
        rv.set("sitesCleared", static_cast<int64_t>(s.sitesCleared));
        rv.set("sitesRespawnable", static_cast<int64_t>(s.sitesRespawnable));
        rv.set("liveMembers", static_cast<int64_t>(r->liveMembers()));
        v.set("registry", rv);
    }
    out = v.dump();
    return true;
}

}  // namespace nvcoop::spawn
