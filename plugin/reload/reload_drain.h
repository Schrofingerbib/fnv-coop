// Reload drain (T3b). After the hooks are suspended (original bytes back, so no thread can enter a detour any more), wait until
// no other thread is still executing, or has a frame on its stack in, the code we are about to unload (the core image and the
// hook trampolines). Windows, in-process. A thread still inside would crash the game the moment the DLL is unmapped.
#pragma once
#include <cstdint>
#include <string>
#include <vector>

namespace nvreload {

struct CodeRange {
    uintptr_t lo = 0, hi = 0;  // [lo, hi)
    const char* what = "";     // static label used in the log ("core image", "hook trampoline")
};

struct DrainResult {
    bool drained = false;
    int attempts = 0;
    int scanned = 0;               // threads inspected in the last attempt
    unsigned long busyThread = 0;  // last thread found inside (0 if drained)
    unsigned elapsedMs = 0;        // wall time spent waiting
    unsigned timeoutMs = 0;        // the limit that was asked for
    std::string why;               // empty when drained; otherwise a complete sentence for the log
    std::string where;             // what made the last busy thread count: "instruction pointer 0x... in <label> ..." or "return address ..."
    // One JSON object with the fields above (strings escaped). The core stores it for `reload.state` and hands it to the next core.
    std::string toJson() const;
};

// Waits up to timeoutMs. `ignore` = thread ids that are not counted (the caller itself is always ignored; ManagedThreads are
// passed in by the glue because their owners join them). A thread counts as inside when its instruction pointer is in a range or
// any aligned word of its live stack (ESP up to the stack base, at most 1 MB) points into a range (a return address).
// Conservative: a stale word can delay the drain, never make it succeed wrongly. A thread that cannot be opened, suspended or
// read, and a process with more than 4096 threads, also count as busy (never as drained). timeoutMs 0 = one attempt; a positive
// timeout always gets at least two attempts, even if the first scan alone took longer than the timeout.
// While threads are suspended this does no allocation and no logging.
DrainResult waitDrained(const std::vector<CodeRange>& ranges, const std::vector<unsigned long>& ignore, unsigned timeoutMs);

// Freeze every other thread of the process (at most 256) so a code patch can be written without any thread executing torn bytes.
// A frozen thread whose instruction pointer is inside [from+1, from+n) is moved by `delta` (the same offset in the trampoline copy).
// Between freezeOthers and unfreezeOthers do nothing but the memory write: no allocation, no locks, no logging.
void freezeOthers(uintptr_t from, size_t n, intptr_t delta);
void unfreezeOthers();
// How many times freezeOthers ran in this DLL (the host test checks that hook writes really go through the guard).
unsigned guardCalls();

// The core image containing `anyAddressInCore` as a range.
CodeRange moduleRangeOf(const void* anyAddressInCore);

}  // namespace nvreload
