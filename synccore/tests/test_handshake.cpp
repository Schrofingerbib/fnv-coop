// Version / identity handshake: protocol, build, rules.json, volatile keys, mod list.
#include <memory>
#include "fnvcoop/rules.h"
#include "fnvcoop/session.h"
#include "minitest.h"

using namespace fnvcoop;

namespace {
struct Null : IEffectSink { void apply(const Effect&, const Context&) override {} };

HandshakeInfo hostInfo() {
    HandshakeInfo h;
    h.buildNumber = 120; h.buildHash = 0xAAAA; h.rulesHash = 0xBBBB; h.volatileHash = 0xCCCC; h.buildName = "fnv-coop 0.12.0";
    h.plugins = {"FalloutNV.esm", "DeadMoney.esm", "CaravanPack.esm", "ModA.esp"};
    return h;
}

struct Result { bool joined = false, rejected = false; uint8_t reason = 0; std::string text; uint64_t hostRejects = 0; };

Result tryJoin(const HandshakeInfo& host, const HandshakeInfo& client) {
    InMemoryNetwork net{2};
    HostSession hs(*net.endpoint(0));
    hs.setHandshake(host);
    Null sink;
    ClientSession cs(*net.endpoint(1), 0, 9, sink);
    cs.setHandshake(client);
    cs.connect(0, 1);
    uint64_t now = 0;
    for (int i = 0; i < 100; ++i) { now += 10; net.advance(now); hs.tick(now); cs.tick(now); net.advance(now); }
    Result r;
    r.joined = cs.joined(); r.rejected = cs.rejected(); r.reason = cs.rejectReason(); r.text = cs.rejectText(); r.hostRejects = hs.stats().handshakeRejects;
    return r;
}
bool has(const std::string& s, const char* part) { return s.find(part) != std::string::npos; }
}  // namespace

TEST(handshake, identical_builds_join) {
    Result r = tryJoin(hostInfo(), hostInfo());
    CHECK(r.joined);
    CHECK(!r.rejected);
    CHECK(r.hostRejects == 0);
}

TEST(handshake, an_unconfigured_host_only_checks_the_protocol) {
    HandshakeInfo bare;   // nothing set
    Result r = tryJoin(bare, hostInfo());
    CHECK(r.joined);
}

TEST(handshake, protocol_mismatch_says_who_is_older) {
    HandshakeInfo old = hostInfo(); old.protocol = kProtocolVersion - 1;
    Result a = tryJoin(hostInfo(), old);
    CHECK(a.rejected); CHECK(a.reason == kRejectProtocol); CHECK(has(a.text, "older than the host")); CHECK(!a.joined);
    HandshakeInfo newer = hostInfo(); newer.protocol = kProtocolVersion + 1;
    Result b = tryJoin(hostInfo(), newer);
    CHECK(b.rejected); CHECK(b.reason == kRejectProtocol); CHECK(has(b.text, "host has an older"));
}

TEST(handshake, build_mismatch_messages) {
    HandshakeInfo oldClient = hostInfo(); oldClient.buildNumber = 110; oldClient.buildName = "fnv-coop 0.11.0";
    Result a = tryJoin(hostInfo(), oldClient);
    CHECK(a.rejected); CHECK(a.reason == kRejectBuild);
    CHECK(has(a.text, "host has a newer fnv-coop build")); CHECK(has(a.text, "0.12.0")); CHECK(has(a.text, "0.11.0"));
    HandshakeInfo newClient = hostInfo(); newClient.buildNumber = 130; newClient.buildName = "fnv-coop 0.13.0";
    Result b = tryJoin(hostInfo(), newClient);
    CHECK(b.rejected); CHECK(has(b.text, "host has an older fnv-coop build"));
    HandshakeInfo sameVersionOtherBinary = hostInfo(); sameVersionOtherBinary.buildHash = 0x1234;
    Result c = tryJoin(hostInfo(), sameVersionOtherBinary);
    CHECK(c.rejected); CHECK(c.reason == kRejectBuild); CHECK(has(c.text, "different fnv-coop binaries"));
}

TEST(handshake, rules_and_volatile_mismatch) {
    HandshakeInfo r1 = hostInfo(); r1.rulesHash = 1;
    Result a = tryJoin(hostInfo(), r1);
    CHECK(a.rejected); CHECK(a.reason == kRejectRules); CHECK(has(a.text, "rules.json"));
    HandshakeInfo v1 = hostInfo(); v1.volatileHash = 1;
    Result b = tryJoin(hostInfo(), v1);
    CHECK(b.rejected); CHECK(b.reason == kRejectVolatile); CHECK(has(b.text, "volatile"));
}

TEST(handshake, mod_list_differences_name_the_mods) {
    HandshakeInfo missing = hostInfo(); missing.plugins = {"FalloutNV.esm", "DeadMoney.esm", "ModA.esp"};
    Result a = tryJoin(hostInfo(), missing);
    CHECK(a.rejected); CHECK(a.reason == kRejectModList);
    CHECK(has(a.text, "you are missing CaravanPack.esm"));
    HandshakeInfo extra = hostInfo(); extra.plugins.push_back("Texture Overhaul.esp");
    Result b = tryJoin(hostInfo(), extra);
    CHECK(has(b.text, "you have extra Texture Overhaul.esp")); CHECK(!has(b.text, "missing"));
    HandshakeInfo reordered = hostInfo(); std::swap(reordered.plugins[1], reordered.plugins[2]);
    Result c = tryJoin(hostInfo(), reordered);
    CHECK(c.rejected); CHECK(has(c.text, "different load order"));
    HandshakeInfo both = hostInfo(); both.plugins = {"FalloutNV.esm", "Other.esp"};
    Result d = tryJoin(hostInfo(), both);
    CHECK(has(d.text, "missing")); CHECK(has(d.text, "extra Other.esp"));
}

TEST(handshake, rejects_are_readable_by_a_client_of_any_wire_version_and_hello_by_a_host_of_any_version) {
    // Hello / Reject travel in a version-independent envelope (0xA1); everything else uses the wire version.
    Hello h; h.session = 3; h.token = 5; h.hs = hostInfo();
    Bytes b = encodeMessage(h);
    REQUIRE(b.size() > 4);
    CHECK(b[2] == kHandshakeEnvelope);
    Reject rj; rj.session = 3; rj.reason = kRejectBuild; rj.text = "update";
    CHECK(encodeMessage(rj)[2] == kHandshakeEnvelope);
    CHECK(encodeMessage(Ack{1, 2, 3})[2] == kWireVersion);
    // a Hello from a NEWER build with extra trailing fields still decodes (the host can then say why it refuses)
    Bytes body(b.begin(), b.end() - 4);
    body.push_back(0x7E); body.push_back(0x7F);
    uint32_t c = crc32(body.data(), body.size());
    for (int i = 0; i < 4; ++i) body.push_back(uint8_t(c >> (8 * i)));
    Message m;
    CHECK(decodeMessage(body, m) == DecodeStatus::Ok);
    CHECK(std::get<Hello>(m).hs.buildName == "fnv-coop 0.12.0");
    // but a normal message in the wrong version, or a Hello in a normal envelope, is refused
    Bytes ack = encodeMessage(Ack{1, 2, 3});
    ack[2] = kWireVersion + 1;
    uint32_t c2 = crc32(ack.data(), ack.size() - 4);
    for (int i = 0; i < 4; ++i) ack[ack.size() - 4 + i] = uint8_t(c2 >> (8 * i));
    CHECK(decodeMessage(ack, m) == DecodeStatus::BadVersion);
    Bytes hello = encodeMessage(h);
    hello[2] = kWireVersion;
    uint32_t c3 = crc32(hello.data(), hello.size() - 4);
    for (int i = 0; i < 4; ++i) hello[hello.size() - 4 + i] = uint8_t(c3 >> (8 * i));
    CHECK(decodeMessage(hello, m) == DecodeStatus::BadVersion);
}

TEST(handshake, a_future_protocol_client_gets_a_readable_reject) {
    // Simulate a client from a newer build: protocol 9 in an otherwise valid Hello.
    InMemoryNetwork net{2};
    HostSession hs(*net.endpoint(0));
    hs.setHandshake(hostInfo());
    Hello h; h.session = 1; h.token = 3; h.hs = hostInfo(); h.hs.protocol = 9;
    net.endpoint(1)->send(0, encodeMessage(h));
    net.advance(0); hs.tick(0); net.advance(0);
    PeerId from; Bytes data;
    REQUIRE(net.endpoint(1)->poll(from, data));
    Message m;
    REQUIRE(decodeMessage(data, m) == DecodeStatus::Ok);
    auto* r = std::get_if<Reject>(&m);
    REQUIRE(r != nullptr);
    CHECK(r->reason == kRejectProtocol);
    CHECK(has(r->text, "older"));
}

TEST(handshake, hashes_of_rules_and_volatile_keys_are_stable_and_sensitive) {
    RulesDb a, b;
    const char* t1 = "{\"schema\":1,\"quests\":{\"A\":{\"rules\":[{\"kind\":\"Barrier\",\"params\":{}}]}}}";
    const char* t2 = "{\"schema\":1,\"quests\":{\"A\":{\"rules\":[{\"kind\":\"RewardAll\",\"params\":{}}]}}}";
    REQUIRE(a.load(t1)); REQUIRE(b.load(t2));
    RulesDb a2; REQUIRE(a2.load(t1));
    CHECK(a.textHash() == a2.textHash());
    CHECK(a.textHash() != b.textHash());
    CHECK(a.textHash() != 0);
    volatileKeys().clear();
    uint64_t empty = volatileKeys().contentHash();
    volatileKeys().addVar(FormRef("a.esm", 1), 2);
    volatileKeys().addGlobal(FormRef("a.esm", 3));
    uint64_t h1 = volatileKeys().contentHash();
    volatileKeys().clear();
    volatileKeys().addGlobal(FormRef("a.esm", 3));
    volatileKeys().addVar(FormRef("a.esm", 1), 2);
    CHECK(volatileKeys().contentHash() == h1);       // insertion order does not matter
    volatileKeys().addVar(FormRef("a.esm", 1), 3);
    CHECK(volatileKeys().contentHash() != h1);
    CHECK(h1 != empty);
    volatileKeys().clear();
}

TEST(twin, unit_handshake) {
    HandshakeInfo bad = hostInfo(); bad.plugins.pop_back(); bad.rulesHash = 7;
    CHECK(tryJoin(hostInfo(), bad).rejected);                 // normally refused ...
    Mutations m; m.skipHandshake = true;
    MutationScope s(m);
    Result r = tryJoin(hostInfo(), bad);
    CHECK(!r.rejected);                                       // ... but a host that skips the check lets incompatible clients in
    CHECK(r.joined);
}
