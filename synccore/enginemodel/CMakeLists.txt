# Fake game engine (T2a): deterministic in-memory model of the synced world plus the live-layer protocol.
# Game-free. Included from synccore/CMakeLists.txt (after enable_testing()).
add_library(fnvcoop_enginemodel STATIC
  src/world.cpp src/messages.cpp src/machine_link.cpp src/machine_sim.cpp src/machine_host.cpp src/machine_intent.cpp src/cluster.cpp)
target_include_directories(fnvcoop_enginemodel PUBLIC include)
target_link_libraries(fnvcoop_enginemodel PUBLIC fnvcoop)

add_executable(fnvcoop_engine_tests ../tests/main.cpp tests/test_ownership.cpp tests/test_world.cpp tests/test_engine.cpp tests/test_twins.cpp tests/scenarios_own.cpp tests/scenarios_engine.cpp)
target_include_directories(fnvcoop_engine_tests PRIVATE ../tests)
target_link_libraries(fnvcoop_engine_tests PRIVATE fnvcoop_enginemodel)

if(MSVC)
  foreach(t fnvcoop_enginemodel fnvcoop_engine_tests)
    target_compile_options(${t} PRIVATE /W4 /permissive- /utf-8 /wd4456 /D_CRT_SECURE_NO_WARNINGS)
  endforeach()
else()
  foreach(t fnvcoop_enginemodel fnvcoop_engine_tests)
    target_compile_options(${t} PRIVATE -Wall -Wextra)
  endforeach()
endif()

add_test(NAME engine_own   COMMAND fnvcoop_engine_tests --group own)
add_test(NAME engine_world COMMAND fnvcoop_engine_tests --group world)
add_test(NAME engine_sim   COMMAND fnvcoop_engine_tests --group engine)
foreach(twin own_no_epoch own_stale_epoch own_adopt_echo own_transfer_no_ack own_no_hysteresis own_follower_ignored own_accept_non_owner
             no_dedupe hit_no_validation hit_by_non_owner pickup_no_arbitration pickup_no_rollback spawn_no_arbitration
             loot_per_machine pick_per_machine container_no_lock named_killable no_session_epoch heal_disabled drop_no_idempotency
             owner_keeps_publishing join_no_ownership)
  add_test(NAME engine_twin_${twin} COMMAND fnvcoop_engine_tests --group twin --name ${twin})
endforeach()
