// Tests for the nvcoop-side NV:MP client patcher. Uses the REAL NV:MP 9.4 client.dll expanded in memory
// (never loaded or executed). Mutation twin (-DCLIENT_PATCHER_MUTANT_ALL -DPATCHES_MUTANT_SKIP_VERIFY) must fail.
#include <windows.h>

#include <cstdio>
#include <cstdlib>
#include <map>
#include <cstring>
#include <fstream>
#include <iterator>
#include <string>
#include <vector>

#include "../../../common/registry.h"
#include "../../sigscan.h"
#include "../../ghost_ref.h"
#include "../client_patcher.h"

using namespace nvmp;
using namespace nvmp::client;

static int g_fail = 0, g_run = 0;
#define CHECK(c)                                                                      \
    do {                                                                              \
        ++g_run;                                                                      \
        if (!(c)) { ++g_fail; std::printf("  FAIL line %d: %s\n", __LINE__, #c); }    \
    } while (0)

struct Event { std::string type, json; };
struct FakeMods : ModuleSource {
    ModuleView view;
    bool present = false;
    int lookups = 0;
    bool Find(const std::string& name, ModuleView& out) override {
        ++lookups;
        if (!present || name != "client.dll") return false;
        out = view;
        return true;
    }
};

static std::vector<uint8_t> g_image;   // real client.dll, mapped
static bool g_have = false;

static void LoadReal() {
    const char* env = std::getenv("NVMP_CLIENT_DLL");
    std::string path = env ? env : "third_party/nvmp/nvmp/client.dll";
    std::ifstream f(path, std::ios::binary);
    if (!f) return;
    std::vector<uint8_t> file((std::istreambuf_iterator<char>(f)), std::istreambuf_iterator<char>());
    g_have = MapPeFileToImage(file, g_image);
}

static ConfigGetter Cfg(std::map<std::string, std::string> kv) {
    return [kv](const char* k, const char* d) { auto it = kv.find(k); return it == kv.end() ? std::string(d) : it->second; };
}
static bool Has(const std::vector<Event>& ev, const char* type, const char* needle = nullptr) {
    for (auto& e : ev) if (e.type == type && (!needle || e.json.find(needle) != std::string::npos)) return true;
    return false;
}

namespace nvmp::client { bool IsClientDllName(const wchar_t* n, size_t len); }

// The loader-lock callback's name test: CRT-free, exact, case-insensitive, length-bounded (buffers are not NUL-terminated).
static void TestDllNameMatch() {
    using nvmp::client::IsClientDllName;
    CHECK(IsClientDllName(L"client.dll", 10) && IsClientDllName(L"CLIENT.DLL", 10) && IsClientDllName(L"Client.Dll", 10));
    CHECK(!IsClientDllName(L"client.dl", 9) && !IsClientDllName(L"xclient.dll", 11) && !IsClientDllName(L"steamclient.dll", 15));
    CHECK(!IsClientDllName(L"client.dlx", 10) && !IsClientDllName(L"", 0) && !IsClientDllName(L"client.dll.bak", 14));
    CHECK(IsClientDllName(L"client.dllEXTRA", 10));   // only `len` wchar_t are looked at
}

static void TestSettings() {
    std::vector<std::string> w;
    Settings d = ParseSettings(Cfg({}), &w);
    CHECK(d.enabled.size() == 1 && d.enabled.count("client.quested_not_localized") && w.empty());   // only the recommended first patch
    Settings all = ParseSettings(Cfg({{"questedRef", "1"}, {"questedRefScan", "yes"}, {"scriptedRef", "on"}, {"ghostDamage", "true"}}), &w);
    CHECK(all.enabled.size() == 6);   // quested + questedRef + scan + scriptedRef + 2 ghost
    Settings off = ParseSettings(Cfg({{"quested", "0"}}), &w);
    CHECK(off.enabled.empty());
    Settings kill = ParseSettings(Cfg({{"enabled", "0"}, {"questedRef", "1"}}), &w);
    CHECK(kill.enabled.empty());
    Settings lst = ParseSettings(Cfg({{"patches", "questedRef, client.scripted_ref_not_localized ,none"}}), &w);
    CHECK(lst.enabled.size() == 2 && lst.enabled.count("client.quested_ref_not_localized") && !lst.enabled.count("client.quested_not_localized"));
    w.clear();
    Settings bad = ParseSettings(Cfg({{"patches", "nonsense,quested"}}), &w);
    CHECK(bad.enabled.size() == 1 && w.size() == 1);
    CHECK(ParseSettings(Cfg({{"patches", "none"}}), &w).enabled.empty());
    CHECK(ParseSettings(Cfg({{"autoApply", "0"}}), &w).autoApply == false);
    CHECK(ExpandName("ghostDamage").size() == 2 && ExpandName("server.attack_owner_bypass").empty() && ExpandName("x").empty());
}

static void TestWaiting() {
    FakeMods mods;
    std::vector<Event> ev;
    Settings s = ParseSettings(Cfg({}));
    s.pollEveryFrames = 10;
    s.giveUpAfterFrames = 50;
    ClientPatcher p(s, mods, DirectWriter(), [&](const char* t, const std::string& j) { ev.push_back({t, j}); });
    for (int i = 0; i < 9; ++i) p.OnFrame();
    CHECK(mods.lookups == 1 && ev.empty() && p.phase() == Phase::Waiting);   // polled once (frame 1), not every frame
    p.NotifyModuleLoaded();
    p.OnFrame();                                                              // notification forces a look at the next frame
    CHECK(mods.lookups == 2);
    for (int i = 0; i < 60; ++i) p.OnFrame();
    CHECK(p.phase() == Phase::GaveUp && Has(ev, "nvmp.client.absent"));
    size_t n = ev.size();
    for (int i = 0; i < 100; ++i) p.OnFrame();
    CHECK(ev.size() == n);                                                     // silent afterwards
    CHECK(p.StatusJson().find("\"phase\":\"gave-up\"") != std::string::npos);
}

static void TestRealImage() {
    if (!g_have) { std::printf("  (real client.dll not found, skipped)\n"); return; }
    std::vector<uint8_t> img = g_image;   // working copy
    const std::vector<uint8_t> pristine = g_image;
    FakeMods mods;
    mods.view = {img.data(), img.size()};
    std::vector<Event> ev;
    auto sink = [&](const char* t, const std::string& j) { ev.push_back({t, j}); };

    // module not there yet at frame 1, appears later (late NV:MP load), found via the notification
    Settings s = ParseSettings(Cfg({}));
    ClientPatcher p(s, mods, DirectWriter(), sink);
    p.OnFrame();
    CHECK(img == pristine && ev.empty());
    mods.present = true;
    p.NotifyModuleLoaded();
    p.OnFrame();
    CHECK(p.phase() == Phase::Done);
    CHECK(Has(ev, "nvmp.client.detected") && Has(ev, "nvmp.patch", "client.quested_not_localized") && Has(ev, "nvmp.patch", "\"status\":\"applied\"") &&
          Has(ev, "nvmp.patches.done", "\"applied\":1,\"failed\":0"));
    CHECK(img[0xCCAF6] == 0xEB);                     // quested patch applied
    CHECK(img[0xCCB19] == 0x75 && img[0xCD09E] == 0x75 && img[0xCDFDE] == 0x0F && img[0xCDFDF] == 0x85);   // nothing else touched
    size_t diff = 0;
    for (size_t i = 0; i < img.size(); ++i) diff += img[i] != pristine[i];
    CHECK(diff == 1);                                // exactly one byte changed in the whole image
    std::string js = p.StatusJson();
    CHECK(js.find("\"name\":\"client.quested_not_localized\",\"enabled\":true,\"status\":\"applied\",\"rva\":\"0xCCAF6\",\"applied\":true") != std::string::npos);
    CHECK(js.find("client.quested_ref_not_localized\",\"enabled\":false") != std::string::npos);
    size_t evCount = ev.size();
    for (int i = 0; i < 500; ++i) p.OnFrame();
    CHECK(ev.size() == evCount);                     // applies once

    // manual revert / re-apply (RPC path), also for a patch that was not enabled
    CHECK(p.Revert("client.quested_not_localized") == PatchStatus::Reverted && img == pristine);
    CHECK(p.Apply("client.quested_ref_not_localized") == PatchStatus::Applied && img[0xCD09E] == 0xEB && img[0xCD09F] == 0x28);
    CHECK(p.Revert("client.quested_ref_not_localized") == PatchStatus::Reverted && img == pristine);
    CHECK(p.Apply("server.attack_owner_bypass") == PatchStatus::BadDefinition);     // server patches are not ours
    CHECK(p.Apply("nope") == PatchStatus::BadDefinition);
    CHECK(p.Revert("client.ghost_damage_flag31") == PatchStatus::NotApplied);

    // a second patcher on an already-patched image: already-applied, still exactly one byte
    ClientPatcher a(s, mods, DirectWriter(), sink);
    a.Apply("client.quested_not_localized");
    ev.clear();
    ClientPatcher b(s, mods, DirectWriter(), sink);
    b.OnFrame();
    CHECK(Has(ev, "nvmp.patch", "already-applied"));

    // full set enabled
    img = pristine;
    ev.clear();
    Settings all = ParseSettings(Cfg({{"questedRef", "1"}, {"questedRefScan", "1"}, {"scriptedRef", "1"}, {"ghostDamage", "1"}}));
    ClientPatcher c(all, mods, DirectWriter(), sink);
    c.OnFrame();
    CHECK(Has(ev, "nvmp.patches.done", "\"applied\":6,\"failed\":0"));
    CHECK(img[0xCCAF6] == 0xEB && img[0xCCB19] == 0xEB && img[0xCD09E] == 0xEB && img[0xCDFDF] == 0x80 && img[0x3E30D] == 0x80 && img[0x3E31B] == 0x80);

    // autoApply=false: nothing happens until the RPC asks
    img = pristine;
    ev.clear();
    Settings manual = ParseSettings(Cfg({{"autoApply", "0"}}));
    ClientPatcher m(manual, mods, DirectWriter(), sink);
    for (int i = 0; i < 100; ++i) m.OnFrame();
    CHECK(img == pristine && ev.empty());
}

// An NV:MP update: signature no longer matches => report it and write NOTHING.
static void TestUpdatedBuildDetected() {
    if (!g_have) return;
    std::vector<uint8_t> img = g_image;
    // break the signature of the recommended patch (a fixed byte inside the quest-string push sequence)
    ScanResult r = ScanImage(img.data(), img.size(), FindPatch("client.quested_not_localized")->signature);
    CHECK(r.ok());
    img[r.offset + 20] ^= 0x5A;   // inside "6A 03 FF 35" region
    const std::vector<uint8_t> before = img;
    FakeMods mods;
    mods.present = true;
    mods.view = {img.data(), img.size()};
    std::vector<Event> ev;
    ClientPatcher p(ParseSettings(Cfg({})), mods, DirectWriter(), [&](const char* t, const std::string& j) { ev.push_back({t, j}); });
    p.OnFrame();
    CHECK(img == before);
    CHECK(Has(ev, "nvmp.patch", "signature-not-found") && Has(ev, "nvmp.patches.done", "\"applied\":0,\"failed\":1") && Has(ev, "nvmp.patch", "\"ok\":false"));

    // signature intact but the bytes at the site are something else: refuse
    img = g_image;
    img[0xCCAF6] = 0x75;
    const std::vector<uint8_t> before2 = img;
    ev.clear();
    mods.view = {img.data(), img.size()};
    ClientPatcher q(ParseSettings(Cfg({})), mods, DirectWriter(), [&](const char* t, const std::string& j) { ev.push_back({t, j}); });
    q.OnFrame();
    CHECK(img == before2 && Has(ev, "nvmp.patch", "expected-bytes-mismatch"));
}

// The nvcoop module: registered, config from env, RPC reachable, dormant without client.dll (no NV:MP in this process).
static void TestModuleRegistration() {
    bool found = false;
    const nvcoop::Module* mod = nullptr;
    for (auto& m : nvcoop::registry::Modules())
        if (std::string(m.name) == "nvmp_client_patches") { found = true; mod = &m; }
    CHECK(found);
    if (!found) return;
    SetEnvironmentVariableA("NVCOOP_NVMP_PATCHES", "quested,questedRef");
    mod->onLoad(nullptr);
    nvcoop::registry::MethodEntry me;
    CHECK(nvcoop::registry::FindMethod("nvmp.patches", me) && me.mainThread);
    for (int i = 0; i < 5; ++i) mod->onFrame();
    std::string out;
    CHECK(me.fn("", out) && out.find("\"phase\":\"waiting\"") != std::string::npos && out.find("\"module\":null") != std::string::npos);
    CHECK(out.find("client.quested_ref_not_localized\",\"enabled\":true") != std::string::npos);
    CHECK(!me.fn("{\"apply\":\"bogus\"}", out));
    CHECK(!me.fn("{\"apply\":\"quested\"}", out) && out.find("module-not-found") != std::string::npos);   // no client.dll in this process
    CHECK(!me.fn("{bad", out));
    nvcoop::registry::MethodEntry pe;
    CHECK(nvcoop::registry::FindMethod("nvmp.players", pe) && pe.mainThread);
    CHECK(!pe.fn("", out) && out.find("not loaded") != std::string::npos);   // no NV:MP in this process
    CHECK(nvmp::GhostRefOfPlayer(1) == 0 && nvmp::GhostRefOfPlayer(0) == 0 && nvmp::NetIdByName("x") == 0);   // L10 API: unknown => 0
    nvmp::BindSlotToNetId(1, 11);
    nvmp::BindSlotToNetId(0, 5);   // slot 0 can't be bound
    nvcoop::registry::MethodEntry be;
    std::string bo;
    CHECK(nvcoop::registry::FindMethod("nvmp.bindslot", be) && be.fn("{\"slot\":2,\"netId\":12}", bo) &&
          bo == "[{\"slot\":1,\"netId\":11},{\"slot\":2,\"netId\":12}]");
    CHECK(be.fn("{\"slot\":1,\"netId\":null}", bo) && bo == "[{\"slot\":2,\"netId\":12}]");
    CHECK(!be.fn("{\"slot\":0,\"netId\":1}", bo));
    CHECK(be.fn("{\"clear\":true}", bo) && bo == "[]");
}

int main() {
    LoadReal();
    struct { const char* n; void (*f)(); } tests[] = {
        {"DLL name match (loader-lock callback)", TestDllNameMatch},
        {"Settings", TestSettings},
        {"Waiting / polling / notification", TestWaiting},
        {"Real 9.4 client.dll image (mutation twin)", TestRealImage},
        {"Updated build detected, nothing written (mutation twin)", TestUpdatedBuildDetected},
        {"nvcoop module registration + RPC", TestModuleRegistration},
    };
    for (auto& t : tests) {
        int before = g_fail;
        std::printf("[ RUN ] %s\n", t.n);
        t.f();
        std::printf("[%s] %s\n", g_fail == before ? "  OK " : "FAIL ", t.n);
    }
    std::printf("\n%d checks, %d failed%s\n", g_run, g_fail, g_have ? "" : " (real client.dll NOT found)");
    return g_fail ? 1 : 0;
}
