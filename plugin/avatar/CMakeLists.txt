# Player puppets (plugin/avatar). Works two ways:
#   * included by plugin/CMakeLists.txt: defines the static library nvcoop_avatar (EXCLUDE_FROM_ALL, so the canonical
#     nvcoop.dll build does not compile it until something links it) and the test targets (also EXCLUDE_FROM_ALL).
#   * standalone, for the game-free tests (any arch):  plugin\avatar\run_tests.cmd
# Mutation twins are runtime switches (avatar_faults.h), so there is ONE test exe and no per-mutant rebuild.
cmake_minimum_required(VERSION 3.20)
if(CMAKE_SOURCE_DIR STREQUAL CMAKE_CURRENT_LIST_DIR)
  project(nvcoop_avatar CXX)
  set(CMAKE_CXX_STANDARD 17)
  set(CMAKE_CXX_STANDARD_REQUIRED ON)
  set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded")
  set(NVAV_EXCLUDE "")
else()
  set(NVAV_EXCLUDE EXCLUDE_FROM_ALL)
endif()

set(NVAV_DIR "${CMAKE_CURRENT_LIST_DIR}")
set(NVAV_SYNC "${CMAKE_CURRENT_LIST_DIR}/../../synccore")

add_library(nvcoop_avatar STATIC ${NVAV_EXCLUDE}
  ${NVAV_DIR}/anim_planner.cpp ${NVAV_DIR}/equip_corrector.cpp ${NVAV_DIR}/puppet_manager.cpp ${NVAV_DIR}/script_engine.cpp ${NVAV_DIR}/avatar_api.cpp ${NVAV_DIR}/puppet_hits.cpp)
target_include_directories(nvcoop_avatar PUBLIC ${NVAV_DIR} ${NVAV_SYNC} ${NVAV_SYNC}/include)

add_executable(avatar_tests ${NVAV_EXCLUDE} ${NVAV_DIR}/tests/test_avatar.cpp)
target_link_libraries(avatar_tests PRIVATE nvcoop_avatar)

# Integration with the real live layer (InterpBuffer, snapshot codec): needs synccore/live/live.cpp (live-core).
if(EXISTS ${NVAV_SYNC}/live/live.cpp AND EXISTS ${NVAV_SYNC}/src/wire.cpp)
  add_executable(avatar_live_tests ${NVAV_EXCLUDE} ${NVAV_DIR}/tests/test_live_integration.cpp
    ${NVAV_SYNC}/live/live.cpp ${NVAV_SYNC}/src/wire.cpp)
  target_link_libraries(avatar_live_tests PRIVATE nvcoop_avatar)
  set(NVAV_HAVE_LIVE TRUE)
endif()

foreach(t nvcoop_avatar avatar_tests avatar_live_tests)
  if(TARGET ${t})
    if(MSVC)
      target_compile_options(${t} PRIVATE /W4 /EHsc /permissive- /utf-8 /wd4100 /D_CRT_SECURE_NO_WARNINGS)
    else()
      target_compile_options(${t} PRIVATE -Wall -Wextra -Wno-unused-parameter)
    endif()
    foreach(cfg "" _DEBUG _RELEASE _RELWITHDEBINFO _MINSIZEREL)
      set_target_properties(${t} PROPERTIES RUNTIME_OUTPUT_DIRECTORY${cfg} "${CMAKE_BINARY_DIR}/avatar_tests")
    endforeach()
  endif()
endforeach()

# the real puppet engine on top of the driver (engine/avatar_engine.cmake): game-free policy + tests, and the game side for nvcoop.dll
include(${NVAV_DIR}/engine/avatar_engine.cmake)
# the whole chain on three simulated machines (full/avatar_full.cmake; needs the shared plugin build)
include(${NVAV_DIR}/full/avatar_full.cmake)

enable_testing()
add_test(NAME avatar_suite COMMAND avatar_tests --no-twins)
if(NVAV_HAVE_LIVE)
  add_test(NAME avatar_live COMMAND avatar_live_tests)
endif()
