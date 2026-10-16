#include "resolve.h"

#include <cstdio>
#include <cstring>

#include "../sigscan.h"
#include "sites.h"

namespace nvmp::hook {

namespace {
std::string Fmt(const char* f, unsigned a = 0, unsigned b = 0, unsigned c = 0) {
    char buf[256];
    std::snprintf(buf, sizeof(buf), f, a, b, c);
    return buf;
}
}  // namespace

bool ResolveSites(const uint8_t* image, size_t size, uintptr_t loadBase, ResolvedSites& out, std::vector<std::string>& log) {
    out = ResolvedSites{};
    auto locate = [&](const Site& s, uint32_t* rva) {
        ScanResult r = ScanImage(image, size, s.sig);
        if (!r.ok()) {
            log.push_back(std::string("SIG ") + s.name + " FAIL (" + ToString(r.status) + ", " + std::to_string(r.matches) + " matches)");
            return false;
        }
        *rva = (uint32_t)r.offset;
        log.push_back(std::string("SIG ") + s.name + Fmt(" ok rva=0x%X", *rva));
        return true;
    };

    if (!locate(kEnc, &out.encRva)) return false;
    locate(kResel, &out.reselRva);

    // character lookup + manager global: through the call inside the encounter handler
    if (out.encRva + kEncCallsFindChar + 5 <= size && image[out.encRva + kEncCallsFindChar] == 0xE8) {
        int32_t rel = 0;
        std::memcpy(&rel, image + out.encRva + kEncCallsFindChar + 1, 4);
        int64_t fn = (int64_t)out.encRva + (int64_t)kEncCallsFindChar + 5 + rel;
        if (fn > 0 && (size_t)fn + 70 < size && image[fn + kFindCharGlobalImm - 1] == 0xA1) {
            out.findCharRva = (uint32_t)fn;
            std::memcpy(&out.charMgrGlobal, image + fn + kFindCharGlobalImm, 4);
            log.push_back(Fmt("character lookup rva=0x%X, manager global=0x%08X", out.findCharRva, out.charMgrGlobal));
        } else {
            log.push_back("character lookup function has an unexpected shape");
        }
    } else {
        log.push_back("encounter handler does not call the character lookup where expected");
    }

    // connection-accept handler: pick the structural twin whose string starts with kConn.strPrefix
    {
        std::vector<size_t> hits = ScanImageAll(image, size, kConn.sig, 4);
        out.connCandidates = (int)hits.size();
        int found = 0;
        size_t chosen = 0;
        for (size_t h : hits) {
            uint32_t strVa = 0;
            std::memcpy(&strVa, image + h + kConn.strImm, 4);
            if (strVa < loadBase) continue;
            size_t o = strVa - loadBase;
            if (o + 32 >= size) continue;
            if (std::strncmp((const char*)image + o, kConn.strPrefix, std::strlen(kConn.strPrefix)) == 0) {
                chosen = h;
                ++found;
            }
        }
        log.push_back(Fmt("SIG connection accept: %u candidates, %u match the string", (unsigned)hits.size(), (unsigned)found));
        if (found == 1) {
            out.connRva = (uint32_t)chosen;
            int32_t rel = 0;
            std::memcpy(&rel, image + chosen + 31, 4);
            out.addrFnRva = (uint32_t)((int64_t)chosen + 35 + rel);
            log.push_back(Fmt("connection accept rva=0x%X, address formatter rva=0x%X", out.connRva, out.addrFnRva));
        }
    }
    return true;
}

}  // namespace nvmp::hook
