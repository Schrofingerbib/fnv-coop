// Shared helpers of the social tests.
#pragma once
#include <set>
#include <vector>
#include "minitest.h"
#include "social/companions.h"
#include "social/crime.h"
#include "social/rewards.h"
#include "social/wire.h"

namespace social_test {
using namespace fnvcoop;
using namespace fnvcoop::social;

inline FormRef npc(uint32_t n) { return FormRef("falloutnv.esm", 0x100000u + n); }
inline FormRef item(uint32_t n) { return FormRef("falloutnv.esm", 0x200000u + n); }
inline FormRef faction(uint32_t n) { return FormRef("falloutnv.esm", 0x300000u + n); }
inline Effect recruitFx(const FormRef& n, PlayerId owner, uint8_t kind = 0) { CompanionOwner c; c.npc = n; c.owner = owner; c.active = true; c.kind = kind; return c; }
inline Effect dismissFx(const FormRef& n, PlayerId owner = 0) { CompanionOwner c; c.npc = n; c.owner = owner; c.active = false; return c; }

inline CrimeEvent crime(uint64_t id, CrimeKind k, PlayerId criminal, const FormRef& f, cc::Entity victim = cc::Entity::actor(7)) {
    CrimeEvent e;
    e.id = id; e.kind = k; e.criminal = criminal; e.reporter = criminal; e.victim = victim; e.faction = f; e.cell = 5; e.where = {10, 20, 30};
    return e;
}
inline Detection sees(uint32_t observer, const FormRef& f, PlayerId target, uint32_t cell = 5) {
    Detection d;
    d.observer = cc::Entity::actor(observer); d.faction = f; d.target = target; d.cell = cell; d.seen = true;
    return d;
}

template <class T> T roundTrip(T v) {
    Archive w = Archive::writer();
    io(w, v);
    Bytes b = w.finish();
    Archive r = Archive::reader(b.data(), b.size());
    T out{};
    io(r, out);
    return r.ok() && r.atEnd() ? out : T{};
}

}  // namespace social_test
