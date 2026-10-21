# plugin/areadigest: per-area desync digests in the plugin (FULL-SYNC-PLAN section 4). Works two ways:
#   * included by plugin/CMakeLists.txt: adds nvcoop_areadigest_core (and, when the coop module is built, the glue + module that go into nvcoop.dll)
#   * standalone, for the game-free tests + mutation twins:  plugin\areadigest\run_tests.cmd (uses the shared tree build\all)
# Targets
#   nvcoop_areadigest_proto / _rt   synccore/areadigest compiled here (production / FNVCOOP_RUNTIME_MUT), because the plugin tree is configured
#                                   before synccore/ and cannot link synccore's own fnvcoop_areadigest by name
#   nvcoop_areadigest_core / _rt    config, actor source, shadow sources, runtime, service (production / runtime twins 61..82)
#   nvcoop_areadigest_tests         EXCLUDE_FROM_ALL; twins are switched with FNVCOOP_MUT=<n> (tools/mut/run_twins.py)
cmake_minimum_required(VERSION 3.20)
if(CMAKE_SOURCE_DIR STREQUAL CMAKE_CURRENT_LIST_DIR)
  project(nvcoop_areadigest C CXX)
  set(CMAKE_CXX_STANDARD 20)
  set(CMAKE_CXX_STANDARD_REQUIRED ON)
  set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded")
endif()

set(NVA_DIR "${CMAKE_CURRENT_LIST_DIR}")
set(NVA_REPO "${NVA_DIR}/../..")
set(NVA_MUT_INC "${NVA_REPO}/tools/mut")

function(nva_flags tgt)
  if(MSVC)
    target_compile_options(${tgt} PRIVATE /W4 /EHsc /permissive- /utf-8 /wd4456 /wd4100 /wd4127 /wd4245 /wd4005)
  endif()
  target_compile_definitions(${tgt} PRIVATE _CRT_SECURE_NO_WARNINGS WIN32_LEAN_AND_MEAN NOMINMAX)
endfunction()

# ---- synccore (wire, effects, shadow, flight recorder) reused from the coop lane's lib inside the plugin build ----
if(TARGET nvcoop_coop_synccore)
  set(NVA_SYNC nvcoop_coop_synccore)
else()
  file(GLOB NVA_SYNC_SRC CONFIGURE_DEPENDS ${NVA_REPO}/synccore/src/*.cpp)
  list(FILTER NVA_SYNC_SRC EXCLUDE REGEX "enet_transport\\.cpp$")
  add_library(nvcoop_areadigest_synccore STATIC ${NVA_SYNC_SRC} ${NVA_REPO}/synccore/flight/flight.cpp)
  target_include_directories(nvcoop_areadigest_synccore PUBLIC ${NVA_REPO}/synccore/include ${NVA_REPO}/synccore)
  nva_flags(nvcoop_areadigest_synccore)
  set(NVA_SYNC nvcoop_areadigest_synccore)
endif()

set(NVA_INC "${NVA_DIR}/.." "${NVA_REPO}/synccore" "${NVA_REPO}/synccore/include" "${NVA_MUT_INC}")

foreach(sfx "" "_rt")
  add_library(nvcoop_areadigest_proto${sfx} STATIC ${NVA_REPO}/synccore/areadigest/areadigest.cpp ${NVA_REPO}/synccore/areadigest/ad_wire.cpp)
  target_include_directories(nvcoop_areadigest_proto${sfx} PUBLIC ${NVA_INC})
  target_link_libraries(nvcoop_areadigest_proto${sfx} PUBLIC ${NVA_SYNC})
  nva_flags(nvcoop_areadigest_proto${sfx})
  if(sfx STREQUAL "_rt")
    target_compile_definitions(nvcoop_areadigest_proto${sfx} PUBLIC FNVCOOP_RUNTIME_MUT)
  endif()
endforeach()

# ---- the module core: production (what the plugin links) and the runtime-twin copy (what the tests link) ----
set(NVA_SRC ${NVA_DIR}/area_config.cpp ${NVA_DIR}/actor_source.cpp ${NVA_DIR}/shadow_source.cpp ${NVA_DIR}/area_runtime.cpp ${NVA_DIR}/area_service.cpp)
foreach(sfx "" "_rt")
  add_library(nvcoop_areadigest_core${sfx} STATIC ${NVA_SRC})
  target_include_directories(nvcoop_areadigest_core${sfx} PUBLIC ${NVA_INC})
  target_link_libraries(nvcoop_areadigest_core${sfx} PUBLIC nvcoop_areadigest_proto${sfx})
  nva_flags(nvcoop_areadigest_core${sfx})
  if(sfx STREQUAL "_rt")
    target_compile_definitions(nvcoop_areadigest_core${sfx} PUBLIC FNVCOOP_RUNTIME_MUT)
  endif()
endforeach()

# ---- tests. One exe: synccore/areadigest twins 1..42 live in the other suite; twins 61..82 here are chosen at run time with FNVCOOP_MUT=<n> ----
set(NVA_TEST_EXCL "")
if(TARGET nvcoop)
  set(NVA_TEST_EXCL EXCLUDE_FROM_ALL)   # plugin build: skip tests unless asked for
endif()
add_executable(nvcoop_areadigest_tests ${NVA_TEST_EXCL} ${NVA_DIR}/tests/test_plugin.cpp ${NVA_DIR}/tests/test_service.cpp ${NVA_REPO}/synccore/tests/main.cpp)
target_include_directories(nvcoop_areadigest_tests PRIVATE ${NVA_REPO}/synccore/tests ${NVA_DIR}/tests ${NVA_REPO}/synccore/areadigest/tests)
target_link_libraries(nvcoop_areadigest_tests PRIVATE nvcoop_areadigest_core_rt)
nva_flags(nvcoop_areadigest_tests)
if(NOT CMAKE_RUNTIME_OUTPUT_DIRECTORY)
  set_target_properties(nvcoop_areadigest_tests PROPERTIES RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/areadigest_plugin_tests")
endif()
foreach(cfg "" _DEBUG _RELEASE _RELWITHDEBINFO _MINSIZEREL)
  set_target_properties(nvcoop_areadigest_tests PROPERTIES RUNTIME_OUTPUT_DIRECTORY${cfg} "${CMAKE_BINARY_DIR}/areadigest_plugin_tests")
endforeach()

enable_testing()
add_test(NAME areadigest_plugin_unit COMMAND nvcoop_areadigest_tests)
foreach(v 61 62 63 64 65 66 67 68 70 72 73 74 76 77 78 79 80 81)
  add_test(NAME areadigest_plugin_mut${v} COMMAND nvcoop_areadigest_tests)
  set_tests_properties(areadigest_plugin_mut${v} PROPERTIES WILL_FAIL TRUE ENVIRONMENT "FNVCOOP_MUT=${v}")
endforeach()

# ---- plugin glue: areadigest.status + the per-frame hook (x86, linked into nvcoop.dll next to the coop module) ----
if(TARGET nvcoop AND TARGET nvcoop_coop_core)
  target_link_libraries(nvcoop PRIVATE nvcoop_areadigest_core)
  target_compile_definitions(nvcoop PRIVATE NVCOOP_WITH_AREADIGEST=1)
  target_include_directories(nvcoop PRIVATE ${NVA_INC} ${NVA_REPO}/plugin/coop)
  target_sources(nvcoop PRIVATE ${NVA_DIR}/area_glue.cpp ${NVA_DIR}/areadigest_module.cpp)   # NVCOOP_MODULE must be a direct source
  if(TARGET nvcoop_actor_engine_core AND MSVC AND CMAKE_SIZEOF_VOID_P EQUAL 4)
    target_sources(nvcoop PRIVATE ${NVA_DIR}/engine_facade.cpp)
    target_link_libraries(nvcoop PRIVATE nvcoop_actor_engine_core)
    target_compile_definitions(nvcoop PRIVATE NVCOOP_AREADIGEST_FACADE=1)
  endif()
endif()
