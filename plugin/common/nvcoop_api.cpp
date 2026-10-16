#include <chrono>
#include <cstdarg>
#include <cstdio>
#include <deque>
#include <map>
#include <mutex>

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>

#include "registry.h"

namespace nvcoop {
namespace {

using Clock = std::chrono::steady_clock;

struct State {
    std::mutex mu;
    std::deque<registry::EventRec> ring;
    uint64_t nextSeq = 1;
    StateProvider provider = nullptr;
    std::map<std::string, registry::MethodEntry> methods;
    std::vector<Module> modules;
    std::string logPath = "nvcoop.log";
    Clock::time_point t0 = Clock::now();
};

State& S() {
    static State s;  // function-static: safe for use from other TUs' static initialisers
    return s;
}

}  // namespace

void PushEvent(const char* type, const std::string& dataJson) {
    State& s = S();
    std::lock_guard<std::mutex> g(s.mu);
    registry::EventRec r;
    r.seq = s.nextSeq++;
    r.tMs = static_cast<uint64_t>(std::chrono::duration_cast<std::chrono::milliseconds>(Clock::now() - s.t0).count());
    r.type = type ? type : "";
    r.dataJson = dataJson.empty() ? "null" : dataJson;
    s.ring.push_back(std::move(r));
    while (s.ring.size() > registry::kEventCapacity) s.ring.pop_front();
}

void SetStateProvider(StateProvider fn) {
    std::lock_guard<std::mutex> g(S().mu);
    S().provider = fn;
}

void RegisterMethod(const char* name, MethodFn fn, bool mainThread) {
    if (!name || !fn) return;
    std::lock_guard<std::mutex> g(S().mu);
    S().methods[name] = registry::MethodEntry{fn, mainThread};
}

void RegisterModule(const Module& m) {
    std::lock_guard<std::mutex> g(S().mu);
    S().modules.push_back(m);
}

void Log(const char* fmt, ...) {
    char buf[1024];
    va_list ap;
    va_start(ap, fmt);
    std::vsnprintf(buf, sizeof buf, fmt, ap);
    va_end(ap);
    std::string path;
    {
        std::lock_guard<std::mutex> g(S().mu);
        path = S().logPath;
    }
    if (FILE* f = std::fopen(path.c_str(), "a")) {
        std::fprintf(f, "[nvcoop] %s\n", buf);
        std::fclose(f);
    }
    OutputDebugStringA("[nvcoop] ");
    OutputDebugStringA(buf);
    OutputDebugStringA("\n");
}

namespace registry {

EventPage ReadEvents(uint64_t since, size_t limit) {
    State& s = S();
    std::lock_guard<std::mutex> g(s.mu);
    EventPage p;
    p.next = since;
    if (!s.ring.empty() && s.ring.front().seq > since + 1) p.dropped = s.ring.front().seq - since - 1;
    for (const auto& e : s.ring) {
        if (e.seq <= since) continue;
        if (limit && p.events.size() >= limit) break;
        p.events.push_back(e);
        p.next = e.seq;
    }
    return p;
}

uint64_t LastEventSeq() {
    std::lock_guard<std::mutex> g(S().mu);
    return S().nextSeq - 1;
}

void ClearEvents() {
    std::lock_guard<std::mutex> g(S().mu);
    S().ring.clear();
}

StateProvider GetStateProvider() {
    std::lock_guard<std::mutex> g(S().mu);
    return S().provider;
}

bool FindMethod(const std::string& name, MethodEntry& out) {
    std::lock_guard<std::mutex> g(S().mu);
    auto it = S().methods.find(name);
    if (it == S().methods.end()) return false;
    out = it->second;
    return true;
}

const std::vector<Module>& Modules() { return S().modules; }

void SetLogFile(const std::string& path) {
    std::lock_guard<std::mutex> g(S().mu);
    S().logPath = path;
}

}  // namespace registry
}  // namespace nvcoop
