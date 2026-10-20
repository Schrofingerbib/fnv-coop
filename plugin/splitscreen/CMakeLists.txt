# Track B split-screen pieces. Works two ways:
#   * included by plugin/CMakeLists.txt: builds nvsplit_focus, linked into the permanent DLL (monolithic nvcoop.dll or
#     the split-mode loader), never into the reloadable core
#   * standalone, for the game-free tests + mutation twins:  plugin\splitscreen\run_tests.cmd
cmake_minimum_required(VERSION 3.20)
if(CMAKE_SOURCE_DIR STREQUAL CMAKE_CURRENT_LIST_DIR)
  project(nvcoop_splitscreen CXX)
  set(CMAKE_CXX_STANDARD 20)
  set(CMAKE_CXX_STANDARD_REQUIRED ON)
  set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded")
endif()

set(NVSPLIT_DIR "${CMAKE_CURRENT_LIST_DIR}")

function(nvsplit_flags tgt)
  if(MSVC)
    target_compile_options(${tgt} PRIVATE /W4 /EHsc /permissive- /utf-8)
  endif()
endfunction()

add_library(nvsplit_focus STATIC ${NVSPLIT_DIR}/focus_guard.cpp)
target_include_directories(nvsplit_focus PUBLIC ${NVSPLIT_DIR})
nvsplit_flags(nvsplit_focus)

if(CMAKE_SOURCE_DIR STREQUAL CMAKE_CURRENT_LIST_DIR)
  # FOCUS_GUARD_TESTING gives the test-only hooks in focus_guard.h. The nvsplit_focus library above never defines it.
  foreach(n 0 1 2 3 4 5 6)
    add_executable(nvsplit_focus_tests_m${n} ${NVSPLIT_DIR}/tests/test_focus_guard.cpp ${NVSPLIT_DIR}/focus_guard.cpp)
    target_compile_definitions(nvsplit_focus_tests_m${n} PRIVATE FOCUS_GUARD_MUTANT=${n} FOCUS_GUARD_TESTING=1)
    target_link_libraries(nvsplit_focus_tests_m${n} PRIVATE user32)
    nvsplit_flags(nvsplit_focus_tests_m${n})
  endforeach()
endif()
