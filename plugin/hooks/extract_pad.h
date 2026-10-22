// Padded calls of the engine's ExtractArgs (0x5ACCB0, which xNVSE replaces with ExtractArgsEx). Game-free; used by the engine glue
// (engine_hooks.cpp, hooks_ui/ui_hooks.cpp) and unit-tested in tests/test_hooks.cpp.
//
// Why: ExtractArgs is variadic and takes one out pointer (va_arg) for every parameter PRESENT in the compiled script line. A hook that
// re-parses a command with fewer out pointers than the line has parameters (AddItem <item> <n> 1 has a third, "hide message" flag)
// makes ExtractArgs read the next stack word as a pointer and write through it: an int, a float or a whole string parameter lands at a
// random address, on our stack or the engine's (issue #33 class of crash). extractPadded always hands over kExtractSlots pointers: the
// caller's outs first, then spare buffers big enough for any parameter, so extra parameters land in scratch memory.
#pragma once
#include <cstddef>
#include <cstdint>
#include <initializer_list>

#ifdef NVHOOKS_CHAIN_TWINS
#include <cstdlib>
#endif

namespace nvhooks {

constexpr int kExtractSlots = 12;            // more than any FNV script command has parameters
constexpr size_t kExtractSpareBytes = 0x400;  // a String parameter is copied whole (engine script strings are < 0x200 bytes)

// Scratch for the parameters nobody asked for. Shared: what lands here is never read, so nested or concurrent calls may overwrite it.
inline char* extractSpare(int slot) {
    static char spare[kExtractSlots][kExtractSpareBytes];
    return spare[slot];
}

// Twin 4 of the hook twins (NVHOOKS_CHAIN_MUT=4, test build only): no padding, the slots after the caller's outs are left null.
inline bool extractPadTwin() {
#ifdef NVHOOKS_CHAIN_TWINS
    const char* e = std::getenv("NVHOOKS_CHAIN_MUT");
    return e && std::atoi(e) == 4;
#else
    return false;
#endif
}

// fn: the ExtractArgs pointer (cdecl, variadic). outs: the caller's out pointers in parameter order (at most kExtractSlots).
template <class Fn, class Off>
bool extractPadded(Fn fn, void* pi, void* sd, Off* off, void* thisObj, void* contObj, void* script, void* ev, std::initializer_list<void*> outs) {
    if (outs.size() > static_cast<size_t>(kExtractSlots)) return false;
    void* p[kExtractSlots];
    int i = 0;
    for (void* o : outs) p[i++] = o;
    for (; i < kExtractSlots; ++i) p[i] = extractPadTwin() ? nullptr : extractSpare(i);
    return fn(pi, sd, off, thisObj, contObj, script, ev, p[0], p[1], p[2], p[3], p[4], p[5], p[6], p[7], p[8], p[9], p[10], p[11]);
}

}  // namespace nvhooks
