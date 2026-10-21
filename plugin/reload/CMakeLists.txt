# T3b reload state contract. Works two ways:
#   * included by plugin/CMakeLists.txt (adds the registry + glue to the core target, nvcoop or nvcoop_mod)
#   * standalone, game-free tests (x86, because the host test patches code with the real hook patcher):
#       plugin\reload\run_tests.cmd        (prints ALL OK)
cmake_minimum_required(VERSION 3.20)
set(RELOAD_DIR "${CMAKE_CURRENT_LIST_DIR}")
set(RELOAD_PLUGIN_DIR "${CMAKE_CURRENT_LIST_DIR}/..")

if(NOT CMAKE_SOURCE_DIR STREQUAL CMAKE_CURRENT_LIST_DIR)
  set(_core nvcoop)
  if(TARGET nvcoop_mod)
    set(_core nvcoop_mod)
  endif()
  if(TARGET ${_core})
    target_sources(${_core} PRIVATE ${RELOAD_DIR}/reload_state.cpp ${RELOAD_DIR}/reload_drain.cpp ${RELOAD_DIR}/reload_glue.cpp)
    target_include_directories(${_core} PRIVATE ${RELOAD_DIR} ${RELOAD_DIR}/../../tools/mut)
  endif()
endif()

if(CMAKE_SOURCE_DIR STREQUAL CMAKE_CURRENT_LIST_DIR)
  project(nvcoop_reload_tests CXX)
  set(CMAKE_CXX_STANDARD 20)
  set(CMAKE_CXX_STANDARD_REQUIRED ON)
  set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded")
endif()
# test binaries land in out/T3b whether this is built standalone or inside the shared tree (plugin/CMakeLists.txt)
set(RELOAD_OUT "${RELOAD_DIR}/../../out/T3b" CACHE PATH "Where the reload test binaries land")
set(RELOAD_MUT_INC "${RELOAD_DIR}/../../tools/mut")
function(reload_outdir tgt)
  foreach(cfg "" _DEBUG _RELEASE _RELWITHDEBINFO _MINSIZEREL)
    set_target_properties(${tgt} PROPERTIES RUNTIME_OUTPUT_DIRECTORY${cfg} "${RELOAD_OUT}" LIBRARY_OUTPUT_DIRECTORY${cfg} "${RELOAD_OUT}")
  endforeach()
endfunction()

function(reload_flags tgt)
  if(MSVC)
    target_compile_options(${tgt} PRIVATE /W4 /EHsc /permissive- /utf-8 /wd4100 /wd4127)
  endif()
  target_compile_definitions(${tgt} PRIVATE _CRT_SECURE_NO_WARNINGS WIN32_LEAN_AND_MEAN)
endfunction()

# ---- Mutation twins are RUNTIME-switchable (tools/mut/fnvcoop_mut.h): FNVCOOP_MUT=<n> picks the twin, nothing is rebuilt per twin. ----
# registry unit tests: real + twins 1..8
add_executable(nvreload_unit ${RELOAD_DIR}/tests/test_reload_state.cpp ${RELOAD_DIR}/reload_state.cpp)
reload_flags(nvreload_unit)
target_include_directories(nvreload_unit PRIVATE ${RELOAD_MUT_INC})
target_compile_definitions(nvreload_unit PRIVATE FNVCOOP_RUNTIME_MUT)
reload_outdir(nvreload_unit)

# drain unit tests: real + twins 7,8,10..14 (see the list at the top of reload_drain.cpp)
add_executable(nvreload_drain ${RELOAD_DIR}/tests/test_reload_drain.cpp ${RELOAD_DIR}/reload_drain.cpp)
reload_flags(nvreload_drain)
target_include_directories(nvreload_drain PRIVATE ${RELOAD_MUT_INC})
target_compile_definitions(nvreload_drain PRIVATE FNVCOOP_RUNTIME_MUT)
reload_outdir(nvreload_drain)

# ---- host-process test (x86 only) ----
if(CMAKE_SIZEOF_VOID_P EQUAL 4)
  add_library(nvr_api STATIC ${RELOAD_PLUGIN_DIR}/common/nvcoop_api.cpp)
  target_include_directories(nvr_api PUBLIC ${RELOAD_PLUGIN_DIR} ${RELOAD_PLUGIN_DIR}/common)
  target_compile_definitions(nvr_api PUBLIC _CRT_SECURE_NO_WARNINGS)

  add_executable(nvreload_host_test ${RELOAD_DIR}/tests/test_reload_host.cpp ${RELOAD_PLUGIN_DIR}/loader/loader_host.cpp)
  target_include_directories(nvreload_host_test PRIVATE ${RELOAD_PLUGIN_DIR} ${RELOAD_PLUGIN_DIR}/loader)
  target_link_libraries(nvreload_host_test PRIVATE nvr_api ws2_32)
  # loader_host.cpp is compiled with the runtime twins 25 (noconfig) and 26 (configonce); the test core's twins are 1..14
  target_include_directories(nvreload_host_test PRIVATE ${RELOAD_MUT_INC})
  target_compile_definitions(nvreload_host_test PRIVATE FNVCOOP_RUNTIME_MUT)
  reload_flags(nvreload_host_test)
  reload_outdir(nvreload_host_test)

  # ONE test core; FNVCOOP_MUT=1..6 (testcore.cpp), 7/8 + 10..14 (reload_drain.cpp), 9 (reload_glue.cpp) select the broken variant.
  # Runtime twins are enabled per source: reload_state.cpp stays production (its own twins 1..8 belong to nvreload_unit), exactly as
  # the old per-twin builds only defined TESTCORE_MUTANT / NVRELOAD_DRAIN_MUTANT / NVRELOAD_NO_WRITE_GUARD.
  add_library(nvtestcore SHARED
    ${RELOAD_DIR}/tests/testcore.cpp ${RELOAD_DIR}/reload_state.cpp ${RELOAD_DIR}/reload_drain.cpp ${RELOAD_DIR}/reload_glue.cpp
    ${RELOAD_PLUGIN_DIR}/hooks/hook_patch.cpp ${RELOAD_PLUGIN_DIR}/loader/core_thunks.cpp)
  target_include_directories(nvtestcore PRIVATE ${RELOAD_PLUGIN_DIR} ${RELOAD_PLUGIN_DIR}/loader ${RELOAD_DIR} ${RELOAD_MUT_INC})
  target_link_libraries(nvtestcore PRIVATE ws2_32)
  reload_flags(nvtestcore)
  set_source_files_properties(${RELOAD_DIR}/tests/testcore.cpp ${RELOAD_DIR}/reload_drain.cpp ${RELOAD_DIR}/reload_glue.cpp
    PROPERTIES COMPILE_DEFINITIONS FNVCOOP_RUNTIME_MUT)
  reload_outdir(nvtestcore)
endif()
