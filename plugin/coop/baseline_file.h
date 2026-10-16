// Binary file form of a baseline dump: [wire archive: u64 contentHash, ShadowState]. Game-free (L4 wire io).
// Written by the in-game bridge method coop.baseline.export, read by coop_peer (--baseline-file) so a fake client can
// join a real host with a deliberately different world.
#pragma once
#include <cstdio>
#include <string>
#include <vector>

#include "fnvcoop/shadow.h"
#include "fnvcoop/wire.h"

namespace nvcoop::coop {

inline bool SaveBaselineFile(const std::string& path, const fnvcoop::ShadowState& st, uint64_t hash) {
    fnvcoop::Archive a = fnvcoop::Archive::writer();
    uint64_t h = hash;
    a.u64(h);
    fnvcoop::ShadowState copy = st;
    fnvcoop::io(a, copy);
    std::vector<uint8_t> b = a.finish();
    FILE* f = std::fopen(path.c_str(), "wb");
    if (!f) return false;
    bool ok = std::fwrite(b.data(), 1, b.size(), f) == b.size();
    std::fclose(f);
    return ok;
}

inline bool LoadBaselineFile(const std::string& path, fnvcoop::ShadowState* st, uint64_t* hash) {
    FILE* f = std::fopen(path.c_str(), "rb");
    if (!f) return false;
    std::vector<uint8_t> b;
    uint8_t buf[65536];
    size_t n;
    while ((n = std::fread(buf, 1, sizeof buf, f)) > 0) b.insert(b.end(), buf, buf + n);
    std::fclose(f);
    fnvcoop::Archive a = fnvcoop::Archive::reader(b.data(), b.size());
    uint64_t h = 0;
    a.u64(h);
    fnvcoop::ShadowState s;
    fnvcoop::io(a, s);
    if (!a.ok() || !a.atEnd()) return false;
    *st = std::move(s);
    *hash = h;
    return true;
}

}  // namespace nvcoop::coop
