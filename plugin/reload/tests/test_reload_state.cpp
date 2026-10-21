// Game-free tests for the reload state registry. Exit code != 0 on any failure.
// Built once (FNVCOOP_RUNTIME_MUT); run once for real and once per FNVCOOP_MUT=1..8; every twin MUST fail.
#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

#include "../reload_state.h"

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
#define TEST(name) static void name()
static void run(const char* n, void (*f)()) {
    int before = g_fail;
    f();
    std::printf("[%s] %s\n", g_fail == before ? " ok " : "FAIL", n);
}

// A tiny stand-in for a module: a counter plus a string, saved and restored through the registry.
struct Mod {
    std::string name;
    uint32_t version = 1;
    int order = 100;
    uint64_t counter = 0;
    std::string label;
    int shutdowns = 0, freshes = 0;
    bool failSave = false, throwShutdown = false, rejectRestore = false;
    std::vector<std::string>* shutdownLog = nullptr;
    uint32_t restoredFrom = 0;

    Participant make() {
        Participant p;
        p.name = name.c_str();
        p.version = version;
        p.order = order;
        p.save = [this](Writer& w) {
            if (failSave) return false;
            w.u64(counter);
            w.str(label);
            return true;
        };
        p.restore = [this](uint32_t v, Reader& r) {
            restoredFrom = v;
            counter = r.u64();
            label = r.str();
            return !rejectRestore;
        };
        p.shutdown = [this] {
            ++shutdowns;
            if (shutdownLog) shutdownLog->push_back(name);
            if (throwShutdown) throw std::runtime_error("boom");
        };
        p.fresh = [this] {
            ++freshes;
            counter = 0;
            label.clear();
        };
        return p;
    }
};

TEST(writer_reader_roundtrip_and_short_read_is_safe) {
    Writer w;
    w.u8(7);
    w.u16(0xBEEF);
    w.u32(0xDEADBEEF);
    w.u64(0x0123456789ABCDEFull);
    w.i64(-5);
    w.f64(3.25);
    w.boolean(true);
    w.str("hello");
    w.bytes(Bytes{1, 2, 3});
    Reader r(w.data());
    CHECK(r.u8() == 7);
    CHECK(r.u16() == 0xBEEF);
    CHECK(r.u32() == 0xDEADBEEF);
    CHECK(r.u64() == 0x0123456789ABCDEFull);
    CHECK(r.i64() == -5);
    CHECK(r.f64() == 3.25);
    CHECK(r.boolean());
    CHECK(r.str() == "hello");
    CHECK(r.bytes() == (Bytes{1, 2, 3}));
    CHECK(r.ok() && r.atEnd());
    CHECK(r.u32() == 0 && !r.ok());  // past the end: zero and sticky failure
    Writer big;
    big.u32(0x7FFFFFFF);  // a length prefix larger than the data must not allocate or read
    Reader r2(big.data());
    CHECK(r2.str().empty() && !r2.ok());
}

TEST(state_survives_save_shutdown_restore) {
    Registry reg;
    Mod a{"alpha"}, b{"beta"};
    a.counter = 41;
    a.label = "goodsprings";
    b.counter = 7;
    reg.add(a.make());
    reg.add(b.make());
    Bytes blob;
    Report rep;
    CHECK(reg.saveAll(&blob, &rep));
    CHECK(reg.shutdownAll(&rep) == 0);
    a.counter = b.counter = 0;
    a.label.clear();
    CHECK(reg.restoreAll(blob, &rep));
    CHECK(a.counter == 41 && a.label == "goodsprings" && b.counter == 7);
    CHECK(rep.restored == 2 && rep.freshStarts == 0 && rep.ok());
}

TEST(a_failing_save_vetoes_the_reload_before_anything_is_shut_down) {
    Registry reg;
    Mod a{"alpha"}, b{"beta"};
    b.failSave = true;
    reg.add(a.make());
    reg.add(b.make());
    Bytes blob;
    Report rep;
    CHECK(!reg.saveAll(&blob, &rep));
    CHECK(blob.empty());
    CHECK(a.shutdowns == 0 && b.shutdowns == 0);  // saveAll itself never shuts anything down
    CHECK(!rep.failures.empty());
}

TEST(preflight_refusal_vetoes_before_any_save_or_shutdown) {
    Registry reg;
    Mod a{"alpha"}, b{"hooks"};
    int saves = 0;
    Participant pb = b.make();
    pb.canUnload = [] { return std::string("hook chained over by another mod"); };
    Participant pa = a.make();
    auto inner = pa.save;
    pa.save = [&, inner](Writer& w) { ++saves; return inner(w); };
    reg.add(pa);
    reg.add(pb);
    Bytes blob;
    Report rep;
    CHECK(!reg.saveAll(&blob, &rep));
    CHECK(blob.empty() && saves == 0 && a.shutdowns == 0 && b.shutdowns == 0);
    CHECK(rep.failures.size() == 1);
}

TEST(corrupt_blob_never_reaches_restore) {
    Registry reg;
    Mod a{"alpha"};
    a.counter = 99;
    reg.add(a.make());
    Bytes blob;
    CHECK(reg.saveAll(&blob, nullptr));
    Bytes bad = blob;
    bad[bad.size() / 2] ^= 0x40;  // damage anywhere: the whole-blob checksum catches it
    a.counter = 5;
    a.restoredFrom = 0;
    Report rep;
    CHECK(!reg.restoreAll(bad, &rep));
    CHECK(a.restoredFrom == 0);
    CHECK(a.freshes == 1 && a.counter == 0);
    // truncated and empty blobs are survivable too
    Bytes shortB(blob.begin(), blob.begin() + 6);
    CHECK(!reg.restoreAll(shortB, nullptr));
    CHECK(a.freshes == 2);
    CHECK(reg.restoreAll(Bytes{}, nullptr));  // cold start is not an error
    CHECK(a.freshes == 3);
}

TEST(damaged_payload_with_a_valid_outer_checksum_is_still_rejected) {
    Registry reg;
    Mod a{"alpha"};
    a.counter = 1234;
    reg.add(a.make());
    Bytes blob;
    CHECK(reg.saveAll(&blob, nullptr));
    // flip a payload byte, then recompute the outer checksum so only the per-entry checksum can notice
    size_t pos = blob.size() - 4 - 4 - 3;  // inside the payload
    blob[pos] ^= 0x01;
    uint32_t c = crc32(blob.data(), blob.size() - 4);
    std::memcpy(blob.data() + blob.size() - 4, &c, 4);
    a.counter = 0;
    a.restoredFrom = 0;
    Report rep;
    reg.restoreAll(blob, &rep);
    CHECK(a.restoredFrom == 0);
    CHECK(a.freshes == 1);
    CHECK(!rep.failures.empty());
}

TEST(version_mismatch_uses_migrate_or_starts_fresh_never_misreads) {
    Registry old;
    Mod a{"alpha"};
    a.version = 1;
    a.counter = 10;
    old.add(a.make());
    Bytes blob;
    CHECK(old.saveAll(&blob, nullptr));

    {  // new build, layout v2, no migrate: fresh
        Registry nw;
        Mod a2{"alpha"};
        a2.version = 2;
        a2.counter = 777;
        nw.add(a2.make());
        Report rep;
        CHECK(nw.restoreAll(blob, &rep));
        CHECK(a2.freshes == 1 && a2.counter == 0 && a2.restoredFrom == 0);
    }
    {  // with migrate: called with the old version
        Registry nw;
        Mod a2{"alpha"};
        a2.version = 2;
        Participant p = a2.make();
        uint32_t seen = 0;
        p.migrate = [&](uint32_t v, Reader& r) {
            seen = v;
            a2.counter = r.u64() * 100;
            r.str();
            return true;
        };
        nw.add(p);
        Report rep;
        CHECK(nw.restoreAll(blob, &rep));
        CHECK(seen == 1 && a2.counter == 1000 && rep.migrated == 1 && a2.freshes == 0);
    }
}

TEST(restore_that_rejects_or_overreads_falls_back_to_fresh) {
    Registry reg;
    Mod a{"alpha"}, b{"beta"};
    reg.add(a.make());
    reg.add(b.make());
    Bytes blob;
    CHECK(reg.saveAll(&blob, nullptr));
    a.rejectRestore = true;
    Report rep;
    reg.restoreAll(blob, &rep);
    CHECK(a.freshes == 1 && b.freshes == 0);
    Registry reg2;  // a participant that reads more than it saved
    Participant greedy;
    greedy.name = "alpha";
    greedy.restore = [](uint32_t, Reader& r) {
        r.u64();
        r.u64();
        r.u64();
        r.u64();
        return true;
    };
    int fr = 0;
    greedy.fresh = [&] { ++fr; };
    reg2.add(greedy);
    reg2.restoreAll(blob, nullptr);
    CHECK(fr == 1);
}

TEST(removed_and_new_participants_are_handled) {
    Registry old;
    Mod a{"alpha"}, gone{"gone"};
    old.add(a.make());
    old.add(gone.make());
    Bytes blob;
    CHECK(old.saveAll(&blob, nullptr));
    Registry nw;
    Mod a2{"alpha"}, fresh{"brand_new"};
    a2.counter = 3;
    nw.add(a2.make());
    nw.add(fresh.make());
    Report rep;
    CHECK(nw.restoreAll(blob, &rep));
    CHECK(rep.dropped == 1 && fresh.freshes == 1 && a2.freshes == 0);
}

TEST(shutdown_runs_in_reverse_order_and_survives_a_throwing_participant) {
    Registry reg;
    std::vector<std::string> log;
    Mod a{"a", 1, 10}, b{"b", 1, 20}, c{"c", 1, 30};
    for (Mod* m : {&a, &b, &c}) m->shutdownLog = &log;
    b.throwShutdown = true;
    reg.add(a.make());
    reg.add(b.make());
    reg.add(c.make());
    Report rep;
    reg.shutdownAll(&rep);
    CHECK(log.size() == 3);
    CHECK(log.size() == 3 && log[0] == "c" && log[1] == "b" && log[2] == "a");
    CHECK(!rep.failures.empty());  // the throw is reported, not swallowed silently
}

TEST(leak_probes_are_read_after_shutdown_and_report_the_leak) {
    Registry reg;
    int hooks = 3;
    Mod a{"hooks"};
    Participant p = a.make();
    p.shutdown = [&] { hooks = 1; };  // forgot two of them
    reg.add(p);
    reg.addLeakProbe(LeakProbe{"hooks", [&] { return hooks; }, 0});
    reg.addLeakProbe(LeakProbe{"loader-hooks", [] { return 2; }, 2});  // baseline: owned by the permanent loader
    Report rep;
    CHECK(reg.shutdownAll(&rep) == 1);
    CHECK(rep.leaks.size() == 1 && !rep.ok());
}

TEST(managed_threads_stop_and_are_counted) {
    int base = liveThreads();
    {
        ManagedThread t;
        std::atomic<int> ticks{0};
        t.start("ticker", [&](const std::function<bool()>& stop) {
            while (!stop()) {
                ++ticks;
                std::this_thread::sleep_for(std::chrono::milliseconds(1));
            }
        });
        CHECK(liveThreads() == base + 1);
        while (ticks.load() < 3) std::this_thread::sleep_for(std::chrono::milliseconds(1));
        t.stopAndJoin();
        CHECK(liveThreads() == base);
        CHECK(!t.running());
    }
    // restart on the same object, and destruction while running
    {
        ManagedThread t;
        for (int i = 0; i < 20; ++i) t.start("again", [](const std::function<bool()>& stop) { while (!stop()) std::this_thread::yield(); });
        CHECK(liveThreads() == base + 1);
    }
    CHECK(liveThreads() == base);
}

TEST(a_hundred_reload_cycles_keep_state_and_leak_nothing) {
    Registry reg;
    Mod a{"session", 1, 50}, b{"ui", 1, 80};
    ManagedThread worker;
    std::atomic<uint64_t> bg{0};
    Participant pa = a.make();
    auto startWorker = [&] {
        worker.start("bg", [&](const std::function<bool()>& stop) {
            while (!stop()) {
                ++bg;
                std::this_thread::yield();
            }
        });
    };
    pa.shutdown = [&] { worker.stopAndJoin(); };
    reg.add(pa);
    reg.add(b.make());
    reg.addLeakProbe(threadProbe());
    startWorker();
    for (int i = 1; i <= 100; ++i) {
        a.counter += 3;
        b.counter += 1;
        a.label = "cycle" + std::to_string(i);
        Bytes blob;
        Report rep;
        CHECK(reg.saveAll(&blob, &rep));
        CHECK(reg.shutdownAll(&rep) == 0);
        a.counter = b.counter = 0;  // the new core starts with empty statics
        a.label.clear();
        CHECK(reg.restoreAll(blob, &rep));
        startWorker();
        if (a.counter != uint64_t(3 * i) || b.counter != uint64_t(i) || a.label != "cycle" + std::to_string(i)) {
            CHECK(false);
            break;
        }
    }
    worker.stopAndJoin();
    CHECK(a.counter == 300 && b.counter == 100);
    CHECK(liveThreads() == 0);
}

int main() {
    run("writer_reader_roundtrip_and_short_read_is_safe", writer_reader_roundtrip_and_short_read_is_safe);
    run("state_survives_save_shutdown_restore", state_survives_save_shutdown_restore);
    run("a_failing_save_vetoes_the_reload_before_anything_is_shut_down", a_failing_save_vetoes_the_reload_before_anything_is_shut_down);
    run("preflight_refusal_vetoes_before_any_save_or_shutdown", preflight_refusal_vetoes_before_any_save_or_shutdown);
    run("corrupt_blob_never_reaches_restore", corrupt_blob_never_reaches_restore);
    run("damaged_payload_with_a_valid_outer_checksum_is_still_rejected", damaged_payload_with_a_valid_outer_checksum_is_still_rejected);
    run("version_mismatch_uses_migrate_or_starts_fresh_never_misreads", version_mismatch_uses_migrate_or_starts_fresh_never_misreads);
    run("restore_that_rejects_or_overreads_falls_back_to_fresh", restore_that_rejects_or_overreads_falls_back_to_fresh);
    run("removed_and_new_participants_are_handled", removed_and_new_participants_are_handled);
    run("shutdown_runs_in_reverse_order_and_survives_a_throwing_participant", shutdown_runs_in_reverse_order_and_survives_a_throwing_participant);
    run("leak_probes_are_read_after_shutdown_and_report_the_leak", leak_probes_are_read_after_shutdown_and_report_the_leak);
    run("managed_threads_stop_and_are_counted", managed_threads_stop_and_are_counted);
    run("a_hundred_reload_cycles_keep_state_and_leak_nothing", a_hundred_reload_cycles_keep_state_and_leak_nothing);
    std::printf("%d checks, %d failed\n", g_checks, g_fail);
    return g_fail ? 1 : 0;
}
