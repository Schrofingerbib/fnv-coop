#include "reload_drain.h"

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#include <tlhelp32.h>

#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <mutex>

// NVRELOAD_DRAIN_MUTANT: 7 = no drain at all, 8 = only the instruction pointer is checked (a thread blocked inside a detour is missed).
#ifndef NVRELOAD_DRAIN_MUTANT
#define NVRELOAD_DRAIN_MUTANT 0
#endif

namespace nvreload {

CodeRange moduleRangeOf(const void* p) {
    CodeRange r;
    HMODULE m = nullptr;
    if (!GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT, static_cast<LPCSTR>(p), &m) || !m)
        return r;
    auto* dos = reinterpret_cast<const IMAGE_DOS_HEADER*>(m);
    auto* nt = reinterpret_cast<const IMAGE_NT_HEADERS*>(reinterpret_cast<const char*>(m) + dos->e_lfanew);
    r.lo = reinterpret_cast<uintptr_t>(m);
    r.hi = r.lo + nt->OptionalHeader.SizeOfImage;
    return r;
}

namespace {

typedef LONG(NTAPI* NtQueryInformationThreadFn)(HANDLE, ULONG, PVOID, ULONG, PULONG);
struct ThreadBasicInfo {
    LONG exitStatus;
    PVOID teb;
    PVOID clientIdProcess, clientIdThread;
    ULONG_PTR affinity;
    LONG priority, basePriority;
};

inline bool inRanges(uintptr_t v, const CodeRange* r, size_t n) {
    for (size_t i = 0; i < n; ++i)
        if (v >= r[i].lo && v < r[i].hi) return true;
    return false;
}

#if defined(_M_IX86)
#define NV_IP(c) ((c).Eip)
#define NV_SP(c) ((c).Esp)
#else
#define NV_IP(c) ((c).Rip)
#define NV_SP(c) ((c).Rsp)
#endif

// Is `h` (a thread handle) inside any range? No allocation, no logging: other threads are suspended while this runs.
struct Diag { uintptr_t ip, sp, base; int hit; };
Diag g_diag[64];
int g_diagN = 0;
bool isInside(HANDLE h, NtQueryInformationThreadFn ntq, const CodeRange* r, size_t n) {
    CONTEXT ctx;
    std::memset(&ctx, 0, sizeof ctx);
    ctx.ContextFlags = CONTEXT_CONTROL | CONTEXT_INTEGER;
    if (!GetThreadContext(h, &ctx)) return true;  // cannot prove it is outside: count it as busy
    Diag* dg = g_diagN < 64 ? &g_diag[g_diagN++] : nullptr;
    if (dg) { dg->ip = NV_IP(ctx); dg->sp = NV_SP(ctx); dg->base = 0; dg->hit = 0; }
    if (inRanges(NV_IP(ctx), r, n)) { if (dg) dg->hit = 1; return true; }
    if (NVRELOAD_DRAIN_MUTANT == 8) return false;
    ThreadBasicInfo tbi;
    std::memset(&tbi, 0, sizeof tbi);
    uintptr_t base = 0;
    if (ntq && ntq(h, 0 /*ThreadBasicInformation*/, &tbi, sizeof tbi, nullptr) >= 0 && tbi.teb) {
        __try {
            base = *reinterpret_cast<const uintptr_t*>(static_cast<const char*>(tbi.teb) + sizeof(void*));  // NT_TIB.StackBase
        } __except (EXCEPTION_EXECUTE_HANDLER) {
            base = 0;
        }
    }
    if (dg) dg->base = base;
    uintptr_t sp = NV_SP(ctx) & ~(sizeof(void*) - 1);
    uintptr_t end = base ? base : sp + 65536;
    if (end > sp + (1u << 20)) end = sp + (1u << 20);
    for (uintptr_t a = sp; a + sizeof(void*) <= end; a += sizeof(void*)) {
        uintptr_t v;
        __try {
            v = *reinterpret_cast<const uintptr_t*>(a);
        } __except (EXCEPTION_EXECUTE_HANDLER) {
            break;
        }
        if (inRanges(v, r, n)) { if (dg) dg->hit = 2; return true; }
    }
    return false;
}

}  // namespace

namespace {
std::mutex g_freezeMu;
HANDLE g_frozen[256];
unsigned g_guardCalls = 0;
int g_frozenN = 0;
}  // namespace

void freezeOthers(uintptr_t from, size_t n, intptr_t delta) {
    g_freezeMu.lock();
    ++g_guardCalls;
    g_frozenN = 0;
    static DWORD ids[256];
    int idn = 0;
    const DWORD self = GetCurrentThreadId(), pid = GetCurrentProcessId();
    HANDLE snap = CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0);  // allocates: done before anything is suspended
    if (snap != INVALID_HANDLE_VALUE) {
        THREADENTRY32 te;
        te.dwSize = sizeof te;
        if (Thread32First(snap, &te))
            do {
                if (te.th32OwnerProcessID == pid && te.th32ThreadID != self && idn < 256) ids[idn++] = te.th32ThreadID;
            } while (Thread32Next(snap, &te));
        CloseHandle(snap);
    }
    for (int i = 0; i < idn; ++i) {
        HANDLE h = OpenThread(THREAD_SUSPEND_RESUME | THREAD_GET_CONTEXT | THREAD_SET_CONTEXT, FALSE, ids[i]);
        if (!h) continue;
        if (SuspendThread(h) == static_cast<DWORD>(-1)) { CloseHandle(h); continue; }
        g_frozen[g_frozenN++] = h;
    }
    for (int i = 0; i < g_frozenN; ++i) {
        CONTEXT ctx;
        std::memset(&ctx, 0, sizeof ctx);
        ctx.ContextFlags = CONTEXT_CONTROL;
        if (!GetThreadContext(g_frozen[i], &ctx)) continue;
        uintptr_t ip = NV_IP(ctx);
        if (ip > from && ip < from + n) {
            NV_IP(ctx) = static_cast<DWORD_PTR>(ip + delta);
            SetThreadContext(g_frozen[i], &ctx);
        }
    }
}

unsigned guardCalls() { return g_guardCalls; }

void unfreezeOthers() {
    for (int i = 0; i < g_frozenN; ++i) {
        ResumeThread(g_frozen[i]);
        CloseHandle(g_frozen[i]);
    }
    g_frozenN = 0;
    g_freezeMu.unlock();
}

DrainResult waitDrained(const std::vector<CodeRange>& ranges, const std::vector<unsigned long>& ignore, unsigned timeoutMs) {
    DrainResult res;
    if (NVRELOAD_DRAIN_MUTANT == 7) {
        res.drained = true;
        return res;
    }
    NtQueryInformationThreadFn ntq = nullptr;
    if (HMODULE nt = GetModuleHandleA("ntdll.dll")) ntq = reinterpret_cast<NtQueryInformationThreadFn>(GetProcAddress(nt, "NtQueryInformationThread"));
    const DWORD self = GetCurrentThreadId(), pid = GetCurrentProcessId();
    const ULONGLONG deadline = GetTickCount64() + timeoutMs;
    std::vector<HANDLE> handles;  // reserved before any thread is suspended
    handles.reserve(4096);
    std::vector<unsigned long> ids;
    ids.reserve(4096);
    for (;;) {
        ++res.attempts;
        ids.clear();
        handles.clear();
        HANDLE snap = CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0);
        if (snap != INVALID_HANDLE_VALUE) {
            THREADENTRY32 te;
            te.dwSize = sizeof te;
            if (Thread32First(snap, &te))
                do {
                    if (te.th32OwnerProcessID != pid || te.th32ThreadID == self) continue;
                    if (std::find(ignore.begin(), ignore.end(), te.th32ThreadID) != ignore.end()) continue;
                    if (ids.size() < ids.capacity()) ids.push_back(te.th32ThreadID);
                } while (Thread32Next(snap, &te));
            CloseHandle(snap);
        }
        bool busy = false;
        unsigned long busyTid = 0;
        for (unsigned long id : ids) {
            HANDLE h = OpenThread(THREAD_SUSPEND_RESUME | THREAD_GET_CONTEXT | THREAD_QUERY_INFORMATION, FALSE, id);
            if (h) handles.push_back(h);
            else if (GetLastError() != ERROR_INVALID_PARAMETER) { busy = true; busyTid = id; }  // exists but cannot be inspected
        }
        // ---- from here until the resume loop: no allocation, no logging ----
        g_diagN = 0;
        size_t suspended = 0;
        for (HANDLE h : handles) {
            if (SuspendThread(h) == static_cast<DWORD>(-1)) continue;
            ++suspended;
        }
        for (size_t i = 0; i < handles.size(); ++i) {
            if (!busy && isInside(handles[i], ntq, ranges.data(), ranges.size())) {
                busy = true;
                busyTid = GetThreadId(handles[i]);
            }
        }
        for (HANDLE h : handles) ResumeThread(h);
        (void)suspended;
        res.scanned = static_cast<int>(handles.size());
        for (HANDLE h : handles) CloseHandle(h);
        if (std::getenv("NVRELOAD_DRAIN_DEBUG")) {
            char b[256];
            for (int i = 0; i < g_diagN; ++i) {
                std::snprintf(b, sizeof b, "[drain] ip=%p sp=%p base=%p hit=%d range0=%p-%p\n", (void*)g_diag[i].ip, (void*)g_diag[i].sp, (void*)g_diag[i].base, g_diag[i].hit, (void*)(ranges.empty() ? 0 : ranges[0].lo), (void*)(ranges.empty() ? 0 : ranges[0].hi));
                OutputDebugStringA(b);
                std::fputs(b, stderr);
            }
        }
        if (!busy) {
            res.drained = true;
            res.busyThread = 0;
            return res;
        }
        res.busyThread = busyTid;
        if (GetTickCount64() >= deadline) {
            res.why = "thread " + std::to_string(busyTid) + " is still running inside the core (instruction pointer or a return address on its stack)";
            return res;
        }
        Sleep(1);
    }
}

}  // namespace nvreload
