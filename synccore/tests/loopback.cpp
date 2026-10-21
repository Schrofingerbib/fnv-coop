// Two-process (host + N client processes) localhost test of the ENet transport carrying the full session protocol,
// with the seeded fault-injection wrapper on top. `fnvcoop_loopback selftest [loss%]` orchestrates the child processes.
//   host:   fnvcoop_loopback host   --port P --seed S --txns N --clients C --loss L --out F [--unreliable]
//   client: fnvcoop_loopback client --port P --seed S --txns N --token T --loss L --out F [--unreliable]
#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#else
#include <signal.h>
#include <spawn.h>
#include <sys/wait.h>
#include <unistd.h>
extern char** environ;
#endif
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <map>
#include <sstream>
#include <string>
#include <thread>
#include "fnvcoop/enet_transport.h"
#include "fnvcoop/session.h"
#include "fnvcoop/transport_util.h"
#include "fnvcoop/mutations.h"
#include "sim.h"

using namespace fnvcoop;
using Clock = std::chrono::steady_clock;

namespace {
std::map<std::string, std::string> parseArgs(int argc, char** argv, int from) {
    std::map<std::string, std::string> m;
    for (int i = from; i < argc; ++i) {
        std::string k = argv[i];
        if (k.rfind("--", 0) != 0) continue;
        k = k.substr(2);
        if (i + 1 < argc && std::strncmp(argv[i + 1], "--", 2) != 0) m[k] = argv[++i]; else m[k] = "1";
    }
    return m;
}
uint64_t nowMs(Clock::time_point t0) { return uint64_t(std::chrono::duration_cast<std::chrono::milliseconds>(Clock::now() - t0).count()); }

// Forces every send onto the unreliable ENet channel (the session protocol must cope).
class ForceChannel : public ITransport {
public:
    ForceChannel(ITransport& in, Channel c) : in_(in), c_(c) {}
    void send(PeerId to, Bytes d) override { in_.sendEx(to, std::move(d), c_); }
    void sendEx(PeerId to, Bytes d, Channel) override { in_.sendEx(to, std::move(d), c_); }
    bool poll(PeerId& f, Bytes& d) override { return in_.poll(f, d); }
private:
    ITransport& in_;
    Channel c_;
};

struct NullSink : IEffectSink {
    void apply(const Effect&, const Context&) override {}
};

uint32_t digestOf(const ShadowState& s) {
    Archive a = Archive::writer();
    ShadowState c = s;
    io(a, c);
    Bytes b = a.finish();
    return crc32(b.data(), b.size());
}

LinkParams linkFrom(const std::map<std::string, std::string>& a) {
    LinkParams p;
    double loss = a.count("loss") ? std::atof(a.at("loss").c_str()) / 100.0 : 0;
    p.loss = loss;
    if (loss > 0) { p.duplicate = 0.1; p.corrupt = 0.02; p.minLatencyMs = 0; p.maxLatencyMs = 40; }
    return p;
}

int runHost(std::map<std::string, std::string>& a) {
    uint16_t port = uint16_t(std::atoi(a["port"].c_str()));
    uint64_t seed = std::strtoull(a["seed"].c_str(), nullptr, 10);
    int N = std::atoi(a["txns"].c_str()), C = std::atoi(a["clients"].c_str());
    EnetTransport enet;
    if (!enet.listen(port, 8, true)) { std::printf("host: listen failed\n"); return 2; }
    FaultyTransport faulty(enet, linkFrom(a), seed * 31 + 1);
    ForceChannel forced(faulty, ITransport::Channel::Unreliable);
    ITransport& wire = a.count("unreliable") ? static_cast<ITransport&>(forced) : static_cast<ITransport&>(faulty);
    MuxTransport mux(wire);
    uint64_t dialogue = 0;
    mux.registerChannel(kChannelDialogue, [&](PeerId, Bytes&&) { ++dialogue; });
    SharePolicy pol; pol.maxPlayers = uint8_t(C + 1);
    HostSession host(mux, pol);
    Rng rng(seed);
    auto t0 = Clock::now();
    int committed = 0;
    uint64_t lastCommit = 0;
    bool done = false;
    while (nowMs(t0) < 90000) {
        uint64_t now = nowMs(t0);
        faulty.pump(now);
        host.tick(now);
        size_t joined = 0;
        for (auto& c : host.clients()) joined += c.joined;
        if (joined >= size_t(C) && committed < N && now >= lastCommit + 8) {
            lastCommit = now;
            std::vector<Effect> eff;
            for (uint32_t i = 0, n = rng.range(1, 5); i < n; ++i) eff.push_back(sim::randomEffect(rng, C + 1));
            if (host.commit(PlayerId(rng.range(0, C)), sim::randomCause(rng), std::move(eff))) ++committed;
            if (committed % 50 == 0) host.mutableLog().compact(100000);  // force some snapshot paths too
        }
        if (committed >= N && host.allSynced() && joined >= size_t(C)) { if (!done) { done = true; lastCommit = now; } else if (now > lastCommit + 700) break; }
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    bool ok = done && host.allSynced();
    std::ofstream(a["out"]) << (ok ? "OK" : "TIMEOUT") << " DIGEST " << digestOf(host.state()) << " HEAD " << host.log().head()
                            << " DIALOGUE " << dialogue << " BAD " << host.stats().badMessages << " HSNAP " << host.stats().snapshots << " HREPL " << host.stats().replays << " RETX " << host.stats().retransmitBatches << "\n";
    return ok ? 0 : 3;
}

int runClient(std::map<std::string, std::string>& a) {
    if (a.count("mutate")) {  // TEST ONLY: mutation twin
        if (a["mutate"] == "idempotency") mutations().noIdempotency = true;
        if (a["mutate"] == "order") mutations().applyOutOfOrder = true;
    }
    uint16_t port = uint16_t(std::atoi(a["port"].c_str()));
    uint64_t seed = std::strtoull(a["seed"].c_str(), nullptr, 10);
    uint64_t token = std::strtoull(a["token"].c_str(), nullptr, 10);
    int N = std::atoi(a["txns"].c_str());
    EnetTransport enet;
    if (!enet.connect("127.0.0.1", port)) { std::printf("client: connect failed\n"); return 2; }
    FaultyTransport faulty(enet, linkFrom(a), seed * 77 + token);
    ForceChannel forced(faulty, ITransport::Channel::Unreliable);
    ITransport& wire = a.count("unreliable") ? static_cast<ITransport&>(forced) : static_cast<ITransport&>(faulty);
    NullSink sink;
    EffectCapture cap;
    ClientSession cs(wire, 0, token, sink, &cap);
    auto t0 = Clock::now();
    bool started = false;
    uint64_t reached = 0, lastDialogue = 0;
    while (nowMs(t0) < 90000) {
        uint64_t now = nowMs(t0);
        EnetTransport::Event ev;
        while (enet.nextEvent(ev)) if (ev.type == EnetTransport::Event::Connected && !started) { started = true; cs.connect(now, 1); }
        faulty.pump(now);
        cs.tick(now);
        if (cs.joined() && now >= lastDialogue + 50) { lastDialogue = now; wire.send(0, Bytes{kChannelDialogue, 1, 2, 3}); }
        if (cs.applied() >= uint64_t(N) && !reached) reached = now;
        if (reached && now > reached + 1200) break;  // linger so the last acks reach the host
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    bool ok = reached != 0;
    std::ofstream(a["out"]) << (ok ? "OK" : "TIMEOUT") << " DIGEST " << digestOf(cs.state()) << " HEAD " << cs.applied() << " DUPS " << cs.stats().duplicatesDropped << " APPLIED " << cs.stats().txnsApplied << " SNAP " << cs.stats().snapshots << " REPLAY " << cs.stats().replays << "\n";
    return ok ? 0 : 3;
}

#ifdef _WIN32
using Proc = HANDLE;
constexpr const char* kSep = "\\";
Proc spawn(const std::string& cmdline) {
    STARTUPINFOA si{}; si.cb = sizeof si;
    PROCESS_INFORMATION pi{};
    std::string c = cmdline;
    if (!CreateProcessA(nullptr, c.data(), nullptr, nullptr, FALSE, 0, nullptr, nullptr, &si, &pi)) return nullptr;
    CloseHandle(pi.hThread);
    return pi.hProcess;
}
unsigned long selfPid() { return GetCurrentProcessId(); }
std::string selfExe() { char exe[MAX_PATH]; GetModuleFileNameA(nullptr, exe, MAX_PATH); return exe; }
// Waits up to 120 s; returns the exit code (99 if it could not be read).
int waitProc(Proc h) {
    WaitForSingleObject(h, 120000);
    DWORD code = 99;
    GetExitCodeProcess(h, &code);
    CloseHandle(h);
    return int(code);
}
void removeFile(const std::string& f) { DeleteFileA(f.c_str()); }
#else
using Proc = pid_t;  // 0 = spawn failed
constexpr const char* kSep = "/";
Proc spawn(const std::string& cmdline) {
    const char* argv[] = {"/bin/sh", "-c", cmdline.c_str(), nullptr};
    pid_t pid = 0;
    if (posix_spawn(&pid, "/bin/sh", nullptr, nullptr, const_cast<char* const*>(argv), environ) != 0) return 0;
    return pid;
}
unsigned long selfPid() { return static_cast<unsigned long>(getpid()); }
std::string selfExe() {
    char exe[4096];
    ssize_t n = readlink("/proc/self/exe", exe, sizeof exe - 1);
    return n > 0 ? std::string(exe, size_t(n)) : std::string();
}
int waitProc(Proc p) {
    for (int waited = 0; waited < 120000; waited += 10) {
        int st = 0;
        pid_t r = waitpid(p, &st, WNOHANG);
        if (r == p) return WIFEXITED(st) ? WEXITSTATUS(st) : 99;
        if (r < 0) return 99;
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }
    kill(p, SIGKILL);
    waitpid(p, nullptr, 0);
    return 99;
}
void removeFile(const std::string& f) { std::remove(f.c_str()); }
#endif

std::string slurp(const std::string& f) { std::ifstream i(f); std::stringstream s; s << i.rdbuf(); return s.str(); }

int selftest(int argc, char** argv) {
    auto a = parseArgs(argc, argv, 2);
    std::string loss = a.count("loss") ? a["loss"] : "0";
    bool unrel = a.count("unreliable") > 0;
    std::string mutate = a.count("mutate") ? a["mutate"] : "";
    bool expectFail = !mutate.empty();
    const int C = 2, N = 150;
    std::string exe = selfExe();
    if (exe.empty()) { std::printf("cannot find own executable\n"); return 1; }
    int port = 27500 + int(selfPid() % 2000);
    const char* tmpDir = std::getenv("TEMP") ? std::getenv("TEMP") : std::getenv("TMPDIR");
    std::string tmp = std::string(tmpDir ? tmpDir : ".") + kSep + "fnvcoop_loop_" + std::to_string(selfPid());
    std::string common = std::string(" --port ") + std::to_string(port) + " --seed 7 --txns " + std::to_string(N) + " --loss " + loss + (unrel ? " --unreliable" : "");
    std::string clientExtra = mutate.empty() ? "" : " --mutate " + mutate;
    std::string q = "\"" + exe + "\" ";
    Proc h = spawn(q + "host" + common + " --clients " + std::to_string(C) + " --out " + tmp + "_h.txt");
    if (!h) { std::printf("spawn host failed\n"); return 1; }
    std::this_thread::sleep_for(std::chrono::milliseconds(300));
    std::vector<Proc> hs{h};
    for (int i = 0; i < C; ++i)
        hs.push_back(spawn(q + "client" + common + clientExtra + " --token " + std::to_string(1000 + i) + " --out " + tmp + "_c" + std::to_string(i) + ".txt"));
    for (auto x : hs) if (!x) { std::printf("spawn client failed\n"); return 1; }
    int bad = 0;
    for (auto x : hs) { int code = waitProc(x); if (code != 0) { ++bad; std::printf("child exit %d\n", code); } }
    std::string host = slurp(tmp + "_h.txt");
    std::printf("host:    %s", host.c_str());
    std::string hd = host.substr(host.find("DIGEST"), host.find("DIALOGUE") - host.find("DIGEST"));
    for (int i = 0; i < C; ++i) {
        std::string c = slurp(tmp + "_c" + std::to_string(i) + ".txt");
        std::printf("client%d: %s", i, c.c_str());
        if (c.find("OK") != 0) ++bad;
        if (c.find(hd.substr(0, hd.find("HEAD"))) == std::string::npos) { std::printf("  digest mismatch\n"); ++bad; }
    }
    if (host.find("OK") != 0) ++bad;
    if (host.find("DIALOGUE 0") != std::string::npos) { std::printf("no dialogue datagrams demuxed\n"); ++bad; }
    removeFile(tmp + "_h.txt");
    for (int i = 0; i < C; ++i) removeFile(tmp + "_c" + std::to_string(i) + ".txt");
    if (expectFail) {  // mutation twin: the injected bug MUST be detected
        std::printf(bad ? "TWIN OK: bug '%s' detected over real UDP\n" : "TWIN FAIL: bug '%s' went unnoticed\n", mutate.c_str());
        return bad ? 0 : 1;
    }
    std::printf(bad ? "LOOPBACK FAIL\n" : "LOOPBACK PASS (loss %s%%%s)\n", loss.c_str(), unrel ? ", unreliable channel" : "");
    return bad ? 1 : 0;
}
}  // namespace

int main(int argc, char** argv) {
    if (argc < 2) { std::printf("usage: fnvcoop_loopback selftest|host|client ...\n"); return 1; }
    std::string mode = argv[1];
    if (mode == "selftest") return selftest(argc, argv);
    auto a = parseArgs(argc, argv, 2);
    if (mode == "host") return runHost(a);
    if (mode == "client") return runClient(a);
    return 1;
}
