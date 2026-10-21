// T1a plugin-side tests: the Service (ini, dump, crash path) and the recorder wired into real host + client Runtimes over loopback
// ENet. Run with FNVCOOP_MUT=n (runtime twins, tools/mut/fnvcoop_mut.h) for the mutation twins (each wiring bug must make this program FAIL):
//   1 host recorder not attached   2 client recorder not attached   3 recorder attached after start()
#include <chrono>
#include <cstdio>
#include <filesystem>
#include <functional>
#include <map>
#include <memory>
#include <thread>

#include "fake_world.h"
#include "flight/flight_service.h"

using namespace nvcoop::coop;
using namespace nvcoop::coop::test;
namespace fl = fnvcoop::flight;
namespace fs = std::filesystem;

#include "fnvcoop_mut.h"
#define FLIGHT_MUT (fnvcoop_mut::current())   // runtime twin number (env FNVCOOP_MUT=n); this test is always built with FNVCOOP_RUNTIME_MUT

static int g_checks = 0, g_failed = 0;
#define CHECK(c)                                                                                     \
    do {                                                                                             \
        ++g_checks;                                                                                  \
        if (!(c)) { ++g_failed; std::printf("  FAIL %s:%d  %s\n", __FILE__, __LINE__, #c); }          \
    } while (0)

static uint64_t nowMs() {
    return std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now().time_since_epoch()).count();
}
static bool pumpUntil(const std::function<void()>& frame, const std::function<bool()>& done, int timeoutMs = 8000) {
    uint64_t end = nowMs() + timeoutMs;
    while (nowMs() < end) {
        frame();
        if (done()) return true;
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    return done();
}
static fnvcoop::FormRef R(const char* plugin, uint32_t id) { return nvhooks::makeRef(plugin, id); }

static std::string g_tmp;

struct Node {
    nvhooks::CaptureHub hub;
    FakeWorld world;
    std::unique_ptr<fl::FlightRecorder> rec;
    std::unique_ptr<Runtime> rt;
    Node(Role role, uint16_t port, uint64_t token, bool baseline, int mutNoAttach, const std::string& name, uint32_t digestMs = 10000) {
        world.seed(5);
        Config c;
        c.enable = true;
        c.role = role;
        c.port = port;
        c.token = token;
        c.transport = "enet";
        c.baseline = baseline;
        c.digestIntervalMs = digestMs;
        fl::Config fc;
        fc.label = name;
        fc.dir = g_tmp + "/" + name;
        fc.autoDumpMinIntervalMs = 0;
        rec.reset(new fl::FlightRecorder(fc));
        rt.reset(new Runtime(c, &hub, &world, nowMs));
        rt->setDumper(&world);
        if (FLIGHT_MUT != mutNoAttach && FLIGHT_MUT != 3) rt->setFlight(rec.get());
        std::string err;
        if (!rt->start(&err)) std::printf("  start failed: %s\n", err.c_str());
        if (FLIGHT_MUT == 3) rt->setFlight(rec.get());   // too late: the transport / sink are already built
    }
};

static size_t countType(const fl::Trace& t, uint8_t type) { size_t n = 0; for (auto& r : t.records) if (r.type == type) ++n; return n; }

static void runVcg02(FakeWorld& w, nvhooks::CaptureHub& hub) {
    using namespace fnvcoop;
    nvhooks::ScopedCapture outer(hub, Cause{CauseKind::QuestResult, R("FalloutNV.esm", 0x10A214), 10});
    w.hostMutate(hub, SetStage{R("FalloutNV.esm", 0x10A214), 10});
    w.hostMutate(hub, SetGlobal{R("FalloutNV.esm", 0x123456), 3.0f});
    w.hostMutate(hub, AddItem{R("FalloutNV.esm", 0xF), 100, Recipient::dflt()});
    w.hostMutate(hub, EnableRef{R("FalloutNV.esm", 0x777)});
}

static void testConfigAndService() {
    std::printf("service: ini + dump + crash path\n");
    nvcoop::flight::ServiceConfig c;
    CHECK(nvcoop::flight::parseServiceConfig("[coop]\nenable=0\n[flight]\nenable=1\ndir=C:\\x\\fl\nringMb=4\ncrash=0\nlabel=host\n[other]\nringMb=99\n", &c));
    CHECK(c.enable && c.dir == "C:\\x\\fl" && c.ringMb == 4 && !c.crashDump && c.label == "host");
    nvcoop::flight::ServiceConfig d;
    nvcoop::flight::parseServiceConfig("", &d);
    CHECK(d.enable && d.ringMb == 16 && d.crashDump);
    nvcoop::flight::ServiceConfig off;
    nvcoop::flight::parseServiceConfig("[flight]\nenable=0\n", &off);
    CHECK(!off.enable);
    nvcoop::flight::ServiceConfig bad;
    nvcoop::flight::parseServiceConfig("[flight]\nringMb=0\n", &bad);
    CHECK(bad.ringMb == 16);   // out of range keeps the default

    nvcoop::flight::Service s;
    CHECK(!s.init(off, fl::kNoSlot, fl::kRoleSim));
    std::string out;
    CHECK(!s.dumpJson("", out));   // off: refuses with a message
    nvcoop::flight::ServiceConfig on;
    on.dir = g_tmp + "/svc";
    on.crashDump = false;
    CHECK(s.init(on, 0, fl::kRoleHost));
    s.recorder()->marker(fl::kMarkNote, "hello");
    CHECK(s.dumpJson("", out));
    nvcoop::json::Value v;
    std::string err;
    CHECK(nvcoop::json::parse(out, v, err));
    std::string path = v.get("path").asString("");
    fl::Trace t;
    CHECK(!path.empty() && fl::readTrace(path, t) && t.header.reason == fl::kDumpManual && t.header.role == fl::kRoleHost);
    std::string p2 = g_tmp + "/svc/explicit.nvfr";
    for (char& ch : p2) if (ch == '\\') ch = '/';   // JSON string: no backslashes
    CHECK(s.dumpJson("{\"path\":\"" + p2 + "\"}", out));
    CHECK(fs::exists(p2));
    CHECK(nvcoop::json::parse(s.statusJson(), v, err) && v.get("enabled").asBool(false) && v.get("dumps").asInt() >= 2);
    std::string cp = s.crashDumpNow();
    fl::Trace ct;
    CHECK(!cp.empty() && fl::readTrace(cp, ct) && ct.header.reason == fl::kDumpCrash);
    s.installCrashHandler();
    s.installCrashHandler();   // idempotent
    s.removeCrashHandler();
}

static void testRuntimes() {
    std::printf("runtime: host + client record the same story\n");
    Node host(Role::Host, 0, 0, false, 1, "host");
    CHECK(host.rt->running());
    Node cli(Role::Client, host.rt->boundPort(), 1001, false, 2, "client");
    auto frameAll = [&] { host.rt->frame(); cli.rt->frame(); };
    CHECK(pumpUntil(frameAll, [&] { return cli.rt->client()->joined(); }));
    runVcg02(host.world, host.hub);
    CHECK(pumpUntil(frameAll, [&] { return cli.rt->client()->applied() == 1 && host.rt->host()->allSynced(); }));
    // two seconds so both sides write the story digest for seq 1
    uint64_t until = nowMs() + 2300;
    while (nowMs() < until) { frameAll(); std::this_thread::sleep_for(std::chrono::milliseconds(2)); }

    std::string hp = host.rec->dumpToDir(fl::kDumpTest), cp = cli.rec->dumpToDir(fl::kDumpTest);
    fl::Trace ht, ct;
    CHECK(!hp.empty() && fl::readTrace(hp, ht));
    CHECK(!cp.empty() && fl::readTrace(cp, ct));
    if (hp.empty() || cp.empty()) return;
    CHECK(ht.header.role == fl::kRoleHost && ht.header.slot == 0);
    CHECK(ct.header.role == fl::kRoleClient && ct.header.slot == cli.rt->client()->slot());

    uint64_t hostSentMax = 0, cliGotMax = 0;
    for (auto& r : ht.records) { fl::NetInfo n; if (r.type == fl::kNetOut && fl::parseNet(r, n) && n.msgType == 4) hostSentMax = std::max(hostSentMax, n.seqHi); }
    for (auto& r : ct.records) { fl::NetInfo n; if (r.type == fl::kNetIn && fl::parseNet(r, n) && n.msgType == 4) cliGotMax = std::max(cliGotMax, n.seqHi); }
    CHECK(hostSentMax == 1 && cliGotMax == 1);

    bool hostCaptured = false;
    for (auto& r : ht.records) if (r.type == fl::kTxnCaptured && r.payload.size() > 14 && r.payload[0] == 0 && r.payload[5] == 1) hostCaptured = true;   // kind 0, seq 1
    CHECK(hostCaptured);

    size_t appliedSeq1 = 0; bool sawSetStage = false;
    for (auto& r : ct.records) if (r.type == fl::kTxnApplied && r.payload[0] == 1) { ++appliedSeq1; if (r.payload[19] == 0) sawSetStage = true; }
    CHECK(appliedSeq1 >= 1 && sawSetStage);

    // story digests: same seq => same hash on both machines
    std::map<uint64_t, uint64_t> hostD, cliD;
    for (auto& r : ht.records) { fl::DigestEvent d; if (!(r.flags & fl::kFlagDerived) && fl::parseDigest(r, d) && d.category == fl::kCatStory) hostD[d.seq] = d.hash; }
    for (auto& r : ct.records) { fl::DigestEvent d; if (!(r.flags & fl::kFlagDerived) && fl::parseDigest(r, d) && d.category == fl::kCatStory) cliD[d.seq] = d.hash; }
    CHECK(hostD.count(1) && cliD.count(1));
    if (hostD.count(1) && cliD.count(1)) CHECK(hostD[1] == cliD[1]);
    CHECK(countType(ht, fl::kNetIn) > 0 && countType(ct, fl::kNetOut) > 0);
}

static void testDesyncDump() {
    std::printf("runtime: a desync writes a dump on the host\n");
    Node host(Role::Host, 0, 0, true, 1, "dhost");
    Node cli(Role::Client, host.rt->boundPort(), 2002, true, 2, "dclient", 150);
    auto frameAll = [&] { host.rt->frame(); cli.rt->frame(); };
    CHECK(pumpUntil(frameAll, [&] { return cli.rt->client()->joined() && cli.rt->client()->baselineDone(); }));
    runVcg02(host.world, host.hub);
    CHECK(pumpUntil(frameAll, [&] { return cli.rt->client()->applied() == 1 && host.rt->host()->allSynced(); }));
    cli.world.stages["falloutnv.esm:000203"] = 77;   // the client's real world drifts behind everyone's back
    bool seen = pumpUntil(frameAll, [&] { return host.rt->host()->stats().digestsMismatch > 0; }, 6000);
    CHECK(seen);
    std::error_code ec;
    // the host repairs the client after it sees the mismatch; wait for the client's own desync dump (a loaded PC needs more than a fixed pause)
    pumpUntil(frameAll, [&] {
        for (auto& e : fs::directory_iterator(g_tmp + "/dclient", ec)) { fl::Trace t; if (fl::readTrace(e.path().string(), t) && t.header.reason == fl::kDumpDesync) return true; }
        return false;
    }, 15000);
    pumpUntil(frameAll, [&] { return false; }, 150);
    size_t hostDumps = 0, cliDumps = 0;
    for (auto& e : fs::directory_iterator(g_tmp + "/dhost", ec)) { fl::Trace t; if (fl::readTrace(e.path().string(), t) && t.header.reason == fl::kDumpDesync) ++hostDumps; }
    for (auto& e : fs::directory_iterator(g_tmp + "/dclient", ec)) { fl::Trace t; if (fl::readTrace(e.path().string(), t) && t.header.reason == fl::kDumpDesync) ++cliDumps; }
    CHECK(hostDumps >= 1);
    std::printf("    host desync dumps %zu, client desync dumps %zu\n", hostDumps, cliDumps);

    // Per-key pinpoint: both desync dumps carry the 256-bucket digest and every entry of the world they compared; diffing the two
    // names exactly the drifted key (stage of quest 0x203: host 0, client 77) and its value text on the client.
    struct Side { std::vector<uint32_t> buckets; std::map<uint64_t, uint32_t> detail; std::map<uint64_t, std::string> text; bool ok = false; };
    auto load = [&](const std::string& dir) {
        Side sd;
        std::error_code e2;
        for (auto& e : fs::directory_iterator(dir, e2)) {
            fl::Trace t;
            if (!fl::readTrace(e.path().string(), t) || t.header.reason != fl::kDumpDesync) continue;
            for (auto& r : t.records) {
                fl::DigestEvent d;
                if (fl::parseDigest(r, d) && d.category == fl::kCatWorld) {
                    if (r.flags & fl::kFlagDetail) for (auto& kv : d.keys) sd.detail[kv.first] = kv.second;
                    else if (!d.shards.empty()) { sd.buckets = d.shards; sd.ok = true; }
                }
                if (r.type == fl::kMarker && r.payload.size() > 8 && std::string(r.payload.begin() + 4, r.payload.begin() + 10) == "entry ") {
                    std::string txt(r.payload.begin() + 4, r.payload.end());
                    unsigned long long k = std::strtoull(txt.c_str() + 6, nullptr, 16);
                    sd.text[k] = txt;
                }
            }
        }
        return sd;
    };
    Side hs = load(g_tmp + "/dhost"), cs = load(g_tmp + "/dclient");
    CHECK(hs.ok && cs.ok && hs.buckets.size() == 256 && cs.buckets.size() == 256);
    if (hs.ok && cs.ok) {
        std::vector<size_t> bd;
        for (size_t i = 0; i < 256; ++i) if (hs.buckets[i] != cs.buckets[i]) bd.push_back(i);
        CHECK(bd.size() == 1);
        std::vector<uint64_t> kd;
        for (auto& kv : hs.detail) { auto it = cs.detail.find(kv.first); if (it == cs.detail.end() || it->second != kv.second) kd.push_back(kv.first); }
        for (auto& kv : cs.detail) if (!hs.detail.count(kv.first)) kd.push_back(kv.first);
        CHECK(kd.size() == 1);
        if (kd.size() == 1) {
            CHECK(bd.size() == 1 && bd[0] == (kd[0] & 0xFF));
            std::printf("    pinpointed key %016llx: host \"%s\" / client \"%s\"\n", (unsigned long long)kd[0], hs.text[kd[0]].c_str(), cs.text[kd[0]].c_str());
            CHECK(cs.text[kd[0]].find("77") != std::string::npos && hs.text[kd[0]].find("77") == std::string::npos);
            CHECK(cs.text[kd[0]].find("203") != std::string::npos);
        }
    }
}

int main(int argc, char** argv) {
    g_tmp = (argc > 1) ? argv[1] : "flight_tmp";
    std::error_code ec;
    fs::remove_all(g_tmp, ec);
    fs::create_directories(g_tmp, ec);
    testConfigAndService();
    testRuntimes();
    testDesyncDump();
    if (!std::getenv("FNVCOOP_FLIGHT_KEEP")) fs::remove_all(g_tmp, ec);   // KEEP=1 leaves dhost/dclient for the Python analyzer
    std::printf("== %d checks, %d failed%s ==\n", g_checks, g_failed, g_failed ? "" : " ==");
    if (!g_failed) std::printf("ALL OK\n");
    return g_failed ? 1 : 0;
}
