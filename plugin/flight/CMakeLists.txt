# T1a flight recorder, plugin side. Works two ways:
#   * included by plugin/CMakeLists.txt (after coop): adds the module + service to nvcoop.dll
#   * standalone, for the game-free tests:  plugin\flight\run_tests.cmd
cmake_minimum_required(VERSION 3.20)
if(CMAKE_SOURCE_DIR STREQUAL CMAKE_CURRENT_LIST_DIR)
  project(nvcoop_flight CXX)
  set(CMAKE_CXX_STANDARD 20)
  set(CMAKE_CXX_STANDARD_REQUIRED ON)
  set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded")
  set(FLIGHT_STANDALONE ON)
endif()
set(FLIGHT_DIR "${CMAKE_CURRENT_LIST_DIR}")
set(FLIGHT_REPO "${FLIGHT_DIR}/../..")

function(flight_flags tgt)
  if(MSVC)
    target_compile_options(${tgt} PRIVATE /W4 /EHsc /permissive- /utf-8 /wd4456 /wd4100 /wd4127 /wd4245 /wd4005)
  endif()
  target_compile_definitions(${tgt} PRIVATE _CRT_SECURE_NO_WARNINGS WIN32_LEAN_AND_MEAN NOMINMAX)
endfunction()

if(FLIGHT_STANDALONE)
  # coop's own CMake builds synccore + the runtime (+ the recorder); its tests are excluded from this build
  add_subdirectory("${FLIGHT_DIR}/../coop" "${CMAKE_BINARY_DIR}/coop" EXCLUDE_FROM_ALL)
endif()
if(FLIGHT_STANDALONE OR (TARGET nvcoop AND TARGET nvcoop_coop_core))
  add_library(nvcoop_flight_service STATIC ${FLIGHT_DIR}/flight_service.cpp)
  target_include_directories(nvcoop_flight_service PUBLIC ${FLIGHT_DIR} ${FLIGHT_DIR}/.. ${FLIGHT_REPO}/synccore ${FLIGHT_REPO}/synccore/include)
  target_link_libraries(nvcoop_flight_service PUBLIC nvcoop_coop_synccore)
  flight_flags(nvcoop_flight_service)
  if(NOT FLIGHT_STANDALONE)
    set_target_properties(nvcoop_flight_service PROPERTIES EXCLUDE_FROM_ALL TRUE)   # shared tree: tests only when asked for
  endif()

  # Mutation twins of the plugin-side wiring are RUNTIME-switchable (tools/mut/fnvcoop_mut.h): ONE test exe, FNVCOOP_MUT=1 no desync
  # dump, 2 no sink recording, 3 ini ignored. It links the PRODUCTION coop core (its own twins 1..5 share FNVCOOP_MUT numbers and must stay off).
  add_executable(flight_runtime_tests ${FLIGHT_DIR}/tests/test_flight_runtime.cpp)
  target_link_libraries(flight_runtime_tests PRIVATE nvcoop_coop_core nvcoop_flight_service)   # PRODUCTION coop core: twins 1..3 here must not also trip the coop twins
  target_include_directories(flight_runtime_tests PRIVATE ${FLIGHT_DIR}/../coop/tests ${FLIGHT_REPO}/tools/mut)
  flight_flags(flight_runtime_tests)
  target_compile_definitions(flight_runtime_tests PRIVATE FNVCOOP_RUNTIME_MUT)
  foreach(cfg "" _DEBUG _RELEASE _RELWITHDEBINFO _MINSIZEREL)
    set_target_properties(flight_runtime_tests PROPERTIES RUNTIME_OUTPUT_DIRECTORY${cfg} "${FLIGHT_REPO}/out/T1a")
  endforeach()
  if(NOT FLIGHT_STANDALONE)
    set_target_properties(flight_runtime_tests PROPERTIES EXCLUDE_FROM_ALL TRUE)
  endif()
endif()
if(FLIGHT_STANDALONE)
elseif(TARGET nvcoop)
  target_sources(nvcoop PRIVATE ${FLIGHT_DIR}/flight_module.cpp ${FLIGHT_DIR}/flight_service.cpp)   # NVCOOP_MODULE must be a direct source
  target_include_directories(nvcoop PRIVATE ${FLIGHT_DIR}/.. ${FLIGHT_REPO}/synccore)
  target_compile_definitions(nvcoop PRIVATE NVCOOP_WITH_FLIGHT=1)   # coop_module.cpp hands the recorder to the Runtime
  if(TARGET nvcoop_coop_synccore)
    target_link_libraries(nvcoop PRIVATE nvcoop_coop_synccore)   # contains synccore/flight/flight.cpp
  endif()
endif()
