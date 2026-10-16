# L6 nvmp: signature-guarded NV:MP patches. Picked up by plugin/CMakeLists.txt (add_subdirectory(nvmp)).
# Links the client-side patcher into nvcoop.dll; everything is dormant until NV:MP's client.dll is loaded
# in the process and the first frames run (see client_patches/client_patches_module.cpp for the [nvmp] ini keys).
# The server hook (server_hook/) is a separate x86 DLL built by server_hook/run_tests.cmd, not part of nvcoop.
cmake_minimum_required(VERSION 3.20)

add_library(nvcoop_nvmp STATIC
  ${CMAKE_CURRENT_LIST_DIR}/sigscan.cpp
  ${CMAKE_CURRENT_LIST_DIR}/patches.cpp
  ${CMAKE_CURRENT_LIST_DIR}/client_patches/client_patcher.cpp
  ${CMAKE_CURRENT_LIST_DIR}/client_patches/players.cpp)
target_include_directories(nvcoop_nvmp PUBLIC ${CMAKE_CURRENT_LIST_DIR})
target_compile_definitions(nvcoop_nvmp PRIVATE _CRT_SECURE_NO_WARNINGS)
if(MSVC)
  target_compile_options(nvcoop_nvmp PRIVATE /W4 /EHsc /permissive- /utf-8)
endif()

if(TARGET nvcoop)
  # The module file registers itself with NVCOOP_MODULE (static registrar), so it is compiled into nvcoop itself.
  target_sources(nvcoop PRIVATE ${CMAKE_CURRENT_LIST_DIR}/client_patches/client_patches_module.cpp)
  target_link_libraries(nvcoop PRIVATE nvcoop_nvmp)
endif()
