// Raw memory read for engine-address verification. Always built (it has no hook dependencies), registered as its own
// nvcoop module so it is available even when the rest of the world glue is not.
//   world.peek {addr: "0x5D8E20", len: 64}  -> {addr, hex}      (len 1..4096, SEH-protected)
#include <Windows.h>

#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

#include "common/minijson.h"
#include "common/nvcoop_api.h"

namespace nvworld {
bool peekMemory(uint32_t addr, void* out, size_t n) {
    __try {
        std::memcpy(out, reinterpret_cast<const void*>(addr), n);
        return true;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return false;
    }
}
}  // namespace nvworld

namespace {
bool MPeek(const std::string& params, std::string& out) {
    nvcoop::json::Value p;
    std::string err;
    if (!nvcoop::json::parse(params, p, err)) { out = "bad params: " + err; return false; }
    int64_t len = p.get("len").asInt(16);
    if (len < 1 || len > 4096) { out = "len must be 1..4096"; return false; }
    uint32_t addr = p.get("addr").isNumber() ? static_cast<uint32_t>(p.get("addr").asInt())
                                             : static_cast<uint32_t>(std::strtoul(p.get("addr").asString("").c_str(), nullptr, 0));
    std::vector<uint8_t> buf(static_cast<size_t>(len));
    if (!nvworld::peekMemory(addr, buf.data(), buf.size())) { out = "unreadable"; return false; }
    static const char* d = "0123456789abcdef";
    std::string hex;
    for (uint8_t b : buf) { hex += d[b >> 4]; hex += d[b & 15]; }
    nvcoop::json::Value o = nvcoop::json::Value::object();
    o.set("addr", (int64_t)addr).set("hex", hex);
    out = o.dump();
    return true;
}
void OnLoad(void*) { nvcoop::RegisterMethod("world.peek", MPeek, true); }
}  // namespace

NVCOOP_MODULE(l12_peek, "l12_peek", OnLoad, nullptr, nullptr);
