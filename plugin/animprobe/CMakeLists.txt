# T7 animprobe. Works two ways:
#   * included by plugin/CMakeLists.txt: compiles the probe (core + engine glue) straight into nvcoop.dll
#   * standalone, for the game-free unit tests + mutation twins (any arch):  plugin\animprobe\run_tests.cmd
cmake_minimum_required(VERSION 3.20)
if(CMAKE_SOURCE_DIR STREQUAL CMAKE_CURRENT_LIST_DIR)
  project(nvcoop_animprobe CXX)
  set(CMAKE_CXX_STANDARD 20)
  set(CMAKE_CXX_STANDARD_REQUIRED ON)
  set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded")
endif()

set(NVAP_DIR "${CMAKE_CURRENT_LIST_DIR}")

# ---- game-free core + tests. Mutation twins: ANIMPROBE_MUT=<n>, each its own exe, each MUST fail ----
set(NVAP_MUTANTS 1 2 3 4 5 6)
foreach(v "" ${NVAP_MUTANTS})
  if(v STREQUAL "")
    set(sfx "")
  else()
    set(sfx "_mut${v}")
  endif()
  add_executable(animprobe_tests${sfx} ${NVAP_DIR}/tests/test_animprobe.cpp ${NVAP_DIR}/animprobe_core.cpp)
  target_include_directories(animprobe_tests${sfx} PRIVATE ${NVAP_DIR} ${NVAP_DIR}/..)
  if(MSVC)
    target_compile_options(animprobe_tests${sfx} PRIVATE /W4 /EHsc /permissive- /utf-8 /wd4100)
  endif()
  target_compile_definitions(animprobe_tests${sfx} PRIVATE ANIMPROBE_GOLDEN="${NVAP_DIR}/tests/golden_sample.json")
  if(NOT v STREQUAL "")
    target_compile_definitions(animprobe_tests${sfx} PRIVATE ANIMPROBE_MUT=${v})
  endif()
  foreach(cfg "" _DEBUG _RELEASE _RELWITHDEBINFO _MINSIZEREL)
    set_target_properties(animprobe_tests${sfx} PROPERTIES RUNTIME_OUTPUT_DIRECTORY${cfg} "${CMAKE_BINARY_DIR}/animprobe_tests")
  endforeach()
endforeach()

enable_testing()
add_test(NAME animprobe_unit COMMAND animprobe_tests)
foreach(v ${NVAP_MUTANTS})
  add_test(NAME animprobe_mut${v} COMMAND animprobe_tests_mut${v})
  set_tests_properties(animprobe_mut${v} PROPERTIES WILL_FAIL TRUE)
endforeach()

# ---- engine glue (x86 only), compiled into nvcoop.dll against the xNVSE headers. Needs the xNVSE game-API sources
# that plugin/snapshot builds (LookupFormByID), so it is skipped when NVCOOP_LINK_SNAPSHOT is off. ----
if(MSVC AND CMAKE_SIZEOF_VOID_P EQUAL 4 AND TARGET nvcoop AND TARGET fnvcoop_nvse_gameapi)
  target_sources(nvcoop PRIVATE ${NVAP_DIR}/animprobe_core.cpp ${NVAP_DIR}/animprobe_game.cpp)
endif()
