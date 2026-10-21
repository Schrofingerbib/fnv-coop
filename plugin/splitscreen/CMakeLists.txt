# Track B split-screen pieces. Works two ways:
#   * included by plugin/CMakeLists.txt (the shared tree build/all): builds nvsplit_focus, linked into the permanent DLL
#     (monolithic nvcoop.dll or the split-mode loader), never into the reloadable core, and the test exe (EXCLUDE_FROM_ALL)
#   * standalone, for the game-free tests + mutation twins
# plugin\splitscreen\run_tests.cmd builds the target nvsplit_focus_tests through tools\build\build.py (build lock, MSVC env).
cmake_minimum_required(VERSION 3.20)
if(CMAKE_SOURCE_DIR STREQUAL CMAKE_CURRENT_LIST_DIR)
  project(nvcoop_splitscreen CXX)
  set(CMAKE_CXX_STANDARD 20)
  set(CMAKE_CXX_STANDARD_REQUIRED ON)
  set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded")
endif()

set(NVSPLIT_DIR "${CMAKE_CURRENT_LIST_DIR}")
set(NVSPLIT_MUT_INC "${NVSPLIT_DIR}/../../tools/mut")

function(nvsplit_flags tgt)
  if(MSVC)
    target_compile_options(${tgt} PRIVATE /W4 /EHsc /permissive- /utf-8)
  endif()
  target_compile_definitions(${tgt} PRIVATE _CRT_SECURE_NO_WARNINGS)
endfunction()

# The production library: FNV_MUT(n) is constant false here, nothing reads the environment.
add_library(nvsplit_focus STATIC ${NVSPLIT_DIR}/focus_guard.cpp)
target_include_directories(nvsplit_focus PUBLIC ${NVSPLIT_DIR} PRIVATE ${NVSPLIT_MUT_INC})
nvsplit_flags(nvsplit_focus)

# Test exe. Mutation twins 1..7 are runtime-switchable (FNVCOOP_MUT=<n>, tools/mut/fnvcoop_mut.h, list at the top of focus_guard.cpp):
# ONE exe, compiled once. FOCUS_GUARD_TESTING gives the test-only hooks in focus_guard.h; the nvsplit_focus library never defines it.
set(NVSPLIT_EXCL "")
if(TARGET nvcoop)
  set(NVSPLIT_EXCL EXCLUDE_FROM_ALL)
endif()
add_executable(nvsplit_focus_tests ${NVSPLIT_EXCL} ${NVSPLIT_DIR}/tests/test_focus_guard.cpp ${NVSPLIT_DIR}/focus_guard.cpp)
target_include_directories(nvsplit_focus_tests PRIVATE ${NVSPLIT_DIR} ${NVSPLIT_MUT_INC})
target_compile_definitions(nvsplit_focus_tests PRIVATE FNVCOOP_RUNTIME_MUT FOCUS_GUARD_TESTING=1)
target_link_libraries(nvsplit_focus_tests PRIVATE user32)
nvsplit_flags(nvsplit_focus_tests)
foreach(cfg "" _DEBUG _RELEASE _RELWITHDEBINFO _MINSIZEREL)
  set_target_properties(nvsplit_focus_tests PROPERTIES RUNTIME_OUTPUT_DIRECTORY${cfg} "${CMAKE_BINARY_DIR}/splitscreen_tests")
endforeach()

# ---- split-screen libraries (config, pad routing, import-table patching, focus faking, audio policy) ----
# Game-free. nvsplit_core is the production library (FNV_MUT(n) is constant false); nvsplit_tests compiles the same sources ONCE with
# FNVCOOP_RUNTIME_MUT and is run once per twin (FNVCOOP_MUT=8..34, numbered after the focus guard twins 1..7) by tools/splitscreen/run_tests.py.
set(NVSPLIT_CORE_SRC ${NVSPLIT_DIR}/split_config.cpp ${NVSPLIT_DIR}/pad_router.cpp ${NVSPLIT_DIR}/iat_patch.cpp
    ${NVSPLIT_DIR}/focus_fake.cpp ${NVSPLIT_DIR}/audio_policy.cpp)
add_library(nvsplit_core STATIC ${NVSPLIT_CORE_SRC})
target_include_directories(nvsplit_core PUBLIC ${NVSPLIT_DIR} PRIVATE ${NVSPLIT_MUT_INC})
nvsplit_flags(nvsplit_core)

add_executable(nvsplit_tests ${NVSPLIT_EXCL} ${NVSPLIT_DIR}/tests/test_splitscreen.cpp ${NVSPLIT_CORE_SRC})
target_include_directories(nvsplit_tests PRIVATE ${NVSPLIT_DIR} ${NVSPLIT_MUT_INC})
target_compile_definitions(nvsplit_tests PRIVATE FNVCOOP_RUNTIME_MUT)
nvsplit_flags(nvsplit_tests)
foreach(cfg "" _DEBUG _RELEASE _RELWITHDEBINFO _MINSIZEREL)
  set_target_properties(nvsplit_tests PROPERTIES RUNTIME_OUTPUT_DIRECTORY${cfg} "${CMAKE_BINARY_DIR}/splitscreen_tests")
endforeach()

# ---- game glue (x86 only): the nvcoop module that wires the libraries to Win32 (IAT patches, Core Audio mute, split.status) ----
# Inert unless nvcoop.ini has [splitscreen] enable=1. nvsplit_glue_check compiles the same file without the rest of the plugin
# (EXCLUDE_FROM_ALL), so the glue can be checked without building every other lane.
if(MSVC AND CMAKE_SIZEOF_VOID_P EQUAL 4)
  if(TARGET nvcoop)
    target_sources(nvcoop PRIVATE ${NVSPLIT_DIR}/splitscreen_module.cpp)
    target_include_directories(nvcoop PRIVATE ${NVSPLIT_DIR} ${NVSPLIT_DIR}/..)
    target_link_libraries(nvcoop PRIVATE nvsplit_core ole32)
    add_library(nvsplit_glue_check OBJECT EXCLUDE_FROM_ALL ${NVSPLIT_DIR}/splitscreen_module.cpp)
    target_include_directories(nvsplit_glue_check PRIVATE ${NVSPLIT_DIR} ${NVSPLIT_DIR}/..)
    nvsplit_flags(nvsplit_glue_check)
  endif()
endif()
