# plugin/combat: enemy / NPC combat sync in the plugin (FULL-SYNC-PLAN 3.4). Works two ways:
#   * included by plugin/CMakeLists.txt (shared tree build/all): adds the game-free core, the tests, and (x86, nvcoop target present) the engine glue
#   * standalone, for the game-free tests + mutation twins:  plugin\combat\run_tests.cmd
# Targets
#   nvcoop_combat_rules / _rt  synccore/combat/combat.cpp compiled here (production / FNVCOOP_RUNTIME_MUT), so the plugin tree does not depend on
#                              the synccore tree being configured first
#   nvcoop_combat_core / _rt   CombatSync + wire + runtime + call-site patcher (production / runtime twins 701..760)
#   nvcoop_combat_tests        EXCLUDE_FROM_ALL; twins are switched with FNVCOOP_MUT=<n> (tools/mut/run_twins.py)
cmake_minimum_required(VERSION 3.20)
if(CMAKE_SOURCE_DIR STREQUAL CMAKE_CURRENT_LIST_DIR)
  project(nvcoop_combat CXX)
  set(CMAKE_CXX_STANDARD 20)
  set(CMAKE_CXX_STANDARD_REQUIRED ON)
  set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded")
endif()

set(NVCB_DIR "${CMAKE_CURRENT_LIST_DIR}")
set(NVCB_REPO "${NVCB_DIR}/../..")
set(NVCB_INC "${NVCB_DIR}/.." "${NVCB_REPO}/synccore/combat" "${NVCB_REPO}/synccore/include" "${NVCB_REPO}/synccore" "${NVCB_REPO}/tools/mut"
    "${NVCB_REPO}/plugin/hooks")

function(nvcb_flags tgt)
  if(MSVC)
    target_compile_options(${tgt} PRIVATE /W4 /EHsc /permissive- /utf-8 /wd4456 /wd4100 /wd4127 /wd4245 /wd4005 /wd4244)
  endif()
  target_compile_definitions(${tgt} PRIVATE _CRT_SECURE_NO_WARNINGS NOMINMAX)
endfunction()

# ---- synccore base (wire, ownership ...): reuse the coop lane's lib inside the plugin build ----
if(TARGET nvcoop_coop_synccore)
  set(NVCB_SYNC nvcoop_coop_synccore)
elseif(TARGET nvcoop_actors_synccore)
  set(NVCB_SYNC nvcoop_actors_synccore)
else()
  file(GLOB NVCB_SYNC_SRC CONFIGURE_DEPENDS ${NVCB_REPO}/synccore/src/*.cpp)
  list(FILTER NVCB_SYNC_SRC EXCLUDE REGEX "enet_transport\\.cpp$")
  add_library(nvcoop_combat_synccore STATIC ${NVCB_SYNC_SRC})
  target_include_directories(nvcoop_combat_synccore PUBLIC ${NVCB_REPO}/synccore/include ${NVCB_REPO}/synccore)
  nvcb_flags(nvcoop_combat_synccore)
  set(NVCB_SYNC nvcoop_combat_synccore)
endif()

# ---- combat-core rules (synccore/combat/combat.cpp): production and runtime-twin flavours ----
foreach(sfx "" "_rt")
  add_library(nvcoop_combat_rules${sfx} STATIC ${NVCB_REPO}/synccore/combat/combat.cpp)
  target_include_directories(nvcoop_combat_rules${sfx} PUBLIC ${NVCB_REPO}/synccore/combat)
  target_link_libraries(nvcoop_combat_rules${sfx} PUBLIC ${NVCB_SYNC})
  nvcb_flags(nvcoop_combat_rules${sfx})
  if(sfx STREQUAL "_rt")
    target_compile_definitions(nvcoop_combat_rules${sfx} PUBLIC FNVCOOP_RUNTIME_MUT)
  endif()
endforeach()

# ---- the module core ----
set(NVCB_CORE_SRC ${NVCB_DIR}/combat_sync.cpp ${NVCB_DIR}/combat_wire.cpp ${NVCB_DIR}/combat_runtime.cpp ${NVCB_DIR}/call_patch.cpp ${NVCB_DIR}/hit_edit.cpp)
foreach(sfx "" "_rt")
  add_library(nvcoop_combat_core${sfx} STATIC ${NVCB_CORE_SRC})
  target_include_directories(nvcoop_combat_core${sfx} PUBLIC ${NVCB_INC})
  target_link_libraries(nvcoop_combat_core${sfx} PUBLIC nvcoop_combat_rules${sfx})
  nvcb_flags(nvcoop_combat_core${sfx})
  if(sfx STREQUAL "_rt")
    target_compile_definitions(nvcoop_combat_core${sfx} PUBLIC FNVCOOP_RUNTIME_MUT)
  endif()
endforeach()

# ---- tests (+ runtime twins: run_tests.cmd runs the exe once per twin number) ----
set(NVCB_TEST_EXCL "")
if(TARGET nvcoop)
  set(NVCB_TEST_EXCL EXCLUDE_FROM_ALL)   # plugin build: skip tests unless asked for
endif()
file(GLOB NVCB_TEST_SRC CONFIGURE_DEPENDS ${NVCB_DIR}/tests/test_*.cpp)
add_executable(nvcoop_combat_tests ${NVCB_TEST_EXCL} ${NVCB_TEST_SRC} ${NVCB_REPO}/synccore/tests/main.cpp)
target_include_directories(nvcoop_combat_tests PRIVATE ${NVCB_DIR}/tests ${NVCB_REPO}/synccore/tests)
target_link_libraries(nvcoop_combat_tests PRIVATE nvcoop_combat_core_rt)
nvcb_flags(nvcoop_combat_tests)
set_target_properties(nvcoop_combat_tests PROPERTIES RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/combat_tests")
foreach(cfg _DEBUG _RELEASE _RELWITHDEBINFO _MINSIZEREL)
  set_target_properties(nvcoop_combat_tests PROPERTIES RUNTIME_OUTPUT_DIRECTORY${cfg} "${CMAKE_BINARY_DIR}/combat_tests")
endforeach()

# ---- x86 game glue (compiled into nvcoop.dll): the hit gate, the engine effects, the ownership/identity view ----
if(TARGET nvcoop AND TARGET nvcoop_hooks_game AND TARGET nvcoop_actor_engine_core AND MSVC AND CMAKE_SIZEOF_VOID_P EQUAL 4
   AND EXISTS "${NVCB_DIR}/combat_module.cpp")
  target_sources(nvcoop PRIVATE ${NVCB_DIR}/combat_module.cpp)
  target_link_libraries(nvcoop PRIVATE nvcoop_combat_core nvcoop_actor_engine_core)
  target_compile_definitions(nvcoop PRIVATE NVCOOP_WITH_COMBAT=1)
  target_include_directories(nvcoop PRIVATE ${NVCB_INC} ${NVCB_REPO}/plugin/coop)
endif()
