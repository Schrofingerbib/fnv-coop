// Tests for the server hook: policy logic (hostfilter), file parsing, hot reload and the inline-hook mechanics
// against a fake "game function" in executable memory. No game, no server. exit code 0 = pass.
// The mutation twin (-DHOOK_MUTANT_INVERT_FILTER -DHOOK_MUTANT_SKIP_PROLOGUE_CHECK) must FAIL.
#include <windows.h>

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

#include "../../sigscan.h"
#include "../hookcore.h"
#include "../hostfilter.h"
#include "../resolve.h"
#include "../sites.h"

using namespace nvmp;
using namespace nvmp::hook;

static int g_fail = 0, g_run = 0;
#define CHECK(c)                                                                          \
    do {                                                                                  \
        ++g_run;                                                                          \
        if (!(c)) { ++g_fail; std::printf("  FAIL line %d: %s\n", __LINE__, #c); }        \
    } while (0)

// ---- parsing -----------------------------------------------------------------------------------
static void TestParseHostOwned() {
    auto s = ParseHostOwned("# header\n00104C6D  # TrudyRef\n0x00104C6E\n\n; note\n  1A2B3C ; spaces\nDEADBEEF\r\nzzz\n0\n123456789\n");
    CHECK(s.ids.size() == 4);
    CHECK(s.Contains(0x00104C6D) && s.Contains(0x00104C6E) && s.Contains(0x1A2B3C) && s.Contains(0xDEADBEEF));
    CHECK(!s.Contains(0));
    CHECK(s.rejected == 3);     // "zzz", "0", 9 digits
    CHECK(s.lines == 7);
    CHECK(s.errors.size() == 3 && s.errors[0].find("zzz") != std::string::npos);
    auto bom = ParseHostOwned("\xEF\xBB\xBF" "0000ABCD\n");
    CHECK(bom.ids.size() == 1 && bom.Contains(0xABCD));
    auto empty = ParseHostOwned("");
    CHECK(empty.ids.empty() && empty.rejected == 0);
    auto crlf = ParseHostOwned("0000000A\r\n0000000B\r\n");
    CHECK(crlf.ids.size() == 2);
    auto dup = ParseHostOwned("00000A\n0x0000000a\n");
    CHECK(dup.ids.size() == 1);
}

static void TestLoadOrderResolution() {
    // plugins.txt: '*' prefixes, comments, non-plugin lines, duplicates; FalloutNV.esm forced to index 0
    LoadOrder lo = ParseLoadOrder("# comment\n*HonestHearts.esm\n\nDeadMoney.esm # dlc\nrandom line\nhonesthearts.esm\nMod.esp\n", "test");
    CHECK(lo.plugins.size() == 4 && lo.plugins[0] == "FalloutNV.esm" && lo.IndexOf("deadmoney.ESM") == 2 && lo.IndexOf("Mod.esp") == 3);
    CHECK(lo.IndexOf("Nope.esm") == -1);
    LoadOrder withFnv = ParseLoadOrder("FalloutNV.esm\nDeadMoney.esm\n", "t");
    CHECK(withFnv.plugins.size() == 2 && withFnv.IndexOf("falloutnv.esm") == 0);

    // named entries resolve to the SERVER's indices
    LoadOrder actual = ParseLoadOrder("HonestHearts.esm\nDeadMoney.esm\nMod.esp\n", "actual");   // HH=1 DM=2 Mod=3
    auto s = ParseHostOwned("FalloutNV.esm:104C6D\nDeadMoney.esm:00ABCD  # x\nhonesthearts.esm:0x000001\nGone.esm:000005\nGone.esm:000006\nbad.esm:zz\n", &actual);
    CHECK(s.Contains(0x00104C6D) && s.Contains(0x0200ABCD) && s.Contains(0x01000001));
    CHECK(s.named == 3 && s.unresolved == 2 && s.rejected == 1);
    CHECK(s.warnings.size() == 1 && s.warnings[0].find("Gone.esm") != std::string::npos);   // warned once per plugin
    // named entries without any load order are skipped with a warning (never guessed)
    auto none = ParseHostOwned("FalloutNV.esm:104C6D\n", nullptr);
    CHECK(none.ids.empty() && none.unresolved == 1 && none.warnings.size() == 1);

    // plain hex + the file's own "# load order:" header: re-mapped when the server order differs
    const char* l9 = "# header\n# load order: 00=FalloutNV.esm, 01=DeadMoney.esm, 02=HonestHearts.esm, 03=OldWorldBlues.esm\n"
                     "00104C6D\n0100AAAA\n0200BBBB\n0300CCCC\n";
    auto r = ParseHostOwned(l9, &actual);   // actual: HH=1 DM=2, no OWB
    CHECK(r.Contains(0x00104C6D));
    CHECK(r.Contains(0x0200AAAA));   // DeadMoney 01 -> 02
    CHECK(r.Contains(0x0100BBBB));   // HonestHearts 02 -> 01
    CHECK(!r.Contains(0x0100AAAA) && !r.Contains(0x0200BBBB));
    CHECK(r.remapped == 2 && r.unresolved == 1);   // OldWorldBlues not loaded: skipped
    bool warnedOwb = false, warnedRemap = false;
    for (auto& w : r.warnings) { if (w.find("OldWorldBlues.esm") != std::string::npos) warnedOwb = true; if (w.find("re-mapped") != std::string::npos) warnedRemap = true; }
    CHECK(warnedOwb && warnedRemap);
    // identical order: nothing remapped, no warnings
    LoadOrder same = ParseLoadOrder("DeadMoney.esm\nHonestHearts.esm\nOldWorldBlues.esm\n", "same");
    auto ok = ParseHostOwned(l9, &same);
    CHECK(ok.remapped == 0 && ok.unresolved == 0 && ok.warnings.empty() && ok.ids.size() == 4 && ok.Contains(0x0300CCCC));

    // plain hex without a header: index checked against the number of loaded plugins (kept, but warned loudly)
    auto h = ParseHostOwned("00104C6D\n0900FFFF\n0A00FFFF\n", &actual);   // actual has 4 plugins: index 9/10 do not exist
    CHECK(h.ids.size() == 3 && h.badIndex == 2 && h.warnings.size() == 2);
    // guessed order is flagged
    LoadOrder guess = CanonicalAssumedOrder();
    auto g = ParseHostOwned("00104C6D\n", &guess);
    CHECK(g.warnings.size() == 1 && g.warnings[0].find("GUESS") != std::string::npos);
    // the store applies the order on reload
    HostOwnedStore st;
    st.SetLoadOrder(actual);
    st.PollOnce(1, [] { return std::string("DeadMoney.esm:000001\n"); });
    CHECK(st.Get()->Contains(0x02000001));
}

static void TestConfig() {
    std::vector<std::string> w;
    auto c = ParseConfig("# c\nenabled = yes\nblock_handover=off\ndrop_actors=1\nhost_mode=First\nhost_address=10.0.0.5\nhost_address=192.168.1.9\npoll_ms=250 # fast\nbogus=1\nlog_all=maybe\nnoequals\n", &w);
    CHECK(c.enabled && !c.blockHandover && c.dropActors && c.dropReferences);
    CHECK(c.mode == HostMode::First && c.hostAddresses.size() == 2 && c.pollMs == 250);
    CHECK(w.size() == 3);
    auto d = ParseConfig("");
    CHECK(d.enabled && d.blockHandover && d.mode == HostMode::Auto && d.pollMs == 1000 && !d.logAll);
    w.clear();
    auto e = ParseConfig("poll_ms=5\nhost_mode=zzz\n", &w);
    CHECK(e.pollMs == 1000 && e.mode == HostMode::Auto && w.size() == 2);
}

// ---- policy (the mutation twin inverts this) -----------------------------------------------------
static void TestEncounterPolicy() {
    HostOwnedSet set = ParseHostOwned("00104C6D\n00104C6E\n");
    HookConfig cfg;
    CHECK(DecideEncounter(set, cfg, 0x104C6D, 0, SenderKind::Guest) == EncounterVerdict::DropGuest);   // guest => drop
    CHECK(DecideEncounter(set, cfg, 0x104C6D, 0, SenderKind::Host) == EncounterVerdict::AllowHostSender);  // host => keep
    CHECK(DecideEncounter(set, cfg, 0x104C6D, 3, SenderKind::Guest) == EncounterVerdict::DropGuest);
    CHECK(DecideEncounter(set, cfg, 0x104C6D, 0, SenderKind::Unknown) == EncounterVerdict::AllowHostUnknown);  // fail open
    CHECK(DecideEncounter(set, cfg, 0x999999, 0, SenderKind::Guest) == EncounterVerdict::AllowNotFlagged);
    HookConfig off = cfg;
    off.enabled = false;
    CHECK(DecideEncounter(set, off, 0x104C6D, 0, SenderKind::Guest) == EncounterVerdict::AllowDisabled);
    HookConfig noAct = cfg;
    noAct.dropActors = false;
    CHECK(DecideEncounter(set, noAct, 0x104C6D, 0, SenderKind::Guest) == EncounterVerdict::AllowTypeSkipped);
    CHECK(DecideEncounter(set, noAct, 0x104C6D, 1, SenderKind::Guest) == EncounterVerdict::DropGuest);
    HookConfig noRef = cfg;
    noRef.dropReferences = false;
    CHECK(DecideEncounter(set, noRef, 0x104C6D, 2, SenderKind::Guest) == EncounterVerdict::AllowTypeSkipped);
    CHECK(DecideEncounter(set, noRef, 0x104C6D, 0, SenderKind::Guest) == EncounterVerdict::DropGuest);
}

static void TestHandoverPolicy() {
    HostOwnedSet set = ParseHostOwned("00104C6D\n");
    HookConfig cfg;
    CHECK(DecideHandover(set, cfg, 0x104C6D, true) == HandoverVerdict::Block);
    CHECK(DecideHandover(set, cfg, 0x104C6D, false) == HandoverVerdict::AllowHostAbsent);
    CHECK(DecideHandover(set, cfg, 0x1, true) == HandoverVerdict::AllowNotFlagged);
    HookConfig nb = cfg;
    nb.blockHandover = false;
    CHECK(DecideHandover(set, nb, 0x104C6D, true) == HandoverVerdict::AllowDisabled);
    nb = cfg;
    nb.enabled = false;
    CHECK(DecideHandover(set, nb, 0x104C6D, true) == HandoverVerdict::AllowDisabled);
}

static void TestHostIdentification() {
    CHECK(IsLoopbackAddress("127.0.0.1") && IsLoopbackAddress("127.1.2.3") && IsLoopbackAddress("::1") &&
          IsLoopbackAddress("localhost") && IsLoopbackAddress("::ffff:127.0.0.1") && IsLoopbackAddress("127.0.0.1:27015") &&
          IsLoopbackAddress(" 127.0.0.1 "));
    CHECK(!IsLoopbackAddress("192.168.1.5") && !IsLoopbackAddress("10.0.0.1") && !IsLoopbackAddress("1127.0.0.1") &&
          !IsLoopbackAddress("127.0.0") && !IsLoopbackAddress("127.a.b.c") && !IsLoopbackAddress("") && !IsLoopbackAddress("8.8.8.8"));

    PeerTable t;
    PeerInfo host;
    host.ptr = 0x1000; host.key0 = 0x11; host.key1 = 7; host.addr = "127.0.0.1"; host.loopback = true;
    PeerInfo guest;
    guest.ptr = 0x2000; guest.key0 = 0x22; guest.key1 = 9; guest.addr = "203.0.113.7"; guest.loopback = false;
    t.Add(host);
    t.Add(guest);

    // table lookup by pointer, then by key
    CHECK(ResolveSender(t, {0x1000, 0}, 0x22, HostMode::Auto).kind == SenderKind::Host);   // table beats first-char
    CHECK(ResolveSender(t, {0x2000, 0}, 0x22, HostMode::Auto).kind == SenderKind::Guest);
    CHECK(ResolveSender(t, {0, 0x11}, 0x22, HostMode::Auto).kind == SenderKind::Host);     // key0
    CHECK(ResolveSender(t, {0, 9}, 0x11, HostMode::Auto).kind == SenderKind::Guest);       // key1
    // unknown sender while a loopback host is known: stay Unknown (fail open), never guess
    CHECK(ResolveSender(t, {0x3000, 0x33}, 0x33, HostMode::Auto).kind == SenderKind::Unknown);
    CHECK(ResolveSender(t, {0x3000, 0x33}, 0x33, HostMode::Loopback).kind == SenderKind::Unknown);
    // no loopback peer known at all: Auto falls back to the first connected character
    PeerTable remoteOnly;
    remoteOnly.Add(guest);
    CHECK(ResolveSender(remoteOnly, {0x3000, 0x33}, 0x33, HostMode::Auto).kind == SenderKind::Host);
    CHECK(ResolveSender(remoteOnly, {0x3000, 0x34}, 0x33, HostMode::Auto).kind == SenderKind::Guest);
    CHECK(IsHostPresent(remoteOnly, {0x33}, 0x33, HostMode::Auto));
    // First mode ignores the table entirely
    CHECK(ResolveSender(t, {0x1000, 0x11}, 0x22, HostMode::First).kind == SenderKind::Guest);
    CHECK(ResolveSender(t, {0x2000, 0x22}, 0x22, HostMode::First).kind == SenderKind::Host);
    // nothing known
    PeerTable empty;
    CHECK(ResolveSender(empty, {0x1, 0x1}, 0, HostMode::Auto).kind == SenderKind::Unknown);
    CHECK(ResolveSender(empty, {0, 0}, 5, HostMode::Auto).kind == SenderKind::Unknown);

    // host presence
    CHECK(IsHostPresent(t, {0x22, 0x11}, 0x22, HostMode::Auto));
    CHECK(!IsHostPresent(t, {0x22}, 0, HostMode::Loopback));
    CHECK(IsHostPresent(t, {0x22}, 0x22, HostMode::First));
    CHECK(!IsHostPresent(t, {}, 0, HostMode::Auto));
    t.Remove(0x1000);
    CHECK(!IsHostPresent(t, {0x22}, 0, HostMode::Loopback));
    // replaced entry with the same pointer
    PeerInfo g2 = guest;
    g2.loopback = true;
    t.Add(g2);
    CHECK(t.size() == 1 && ResolveSender(t, {0x2000, 0}, 0, HostMode::Auto).kind == SenderKind::Host);
}

static void TestStoreReload() {
    HostOwnedStore st;
    CHECK(st.Get()->ids.empty());
    int reads = 0;
    auto rd = [&]() { ++reads; return std::string("00000001\n00000002\n"); };
    CHECK(st.PollOnce(100, rd) && reads == 1);
    auto snap = st.Get();
    CHECK(snap->ids.size() == 2);
    CHECK(!st.PollOnce(100, rd) && reads == 1);   // unchanged stamp: file not even read
    CHECK(st.PollOnce(101, [&]() { ++reads; return std::string("00000003\n"); }));
    CHECK(st.Get()->ids.size() == 1 && st.Get()->Contains(3) && !st.Get()->Contains(1));
    CHECK(snap->ids.size() == 2 && snap->Contains(1));   // old snapshot stays valid for readers mid-decision
    CHECK(st.PollOnce(0, [&]() { return std::string(); }) && st.Get()->ids.empty());   // file deleted => empty set
}

// End-to-end policy in a fake server: connections, hostowned.txt, encounter and hand-over decisions.
static void TestFakeServerScenario() {
    HostOwnedStore store;
    store.PollOnce(1, [] { return std::string("00104C6D # Trudy\n00104C6E\n"); });
    HookConfig cfg;
    PeerTable peers;
    PeerInfo h; h.ptr = 0xA0; h.key0 = 1; h.addr = "127.0.0.1"; h.loopback = IsLoopbackAddress(h.addr);
    PeerInfo g; g.ptr = 0xB0; g.key0 = 2; g.addr = "198.51.100.4"; g.loopback = IsLoopbackAddress(g.addr);
    peers.Add(h);
    peers.Add(g);
    std::vector<uint32_t> chars = {2, 1};   // the guest happens to be the first character: the table must win
    auto encounter = [&](uint32_t form, SenderRef s) {
        auto set = store.Get();
        auto r = ResolveSender(peers, s, chars.empty() ? 0 : chars.front(), cfg.mode);
        return IsDrop(DecideEncounter(*set, cfg, form, 0, r.kind));
    };
    CHECK(encounter(0x104C6D, {0xB0, 2}) == true);    // guest meets flagged quest actor first: dropped
    CHECK(encounter(0x104C6D, {0xA0, 1}) == false);   // host: owns it
    CHECK(encounter(0x777777, {0xB0, 2}) == false);   // unflagged actors keep first-come behaviour
    store.PollOnce(2, [] { return std::string("00104C6D\n00777777\n"); });   // hot reload while running
    CHECK(encounter(0x777777, {0xB0, 2}) == true);
    CHECK(encounter(0x104C6E, {0xB0, 2}) == false);   // removed from the file
    auto handover = [&](uint32_t form) {
        auto set = store.Get();
        bool present = IsHostPresent(peers, chars, chars.empty() ? 0 : chars.front(), cfg.mode);
        return IsBlock(DecideHandover(*set, cfg, form, present));
    };
    CHECK(handover(0x104C6D) == true);                // host connected: object stays with the host
    chars = {2};                                      // host leaves
    CHECK(handover(0x104C6D) == false);               // now the closest guest may take over
}

// ---- hook mechanics against a fake game function ------------------------------------------------
static volatile uint32_t g_bodyRan = 0;
static void* g_seenThis = nullptr;
static uint32_t g_seenArg = 0, g_calls = 0, g_dropNext = 0;
static uint8_t* g_tramp = nullptr;

extern "C" unsigned __cdecl TestDecision(void* self, uint32_t arg) {
    g_seenThis = self;
    g_seenArg = arg;
    ++g_calls;
    return g_dropNext;
}

__declspec(naked) void TestStub() {   // identical shape to EncounterStub in server_hook.cpp
    __asm {
        pushad
        mov eax, [esp + 0x24]
        push eax
        push ecx
        call TestDecision
        add esp, 8
        test eax, eax
        jnz drop
        popad
        jmp dword ptr[g_tramp]
    drop:
        popad
        ret 4
    }
}

typedef uint32_t(__thiscall* FakeFn)(void* self, uint32_t arg);

static uint8_t* MakeFake(const uint8_t* prologue, size_t plen) {
    uint8_t* p = (uint8_t*)VirtualAlloc(nullptr, 64, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
    std::memcpy(p, prologue, plen);
    size_t i = plen;
    p[i++] = 0xC7; p[i++] = 0x05;                 // mov dword ptr [&g_bodyRan], 1
    uint32_t a = (uint32_t)(uintptr_t)&g_bodyRan;
    std::memcpy(p + i, &a, 4); i += 4;
    p[i++] = 1; p[i++] = 0; p[i++] = 0; p[i++] = 0;
    p[i++] = 0xB8; p[i++] = 0x78; p[i++] = 0x56; p[i++] = 0x34; p[i++] = 0x12;   // mov eax, 0x12345678
    p[i++] = 0x8B; p[i++] = 0xE5;                 // mov esp, ebp
    p[i++] = 0x5D;                                // pop ebp
    p[i++] = 0xC2; p[i++] = 0x04; p[i++] = 0x00;  // ret 4
    return p;
}

static uint32_t CallFake(FakeFn f, void* self, uint32_t arg, uint32_t* espDelta) {
    uint32_t before, after, r;
    __asm mov before, esp
    r = f(self, arg);
    __asm mov after, esp
    *espDelta = before - after;
    return r;
}

static void TestHookMechanics() {
    static const uint8_t pro[] = {0x55, 0x8B, 0xEC, 0x83, 0xE4, 0xF8};   // push ebp; mov ebp,esp; and esp,-8 (6 bytes, like the real handler)
    uint8_t* fn = MakeFake(pro, sizeof(pro));
    uint8_t orig[32];
    std::memcpy(orig, fn, 32);
    FakeFn call = (FakeFn)fn;
    uint32_t esp = 0;

    // unhooked sanity
    g_bodyRan = 0;
    CHECK(CallFake(call, (void*)0x1234, 7, &esp) == 0x12345678 && g_bodyRan == 1 && esp == 0);

    InlineHook h;
    HookStatus st = InstallInlineHook(h, fn, pro, sizeof(pro), (const void*)&TestStub);
    CHECK(st == HookStatus::Ok && h.installed);
    g_tramp = h.tramp;
    CHECK(fn[0] == 0xE9 && fn[5] == 0x90);   // jmp + NOP filler of the 6th byte
    CHECK(InstallInlineHook(h, fn, pro, sizeof(pro), (const void*)&TestStub) == HookStatus::AlreadyInstalled);

    // pass-through: original body runs, args reach the decision, stack balanced
    g_bodyRan = 0; g_calls = 0; g_dropNext = 0;
    uint32_t r = CallFake(call, (void*)0xCAFE, 0x55AA, &esp);
    CHECK(r == 0x12345678 && g_bodyRan == 1 && g_calls == 1 && esp == 0);
    CHECK(g_seenThis == (void*)0xCAFE && g_seenArg == 0x55AA);

    // drop: original body does NOT run, callee-pop (ret 4) leaves the stack balanced
    g_bodyRan = 0; g_dropNext = 1;
    CallFake(call, (void*)0xBEEF, 0x66BB, &esp);
    CHECK(g_bodyRan == 0 && g_calls == 2 && esp == 0 && g_seenThis == (void*)0xBEEF && g_seenArg == 0x66BB);
    for (int i = 0; i < 1000; ++i) { g_dropNext = i & 1; CallFake(call, (void*)1, (uint32_t)i, &esp); if (esp) break; }
    CHECK(esp == 0);

    // remove restores the exact original bytes
    CHECK(RemoveInlineHook(h) && std::memcmp(fn, orig, 32) == 0);
    g_bodyRan = 0;
    CHECK(CallFake(call, (void*)1, 1, &esp) == 0x12345678 && g_bodyRan == 1);

    // 5-byte prologue variant (like the reselect hook): push ebp; mov ebp,esp; push -1
    static const uint8_t pro5[] = {0x55, 0x8B, 0xEC, 0x6A, 0xFF};
    uint8_t* fn5 = MakeFake(pro5, sizeof(pro5));
    // body after `push -1` must pop it: patch `mov esp, ebp` handles that already
    InlineHook h5;
    CHECK(InstallInlineHook(h5, fn5, pro5, sizeof(pro5), (const void*)&TestStub) == HookStatus::Ok);
    g_tramp = h5.tramp;
    g_bodyRan = 0; g_dropNext = 0;
    CHECK(CallFake((FakeFn)fn5, (void*)3, 4, &esp) == 0x12345678 && g_bodyRan == 1 && esp == 0);
    g_bodyRan = 0; g_dropNext = 1;
    CallFake((FakeFn)fn5, (void*)3, 4, &esp);
    CHECK(g_bodyRan == 0 && esp == 0);
}

// Mutation twin target: a prologue that is not the expected one must be refused and left untouched.
static void TestPrologueMismatchRefused() {
    static const uint8_t actual[] = {0x55, 0x8B, 0xEC, 0x83, 0xE4, 0xF0};   // `and esp,-16`, not -8
    static const uint8_t expected[] = {0x55, 0x8B, 0xEC, 0x83, 0xE4, 0xF8};
    uint8_t* fn = MakeFake(actual, sizeof(actual));
    uint8_t before[16];
    std::memcpy(before, fn, 16);
    InlineHook h;
    HookStatus st = InstallInlineHook(h, fn, expected, sizeof(expected), (const void*)&TestStub);
    CHECK(st == HookStatus::PrologueMismatch);
    CHECK(!h.installed && std::memcmp(fn, before, 16) == 0);
    CHECK(InstallInlineHook(h, fn, expected, 4, (const void*)&TestStub) == HookStatus::BadArgs);   // < 5 bytes
    CHECK(InstallInlineHook(h, nullptr, expected, 6, (const void*)&TestStub) == HookStatus::BadArgs);
}

// ---- signatures --------------------------------------------------------------------------------
static std::vector<uint8_t> BytesOfSig(const char* sig) {
    Pattern p;
    ParsePattern(sig, p);
    std::vector<uint8_t> b = p.bytes;
    for (size_t i = 0; i < b.size(); ++i) if (!p.mask[i]) b[i] = 0x11;
    return b;
}
static void TestSites() {
    for (const Site* s : {&kEnc, &kResel, &kConn}) {
        Pattern p;
        CHECK(ParsePattern(s->sig, p));
        CHECK(s->copyLen >= 5 && s->copyLen <= 16 && s->copyLen <= p.size());
        for (size_t i = 0; i < s->copyLen; ++i) CHECK(p.mask[i] != 0);   // copied prologue bytes are all fixed
        if (s->strImm) CHECK(s->strImm + 4 <= p.size() && p.mask[s->strImm] == 0);
    }
    // twins: two copies of the connection site are both found by ScanImageAll (the DLL then picks by string)
    std::vector<uint8_t> code(0x1000, 0xCC);
    auto sb = BytesOfSig(kConn.sig);
    std::memcpy(&code[0x100], sb.data(), sb.size());
    std::memcpy(&code[0x400], sb.data(), sb.size());
    CHECK(ScanBuffer(code.data(), code.size(), kConn.sig).status == ScanStatus::Ambiguous);
    // single encounter site is found uniquely
    auto eb = BytesOfSig(kEnc.sig);
    std::vector<uint8_t> code2(0x800, 0xCC);
    std::memcpy(&code2[0x80], eb.data(), eb.size());
    ScanResult r = ScanBuffer(code2.data(), code2.size(), kEnc.sig);
    CHECK(r.ok() && r.offset == 0x80);
    // the real-binary check of these signatures is plugin/nvmp/tools/verify_sigs.py
}

// Resolve every site against the REAL 9.4 storyserver (read-only: file is mapped into memory, never executed).
static void TestRealServerSites() {
    const char* env = std::getenv("NVMP_SERVER_EXE");
    std::string path = env ? env : "third_party/nvmp/nvmp_storyserver.exe";
    FILE* f = std::fopen(path.c_str(), "rb");
    if (!f) { std::printf("  (real server not found at %s, skipped)\n", path.c_str()); return; }
    std::vector<uint8_t> file;
    uint8_t buf[65536];
    size_t n;
    while ((n = std::fread(buf, 1, sizeof(buf), f)) > 0) file.insert(file.end(), buf, buf + n);
    std::fclose(f);
    std::vector<uint8_t> img;
    uint32_t imageBase = 0;
    std::vector<SectionInfo> secs;
    CHECK(MapPeFileToImage(file, img) && ParseSections(file.data(), file.size(), secs, &imageBase));
    ResolvedSites rs;
    std::vector<std::string> log;
    CHECK(ResolveSites(img.data(), img.size(), imageBase, rs, log));
    for (auto& l : log) std::printf("    %s\n", l.c_str());
    CHECK(rs.encRva == 0x9BFD0);
    CHECK(rs.reselRva == 0x9E350);
    CHECK(rs.connCandidates == 2);          // accept + disconnect twins
    CHECK(rs.connRva == 0x7FFB0);           // the one that logs "New connection from"
    CHECK(rs.findCharRva == 0x14580);       // not the twin at 0x1B20
    CHECK(rs.charMgrGlobal == 0x4E3678);    // player manager, not the twin's 0x4E35EC
    CHECK(rs.addrFnRva == 0xA74D0);
}

// If tools/build_hostowned.py output exists, it must parse cleanly with the hook's own parser.
static void TestGeneratedHostOwned() {
    const char* env = std::getenv("NVMP_HOSTOWNED");
    std::string path = env ? env : "../../../out/nvmp/hostowned.txt";
    FILE* f = std::fopen(path.c_str(), "rb");
    if (!f) { std::printf("  (no generated hostowned.txt at %s, skipped)\n", path.c_str()); return; }
    std::string text;
    char buf[4096];
    size_t n;
    while ((n = std::fread(buf, 1, sizeof(buf), f)) > 0) text.append(buf, n);
    std::fclose(f);
    LoadOrder canon = CanonicalAssumedOrder();
    HostOwnedSet s = ParseHostOwned(text, &canon);
    std::printf("    %zu FormIDs, %zu rejected, %zu unresolved, %zu remapped\n", s.ids.size(), s.rejected, s.unresolved, s.remapped);
    CHECK(s.rejected == 0 && s.unresolved == 0 && s.ids.size() > 100);
    CHECK(s.Contains(0x00104C6D));   // TrudyRef
}

int main() {
    struct { const char* n; void (*f)(); } tests[] = {
        {"ParseHostOwned", TestParseHostOwned},
        {"Load-order resolution", TestLoadOrderResolution},
        {"ParseConfig", TestConfig},
        {"Encounter policy (mutation twin)", TestEncounterPolicy},
        {"Handover policy", TestHandoverPolicy},
        {"Host identification", TestHostIdentification},
        {"Store hot reload", TestStoreReload},
        {"Fake server scenario (mutation twin)", TestFakeServerScenario},
        {"Hook mechanics", TestHookMechanics},
        {"Prologue mismatch refused (mutation twin)", TestPrologueMismatchRefused},
        {"Signature sites", TestSites},
        {"Real 9.4 server site resolution", TestRealServerSites},
        {"Generated hostowned.txt parses", TestGeneratedHostOwned},
    };
    for (auto& t : tests) {
        int before = g_fail;
        std::printf("[ RUN ] %s\n", t.n);
        t.f();
        std::printf("[%s] %s\n", g_fail == before ? "  OK " : "FAIL ", t.n);
    }
    std::printf("\n%d checks, %d failed\n", g_run, g_fail);
    return g_fail ? 1 : 0;
}
