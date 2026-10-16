#include "sigscan.h"

#include <cstring>

namespace nvmp {

namespace {
int HexVal(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}
bool IsSpace(char c) { return c == ' ' || c == '\t' || c == '\n' || c == '\r'; }
template <class T> bool Rd(const uint8_t* p, size_t size, size_t off, T& v) {
    if (off + sizeof(T) > size) return false;
    std::memcpy(&v, p + off, sizeof(T));
    return true;
}
}  // namespace

const char* ToString(ScanStatus s) {
    switch (s) {
        case ScanStatus::Ok: return "ok";
        case ScanStatus::NotFound: return "not-found";
        case ScanStatus::Ambiguous: return "ambiguous";
        case ScanStatus::BadPattern: return "bad-pattern";
    }
    return "?";
}

bool ParsePattern(std::string_view text, Pattern& out, std::string* err) {
    auto fail = [&](const char* m) { if (err) *err = m; return false; };
    out.bytes.clear();
    out.mask.clear();
    size_t i = 0;
    while (i < text.size()) {
        while (i < text.size() && IsSpace(text[i])) ++i;
        if (i >= text.size()) break;
        size_t j = i;
        while (j < text.size() && !IsSpace(text[j])) ++j;
        std::string_view tok = text.substr(i, j - i);
        i = j;
        if (tok == "?" || tok == "??") {
            out.bytes.push_back(0);
            out.mask.push_back(0x00);
        } else if (tok.size() == 2 && HexVal(tok[0]) >= 0 && HexVal(tok[1]) >= 0) {
            out.bytes.push_back(static_cast<uint8_t>(HexVal(tok[0]) * 16 + HexVal(tok[1])));
            out.mask.push_back(0xFF);
        } else {
            return fail("bad token");
        }
    }
    if (out.bytes.empty()) return fail("empty pattern");
    if (out.mask.front() == 0x00 || out.mask.back() == 0x00) return fail("pattern must start and end with a fixed byte");
    return true;
}

ScanResult ScanBuffer(const uint8_t* data, size_t size, const Pattern& pat) {
    ScanResult r;
    if (pat.size() == 0 || pat.bytes.size() != pat.mask.size() || pat.mask[0] == 0) {
        r.status = ScanStatus::BadPattern;
        return r;
    }
    if (size < pat.size()) return r;
    const size_t n = pat.size();
    const uint8_t first = pat.bytes[0];
    const size_t last = size - n;
    for (size_t i = 0; i <= last; ++i) {
        if (data[i] != first) continue;
        size_t k = 1;
        for (; k < n; ++k)
            if ((data[i + k] & pat.mask[k]) != pat.bytes[k]) break;
        if (k != n) continue;
        if (++r.matches == 1) r.offset = i;
#ifndef SIGSCAN_MUTANT_ACCEPT_AMBIGUOUS
        if (r.matches >= 2) { r.status = ScanStatus::Ambiguous; return r; }
#endif
    }
    r.status = r.matches ? ScanStatus::Ok : ScanStatus::NotFound;
    return r;
}

ScanResult ScanBuffer(const uint8_t* data, size_t size, std::string_view pattern) {
    Pattern p;
    if (!ParsePattern(pattern, p)) { ScanResult r; r.status = ScanStatus::BadPattern; return r; }
    return ScanBuffer(data, size, p);
}

bool ParseSections(const uint8_t* image, size_t imageSize, std::vector<SectionInfo>& out,
                   uint32_t* imageBase, uint32_t* sizeOfImage) {
    out.clear();
    uint16_t mz = 0;
    if (!Rd(image, imageSize, 0, mz) || mz != 0x5A4D) return false;
    uint32_t pe = 0;
    if (!Rd(image, imageSize, 0x3C, pe)) return false;
    uint32_t sig = 0;
    if (!Rd(image, imageSize, pe, sig) || sig != 0x00004550) return false;
    uint16_t nsec = 0, optSize = 0, magic = 0;
    if (!Rd(image, imageSize, pe + 6, nsec) || !Rd(image, imageSize, pe + 20, optSize)) return false;
    if (!Rd(image, imageSize, pe + 24, magic) || magic != 0x10B) return false;  // PE32 only (x86)
    if (imageBase) Rd(image, imageSize, pe + 24 + 28, *imageBase);
    if (sizeOfImage) Rd(image, imageSize, pe + 24 + 56, *sizeOfImage);
    size_t off = static_cast<size_t>(pe) + 24 + optSize;
    for (uint16_t i = 0; i < nsec; ++i, off += 40) {
        if (off + 40 > imageSize) return false;
        SectionInfo s;
        char nm[9] = {};
        std::memcpy(nm, image + off, 8);
        s.name = nm;
        Rd(image, imageSize, off + 8, s.virtualSize);
        Rd(image, imageSize, off + 12, s.rva);
        Rd(image, imageSize, off + 16, s.rawSize);
        Rd(image, imageSize, off + 20, s.rawOffset);
        Rd(image, imageSize, off + 36, s.characteristics);
        out.push_back(s);
    }
    return true;
}

ScanResult ScanImage(const uint8_t* image, size_t imageSize, const Pattern& pat) {
    ScanResult total;
    std::vector<SectionInfo> secs;
    if (!ParseSections(image, imageSize, secs)) { total.status = ScanStatus::BadPattern; return total; }
    for (const auto& s : secs) {
        if (!s.executable() || s.rva >= imageSize) continue;
        size_t len = s.virtualSize ? s.virtualSize : s.rawSize;
        if (s.rva + len > imageSize) len = imageSize - s.rva;
        ScanResult r = ScanBuffer(image + s.rva, len, pat);
        if (r.status == ScanStatus::BadPattern) return r;
        if (r.status == ScanStatus::Ambiguous) { total.status = ScanStatus::Ambiguous; total.matches = 2; return total; }
        if (r.status == ScanStatus::Ok) {
            if (total.matches == 0) total.offset = s.rva + r.offset;
            total.matches += r.matches;
        }
    }
#ifdef SIGSCAN_MUTANT_ACCEPT_AMBIGUOUS
    total.status = total.matches ? ScanStatus::Ok : ScanStatus::NotFound;
#else
    total.status = total.matches == 0 ? ScanStatus::NotFound
                                      : (total.matches == 1 ? ScanStatus::Ok : ScanStatus::Ambiguous);
#endif
    return total;
}

ScanResult ScanImage(const uint8_t* image, size_t imageSize, std::string_view pattern) {
    Pattern p;
    if (!ParsePattern(pattern, p)) { ScanResult r; r.status = ScanStatus::BadPattern; return r; }
    return ScanImage(image, imageSize, p);
}

std::vector<size_t> ScanImageAll(const uint8_t* image, size_t imageSize, std::string_view pattern, size_t maxMatches) {
    std::vector<size_t> out;
    Pattern p;
    std::vector<SectionInfo> secs;
    if (!ParsePattern(pattern, p) || !ParseSections(image, imageSize, secs)) return out;
    for (const auto& s : secs) {
        if (!s.executable() || s.rva >= imageSize) continue;
        size_t len = s.virtualSize ? s.virtualSize : s.rawSize;
        if (s.rva + len > imageSize) len = imageSize - s.rva;
        if (len < p.size()) continue;
        for (size_t i = 0; i + p.size() <= len && out.size() < maxMatches; ++i) {
            const uint8_t* d = image + s.rva + i;
            if (d[0] != p.bytes[0]) continue;
            size_t k = 1;
            for (; k < p.size(); ++k)
                if ((d[k] & p.mask[k]) != p.bytes[k]) break;
            if (k == p.size()) out.push_back(s.rva + i);
        }
    }
    return out;
}

bool MapPeFileToImage(const std::vector<uint8_t>& file, std::vector<uint8_t>& image) {
    std::vector<SectionInfo> secs;
    uint32_t soi = 0;
    if (!ParseSections(file.data(), file.size(), secs, nullptr, &soi) || soi == 0) return false;
    image.assign(soi, 0);
    size_t hdr = soi < 0x1000 ? soi : 0x1000;
    std::memcpy(image.data(), file.data(), hdr < file.size() ? hdr : file.size());
    for (const auto& s : secs) {
        if (s.rawOffset >= file.size() || s.rva >= soi) continue;
        size_t n = s.rawSize;
        if (s.rawOffset + n > file.size()) n = file.size() - s.rawOffset;
        if (s.rva + n > soi) n = soi - s.rva;
        std::memcpy(image.data() + s.rva, file.data() + s.rawOffset, n);
    }
    return true;
}

}  // namespace nvmp
