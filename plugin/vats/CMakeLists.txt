# plugin/vats: real-time VATS for every player (FULL-SYNC-PLAN 3.4, owner requirement). Works two ways:
#   * included by plugin/CMakeLists.txt (shared tree build/all): adds the game-free core, the tests, and (x86, nvcoop target present) the engine glue
#   * standalone, for the game-free tests + mutation twins:  plugin\vats\run_tests.cmd
# Targets
#   nvcoop_vats_core / _rt   the game-free logic (policy, party slow-mo, perk ledger, limb API, wire, sync, runtime, call-site patcher);
#                            _rt is compiled with FNVCOOP_RUNTIME_MUT (runtime twins 801..910, chosen with FNVCOOP_MUT=<n>)
#   nvcoop_vats_tests        EXCLUDE_FROM_ALL; twins are switched with FNVCOOP_MUT=<n> (tools/mut/run_twins.py)
cmake_minimum_required(VERSION 3.20)
if(CMAKE_SOURCE_DIR STREQUAL CMAKE_CURRENT_LIST_DIR)
  project(nvcoop_vats CXX)
  set(CMAKE_CXX_STANDARD 20)
  set(CMAKE_CXX_STANDARD_REQUIRED ON)
  set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded")
endif()

set(NVV_DIR "${CMAKE_CURRENT_LIST_DIR}")
set(NVV_REPO "${NVV_DIR}/../..")
set(NVV_INC "${NVV_DIR}/.." "${NVV_REPO}/synccore/include" "${NVV_REPO}/synccore" "${NVV_REPO}/tools/mut" "${NVV_REPO}/plugin/hooks")

function(nvv_flags tgt)
  if(MSVC)
    target_compile_options(${tgt} PRIVATE /W4 /EHsc /permissive- /utf-8 /wd4456 /wd4100 /wd4127 /wd4245 /wd4005 /wd4244)
  endif()
  target_compile_definitions(${tgt} PRIVATE _CRT_SECURE_NO_WARNINGS NOMINMAX)
endfunction()

# ---- synccore base (wire Archive, transport): reuse the coop lane's lib inside the plugin build ----
if(TARGET nvcoop_coop_synccore)
  set(NVV_SYNC nvcoop_coop_synccore)
elseif(TARGET nvcoop_actors_synccore)
  set(NVV_SYNC nvcoop_actors_synccore)
elseif(TARGET nvcoop_combat_synccore)
  set(NVV_SYNC nvcoop_combat_synccore)
else()
  file(GLOB NVV_SYNC_SRC CONFIGURE_DEPENDS ${NVV_REPO}/synccore/src/*.cpp)
  list(FILTER NVV_SYNC_SRC EXCLUDE REGEX "enet_transport\\.cpp$")
  add_library(nvcoop_vats_synccore STATIC ${NVV_SYNC_SRC})
  target_include_directories(nvcoop_vats_synccore PUBLIC ${NVV_REPO}/synccore/include ${NVV_REPO}/synccore)
  nvv_flags(nvcoop_vats_synccore)
  set(NVV_SYNC nvcoop_vats_synccore)
endif()

# ---- the module core: production (what the plugin links) and the runtime-twin copy (what the tests link) ----
set(NVV_CORE_SRC ${NVV_DIR}/vats_types.cpp ${NVV_DIR}/vats_policy.cpp ${NVV_DIR}/vats_slowmo.cpp ${NVV_DIR}/vats_perks.cpp ${NVV_DIR}/vats_wire.cpp
    ${NVV_DIR}/vats_api.cpp ${NVV_DIR}/vats_sync.cpp ${NVV_DIR}/vats_runtime.cpp ${NVV_DIR}/vats_patch.cpp)
foreach(sfx "" "_rt")
  add_library(nvcoop_vats_core${sfx} STATIC ${NVV_CORE_SRC})
  target_include_directories(nvcoop_vats_core${sfx} PUBLIC ${NVV_INC})
  target_link_libraries(nvcoop_vats_core${sfx} PUBLIC ${NVV_SYNC})
  nvv_flags(nvcoop_vats_core${sfx})
  if(sfx STREQUAL "_rt")
    target_compile_definitions(nvcoop_vats_core${sfx} PUBLIC FNVCOOP_RUNTIME_MUT)
  endif()
endforeach()

# ---- tests (+ runtime twins: run_tests.cmd runs the exe once per twin number) ----
set(NVV_TEST_EXCL "")
if(TARGET nvcoop)
  set(NVV_TEST_EXCL EXCLUDE_FROM_ALL)   # plugin build: skip tests unless asked for
endif()
file(GLOB NVV_TEST_SRC CONFIGURE_DEPENDS ${NVV_DIR}/tests/test_*.cpp)
add_executable(nvcoop_vats_tests ${NVV_TEST_EXCL} ${NVV_TEST_SRC} ${NVV_REPO}/synccore/tests/main.cpp)
target_include_directories(nvcoop_vats_tests PRIVATE ${NVV_DIR}/tests ${NVV_REPO}/synccore/tests)
target_link_libraries(nvcoop_vats_tests PRIVATE nvcoop_vats_core_rt)
nvv_flags(nvcoop_vats_tests)
set_target_properties(nvcoop_vats_tests PROPERTIES RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/vats_tests")
foreach(cfg _DEBUG _RELEASE _RELWITHDEBINFO _MINSIZEREL)
  set_target_properties(nvcoop_vats_tests PROPERTIES RUNTIME_OUTPUT_DIRECTORY${cfg} "${CMAKE_BINARY_DIR}/vats_tests")
endforeach()

# ---- x86 game glue (compiled into nvcoop.dll): the hooks, the engine reads, the bridge methods. Inert until nvcoop.ini [vats] enabled=1 ----
if(TARGET nvcoop AND TARGET nvcoop_hooks_game AND TARGET nvcoop_actor_engine_core AND MSVC AND CMAKE_SIZEOF_VOID_P EQUAL 4
   AND EXISTS "${NVV_DIR}/vats_module.cpp")
  target_sources(nvcoop PRIVATE ${NVV_DIR}/vats_module.cpp)
  target_link_libraries(nvcoop PRIVATE nvcoop_vats_core nvcoop_actor_engine_core)
  target_compile_definitions(nvcoop PRIVATE NVCOOP_WITH_VATS=1)
  target_include_directories(nvcoop PRIVATE ${NVV_INC} ${NVV_REPO}/plugin/coop)
endif()
