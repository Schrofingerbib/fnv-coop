# fnvcoop_snapshot: canonical world-state snapshot (L7).
# Wire into L1's plugin/CMakeLists.txt:
#   add_subdirectory(snapshot)                       # path relative to plugin/
#   target_link_libraries(nvcoop PRIVATE fnvcoop_snapshot)
# and call fnvcoop::snapshot::SnapshotJson(opts) from the `state` method (main thread only).
cmake_minimum_required(VERSION 3.20)
if(NOT PROJECT_NAME)
  project(fnvcoop_snapshot CXX)
endif()

set(FNVCOOP_NVSE_DIR "${CMAKE_CURRENT_LIST_DIR}/../../third_party/NVSE" CACHE PATH
    "Directory holding the xNVSE checkout (contains nvse/nvse/GameForms.h)")

# Game-free core: JSON writer + helpers. Builds on any compiler/arch; unit-tested natively.
add_library(fnvcoop_snapshot_core STATIC src/json.cpp src/snapshot_util.cpp)
target_include_directories(fnvcoop_snapshot_core PUBLIC include)
target_compile_features(fnvcoop_snapshot_core PUBLIC cxx_std_17)

# Engine dumper (needs xNVSE headers; Win32/x86 MSVC only).
# (standalone build: engine dumper only; baseline_*.cpp are compiled into nvcoop below)
add_library(fnvcoop_snapshot STATIC src/snapshot_nvse.cpp)
target_link_libraries(fnvcoop_snapshot PUBLIC fnvcoop_snapshot_core)
target_include_directories(fnvcoop_snapshot PRIVATE
  "${FNVCOOP_NVSE_DIR}" "${FNVCOOP_NVSE_DIR}/nvse" "${FNVCOOP_NVSE_DIR}/nvse/nvse")
target_compile_definitions(fnvcoop_snapshot PRIVATE RUNTIME=1 RUNTIME_VERSION=0x040020D0)
if(MSVC)
  # xNVSE headers expect its prefix header (IPrefix + utility types) forced in.
  target_compile_options(fnvcoop_snapshot PRIVATE "SHELL:/FI ${FNVCOOP_NVSE_DIR}/nvse/nvse/prefix.h" /EHsc /Zc:__cplusplus /permissive-)
  target_compile_features(fnvcoop_snapshot PRIVATE cxx_std_20)
endif()

# When included from plugin/CMakeLists.txt (target nvcoop exists) the engine dumper and the xNVSE
# game-API sources it needs are compiled straight into nvcoop (so SnapshotJson is always linked and
# unresolved engine symbols show up at link time, not at runtime).
if(TARGET nvcoop AND NVCOOP_LINK_SNAPSHOT)
  # Same source set as nvse_plugin_example.vcxproj (+ common/ logging), as an OBJECT library.
  set(_N "${FNVCOOP_NVSE_DIR}/nvse/nvse")
  set(_NVSE_SRC)
  foreach(f containers GameAPI GameBSExtraData GameData GameExtraData GameForms GameObjects GameOSDepend
            GameProcess GameRTTI GameScript GameSettings GameTasks GameTiles GameTypes NiTypes printf
            SafeWrite Utilities utility)
    list(APPEND _NVSE_SRC "${_N}/${f}.cpp")
  endforeach()
  list(APPEND _NVSE_SRC "${FNVCOOP_NVSE_DIR}/common/IDebugLog.cpp" "${FNVCOOP_NVSE_DIR}/common/IErrors.cpp" "${FNVCOOP_NVSE_DIR}/common/IFileStream.cpp" "${FNVCOOP_NVSE_DIR}/common/IDataStream.cpp")
  add_library(fnvcoop_nvse_gameapi OBJECT ${_NVSE_SRC})
  target_include_directories(fnvcoop_nvse_gameapi PRIVATE
    "${FNVCOOP_NVSE_DIR}" "${FNVCOOP_NVSE_DIR}/nvse" "${FNVCOOP_NVSE_DIR}/nvse/nvse")
  target_compile_definitions(fnvcoop_nvse_gameapi PRIVATE RUNTIME=1 RUNTIME_VERSION=0x040020D0 WIN32 _WINDOWS _CRT_SECURE_NO_WARNINGS)
  target_compile_features(fnvcoop_nvse_gameapi PRIVATE cxx_std_20)
  if(MSVC)
    target_compile_options(fnvcoop_nvse_gameapi PRIVATE "SHELL:/FI ${FNVCOOP_NVSE_DIR}/nvse/nvse/prefix.h" /EHsc /Zc:__cplusplus /w)
  endif()
  # Compile the dumper straight into nvcoop so SnapshotJson is always linked.
  target_sources(nvcoop PRIVATE ${CMAKE_CURRENT_LIST_DIR}/src/snapshot_nvse.cpp
    ${CMAKE_CURRENT_LIST_DIR}/src/baseline_nvse.cpp ${CMAKE_CURRENT_LIST_DIR}/src/baseline_build.cpp
    ${CMAKE_CURRENT_LIST_DIR}/src/test_helpers_nvse.cpp
    $<TARGET_OBJECTS:fnvcoop_nvse_gameapi>)
  # Default volatile-keys file baked in as an absolute source-tree path: OFF by default, because that path carries the
  # builder's user name into the dll. Provisioning and the release packager put nvcoop_volatile_keys.txt next to the dll
  # (the plugin looks there first). Turn on only for a local dev build that is never shared.
  option(FNVCOOP_EMBED_DEV_PATHS "bake source-tree paths into nvcoop.dll (local dev only, never ship)" OFF)
  if(FNVCOOP_EMBED_DEV_PATHS)
    set_source_files_properties(${CMAKE_CURRENT_LIST_DIR}/src/baseline_nvse.cpp TARGET_DIRECTORY nvcoop PROPERTIES COMPILE_DEFINITIONS
      "FNVCOOP_DEFAULT_VOLATILE_KEYS=\"${CMAKE_CURRENT_LIST_DIR}/data/volatile_keys.txt\"")
  endif()
  # baseline_nvse.cpp uses synccore's shared volatile-keys policy (fnvcoop::volatileKeys, FormRef::make, ...), so
  # nvcoop must link the synccore library. plugin/coop defines it (nvcoop_coop_synccore, resolved at generate time
  # even though the coop directory is added after this one).
  if(EXISTS "${CMAKE_CURRENT_LIST_DIR}/../coop/CMakeLists.txt")
    target_link_libraries(nvcoop PRIVATE nvcoop_coop_synccore)
  else()
    message(FATAL_ERROR "plugin/snapshot baseline dumper needs synccore (nvcoop_coop_synccore from plugin/coop); "
                        "configure with -DNVCOOP_LINK_SNAPSHOT=OFF or provide the coop module")
  endif()
  # baseline_dump.h uses fnvcoop/shadow.h
  target_include_directories(nvcoop PRIVATE "${CMAKE_CURRENT_LIST_DIR}/../../synccore/include")
  target_link_libraries(nvcoop PRIVATE fnvcoop_snapshot_core)
endif()

include(CTest)
add_executable(snapshot_json_test tests/json_test.cpp)
target_link_libraries(snapshot_json_test PRIVATE fnvcoop_snapshot_core)
add_test(NAME snapshot_json COMMAND snapshot_json_test)
