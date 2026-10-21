// plugin/vats: VatsRuntime on a real MuxTransport (session lifecycle, channel routing, slot mapping).
#include <deque>
#include "harness.h"
#include "vats/vats_runtime.h"

using namespace nvcoop::vats;
using namespace vtest;

namespace {

struct LoopTransport : fnvcoop::ITransport {
    std::vector<std::pair<fnvcoop::PeerId, Bytes>> sent;
    std::deque<std::pair<fnvcoop::PeerId, Bytes>> in;
    void send(fnvcoop::PeerId to, Bytes d) override { sent.push_back({to, std::move(d)}); }
    bool poll(fnvcoop::PeerId& from, Bytes& d) override {
        if (in.empty()) return false;
        from = in.front().first;
        d = std::move(in.front().second);
        in.pop_front();
        return true;
    }
    size_t countKind(fnvcoop::PeerId to, Kind k) const {
        size_t n = 0;
        for (const auto& s : sent) {
            Envelope e;
            if (s.first == to && decodeEnvelope(s.second.data(), s.second.size(), e) && kindOf(e.body) == k) ++n;
        }
        return n;
    }
};

Config baseCfg() {
    Config c;
    c.settings.partySlowMo = true;
    return c;
}

FrameInfo hostFrame(fnvcoop::MuxTransport* mux, uint64_t now, std::vector<ClientLink> clients = {}) {
    FrameInfo f;
    f.active = true; f.isHost = true; f.mux = mux; f.nowMs = now; f.clients = std::move(clients);
    return f;
}

TEST(vats_runtime, no_session_no_sync) {
    FakeEngine eng;
    VatsRuntime rt(eng, baseCfg());
    FrameInfo f;
    rt.frame(f);
    CHECK(rt.sync() == nullptr);
    LoopTransport lt;
    fnvcoop::MuxTransport mux(lt);
    f.active = true; f.isHost = false; f.mux = &mux; f.self = kNoPlayer; f.hostLinkUp = false;   // a client that has not joined yet
    rt.frame(f);
    CHECK(rt.sync() == nullptr);
}

TEST(vats_runtime, a_host_session_builds_the_sync_sends_the_config_to_a_joining_client_and_routes_its_datagrams) {
    FakeEngine eng;
    VatsRuntime rt(eng, baseCfg());
    LoopTransport lt;
    fnvcoop::MuxTransport mux(lt);
    rt.frame(hostFrame(&mux, 1000));
    REQUIRE(rt.sync() != nullptr);
    CHECK(rt.rebuilds() == 1);
    CHECK(rt.sync()->isHost() && rt.sync()->self() == kHostPlayer);
    ClientLink l; l.slot = 1; l.peer = 11; l.joined = true;
    rt.frame(hostFrame(&mux, 1100, {l}));
    CHECK(lt.countKind(11, Kind::Config) == 1);
    CHECK(lt.sent.front().second[0] == kChannelVats);
    // a datagram from that client
    Envelope e; e.src = 1; e.dest = kNoPlayer;
    MPhase p; p.player = 1; p.mode = Mode::TargetSelect;
    e.body = p;
    lt.in.push_back({11, encodeEnvelope(e)});
    fnvcoop::PeerId from; Bytes data;
    CHECK(!mux.poll(from, data));                                  // consumed by the channel handler, nothing returned to the session
    CHECK(rt.sync()->modeOf(1) == Mode::TargetSelect);
    // a datagram from a peer that is no client
    lt.in.push_back({99, encodeEnvelope(e)});
    mux.poll(from, data);
    CHECK(rt.droppedUnknownPeer() == 1);
}

TEST(vats_runtime, the_story_channel_is_not_ours) {
    FakeEngine eng;
    VatsRuntime rt(eng, baseCfg());
    LoopTransport lt;
    fnvcoop::MuxTransport mux(lt);
    rt.frame(hostFrame(&mux, 1000));
    lt.in.push_back({5, Bytes{fnvcoop::kChannelStory, 1, 2, 3}});
    fnvcoop::PeerId from = 0; Bytes data;
    CHECK(mux.poll(from, data));                                   // handed to the session, as before
    CHECK(from == 5 && data[0] == fnvcoop::kChannelStory);
}

TEST(vats_runtime, a_leaving_client_and_a_new_session_reset_everything_and_restore_the_time_scale) {
    FakeEngine eng;
    VatsRuntime rt(eng, baseCfg());
    LoopTransport lt;
    fnvcoop::MuxTransport mux(lt);
    ClientLink l; l.slot = 1; l.peer = 11; l.joined = true;
    rt.frame(hostFrame(&mux, 1000, {l}));
    eng.enter({humanoid(0x1000)}, 0x1000);
    rt.frame(hostFrame(&mux, 1100, {l}));
    rt.frame(hostFrame(&mux, 1200, {l}));
    REQUIRE(rt.sync()->partySlowActive());
    REQUIRE(eng.scaleTgt == 0.35f);
    // the session ends
    FrameInfo off;
    rt.frame(off);
    CHECK(rt.sync() == nullptr);
    CHECK(eng.scaleTgt == 1.0f);                                   // slow-mo does not outlive the session
    // a new session with a new mux is a new sync
    eng.leave();
    LoopTransport lt2;
    fnvcoop::MuxTransport mux2(lt2);
    rt.frame(hostFrame(&mux2, 5000));
    CHECK(rt.rebuilds() == 2);
    CHECK(!rt.sync()->partySlowActive());
}

TEST(vats_runtime, a_client_session_follows_the_host_and_goes_back_to_its_own_settings_when_the_link_drops) {
    FakeEngine eng;
    Config cfg = baseCfg();
    cfg.settings.partySlowMo = false;                              // the client's own ini
    VatsRuntime rt(eng, cfg);
    LoopTransport lt;
    fnvcoop::MuxTransport mux(lt);
    FrameInfo f;
    f.active = true; f.isHost = false; f.mux = &mux; f.self = 2; f.hostLinkUp = true; f.nowMs = 1000;
    rt.frame(f);
    REQUIRE(rt.sync() != nullptr);
    CHECK(!rt.sync()->isHost() && rt.sync()->self() == 2);
    Envelope e; e.src = 0; e.dest = 2;
    MConfig m; m.epoch = 1; m.s.partySlowMo = true;
    e.body = m;
    lt.in.push_back({0, encodeEnvelope(e)});
    fnvcoop::PeerId from; Bytes data;
    mux.poll(from, data);
    CHECK(rt.sync()->settings().partySlowMo);                      // the host's value
    f.hostLinkUp = false; f.nowMs = 2000;                          // rebaselining / link down
    rt.frame(f);
    f.hostLinkUp = true; f.nowMs = 2100;
    rt.frame(f);
    CHECK(rt.sync() != nullptr);
    CHECK(!rt.sync()->settings().partySlowMo);                     // back to the local ini until the host speaks again
}

TEST(vats_runtime, each_session_gets_a_higher_epoch) {
    FakeEngine eng;
    VatsRuntime rt(eng, baseCfg());
    LoopTransport lt1, lt2;
    fnvcoop::MuxTransport m1(lt1), m2(lt2);
    rt.frame(hostFrame(&m1, 1000));
    eng.enter({humanoid(0x1000)}, 0x1000);
    rt.frame(hostFrame(&m1, 1050));
    rt.frame(hostFrame(&m1, 1100));
    // the config the first session sent has epoch 1
    ClientLink l; l.slot = 1; l.peer = 11; l.joined = true;
    rt.frame(hostFrame(&m1, 1150, {l}));
    bool saw1 = false;
    for (const auto& s : lt1.sent) {
        Envelope e;
        if (decodeEnvelope(s.second.data(), s.second.size(), e))
            if (const MConfig* c = std::get_if<MConfig>(&e.body)) saw1 = saw1 || c->epoch == 1;
    }
    CHECK(saw1);
    FrameInfo off;
    rt.frame(off);
    eng.leave();
    rt.frame(hostFrame(&m2, 3000, {l}));
    rt.frame(hostFrame(&m2, 3100, {l}));
    bool saw2 = false;
    for (const auto& s : lt2.sent) {
        Envelope e;
        if (decodeEnvelope(s.second.data(), s.second.size(), e))
            if (const MConfig* c = std::get_if<MConfig>(&e.body)) saw2 = saw2 || c->epoch == 2;
    }
    CHECK(saw2);
}

}  // namespace
