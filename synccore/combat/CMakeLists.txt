# combat-core: game-free combat rules (hit validation, limbs, death, projectiles, explosions, kill XP, hostility). Included from synccore/CMakeLists.txt.
#   fnvcoop_combat     production library (twins compiled out). Linked by the fake engine (synccore/enginemodel) and the plugin glue.
#   fnvcoop_combat_rt  the same source with FNVCOOP_RUNTIME_MUT: mutation twins 301..362 are switched with FNVCOOP_MUT=<n> at run time.
#   combat_tests       the unit tests (links the _rt library). One command: synccore\combat\run_tests.cmd  (prints ALL OK)
add_library(fnvcoop_combat STATIC combat.cpp)
# "combat.h" (inside this folder) and "combat/combat.h" (from synccore/) both resolve
target_include_directories(fnvcoop_combat PUBLIC ${CMAKE_CURRENT_LIST_DIR} ${CMAKE_CURRENT_LIST_DIR}/..)
target_link_libraries(fnvcoop_combat PUBLIC fnvcoop)

add_library(fnvcoop_combat_rt STATIC combat.cpp)
target_include_directories(fnvcoop_combat_rt PUBLIC ${CMAKE_CURRENT_LIST_DIR} ${CMAKE_CURRENT_LIST_DIR}/..)
target_link_libraries(fnvcoop_combat_rt PUBLIC fnvcoop)
target_compile_definitions(fnvcoop_combat_rt PUBLIC FNVCOOP_RUNTIME_MUT)

add_executable(combat_tests tests/test_hits.cpp tests/test_limbs_death.cpp tests/test_proj_blast.cpp tests/test_xp_hostility.cpp tests/test_wire.cpp
  ${CMAKE_CURRENT_LIST_DIR}/../tests/main.cpp)
target_include_directories(combat_tests PRIVATE ${CMAKE_CURRENT_LIST_DIR}/../tests ${CMAKE_CURRENT_LIST_DIR}/tests)
target_link_libraries(combat_tests PRIVATE fnvcoop_combat_rt)

if(MSVC)
  foreach(t fnvcoop_combat fnvcoop_combat_rt combat_tests)
    target_compile_options(${t} PRIVATE /W4 /permissive- /utf-8 /wd4456 /D_CRT_SECURE_NO_WARNINGS)
  endforeach()
else()
  foreach(t fnvcoop_combat fnvcoop_combat_rt combat_tests)
    target_compile_options(${t} PRIVATE -Wall -Wextra)
  endforeach()
endif()

add_test(NAME combat_unit COMMAND combat_tests)
# the twins (FNVCOOP_MUT=301..365) are run by synccore/combat/run_tests.cmd (tools/mut/run_twins.py); ctest only runs the real suite
