# plugin/world_time: game time, weather, wait / sleep / fast-travel consent, save policy and the native world-object poll
# (FULL-SYNC-PLAN 3.6 and 3.9). Works two ways:
#   * included by plugin/CMakeLists.txt: adds nvcoop_wt_core (and, when the coop module and the world lane are built, the glue
#     wt_glue.cpp that registers the module; everything stays OFF until nvcoop.ini [worldtime] enabled=1)
#   * standalone, for the game-free tests + mutation twins:  plugin\world_time\run_tests.cmd (uses the shared tree build\all)
cmake_minimum_required(VERSION 3.20)
if(CMAKE_SOURCE_DIR STREQUAL CMAKE_CURRENT_LIST_DIR)
  project(nvcoop_world_time CXX)
  set(CMAKE_CXX_STANDARD 20)
  set(CMAKE_CXX_STANDARD_REQUIRED ON)
  set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded")
endif()

set(WT_DIR "${CMAKE_CURRENT_LIST_DIR}")
set(WT_REPO "${WT_DIR}/../..")
set(WT_MUT_INC "${WT_REPO}/tools/mut")

function(wt_flags tgt)
  if(MSVC)
    target_compile_options(${tgt} PRIVATE /W4 /EHsc /permissive- /utf-8 /wd4456 /wd4100 /wd4127 /wd4245 /wd4005)
  endif()
  target_compile_definitions(${tgt} PRIVATE _CRT_SECURE_NO_WARNINGS WIN32_LEAN_AND_MEAN NOMINMAX)
endfunction()

# game-free core: production (what the plugin links) and the runtime-twin copy (what the tests link). Header-only use of synccore
# (effect.h / types.h), so nothing from synccore is linked.
set(WT_SRC ${WT_DIR}/wt_clock.cpp ${WT_DIR}/wt_wire.cpp ${WT_DIR}/wt_broker.cpp ${WT_DIR}/wt_objects.cpp ${WT_DIR}/wt_save.cpp ${WT_DIR}/wt_session.cpp)
foreach(sfx "" "_rt")
  add_library(nvcoop_wt_core${sfx} STATIC ${WT_SRC})
  target_include_directories(nvcoop_wt_core${sfx} PUBLIC ${WT_DIR} ${WT_REPO}/synccore/include ${WT_REPO}/synccore ${WT_MUT_INC})
  wt_flags(nvcoop_wt_core${sfx})
  if(sfx STREQUAL "_rt")
    target_compile_definitions(nvcoop_wt_core${sfx} PUBLIC FNVCOOP_RUNTIME_MUT)
  endif()
endforeach()

# ---- tests. One exe; twins 101..199 are chosen at run time with FNVCOOP_MUT=<n> ----
set(WT_TEST_EXCL "")
if(TARGET nvcoop)
  set(WT_TEST_EXCL EXCLUDE_FROM_ALL)   # plugin build: skip tests unless asked for
endif()
add_executable(nvcoop_wt_tests ${WT_TEST_EXCL} ${WT_DIR}/tests/test_worldtime.cpp ${WT_REPO}/synccore/tests/main.cpp)
target_include_directories(nvcoop_wt_tests PRIVATE ${WT_REPO}/synccore/tests ${WT_DIR}/tests)
target_link_libraries(nvcoop_wt_tests PRIVATE nvcoop_wt_core_rt)
wt_flags(nvcoop_wt_tests)
if(NOT CMAKE_RUNTIME_OUTPUT_DIRECTORY)
  set_target_properties(nvcoop_wt_tests PROPERTIES RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/wt_tests")
endif()
foreach(cfg "" _DEBUG _RELEASE _RELWITHDEBINFO _MINSIZEREL)
  set_target_properties(nvcoop_wt_tests PROPERTIES RUNTIME_OUTPUT_DIRECTORY${cfg} "${CMAKE_BINARY_DIR}/wt_tests")
endforeach()

enable_testing()
add_test(NAME worldtime_unit COMMAND nvcoop_wt_tests)

# ---- plugin glue (x86, linked into nvcoop.dll next to the coop module). Added by the glue step below once wt_glue.cpp exists. ----
if(MSVC AND CMAKE_SIZEOF_VOID_P EQUAL 4 AND TARGET nvcoop AND TARGET nvcoop_coop_core AND EXISTS "${WT_DIR}/wt_glue.cpp")
  target_link_libraries(nvcoop PRIVATE nvcoop_wt_core)
  target_compile_definitions(nvcoop PRIVATE NVCOOP_WITH_WORLDTIME=1)
  target_sources(nvcoop PRIVATE ${WT_DIR}/wt_glue.cpp)
endif()
