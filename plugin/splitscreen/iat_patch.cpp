#include "iat_patch.h"

#include <cctype>
#include <cstring>

#include "fnvcoop_mut.h"

// Runtime mutation twins (FNVCOOP_MUT=<n>), numbered after pad_router.cpp (12..20):
//   21 = hook() accepts an unbound slot (value 0): the original would be null and the first call crashes
//   22 = unhook() restores the original even when another patcher chained over the slot
//   23 = hooking the same slot twice records our own detour as the original (the detour would call itself)
//   24 = EnumImports stops after the first DLL
//   25 = FindImportSlots matches every function of the DLL (name test dropped)
//   26 = allIntact() always says yes
namespace nvsplit {
namespace {
bool Rd(const uint8_t* img, size_t size, size_t off, void* dst, size_t n) {
    if (off > size || n > size - off) return false;
    std::memcpy(dst, img + off, n);
    return true;
}
bool ReadStr(const uint8_t* img, size_t size, size_t off, std::string* s) {
    s->clear();
    for (size_t i = off; i < size && i - off < 256; ++i) {
        if (img[i] == 0) return true;
        s->push_back(static_cast<char>(img[i]));
    }
    return false;
}
std::string Lower(std::string s) {
    for (char& c : s) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    return s;
}
}  // namespace

bool EnumImports(const uint8_t* image, size_t size, std::vector<ImportRef>* out, std::string* err) {
    auto fail = [&](const char* m) { if (err) *err = m; return false; };
    if (!image || !out) return fail("no image");
    uint16_t mz = 0;
    uint32_t lfanew = 0, sig = 0;
    if (!Rd(image, size, 0, &mz, 2) || mz != 0x5A4D) return fail("not an MZ image");
    if (!Rd(image, size, 0x3C, &lfanew, 4) || !Rd(image, size, lfanew, &sig, 4) || sig != 0x4550) return fail("no PE signature");
    uint16_t magic = 0;
    const size_t opt = static_cast<size_t>(lfanew) + 24;
    if (!Rd(image, size, opt, &magic, 2) || magic != 0x10B) return fail("not a PE32 image");
    uint32_t impRva = 0, impSize = 0;
    if (!Rd(image, size, opt + 96 + 8, &impRva, 4) || !Rd(image, size, opt + 96 + 12, &impSize, 4)) return fail("no data directory");
    if (impRva == 0) return true;  // no imports
    for (uint32_t d = 0; d < 4096; ++d) {
        uint32_t desc[5];
        if (!Rd(image, size, static_cast<size_t>(impRva) + d * 20, desc, 20)) return fail("import descriptor out of range");
        const uint32_t oft = desc[0], nameRva = desc[3], ft = desc[4];
        if (nameRva == 0 && ft == 0) break;
        if (FNV_MUT(24) && d > 0) break;
        std::string dll;
        if (!ReadStr(image, size, nameRva, &dll)) return fail("import dll name out of range");
        const uint32_t lookup = oft ? oft : ft;
        for (uint32_t i = 0; i < 16384; ++i) {
            uint32_t thunk = 0, value = 0;
            if (!Rd(image, size, static_cast<size_t>(lookup) + i * 4, &thunk, 4)) return fail("import thunk out of range");
            if (thunk == 0) break;
            if (!Rd(image, size, static_cast<size_t>(ft) + i * 4, &value, 4)) return fail("IAT slot out of range");
            ImportRef r;
            r.dll = dll;
            r.slotRva = ft + i * 4;
            r.value = value;
            if (thunk & 0x80000000u) {
                r.byOrdinal = true;
                r.ordinal = static_cast<uint16_t>(thunk & 0xFFFF);
            } else if (!ReadStr(image, size, static_cast<size_t>(thunk) + 2, &r.name)) {
                return fail("import name out of range");
            }
            out->push_back(r);
        }
    }
    return true;
}

std::vector<uint8_t> MapPeFile(const uint8_t* file, size_t n, std::string* err) {
    std::vector<uint8_t> none;
    auto fail = [&](const char* m) { if (err) *err = m; return none; };
    uint32_t lfanew = 0;
    uint16_t nsec = 0, optSize = 0;
    uint32_t sizeOfImage = 0, sizeOfHeaders = 0;
    if (!Rd(file, n, 0x3C, &lfanew, 4) || !Rd(file, n, lfanew + 6, &nsec, 2) || !Rd(file, n, lfanew + 20, &optSize, 2)) return fail("bad PE header");
    const size_t opt = static_cast<size_t>(lfanew) + 24;
    if (!Rd(file, n, opt + 56, &sizeOfImage, 4) || !Rd(file, n, opt + 60, &sizeOfHeaders, 4)) return fail("bad optional header");
    if (sizeOfImage == 0 || sizeOfImage > (1u << 28)) return fail("odd SizeOfImage");
    std::vector<uint8_t> img(sizeOfImage, 0);
    if (sizeOfHeaders > n || sizeOfHeaders > sizeOfImage) return fail("bad SizeOfHeaders");
    std::memcpy(img.data(), file, sizeOfHeaders);
    const size_t secs = opt + optSize;
    for (uint16_t i = 0; i < nsec; ++i) {
        uint32_t s[10];
        if (!Rd(file, n, secs + i * 40, s, 40)) return fail("bad section table");
        const uint32_t vsize = s[2], va = s[3], rawSize = s[4], rawPtr = s[5];
        const uint32_t take = rawSize < vsize ? rawSize : vsize;
        if (take == 0) continue;
        if (rawPtr > n || take > n - rawPtr || va > sizeOfImage || take > sizeOfImage - va) return fail("section out of range");
        std::memcpy(img.data() + va, file + rawPtr, take);
    }
    return img;
}

std::vector<ImportRef> FindImportSlots(const std::vector<ImportRef>& all, const char* dllPrefix, const char* func, IExports* exports) {
    std::vector<ImportRef> hit;
    const std::string prefix = Lower(dllPrefix ? dllPrefix : "");
    for (const ImportRef& r : all) {
        if (Lower(r.dll).compare(0, prefix.size(), prefix) != 0) continue;
        bool match = !r.byOrdinal && r.name == func;
        if (!match && exports && r.value != 0) match = exports->addressOf(r.dll, func) == r.value;
        if (FNV_MUT(25)) match = true;
        if (match) hit.push_back(r);
    }
    return hit;
}

bool IatPatcher::hook(uint32_t slotAddr, uint32_t detour, uint32_t* origOut) {
    uint32_t cur = 0;
    if (!mem_.read32(slotAddr, &cur)) return false;
    if (slots_.count(slotAddr) && !FNV_MUT(23)) return false;  // already ours
    if (cur == 0 && !FNV_MUT(21)) return false;
    if (cur == detour && !FNV_MUT(23)) return false;  // a copy of this detour: never record it as the original
    if (!mem_.write32(slotAddr, detour)) return false;
    slots_[slotAddr] = Rec{detour, cur};
    if (origOut) *origOut = cur;
    return true;
}

bool IatPatcher::unhook(uint32_t slotAddr) {
    auto it = slots_.find(slotAddr);
    if (it == slots_.end()) return false;
    uint32_t cur = 0;
    if (!mem_.read32(slotAddr, &cur)) return false;
    if (cur != it->second.detour && !FNV_MUT(22)) return false;  // chained over: leave it, the caller must keep the detour alive
    if (!mem_.write32(slotAddr, it->second.orig)) return false;
    slots_.erase(it);
    return true;
}

bool IatPatcher::allIntact() {
    if (FNV_MUT(26)) return true;
    for (const auto& kv : slots_) {
        uint32_t cur = 0;
        if (!mem_.read32(kv.first, &cur) || cur != kv.second.detour) return false;
    }
    return true;
}

int IatPatcher::unhookAll() {
    int failed = 0;
    std::vector<uint32_t> keys;
    for (const auto& kv : slots_) keys.push_back(kv.first);
    for (uint32_t k : keys) {
        if (!unhook(k)) ++failed;
    }
    return failed;
}

}  // namespace nvsplit
