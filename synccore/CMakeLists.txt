cmake_minimum_required(VERSION 3.20)
project(fnvcoop_synccore LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
if(NOT CMAKE_CONFIGURATION_TYPES AND NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()

# Core library: game-free, links into the game plugin (build Win32/x86 there).
add_library(fnvcoop STATIC
  src/wire.cpp src/shadow.cpp src/policy.cpp src/storylog.cpp
  src/protocol.cpp src/rules.cpp src/baseline.cpp src/replicator.cpp src/transport.cpp src/transport_util.cpp src/session.cpp)
target_include_directories(fnvcoop PUBLIC include)

# Real transport (ENet, vendored MIT source). Separate so the pure core has no socket dependency.
set(ENET_DIR ${CMAKE_CURRENT_SOURCE_DIR}/../third_party/enet)
add_library(enet_static STATIC ${ENET_DIR}/callbacks.c ${ENET_DIR}/compress.c ${ENET_DIR}/host.c ${ENET_DIR}/list.c
  ${ENET_DIR}/packet.c ${ENET_DIR}/peer.c ${ENET_DIR}/protocol.c ${ENET_DIR}/win32.c)
target_include_directories(enet_static PUBLIC ${ENET_DIR}/include)
target_compile_definitions(enet_static PRIVATE _WINSOCK_DEPRECATED_NO_WARNINGS)
target_link_libraries(enet_static PUBLIC ws2_32 winmm)
add_library(fnvcoop_enet STATIC src/enet_transport.cpp)
target_link_libraries(fnvcoop_enet PUBLIC fnvcoop enet_static)

# Simulator + tests (not shipped in the plugin).
add_library(fnvcoop_sim STATIC sim/sim.cpp)
target_include_directories(fnvcoop_sim PUBLIC sim)
target_link_libraries(fnvcoop_sim PUBLIC fnvcoop)

add_executable(fnvcoop_tests tests/main.cpp tests/test_wire.cpp tests/test_core.cpp tests/test_session.cpp tests/test_sim.cpp tests/test_transport.cpp tests/test_baseline.cpp tests/test_saveload.cpp tests/test_rules.cpp tests/test_desync.cpp tests/test_handshake.cpp tests/test_perf.cpp tests/test_rejoin.cpp tests/test_rejoin_dlg.cpp tests/test_failquest.cpp)
target_compile_definitions(fnvcoop_tests PRIVATE FNVCOOP_RULES_JSON="${CMAKE_CURRENT_SOURCE_DIR}/../docs/quest-policies/rules.json")
target_include_directories(fnvcoop_tests PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/../plugin/dialogue/include)
target_link_libraries(fnvcoop_tests PRIVATE fnvcoop_sim fnvcoop_enet)

if(MSVC)
  foreach(t fnvcoop fnvcoop_sim fnvcoop_tests)
    target_compile_options(${t} PRIVATE /W4 /permissive- /utf-8 /wd4456 /D_CRT_SECURE_NO_WARNINGS)
  endforeach()
else()
  foreach(t fnvcoop fnvcoop_sim fnvcoop_tests)
    target_compile_options(${t} PRIVATE -Wall -Wextra)
  endforeach()
endif()

add_executable(fnvcoop_loopback tests/loopback.cpp)
target_link_libraries(fnvcoop_loopback PRIVATE fnvcoop_sim fnvcoop_enet)
if(MSVC)
  target_compile_options(fnvcoop_enet PRIVATE /W4 /permissive- /utf-8)
  target_compile_options(fnvcoop_loopback PRIVATE /W4 /permissive- /utf-8 /wd4456 /D_CRT_SECURE_NO_WARNINGS)
endif()

add_executable(fnvcoop_soak soak/soak.cpp)
target_link_libraries(fnvcoop_soak PRIVATE fnvcoop)
target_compile_definitions(fnvcoop_soak PRIVATE
  FNVCOOP_PROFILE_DEFAULT="${CMAKE_CURRENT_SOURCE_DIR}/data/goodsprings_profile.txt"
  FNVCOOP_RULES_DEFAULT="${CMAKE_CURRENT_SOURCE_DIR}/../docs/quest-policies/rules.json")
if(MSVC)
  target_compile_options(fnvcoop_soak PRIVATE /W4 /permissive- /utf-8 /wd4456 /D_CRT_SECURE_NO_WARNINGS)
endif()

enable_testing()
add_test(NAME soak_smoke COMMAND fnvcoop_soak --seconds 300 --players 4 --seed 3)
add_test(NAME soak_smoke_threaded COMMAND fnvcoop_soak --seconds 300 --players 4 --seed 4 --threaded)
add_test(NAME loopback_clean       COMMAND fnvcoop_loopback selftest)
add_test(NAME loopback_faults      COMMAND fnvcoop_loopback selftest --loss 15)
add_test(NAME twin_loopback_idempotency COMMAND fnvcoop_loopback selftest --loss 15 --mutate idempotency)
add_test(NAME twin_loopback_order      COMMAND fnvcoop_loopback selftest --loss 15 --mutate order)
add_test(NAME loopback_unreliable  COMMAND fnvcoop_loopback selftest --loss 10 --unreliable)
add_test(NAME unit_wire     COMMAND fnvcoop_tests --group wire)
add_test(NAME unit_core     COMMAND fnvcoop_tests --group core)
add_test(NAME unit_session  COMMAND fnvcoop_tests --group session)
add_test(NAME unit_rejoin COMMAND fnvcoop_tests --group rejoin)
add_test(NAME unit_perf COMMAND fnvcoop_tests --group perf)
add_test(NAME unit_handshake COMMAND fnvcoop_tests --group handshake)
add_test(NAME unit_desync COMMAND fnvcoop_tests --group desync)
add_test(NAME unit_rules COMMAND fnvcoop_tests --group rules)
add_test(NAME unit_saveload COMMAND fnvcoop_tests --group saveload)
add_test(NAME unit_baseline COMMAND fnvcoop_tests --group baseline)
add_test(NAME unit_transport COMMAND fnvcoop_tests --group transport)
add_test(NAME sim_seeds     COMMAND fnvcoop_tests --group sim)
foreach(twin no_echo_guard no_idempotency stage_last_wins flags_and kills_replace apply_out_of_order
             unit_merge unit_idempotency unit_echo no_guest_suppression unit_ghost guest_runs_quest_scripts baseline_skipped unit_volatile no_epoch_bump unit_epoch unit_flood rules_barrier_release_early rules_disabled rules_extended_disabled unit_heal_disabled heal_disabled unit_chatty_baseline unit_handshake unit_slow_digest fail_not_completed rejoin_sidecar_loses_ledger rejoin_no_epoch_bump rejoin_no_idempotency rejoin_departed_hold_barrier rejoin_barrier_release_early rejoin_dlg_no_join_catchup rejoin_dlg_ignore_result_link rejoin_dlg_drop_end_on_close rejoin_dlg_leak_lock_on_close rejoin_dlg_skip_control_release rejoin_dlg_no_resync rejoin_dlg_replay_duplicate_pick)
  add_test(NAME twin_${twin} COMMAND fnvcoop_tests --group twin --name ${twin})
endforeach()
