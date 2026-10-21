# Precompiled headers for the heavy standard headers that nearly every TU in this repo includes (variant/map/functional/string...).
# Usage in a suite's CMakeLists.txt (safe when the file is also used standalone):
#     if(COMMAND fnvcoop_pch)
#       fnvcoop_pch(my_target)
#     endif()
# Only worth it for targets with several sources (the PCH itself costs one extra compile per target). MSVC only; a no-op elsewhere
# or when -DFNVCOOP_PCH=OFF (build.py sets that automatically when sccache is used).
function(fnvcoop_pch tgt)
  if(NOT FNVCOOP_PCH OR NOT MSVC)
    return()
  endif()
  get_target_property(_srcs ${tgt} SOURCES)
  set(_n 0)
  foreach(_s ${_srcs})
    if(_s MATCHES "\\.(cpp|cc|cxx)$")
      math(EXPR _n "${_n} + 1")
    endif()
  endforeach()
  if(_n LESS 3)
    return()
  endif()
  target_precompile_headers(${tgt} PRIVATE
    <algorithm> <array> <cstdint> <cstdio> <cstring> <functional> <map> <memory> <optional> <set> <string> <unordered_map>
    <utility> <variant> <vector>)
endfunction()
