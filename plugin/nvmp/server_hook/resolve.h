// Locates every hook site and helper in a mapped nvmp_storyserver.exe image (in-process module or a PE file
// expanded by MapPeFileToImage). Pure memory reads, no Windows API: used by the DLL and by tests against the
// real 9.4 binary.
#pragma once
#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

namespace nvmp::hook {

struct ResolvedSites {
    uint32_t encRva = 0;        // encounter handler entry
    uint32_t reselRva = 0;      // hot-swap owner reselect entry
    uint32_t connRva = 0;       // connection-accept handler entry (0 = not found / not disambiguated)
    uint32_t addrFnRva = 0;     // sockaddr -> string formatter called by the accept handler
    uint32_t findCharRva = 0;   // character lookup-by-id (reached through the encounter handler)
    uint32_t charMgrGlobal = 0; // absolute address (as stored in code) of the global pointer to the character manager
    int connCandidates = 0;     // structural twins found for the accept handler
};

// `loadBase` is the address the image is mapped at as seen by absolute pointers inside it
// (module base in-process; the PE ImageBase for an unrelocated file image).
// Returns false only if the REQUIRED encounter handler is missing; optional parts are reported in `log`.
bool ResolveSites(const uint8_t* image, size_t imageSize, uintptr_t loadBase, ResolvedSites& out, std::vector<std::string>& log);

}  // namespace nvmp::hook
