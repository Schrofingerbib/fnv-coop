// Tests of the runtime mutation-twin mechanism itself (tools/mut/fnvcoop_mut.h). Built once with FNVCOOP_RUNTIME_MUT; run once for
// real and once per FNVCOOP_MUT=<n>. Each twin flips one function of the tiny "code under test" below, so every twin MUST make this
// program report failures: that proves the switch really reaches the code and that run_twins.py really detects a failing binary.
//
// Also built a second time WITHOUT FNVCOOP_RUNTIME_MUT (target fnvcoop_mut_prod_tests): there FNV_MUT(n) must be a compile-time false
// even when FNVCOOP_MUT is set in the environment, i.e. nothing in a production build can be switched by an environment variable.
#include <cstdio>
#include <cstdlib>
#include <cstring>

#include "fnvcoop_mut.h"

namespace {
int g_checks = 0, g_failed = 0;
#define CHECK(c)                                                                    \
    do {                                                                            \
        ++g_checks;                                                                 \
        if (!(c)) {                                                                 \
            ++g_failed;                                                             \
            std::printf("[FAIL] %s:%d  %s\n", __FILE__, __LINE__, #c);              \
        }                                                                           \
    } while (0)

// "code under test": three tiny functions, each with its own twin
int add(int a, int b) {
    if (FNV_MUT(1)) return a - b;  // twin 1: wrong operator
    return a + b;
}
bool isEven(int x) {
    if (!FNV_MUT(2)) return (x % 2) == 0;  // twin 2: the check is removed
    return true;
}
const char* name(bool flag) {
    return FNV_MUT(3) ? "off" : (flag ? "on" : "off");  // twin 3: always "off"
}
}  // namespace

int main(int argc, char** argv) {
#ifdef FNVCOOP_RUNTIME_MUT
    fnvcoop_mut::initFromArgs(argc, argv);  // also accepts "--mut N"
    std::printf("mut self-test (runtime twins), active twin = %d\n", fnvcoop_mut::current());
    // the mechanism itself: set() overrides, 0 restores the real behaviour
    const int saved = fnvcoop_mut::current();
    fnvcoop_mut::set(0);
    CHECK(!FNV_MUT(1) && !FNV_MUT(2) && !FNV_MUT_ANY());
    fnvcoop_mut::set(2);
    CHECK(FNV_MUT(2) && !FNV_MUT(1) && FNV_MUT_ANY());
    fnvcoop_mut::set(saved);
#else
    (void)argc;
    (void)argv;
    std::printf("mut self-test (production build: twins compiled out)\n");
    // FNV_MUT is a constant false here whatever FNVCOOP_MUT says (the harness sets FNVCOOP_MUT=1 for this binary on purpose)
    static_assert(!FNV_MUT(1) && !FNV_MUT(2) && !FNV_MUT(3), "production FNV_MUT must be constant false");
    CHECK(!FNV_MUT_ANY());
#endif
    CHECK(add(2, 3) == 5);
    CHECK(isEven(4) && !isEven(7));
    CHECK(std::strcmp(name(true), "on") == 0);
    CHECK(std::strcmp(name(false), "off") == 0);
    std::printf("%d checks, %d failed\n", g_checks, g_failed);
    return g_failed == 0 ? 0 : 1;
}
