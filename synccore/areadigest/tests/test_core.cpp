// synccore/areadigest unit tests (game-free): hashing, comparison and the rubber-band rules, change tracker, rolling counters, smoother,
// wire codec. Groups: hash, rubber, tracker, rate, wire, story. Twins 1..17, 31, 32 and 8 are caught here (see the CMakeLists list).
#include <cstring>
#include <string>
#include "fnvcoop/rng.h"
#include "fnvcoop/wire.h"
#include "th.h"

using namespace adt;

namespace {

Item actor(Key k, CellId cell, float hp, Vec3 pos, uint32_t settled = 5000) {
    Item it;
    it.key = k; it.cat = Cat::Actors; it.cell = cell; it.hp = hp; it.pos = pos; it.settledMs = settled; it.version = 1;
    return it;
}
Item thing(Cat c, Key k, CellId cell, uint32_t state, bool alive = true, uint32_t settled = 5000) {
    Item it;
    it.key = k; it.cat = c; it.cell = cell; it.state = state; it.alive = alive; it.settledMs = settled; it.version = 1;
    return it;
}

}  // namespace

// ---- hash ---------------------------------------------------------------------------------------------------------------------
TEST(hash, every_compared_field_changes_the_hash) {
    Config c;
    const Item base = actor(7, 3, 80, V(100, 200, 300));
    const uint32_t h = valueHash(base, c);
    CHECK(h == valueHash(base, c));
    { Item x = base; x.hp = 40; CHECK(valueHash(x, c) != h); }                         // twin 3
    { Item x = base; x.pos = V(100 + 3 * kU, 200, 300); CHECK(valueHash(x, c) != h); }  // twin 4: 3 m is another 2 m bucket
    { Item x = base; x.cell = 4; CHECK(valueHash(x, c) != h); }                         // twin 6
    { Item x = base; x.alive = false; CHECK(valueHash(x, c) != h); }                    // twin 7
    const Item box = thing(Cat::Containers, 9, 3, 0xABCD);
    const uint32_t hb = valueHash(box, c);
    { Item x = box; x.state = 0xABCE; CHECK(valueHash(x, c) != hb); }                   // twin 5
    { Item x = box; x.alive = false; CHECK(valueHash(x, c) != hb); }
}

TEST(hash, small_changes_inside_one_bucket_do_not_change_the_hash) {
    Config c;
    const Item base = actor(7, 3, 80, V(10, 10, 10));
    CHECK(valueHash(base, c) == valueHash(actor(7, 3, 83, V(40, 30, 20)), c));   // same health bucket (8), same 2 m bucket
}

TEST(hash, items_in_flux_hash_only_their_identity) {
    Config c;
    Item a = actor(7, 3, 80, V(10, 10, 10), 100);      // changed 100 ms ago
    Item b = actor(7, 3, 10, V(900, 10, 10), 100);     // wildly different values, also in flux
    CHECK(valueHash(a, c) == valueHash(b, c));         // twin 1: in-flux values are hashed too, so a value still travelling makes every digest differ
    Item d = a; d.alive = false;
    CHECK(valueHash(a, c) != valueHash(d, c));         // but a death always counts
    Item e = a; e.settledMs = c.settleMs;              // exactly settled: values count
    CHECK(valueHash(e, c) != valueHash(actor(7, 3, 10, V(900, 10, 10), c.settleMs), c));
}

TEST(hash, a_corpse_hashes_neither_health_nor_position) {
    Config c;
    Item a = actor(7, 3, 0, V(10, 10, 10)); a.alive = false;
    Item b = actor(7, 3, 55, V(700, 90, 10)); b.alive = false;      // ragdolls rest in different places on different machines
    CHECK(valueHash(a, c) == valueHash(b, c));                        // twin 2
    Item f = a; f.cell = 9;
    CHECK(valueHash(f, c) != valueHash(a, c));
}

TEST(hash, group_hash_is_order_independent_and_counts_items) {
    Config c;
    std::vector<Item> v = {actor(1, 3, 100, V()), actor(2, 3, 50, V(300, 0, 0)), thing(Cat::Containers, 3, 3, 5), actor(4, 8, 100, V())};
    auto g1 = groupsOf(v, c);
    std::vector<Item> r(v.rbegin(), v.rend());
    auto g2 = groupsOf(r, c);
    REQUIRE(g1.size() == 3 && g2.size() == 3);
    for (size_t i = 0; i < g1.size(); ++i) CHECK(g1[i].cell == g2[i].cell && g1[i].cat == g2[i].cat && g1[i].hash == g2[i].hash && g1[i].count == g2[i].count);
    CHECK(g1[0].cell == 3 && g1[0].cat == Cat::Actors && g1[0].count == 2);
    v.pop_back();
    CHECK(groupsOf(v, c).size() == 2);
    std::vector<Item> w = {actor(1, 3, 100, V())};
    std::vector<Item> w2 = {actor(2, 3, 100, V())};
    CHECK(groupsOf(w, c)[0].hash != groupsOf(w2, c)[0].hash);   // the key is part of the term
}

// ---- rubber band ----------------------------------------------------------------------------------------------------------------
TEST(rubber, offsets_classify_by_the_plan_thresholds) {
    Config c;
    CHECK(classifyOffset(0, true, c) == RubberMode::None);
    CHECK(classifyOffset(1.9f * kU, true, c) == RubberMode::None);
    CHECK(classifyOffset(2.0f * kU, true, c) == RubberMode::None);      // exactly 2 m: not more than 2 m (twin 9)
    CHECK(classifyOffset(2.01f * kU, true, c) == RubberMode::Smooth);
    CHECK(classifyOffset(9.9f * kU, true, c) == RubberMode::Smooth);    // twin 10: smooth up to 10 m
    CHECK(classifyOffset(10.0f * kU, true, c) == RubberMode::Smooth);   // exactly 10 m is not more than 10 m
    CHECK(classifyOffset(10.01f * kU, true, c) == RubberMode::Snap);
    CHECK(classifyOffset(0, false, c) == RubberMode::Snap);             // another cell always snaps (twin 11)
    CHECK(classifyOffset(3 * kU, false, c) == RubberMode::Snap);
}

TEST(rubber, compare_items_says_what_to_change) {
    Config c;
    const Item auth = actor(7, 3, 100, V(0, 0, 0));
    { Diff d = compareItems(auth, actor(7, 3, 100, V(0, 0, 0)), c); CHECK(!d.material && d.pos == RubberMode::None); }
    { Diff d = compareItems(auth, actor(7, 3, 96, V(0, 0, 0)), c); CHECK(!d.material); }                           // inside the 5 hp tolerance (twin 16)
    { Diff d = compareItems(auth, actor(7, 3, 90, V(0, 0, 0)), c); CHECK(d.material && d.hp && d.pos == RubberMode::None); }
    { Diff d = compareItems(auth, actor(7, 3, 100, V(3 * kU, 0, 0)), c); CHECK(d.material && d.pos == RubberMode::Smooth && !d.hp); }
    { Diff d = compareItems(auth, actor(7, 3, 100, V(11 * kU, 0, 0)), c); CHECK(d.material && d.pos == RubberMode::Snap); }
    { Diff d = compareItems(auth, actor(7, 4, 100, V(0, 0, 0)), c); CHECK(d.material && d.cell && d.pos == RubberMode::Snap); }
    { Item o = actor(7, 3, 100, V(0, 0, 0)); o.alive = false; Diff d = compareItems(auth, o, c); CHECK(d.material && d.alive); }
    // corpses: only the alive flag and the cell matter (twin 17)
    { Item a = auth; a.alive = false; Item o = actor(7, 3, 0, V(500, 500, 0)); o.alive = false; CHECK(!compareItems(a, o, c).material); }
    // containers / objects compare their state
    { Diff d = compareItems(thing(Cat::Objects, 1, 3, 1), thing(Cat::Objects, 1, 3, 2), c); CHECK(d.material && d.state && d.pos == RubberMode::None); }
    { Diff d = compareItems(thing(Cat::Objects, 1, 3, 1), thing(Cat::Objects, 1, 3, 1), c); CHECK(!d.material); }
}

TEST(rubber, smoother_closes_the_gap_without_a_pop) {
    Config c;
    Smoother s;
    CHECK(!s.active());
    s.begin(1000, V(0, 0, 0), V(5 * kU, 0, 0), c);
    CHECK(s.active());
    Vec3 p;
    float last = -1;
    int steps = 0;
    for (uint64_t t = 1000; t < 4000; t += 16) {
        const bool more = s.step(t, p);
        CHECK(p.x >= last);                               // never backwards
        if (more) { CHECK(p.x < 5 * kU); ++steps; }       // twin 12: jumps straight to the target
        last = p.x;
        if (!more) break;
    }
    CHECK(steps > 10);
    CHECK(p.x == 5 * kU && !s.active());
    CHECK(!s.step(9000, p) && p.x == 5 * kU);             // after the end it keeps answering the target
}

// ---- tracker -------------------------------------------------------------------------------------------------------------------
TEST(tracker, version_and_settle_follow_real_changes_only) {
    Config c;
    ChangeTracker tr(c);
    std::vector<Item> v = {actor(1, 3, 100, V(0, 0, 0))};
    tr.update(v, 1000);
    CHECK(v[0].version == 1 && v[0].settledMs == 0);                       // new: just appeared
    v = {actor(1, 3, 100, V(0, 0, 0))};
    tr.update(v, 2500);
    CHECK(v[0].version == 1 && v[0].settledMs == 1500);
    v = {actor(1, 3, 100, V(1.5f, 0, 0))};                                  // engine jitter well below the epsilon: not a change (twin 15)
    tr.update(v, 3000);
    CHECK(v[0].version == 1 && v[0].settledMs == 2000);
    v = {actor(1, 3, 60, V(1.5f, 0, 0))};                                   // health: a change (twin 14: the version must move)
    tr.update(v, 3500);
    CHECK(v[0].version == 2 && v[0].settledMs == 0);
    v = {actor(1, 3, 60, V(20, 0, 0))};
    tr.update(v, 3600);
    CHECK(v[0].version == 3);
    CHECK(tr.size() == 1);
    v = {};
    tr.update(v, 4000);
    CHECK(tr.size() == 0);                                                  // forgotten
}

TEST(tracker, slow_drift_eventually_counts) {
    Config c;
    ChangeTracker tr(c);
    std::vector<Item> v = {actor(1, 3, 100, V(0, 0, 0))};
    tr.update(v, 1000);
    uint32_t ver = 1;
    for (int i = 1; i <= 20; ++i) {                                         // 1 unit per step: each step is below the 4 unit epsilon
        v = {actor(1, 3, 100, V(float(i), 0, 0))};
        tr.update(v, 1000 + i * 100);
        ver = v[0].version;
    }
    CHECK(ver > 1);
}

TEST(tracker, refresh_reads_one_item_against_the_same_memory) {
    Config c;
    ChangeTracker tr(c);
    std::vector<Item> v = {actor(1, 3, 100, V(0, 0, 0))};
    tr.update(v, 1000);
    Item x = actor(1, 3, 100, V(0, 0, 0));
    CHECK(tr.refresh(x, 2000) && x.version == 1 && x.settledMs == 1000);
    Item y = actor(1, 3, 20, V(0, 0, 0));
    CHECK(tr.refresh(y, 2100) && y.version == 2 && y.settledMs == 0);
    Item z = actor(99, 3, 20, V(0, 0, 0));
    CHECK(!tr.refresh(z, 2100) && z.version == 1);                          // unknown key: remembered from now on
}

// ---- rolling counter -------------------------------------------------------------------------------------------------------------
TEST(rate, per_minute_expires_and_total_does_not) {
    RollingRate r;
    CHECK(r.perMinute(5000) == 0);
    r.add(10000);
    r.add(10500, 2);
    CHECK(r.perMinute(10600) == 3 && r.total() == 3);
    CHECK(r.perMinute(69000) == 3);
    CHECK(r.perMinute(70000) == 0);              // twin 13: the per-minute count never expires
    CHECK(r.total() == 3);
    r.add(100000);
    CHECK(r.perMinute(100001) == 1 && r.total() == 4);
    r.add(100000 + 59000);
    CHECK(r.perMinute(100000 + 59500) == 2);
    CHECK(r.perMinute(100000 + 60500) == 1);
    r.add(100000 + 600000);                       // a long gap clears every bucket
    CHECK(r.perMinute(100000 + 600000) == 1);
}

// ---- wire ----------------------------------------------------------------------------------------------------------------------
namespace {

Item wireItem(Key k) {
    Item it;
    it.key = k; it.cat = Cat::Actors; it.cell = 0x1234; it.alive = true; it.hp = 87.5f; it.pos = V(1.5f, -2.5f, 300.25f);
    it.state = 0xCAFE; it.settledMs = 4000; it.version = 9;
    return it;
}

std::vector<Envelope> sampleEnvelopes() {
    std::vector<Envelope> v;
    { Envelope e; e.src = 2; MDigest d; d.round = 5; d.cells = {1, 2, 0x55}; d.groups = {{1, Cat::Actors, 0xDEADBEEF, 12}, {2, Cat::Objects, 7, 1}};
      d.story.has = true; d.story.hash = 0x1122334455667788ull; d.story.seq = 99; d.story.count = 4; d.story.epoch = 3; e.body = d; v.push_back(e); }
    { Envelope e; e.src = 0; MDigest d; e.body = d; v.push_back(e); }
    { Envelope e; e.src = 0; MItemsReq r; r.caseId = 4; r.groups = {{1, Cat::Actors}, {9, Cat::Containers}}; r.keys = {{Cat::Actors, 0xFFC00001ull}}; e.body = r; v.push_back(e); }
    { Envelope e; e.src = 1; MItems r; r.caseId = 4; r.truncated = true; r.items = {wireItem(1), wireItem(0x123456789ull)}; e.body = r; v.push_back(e); }
    { Envelope e; e.src = 0; MHeal h; h.caseId = 4; h.items.push_back(WireHeal{wireItem(5), 9, 3}); e.body = h; v.push_back(e); }
    return v;
}

}  // namespace

TEST(wire, every_message_round_trips) {
    for (const Envelope& e : sampleEnvelopes()) {
        std::vector<uint8_t> b = encodeEnvelope(e);
        REQUIRE(!b.empty() && b[0] == kChannelAreaDigest);
        Envelope back;
        REQUIRE(decodeEnvelope(b.data(), b.size(), back));
        CHECK(back.src == e.src && back.body.index() == e.body.index());
        CHECK(encodeEnvelope(back) == b);        // canonical: re-encoding gives the same bytes
    }
    // content check on the biggest one
    Envelope e = sampleEnvelopes()[3];
    std::vector<uint8_t> b = encodeEnvelope(e);
    Envelope back;
    REQUIRE(decodeEnvelope(b.data(), b.size(), back));
    const MItems& it = std::get<MItems>(back.body);
    CHECK(it.caseId == 4 && it.truncated && it.items.size() == 2 && it.items[0] == wireItem(1) && it.items[1] == wireItem(0x123456789ull));
}

TEST(wire, malformed_input_is_rejected_never_crashes) {
    Envelope back;
    CHECK(!decodeEnvelope(nullptr, 0, back));
    const uint8_t one[] = {kChannelAreaDigest};
    CHECK(!decodeEnvelope(one, 1, back));
    for (const Envelope& e : sampleEnvelopes()) {
        std::vector<uint8_t> b = encodeEnvelope(e);
        for (size_t cut = 0; cut + 1 < b.size(); ++cut) CHECK(!decodeEnvelope(b.data(), cut, back));   // every strict prefix fails
        std::vector<uint8_t> t = b;
        t.push_back(0);
        CHECK(!decodeEnvelope(t.data(), t.size(), back));                                                // twin 32: trailing garbage
        std::vector<uint8_t> w = b;
        w[0] = 0x00;
        CHECK(!decodeEnvelope(w.data(), w.size(), back));
        w = b;
        w[1] ^= 0x7F;                                                                                    // table/version byte damaged
        Envelope junk;
        (void)decodeEnvelope(w.data(), w.size(), junk);                                                  // may or may not decode, must not crash
    }
    Rng rng(99);
    for (int i = 0; i < 4000; ++i) {                                                                     // random bytes behind the channel byte
        std::vector<uint8_t> r(1 + rng.range(0, 40));
        r[0] = kChannelAreaDigest;
        for (size_t j = 1; j < r.size(); ++j) r[j] = static_cast<uint8_t>(rng.next());
        Envelope junk;
        (void)decodeEnvelope(r.data(), r.size(), junk);
    }
}

TEST(wire, hostile_counts_and_values_are_refused) {
    // a digest with more groups than the receiver allows, every group well formed (so only the bound can stop it; twin 31 removes the bound)
    {
        Archive a = Archive::writer();
        uint8_t ver = kAreaDigestWireVersion, src = 1, idx = 0;
        a.u8(ver); a.u8(src); a.u8(idx);
        uint32_t round = 1;
        a.varu32(round);
        uint64_t cells = 0;
        a.varu(cells);
        uint64_t groups = kMaxGroups + 900;
        a.varu(groups);
        for (uint64_t i = 0; i < groups; ++i) {
            uint32_t cell = static_cast<uint32_t>(i), h = 0, cnt = 1;
            uint8_t cat = 1;
            a.varu32(cell); a.u8(cat); a.u32(h); a.varu32(cnt);
        }
        bool has = false;
        a.boolean(has);
        std::vector<uint8_t> body = a.finish(), dg;
        dg.push_back(kChannelAreaDigest);
        dg.insert(dg.end(), body.begin(), body.end());
        Envelope e;
        CHECK(!decodeEnvelope(dg.data(), dg.size(), e));
    }
    // non-finite floats cannot be encoded, and a category outside 1..3 cannot be decoded
    {
        Envelope e;
        e.src = 1;
        MItems it;
        Item x = wireItem(1);
        x.hp = std::nanf("");
        it.items.push_back(x);
        e.body = it;
        CHECK(encodeEnvelope(e).empty());
        Envelope ok = sampleEnvelopes()[3];
        std::get<MItems>(ok.body).items[0].cat = Cat::Story;   // the codec writes the byte; the reader must refuse it (items are never the story category)
        std::vector<uint8_t> bad = encodeEnvelope(ok);
        Envelope back;
        CHECK(!bad.empty() && !decodeEnvelope(bad.data(), bad.size(), back));
    }
}

// ---- story digest -----------------------------------------------------------------------------------------------------------------
TEST(story, the_digest_follows_the_shadow_state) {
    FakeStory a, b;
    a.commit(0x100, 10);
    b.commit(0x100, 10);
    StoryDigest da, db;
    REQUIRE(a.local(da) && b.local(db));
    CHECK(da.ok && da.hash == db.hash && da.count == db.count && da.seq == 1);
    b.commit(0x100, 20);                                  // twin 8: a different value must give a different digest
    REQUIRE(b.local(db));
    CHECK(db.hash != da.hash);
    a.commit(0x100, 20);
    REQUIRE(a.local(da));
    CHECK(da.hash == db.hash && da.seq == db.seq);
    a.commit(0x101, 5);
    REQUIRE(a.local(da));
    CHECK(da.hash != db.hash && da.count > db.count);
}
