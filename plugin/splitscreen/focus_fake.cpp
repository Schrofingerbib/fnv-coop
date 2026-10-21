#include "focus_fake.h"

#include "fnvcoop_mut.h"

// Runtime mutation twins (FNVCOOP_MUT=<n>), numbered after iat_patch.cpp (21..26):
//   27 = a non-NULL system answer is replaced too (a dialog window of the engine would be hidden)
//   28 = the focusFake switch is ignored
//   29 = the lookup is not tied to the calling thread (the wrong copy's window could be returned)
namespace nvsplit {

uintptr_t ResolveActiveWindow(bool enabled, uintptr_t real, uint32_t callerThread, IWindowQuery& q) {
    if (!enabled && !FNV_MUT(28)) return real;
    if (real != 0 && !FNV_MUT(27)) return real;
    const uintptr_t w = q.topWindowOfThread(FNV_MUT(29) ? 0u : callerThread);
    return w ? w : real;
}

}  // namespace nvsplit
