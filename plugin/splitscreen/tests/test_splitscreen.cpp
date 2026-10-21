// Game-free tests for the split-screen libraries: config, pad router, import-table patching, focus faking, audio policy.
// Prints "splitscreen: N checks, M failed". tools\splitscreen\run_tests.py builds it once and runs it once per runtime twin (FNVCOOP_MUT=8..34).
// With SPLIT_EXE=<path to the reference FalloutNV.exe 1.4.0.525> it also checks the engine facts the design rests on (pins, XInput imports).
#include <cctype>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iterator>
#include <map>
#include <string>
#include <vector>

#include "audio_policy.h"
#include "focus_fake.h"
#include "iat_patch.h"
#include "pad_router.h"
#include "split_addrs.h"
#include "split_config.h"

namespace {
int g_checks = 0, g_failed = 0;
void Check(bool ok, const char* what, int line) {
    ++g_checks;
    if (!ok) {
        ++g_failed;
        std::printf("  FAIL line %d: %s\n", line, what);
    }
}
#define CHECK(x) Check((x), #x, __LINE__)

using namespace nvsplit;

// ------------------------------------------------------------------------------------------------ config
Config Parse(const std::string& text, bool* ok = nullptr, std::string* err = nullptr) {
    Config c;
    const bool r = ParseConfig(text, &c, err);
    if (ok) *ok = r;
    return c;
}

void TestConfig() {
    bool ok = false;
    Config c = Parse("", &ok);
    CHECK(ok && !c.enable && c.pad == -2 && !c.routesPad());

    c = Parse("[splitscreen]\nenable=1\nplayer=0\nplayers=2\n", &ok);
    CHECK(ok && c.enable && c.player == 0 && c.players == 2);
    CHECK(c.kbm && !c.ghostPad && c.audio == AudioMode::Play && c.pad == -2);  // seat 0: keyboard and mouse, sound, pad left alone
    CHECK(!c.routesPad());                                                    // no pad key: XInput is not touched

    c = Parse("[other]\nenable=0\n[splitscreen]\nenable=1\nplayer=2\nplayers=3\npad=1\n", &ok);
    CHECK(ok && c.player == 2 && c.pad == 1 && c.routesPad());
    CHECK(!c.kbm && c.ghostPad && c.audio == AudioMode::Mute);  // seat 2: pad only, neutral ghost pad, muted

    c = Parse("[splitscreen]\nenable=1\nplayers=2\npad=none\n", &ok);
    CHECK(ok && c.pad == -1 && c.routesPad());  // pad=none is -1, never pad 0

    c = Parse("; comment\n[SplitScreen]\nEnable = 1\nPLAYER = 1\nPlayers=2\nPAD=3\nKbM=1\nGhostPad=0\nAudio=Play\nFocusFake=0\nDevInject=1\n", &ok);
    CHECK(ok && c.enable && c.player == 1 && c.pad == 3 && c.kbm && !c.ghostPad && c.audio == AudioMode::Play && !c.focusFake && c.devInject);

    std::string err;
    c = Parse("[splitscreen]\nenable=1\nplayers=9\n", &ok, &err);
    CHECK(!ok && !c.enable && !err.empty());  // a bad value leaves split-screen OFF
    c = Parse("[splitscreen]\nenable=1\nplayers=2\nplayer=2\n", &ok, &err);
    CHECK(!ok && !c.enable);  // seat outside the group
    c = Parse("[splitscreen]\nenable=1\nplayers=2\npad=7\n", &ok, &err);
    CHECK(!ok && !c.enable);
    c = Parse("[splitscreen]\nenable=maybe\n", &ok, &err);
    CHECK(!ok && !c.enable);
    c = Parse("[splitscreen]\nenable=1\nplayers=2\naudio=loud\n", &ok, &err);
    CHECK(!ok && !c.enable);
}

// ------------------------------------------------------------------------------------------------ pad router
struct FakeXInput : IXInput {
    bool connected[4] = {true, true, true, true};
    std::vector<uint32_t> stateCalls, setCalls, capsCalls;
    FakeXInput() {}
    static PadState StateOf(uint32_t i) {
        PadState s = {};
        s.gamepad.buttons = static_cast<uint16_t>(1u << i);
        s.gamepad.thumbLX = static_cast<int16_t>(1000 * (i + 1));
        return s;
    }
    uint32_t getState(uint32_t u, PadState* o) override {
        stateCalls.push_back(u);
        if (u < 4 && connected[u]) { *o = StateOf(u); return kErrSuccess; }
        return kErrNotConnected;
    }
    uint32_t setState(uint32_t u, PadVibration*) override {
        setCalls.push_back(u);
        return (u < 4 && connected[u]) ? kErrSuccess : kErrNotConnected;
    }
    uint32_t getCaps(uint32_t u, uint32_t, PadCaps* o) override {
        capsCalls.push_back(u);
        if (u < 4 && connected[u]) { std::memset(o, 0, sizeof *o); o->type = 1; o->subType = static_cast<uint8_t>(10 + u); return kErrSuccess; }
        return kErrNotConnected;
    }
};

bool Called(const std::vector<uint32_t>& v, uint32_t x) {
    for (uint32_t y : v) if (y == x) return true;
    return false;
}

void TestPadRouter() {
    // pad=2: virtual pad 0 is physical pad 2, and nothing else
    {
        FakeXInput real;
        PadRouter r({2, false}, real);
        PadState s = {};
        CHECK(r.getState(0, &s) == kErrSuccess);
        CHECK(s.gamepad.buttons == (1u << 2) && s.gamepad.thumbLX == 3000);  // the state of physical pad 2
        CHECK(real.stateCalls.size() == 1 && real.stateCalls[0] == 2);        // physical pad 0 was never asked
        CHECK(r.stats().physicalPolls[2] == 1 && r.stats().physicalPolls[0] == 0);
        CHECK(r.getState(kUserAny, &s) == kErrSuccess && s.gamepad.buttons == (1u << 2));
        real.stateCalls.clear();
        for (uint32_t u = 1; u <= 3; ++u) {
            PadState t = FakeXInput::StateOf(0);
            CHECK(r.getState(u, &t) == kErrNotConnected);  // the other copies' pads do not show up under another index
        }
        CHECK(real.stateCalls.empty());
        CHECK(r.stats().hidden == 3);
        CHECK(r.getState(0, nullptr) == kErrInvalidParameter);
        // vibration follows the pad
        PadVibration v = {100, 100};
        CHECK(r.setState(0, &v) == kErrSuccess);
        CHECK(real.setCalls.size() == 1 && real.setCalls[0] == 2);
        CHECK(r.setState(1, &v) == kErrNotConnected && real.setCalls.size() == 1);
        // capabilities follow the pad
        PadCaps caps = {};
        CHECK(r.getCaps(0, 0, &caps) == kErrSuccess && caps.subType == 12);
        CHECK(real.capsCalls.size() == 1 && real.capsCalls[0] == 2);
        CHECK(r.getCaps(2, 0, &caps) == kErrNotConnected && real.capsCalls.size() == 1);
        CHECK(r.getCaps(0, 0, nullptr) == kErrInvalidParameter);
    }
    // pad=1, unplugged: with ghostPad the engine still sees a connected (neutral) pad, without it "not connected"
    {
        FakeXInput real;
        real.connected[1] = false;
        PadRouter ghost({1, true}, real);
        PadState s = FakeXInput::StateOf(3);
        CHECK(ghost.getState(0, &s) == kErrSuccess);
        CHECK(s.gamepad.buttons == 0 && s.gamepad.thumbLX == 0);
        CHECK(ghost.stats().ghost == 1);
        PadVibration v = {1, 1};
        CHECK(ghost.setState(0, &v) == kErrSuccess && ghost.stats().rumbleDropped == 1);
        PadCaps caps = {};
        CHECK(ghost.getCaps(0, 0, &caps) == kErrSuccess && caps.type == 1);
        PadRouter plain({1, false}, real);
        s = FakeXInput::StateOf(3);
        CHECK(plain.getState(0, &s) == kErrNotConnected);
        CHECK(plain.setState(0, &v) == kErrNotConnected);
        CHECK(plain.getCaps(0, 0, &caps) == kErrNotConnected);
        // once plugged in, the real state flows again
        real.connected[1] = true;
        CHECK(ghost.getState(0, &s) == kErrSuccess && s.gamepad.buttons == (1u << 1));
    }
    // pad=none: no physical pad is ever read
    {
        FakeXInput real;
        PadRouter none({-1, false}, real);
        PadState s = FakeXInput::StateOf(2);
        CHECK(none.getState(0, &s) == kErrNotConnected);
        CHECK(real.stateCalls.empty());
        PadRouter ghost({-1, true}, real);
        s = FakeXInput::StateOf(2);
        CHECK(ghost.getState(0, &s) == kErrSuccess && s.gamepad.buttons == 0 && s.gamepad.thumbLX == 0);  // neutral, not pad 0's state
        CHECK(real.stateCalls.empty());
        PadVibration v = {9, 9};
        CHECK(ghost.setState(0, &v) == kErrSuccess && real.setCalls.empty());
    }
    // no routing (pad unset): everything passes through unchanged, including pads 1..3
    {
        FakeXInput real;
        PadRouter pass({-2, false}, real);
        PadState s = {};
        CHECK(pass.getState(1, &s) == kErrSuccess && s.gamepad.buttons == (1u << 1));
        CHECK(pass.getState(0, &s) == kErrSuccess && s.gamepad.buttons == 1u);
        PadVibration v = {2, 2};
        CHECK(pass.setState(3, &v) == kErrSuccess && real.setCalls.size() == 1 && real.setCalls[0] == 3);
        PadCaps caps = {};
        CHECK(pass.getCaps(2, 0, &caps) == kErrSuccess && caps.subType == 12);
    }
}

// ------------------------------------------------------------------------------------------------ import table
struct FnSpec {
    std::string name;      // empty for an ordinal import
    int ordinal = 0;
    uint32_t slotValue = 0;
};
struct DllSpec {
    std::string dll;
    std::vector<FnSpec> fns;
};
struct Built {
    std::vector<uint8_t> img;
    std::vector<std::vector<uint32_t>> slotRva;  // per DLL, per function
};

Built BuildImage(const std::vector<DllSpec>& dlls) {
    Built b;
    b.img.assign(0x4000, 0);
    auto w32 = [&](size_t off, uint32_t v) { std::memcpy(&b.img[off], &v, 4); };
    auto w16 = [&](size_t off, uint16_t v) { std::memcpy(&b.img[off], &v, 2); };
    b.img[0] = 'M';
    b.img[1] = 'Z';
    w32(0x3C, 0x80);
    w32(0x80, 0x4550);
    const size_t opt = 0x80 + 24;
    w16(opt, 0x10B);
    w32(opt + 96 + 8, 0x1000);
    w32(opt + 96 + 12, static_cast<uint32_t>((dlls.size() + 1) * 20));
    size_t cursor = 0x1000 + (dlls.size() + 1) * 20;
    auto alloc = [&](size_t n) { size_t o = cursor; cursor += (n + 3) & ~size_t(3); return o; };
    for (size_t i = 0; i < dlls.size(); ++i) {
        const DllSpec& d = dlls[i];
        const size_t nameOff = alloc(d.dll.size() + 1);
        std::memcpy(&b.img[nameOff], d.dll.c_str(), d.dll.size() + 1);
        const size_t oft = alloc((d.fns.size() + 1) * 4), ft = alloc((d.fns.size() + 1) * 4);
        b.slotRva.emplace_back();
        for (size_t j = 0; j < d.fns.size(); ++j) {
            const FnSpec& f = d.fns[j];
            if (f.ordinal) {
                w32(oft + j * 4, 0x80000000u | static_cast<uint32_t>(f.ordinal));
            } else {
                const size_t hn = alloc(2 + f.name.size() + 1);
                std::memcpy(&b.img[hn + 2], f.name.c_str(), f.name.size() + 1);
                w32(oft + j * 4, static_cast<uint32_t>(hn));
            }
            w32(ft + j * 4, f.slotValue);
            b.slotRva.back().push_back(static_cast<uint32_t>(ft + j * 4));
        }
        const size_t desc = 0x1000 + i * 20;
        w32(desc + 0, static_cast<uint32_t>(oft));
        w32(desc + 12, static_cast<uint32_t>(nameOff));
        w32(desc + 16, static_cast<uint32_t>(ft));
    }
    return b;
}

struct FakeExports : IExports {
    std::map<std::string, uint32_t> addr;  // "dll|func"
    uint32_t addressOf(const std::string& dll, const std::string& func) override {
        auto it = addr.find(dll + "|" + func);
        return it == addr.end() ? 0 : it->second;
    }
};

struct FakeMemory : IMemory32 {
    std::map<uint32_t, uint32_t> m;
    int writes = 0;
    bool failWrites = false;
    bool read32(uint32_t a, uint32_t* v) override {
        auto it = m.find(a);
        if (it == m.end()) return false;
        *v = it->second;
        return true;
    }
    bool write32(uint32_t a, uint32_t v) override {
        if (failWrites || !m.count(a)) return false;
        m[a] = v;
        ++writes;
        return true;
    }
};

void TestImports() {
    std::vector<DllSpec> dlls = {
        {"USER32.dll", {{"GetActiveWindow", 0, 0x7000}, {"ShowCursor", 0, 0x7010}}},
        {"XINPUT1_3.dll", {{"", 2, 0x8002}, {"", 3, 0x8003}}},  // by ordinal, like the game
        {"xinput1_4.dll", {{"XInputGetState", 0, 0x9001}, {"XInputSetState", 0, 0x9002}}},
    };
    Built b = BuildImage(dlls);
    std::vector<ImportRef> all;
    std::string err;
    CHECK(EnumImports(b.img.data(), b.img.size(), &all, &err));
    CHECK(all.size() == 6);
    CHECK(all.size() == 6 && all[0].dll == "USER32.dll" && all[0].name == "GetActiveWindow" && all[0].slotRva == b.slotRva[0][0] && all[0].value == 0x7000);
    CHECK(all.size() == 6 && all[2].byOrdinal && all[2].ordinal == 2 && all[3].ordinal == 3);
    CHECK(all.size() == 6 && all[4].dll == "xinput1_4.dll" && all[4].name == "XInputGetState");

    // by name
    auto hits = FindImportSlots(all, "user32", "GetActiveWindow", nullptr);
    CHECK(hits.size() == 1 && hits[0].slotRva == b.slotRva[0][0]);
    // XInputGetState: the by-name import of xinput1_4 only (the ordinal ones cannot be told apart without the DLL)
    hits = FindImportSlots(all, "xinput", "XInputGetState", nullptr);
    CHECK(hits.size() == 1 && hits[0].dll == "xinput1_4.dll");
    // by value: the loader put the export's address into the ordinal slot
    FakeExports ex;
    ex.addr["XINPUT1_3.dll|XInputGetState"] = 0x8002;
    ex.addr["XINPUT1_3.dll|XInputSetState"] = 0x8003;
    hits = FindImportSlots(all, "xinput", "XInputGetState", &ex);
    CHECK(hits.size() == 2);  // the ordinal import (by value) and the by-name one
    bool hasOrdinalSlot = false;
    for (const auto& h : hits) hasOrdinalSlot |= (h.byOrdinal && h.slotRva == b.slotRva[1][0]);
    CHECK(hasOrdinalSlot);
    hits = FindImportSlots(all, "xinput", "XInputSetState", &ex);
    bool hasSet = false;
    for (const auto& h : hits) hasSet |= (h.byOrdinal && h.slotRva == b.slotRva[1][1]);
    CHECK(hits.size() == 2 && hasSet);
    CHECK(FindImportSlots(all, "xinput", "XInputGetCapabilities", &ex).empty());
    CHECK(FindImportSlots(all, "dsound", "XInputGetState", &ex).empty());

    // damaged tables: no crash, a message, and what was read before stays
    std::vector<ImportRef> part;
    CHECK(!EnumImports(b.img.data(), 0x1010, &part, &err) && !err.empty());
    Built b2 = b;
    b2.img[0] = 'X';
    part.clear();
    CHECK(!EnumImports(b2.img.data(), b2.img.size(), &part, &err));
    Built b3 = b;
    uint32_t wild = 0xFFFFF000u;
    std::memcpy(&b3.img[0x1000 + 12], &wild, 4);  // first DLL name points outside the image
    part.clear();
    CHECK(!EnumImports(b3.img.data(), b3.img.size(), &part, &err));
    CHECK(!EnumImports(nullptr, 0, &part, &err));

    // patcher
    FakeMemory mem;
    mem.m[0x1000] = 0xAAAA;
    mem.m[0x1004] = 0xBBBB;
    mem.m[0x1008] = 0;  // unbound slot
    IatPatcher p(mem);
    uint32_t orig = 0;
    CHECK(p.hook(0x1000, 0xD000, &orig) && orig == 0xAAAA && mem.m[0x1000] == 0xD000);
    CHECK(p.live() == 1);
    CHECK(!p.hook(0x1000, 0xD111, &orig));  // twice: refused, the original stays what it was
    CHECK(mem.m[0x1000] == 0xD000 && orig == 0xAAAA);
    CHECK(!p.hook(0x1008, 0xD000, &orig) && mem.m[0x1008] == 0 && p.live() == 1);  // unbound
    CHECK(!p.hook(0x2000, 0xD000, &orig));                                         // unreadable
    CHECK(p.hook(0x1004, 0xD004, &orig) && orig == 0xBBBB);
    CHECK(p.allIntact());
    CHECK(p.unhook(0x1000) && mem.m[0x1000] == 0xAAAA && p.live() == 1);
    CHECK(!p.unhook(0x1000));  // not ours any more
    // someone chained over the second slot
    mem.m[0x1004] = 0xEEEE;
    CHECK(!p.allIntact());
    CHECK(!p.unhook(0x1004) && mem.m[0x1004] == 0xEEEE && p.live() == 1);  // left alone
    CHECK(p.unhookAll() == 1);
    mem.m[0x1004] = 0xD004;
    CHECK(p.unhookAll() == 0 && mem.m[0x1004] == 0xBBBB && p.live() == 0);
    // a failed write is not recorded
    mem.failWrites = true;
    CHECK(!p.hook(0x1000, 0xD000, &orig) && p.live() == 0);
}

// The engine facts. Only with SPLIT_EXE=<reference FalloutNV.exe>.
void TestRealExe(const char* path) {
    std::ifstream f(path, std::ios::binary);
    if (!f) {
        CHECK(false && "SPLIT_EXE is set but cannot be opened");
        return;
    }
    std::vector<uint8_t> file((std::istreambuf_iterator<char>(f)), std::istreambuf_iterator<char>());
    std::string err;
    std::vector<uint8_t> img = MapPeFile(file.data(), file.size(), &err);
    CHECK(!img.empty());
    if (img.empty()) return;
    for (const auto& pin : addr::kPins) {
        const uint32_t off = pin.addr - addr::kImageBase;
        const bool inside = off + 16 <= img.size();
        CHECK(inside && std::memcmp(&img[off], pin.bytes, 16) == 0);
        if (!inside || std::memcmp(&img[off], pin.bytes, 16) != 0) std::printf("  pin mismatch: %s\n", pin.name);
    }
    std::vector<ImportRef> all;
    CHECK(EnumImports(img.data(), img.size(), &all, &err));
    std::vector<ImportRef> xi;
    for (const auto& r : all) {
        std::string l = r.dll;
        for (char& c : l) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
        if (l.compare(0, 6, "xinput") == 0) xi.push_back(r);
    }
    CHECK(xi.size() == 2);  // the game imports exactly XInputGetState and XInputSetState (not XInputGetCapabilities) ...
    CHECK(xi.size() == 2 && xi[0].byOrdinal && xi[0].ordinal == 2 && xi[0].slotRva == addr::XInputGetStateIatRva);
    CHECK(xi.size() == 2 && xi[1].byOrdinal && xi[1].ordinal == 3 && xi[1].slotRva == addr::XInputSetStateIatRva);  // ... by ordinal
    CHECK(!xi.empty() && xi[0].dll.size() >= 9 && (xi[0].dll == "XINPUT1_3.dll" || xi[0].dll == "xinput1_3.dll"));
    // the value match finds both slots once the DLL's export addresses are known
    if (xi.size() == 2) {
        FakeExports ex;
        ex.addr[xi[0].dll + "|XInputGetState"] = xi[0].value;
        ex.addr[xi[0].dll + "|XInputSetState"] = xi[1].value;
        auto g = FindImportSlots(all, "xinput", "XInputGetState", &ex);
        auto s = FindImportSlots(all, "xinput", "XInputSetState", &ex);
        CHECK(g.size() == 1 && g[0].slotRva == addr::XInputGetStateIatRva);
        CHECK(s.size() == 1 && s[0].slotRva == addr::XInputSetStateIatRva);
    }
    auto ga = FindImportSlots(all, "user32", "GetActiveWindow", nullptr);
    CHECK(ga.size() == 1);
    // no GetForegroundWindow / GetFocus import: the engine's focus questions are GetActiveWindow only
    CHECK(FindImportSlots(all, "user32", "GetForegroundWindow", nullptr).empty());
    std::printf("  real exe: %zu imports, XInput slots 0x%X and 0x%X\n", all.size(), xi.size() > 0 ? xi[0].slotRva : 0u, xi.size() > 1 ? xi[1].slotRva : 0u);
}

// ------------------------------------------------------------------------------------------------ focus fake
struct FakeWindows : IWindowQuery {
    std::map<uint32_t, uintptr_t> byThread;
    uintptr_t topWindowOfThread(uint32_t tid) override {
        auto it = byThread.find(tid);
        return it == byThread.end() ? 0 : it->second;
    }
};

void TestFocusFake() {
    FakeWindows w;
    w.byThread[7] = 0x1111;
    w.byThread[8] = 0x2222;
    CHECK(ResolveActiveWindow(true, 0, 7, w) == 0x1111);       // inactive copy: the game window instead of NULL
    CHECK(ResolveActiveWindow(true, 0, 8, w) == 0x2222);       // and the window of the calling thread, not another one
    CHECK(ResolveActiveWindow(true, 0x9999, 7, w) == 0x9999);  // a real answer (an engine dialog) is never replaced
    CHECK(ResolveActiveWindow(false, 0, 7, w) == 0);           // switched off
    CHECK(ResolveActiveWindow(true, 0, 99, w) == 0);           // a thread without a window stays NULL
}

// ------------------------------------------------------------------------------------------------ audio
struct FakeSessions : ISessions {
    std::vector<AudioSession> s;
    int muteCalls = 0;
    bool list(std::vector<AudioSession>* out) override { *out = s; return true; }
    bool setMute(const std::string& id, bool mute) override {
        for (auto& a : s) if (a.id == id) { a.muted = mute; ++muteCalls; return true; }
        return false;
    }
    const AudioSession* get(const std::string& id) const {
        for (const auto& a : s) if (a.id == id) return &a;
        return nullptr;
    }
};

void TestAudio() {
    const uint32_t me = 4242;
    {
        FakeSessions fs;
        fs.s = {{"game-a", me, false}, {"game-b", me, false}, {"browser", 99, false}};
        AudioMuter m(AudioMode::Mute, me);
        m.tick(fs);
        CHECK(fs.get("game-a")->muted && fs.get("game-b")->muted);
        CHECK(!fs.get("browser")->muted);  // other programs are never touched
        CHECK(m.mutedCount() == 2);
        fs.s.push_back({"game-c", me, false});  // a session appears later (the first sound)
        m.tick(fs);
        CHECK(fs.get("game-c")->muted && m.mutedCount() == 3);
        const int calls = fs.muteCalls;
        m.tick(fs);
        CHECK(fs.muteCalls == calls);  // nothing to do: no repeated calls
        m.release(fs);
        CHECK(!fs.get("game-a")->muted && !fs.get("game-b")->muted && !fs.get("game-c")->muted);
        CHECK(!fs.get("browser")->muted && m.mutedCount() == 0);
    }
    {
        // a session the user muted by hand is not ours: it stays muted after release
        FakeSessions fs;
        fs.s = {{"hand", me, true}, {"game", me, false}};
        AudioMuter m(AudioMode::Mute, me);
        m.tick(fs);
        CHECK(m.mutedCount() == 1);
        m.release(fs);
        CHECK(fs.get("hand")->muted && !fs.get("game")->muted);
    }
    {
        // player one: sound on, nothing is touched
        FakeSessions fs;
        fs.s = {{"game", me, false}};
        AudioMuter m(AudioMode::Play, me);
        m.tick(fs);
        CHECK(!fs.get("game")->muted && fs.muteCalls == 0 && m.mutedCount() == 0);
    }
}

}  // namespace

int main() {
    TestConfig();
    TestPadRouter();
    TestImports();
    TestFocusFake();
    TestAudio();
    if (const char* exe = std::getenv("SPLIT_EXE")) {
        if (*exe) TestRealExe(exe);
    } else {
        std::printf("  (SPLIT_EXE not set: engine pins and the XInput import facts were not checked)\n");
    }
    std::printf("splitscreen: %d checks, %d failed\n", g_checks, g_failed);
    return g_failed ? 1 : 0;
}
