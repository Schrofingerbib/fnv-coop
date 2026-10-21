# synccore/areadigest: per-area desync digests, targeted diff and heal (FULL-SYNC-PLAN section 4), game-free.
# Included from synccore/CMakeLists.txt (after enable_testing() and after flight/, whose recorder the counters are exported to).
#   fnvcoop_areadigest     production library (twins compiled out): what plugin/areadigest links
#   fnvcoop_areadigest_rt  the same sources with FNVCOOP_RUNTIME_MUT: the test exe picks a mutation twin with FNVCOOP_MUT=<n> at run time
#   areadigest_tests       unit tests + session rig + storm; twins 1..40 must each fail it
set(AD_MUT_INC ${CMAKE_CURRENT_LIST_DIR}/../../tools/mut)
foreach(sfx "" "_rt")
  add_library(fnvcoop_areadigest${sfx} STATIC ${CMAKE_CURRENT_LIST_DIR}/areadigest.cpp ${CMAKE_CURRENT_LIST_DIR}/ad_wire.cpp)
  target_include_directories(fnvcoop_areadigest${sfx} PUBLIC ${CMAKE_CURRENT_LIST_DIR}/.. ${AD_MUT_INC})
  target_link_libraries(fnvcoop_areadigest${sfx} PUBLIC fnvcoop)
  if(sfx STREQUAL "_rt")
    target_compile_definitions(fnvcoop_areadigest${sfx} PUBLIC FNVCOOP_RUNTIME_MUT)
  endif()
  if(MSVC)
    target_compile_options(fnvcoop_areadigest${sfx} PRIVATE /W4 /permissive- /utf-8 /wd4456 /D_CRT_SECURE_NO_WARNINGS)
  else()
    target_compile_options(fnvcoop_areadigest${sfx} PRIVATE -Wall -Wextra)
  endif()
endforeach()

add_executable(areadigest_tests tests/test_core.cpp tests/test_sync.cpp tests/test_storm.cpp tests/test_flight_export.cpp
               ${CMAKE_CURRENT_LIST_DIR}/../tests/main.cpp)
target_include_directories(areadigest_tests PRIVATE ${CMAKE_CURRENT_LIST_DIR}/../tests ${CMAKE_CURRENT_LIST_DIR}/../flight)
target_link_libraries(areadigest_tests PRIVATE fnvcoop_areadigest_rt fnvcoop_flight)
if(MSVC)
  target_compile_options(areadigest_tests PRIVATE /W4 /permissive- /utf-8 /wd4456 /D_CRT_SECURE_NO_WARNINGS)
else()
  target_compile_options(areadigest_tests PRIVATE -Wall -Wextra)
endif()
if(NOT CMAKE_RUNTIME_OUTPUT_DIRECTORY)
  foreach(cfg "" _DEBUG _RELEASE _RELWITHDEBINFO _MINSIZEREL)
    set_target_properties(areadigest_tests PROPERTIES RUNTIME_OUTPUT_DIRECTORY${cfg} "${CMAKE_BINARY_DIR}/areadigest_tests")
  endforeach()
endif()

add_test(NAME areadigest_unit COMMAND areadigest_tests)
foreach(v 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30 31 32 34 35 36 37 38 39 41 42)
  add_test(NAME areadigest_mut${v} COMMAND areadigest_tests)
  set_tests_properties(areadigest_mut${v} PROPERTIES WILL_FAIL TRUE ENVIRONMENT "FNVCOOP_MUT=${v}")
endforeach()
