// Round 4 scenarios: forced dialogue (SayTo / StartConversation) and ModalChoice party consent.

static const FormRef kBenny = FormRef("falloutnv.esm", 0x7777), kTopic = FormRef("falloutnv.esm", 0x1111), kInfoF = FormRef("falloutnv.esm", 0x2222);

static bool scn_forced_say_reaches_everyone_once() {
    World w(3);
    w.net.dupAll = true;                                                   // duplicated deliveries must not replay the line
    w.fan(w.host.forcedSay(kBenny, kTopic, kInfoF, {L("On your knees.")}, 2, false, 41));
    w.pump();
    for (PlayerId p = 0; p < 3; ++p) CHECK(w.count(p, Event::ForcedSayShow) == 1);
    const auto* f = std::get_if<ForcedLines>(&w.log[1].back().msg);
    CHECK(f && f->target == 2 && !f->scene && f->txnSeq == 41 && f->lines.size() == 1 && f->lines[0].text == "On your knees.");
    w.fan(w.host.forcedSay(kBenny, kTopic, kInfoF, {L("Again.")}, 2, false, 42));
    w.pump();
    CHECK(w.count(2, Event::ForcedSayShow) == 2);                          // a new scripted line is a new id
    return true;
}

static bool scn_forced_conversation_opens_on_target_only() {
    World w(3);
    PlayerId actor = 255;
    w.fan(w.host.forceConversation(kBenny, kTopic, 1, 0, &actor));
    w.pump();
    CHECK(actor == 1);
    CHECK(w.count(1, Event::ForcedOpen) == 1 && w.count(0, Event::ForcedOpen) == 0 && w.count(2, Event::ForcedOpen) == 0);
    CHECK(w.cl[1].role() == ClientDialogue::Role::Initiator);              // the target is the initiator: normal picking follows
    CHECK(w.cl[0].role() == ClientDialogue::Role::Spectator && w.cl[2].role() == ClientDialogue::Role::Spectator);
    CHECK(w.host.npcBusy(kBenny));
    // another player cannot barge in, the actor then drives the conversation normally
    w.emit(2, w.cl[2].requestStart(kBenny));
    w.pump();
    CHECK(w.count(2, Event::Denied) == 1);
    w.emit(1, w.cl[1].pick(F(0x10), F(0x11), 0, true, {L("Benny talks.")}, {}));
    w.pump();
    w.emit(1, w.cl[1].lineDone(w.net.now));
    w.pump();
    CHECK(w.runsOf(Phase::Begin) == 1 && w.runsOf(Phase::End) == 1 && !w.host.npcBusy(kBenny));
    return true;
}

static bool scn_forced_conversation_displaces_others() {
    World w(3);
    w.emit(2, w.cl[2].requestStart(kBenny));                               // p2 is chatting with Benny
    w.emit(1, w.cl[1].requestStart(F(0x4242)));                            // p1 is busy with someone else
    w.pump();
    PlayerId actor = 255;
    w.fan(w.host.forceConversation(kBenny, kTopic, 1, 10, &actor));
    w.pump();
    CHECK(actor == 1 && w.cl[1].role() == ClientDialogue::Role::Initiator && w.host.initiatorOf(3) == 1);
    CHECK(!w.host.npcBusy(F(0x4242)));                                     // p1's old conversation is gone
    bool preempted = false;
    for (auto& e : w.log[2]) if (e.kind == Event::CloseMenu && e.reason == CloseReason::Preempted) preempted = true;
    CHECK(preempted);
    CHECK(w.host.open() == 1);
    return true;
}

static bool scn_forced_party_scene_actor_choice() {
    World w(3);
    PlayerId actor = 255;
    w.host.setActorChooser([](const FormRef&, uint8_t mask) { return PlayerId((mask & 0x04) ? 2 : 0); });   // e.g. nearest player
    w.fan(w.host.forceConversation(kBenny, kTopic, fnvcoop::kNoPlayer, 0, &actor));
    w.pump();
    CHECK(actor == 2 && w.count(2, Event::ForcedOpen) == 1 && w.count(0, Event::ForcedOpen) == 0);
    return true;
}

static bool scn_forced_target_gone_falls_back() {
    World w(3);
    w.fan(w.host.playerLeft(1, 0));
    PlayerId actor = 255;
    w.fan(w.host.forceConversation(kBenny, kTopic, 1, 0, &actor));         // aimed at a player who just left
    w.pump();
    CHECK(actor == 0 && w.count(0, Event::ForcedOpen) == 1);               // lowest connected slot (no chooser set)
    w.fan(w.host.playerLeft(0, 0)); w.fan(w.host.playerLeft(2, 0));
    CHECK(w.host.forceConversation(F(0x99), kTopic, 1, 0, &actor).empty() && actor == fnvcoop::kNoPlayer);   // nobody left: no-op
    return true;
}

static bool scn_forced_dead_npc_ignored() {
    World w(2);
    w.fan(w.host.npcDied(kBenny, 0));
    PlayerId actor = 255;
    CHECK(w.host.forceConversation(kBenny, kTopic, 1, 0, &actor).empty() && actor == fnvcoop::kNoPlayer);
    return true;
}

static bool scn_forced_codec() {
    ForcedLines f; f.id = 7; f.kind = ForcedKind::Conversation; f.npc = kBenny; f.topic = kTopic; f.info = kInfoF; f.target = 3;
    f.scene = true; f.txnSeq = 0x123456789ull; f.lines = {L("a"), L("b")};
    ForcedStart s; s.id = 8; s.conv = 4; s.npc = kBenny; s.topic = kTopic;
    MenuRequest rq; rq.menu = 9; rq.kind = MenuKind::MessageBox; rq.origin = 1; rq.text = "Go?"; rq.buttons = {"Ready", "Not yet"}; rq.consent = true;
    for (const Msg& m : std::vector<Msg>{f, s, rq}) {
        auto b = encode(m);
        Msg d;
        CHECK(decode(b, d) == DecodeStatus::Ok && d.index() == m.index() && encode(d) == b);
        CHECK(decode(b.data(), b.size() - 1, d) != DecodeStatus::Ok);
    }
    return true;
}

// ---------- ModalChoice with party consent
static const HostDialogue::ModalOptions kConsent = [] { HostDialogue::ModalOptions o; o.partyConsent = true; o.confirmButton = 0; o.declineChoice = 1; return o; }();

static uint32_t raiseConsent(MenuWorld& m, PlayerId origin) {
    uint32_t id = 0;
    m.w.fan(m.w.host.raiseMenu(origin, MenuKind::MessageBox, "Start the battle of Hoover Dam?", {"Begin", "Not yet"}, 1, FormRef(), m.w.net.now, &id, kConsent));
    m.w.pump();
    return id;
}

static bool scn_consent_all_ready() {
    MenuWorld m; World& w = m.w;
    uint32_t id = raiseConsent(m, 2);
    w.emit(2, w.cl[2].menuReply(id, 0));                                   // origin presses "Begin"
    w.pump();
    CHECK(m.resumed.empty());                                              // the point of no return waits for the party
    CHECK(w.count(0, Event::MenuShow) == 1 && w.count(1, Event::MenuShow) == 1);    // consent prompts on the other two
    const auto* rq = std::get_if<MenuRequest>(&w.log[1].back().msg);
    CHECK(rq && rq->consent && rq->buttons.size() == 2 && rq->buttons[0] == "Ready");
    w.emit(0, w.cl[0].menuReply(id, 0));
    w.pump();
    CHECK(m.resumed.empty());                                              // still one missing
    w.emit(1, w.cl[1].menuReply(id, 0));
    w.pump();
    CHECK(m.resumed.size() == 1 && std::get<1>(m.resumed[0]) == 0 && !std::get<2>(m.resumed[0]));
    return true;
}

static bool scn_consent_declined() {
    MenuWorld m; World& w = m.w;
    uint32_t id = raiseConsent(m, 2);
    w.emit(2, w.cl[2].menuReply(id, 0));
    w.pump();
    w.emit(1, w.cl[1].menuReply(id, 1));                                   // "Not yet"
    w.pump();
    CHECK(m.resumed.size() == 1 && std::get<1>(m.resumed[0]) == 1);       // the script gets the decline choice
    w.emit(0, w.cl[0].menuReply(id, 0));                                   // late consent after the decision
    w.pump();
    CHECK(m.resumed.size() == 1 && w.host.pendingMenus() == 0);
    return true;
}

static bool scn_consent_timeout_declines() {
    HostDialogue::Config c; c.messageMenuMs = 1000;
    World w(3, c);
    std::vector<std::tuple<uint32_t, int32_t, bool>> res;
    w.host.setMenuResume([&](uint32_t mnu, int32_t ch, bool t) { res.emplace_back(mnu, ch, t); });
    uint32_t id = 0;
    w.fan(w.host.raiseMenu(2, MenuKind::MessageBox, "Go?", {"Begin", "Not yet"}, 1, FormRef(), 0, &id, kConsent));
    w.pump();
    w.fan(w.host.onMessage(2, MenuReply{id, 0}, 100));
    w.fan(w.host.onMessage(0, MenuReply{id, 0}, 200));
    w.fan(w.host.tick(1000));                                              // still inside the window that restarted at 100
    CHECK(res.empty());
    w.fan(w.host.tick(1200));                                              // player 1 never answered
    CHECK(res.size() == 1 && std::get<1>(res[0]) == 1 && std::get<2>(res[0]));
    return true;
}

static bool scn_consent_leaver_does_not_block() {
    MenuWorld m; World& w = m.w;
    uint32_t id = raiseConsent(m, 2);
    w.emit(2, w.cl[2].menuReply(id, 0));
    w.pump();
    w.emit(0, w.cl[0].menuReply(id, 0));
    w.pump();
    w.fan(w.host.playerLeft(1, 5));                                        // the player we were waiting for drops
    w.pump();
    CHECK(m.resumed.size() == 1 && std::get<1>(m.resumed[0]) == 0);
    return true;
}

static bool scn_consent_not_needed_for_other_buttons() {
    MenuWorld m; World& w = m.w;
    uint32_t id = raiseConsent(m, 2);
    w.emit(2, w.cl[2].menuReply(id, 1));                                   // origin picks "Not yet": nobody else is asked
    w.pump();
    CHECK(m.resumed.size() == 1 && std::get<1>(m.resumed[0]) == 1 && w.count(0, Event::MenuShow) == 0);
    return true;
}

static bool scn_consent_only_required_players_answer() {
    MenuWorld m; World& w = m.w;
    uint32_t id = raiseConsent(m, 2);
    w.fan(w.host.onMessage(2, MenuReply{id, 0}, 0));
    w.fan(w.host.onMessage(2, MenuReply{id, 0}, 0));                       // the origin cannot consent for the others
    w.fan(w.host.onMessage(0, MenuReply{id, 5}, 0));                       // invalid consent value
    CHECK(m.resumed.empty());
    CHECK(w.host.rejected() >= 2);
    return true;
}

// ---------- consent message table + info reader factory
static bool scn_consent_message_table() {
    std::istringstream in("# c\nFalloutNV.esm:171DC2 VEndGameWarningMsgLegion confirm=1 decline=0 buttons=I'm not ready yet.|Let's do this!\nbad line\n");
    auto t = loadConsentMessages(in);
    auto it = t.find(FormRef("falloutnv.esm", 0x171DC2));
    CHECK(t.size() == 1 && it != t.end() && it->second.confirmButton == 1 && it->second.declineChoice == 0 && it->second.edid == "VEndGameWarningMsgLegion");
    return true;
}
