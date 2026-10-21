// Wire format primitives: compact, little-endian, versioned, fuzz-safe.
//
// One symmetric Archive class is used for both directions: every io(Archive&, T&) function reads when the
// archive is a reader and writes when it is a writer, so encode/decode can never drift apart.
// Integers are LEB128 varints (signed = zigzag); floats are 4-byte LE and must be finite; strings and
// vectors are length-prefixed and bounds-checked against both a hard cap and the bytes remaining.
// FormRef plugin names go through a per-message string table (written ahead of the body).
// After any failure the archive is poisoned: ok() == false, reads return zero values, writes are no-ops.
#pragma once
#include <cstddef>
#include <cstdint>
#include <map>
#include <string>
#include <vector>
#include "fnvcoop/effect.h"
#include "fnvcoop/policy.h"

namespace fnvcoop {

constexpr uint8_t kWireVersion = 6;
// Hello / Reject use a version-independent envelope byte so that two incompatible builds can still tell each other WHY
// they cannot play together instead of silently dropping each other's datagrams.
constexpr uint8_t kHandshakeEnvelope = 0xA1;
constexpr uint16_t kProtocolVersion = 6;
uint64_t hash64(const void* data, size_t size, uint64_t seed = 1469598103934665603ull);   // FNV-1a
constexpr size_t kMaxEffectsPerTxn = 1024;
constexpr size_t kMaxTxnsPerBatch = 256;
constexpr size_t kMaxStringLen = 255;
constexpr size_t kMaxMessageBytes = 8u << 20;

class Archive {
public:
    static Archive writer();
    // Parses the string table that prefixes `data` (see finish()); check ok() afterwards.
    static Archive reader(const uint8_t* data, size_t size);

    bool reading() const { return reading_; }
    bool ok() const { return ok_; }
    void fail() { ok_ = false; }
    size_t remaining() const { return reading_ ? size_ - pos_ : 0; }
    bool atEnd() const { return reading_ && pos_ == size_; }

    void u8(uint8_t& v);
    void u16(uint16_t& v);
    void u32(uint32_t& v);
    void u64(uint64_t& v);
    void varu(uint64_t& v);
    void vari(int64_t& v);
    void varu32(uint32_t& v);
    void vari32(int32_t& v);
    void f32(float& v);
    void f64(double& v);
    void boolean(bool& v);
    void str(std::string& s, size_t maxLen = kMaxStringLen);
    void bytes(std::vector<uint8_t>& b, size_t maxLen);
    // Element count of a container. Reading validates n <= maxN and n <= remaining() (every element is >= 1 byte).
    bool count(size_t& n, size_t maxN);

    // String-table interning for FormRef plugin names.
    void pluginRef(std::string& plugin);

    // Writer only: returns [string table][body].
    std::vector<uint8_t> finish() const;

private:
    bool reading_ = false;
    bool ok_ = true;
    std::vector<uint8_t> out_;
    const uint8_t* in_ = nullptr;
    size_t size_ = 0, pos_ = 0;
    std::vector<std::string> table_;
    std::map<std::string, uint32_t> tableIndex_;
};

uint32_t crc32(const uint8_t* data, size_t size);

// Symmetric field serializers.
void io(Archive& a, FormRef& v);
void io(Archive& a, Recipient& v);
void io(Archive& a, Cause& v);
void io(Archive& a, Effect& v);
void io(Archive& a, EffectTxn& v);
void io(Archive& a, SharePolicy& v);

}  // namespace fnvcoop
