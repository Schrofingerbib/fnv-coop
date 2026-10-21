// Runtime-switchable mutation twins (docs/TOOLS-BRIEF.md, "Build speed").
//
// Old way: every twin was its own compile (-DNVWORLD_MUT=28 ...), so a suite with 42 twins compiled 42 times.
// New way: the code under test is compiled ONCE with FNVCOOP_RUNTIME_MUT defined and a twin is picked when the
// test binary starts:
//     set FNVCOOP_MUT=28 && nvcoop_world_tests.exe      (environment variable)
//     nvcoop_world_tests.exe --mut 28                   (CLI flag, if the test main calls fnvcoop_mut::initFromArgs)
// The sources say  if (FNV_MUT(28)) { ...broken variant... }  or  if (!FNV_MUT(4)) { ...code the twin removes... }.
//
// Production builds (no FNVCOOP_RUNTIME_MUT) get  FNV_MUT(n) == false : the broken variants are dead code that the
// optimiser deletes, and nothing in the shipped DLL reads the environment. Only the *_rt test libraries define
// FNVCOOP_RUNTIME_MUT (see tools/mut/mut.cmake).
//
// Twin numbers are per suite: one binary runs one suite, so the numbers never need to be globally unique.
#pragma once

#ifdef FNVCOOP_RUNTIME_MUT
#include <cstdlib>
#include <cstring>

namespace fnvcoop_mut {

inline int& slot() {
    static int v = [] {
        const char* e = std::getenv("FNVCOOP_MUT");
        return (e && *e) ? std::atoi(e) : 0;
    }();
    return v;
}
inline int current() { return slot(); }
inline void set(int n) { slot() = n; }

// Optional: let a test main accept `--mut N` (the arguments are left in place; the pair is blanked out so a
// positional-argument parser does not see it). Returns the active twin.
inline int initFromArgs(int argc, char** argv) {
    for (int i = 1; i + 1 < argc; ++i) {
        if (std::strcmp(argv[i], "--mut") == 0) {
            set(std::atoi(argv[i + 1]));
            argv[i][0] = '\0';
            argv[i + 1][0] = '\0';
            break;
        }
    }
    return current();
}

}  // namespace fnvcoop_mut

#define FNV_MUT(n) (::fnvcoop_mut::current() == (n))
#define FNV_MUT_ANY() (::fnvcoop_mut::current() != 0)
#else
#define FNV_MUT(n) (false)
#define FNV_MUT_ANY() (false)
#endif
