#include "vats/vats_patch.h"

#include <cstring>

#include "vats/vats_addrs.h"
#include "../../tools/mut/fnvcoop_mut.h"

// Mutation twins 901..910: each FNV_MUT(n) breaks one rule of the header; tests/test_patch.cpp must catch it.

namespace nvcoop::vats {

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

SitePatcher::~SitePatcher() { uninstall(); }

int SitePatcher::install(const SiteRef* sites, size_t n, uint32_t target, const uint8_t* prologue, size_t prologueLen, uint32_t thunk) {
    results_.clear();
    lastError_.clear();
    prologueOk_ = false;
    if (!done_.empty()) uninstall();
    uint8_t got[32] = {};
    if (prologueLen == 0 || prologueLen > sizeof got) lastError_ = "bad prologue length";
    else if (!mem_.read(target, got, prologueLen)) lastError_ = "cannot read the target function";
    else if (std::memcmp(got, prologue, prologueLen) != 0 && !FNV_MUT(901)) lastError_ = "the target function does not start with the pinned bytes (other exe build?)";
    else prologueOk_ = true;   // 901: a function with other bytes is patched anyway
    for (size_t i = 0; i < n; ++i) {
        SiteResult r;
        r.name = sites[i].name; r.site = sites[i].site;
        if (!prologueOk_) { r.status = SiteStatus::Refused; results_.push_back(r); continue; }
        uint8_t b[5] = {};
        if (!mem_.read(sites[i].site, b, 5)) { r.status = SiteStatus::ReadFailed; results_.push_back(r); continue; }
        uint8_t want[5];
        want[0] = 0xE8;
        const uint32_t rel = addr::relTo(sites[i].site, target);
        std::memcpy(want + 1, &rel, 4);
        const uint32_t myRel = addr::relTo(sites[i].site, thunk);
        uint8_t mine[5];
        mine[0] = 0xE8;
        std::memcpy(mine + 1, &myRel, 4);
        if (std::memcmp(b, mine, 5) == 0) { r.status = SiteStatus::AlreadyPatched; results_.push_back(r); continue; }
        if (std::memcmp(b, want, 5) != 0 && !FNV_MUT(902)) { r.status = SiteStatus::WrongBytes; results_.push_back(r); continue; }   // 902: any site bytes are overwritten
        if (!mem_.write(sites[i].site + 1, &myRel, 4)) { r.status = SiteStatus::WriteFailed; results_.push_back(r); continue; }
        Done d;
        d.site = sites[i].site; std::memcpy(d.original, b, 5); d.rel = myRel;
        done_.push_back(d);
        r.status = SiteStatus::Patched;
        results_.push_back(r);
    }
    return static_cast<int>(done_.size());
}

int SitePatcher::uninstall() {
    int restored = 0;
    for (const Done& d : done_) {
        uint8_t b[5] = {};
        uint32_t cur = 0;
        const bool ok = mem_.read(d.site, b, 5);
        std::memcpy(&cur, b + 1, 4);
        const bool ours = ok && b[0] == 0xE8 && cur == d.rel;
        if (!ours && !FNV_MUT(903)) { ++tampered_; continue; }   // 903: somebody's chained call is overwritten on restore
        if (FNV_MUT(904)) continue;                              // 904: nothing is restored (the redirect outlives the module)
        if (mem_.write(d.site + 1, d.original + 1, 4)) ++restored;
    }
    done_.clear();
    return restored;
}

int SitePatcher::audit() {
    int bad = 0;
    for (const Done& d : done_) {
        uint8_t b[5] = {};
        uint32_t cur = 0;
        const bool ok = mem_.read(d.site, b, 5);
        std::memcpy(&cur, b + 1, 4);
        if (!(ok && b[0] == 0xE8 && cur == d.rel)) ++bad;
    }
    return bad;
}

}  // namespace nvcoop::vats
