// Game-free tests for the reload drain (plugin/reload/reload_drain.cpp), called directly (no core DLL needed).
// Built once (FNVCOOP_RUNTIME_MUT); run once for real and once per FNVCOOP_MUT in {7,8,10,11,12,13,14}; every twin MUST fail.
//
// The code range under test is a PE section of this exe (".nvblk") that holds only the two functions threads are parked in.
// Idle helper threads run code in .text, so they are NOT inside the range: that lets the tests tell "inside" from "not inside"
// per thread (needed to catch the "only the first thread is inspected" mutant).
#include <windows.h>

#include <intrin.h>

#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <string>
#include <thread>
#include <vector>

#include "../reload_drain.h"

using namespace nvreload;

static int g_fail = 0, g_checks = 0;
#define CHECK(c)                                                       \
    do {                                                               \
        ++g_checks;                                                    \
        if (!(c)) {                                                    \
            ++g_fail;                                                  \
            std::printf("  FAIL %s:%d  %s\n", __FILE__, __LINE__, #c); \
        }                                                              \
    } while (0)
#define CHECKM(c, msg)                                                          \
    do {                                                                        \
        ++g_checks;                                                             \
        if (!(c)) {                                                             \
            ++g_fail;                                                           \
            std::printf("  FAIL %s:%d  %s  [%s]\n", __FILE__, __LINE__, #c, std::string(msg).c_str()); \
        }                                                                       \
    } while (0)
static void run(const char* n, void (*f)()) {
    int before = g_fail;
    f();
    std::printf("[%s] %s\n", g_fail == before ? " ok " : "FAIL", n);
}

// ---- the code the "core" would run: lives alone in its own section ----
#pragma code_seg(push, ".nvblk")
// Parked inside a kernel call: the instruction pointer is in the OS, only a return address on the stack points here.
__declspec(noinline) void parkedInKernel(volatile LONG* go) {
    while (*go) Sleep(1);
}
// Spinning: the instruction pointer itself is here.
__declspec(noinline) void spinning(volatile LONG* go) {
    while (*go) {
        _mm_pause();
    }
}
#pragma code_seg(pop)

static bool sectionRange(const char* name, uintptr_t* lo, uintptr_t* hi) {
    auto* m = reinterpret_cast<const char*>(GetModuleHandleA(nullptr));
    auto* dos = reinterpret_cast<const IMAGE_DOS_HEADER*>(m);
    auto* nt = reinterpret_cast<const IMAGE_NT_HEADERS*>(m + dos->e_lfanew);
    const IMAGE_SECTION_HEADER* sec = IMAGE_FIRST_SECTION(nt);
    for (unsigned i = 0; i < nt->FileHeader.NumberOfSections; ++i)
        if (std::strncmp(reinterpret_cast<const char*>(sec[i].Name), name, 8) == 0) {
            *lo = reinterpret_cast<uintptr_t>(m) + sec[i].VirtualAddress;
            *hi = *lo + sec[i].Misc.VirtualSize;
            return true;
        }
    return false;
}

static std::vector<CodeRange> g_ranges;

// A helper thread that runs `fn(&flag)` until flag is cleared, and reports its thread id.
struct Parked {
    volatile LONG flag = 1;
    std::atomic<unsigned long> tid{0};
    std::thread th;
    void start(void (*fn)(volatile LONG*)) {
        th = std::thread([this, fn] {
            tid = GetCurrentThreadId();
            fn(&flag);
        });
        while (tid.load() == 0) Sleep(1);
        Sleep(40);  // let it get inside
    }
    void release() {
        flag = 0;
        if (th.joinable()) th.join();
    }
};
// A thread that is idle in .text (never inside the range).
struct Idle {
    volatile LONG flag = 1;
    std::thread th;
    void start() {
        th = std::thread([this] {
            while (flag) Sleep(2);
        });
    }
    void release() {
        flag = 0;
        if (th.joinable()) th.join();
    }
};

static bool contains(const std::string& s, const char* sub) { return s.find(sub) != std::string::npos; }

static void t_nobody_inside() {
    DrainResult r = waitDrained(g_ranges, {}, 0);
    CHECK(r.drained);
    CHECK(r.attempts == 1);
    CHECK(r.why.empty());
    CHECK(r.where.empty());
    CHECK(r.busyThread == 0);
    CHECK(r.timeoutMs == 0);
    std::string j = r.toJson();
    CHECK(contains(j, "\"drained\":true"));
    CHECK(contains(j, "\"why\":\"\""));
}

static void t_return_address_inside_times_out() {
    Idle idle[6];  // created first so they come earlier in the thread list than the parked one
    for (auto& i : idle) i.start();
    Parked p;
    p.start(parkedInKernel);
    const auto t0 = std::chrono::steady_clock::now();
    DrainResult r = waitDrained(g_ranges, {}, 150);
    const auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - t0).count();
    CHECKM(!r.drained, "a thread parked inside the range must block the drain");
    CHECK(r.busyThread == p.tid.load());
    CHECKM(r.attempts > 1, "it must keep retrying until the timeout, attempts=" + std::to_string(r.attempts));
    CHECKM(ms >= 120, "it must wait about the full timeout, waited " + std::to_string(ms) + " ms");
    CHECKM(r.elapsedMs >= 100 && r.elapsedMs < 5000, "elapsedMs " + std::to_string(r.elapsedMs));
    CHECK(r.timeoutMs == 150);
    CHECK(r.scanned >= 7);
    CHECKM(contains(r.where, "return address 0x"), r.where);
    CHECK(contains(r.where, "points into the test section 0x"));
    CHECK(contains(r.why, "drain timed out after "));
    CHECK(contains(r.why, "limit 150 ms"));
    CHECK(contains(r.why, ("thread " + std::to_string(p.tid.load())).c_str()));
    CHECK(contains(r.why, r.where.c_str()));
    std::string j = r.toJson();
    CHECK(contains(j, "\"drained\":false"));
    CHECK(contains(j, "\"timeoutMs\":150"));
    CHECK(contains(j, ("\"busyThread\":" + std::to_string(p.tid.load())).c_str()));
    CHECK(contains(j, "\"where\":\"return address 0x"));
    p.release();
    for (auto& i : idle) i.release();
}

static void t_instruction_pointer_inside() {
    Parked p;
    p.start(spinning);
    DrainResult r = waitDrained(g_ranges, {}, 60);
    CHECK(!r.drained);
    CHECK(r.busyThread == p.tid.load());
    CHECKM(contains(r.where, "instruction pointer 0x"), r.where);
    CHECK(contains(r.where, "is in the test section"));
    p.release();
}

static void t_zero_timeout_is_one_attempt() {
    Parked p;
    p.start(parkedInKernel);
    DrainResult r = waitDrained(g_ranges, {}, 0);
    CHECK(!r.drained);
    CHECKM(r.attempts == 1, "timeoutMs 0 means one attempt, got " + std::to_string(r.attempts));
    CHECK(contains(r.why, "limit 0 ms"));
    p.release();
}

static void t_ignore_list_is_honoured() {
    Parked p;
    p.start(parkedInKernel);
    DrainResult r = waitDrained(g_ranges, {p.tid.load()}, 0);
    CHECKM(r.drained, "a thread on the ignore list must not delay the drain: " + r.why);
    p.release();
}

static void t_waits_for_a_thread_that_leaves() {
    Parked p;
    p.start(parkedInKernel);
    std::atomic<unsigned long> relTid{0};
    std::thread releaser([&] {
        relTid = GetCurrentThreadId();
        Sleep(120);
        p.flag = 0;
    });
    while (relTid.load() == 0) Sleep(1);
    DrainResult r = waitDrained(g_ranges, {relTid.load()}, 3000);  // the releaser runs .text code, but ignore it anyway
    CHECKM(r.drained, "the drain must wait for a thread that leaves shortly: " + r.why);
    CHECKM(r.attempts > 1, "it had to retry at least once, attempts=" + std::to_string(r.attempts));
    CHECKM(r.elapsedMs >= 80 && r.elapsedMs < 3000, "elapsedMs " + std::to_string(r.elapsedMs));
    CHECK(r.why.empty() && r.where.empty());
    releaser.join();
    p.release();
}

static void t_only_inside_when_range_matches() {
    // The same parked thread is not counted when the watched range is somewhere else (here: the range of a different section).
    Parked p;
    p.start(parkedInKernel);
    uintptr_t lo = 0, hi = 0;
    CHECK(sectionRange(".rdata", &lo, &hi));
    std::vector<CodeRange> other;
    CodeRange c;
    c.lo = lo;
    c.hi = hi;
    other.push_back(c);
    DrainResult r = waitDrained(other, {}, 0);
    CHECKM(r.drained, "a thread outside the watched range must not block the drain: " + r.where);
    p.release();
}

static void t_module_range() {
    CodeRange m = moduleRangeOf(reinterpret_cast<const void*>(&t_nobody_inside));
    CHECK(m.lo != 0 && m.hi > m.lo);
    CHECK(m.lo <= reinterpret_cast<uintptr_t>(&t_nobody_inside) && reinterpret_cast<uintptr_t>(&t_nobody_inside) < m.hi);
    CHECK(m.what && std::strcmp(m.what, "core image") == 0);
}

static void t_json_escapes() {
    DrainResult r;
    r.why = "a\"b\\c\nd";
    r.where = "x";
    std::string j = r.toJson();
    CHECKM(contains(j, "\"why\":\"a\\\"b\\\\c\\u000ad\""), j);
    // nothing raw that would break a JSON parser
    CHECK(j.find('\n') == std::string::npos);
}

int main() {
    std::setvbuf(stdout, nullptr, _IONBF, 0);
    uintptr_t lo = 0, hi = 0;
    if (!sectionRange(".nvblk", &lo, &hi)) {
        std::printf("FAIL: .nvblk section not found in the exe\n");
        return 2;
    }
    CodeRange c;
    c.lo = lo;
    c.hi = hi;
    c.what = "test section";
    g_ranges.push_back(c);
    CHECK(reinterpret_cast<uintptr_t>(&parkedInKernel) >= lo && reinterpret_cast<uintptr_t>(&parkedInKernel) < hi);
    CHECK(reinterpret_cast<uintptr_t>(&spinning) >= lo && reinterpret_cast<uintptr_t>(&spinning) < hi);

    run("nobody inside: drains at once, empty why/where", t_nobody_inside);
    run("return address inside: times out, full diagnosis", t_return_address_inside_times_out);
    run("instruction pointer inside: reported as such", t_instruction_pointer_inside);
    run("timeout 0 is one attempt", t_zero_timeout_is_one_attempt);
    run("ignore list is honoured", t_ignore_list_is_honoured);
    run("waits for a thread that leaves", t_waits_for_a_thread_that_leaves);
    run("range must match", t_only_inside_when_range_matches);
    run("moduleRangeOf", t_module_range);
    run("toJson escapes", t_json_escapes);
    std::printf("%d checks, %d failed\n", g_checks, g_fail);
    return g_fail ? 1 : 0;
}
