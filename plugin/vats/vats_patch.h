// Call-site patches for plugin/vats: redirect `call rel32` instructions that call ONE engine function to our own thunk, with every byte checked first.
// The same rules as plugin/combat/call_patch.h (kept as its own small copy so the two lanes do not depend on each other's build targets):
//   1. the target function's first bytes must equal the pinned prologue (wrong exe build => refuse everything, zero bytes written);
//   2. a site is patched only if it holds exactly `E8` + the rel32 that reaches `target` (anything else: already patched by another mod, other build);
//   3. uninstall restores a site only if it still holds OUR rel32; a site somebody chained over is left alone and counted (tampered()).
// Game-free: memory goes through nvhooks::MemoryOps (plugin/hooks/hook_patch.h), so this is unit-tested on a fake image.
#pragma once
#include <cstdint>
#include <string>
#include <vector>
#include "hook_patch.h"
#include "vats/vats_addrs.h"

namespace nvcoop::vats {

enum class SiteStatus : uint8_t { Patched, AlreadyPatched, WrongBytes, ReadFailed, WriteFailed, Refused };
const char* toString(SiteStatus s);

struct SiteResult { const char* name = ""; uint32_t site = 0; SiteStatus status = SiteStatus::Refused; };
using SiteRef = addr::Site;   // {name, address of the E8 byte}

class SitePatcher {
public:
    explicit SitePatcher(nvhooks::MemoryOps& mem) : mem_(mem) {}
    ~SitePatcher();
    SitePatcher(const SitePatcher&) = delete;
    SitePatcher& operator=(const SitePatcher&) = delete;

    // Returns how many sites were patched (or are already ours).
    int install(const SiteRef* sites, size_t n, uint32_t target, const uint8_t* prologue, size_t prologueLen, uint32_t thunk);
    // Restores every site that is still ours; returns the number restored. Sites chained over by someone else stay as they are (tampered()).
    int uninstall();
    int installed() const { return static_cast<int>(done_.size()); }
    int tampered() const { return tampered_; }
    bool prologueOk() const { return prologueOk_; }
    const std::vector<SiteResult>& results() const { return results_; }
    const std::string& lastError() const { return lastError_; }
    // How many installed sites no longer hold our rel32 (someone patched over us): the hot-reload audit.
    int audit();

private:
    struct Done { uint32_t site; uint8_t original[5]; uint32_t rel; };
    nvhooks::MemoryOps& mem_;
    std::vector<Done> done_;
    std::vector<SiteResult> results_;
    int tampered_ = 0;
    bool prologueOk_ = false;
    std::string lastError_;
};

}  // namespace nvcoop::vats
