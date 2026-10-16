// IDA-style pattern scanner ("8B 4D ?? 0F 85 ...") over a PE image.
// Fail-safe contract: a scan only succeeds when the pattern matches EXACTLY once.
// 0 matches -> NotFound, >1 matches -> Ambiguous. Callers must treat both as "do not patch".
// No Windows headers: PE headers are parsed by hand so the same code runs on a mapped
// module (in-process) and on a file expanded by MapPeFileToImage (tests/tools).
#pragma once
#include <cstddef>
#include <cstdint>
#include <string>
#include <string_view>
#include <vector>

namespace nvmp {

struct Pattern {
    std::vector<uint8_t> bytes;
    std::vector<uint8_t> mask;  // 0xFF = must match, 0x00 = wildcard
    size_t size() const { return bytes.size(); }
};

// Tokens: two hex digits, or "?" / "??" for a wildcard. Whitespace separated.
// Rejects empty patterns, bad tokens and patterns that start or end with a wildcard
// (a leading/trailing wildcard makes the reported offset meaningless).
bool ParsePattern(std::string_view text, Pattern& out, std::string* err = nullptr);

enum class ScanStatus { Ok, NotFound, Ambiguous, BadPattern };

struct ScanResult {
    ScanStatus status = ScanStatus::NotFound;
    size_t offset = 0;   // Ok only: offset of the match (buffer offset, or RVA for ScanImage)
    size_t matches = 0;  // number of matches seen (stops at 2)
    bool ok() const { return status == ScanStatus::Ok; }
};

const char* ToString(ScanStatus s);

ScanResult ScanBuffer(const uint8_t* data, size_t size, const Pattern& pat);
ScanResult ScanBuffer(const uint8_t* data, size_t size, std::string_view pattern);

struct SectionInfo {
    std::string name;
    uint32_t rva = 0;
    uint32_t virtualSize = 0;
    uint32_t rawOffset = 0;
    uint32_t rawSize = 0;
    uint32_t characteristics = 0;
    bool executable() const { return (characteristics & 0x20000000u) != 0; }  // IMAGE_SCN_MEM_EXECUTE
};

// Parse headers of a mapped image (or any buffer that starts with the DOS header). PE32 only.
bool ParseSections(const uint8_t* image, size_t imageSize, std::vector<SectionInfo>& out,
                   uint32_t* imageBase = nullptr, uint32_t* sizeOfImage = nullptr);

// Scan every executable section of a MAPPED image (RVA layout). Result offset is an RVA.
// A pattern must be unique across ALL executable sections combined.
ScanResult ScanImage(const uint8_t* image, size_t imageSize, const Pattern& pat);
ScanResult ScanImage(const uint8_t* image, size_t imageSize, std::string_view pattern);

// All matches (RVAs) across executable sections, up to `maxMatches`. Only for callers that disambiguate
// structurally-identical twins by another property (e.g. a referenced string); never used for patching.
std::vector<size_t> ScanImageAll(const uint8_t* image, size_t imageSize, std::string_view pattern, size_t maxMatches = 8);

// Expand a PE file read from disk into a mapped-image-shaped buffer (sections at their RVAs).
bool MapPeFileToImage(const std::vector<uint8_t>& file, std::vector<uint8_t>& image);

}  // namespace nvmp
