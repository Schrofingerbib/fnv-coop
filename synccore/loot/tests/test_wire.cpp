// Wire tests of synccore/loot: every message round-trips, hostile input is refused. Twins 120 (count cap), 121 (trailing bytes).
#include "th.h"
#include "fnvcoop/wire.h"

using namespace lt;

namespace {

ContainerRec richRec() {
    ContainerRec r = rec(0x1234, I({{0x10, 3}, {0x11, 1}, {0x22, 250}}), ContainerKind::Corpse, 0x77);
    r.pos = V(1.5f, -2.5f, 300.f);
    r.rev = 9; r.holder = 2; r.lockSeq = 4;
    return r;
}
WorldItem richItem() {
    WorldItem w;
    w.id = kItemIdBase + 5; w.base = F(0x4242); w.count = 12; w.meta = 77; w.cell = 3; w.pos = V(1, 2, 3); w.rot = V(0.1f, 0.2f, 0.3f);
    w.dropper = 1; w.dropTxn = 17;
    return w;
}

std::vector<Body> allBodies() {
    std::vector<Body> v;
    v.push_back(ReqRegister{3, richRec()});
    ReqOpen o; o.txn = 4; o.cont = 5; o.hasAdopt = true; o.adopt = richRec(); v.push_back(o);
    v.push_back(moveReq(5, 9, 0x10, 4, true));
    v.push_back(ReqClose{6, 9, 0xDEADBEEFCAFEull});
    v.push_back(ReqHold{9});
    ReqDrop d = dropReq(7, 0x33, 5); d.meta = 9; d.rot = V(1, 2, 3); v.push_back(d);
    ReqClaim c; c.txn = 8; c.item = 0x55; c.hasAdopt = true; c.adopt = richItem(); c.adopt.id = 0x55; v.push_back(c);
    v.push_back(ReqResync{9, true});
    v.push_back(ReqDigest{0x1122334455667788ull, 5, 6});
    v.push_back(ReqTouch{kItemIdBase + 1, V(1, 2, 3), V(0.5f, 0, 0)});
    Result r; r.txn = 10; r.op = OpKind::Claim; r.status = Status::AlreadyTaken; r.id = 4; r.rev = 3; r.other = 2; r.base = F(0x99); r.count = 4; r.newId = 8;
    r.hasRec = true; r.rec = richRec();
    v.push_back(r);
    v.push_back(MContainer{richRec()});
    v.push_back(MDelta{9, 12, F(0x10), 0});
    v.push_back(MLock{9, 3, kNoPlayer});
    v.push_back(MItemNew{richItem()});
    v.push_back(MItemGone{kItemIdBase + 5, 2, 8});
    v.push_back(MContainerGone{9});
    v.push_back(MTableBegin{4});
    v.push_back(MTableEnd{4, 10, 20});
    v.push_back(MClutter{kItemIdBase + 5, 2, 99, V(1, 2, 3), V(0.1f, 0.2f, 0.3f), true});
    v.push_back(MClutterOwn{kItemIdBase + 5, 1, 3, V(1, 2, 3), V(0, 0, 0)});
    return v;
}

}  // namespace

TEST(wire, every_message_kind_round_trips_byte_for_byte) {
    auto bodies = allBodies();
    CHECK(bodies.size() == std::variant_size_v<Body>);
    std::set<size_t> seen;
    for (const Body& b : bodies) {
        seen.insert(b.index());
        Envelope e;
        e.src = 2;
        e.body = b;
        auto bytes = encodeEnvelope(e);
        REQUIRE(!bytes.empty());
        CHECK(bytes[0] == kChannelLoot);
        Envelope back;
        REQUIRE(decodeEnvelope(bytes.data(), bytes.size(), back));
        CHECK(back.src == 2);
        CHECK(back.body.index() == b.index());
        CHECK(encodeEnvelope(back) == bytes);
    }
    CHECK(seen.size() == std::variant_size_v<Body>);
}

TEST(wire, request_and_unreliable_classification) {
    auto bodies = allBodies();
    int requests = 0, unreliable = 0;
    for (const Body& b : bodies) {
        if (isRequest(b)) ++requests;
        if (isUnreliable(b)) ++unreliable;
        CHECK(std::string(bodyName(b)).size() > 1);
    }
    CHECK(requests == 11);   // ten Req* plus the clutter snapshot a client sends
    CHECK(unreliable == 1);
}

TEST(wire, every_truncation_is_refused_and_garbage_never_crashes) {
    auto bodies = allBodies();
    for (const Body& b : bodies) {
        Envelope e;
        e.src = 1; e.body = b;
        auto bytes = encodeEnvelope(e);
        for (size_t n = 0; n < bytes.size(); ++n) {
            Envelope back;
            CHECK(!decodeEnvelope(bytes.data(), n, back));
        }
    }
    Rng rng(99);
    int decoded = 0;
    for (int i = 0; i < 6000; ++i) {
        std::vector<uint8_t> buf(rng.range(0, 90));
        for (auto& x : buf) x = static_cast<uint8_t>(rng.u32());
        if (!buf.empty() && rng.chance(0.8)) buf[0] = kChannelLoot;
        if (buf.size() > 1 && rng.chance(0.8)) buf[1] = 0;     // string table size 0
        if (buf.size() > 2 && rng.chance(0.8)) buf[2] = kLootWireVersion;
        Envelope e;
        if (decodeEnvelope(buf.data(), buf.size(), e)) ++decoded;
    }
    CHECK(decoded < 6000);
}

TEST(wire, hostile_stack_count_and_trailing_bytes_are_refused) {
    // trailing byte (twin 121)
    Envelope e;
    e.src = 1; e.body = MContainer{richRec()};
    auto bytes = encodeEnvelope(e);
    bytes.push_back(0);
    Envelope back;
    CHECK(!decodeEnvelope(bytes.data(), bytes.size(), back));

    // a container record that claims more stacks than the cap, with enough bytes behind it to look plausible (twin 120)
    Archive a = Archive::writer();
    uint8_t ver = kLootWireVersion, src = 1, idx = static_cast<uint8_t>(Body(MContainer{}).index());
    a.u8(ver); a.u8(src); a.u8(idx);
    uint32_t id = 5; a.varu32(id);
    uint8_t kind = 0; a.u8(kind);
    uint32_t cell = 1; a.varu32(cell);
    float f = 0; a.f32(f); a.f32(f); a.f32(f);
    uint64_t n = kMaxStacks + 40;
    a.varu(n);
    for (uint64_t i = 0; i < n; ++i) {
        FormRef fr("falloutnv.esm", static_cast<uint32_t>(i + 1));
        fnvcoop::io(a, fr);
        int32_t c = 1; a.vari32(c);
    }
    uint32_t zero = 0; a.varu32(zero);
    uint8_t holder = kNoPlayer; a.u8(holder);
    a.varu32(zero);
    std::vector<uint8_t> body = a.finish();
    std::vector<uint8_t> dg;
    dg.push_back(kChannelLoot);
    dg.insert(dg.end(), body.begin(), body.end());
    CHECK(!decodeEnvelope(dg.data(), dg.size(), back));

    // out-of-range enum / negative counts
    Envelope r;
    Result res;
    res.op = OpKind::Open;
    r.body = res;
    auto rb = encodeEnvelope(r);
    REQUIRE(!rb.empty());
    CHECK(decodeEnvelope(rb.data(), rb.size(), back));
    Envelope d;
    d.body = MDelta{1, 2, F(3), 4};
    auto db = encodeEnvelope(d);
    CHECK(decodeEnvelope(db.data(), db.size(), back));
    db[0] = 0x00;
    CHECK(!decodeEnvelope(db.data(), db.size(), back));
}

TEST(wire, a_client_slot_beyond_the_table_is_refused) {
    Envelope e;
    e.src = 1;
    e.body = ReqHold{4};
    auto bytes = encodeEnvelope(e);
    Envelope back;
    CHECK(decodeEnvelope(bytes.data(), bytes.size(), back));
    // layout: [channel][string table size][version][src][index]...
    bytes[3] = 0x20;
    CHECK(!decodeEnvelope(bytes.data(), bytes.size(), back));
}
