// Engine-glue tests (game-free): layout reading on a synthetic memory image + EngineGlue over a mock IEngineApi and
// real DialogueNode/InMemoryNetwork. Usage: engine_tests
#include <cstdio>
#include <map>
#include <memory>
#include <string>
#include <tuple>
#include "fnvcoop/transport.h"
#include "glue.h"

using namespace fndlg;
static std::string g_why;
#define CHECK(c) do { if (!(c)) { g_why = std::string("line ") + std::to_string(__LINE__) + " " #c; return false; } } while (0)

// ---- synthetic game memory: one flat image starting at kBase
struct Image : IMemory {
    static constexpr Addr32 kBase = 0x10000;
    std::vector<uint8_t> b = std::vector<uint8_t>(0x4000, 0);
    Addr32 top = kBase + 0x2000;  // bump allocator for strings/responses
    bool read(Addr32 a, void* out, size_t n) const override {
        if (a < kBase || a - kBase + n > b.size()) return false;
        std::memcpy(out, &b[a - kBase], n);
        return true;
    }
    void put(Addr32 a, const void* p, size_t n) { std::memcpy(&b[a - kBase], p, n); }
    void u32(Addr32 a, uint32_t v) { put(a, &v, 4); }
    void u8(Addr32 a, uint8_t v) { put(a, &v, 1); }
    Addr32 alloc(size_t n) { Addr32 r = top; top += Addr32((n + 3) & ~3u); return r; }
    void str(Addr32 at, const std::string& s) {  // writes an xNVSE String {char*, u16 len, u16 cap}
        Addr32 d = alloc(s.size() + 1);
        put(d, s.c_str(), s.size() + 1);
        u32(at, d);
        uint16_t l = uint16_t(s.size());
        put(at + 4, &l, 2);
        put(at + 6, &l, 2);
    }
    Addr32 response(const std::string& text, uint32_t emotion, Addr32 next) {
        Addr32 r = alloc(response::kSize);
        u32(r + response::kEmotionType, emotion);
        u32(r + response::kEmotionValue, 0x32);
        u8(r + response::kFlags, 1);
        str(r + response::kText, text);
        u32(r + response::kNext, next);
        return r;
    }
    Addr32 info(const std::vector<std::pair<std::string, uint32_t>>& lines, uint32_t headOffset, uint8_t flags1,
                const std::string& prompt) {
        Addr32 i = alloc(topic_info::kSize);
        Addr32 next = 0;
        for (size_t k = lines.size(); k-- > 0;) next = response(lines[k].first, lines[k].second, next);
        u32(i + headOffset, next);
        u8(i + topic_info::kFlags1, flags1);
        str(i + topic_info::kPrompt, prompt);
        return i;
    }
};

static bool t_menu_and_flags() {
    Image m;
    Addr32 menu = m.alloc(dialog_menu::kSize);
    Addr32 a = m.info({{"Cat.", 0}}, topic_info::kUnk48, 1, "Cat.");
    Addr32 b = m.info({{"Feed.", 0}}, topic_info::kUnk48, 0, "Feed.");
    m.u32(menu + dialog_menu::kPartnerRef, 0xABCD00);
    m.u32(menu + dialog_menu::kInfo078, a);
    m.u32(menu + dialog_menu::kInfos094, a);
    m.u32(menu + dialog_menu::kInfos094 + 8, b);          // gaps are skipped
    m.u32(menu + dialog_menu::kInfos110 + 4, b);
    m.u8(menu + dialog_menu::kIsBarterShown, 1);
    MenuView v;
    CHECK(readMenu(m, menu, v));
    CHECK(v.partnerRef == 0xABCD00 && v.spokenInfo == a && v.barterShown);
    CHECK(v.offered.size() == 3 && v.offered[0] == a && v.offered[1] == b && v.offered[2] == b);
    InfoFlags f;
    CHECK(readInfoFlags(m, a, f) && f.goodbye && f.prompt == "Cat.");
    CHECK(readInfoFlags(m, b, f) && !f.goodbye);
    CHECK(!readMenu(m, 0, v));
    CHECK(!readMenu(m, 0xF000000, v));                    // unreadable memory is an error, not a crash
    return true;
}

static bool t_probe_and_lines(uint32_t headOffset, uint32_t probeAs) {
    Image m;
    Addr32 i = m.info({{"All right. I'm gonna say a word.", 2}, {"Say the first thing.", 0}}, headOffset, 0, "");
    int32_t off = probeResponseOffset(m, i, "All right.");
    CHECK(off == int32_t(headOffset));
    CHECK(probeResponseOffset(m, i, "Different text") == -1);              // ESM cross-check refuses a mismatch
    ResponseLayout lay; lay.headOffset = int32_t(probeAs);
    std::vector<fnvcoop_dlg::Line> lines;
    CHECK(readLines(m, i, lay, lines));
    CHECK(lines.size() == 2 && lines[0].text.rfind("All right.", 0) == 0 && lines[0].emotion == 2 && lines[1].emotionValue == 0x32);
    return true;
}
static bool t_probe_ok() { return t_probe_and_lines(topic_info::kUnk48, topic_info::kUnk48); }
static bool t_probe_wrong_layout_twin() { return t_probe_and_lines(topic_info::kUnk48, topic_info::kUnk48 - 4); }
static bool t_probe_other_offset() { return t_probe_and_lines(0x34, 0x34); }

static bool t_voice() {
    CHECK(voiceFile("FalloutNV.esm", "MaleOldVoice", "VCG01Intro", 0x104BF3, 1) == "falloutnv.esm\\maleoldvoice\\vcg01intro_00104bf3_1.ogg");
    return true;
}

// ---- glue over a mock engine
struct Mock : IEngineApi {
    Image img;
    Addr32 menu = 0;
    std::map<Addr32, std::pair<FormRef, FormRef>> topicOfInfo;   // info -> (topic, infoRef)
    std::vector<std::string> log, subs;
    std::vector<std::string> voices;
    int resumed = 0, rebuilt = 0, forced = 0, hidden = 0, denied = 0, unsup = 0;
    PlayerId deniedOwner = 255;
    uint8_t lockMask = 0; bool locked = false;
    const IMemory& memory() override { return img; }
    Addr32 dialogMenu() override { return menu; }
    bool formRefOf(Addr32, FormRef&) override { return false; }
    bool topicOf(Addr32 i, FormRef& t, std::string& e) override { auto it = topicOfInfo.find(i); if (it == topicOfInfo.end()) return false; t = it->second.first; e = "TOPIC"; return true; }
    bool infoRefOf(Addr32 i, FormRef& r) override { auto it = topicOfInfo.find(i); if (it == topicOfInfo.end()) return false; r = it->second.second; return true; }
    std::string voicePath(Addr32, uint32_t n, Addr32) override { return "v\\line_" + std::to_string(n) + ".ogg"; }
    std::vector<fnvcoop_dlg::Fact> collectFacts(Addr32) override { return {{1, FormRef("falloutnv.esm", 43), 30}}; }
    CheckKind classifyCheck(Addr32, const InfoFlags& f) override { return f.speechChallenge ? CheckKind::Speech : CheckKind::None; }
    void resumeTalk(const FormRef&) override { ++resumed; }
    void talkDenied(DenyReason, PlayerId owner) override { ++denied; deniedOwner = owner; }
    void rebuildTopicList() override { ++rebuilt; }
    void forceCloseDialog() override { ++forced; }
    void setControls(uint8_t m, bool l) override { lockMask = m; locked = l; }
    void banner(const FormRef&, PlayerId) override { log.push_back("banner"); }
    void showTopics(const std::vector<fnvcoop_dlg::TopicEntry>& t) override { log.push_back("topics" + std::to_string(t.size())); }
    void showSubtitle(const FormRef&, const std::string& t, float) override { subs.push_back(t); }
    void playVoice(const FormRef&, const std::string& p) override { voices.push_back(p); }
    void hideOverlay() override { ++hidden; }
    std::vector<Addr32> hiddenTopics; std::vector<fnvcoop_dlg::MenuRequest> menusOpened; std::vector<std::string> notices; std::vector<uint32_t> menusClosed;
    void hideTopic(Addr32 i) override { hiddenTopics.push_back(i); }
    bool audible = true; std::vector<std::pair<FormRef, FormRef>> forcedStarts;
    bool npcAudible(const FormRef&) override { return audible; }
    void startForcedConversation(const FormRef& n, const FormRef& t) override { forcedStarts.emplace_back(n, t); }
    void openMenu(const fnvcoop_dlg::MenuRequest& r) override { menusOpened.push_back(r); }
    void closeMenu(uint32_t m) override { menusClosed.push_back(m); }
    void menuNotice(const std::string& t, bool c) override { notices.push_back((c ? "closed:" : "open:") + t); }
    void unsupported(const char*) override { ++unsup; }
};

struct Rig {
    fnvcoop::InMemoryNetwork net{3};
    Mock mock[3];
    std::unique_ptr<EngineGlue> glue[3];
    std::unique_ptr<fnvcoop_dlg::DialogueNode> node[3];
    uint64_t now = 0;
    Rig() {
        fnvcoop::LinkParams lp; lp.minLatencyMs = lp.maxLatencyMs = 20; net.setLink(lp);
        for (PlayerId i = 0; i < 3; ++i) {
            glue[i].reset(new EngineGlue(mock[i]));
            fnvcoop_dlg::DialogueNode::Config c; c.self = i; c.isHost = i == 0; c.hostPeer = 0;
            c.peerOfSlot = [](PlayerId p) { return fnvcoop::PeerId(p); };
            c.slotOfPeer = [](fnvcoop::PeerId p) { return PlayerId(p); };
            c.ownerOnlyTopics = {FormRef("falloutnv.esm", 0x037071)};
            fnvcoop_dlg::ResultRunner run;
            if (i == 0) run = [](const fnvcoop_dlg::ResultRequest&) { return uint64_t(1); };
            node[i].reset(new fnvcoop_dlg::DialogueNode(net.endpoint(i), c, run, glue[i]->sink()));
            glue[i]->attach(node[i].get());
        }
        node[0]->playerJoined(1); node[0]->playerJoined(2);
    }
    void step(uint64_t ms) {
        for (uint64_t t = 0; t <= ms; t += 5) {
            if (t) now += 5;
            net.advance(now);
            for (PlayerId i = 0; i < 3; ++i) {
                fnvcoop::PeerId from; fnvcoop::Bytes b;
                while (net.endpoint(i)->poll(from, b)) node[i]->onPacket(from, b, uint32_t(now));
                glue[i]->onFrame(uint32_t(now));
            }
        }
    }
};

static bool t_glue_flow(bool breakLayout) {
    Rig r;
    FormRef npc("falloutnv.esm", 0x104C0F);
    // initiator (slot 1) world: a menu offering one INFO with two response lines
    Mock& m = r.mock[1];
    Addr32 info = m.img.info({{"House.", 0}, {"Next word.", 1}}, topic_info::kUnk48, 0, "Cat.");
    m.menu = m.img.alloc(dialog_menu::kSize);
    m.img.u32(m.menu + dialog_menu::kPartnerRef, 0x777);
    m.img.u32(m.menu + dialog_menu::kInfos094, info);
    m.topicOfInfo[info] = {FormRef("falloutnv.esm", 0x105560), FormRef("falloutnv.esm", 0x105583)};
    int32_t off = m.img.top ? probeResponseOffset(m.img, info, "House.") : -1;
    CHECK(off == int32_t(topic_info::kUnk48));
    r.glue[1]->setLayout(ResponseLayout{breakLayout ? int32_t(off - 4) : off});

    CHECK(r.glue[1]->onTalkRequested(npc, uint32_t(r.now)));       // vanilla deferred
    CHECK(m.resumed == 0);                                         // not before the grant
    r.step(100);
    CHECK(m.resumed == 1);                                         // grant -> resume vanilla
    CHECK(r.mock[0].log == std::vector<std::string>{"banner"} && r.mock[2].log == std::vector<std::string>{"banner"});

    r.glue[1]->onTopicsBuilt(uint32_t(r.now));
    r.step(100);
    CHECK(r.mock[2].log.back() == "topics1");

    CHECK(r.glue[1]->onInfoChosen(info, uint32_t(r.now)));         // initiator picks
    r.step(100);
    CHECK(r.mock[2].subs.size() == 2 && r.mock[2].subs[0] == "House." && r.mock[2].subs[1] == "Next word.");
    CHECK(r.mock[2].voices.size() == 2 && r.mock[2].voices[0] == "v\\line_1.ogg");
    CHECK(r.mock[0].subs.size() == 2);                             // host sees it as spectator too
    CHECK(r.mock[1].subs.empty());                                 // initiator never gets its own line echoed

    r.glue[1]->onLineFinished(uint32_t(r.now));
    r.step(100);
    CHECK(m.rebuilt == 1);

    // second player tries to talk to the same NPC: denied with the owner
    CHECK(r.glue[2]->onTalkRequested(npc, uint32_t(r.now)));
    r.step(100);
    CHECK(r.mock[2].denied == 1 && r.mock[2].deniedOwner == 1 && r.mock[2].resumed == 0);

    // NPC dies: initiator's menu is force-closed, spectators' overlays hide
    r.node[0]->npcDied(npc, uint32_t(r.now));
    r.step(100);
    CHECK(m.forced == 1 && r.mock[0].hidden == 1);
    return true;
}
static bool t_glue_ok() { return t_glue_flow(false); }
static bool t_glue_broken_layout_twin() { return t_glue_flow(true); }

static bool t_unknown_layout_reports() {
    Rig r;
    Mock& m = r.mock[1];
    Addr32 info = m.img.info({{"x", 0}}, topic_info::kUnk48, 0, "p");
    m.menu = m.img.alloc(dialog_menu::kSize);
    m.topicOfInfo[info] = {FormRef("falloutnv.esm", 1), FormRef("falloutnv.esm", 2)};
    r.glue[1]->onTalkRequested(FormRef("falloutnv.esm", 9), 0);
    r.step(100);
    CHECK(!r.glue[1]->onInfoChosen(info, 0) && m.unsup == 1);      // layout never probed -> loud, no bogus pick
    return true;
}

static bool t_control_lock_and_goodbye() {
    Rig r;
    FormRef npc("falloutnv.esm", 5);
    r.glue[1]->onTalkRequested(npc, 0);
    r.step(100);
    r.node[0]->controlLock(1, 0x3F, true, uint32_t(r.now));
    r.step(100);
    CHECK(r.mock[1].locked && r.mock[1].lockMask == 0x3F);
    r.glue[1]->onMenuClosedByPlayer(uint32_t(r.now));
    r.step(100);
    CHECK(!r.mock[1].locked);                                      // lease released on close
    CHECK(r.mock[1].forced == 1 && r.mock[2].hidden == 1);
    return true;
}

static bool t_glue_hides_commands_for_non_owner(bool owner) {
    Rig r;
    const FormRef boone("falloutnv.esm", 0x5001), wait("falloutnv.esm", 0x037071), chat("falloutnv.esm", 0x1002);
    for (PlayerId i = 0; i < 3; ++i) { r.node[i]->companions().define(boone, fnvcoop_dlg::CompanionKind::Humanoid); r.node[i]->companions().setOwner(boone, 1); }
    PlayerId who = owner ? 1 : 2;
    Mock& m = r.mock[who];
    Addr32 a = m.img.info({{"Waiting.", 0}}, topic_info::kUnk48, 0, "Wait here.");
    Addr32 b = m.img.info({{"Hm.", 0}}, topic_info::kUnk48, 0, "Chat");
    m.menu = m.img.alloc(dialog_menu::kSize);
    m.img.u32(m.menu + dialog_menu::kInfos094, a);
    m.img.u32(m.menu + dialog_menu::kInfos094 + 4, b);
    m.topicOfInfo[a] = {wait, FormRef("falloutnv.esm", 0x9001)};
    m.topicOfInfo[b] = {chat, FormRef("falloutnv.esm", 0x9002)};
    r.glue[who]->onTalkRequested(boone, 0);
    r.step(100);
    r.glue[who]->onTopicsBuilt(uint32_t(r.now));
    r.step(100);
    return owner ? m.hiddenTopics.empty() : m.hiddenTopics.size() == 1 && m.hiddenTopics[0] == a;
}

static bool t_glue_menus() {
    Rig r;
    std::vector<std::tuple<uint32_t, int32_t, bool>> res;
    r.node[0]->setMenuResume([&](uint32_t mnu, int32_t c, bool t) { res.emplace_back(mnu, c, t); });
    uint32_t id = r.node[0]->raiseMenu(2, fnvcoop_dlg::MenuKind::MessageBox, "Take it?", {"Yes", "No"}, 1, FormRef(), uint32_t(r.now));
    r.step(100);
    CHECK(r.mock[2].menusOpened.size() == 1 && r.mock[2].menusOpened[0].text == "Take it?");   // the real menu opens on p2
    CHECK(r.mock[0].menusOpened.empty() && r.mock[1].menusOpened.empty());
    CHECK(r.mock[1].notices.size() == 1 && r.mock[1].notices[0].rfind("open:", 0) == 0);        // others: read-only notice
    r.glue[2]->onMenuAnswered(id, 0, uint32_t(r.now));
    r.step(100);
    CHECK(res.size() == 1 && std::get<1>(res[0]) == 0);
    CHECK(r.mock[1].notices.back() == "closed:Yes");
    return true;
}

static bool t_glue_forced_say_audibility() {
    Rig r;
    const FormRef benny("falloutnv.esm", 0x7777), topic("falloutnv.esm", 0x1111), info("falloutnv.esm", 0x2222);
    r.mock[1].audible = false;                                      // p1 is far away; p0 (host) and p2 can hear
    r.node[0]->forcedSay(benny, topic, info, {fnvcoop_dlg::Line{"On your knees.", "v.ogg", 0, 50}}, 2, false, 5, uint32_t(r.now));
    r.step(100);
    CHECK(r.mock[2].subs.size() == 1 && r.mock[2].subs[0] == "On your knees.");     // the target
    CHECK(r.mock[0].subs.size() == 1);                                               // audible bystander
    CHECK(r.mock[1].subs.empty());                                                   // out of earshot
    r.node[0]->forcedSay(benny, topic, info, {fnvcoop_dlg::Line{"Cutscene line.", "", 0, 50}}, fnvcoop::kNoPlayer, true, 6, uint32_t(r.now));
    r.step(100);
    CHECK(r.mock[1].subs.size() == 1 && r.mock[1].subs[0] == "Cutscene line.");      // scenes reach everybody
    return true;
}

static bool t_glue_forced_conversation() {
    Rig r;
    const FormRef benny("falloutnv.esm", 0x7777), topic("falloutnv.esm", 0x1111);
    PlayerId actor = r.node[0]->forceConversation(benny, topic, 2, uint32_t(r.now));
    CHECK(actor == 2);
    r.step(100);
    CHECK(r.mock[2].forcedStarts.size() == 1 && r.mock[2].forcedStarts[0].second == topic);   // native start only on the target
    CHECK(r.mock[0].forcedStarts.empty() && r.mock[1].forcedStarts.empty());
    CHECK(r.node[2]->client().role() == fnvcoop_dlg::ClientDialogue::Role::Initiator);
    CHECK(r.node[1]->client().role() == fnvcoop_dlg::ClientDialogue::Role::Spectator);
    return true;
}

static bool t_info_reader_factory() {
    Image m;
    Addr32 i = m.info({{"All right. I'm gonna say a word.", 2}, {"Say the first thing.", 0}}, topic_info::kUnk48, 0, "");
    ResponseLayout lay;                                              // unknown layout: the reader must return nothing, not garbage
    auto rd = makeInfoReader(m, &lay);
    CHECK(rd(i).empty());
    lay.headOffset = probeResponseOffset(m, i, "All right.");        // probing later takes effect without reinstalling the reader
    auto lines = rd(i);
    CHECK(lines.size() == 2 && lines[1].text == "Say the first thing.");
    CHECK(rd(0).empty());
    return true;
}

int main() {
    struct C { const char* name; bool (*fn)(); bool twin; };
    C cases[] = {{"menu_and_flags", t_menu_and_flags, false}, {"probe_and_lines_unk48", t_probe_ok, false},
                 {"probe_and_lines_other_offset", t_probe_other_offset, false}, {"voice_path", t_voice, false},
                 {"glue_flow", t_glue_ok, false}, {"unknown_layout_reports", t_unknown_layout_reports, false},
                 {"control_lock_and_leave", t_control_lock_and_goodbye, false},
                 {"glue_menus", t_glue_menus, false},
                 {"info_reader_factory", t_info_reader_factory, false},
                 {"glue_forced_say_audibility", t_glue_forced_say_audibility, false},
                 {"glue_forced_conversation", t_glue_forced_conversation, false},
                 {"glue_hides_commands_non_owner", []() { return t_glue_hides_commands_for_non_owner(false); }, false},
                 {"glue_keeps_commands_owner", []() { return t_glue_hides_commands_for_non_owner(true); }, false},
                 {"twin_probe_wrong_layout", t_probe_wrong_layout_twin, true},
                 {"twin_glue_broken_layout", t_glue_broken_layout_twin, true}};
    int fail = 0;
    for (auto& c : cases) {
        g_why.clear();
        bool ok = c.fn();
        bool pass = c.twin ? !ok : ok;
        std::printf("[%s] %s%s%s\n", pass ? "PASS" : "FAIL", c.name, c.twin ? (ok ? " (mutant survived)" : " (caught: ") : "", c.twin && !ok ? (g_why + ")").c_str() : (ok ? "" : (" : " + g_why).c_str()));
        fail += !pass;
    }
    std::printf("%d failed\n", fail);
    return fail ? 1 : 0;
}
