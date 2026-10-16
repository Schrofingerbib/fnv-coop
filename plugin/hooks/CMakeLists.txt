# L5 hooks. Works two ways:
#   * included by plugin/CMakeLists.txt (adds nvcoop_hooks_core / nvcoop_hooks_game targets)
#   * standalone, for the game-free unit tests (any arch, any generator):
#       cmake -S plugin/hooks -B build/L5 && cmake --build build/L5 --config Release && ctest --test-dir build/L5 -C Release
#     or just run plugin\hooks\run_tests.bat
cmake_minimum_required(VERSION 3.20)
if(CMAKE_SOURCE_DIR STREQUAL CMAKE_CURRENT_LIST_DIR)
  project(nvcoop_hooks CXX)
  set(CMAKE_CXX_STANDARD 20)
  set(CMAKE_CXX_STANDARD_REQUIRED ON)
  set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded")
endif()

set(NVH_DIR "${CMAKE_CURRENT_LIST_DIR}")
set(NVH_SYNCCORE_INC "${NVH_DIR}/../../synccore/include")

function(nvh_flags tgt)
  if(MSVC)
    target_compile_options(${tgt} PRIVATE /W4 /EHsc /permissive- /utf-8 /wd4456 /wd4100 /wd4127)
  endif()
  target_compile_definitions(${tgt} PRIVATE _CRT_SECURE_NO_WARNINGS)
endfunction()

# ---- game-free core: prologue-checked patcher + capture hub (+ mutant twin) ----
foreach(v "" _mutant)
  add_library(nvcoop_hooks_core${v} STATIC ${NVH_DIR}/hook_patch.cpp ${NVH_DIR}/capture_hub.cpp ${NVH_DIR}/effect_json.cpp ${NVH_DIR}/suppress_policy.cpp ${NVH_DIR}/change_tracker.cpp ${NVH_DIR}/apply_lines.cpp ${NVH_DIR}/inventory_diff.cpp)
  target_include_directories(nvcoop_hooks_core${v} PUBLIC ${NVH_DIR} ${NVH_DIR}/.. ${NVH_SYNCCORE_INC})
  nvh_flags(nvcoop_hooks_core${v})
  add_executable(nvcoop_hooks_tests${v} ${NVH_DIR}/tests/test_hooks.cpp)
  target_link_libraries(nvcoop_hooks_tests${v} PRIVATE nvcoop_hooks_core${v})
  nvh_flags(nvcoop_hooks_tests${v})
endforeach()
target_compile_definitions(nvcoop_hooks_core_mutant PUBLIC NVHOOKS_MUTANT=1)
if(NOT CMAKE_RUNTIME_OUTPUT_DIRECTORY)
  # keep test exes out of L1's out/L1 deployables
  set_target_properties(nvcoop_hooks_tests nvcoop_hooks_tests_mutant PROPERTIES RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/hooks_tests")
endif()
foreach(cfg "" _DEBUG _RELEASE _RELWITHDEBINFO _MINSIZEREL)
  set_target_properties(nvcoop_hooks_tests nvcoop_hooks_tests_mutant PROPERTIES RUNTIME_OUTPUT_DIRECTORY${cfg} "${CMAKE_BINARY_DIR}/hooks_tests")
endforeach()

enable_testing()
add_test(NAME hooks_unit COMMAND nvcoop_hooks_tests)
add_test(NAME hooks_unit_mutation_twin COMMAND nvcoop_hooks_tests_mutant)
set_tests_properties(hooks_unit_mutation_twin PROPERTIES WILL_FAIL TRUE)

# ---- game glue (x86 only): detours, effect capture hooks, remote-effect apply. Compiled against xNVSE headers. ----
if(MSVC AND CMAKE_SIZEOF_VOID_P EQUAL 4 AND EXISTS "${NVH_DIR}/engine_hooks.cpp")
  set(NVH_NVSE "${NVH_DIR}/../../third_party/NVSE" CACHE PATH "xNVSE checkout")
  if(NOT EXISTS "${NVH_NVSE}/nvse/nvse/GameForms.h")
    message(STATUS "hooks: xNVSE headers not found at ${NVH_NVSE}; nvcoop_hooks_game skipped")
  else()
    add_library(nvcoop_hooks_game STATIC ${NVH_DIR}/engine_hooks.cpp ${NVH_DIR}/apply_remote.cpp)
    target_link_libraries(nvcoop_hooks_game PUBLIC nvcoop_hooks_core)
    target_include_directories(nvcoop_hooks_game PUBLIC ${NVH_DIR}
      "${NVH_NVSE}" "${NVH_NVSE}/nvse" "${NVH_NVSE}/nvse/nvse")
    target_compile_definitions(nvcoop_hooks_game PRIVATE RUNTIME=1 RUNTIME_VERSION=0x040020D0 WIN32 _WINDOWS _CRT_SECURE_NO_WARNINGS)
    target_compile_options(nvcoop_hooks_game PRIVATE "SHELL:/FI ${NVH_NVSE}/nvse/nvse/prefix.h" /W3 /EHsc /permissive- /Zc:__cplusplus)
    # Linking into the plugin is harmless: nothing happens until nvhooks::Init() is called by main.cpp (L1).
    if(TARGET nvcoop)
      target_link_libraries(nvcoop PRIVATE nvcoop_hooks_game)
      # bridge methods + role from nvcoop.ini; registered through NVCOOP_MODULE so it must be a direct source
      target_sources(nvcoop PRIVATE ${NVH_DIR}/hooks_module.cpp)
    endif()
  endif()
endif()
