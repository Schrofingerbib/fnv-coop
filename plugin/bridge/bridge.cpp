#include "bridge.h"

#include <atomic>
#include <cctype>
#include <chrono>
#include <condition_variable>
#include <cstdio>
#include <deque>
#include <functional>
#include <list>
#include <mutex>
#include <thread>

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>

#include "minijson.h"
#include "registry.h"
#include "build_info.h"

namespace nvcoop::bridge {
namespace {

using json::Value;
using Clock = std::chrono::steady_clock;

constexpr int kParseError = -32700;
constexpr int kInvalidRequest = -32600;
constexpr int kMethodNotFound = -32601;
constexpr int kInvalidParams = -32602;
constexpr int kHostError = -32000;
constexpr int kMainThreadTimeout = -32001;
constexpr int kLeaseHeld = -32010;     // lock.acquire refused: someone else holds the lease
constexpr int kSaveExists = -32012;    // saveGame refused: name exists and overwrite was not requested
constexpr int kLeaseRequired = -32011; // request needs the lease token of the current holder
constexpr int kRefNotLoaded = -32013;  // exec {ref}: the ref names no loaded reference (plugin, form or form type)
constexpr size_t kMaxLine = 4u << 20;

uint64_t NowMs() {
    return static_cast<uint64_t>(std::chrono::duration_cast<std::chrono::milliseconds>(Clock::now().time_since_epoch()).count());
}

// One request that needs the main thread.
struct Job {
    std::string method;
    Value params;
    std::string customParamsJson;

    uint64_t deadlineMs = 0;  // absolute; enforced by Pump for pending jobs

    std::mutex mu;
    std::condition_variable cv;
    bool done = false;
    bool abandoned = false;  // pipe thread stopped waiting
    bool ok = true;
    int errCode = 0;
    std::string errMsg;
    Value result;

    // pending-state for async methods
    std::function<bool(Host&, Job&)> step;  // returns true when finished
    bool started = false;

    void finishOk(Value r) {
        std::lock_guard<std::mutex> g(mu);
        ok = true; result = std::move(r); done = true;
        cv.notify_all();
    }
    void finishErr(int code, std::string msg) {
        std::lock_guard<std::mutex> g(mu);
        ok = false; errCode = code; errMsg = std::move(msg); done = true;
        cv.notify_all();
    }
};

Value ErrorObj(int code, const std::string& msg) {
    Value e = Value::object();
    e.set("code", code).set("message", msg);
    return e;
}

}  // namespace

struct Bridge::Impl {
    Config cfg;
    std::atomic<bool> stopping{false};
    std::thread acceptor;

    // main-thread inbox
    std::mutex qmu;
    std::deque<std::shared_ptr<Job>> inbox;

    // main-thread only
    std::list<std::shared_ptr<Job>> pending;
    std::atomic<uint64_t> frames{0};
    std::atomic<uint64_t> lastPumpMs{0};
    std::atomic<uint64_t> loadedCount{0};
    std::atomic<uint64_t> savedCount{0};
    std::mutex smu;
    std::string lastSavedPath;
    std::atomic<bool> menuReady{false};
    uint64_t menuReadyFrame = 0;
    bool autoloadStarted = false;
    uint64_t autoloadLoadedAt = 0;
    bool autoloadWaiting = false;
    uint64_t quitAtMs = 0;

    // ---- advisory+enforced lease so several lanes can share one game instance ----
    struct Lease { std::string owner, token; uint64_t expiresMs = 0; };
    std::mutex lmu;
    Lease lease;
    uint64_t leaseSeq = 0;
    bool LeaseActive() const { return !lease.token.empty() && NowMs() < lease.expiresMs; }  // call with lmu held
    Value LockCall(const std::string& method, const Value& params, int& errCode, std::string& errMsg);

    // ---- pipe side (any thread) ----
    void AcceptLoop();
    void Serve(HANDLE pipe);
    std::string HandleLine(const std::string& line);
    Value HandlePipeThread(const std::string& method, const Value& params, bool& handled, int& errCode, std::string& errMsg);
    Value Ping() const;
    Value Events(const Value& params) const;

    // ---- main side ----
    void Dispatch(Host& host, const std::shared_ptr<Job>& job);
    bool StepWaitFor(Host& host, Job& job);
};

namespace {

bool ParseNumberAfter(const std::string& s, const char* key, double& out) {
    size_t p;
    if (!*key) {  // no key: first number anywhere in the text
        p = s.find_first_of("0123456789-.");
        if (p == std::string::npos) return false;
    } else {
        p = s.rfind(key);
        if (p == std::string::npos) return false;
        p += std::char_traits<char>::length(key);
    }
    const char* b = s.c_str() + p;
    char* e = nullptr;
    double d = std::strtod(b, &e);
    if (e == b) return false;
    out = d;
    return true;
}

bool Compare(const std::string& op, double v, double rhs) {
    if (op == "==") return v == rhs;
    if (op == "!=") return v != rhs;
    if (op == "<") return v < rhs;
    if (op == "<=") return v <= rhs;
    if (op == ">") return v > rhs;
    if (op == ">=") return v >= rhs;
    return v != 0;  // "truthy"
}

bool ValidOp(const std::string& op) {
    return op == "truthy" || op == "==" || op == "!=" || op == "<" || op == "<=" || op == ">" || op == ">=";
}

}  // namespace

std::string Bridge::PipeName(int instance) { return "\\\\.\\pipe\\nvcoop-" + std::to_string(instance); }

// ---------------------------------------------------------------------------------------------
// exec {cmd, ref}: the calling reference of a console line
// ---------------------------------------------------------------------------------------------

bool ParseRefSpec(const std::string& text, RefSpec* out, std::string* err) {
    auto fail = [&](const std::string& why) {
        if (err) *err = why;
        return false;
    };
    std::string plugin, hex = text;
    const size_t colon = text.rfind(':');
    if (colon != std::string::npos) {
        plugin = text.substr(0, colon);
        hex = text.substr(colon + 1);
        if (plugin.empty()) return fail("the plugin name before ':' is empty");
    }
    if (hex.size() > 2 && hex[0] == '0' && (hex[1] == 'x' || hex[1] == 'X')) hex.erase(0, 2);
    if (hex.empty() || hex.size() > 8) return fail("the form id must have 1 to 8 hex digits");
    uint32_t v = 0;
    for (char c : hex) {
        int d = (c >= '0' && c <= '9') ? c - '0' : (c >= 'a' && c <= 'f') ? c - 'a' + 10 : (c >= 'A' && c <= 'F') ? c - 'A' + 10 : -1;
        if (d < 0) return fail("'" + hex + "' is not a hex number");
        v = (v << 4) | static_cast<uint32_t>(d);
    }
    if (!plugin.empty() && v > 0xFFFFFFu)
        return fail("the id inside a plugin is at most FFFFFF (give a runtime id as bare HEX, without the plugin)");
    out->plugin = plugin;
    out->id = v;
    return true;
}

bool IsReferenceFormType(int typeId) { return typeId == 0x3A || typeId == 0x3B || typeId == 0x3C; }

// ---------------------------------------------------------------------------------------------
// pipe-thread side
// ---------------------------------------------------------------------------------------------

Value Bridge::Impl::LockCall(const std::string& method, const Value& p, int& errCode, std::string& errMsg) {
    std::lock_guard<std::mutex> g(lmu);
    const uint64_t now = NowMs();
    auto info = [&]() {
        Value r = Value::object();
        bool held = LeaseActive();
        r.set("held", held);
        if (held) r.set("owner", lease.owner).set("remainingMs", static_cast<uint64_t>(lease.expiresMs - now));
        return r;
    };
    if (method == "lock.info") return info();
    const std::string owner = p.get("owner").asString("");
    const std::string token = p.get("token").asString("");
    const bool force = p.get("force").asBool(false);
    if (method == "lock.acquire") {
        if (owner.empty()) { errCode = kInvalidParams; errMsg = "lock.acquire requires string param 'owner'"; return Value(); }
        int64_t ttl = p.get("ttlMs").asInt(60000);
        if (ttl < 100) ttl = 100;
        if (ttl > 3600000) ttl = 3600000;
        if (LeaseActive() && lease.owner != owner && !force) {
            errCode = kLeaseHeld;
            errMsg = "lease held by '" + lease.owner + "' for another " + std::to_string(lease.expiresMs - now) + " ms";
            return Value();
        }
        if (!(LeaseActive() && lease.owner == owner)) {
            lease.owner = owner;
            lease.token = "L" + std::to_string(now) + "-" + std::to_string(++leaseSeq);
        }
        lease.expiresMs = now + static_cast<uint64_t>(ttl);
        nvcoop::PushEvent("lock.acquired", "{\"owner\":" + Value(owner).dump() + "}");
        Value r = info();
        r.set("token", lease.token).set("ttlMs", ttl);
        return r;
    }
    if (method == "lock.renew" || method == "lock.release") {
        if (!LeaseActive() || (lease.token != token && !force)) {
            errCode = kLeaseRequired;
            errMsg = "no matching lease (token mismatch or expired)";
            return Value();
        }
        if (method == "lock.release") {
            nvcoop::PushEvent("lock.released", "{\"owner\":" + Value(lease.owner).dump() + "}");
            lease = Lease();
            return info();
        }
        int64_t ttl = p.get("ttlMs").asInt(60000);
        if (ttl < 100) ttl = 100;
        if (ttl > 3600000) ttl = 3600000;
        lease.expiresMs = now + static_cast<uint64_t>(ttl);
        return info();
    }
    errCode = kMethodNotFound;
    errMsg = "unknown method: " + method;
    return Value();
}

Value Bridge::Impl::Ping() const {
    Value r = Value::object();
    r.set("pong", true)
        .set("protocol", nvcoop::kBridgeProtocolVersion)
        .set("instance", cfg.instance)
        .set("frames", static_cast<uint64_t>(frames.load()))
        .set("pid", static_cast<uint64_t>(GetCurrentProcessId()));
    uint64_t last = lastPumpMs.load();
    // mainLoopAgeMs < 0: main thread has never pumped (still booting / loading).
    r.set("mainLoopAgeMs", last ? static_cast<int64_t>(NowMs() - last) : static_cast<int64_t>(-1));
    r.set("menuReady", menuReady.load());
    r.set("loadedCount", static_cast<uint64_t>(loadedCount.load()));
    return r;
}

Value Bridge::Impl::Events(const Value& params) const {
    uint64_t since = static_cast<uint64_t>(params.get("since").asInt(0));
    size_t limit = static_cast<size_t>(params.get("limit").asInt(0));
    registry::EventPage page = registry::ReadEvents(since, limit);
    Value arr = Value::array();
    for (auto& e : page.events) {
        Value v = Value::object();
        Value data;
        std::string err;
        if (!json::parse(e.dataJson, data, err)) data = Value(e.dataJson);
        v.set("seq", e.seq).set("tMs", e.tMs).set("type", e.type).set("data", std::move(data));
        arr.push(std::move(v));
    }
    Value r = Value::object();
    r.set("events", std::move(arr)).set("next", page.next).set("dropped", page.dropped);
    r.set("last", registry::LastEventSeq());
    return r;
}

std::string Bridge::Impl::HandleLine(const std::string& line) {
    Value req, idv;
    std::string perr;
    auto reply = [&](const Value& id, bool ok, Value payload) {
        Value resp = Value::object();
        Value outId = id;
#ifdef NVBRIDGE_MUTANT
        // mutation twin: answers carry the wrong id
        if (outId.isNumber()) outId = Value(outId.asInt() + 1);
#endif
        resp.set("id", std::move(outId));
        resp.set(ok ? "result" : "error", std::move(payload));
        return resp.dump();
    };
    if (!json::parse(line, req, perr)) return reply(Value(), false, ErrorObj(kParseError, "parse error: " + perr));
    if (!req.isObject()) return reply(Value(), false, ErrorObj(kInvalidRequest, "request must be an object"));
    idv = req.get("id");
    if (!req.get("method").isString()) return reply(idv, false, ErrorObj(kInvalidRequest, "missing method"));
    const std::string method = req.get("method").asString();
    const Value params = req.get("params");
    if (!params.isNull() && !params.isObject()) return reply(idv, false, ErrorObj(kInvalidParams, "params must be an object"));

    // Methods served directly from the pipe thread (work even while the main thread is busy/loading).
    if (method == "ping") return reply(idv, true, Ping());
    if (method == "bridge.version") {
        Value r = Value::object();
        r.set("gitHash", NVCOOP_GIT_HASH).set("dirty", NVCOOP_GIT_DIRTY != 0)
            .set("buildTime", std::string(__DATE__ " " __TIME__))   // when bridge.cpp was last compiled
            .set("protocol", nvcoop::kBridgeProtocolVersion)
#ifdef NVBRIDGE_MUTANT
            .set("mutant", true)
#endif
            .set("pid", static_cast<uint64_t>(GetCurrentProcessId()));
        return reply(idv, true, std::move(r));
    }
    if (method.compare(0, 5, "lock.") == 0) {
        int ec = 0;
        std::string em;
        Value r = LockCall(method, params, ec, em);
        if (ec) return reply(idv, false, ErrorObj(ec, em));
        return reply(idv, true, std::move(r));
    }
    if (method != "events") {  // everything else is gated by the lease, if one is active
        std::lock_guard<std::mutex> g(lmu);
        bool active = LeaseActive();
#ifdef NVBRIDGE_MUTANT_LOCK
        active = false;  // mutation twin: lease never enforced
#endif
        if (active && params.get("lease").asString("") != lease.token)
            return reply(idv, false, ErrorObj(kLeaseRequired, "locked by '" + lease.owner + "': pass the lease token as param 'lease'"));
    }
    if (method == "events") {
        Value p = Events(params);
#ifdef NVBRIDGE_MUTANT
        // mutation twin: ignores `since` bookkeeping (always reports next=0)
        p.set("next", 0);
#endif
        return reply(idv, true, std::move(p));
    }

    auto job = std::make_shared<Job>();
    job->method = method;
    job->params = params;

    static const char* kBuiltin[] = {"exec", "state", "waitFor", "screenshot", "loadSave", "saveGame", "quit"};
    bool builtin = false;
    for (auto* b : kBuiltin) if (method == b) builtin = true;

    bool customMain = false;
    if (!builtin) {
        registry::MethodEntry me;
        if (!registry::FindMethod(method, me)) return reply(idv, false, ErrorObj(kMethodNotFound, "unknown method: " + method));
        if (!me.mainThread) {
            std::string out;
            bool ok = me.fn(params.dump(), out);
            if (!ok) return reply(idv, false, ErrorObj(kHostError, out));
            Value r;
            std::string e2;
            if (!json::parse(out, r, e2)) return reply(idv, false, ErrorObj(kHostError, "custom method returned invalid JSON: " + e2));
            return reply(idv, true, std::move(r));
        }
        customMain = true;
        job->customParamsJson = params.dump();
    }
    (void)customMain;

    int timeoutMs = static_cast<int>(params.get("timeoutMs").asInt(cfg.defaultTimeoutMs));
    if (timeoutMs < 0) timeoutMs = 0;
    job->deadlineMs = NowMs() + static_cast<uint64_t>(timeoutMs);
    {
        std::lock_guard<std::mutex> g(qmu);
        inbox.push_back(job);
    }
    // Hard wait limit: the job's own deadline plus slack for Pump to notice it. If the main thread is
    // not pumping at all, we give up after this and tell the client.
    const uint64_t hardMs = static_cast<uint64_t>(timeoutMs) + 2000;
    std::unique_lock<std::mutex> lk(job->mu);
    bool finished = job->cv.wait_for(lk, std::chrono::milliseconds(hardMs), [&] { return job->done; });
    if (!finished) {
        job->abandoned = true;
        return reply(idv, false, ErrorObj(kMainThreadTimeout, "main thread did not service the request (is the game loading?)"));
    }
    if (!job->ok) return reply(idv, false, ErrorObj(job->errCode, job->errMsg));
    return reply(idv, true, job->result);
}

void Bridge::Impl::Serve(HANDLE pipe) {
    std::string buf;
    char chunk[4096];
    for (;;) {
        DWORD n = 0;
        if (!ReadFile(pipe, chunk, sizeof chunk, &n, nullptr) || n == 0) break;
        buf.append(chunk, n);
        size_t nl;
        while ((nl = buf.find('\n')) != std::string::npos) {
            std::string line = buf.substr(0, nl);
            buf.erase(0, nl + 1);
            if (!line.empty() && line.back() == '\r') line.pop_back();
            if (line.empty()) continue;
            std::string out = HandleLine(line);
            out.push_back('\n');
            DWORD w = 0;
            if (!WriteFile(pipe, out.data(), static_cast<DWORD>(out.size()), &w, nullptr)) goto done;
        }
        if (buf.size() > kMaxLine) break;
    }
done:
    FlushFileBuffers(pipe);
    DisconnectNamedPipe(pipe);
    CloseHandle(pipe);
}

void Bridge::Impl::AcceptLoop() {
    const std::string name = PipeName(cfg.instance);
    bool first = true;
    while (!stopping.load()) {
        HANDLE pipe = CreateNamedPipeA(name.c_str(), PIPE_ACCESS_DUPLEX | (first ? FILE_FLAG_FIRST_PIPE_INSTANCE : 0),
                                       PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT, PIPE_UNLIMITED_INSTANCES,
                                       65536, 65536, 0, nullptr);
        if (pipe == INVALID_HANDLE_VALUE) {
            Log("nvbridge: CreateNamedPipe(%s) failed: %lu", name.c_str(), GetLastError());
            if (first) return;  // another process owns the name; do not retry-spin
            std::this_thread::sleep_for(std::chrono::milliseconds(200));
            continue;
        }
        first = false;
        BOOL c = ConnectNamedPipe(pipe, nullptr) ? TRUE : (GetLastError() == ERROR_PIPE_CONNECTED);
        if (!c || stopping.load()) { CloseHandle(pipe); continue; }
        std::thread(&Impl::Serve, this, pipe).detach();
    }
}

// ---------------------------------------------------------------------------------------------
// main-thread side
// ---------------------------------------------------------------------------------------------

bool Bridge::Impl::StepWaitFor(Host& host, Job& job) {
    // State kept in job.result while pending: {nextPoll, last}
    uint64_t now = NowMs();
    uint64_t nextPoll = static_cast<uint64_t>(job.result.get("nextPoll").asInt(0));
    uint64_t started = static_cast<uint64_t>(job.result.get("started").asInt(now));
    if (!job.started) {
        job.started = true;
        job.result = Value::object();
        job.result.set("started", static_cast<uint64_t>(now));
        started = now;
        nextPoll = 0;
    }
    const Value& p = job.params;
    const std::string op = p.get("op").asString("truthy");
    const double rhs = p.get("value").asNumber(0);
    const std::string contains = p.get("contains").asString("");
    const bool hasContains = p.find("contains") != nullptr;
    const int pollMs = static_cast<int>(p.get("pollMs").asInt(100));

    if (now >= nextPoll) {
        std::string cmd;
        const bool isExpr = p.find("expr") != nullptr;
        if (isExpr) cmd = "printc \"NVCOOP_EVAL=%g\" (" + p.get("expr").asString() + ")";
        else cmd = p.get("cmd").asString();
        ExecResult er = host.Exec(cmd);

        bool satisfied = false;
        Value last;
        if (hasContains) {
            satisfied = er.output.find(contains) != std::string::npos;
            last = Value(er.output);
        } else {
            double v = 0;
            bool parsed = isExpr ? ParseNumberAfter(er.output, "NVCOOP_EVAL=", v) : ParseNumberAfter(er.output, "", v);
            if (parsed) { satisfied = Compare(op, v, rhs); last = Value(v); }
            else last = Value(er.output);
        }
        if (satisfied) {
            Value r = Value::object();
            r.set("satisfied", true).set("timedOut", false).set("value", last)
                .set("elapsedMs", static_cast<uint64_t>(now - started));
            job.finishOk(std::move(r));
            return true;
        }
        job.result.set("last", last);
        job.result.set("nextPoll", static_cast<uint64_t>(now + (pollMs < 1 ? 1 : pollMs)));
    }
    if (now >= job.deadlineMs) {
        Value r = Value::object();
        r.set("satisfied", false).set("timedOut", true).set("value", job.result.get("last"))
            .set("elapsedMs", static_cast<uint64_t>(now - started));
        job.finishOk(std::move(r));
        return true;
    }
    return false;
}

void Bridge::Impl::Dispatch(Host& host, const std::shared_ptr<Job>& job) {
    const std::string& m = job->method;
    const Value& p = job->params;
    auto needStr = [&](const char* k) -> const std::string* {
        const Value* v = p.find(k);
        return (v && v->isString()) ? &v->asString() : nullptr;
    };

    if (m == "exec") {
        const std::string* cmd = needStr("cmd");
        if (!cmd) { job->finishErr(kInvalidParams, "exec requires string param 'cmd'"); return; }
        if (const Value* refv = p.find("ref")) {
            // exec {cmd, ref}: run the line with that reference as the console's calling reference (see bridge.h).
            if (!refv->isString()) {
                job->finishErr(kInvalidParams, "exec: param 'ref' must be a string (\"Plugin.esm:HEX\", \"HEX\" or \"0xHEX\")");
                return;
            }
            const std::string& text = refv->asString();
            RefSpec spec;
            std::string why;
            if (!ParseRefSpec(text, &spec, &why)) { job->finishErr(kInvalidParams, "exec: bad ref '" + text + "': " + why); return; }
            uint32_t id = spec.id;
            if (!spec.plugin.empty()) {
                const int idx = host.PluginIndex(spec.plugin);
                if (idx < 0 || idx > 0xFE) {
                    job->finishErr(kRefNotLoaded, "exec: ref '" + text + "': plugin '" + spec.plugin + "' is not loaded");
                    return;
                }
                id = (static_cast<uint32_t>(idx) << 24) | spec.id;
            }
            char hex[16];
            std::snprintf(hex, sizeof hex, "%08X", id);
            const int type = host.FormType(id);
            if (type < 0) {
                job->finishErr(kRefNotLoaded, "exec: ref '" + text + "' (form " + hex + ") is not a loaded form");
                return;
            }
            if (!IsReferenceFormType(type)) {
                char t[8];
                std::snprintf(t, sizeof t, "0x%02X", type);
                job->finishErr(kRefNotLoaded, "exec: ref '" + text + "' (form " + hex + ", form type " + t +
                                                  ") is not a reference: only a REFR, ACHR or ACRE can be the calling reference");
                return;
            }
#ifdef NVBRIDGE_MUTANT_EXECREF
            ExecResult er = host.Exec(*cmd);  // mutation twin: the ref is checked, then ignored (no calling reference)
#else
            ExecResult er = host.ExecOn(*cmd, id);
#endif
            Value r = Value::object();
            r.set("ok", er.ok).set("output", er.output).set("ref", std::string(hex));
            job->finishOk(std::move(r));
            return;
        }
        ExecResult er = host.Exec(*cmd);
        Value r = Value::object();
        r.set("ok", er.ok).set("output", er.output);
        job->finishOk(std::move(r));
    } else if (m == "state") {
        StateProvider fn = registry::GetStateProvider();
        if (!fn) {
            Value r = Value::object();
            r.set("todo", "L7");
            job->finishOk(std::move(r));
            return;
        }
        std::string sections;
        const Value& s = p.get("sections");
        if (s.isString()) sections = s.asString();
        else if (s.type() == Value::Array)
            for (auto& it : s.items()) { if (!sections.empty()) sections += ','; sections += it.asString(); }
        std::string txt = fn(sections);
        if (const std::string* path = needStr("path")) {  // big dumps: write to disk, return the path
            FILE* f = std::fopen(path->c_str(), "wb");
            if (!f) { job->finishErr(kHostError, "cannot open " + *path); return; }
            std::fwrite(txt.data(), 1, txt.size(), f);
            std::fclose(f);
            Value r = Value::object();
            r.set("path", *path).set("bytes", static_cast<uint64_t>(txt.size()));
            job->finishOk(std::move(r));
            return;
        }
        Value r;
        std::string perr;
        if (!json::parse(txt, r, perr)) { job->finishErr(kHostError, "state provider returned invalid JSON: " + perr); return; }
        job->finishOk(std::move(r));
    } else if (m == "waitFor") {
        const bool hasExpr = needStr("expr") != nullptr, hasCmd = needStr("cmd") != nullptr;
        if (!hasExpr && !hasCmd) { job->finishErr(kInvalidParams, "waitFor requires string param 'expr' or 'cmd'"); return; }
        if (!ValidOp(p.get("op").asString("truthy"))) { job->finishErr(kInvalidParams, "waitFor: bad 'op'"); return; }
        job->step = [this](Host& h, Job& j) { return StepWaitFor(h, j); };
        pending.push_back(job);
    } else if (m == "screenshot") {
        std::string err;
        std::string path = p.get("path").asString("");
        if (!host.Screenshot(path, err)) { job->finishErr(kHostError, err.empty() ? "screenshot failed" : err); return; }
        Value r = Value::object();
        r.set("path", path).set("ok", true);
        job->finishOk(std::move(r));
    } else if (m == "loadSave") {
        const std::string* name = needStr("name");
        if (!name || name->empty()) { job->finishErr(kInvalidParams, "loadSave requires string param 'name'"); return; }
        std::string err;
        const uint64_t before = loadedCount.load();
        if (!host.LoadSave(*name, err)) { job->finishErr(kHostError, err.empty() ? "load failed" : err); return; }
        const std::string saveName = *name;
        job->step = [this, before, saveName](Host&, Job& j) {
            if (loadedCount.load() > before) {
                Value r = Value::object();
                r.set("loaded", true).set("name", saveName);
                j.finishOk(std::move(r));
                return true;
            }
            if (NowMs() >= j.deadlineMs) { j.finishErr(kHostError, "timed out waiting for save to load"); return true; }
            return false;
        };
        pending.push_back(job);
    } else if (m == "saveGame") {
        const std::string* name = needStr("name");
        if (!name || name->empty()) { job->finishErr(kInvalidParams, "saveGame requires string param 'name'"); return; }
        // Never touch the owner's real saves: reserved names, odd characters, and existing files are refused.
        {
            std::string low;
            for (char c : *name) low.push_back(static_cast<char>(std::tolower(static_cast<unsigned char>(c))));
            for (char c : *name)
                if (!(std::isalnum(static_cast<unsigned char>(c)) || c == '_' || c == '-' || c == ' ')) {
                    job->finishErr(kInvalidParams, "saveGame: name may only contain letters, digits, '_', '-' and ' '");
                    return;
                }
            bool reserved = low == "autosave" || low == "quicksave" || low.compare(0, 10, "systemsave") == 0 || low.compare(0, 9, "quicksave") == 0;
            bool overwrite = p.get("overwrite").asBool(false);
#ifdef NVBRIDGE_MUTANT_SAVE
            reserved = false;
            overwrite = true;  // mutation twin: guards removed
#endif
            if (reserved) { job->finishErr(kInvalidParams, "saveGame: refusing reserved save name '" + *name + "'"); return; }
            if (!overwrite && host.SaveExists(*name)) {
                job->finishErr(kSaveExists, "saveGame: '" + *name + "' already exists (pass overwrite:true to replace it)");
                return;
            }
        }
        std::string err;
        const uint64_t before = savedCount.load();
        if (!host.SaveGame(*name, err)) { job->finishErr(kHostError, err.empty() ? "save failed" : err); return; }
        job->step = [this, before](Host&, Job& j) {
            if (savedCount.load() > before) {
                std::string path;
                { std::lock_guard<std::mutex> g(smu); path = lastSavedPath; }
                Value r = Value::object();
                r.set("saved", true).set("path", path);
                j.finishOk(std::move(r));
                return true;
            }
            if (NowMs() >= j.deadlineMs) { j.finishErr(kHostError, "timed out waiting for the NVSE SaveGame message (save not written?)"); return true; }
            return false;
        };
        pending.push_back(job);
    } else if (m == "quit") {
        quitAtMs = NowMs() + static_cast<uint64_t>(cfg.quitDelayMs);
        Value r = Value::object();
        r.set("quitting", true);
        job->finishOk(std::move(r));
    } else {
        registry::MethodEntry me;
        if (!registry::FindMethod(m, me)) { job->finishErr(kMethodNotFound, "unknown method: " + m); return; }
        std::string out;
        bool ok = me.fn(job->customParamsJson, out);
        if (!ok) { job->finishErr(kHostError, out); return; }
        Value r;
        std::string perr;
        if (!json::parse(out, r, perr)) { job->finishErr(kHostError, "custom method returned invalid JSON: " + perr); return; }
        job->finishOk(std::move(r));
    }
}

// ---------------------------------------------------------------------------------------------
// public
// ---------------------------------------------------------------------------------------------

Bridge::Bridge() : impl_(new Impl) {}
Bridge::~Bridge() { Stop(); }

bool Bridge::Start(const Config& cfg) {
    impl_->cfg = cfg;
    impl_->stopping = false;
    impl_->acceptor = std::thread(&Impl::AcceptLoop, impl_.get());
    Log("nvbridge: listening on %s", PipeName(cfg.instance).c_str());
    return true;
}

void Bridge::Stop() {
    if (!impl_->acceptor.joinable()) return;
    impl_->stopping = true;
    // Unblock ConnectNamedPipe with a throw-away client.
    HANDLE h = CreateFileA(PipeName(impl_->cfg.instance).c_str(), GENERIC_READ | GENERIC_WRITE, 0, nullptr, OPEN_EXISTING, 0, nullptr);
    if (h != INVALID_HANDLE_VALUE) CloseHandle(h);
    impl_->acceptor.join();
}

void Bridge::NotifyMainMenuReady() {
    if (!impl_->menuReady.exchange(true)) {
        impl_->menuReadyFrame = impl_->frames.load();
        nvcoop::PushEvent("bridge.menuReady");
    }
}

void Bridge::NotifySaved(const std::string& path) {
    { std::lock_guard<std::mutex> g(impl_->smu); impl_->lastSavedPath = path; }
    impl_->savedCount.fetch_add(1);
    nvcoop::PushEvent("game.saved", "{\"path\":" + Value(path).dump() + "}");
}

void Bridge::NotifyGameLoaded() {
    impl_->loadedCount.fetch_add(1);
    nvcoop::PushEvent("game.loaded");
}

uint64_t Bridge::frames() const { return impl_->frames.load(); }

void Bridge::Pump(Host& host) {
    Impl& s = *impl_;
    s.frames.fetch_add(1);
    s.lastPumpMs.store(NowMs());

    std::deque<std::shared_ptr<Job>> fresh;
    {
        std::lock_guard<std::mutex> g(s.qmu);
        fresh.swap(s.inbox);
    }
    for (auto& job : fresh) {
        bool abandoned;
        { std::lock_guard<std::mutex> g(job->mu); abandoned = job->abandoned; }
        if (!abandoned) s.Dispatch(host, job);
    }
    for (auto it = s.pending.begin(); it != s.pending.end();) {
        Job& j = **it;
        bool abandoned;
        { std::lock_guard<std::mutex> g(j.mu); abandoned = j.abandoned; }
        if (abandoned || j.step(host, j)) it = s.pending.erase(it);
        else ++it;
    }

    // Autoload: once the main menu is up and the delay elapsed, load the configured save (once).
    if (!s.autoloadStarted && !s.cfg.autoloadSave.empty() && s.menuReady.load() &&
        s.frames.load() >= s.menuReadyFrame + static_cast<uint64_t>(s.cfg.autoloadDelayFrames)) {
        s.autoloadStarted = true;
        std::string err;
        nvcoop::PushEvent("autoload.start", "{\"name\":" + Value(s.cfg.autoloadSave).dump() + "}");
        s.autoloadLoadedAt = s.loadedCount.load();
        if (host.LoadSave(s.cfg.autoloadSave, err)) s.autoloadWaiting = true;
        else nvcoop::PushEvent("autoload.failed", "{\"error\":" + Value(err).dump() + "}");
    }
    if (s.autoloadWaiting && s.loadedCount.load() > s.autoloadLoadedAt) {
        s.autoloadWaiting = false;
        nvcoop::PushEvent("autoload.done");
    }

    if (s.quitAtMs && NowMs() >= s.quitAtMs) {
        s.quitAtMs = 0;
        host.Quit();
    }
}

}  // namespace nvcoop::bridge
