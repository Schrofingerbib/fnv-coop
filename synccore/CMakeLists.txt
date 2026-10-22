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
  src/protocol.cpp src/rules.cpp src/baseline.cpp src/replicator.cpp src/transport.cpp src/transport_util.cpp src/session.cpp src/ownership.cpp)
target_include_directories(fnvcoop PUBLIC include)

# Real transport (ENet, vendored MIT source). Separate so the pure core has no socket dependency.
set(ENET_DIR ${CMAKE_CURRENT_SOURCE_DIR}/../third_party/enet)
if(WIN32)
  add_library(enet_static STATIC ${ENET_DIR}/callbacks.c ${ENET_DIR}/compress.c ${ENET_DIR}/host.c ${ENET_DIR}/list.c
    ${ENET_DIR}/packet.c ${ENET_DIR}/peer.c ${ENET_DIR}/protocol.c ${ENET_DIR}/win32.c)
  target_include_directories(enet_static PUBLIC ${ENET_DIR}/include)
  target_compile_definitions(enet_static PRIVATE _WINSOCK_DEPRECATED_NO_WARNINGS)
  target_link_libraries(enet_static PUBLIC ws2_32 winmm)
else()
  # The vendored copy has the Windows backend only (no unix.c). Elsewhere link the system ENet (same 1.3.18 API):
  # Debian/Ubuntu libenet-dev, Fedora enet-devel, Arch enet, macOS brew install enet.
  find_path(FNVCOOP_ENET_INCLUDE enet/enet.h)
  find_library(FNVCOOP_ENET_LIB enet)
  if(NOT FNVCOOP_ENET_INCLUDE OR NOT FNVCOOP_ENET_LIB)
    message(FATAL_ERROR "ENet not found. This platform uses the system ENet: install libenet-dev (or the equivalent for your system).")
  endif()
  add_library(enet_static INTERFACE)
  target_include_directories(enet_static INTERFACE ${FNVCOOP_ENET_INCLUDE})
  target_link_libraries(enet_static INTERFACE ${FNVCOOP_ENET_LIB})
endif()
add_library(fnvcoop_enet STATIC src/enet_transport.cpp)
target_link_libraries(fnvcoop_enet PUBLIC fnvcoop enet_static)

# Simulator + tests (not shipped in the plugin).
add_library(fnvcoop_sim STATIC sim/sim.cpp)
target_include_directories(fnvcoop_sim PUBLIC sim)
target_link_libraries(fnvcoop_sim PUBLIC fnvcoop)

add_executable(fnvcoop_tests tests/main.cpp tests/test_wire.cpp tests/test_core.cpp tests/test_session.cpp tests/test_sim.cpp tests/test_transport.cpp tests/test_baseline.cpp tests/test_saveload.cpp tests/test_rules.cpp tests/test_desync.cpp tests/test_desync_clientonly.cpp tests/test_handshake.cpp tests/test_perf.cpp tests/test_perfguard.cpp tests/test_rejoin.cpp tests/test_rejoin_dlg.cpp tests/test_failquest.cpp tests/test_round3.cpp tests/test_hostload.cpp tests/test_refcmd.cpp tests/test_prepared_load.cpp)
target_compile_definitions(fnvcoop_tests PRIVATE FNVCOOP_RULES_JSON="${CMAKE_CURRENT_SOURCE_DIR}/../docs/quest-policies/rules.json")
target_include_directories(fnvcoop_tests PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/../plugin/dialogue/include)
# perf_guard.h uses runtime mutation twins (tools/mut/fnvcoop_mut.h, FNVCOOP_MUT=<n>); only this test exe, never the plugin
target_include_directories(fnvcoop_tests PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/../tools/mut)
target_compile_definitions(fnvcoop_tests PRIVATE FNVCOOP_RUNTIME_MUT)
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
add_test(NAME soak_smoke_threaded COMMAND fnvcoop_soak --seconds 60 --players 4 --seed 4 --threaded)
# Issue #43: a client that drops on the last event frame with the longest reconnect delay must still rejoin and converge; the twin
# restores the old harness (reconnect after the run ended) and must fail.
add_test(NAME soak_late_disconnect COMMAND fnvcoop_soak --seconds 60 --players 4 --seed 4 --late-disconnect)
add_test(NAME twin_soak_late_disconnect COMMAND fnvcoop_soak --seconds 60 --players 4 --seed 4 --late-disconnect --mutate late-reconnect)
set_tests_properties(twin_soak_late_disconnect PROPERTIES WILL_FAIL TRUE)
add_test(NAME loopback_clean       COMMAND fnvcoop_loopback selftest)
add_test(NAME loopback_faults      COMMAND fnvcoop_loopback selftest --loss 15)
add_test(NAME twin_loopback_idempotency COMMAND fnvcoop_loopback selftest --loss 15 --mutate idempotency)
add_test(NAME twin_loopback_order      COMMAND fnvcoop_loopback selftest --loss 15 --mutate order)
add_test(NAME loopback_unreliable  COMMAND fnvcoop_loopback selftest --loss 10 --unreliable)
add_test(NAME unit_wire     COMMAND fnvcoop_tests --group wire)
add_test(NAME unit_core     COMMAND fnvcoop_tests --group core)
add_test(NAME unit_session  COMMAND fnvcoop_tests --group session)
add_test(NAME unit_round3 COMMAND fnvcoop_tests --group round3)
add_test(NAME unit_refcmd COMMAND fnvcoop_tests --group refcmd)
add_test(NAME unit_hostload COMMAND fnvcoop_tests --group hostload)
add_test(NAME unit_rejoin COMMAND fnvcoop_tests --group rejoin)
add_test(NAME unit_perf COMMAND fnvcoop_tests --group perf)
add_test(NAME unit_perfguard COMMAND fnvcoop_tests --group perfguard)
foreach(n 1 2 3 4 5 6 7 8 9 10)   # perf_guard.h mutation twins: each must make the perfguard group fail
  add_test(NAME twin_perfguard_${n} COMMAND fnvcoop_tests --group perfguard)
  set_tests_properties(twin_perfguard_${n} PROPERTIES WILL_FAIL TRUE ENVIRONMENT "FNVCOOP_MUT=${n}")
endforeach()
# End-to-end twin: the real timing tests with every budget 10x tighter (what a 10x slower code base looks like) must STILL fail, load guard or not.
add_test(NAME twin_perf_regression_still_fails COMMAND fnvcoop_tests --group perf --name heavy_operations_with_an_8k_entry_world_are_cheap_or_offloadable)
set_tests_properties(twin_perf_regression_still_fails PROPERTIES WILL_FAIL TRUE ENVIRONMENT "FNVCOOP_PERF_SCALE=0.1")
add_test(NAME unit_handshake COMMAND fnvcoop_tests --group handshake)
add_test(NAME unit_desync COMMAND fnvcoop_tests --group desync)
add_test(NAME unit_desync_clientonly COMMAND fnvcoop_tests --group desync_clientonly --name client_only_keys_with_an_inverse_converge_even_above_the_repair_cap)
# Issue #25 (fixed): client-only quest running keys are settled by the host's own running state (twin unit_desync_clientonly_quest_running_reverted below).
add_test(NAME unit_desync_client_only_quest_keys COMMAND fnvcoop_tests --group desync_clientonly --name client_only_quest_running_keys_converge)
# Prepared-save load: a client resume cursor from another timeline is rebased (twin prepared_load_trusts_foreign_cursor below).
add_test(NAME unit_preparedload COMMAND fnvcoop_tests --group preparedload)
add_test(NAME unit_rules COMMAND fnvcoop_tests --group rules)
add_test(NAME unit_saveload COMMAND fnvcoop_tests --group saveload)
add_test(NAME unit_baseline COMMAND fnvcoop_tests --group baseline)
add_test(NAME unit_transport COMMAND fnvcoop_tests --group transport)
add_test(NAME sim_seeds     COMMAND fnvcoop_tests --group sim)
foreach(twin hostload_unit_set_weather hostload_unit_set_time_scale hostload_unit_ref_script_var hostload_unit_set_map_marker hostload_unit_lock hostload_unit_object_state_open hostload_unit_object_state_sticky_open hostload_unit_ref_add_item hostload_unit_ref_remove_item hostload_unit_remove_all_items hostload_unit_clear_pre_travels hostload_sim_clear_pre_travels hostload_unit_spawn_ref hostload_unit_despawn_ref hostload_sim_set_weather hostload_sim_set_time_scale hostload_sim_ref_script_var hostload_sim_set_map_marker hostload_sim_object_state_open hostload_sim_ref_inventory hostload_sim_spawn_despawn origins_ignored round3_origins_ignored round3_ref_inv_preimage_ignored round3_spawn_rebase_ignored no_echo_guard no_idempotency stage_last_wins flags_and kills_replace apply_out_of_order
             unit_merge unit_objective_or unit_idempotency unit_echo no_guest_suppression unit_ghost guest_runs_quest_scripts baseline_skipped unit_volatile unit_volatile_ref_script_vars no_epoch_bump unit_epoch unit_flood rules_barrier_release_early rules_disabled rules_extended_disabled rules_tether_ignores_anchor rules_stages_param_ignored rules_sum_as_any rules_exclusive_last_wins rules_ready_proceeds_alone rules_slideshow_no_hold rules_per_player_scene_shared rules_per_player_vars_shared rules_payment_shares_all unit_heal_disabled heal_disabled unit_chatty_baseline unit_handshake unit_slow_digest fail_not_completed round3_spawn_resurrects round3_sticky_objects_reset round3_ref_inv_no_clear round3_active_quest_shared round3_ignore_follower_caps round3_disconnect_keeps_lock round3_guest_local_replicated round3_angle_keeps_first angle_preimage_ignored round3_angle_preimage_ignored rejoin_sidecar_loses_ledger rejoin_no_reward_ids plain_rejoin_no_reward_ids rejoin_no_epoch_bump rejoin_no_idempotency rejoin_departed_hold_barrier rejoin_barrier_release_early rejoin_dlg_no_join_catchup rejoin_dlg_ignore_result_link rejoin_dlg_drop_end_on_close rejoin_dlg_leak_lock_on_close rejoin_dlg_skip_control_release rejoin_dlg_no_resync rejoin_dlg_replay_duplicate_pick refcmd_keeps_first refcmd_events_become_state refcmd_player_shared refcmd_wide_op_narrowed
             unit_desync_clientonly_heal_disabled unit_desync_clientonly_quest_running_reverted prepared_load_trusts_foreign_cursor unit_async_completion_leak)
  add_test(NAME twin_${twin} COMMAND fnvcoop_tests --group twin --name ${twin})
endforeach()

# docs/protocol.md effect table must match effect.h / wire.cpp (regenerate: python -I synccore/tools/gen_effect_table.py --write)
find_program(FNVCOOP_PYTHON NAMES python python3)
if(FNVCOOP_PYTHON)
  add_test(NAME drift_effect_table COMMAND ${FNVCOOP_PYTHON} -I ${CMAKE_CURRENT_SOURCE_DIR}/tools/gen_effect_table.py --check)
  add_test(NAME twin_drift_effect_table COMMAND ${FNVCOOP_PYTHON} -I ${CMAKE_CURRENT_SOURCE_DIR}/tools/gen_effect_table.py --check --mutate)
  set_tests_properties(twin_drift_effect_table PROPERTIES WILL_FAIL TRUE)
  # The real ENet transport and the session protocol through the UDP chaos relay (tools/chaos), seeded profiles, two clients, 150 txns.
  # 'hell' is not gated: see docs/tools/T5-chaos.md ("Through the relay"). A twin passes (exit 0) when its deliberate breakage is caught.
  set(_relay_py ${CMAKE_CURRENT_SOURCE_DIR}/../tools/chaos/enet_relay.py)
  foreach(_p bad_wifi mobile_tether transatlantic)
    add_test(NAME loopback_relay_${_p} COMMAND ${FNVCOOP_PYTHON} -I ${_relay_py} --loopback $<TARGET_FILE:fnvcoop_loopback> --profile ${_p})
  endforeach()
  add_test(NAME twin_loopback_relay_bypass    COMMAND ${FNVCOOP_PYTHON} -I ${_relay_py} --loopback $<TARGET_FILE:fnvcoop_loopback> --profile bad_wifi --twin bypass)
  add_test(NAME twin_loopback_relay_partition COMMAND ${FNVCOOP_PYTHON} -I ${_relay_py} --loopback $<TARGET_FILE:fnvcoop_loopback> --profile bad_wifi --twin partition --timeout 20)
endif()

# T5 chaos mirror (synccore/chaos/CMakeLists.txt)
add_subdirectory(chaos)

# combat-core: game-free combat rules (synccore/combat/CMakeLists.txt); before enginemodel, which links fnvcoop_combat
add_subdirectory(combat)

# companions-crime: followers, crime / alarms / bounty / hostility, kill rewards (synccore/social/CMakeLists.txt); after combat, which it links
add_subdirectory(social)

# T2a: fake game engine model + actor ownership tests (own CMakeLists; ownership.cpp itself is in the core library above)
add_subdirectory(enginemodel)

# T1a flight recorder (synccore/flight/CMakeLists.txt); after enginemodel: it adapts T2a's observer
add_subdirectory(flight)

# live-core: actor snapshots, delta codec, interpolation, interest management (synccore/live/CMakeLists.txt)
add_subdirectory(live)

# spawn-arb: spawn-site arbitration, server-issued net ids, respawn / cell reset rules (synccore/spawn/CMakeLists.txt)
add_subdirectory(spawn)

# loot: containers, corpses, dropped items, pickups, physics clutter; idempotent transfers, locks, claims (synccore/loot/CMakeLists.txt)
add_subdirectory(loot)

# area-desync: per-area digests, targeted diff and heal (synccore/areadigest/CMakeLists.txt); after flight/, the heal counters are exported there
add_subdirectory(areadigest)

# Precompiled headers for the heavy std headers (only in the shared tree, tools/build/pch.cmake; a no-op for standalone builds).
# Targets of subdirectories are handled in their own CMakeLists; these are this directory's.
if(COMMAND fnvcoop_pch)
  foreach(_t fnvcoop fnvcoop_sim fnvcoop_tests)
    fnvcoop_pch(${_t})
  endforeach()
endif()

# ctest -j: timing-sensitive tests run alone, everything else (about 150 twin tests of a few seconds each) can overlap.
foreach(_t unit_perf twin_perf_regression_still_fails soak_smoke soak_smoke_threaded soak_late_disconnect twin_soak_late_disconnect loopback_clean loopback_faults loopback_unreliable
        loopback_relay_bad_wifi loopback_relay_mobile_tether loopback_relay_transatlantic twin_loopback_relay_bypass twin_loopback_relay_partition)
  if(TEST ${_t})
    set_tests_properties(${_t} PROPERTIES RUN_SERIAL TRUE)
  endif()
endforeach()
