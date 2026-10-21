#include "intents/intent_wire.h"

#include "fnvcoop/wire.h"
#include "fnvcoop_mut.h"

namespace nvcoop::intents {

namespace {

constexpr uint8_t kTypeIntent = 1, kTypeVerdict = 2;

void ioIntent(fnvcoop::Archive& a, Intent& m) {
    a.varu32(m.session);
    a.varu32(m.id);
    uint8_t k = static_cast<uint8_t>(m.kind);
    a.u8(k);
    if (a.reading()) {
        if (k == 0 || k >= static_cast<uint8_t>(Kind::Count_)) { a.fail(); return; }
        m.kind = static_cast<Kind>(k);
    }
    fnvcoop::io(a, m.target);
    a.boolean(m.hasAux);
    if (m.hasAux) fnvcoop::io(a, m.aux);
    else if (a.reading()) m.aux = FormRef();
    a.vari32(m.value);
    a.bytes(m.path, FNV_MUT(301) ? 255 : kMaxPath);   // MUTANT 301: the path cap is not enforced
}

void ioVerdict(fnvcoop::Archive& a, Verdict& m) {
    a.varu32(m.session);
    a.varu32(m.id);
    uint8_t r = static_cast<uint8_t>(m.result);
    a.u8(r);
    if (a.reading()) {
        if (r >= static_cast<uint8_t>(Result::Count_)) { a.fail(); return; }
        m.result = static_cast<Result>(r);
    }
    a.u8(m.holder);
    if (a.reading() && m.holder != kNoPlayer && m.holder >= fnvcoop::kMaxSlots) { a.fail(); return; }
    a.boolean(m.hasRedirect);
    if (m.hasRedirect) fnvcoop::io(a, m.redirect);
    else if (a.reading()) m.redirect = FormRef();
}

Bytes frame(uint8_t type, const Bytes& body) {
    Bytes out;
    out.reserve(body.size() + 3);
    out.push_back(kChannelIntents);
    out.push_back(kIntentsWire);
    out.push_back(type);
    out.insert(out.end(), body.begin(), body.end());
    return out;
}

}  // namespace

Bytes encode(const Intent& m) {
    fnvcoop::Archive a = fnvcoop::Archive::writer();
    Intent c = m;
    if (c.path.size() > kMaxPath) c.path.resize(kMaxPath);
    ioIntent(a, c);
    return frame(kTypeIntent, a.finish());
}

Bytes encode(const Verdict& m) {
    fnvcoop::Archive a = fnvcoop::Archive::writer();
    Verdict c = m;
    ioVerdict(a, c);
    return frame(kTypeVerdict, a.finish());
}

WireType decode(const uint8_t* data, size_t size, Intent* intent, Verdict* verdict) {
    if (!data || size < 4 || data[0] != kChannelIntents || data[1] != kIntentsWire) return WireType::Bad;
    fnvcoop::Archive a = fnvcoop::Archive::reader(data + 3, size - 3);
    if (!a.ok()) return WireType::Bad;
    if (data[2] == kTypeIntent) {
        Intent m;
        ioIntent(a, m);
        if (!a.ok() || !a.atEnd()) return WireType::Bad;
        if (intent) *intent = std::move(m);
        return WireType::Intent;
    }
    if (data[2] == kTypeVerdict) {
        Verdict m;
        ioVerdict(a, m);
        if (!a.ok() || !a.atEnd()) return WireType::Bad;
        if (verdict) *verdict = std::move(m);
        return WireType::Verdict;
    }
    return WireType::Bad;
}

const char* kindName(Kind k) {
    switch (k) {
        case Kind::Activate: return "Activate";
        case Kind::TriggerEnter: return "TriggerEnter";
        case Kind::PickUp: return "PickUp";
        case Kind::Hit: return "Hit";
        case Kind::DialogueStart: return "DialogueStart";
        case Kind::TerminalUse: return "TerminalUse";
        case Kind::ContainerOpen: return "ContainerOpen";
        case Kind::ContainerClose: return "ContainerClose";
        case Kind::TriggerLeave: return "TriggerLeave";
        default: return "?";
    }
}

const char* resultName(Result r) {
    static const char* n[] = {"Executed", "Duplicate", "Disabled", "NotJoined", "RateLimited", "BadTarget", "NotLoaded", "OutOfRange",
                              "Busy", "Taken", "Denied", "NoPuppet", "Unsupported", "Failed", "Stale"};
    static_assert(sizeof n / sizeof *n == static_cast<size_t>(Result::Count_), "resultName: a Result was added");
    return static_cast<size_t>(r) < sizeof n / sizeof *n ? n[static_cast<size_t>(r)] : "?";
}

}  // namespace nvcoop::intents
