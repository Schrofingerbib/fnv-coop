# plugin/intents: guest intents (FULL-SYNC-PLAN 3.7, roadmap step 8) + SaveSuppress enforcement. Added by plugin/CMakeLists.txt AFTER coop (it needs
# nvcoop_coop_core), so it must stay behind it in the lane list. Shared tree build/all (tools/build/build.py).
#   nvcoop_intents_core / _rt   wire, policy, host, client, container redirect, service (production / runtime twins 301..324)
#   nvcoop_intents_tests        EXCLUDE_FROM_ALL; twins are switched with FNVCOOP_MUT=<n> (tools/mut/run_twins.py)
#   x86 glue (engine/*.cpp) is compiled into nvcoop.dll and stays inert until nvcoop.ini [intents] enable=1.
cmake_minimum_required(VERSION 3.20)

if(NOT TARGET nvcoop_coop_core_rt)
  message(STATUS "intents: nvcoop_coop_core not configured (plugin/coop must come first); nvcoop_intents skipped")
  return()
endif()

set(NVI_DIR "${CMAKE_CURRENT_LIST_DIR}")
set(NVI_REPO "${NVI_DIR}/../..")

function(nvi_flags tgt)
  if(MSVC)
    target_compile_options(${tgt} PRIVATE /W4 /EHsc /permissive- /utf-8 /wd4456 /wd4100 /wd4127 /wd4245 /wd4005 /wd4244)
  endif()
  target_compile_definitions(${tgt} PRIVATE _CRT_SECURE_NO_WARNINGS WIN32_LEAN_AND_MEAN NOMINMAX)
endfunction()

set(NVI_SRC ${NVI_DIR}/intent_wire.cpp ${NVI_DIR}/intent_policy.cpp ${NVI_DIR}/container_redirect.cpp ${NVI_DIR}/intent_host.cpp
    ${NVI_DIR}/intent_client.cpp ${NVI_DIR}/intent_service.cpp)
foreach(sfx "" "_rt")
  add_library(nvcoop_intents_core${sfx} STATIC ${NVI_SRC})
  target_include_directories(nvcoop_intents_core${sfx} PUBLIC ${NVI_DIR}/.. ${NVI_REPO}/tools/mut)
  target_link_libraries(nvcoop_intents_core${sfx} PUBLIC nvcoop_coop_core${sfx})
  nvi_flags(nvcoop_intents_core${sfx})
  if(sfx STREQUAL "_rt")
    target_compile_definitions(nvcoop_intents_core${sfx} PUBLIC FNVCOOP_RUNTIME_MUT)
  endif()
endforeach()

# ---- tests (one exe; runtime twins are chosen with FNVCOOP_MUT=<n>) ----
file(GLOB NVI_TEST_SRC CONFIGURE_DEPENDS ${NVI_DIR}/tests/test_*.cpp)
add_executable(nvcoop_intents_tests EXCLUDE_FROM_ALL ${NVI_TEST_SRC} ${NVI_REPO}/synccore/tests/main.cpp)
target_include_directories(nvcoop_intents_tests PRIVATE ${NVI_DIR} ${NVI_DIR}/tests ${NVI_REPO}/synccore/tests ${NVI_DIR}/../coop/tests)
target_link_libraries(nvcoop_intents_tests PRIVATE nvcoop_intents_core_rt)
nvi_flags(nvcoop_intents_tests)
set_target_properties(nvcoop_intents_tests PROPERTIES RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/intents_tests")
foreach(cfg _DEBUG _RELEASE _RELWITHDEBINFO _MINSIZEREL)
  set_target_properties(nvcoop_intents_tests PROPERTIES RUNTIME_OUTPUT_DIRECTORY${cfg} "${CMAKE_BINARY_DIR}/intents_tests")
endforeach()

# ---- x86 game glue (compiled into nvcoop.dll): the engine over xNVSE, the module, the SaveSuppress detour ----
if(TARGET nvcoop AND TARGET nvcoop_hooks_game AND MSVC AND CMAKE_SIZEOF_VOID_P EQUAL 4
   AND EXISTS "${NVI_DIR}/engine/intents_module.cpp" AND EXISTS "${NVI_DIR}/engine/game_engine.cpp")
  target_sources(nvcoop PRIVATE ${NVI_DIR}/engine/intents_module.cpp ${NVI_DIR}/engine/game_engine.cpp)
  target_link_libraries(nvcoop PRIVATE nvcoop_intents_core)
  target_compile_definitions(nvcoop PRIVATE NVCOOP_WITH_INTENTS=1)
  target_include_directories(nvcoop PRIVATE ${NVI_DIR}/.. ${NVI_DIR}/../coop)
endif()
