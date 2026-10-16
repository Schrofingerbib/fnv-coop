// Unit tests for sigscan + patches. No framework: exit code 0 = all pass.
// Built twice by run_tests.cmd: normally (must pass) and as a "mutant" (-DSIGSCAN_MUTANT_ACCEPT_AMBIGUOUS
// and -DPATCHES_MUTANT_SKIP_VERIFY; must FAIL, proving the tests catch the removed safety checks).
// Optional: set NVMP_CLIENT_DLL / NVMP_SERVER_EXE (or rely on the default third_party/nvmp paths) to also
// verify every table signature against the real NV:MP 9.4 binaries.
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iterator>
#include <string>
#include <vector>

#include "../patches.h"
#include "../sigscan.h"

using namespace nvmp;

static int g_fail = 0, g_run = 0;
#define CHECK(c)                                                                     \
    do {                                                                             \
        ++g_run;                                                                     \
        if (!(c)) { ++g_fail; std::printf("  FAIL %s:%d  %s\n", __FILE__, __LINE__, #c); } \
    } while (0)
#define CHECK_EQ_STATUS(got, want)                                                              \
    do {                                                                                        \
        ++g_run;                                                                                \
        if ((got) != (want)) {                                                                  \
            ++g_fail;                                                                           \
            std::printf("  FAIL %s:%d  got %s want %s\n", __FILE__, __LINE__, ToString(got), ToString(want)); \
        }                                                                                       \
    } while (0)

static std::vector<uint8_t> Bytes(const char* hex) {
    Pattern p;
    ParsePattern(hex, p);
    return p.bytes;
}

// --- minimal PE32 builder -------------------------------------------------------------------
struct SynSection { const char* name; uint32_t rva, size, chars; std::vector<uint8_t> data; };
static void W16(std::vector<uint8_t>& v, size_t o, uint16_t x) { std::memcpy(&v[o], &x, 2); }
static void W32(std::vector<uint8_t>& v, size_t o, uint32_t x) { std::memcpy(&v[o], &x, 4); }
static std::vector<uint8_t> BuildImage(std::vector<SynSection> secs, uint32_t soi = 0x4000) {
    std::vector<uint8_t> img(soi, 0xCC);
    std::fill(img.begin(), img.begin() + 0x400, 0);
    W16(img, 0, 0x5A4D);
    W32(img, 0x3C, 0x80);
    W32(img, 0x80, 0x4550);
    W16(img, 0x84, 0x14C);
    W16(img, 0x86, (uint16_t)secs.size());
    W16(img, 0x94, 224);
    W16(img, 0x98, 0x10B);
    W32(img, 0x98 + 28, 0x10000000);
    W32(img, 0x98 + 56, soi);
    size_t off = 0x80 + 24 + 224;
    for (auto& s : secs) {
        std::memcpy(&img[off], s.name, std::strlen(s.name));
        W32(img, off + 8, s.size);
        W32(img, off + 12, s.rva);
        W32(img, off + 16, s.size);
        W32(img, off + 36, s.chars);
        std::memcpy(&img[s.rva], s.data.data(), s.data.size());
        off += 40;
    }
    return img;
}
static const uint32_t kCode = 0x60000020, kData = 0x40000040;

// --- tests ----------------------------------------------------------------------------------
static void TestParse() {
    Pattern p;
    std::string err;
    CHECK(ParsePattern("8B 4D ?? 0F 85", p, &err));
    CHECK(p.size() == 5 && p.mask[2] == 0 && p.mask[0] == 0xFF && p.bytes[4] == 0x85);
    CHECK(ParsePattern("8b ? 0f", p));
    CHECK(!ParsePattern("", p, &err));
    CHECK(!ParsePattern("8B GG", p, &err));
    CHECK(!ParsePattern("8B 4", p, &err));
    CHECK(!ParsePattern("?? 8B", p, &err));   // leading wildcard
    CHECK(!ParsePattern("8B ??", p, &err));   // trailing wildcard
    CHECK(!ParsePattern("   ", p, &err));
}

static void TestBuffer() {
    std::vector<uint8_t> b = Bytes("90 90 8B 4D 10 0F 85 AA BB 90 90");
    ScanResult r = ScanBuffer(b.data(), b.size(), "8B 4D ?? 0F 85");
    CHECK(r.ok() && r.offset == 2 && r.matches == 1);
    r = ScanBuffer(b.data(), b.size(), "8B 4D ?? 0F 84");
    CHECK(r.status == ScanStatus::NotFound);
    r = ScanBuffer(b.data(), b.size(), "8B 4D ?? ?? ?? AA BB");
    CHECK(r.ok() && r.offset == 2);
    r = ScanBuffer(b.data(), b.size(), "zz");
    CHECK(r.status == ScanStatus::BadPattern);
    // pattern longer than buffer
    r = ScanBuffer(b.data(), 3, "8B 4D ?? 0F 85");
    CHECK(r.status == ScanStatus::NotFound);
    // match at the very end of the buffer
    std::vector<uint8_t> e = Bytes("01 02 03 AA BB");
    r = ScanBuffer(e.data(), e.size(), "AA BB");
    CHECK(r.ok() && r.offset == 3);
}

// Mutation twin target: an ambiguous pattern MUST be rejected, never "first match wins".
static void TestAmbiguousRejected() {
    std::vector<uint8_t> b = Bytes("11 22 33 44 00 11 22 33 55 00 11 22 33 66");
    ScanResult r = ScanBuffer(b.data(), b.size(), "11 22 33 ?? 00");
    CHECK(r.status == ScanStatus::Ambiguous);
    CHECK(!r.ok());
    r = ScanBuffer(b.data(), b.size(), "11 22 33 55");  // narrowed by a fixed byte -> unique
    CHECK(r.ok() && r.offset == 5);
    // overlapping matches are still ambiguous
    std::vector<uint8_t> o = Bytes("AA AA AA");
    r = ScanBuffer(o.data(), o.size(), "AA AA");
    CHECK(r.status == ScanStatus::Ambiguous);
}

static void TestImage() {
    std::vector<uint8_t> code(0x100, 0x90);
    std::vector<uint8_t> hit = Bytes("DE AD BE EF 01");
    std::memcpy(&code[0x40], hit.data(), hit.size());
    std::vector<uint8_t> dataSec(0x100, 0);
    std::memcpy(&dataSec[0x10], hit.data(), hit.size());  // same bytes in a NON-exec section: ignored
    auto img = BuildImage({{".text", 0x1000, 0x1000, kCode, code}, {".rdata", 0x2000, 0x1000, kData, dataSec}});
    std::vector<SectionInfo> secs;
    CHECK(ParseSections(img.data(), img.size(), secs));
    CHECK(secs.size() == 2 && secs[0].name == ".text" && secs[0].executable() && !secs[1].executable());
    ScanResult r = ScanImage(img.data(), img.size(), "DE AD BE EF 01");
    CHECK(r.ok() && r.offset == 0x1040);

    // same bytes in two executable sections -> ambiguous across sections
    auto img2 = BuildImage({{".text", 0x1000, 0x1000, kCode, code}, {".text2", 0x2000, 0x1000, kCode, dataSec}});
    r = ScanImage(img2.data(), img2.size(), "DE AD BE EF 01");
    CHECK(r.status == ScanStatus::Ambiguous);

    // not a PE
    std::vector<uint8_t> junk(0x400, 0x41);
    r = ScanImage(junk.data(), junk.size(), "41 41");
    CHECK(!r.ok());
    // file -> image expansion keeps section contents at their RVAs
    std::vector<uint8_t> file(0x600, 0);
    W16(file, 0, 0x5A4D); W32(file, 0x3C, 0x80); W32(file, 0x80, 0x4550); W16(file, 0x86, 1); W16(file, 0x94, 224);
    W16(file, 0x98, 0x10B); W32(file, 0x98 + 56, 0x3000);
    size_t so = 0x80 + 24 + 224;
    std::memcpy(&file[so], ".text", 5); W32(file, so + 8, 0x1000); W32(file, so + 12, 0x1000);
    W32(file, so + 16, 0x200); W32(file, so + 20, 0x400); W32(file, so + 36, kCode);
    std::memcpy(&file[0x400 + 0x20], hit.data(), hit.size());
    std::vector<uint8_t> mapped;
    CHECK(MapPeFileToImage(file, mapped));
    r = ScanImage(mapped.data(), mapped.size(), "DE AD BE EF 01");
    CHECK(r.ok() && r.offset == 0x1020);
}

// Build a synthetic site from a table entry: signature with wildcards filled, `expected` at the patch offset.
static std::vector<uint8_t> SiteFor(const PatchDef& d) {
    Pattern p;
    ParsePattern(d.signature, p);
    std::vector<uint8_t> s = p.bytes;
    for (size_t i = 0; i < s.size(); ++i)
        if (!p.mask[i]) s[i] = 0x11;
    for (size_t i = 0; i < d.expected.size(); ++i) s[d.offset + i] = d.expected[i];
    return s;
}
static std::vector<uint8_t> ImageWithSites(const std::vector<std::vector<uint8_t>>& sites) {
    std::vector<uint8_t> code(0x1000, 0xCC);
    size_t at = 0x40;
    for (auto& s : sites) { std::memcpy(&code[at], s.data(), s.size()); at += 0x80 + s.size(); }
    return BuildImage({{".text", 0x1000, 0x1000, kCode, code}});
}
static PatchDef Resolved(const PatchDef& d) {
    static const char kText[] = "TEST";
    return d.name == "client.overlay_text" ? MakeOverlayTextPatch(kText) : d;
}

static void TestTableDefinitions() {
    for (const auto& d0 : PatchTable()) {
        PatchDef d = Resolved(d0);
        std::string v = ValidateDef(d);
        if (!v.empty()) std::printf("  %s: %s\n", d.name.c_str(), v.c_str());
        CHECK(v.empty());
    }
    PatchDef bad = *FindPatch("client.quested_not_localized");
    bad.offset = 0;  // would rewrite a fixed byte of the signature
    CHECK(!ValidateDef(bad).empty());
    bad = *FindPatch("client.quested_not_localized");
    bad.replacement = {0xEB, 0x00};
    CHECK(!ValidateDef(bad).empty());
    bad = *FindPatch("client.quested_not_localized");
    bad.replacement = bad.expected;
    CHECK(!ValidateDef(bad).empty());
    CHECK(!ValidateDef(*FindPatch("client.overlay_text")).empty());  // dynamic: replacement unset
}

static void TestApplyRevert() {
    for (const auto& d0 : PatchTable()) {
        PatchDef d = Resolved(d0);
        auto img = ImageWithSites({SiteFor(d)});
        auto orig = img;
        uint32_t rva = 0;
        CHECK_EQ_STATUS(CheckPatch(d, img.data(), img.size(), &rva), PatchStatus::Applied);
        CHECK(img == orig);  // dry run writes nothing
        PatchState st;
        CHECK_EQ_STATUS(ApplyPatch(d, img.data(), img.size(), DirectWriter(), st), PatchStatus::Applied);
        CHECK(std::memcmp(&img[rva], d.replacement.data(), d.replacement.size()) == 0);
        size_t diff = 0;
        for (size_t i = 0; i < img.size(); ++i) diff += img[i] != orig[i];
        CHECK(diff > 0 && diff <= d.replacement.size());  // only the patched bytes changed
        // idempotent: signature still matches after patching
        PatchState st2;
        CHECK_EQ_STATUS(ApplyPatch(d, img.data(), img.size(), DirectWriter(), st2), PatchStatus::AlreadyApplied);
        CHECK_EQ_STATUS(RevertPatch(st, DirectWriter()), PatchStatus::Reverted);
        CHECK(img == orig);
        CHECK_EQ_STATUS(RevertPatch(st, DirectWriter()), PatchStatus::NotApplied);
    }
}

// Mutation twin target: site bytes that are neither original nor patched must NOT be overwritten.
static void TestExpectedMismatch() {
    PatchDef d = *FindPatch("client.quested_not_localized");
    auto site = SiteFor(d);
    site[d.offset] = 0x75;  // different opcode than the expected 0x74
    auto img = ImageWithSites({site});
    auto orig = img;
    PatchState st;
    PatchStatus s = ApplyPatch(d, img.data(), img.size(), DirectWriter(), st);
    CHECK_EQ_STATUS(s, PatchStatus::ExpectedMismatch);
    CHECK(img == orig);
}

// Mutation twin target: ambiguous signature (site present twice) -> refuse, write nothing.
static void TestAmbiguousSigRefused() {
    PatchDef d = *FindPatch("client.quested_not_localized");
    auto img = ImageWithSites({SiteFor(d), SiteFor(d)});
    auto orig = img;
    PatchState st;
    PatchStatus s = ApplyPatch(d, img.data(), img.size(), DirectWriter(), st);
    CHECK_EQ_STATUS(s, PatchStatus::SigAmbiguous);
    CHECK(img == orig);
    // absent site
    auto empty = ImageWithSites({});
    CHECK_EQ_STATUS(ApplyPatch(d, empty.data(), empty.size(), DirectWriter(), st), PatchStatus::SigNotFound);
}

static void TestRevertRefusesTamper() {
    PatchDef d = *FindPatch("client.quested_not_localized");
    auto img = ImageWithSites({SiteFor(d)});
    PatchState st;
    CHECK_EQ_STATUS(ApplyPatch(d, img.data(), img.size(), DirectWriter(), st), PatchStatus::Applied);
    img[st.rva] = 0x90;  // someone else changed the byte
    CHECK_EQ_STATUS(RevertPatch(st, DirectWriter()), PatchStatus::RevertMismatch);
    CHECK(img[st.rva] == 0x90);
}

static void TestOverlayBuilder() {
    static const char text[] = "x";
    PatchDef d = MakeOverlayTextPatch(text);
    CHECK(d.replacement.size() == 6 && d.replacement[0] == 0xB8 && d.replacement[5] == 0x90);
    uint32_t a;
    std::memcpy(&a, &d.replacement[1], 4);
    CHECK(a == (uint32_t)(uintptr_t)text || sizeof(void*) > 4);
}

// --- real-binary verification -----------------------------------------------------------------
static bool Slurp(const std::string& path, std::vector<uint8_t>& out) {
    std::ifstream f(path, std::ios::binary);
    if (!f) return false;
    out.assign(std::istreambuf_iterator<char>(f), std::istreambuf_iterator<char>());
    return true;
}
static void TestRealBinaries() {
    const char* c = std::getenv("NVMP_CLIENT_DLL");
    const char* s = std::getenv("NVMP_SERVER_EXE");
    std::string cp = c ? c : "third_party/nvmp/nvmp/client.dll";
    std::string sp = s ? s : "third_party/nvmp/nvmp_storyserver.exe";
    std::vector<uint8_t> cf, sf, ci, si;
    bool haveC = Slurp(cp, cf) && MapPeFileToImage(cf, ci);
    bool haveS = Slurp(sp, sf) && MapPeFileToImage(sf, si);
    if (!haveC && !haveS) { std::printf("  (real binaries not found, skipped)\n"); return; }
    for (const auto& d0 : PatchTable()) {
        PatchDef d = Resolved(d0);
        auto& img = d.module == "client.dll" ? ci : si;
        if (img.empty()) continue;
        uint32_t rva = 0;
        PatchStatus st = CheckPatch(d, img.data(), img.size(), &rva);
        std::printf("  %-36s %-22s rva=0x%X (expect 0x%X)\n", d.name.c_str(), ToString(st), rva, d.rva94);
        CHECK_EQ_STATUS(st, PatchStatus::Applied);
        CHECK(rva == d.rva94);
        // apply on the in-memory copy, then confirm the signature still resolves (idempotency on real data)
        PatchState ps;
        CHECK_EQ_STATUS(ApplyPatch(d, img.data(), img.size(), DirectWriter(), ps), PatchStatus::Applied);
        CHECK_EQ_STATUS(CheckPatch(d, img.data(), img.size(), &rva), PatchStatus::AlreadyApplied);
        CHECK_EQ_STATUS(RevertPatch(ps, DirectWriter()), PatchStatus::Reverted);
        CHECK_EQ_STATUS(CheckPatch(d, img.data(), img.size(), &rva), PatchStatus::Applied);
    }
}

int main() {
    struct { const char* n; void (*f)(); } tests[] = {
        {"ParsePattern", TestParse},
        {"ScanBuffer", TestBuffer},
        {"AmbiguousRejected (mutation twin)", TestAmbiguousRejected},
        {"ScanImage / PE", TestImage},
        {"Table definitions", TestTableDefinitions},
        {"Apply / revert / idempotent", TestApplyRevert},
        {"ExpectedMismatch (mutation twin)", TestExpectedMismatch},
        {"AmbiguousSigRefused (mutation twin)", TestAmbiguousSigRefused},
        {"Revert refuses tamper", TestRevertRefusesTamper},
        {"Overlay builder", TestOverlayBuilder},
        {"Real 9.4 binaries", TestRealBinaries},
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
