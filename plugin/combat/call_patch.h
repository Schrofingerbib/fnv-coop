// Call-site patches: redirect `call rel32` instructions that call ONE engine function to our own thunk, with every byte checked first.
//
// Why not the entry-detour of plugin/hooks (nvhooks::HookRegistry)? The engine's hit funnel (ApplyHitData, 0x89A760) starts with an aligned-frame
// prologue (53 8B DC 83 EC 08 83 E4 F0 ...), not the standard `55 8B EC` that HookRegistry insists on. All six places that call it are plain
// `E8 rel32` calls, so redirecting those is just as complete (docs/re/hit-path.md) and needs no trampoline: our thunk calls the real function itself.
//
// Rules (a violation refuses, never writes):
//   1. the target function's first bytes must equal the pinned prologue (wrong exe build => refuse everything, zero bytes written);
//   2. a site is patched only if it holds exactly `E8` + the rel32 that reaches `target` (anything else: already patched by another mod, other build);
//   3. uninstall restores a site only if it still holds OUR rel32; a site somebody chained over is left alone and counted (tampered()).
// Game-free: memory goes through nvhooks::MemoryOps, so this is unit-tested on a fake buffer.
#pragma once
#include <cstdint>
#include <string>
#include <vector>
#include "hook_patch.h"

namespace nvcoop::combat {

struct CallSite {
    const char* name;
    uint32_t site;        // address of the E8 byte
};

enum class SiteStatus : uint8_t { Patched, AlreadyPatched, WrongBytes, ReadFailed, WriteFailed, Refused };
const char* toString(SiteStatus s);

struct SiteResult { const char* name = ""; uint32_t site = 0; SiteStatus status = SiteStatus::Refused; };

class CallSitePatches {
public:
    explicit CallSitePatches(nvhooks::MemoryOps& mem) : mem_(mem) {}
    ~CallSitePatches();
    CallSitePatches(const CallSitePatches&) = delete;
    CallSitePatches& operator=(const CallSitePatches&) = delete;

    // `prologue`/`prologueLen`: bytes the target function must start with. Returns how many sites were patched (or already ours).
    int install(const CallSite* sites, size_t n, uint32_t target, const uint8_t* prologue, size_t prologueLen, uint32_t thunk);
    // Restore every site that is still ours. Returns the number restored. Sites chained over by someone else stay as they are (tampered()).
    int uninstall();
    int installed() const { return static_cast<int>(done_.size()); }
    int tampered() const { return tampered_; }
    bool prologueOk() const { return prologueOk_; }
    const std::vector<SiteResult>& results() const { return results_; }
    std::string lastError() const { return lastError_; }
    // Reload audit: how many installed sites no longer hold our rel32 (someone patched over us).
    int audit();

private:
    struct Done { uint32_t site; uint8_t original[5]; uint32_t rel; };
    nvhooks::MemoryOps& mem_;
    std::vector<Done> done_;
    std::vector<SiteResult> results_;
    uint32_t thunk_ = 0;
    int tampered_ = 0;
    bool prologueOk_ = false;
    std::string lastError_;
};

// rel32 of a call at `site` (5 bytes long) that reaches `to`.
inline uint32_t relTo(uint32_t site, uint32_t to) { return to - (site + 5u); }

}  // namespace nvcoop::combat
