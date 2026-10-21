#include "wt_wire.h"

#include <cmath>
#include <cstring>

#include "fnvcoop_mut.h"

namespace nvwt {

uint32_t crc32Ieee(const uint8_t* data, size_t size) {
    static uint32_t table[256];
    static bool init = false;
    if (!init) {
        for (uint32_t i = 0; i < 256; ++i) {
            uint32_t c = i;
            for (int k = 0; k < 8; ++k) c = (c & 1u) ? (0xEDB88320u ^ (c >> 1)) : (c >> 1);
            table[i] = c;
        }
        init = true;
    }
    uint32_t c = 0xFFFFFFFFu;
    for (size_t i = 0; i < size; ++i) c = table[(c ^ data[i]) & 0xFFu] ^ (c >> 8);
    return c ^ 0xFFFFFFFFu;
}

const char* decodeErrorName(DecodeError e) {
    switch (e) {
        case DecodeError::Ok: return "Ok";
        case DecodeError::TooShort: return "TooShort";
        case DecodeError::BadMagic: return "BadMagic";
        case DecodeError::BadVersion: return "BadVersion";
        case DecodeError::BadCrc: return "BadCrc";
        case DecodeError::BadType: return "BadType";
        case DecodeError::Malformed: return "Malformed";
    }
    return "?";
}

namespace {

constexpr size_t kMaxString = 96;

struct W {
    Bytes b;
    void u8(uint8_t v) { b.push_back(v); }
    void u32(uint32_t v) { for (int i = 0; i < 4; ++i) b.push_back(static_cast<uint8_t>(v >> (8 * i))); }
    void i32(int32_t v) { u32(static_cast<uint32_t>(v)); }
    void f32(float v) { uint32_t u; std::memcpy(&u, &v, 4); u32(u); }
    void f64(double v) { uint64_t u; std::memcpy(&u, &v, 8); u32(static_cast<uint32_t>(u)); u32(static_cast<uint32_t>(u >> 32)); }
    void str(const std::string& s) {
        const size_t n = s.size() > kMaxString ? kMaxString : s.size();
        u8(static_cast<uint8_t>(n));
        b.insert(b.end(), s.begin(), s.begin() + static_cast<std::ptrdiff_t>(n));
    }
    void ref(const FormRef& r) { str(r.plugin); u32(r.localId); }
};

struct R {
    const uint8_t* p;
    size_t n, i = 0;
    bool bad = false;
    R(const uint8_t* d, size_t sz) : p(d), n(sz) {}
    bool need(size_t k) { if (bad || n - i < k) { bad = true; return false; } return true; }
    uint8_t u8() { if (!need(1)) return 0; return p[i++]; }
    uint32_t u32() {
        if (!need(4)) return 0;
        uint32_t v = 0;
        for (int k = 0; k < 4; ++k) v |= static_cast<uint32_t>(p[i + k]) << (8 * k);
        i += 4;
        return v;
    }
    int32_t i32() { return static_cast<int32_t>(u32()); }
    float f32() { uint32_t u = u32(); float v; std::memcpy(&v, &u, 4); return v; }
    double f64() { uint64_t lo = u32(); uint64_t hi = u32(); uint64_t u = lo | (hi << 32); double v; std::memcpy(&v, &u, 8); return v; }
    std::string str() {
        const uint8_t len = u8();
        if (len > kMaxString || !need(len)) { bad = true; return std::string(); }
        std::string s(reinterpret_cast<const char*>(p + i), len);
        i += len;
        return s;
    }
    FormRef ref() { FormRef r; r.plugin = str(); r.localId = u32(); return r; }
    bool done() const { return !bad && i == n; }
};

bool finite(double v) { return std::isfinite(v); }

void putTime(W& w, const GameTime& t) {
    w.i32(t.year);
    w.u8(static_cast<uint8_t>(t.month));
    w.u8(static_cast<uint8_t>(t.day));
    w.f64(t.hour);
    w.f64(t.daysPassed);
}
bool getTime(R& r, GameTime* t) {
    t->year = r.i32();
    t->month = r.u8();
    t->day = r.u8();
    t->hour = r.f64();
    t->daysPassed = r.f64();
    if (r.bad) return false;
    if (FNV_MUT(113)) return finite(t->hour) && finite(t->daysPassed);   // MUTANT 113: an hour of 99 or a month of 40 is accepted
    return validTime(*t);
}

}  // namespace

Bytes encode(const Msg& m) {
    W w;
    w.u8(kChannelWorldTime);
    w.u8(kWireVersion);
    if (auto* x = std::get_if<Beacon>(&m)) {
        w.u8(static_cast<uint8_t>(MsgType::Beacon));
        w.u32(x->seq);
        putTime(w, x->time);
        w.f32(x->rate);
        w.f32(x->timeScale);
        w.u32(x->skipEpoch);
        w.u8(static_cast<uint8_t>((x->jump ? 1 : 0) | (x->partyTravel ? 2 : 0)));
    } else if (auto* x = std::get_if<SkipRequest>(&m)) {
        w.u8(static_cast<uint8_t>(MsgType::SkipRequest));
        w.u32(x->reqId);
        w.u8(static_cast<uint8_t>(x->kind));
        w.u32(x->minutes);
    } else if (auto* x = std::get_if<TravelRequest>(&m)) {
        w.u8(static_cast<uint8_t>(MsgType::TravelRequest));
        w.u32(x->reqId);
        w.ref(x->marker);
    } else if (auto* x = std::get_if<Verdict>(&m)) {
        w.u8(static_cast<uint8_t>(MsgType::Verdict));
        w.u32(x->reqId);
        w.u8(static_cast<uint8_t>(x->kind));
        w.u8(x->granted ? 1 : 0);
        w.u8(static_cast<uint8_t>(x->reason));
        w.u32(x->minutes);
        w.u8(x->party ? 1 : 0);
    } else if (auto* x = std::get_if<TravelGo>(&m)) {
        w.u8(static_cast<uint8_t>(MsgType::TravelGo));
        w.u32(x->reqId);
        w.ref(x->marker);
        w.u8(x->mover);
        w.u8(x->party ? 1 : 0);
    } else if (auto* x = std::get_if<Hello>(&m)) {
        w.u8(static_cast<uint8_t>(MsgType::Hello));
        w.u8(x->wireVersion);
    } else if (auto* x = std::get_if<Status>(&m)) {
        w.u8(static_cast<uint8_t>(MsgType::Status));
        w.u8(x->inCombat ? 1 : 0);
    }
    const uint32_t c = crc32Ieee(w.b.data(), w.b.size());
    w.u32(c);
    return std::move(w.b);
}

DecodeError decode(const uint8_t* d, size_t size, Msg* out) {
    if (size < 3 + 4) return DecodeError::TooShort;
    if (d[0] != kChannelWorldTime) return DecodeError::BadMagic;
    if (d[1] != kWireVersion && !FNV_MUT(112)) return DecodeError::BadVersion;   // MUTANT 112: any version byte is accepted
    if (!FNV_MUT(110)) {   // MUTANT 110: the checksum is not looked at
        const uint32_t want = crc32Ieee(d, size - 4);
        uint32_t got = 0;
        for (int k = 0; k < 4; ++k) got |= static_cast<uint32_t>(d[size - 4 + k]) << (8 * k);
        if (want != got) return DecodeError::BadCrc;
    }
    R r(d + 3, size - 3 - 4);
    const uint8_t type = d[2];
    Msg m;
    switch (static_cast<MsgType>(type)) {
        case MsgType::Beacon: {
            Beacon b;
            b.seq = r.u32();
            if (!getTime(r, &b.time)) return DecodeError::Malformed;
            b.rate = r.f32();
            b.timeScale = r.f32();
            b.skipEpoch = r.u32();
            const uint8_t flags = r.u8();
            b.jump = (flags & 1) != 0;
            b.partyTravel = (flags & 2) != 0;
            if (r.bad || flags > 3 || !finite(b.rate) || b.rate < 0 || b.rate > 1000.0f || !finite(b.timeScale) || b.timeScale < 0 || b.timeScale > 5000.0f)
                return DecodeError::Malformed;
            m = b;
            break;
        }
        case MsgType::SkipRequest: {
            SkipRequest s;
            s.reqId = r.u32();
            const uint8_t k = r.u8();
            s.minutes = r.u32();
            if (r.bad || k > 1) return DecodeError::Malformed;
            if (!FNV_MUT(114) && (s.minutes == 0 || s.minutes > 100000)) return DecodeError::Malformed;   // MUTANT 114: 0 and absurd minute counts pass
            s.kind = static_cast<ReqKind>(k);
            m = s;
            break;
        }
        case MsgType::TravelRequest: {
            TravelRequest t;
            t.reqId = r.u32();
            t.marker = r.ref();
            if (r.bad || t.marker.plugin.empty() || t.marker.localId == 0) return DecodeError::Malformed;
            m = t;
            break;
        }
        case MsgType::Verdict: {
            Verdict v;
            v.reqId = r.u32();
            const uint8_t k = r.u8();
            const uint8_t g = r.u8();
            const uint8_t why = r.u8();
            v.minutes = r.u32();
            const uint8_t party = r.u8();
            if (r.bad || k > 2 || g > 1 || why > static_cast<uint8_t>(Reason::Max) || party > 1) return DecodeError::Malformed;
            v.kind = static_cast<ReqKind>(k);
            v.granted = g != 0;
            v.reason = static_cast<Reason>(why);
            v.party = party != 0;
            m = v;
            break;
        }
        case MsgType::TravelGo: {
            TravelGo g;
            g.reqId = r.u32();
            g.marker = r.ref();
            g.mover = r.u8();
            const uint8_t party = r.u8();
            if (r.bad || g.marker.plugin.empty() || g.marker.localId == 0 || g.mover >= 8 || party > 1) return DecodeError::Malformed;
            g.party = party != 0;
            m = g;
            break;
        }
        case MsgType::Hello: {
            Hello h;
            h.wireVersion = r.u8();
            if (r.bad) return DecodeError::Malformed;
            m = h;
            break;
        }
        case MsgType::Status: {
            Status st;
            const uint8_t f = r.u8();
            if (r.bad || f > 1) return DecodeError::Malformed;
            st.inCombat = f != 0;
            m = st;
            break;
        }
        default: return DecodeError::BadType;
    }
    if (!r.done() && !FNV_MUT(111)) return DecodeError::Malformed;   // MUTANT 111: trailing garbage after a valid message is ignored
    if (out) *out = std::move(m);
    return DecodeError::Ok;
}

}  // namespace nvwt
