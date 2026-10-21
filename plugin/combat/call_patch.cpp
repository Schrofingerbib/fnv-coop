#include "combat/call_patch.h"
#include <cstring>
#include "../../tools/mut/fnvcoop_mut.h"

// Mutation twins 761..770: each FNV_MUT(n) breaks one rule of the header; tests/test_call_patch.cpp must catch it.

namespace nvcoop::combat {

const char* toString(SiteStatus s) {
    switch (s) {
        case SiteStatus::Patched: return "patched";
        case SiteStatus::AlreadyPatched: return "already ours";
        case SiteStatus::WrongBytes: return "bytes do not match";
        case SiteStatus::ReadFailed: return "read failed";
        case SiteStatus::WriteFailed: return "write failed";
        default: return "refused";
    }
}

CallSitePatches::~CallSitePatches() { uninstall(); }

int CallSitePatches::install(const CallSite* sites, size_t n, uint32_t target, const uint8_t* prologue, size_t prologueLen, uint32_t thunk) {
    results_.clear();
    lastError_.clear();
    thunk_ = thunk;
    prologueOk_ = false;
    if (!done_.empty()) uninstall();
    uint8_t got[32] = {};
    if (prologueLen == 0 || prologueLen > sizeof got) { lastError_ = "bad prologue length"; return 0; }
    if (!mem_.read(target, got, prologueLen)) { lastError_ = "cannot read the target function"; }
    else if (std::memcmp(got, prologue, prologueLen) != 0 && !FNV_MUT(761)) { lastError_ = "the target function does not start with the pinned bytes (other exe build?)"; }
    else prologueOk_ = true;
    for (size_t i = 0; i < n; ++i) {
        SiteResult r; r.name = sites[i].name; r.site = sites[i].site;
        if (!prologueOk_) { r.status = SiteStatus::Refused; results_.push_back(r); continue; }
        uint8_t b[5] = {};
        if (!mem_.read(sites[i].site, b, 5)) { r.status = SiteStatus::ReadFailed; results_.push_back(r); continue; }
        uint8_t want[5];
        want[0] = 0xE8;
        const uint32_t rel = relTo(sites[i].site, target);
        std::memcpy(want + 1, &rel, 4);
        uint8_t mine[5];
        mine[0] = 0xE8;
        const uint32_t myRel = relTo(sites[i].site, thunk);
        std::memcpy(mine + 1, &myRel, 4);
        if (std::memcmp(b, mine, 5) == 0) {   // our own patch (a second install without uninstall): nothing to do
            r.status = SiteStatus::AlreadyPatched;
            results_.push_back(r);
            continue;
        }
        if (std::memcmp(b, want, 5) != 0 && !FNV_MUT(762)) { r.status = SiteStatus::WrongBytes; results_.push_back(r); continue; }
        if (!mem_.write(sites[i].site + 1, &myRel, 4)) { r.status = SiteStatus::WriteFailed; results_.push_back(r); continue; }
        Done d; d.site = sites[i].site; std::memcpy(d.original, b, 5); d.rel = myRel;
        done_.push_back(d);
        r.status = SiteStatus::Patched;
        results_.push_back(r);
    }
    return static_cast<int>(done_.size());
}

int CallSitePatches::uninstall() {
    int restored = 0;
    for (const Done& d : done_) {
        uint8_t b[5] = {};
        uint32_t cur = 0;
        const bool ok = mem_.read(d.site, b, 5);
        std::memcpy(&cur, b + 1, 4);
        const bool ours = ok && b[0] == 0xE8 && cur == d.rel;
        if (!ours && !FNV_MUT(763)) { ++tampered_; continue; }   // somebody chained over our call: leave their code alone
        if (mem_.write(d.site + 1, d.original + 1, 4)) ++restored;
    }
    done_.clear();
    return restored;
}

int CallSitePatches::audit() {
    int bad = 0;
    for (const Done& d : done_) {
        uint8_t b[5] = {};
        uint32_t cur = 0;
        if (!mem_.read(d.site, b, 5)) { ++bad; continue; }
        std::memcpy(&cur, b + 1, 4);
        if (b[0] != 0xE8 || cur != d.rel) ++bad;
    }
    return bad;
}

}  // namespace nvcoop::combat
