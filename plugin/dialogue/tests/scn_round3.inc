// Round 3 scenarios (included by main.cpp after World/CHECK/F/L are defined): companions + host-run-script menus.

// ---------- companions
static const FormRef kQuest = FormRef("falloutnv.esm", 0x0B16D0);   // VNPCFollowers
static FormRef npcOf(const char* n) { uint32_t h = 0; for (; *n; ++n) h = h * 31 + uint32_t(*n); return FormRef("falloutnv.esm", 0x10000 + (h & 0xFFFF)); }
static std::map<std::string, FormRef> companionNpcs() {
    std::map<std::string, FormRef> m;
    for (auto& c : vnpc::kCompanions) m[c.name] = npcOf(c.name);
    return m;
}
static const FormRef kWait = FormRef("falloutnv.esm", 0x037071), kChat = FormRef("falloutnv.esm", 0x1002);

// World whose host knows the companion registry; slot 1 owns Boone.
struct CompWorld {
    CompanionRegistry reg;
    World w;
    CompWorld() : w(3) {
        registerVanillaFollowers(reg, kQuest, companionNpcs());
        w.host.setCompanions(&reg, {kWait});
    }
};

static bool scn_comp_command_owner_only_hidden_from_spectators() {
    CompWorld c; World& w = c.w;
    c.reg.recruit(npcOf("Boone"), 1);
    w.emit(1, w.cl[1].requestStart(npcOf("Boone")));
    w.pump();
    w.emit(1, w.cl[1].offer({{kWait, 0, 0}, {kChat, 0, 0}}));
    w.pump();
    CHECK(w.cl[2].spectatorTopics().size() == 1 && w.cl[2].spectatorTopics()[0].topic == kChat);   // command topic never shown
    CHECK(w.cl[0].spectatorTopics().size() == 1);
    w.emit(1, w.cl[1].pick(kWait, F(0x9001), 0, false, {L("Waiting here.")}, {}));
    w.pump();
    CHECK(w.runsOf(Phase::Begin) == 1);                                    // the owner's command runs on the host
    CHECK(w.cl[2].history().empty() && w.cl[0].history().empty());         // and nobody else is shown the line
    w.emit(1, w.cl[1].lineDone(w.net.now));
    w.pump();
    w.emit(1, w.cl[1].pick(kChat, F(0x9002), 0, false, {L("Small talk.")}, {}));
    w.pump();
    CHECK(w.cl[2].history().size() == 1);                                  // ordinary topics stay visible
    return true;
}

static bool scn_comp_command_rejected_for_non_owner() {
    CompWorld c; World& w = c.w;
    c.reg.recruit(npcOf("Boone"), 1);
    w.emit(2, w.cl[2].requestStart(npcOf("Boone")));                       // p2 chats with p1's companion
    w.pump();
    CHECK(w.cl[2].role() == ClientDialogue::Role::Initiator);
    w.emit(2, w.cl[2].pick(kWait, F(0x9001), 0, false, {L("Hijack")}, {}));
    w.pump();
    CHECK(w.runs.empty() && w.host.rejected() == 1);                       // "Wait here" from a stranger does nothing
    CHECK(w.cl[1].history().empty());                                       // the owner is not shown it either
    return true;
}

static bool scn_comp_owner_preempts() {
    CompWorld c; World& w = c.w;
    c.reg.recruit(npcOf("Boone"), 1);
    w.emit(2, w.cl[2].requestStart(npcOf("Boone")));
    w.pump();
    w.emit(1, w.cl[1].requestStart(npcOf("Boone")));
    w.pump();
    CHECK(w.cl[1].role() == ClientDialogue::Role::Initiator);              // owner takes the companion over
    CHECK(w.cl[2].role() == ClientDialogue::Role::Spectator);              // and the old chatter now watches the owner's conversation
    bool preempted = false;
    for (auto& e : w.log[2]) if (e.kind == Event::CloseMenu && e.reason == CloseReason::Preempted) preempted = true;
    CHECK(preempted);
    // a non-owner cannot take it from the owner
    w.emit(0, w.cl[0].requestStart(npcOf("Boone")));
    w.pump();
    CHECK(w.count(0, Event::Denied) == 1 && w.cl[1].role() == ClientDialogue::Role::Initiator);
    return true;
}

static bool scn_comp_per_owner_view() {
    CompWorld c;
    auto& r = c.reg;
    const FormRef boone = npcOf("Boone"), cass = npcOf("Cass"), rex = npcOf("Rex");
    CHECK(r.recruit(boone, 1) == RecruitResult::Ok);
    CHECK(r.viewVar(kQuest, vnpc::kHumanoidInParty, 1, 0) == 1);          // p1 has their humanoid
    CHECK(r.viewVar(kQuest, vnpc::kHumanoidInParty, 2, 1) == 0);          // p2 still can recruit (real shared value says 1)
    CHECK(r.aggregateVar(kQuest, vnpc::kHumanoidInParty, 0) == 1);        // host-side scripts keep the party aggregate
    CHECK(r.viewVar(kQuest, vnpc::kCritterInParty, 1, 0) == 0);
    CHECK(r.viewVar(kQuest, vnpc::kCurrentFollowers, 1, 0) == 1 && r.viewVar(kQuest, vnpc::kCurrentFollowers, 2, 1) == 0);
    CHECK(r.viewVar(kQuest, vnpc::kHasFollower, 2, 1) == 0 && r.viewVar(kQuest, vnpc::kHasFollower, 1, 0) == 1);
    const uint32_t booneHired = 6, booneAvail = 8, cassAvail = 23, cassHired = 21;
    CHECK(r.viewVar(kQuest, booneHired, 1, 1) == 1 && r.viewVar(kQuest, booneHired, 2, 1) == 0);
    CHECK(r.viewVar(kQuest, booneAvail, 2, 1) == 0);                      // hired by someone else: not offered to p2
    CHECK(r.viewVar(kQuest, booneAvail, 1, 1) == 1);
    CHECK(r.viewVar(kQuest, cassAvail, 2, 1) == 1 && r.viewVar(kQuest, cassHired, 2, 1) == 0);   // free companion unaffected
    CHECK(r.viewVar(kQuest, 999, 2, 7) == 7);                             // unknown index: passthrough
    CHECK(r.viewVar(FormRef("falloutnv.esm", 1), vnpc::kHumanoidInParty, 2, 5) == 5);   // other quest: passthrough
    (void)cass; (void)rex;
    return true;
}

static bool scn_comp_caps_per_owner() {
    CompWorld c;
    auto& r = c.reg;
    CHECK(r.recruit(npcOf("Boone"), 1) == RecruitResult::Ok);
    CHECK(r.recruit(npcOf("Cass"), 1) == RecruitResult::OwnerCapHumanoid);   // 1 humanoid per player
    CHECK(r.recruit(npcOf("Rex"), 1) == RecruitResult::Ok);                  // + 1 critter
    CHECK(r.recruit(npcOf("Cass"), 2) == RecruitResult::Ok);                 // p2 has their own allowance
    CHECK(r.recruit(npcOf("EDE"), 2) == RecruitResult::Ok);
    CHECK(r.recruit(npcOf("Raul"), 3) == RecruitResult::PartyFull);          // party total 4
    CHECK(r.viewVar(kQuest, vnpc::kHumanoidInParty, 3, 0) == 1);             // recruiting closed for p3: topic hidden
    CHECK(r.recruit(npcOf("Boone"), 2) == RecruitResult::OwnedByOther);
    CHECK(r.recruit(npcOf("Boone"), 1) == RecruitResult::AlreadyYours);
    CHECK(r.recruit(FormRef("falloutnv.esm", 0x77), 1) == RecruitResult::Unknown);
    return true;
}

static bool scn_comp_dismiss_owner_only() {
    CompWorld c;
    auto& r = c.reg;
    r.recruit(npcOf("Boone"), 1);
    CHECK(!r.dismiss(npcOf("Boone"), 2) && r.ownerOf(npcOf("Boone")) == 1);
    CHECK(r.dismiss(npcOf("Boone"), 1) && r.ownerOf(npcOf("Boone")) == fnvcoop::kNoPlayer);
    CHECK(r.recruit(npcOf("Boone"), 2) == RecruitResult::Ok);                // free again, anyone can hire
    return true;
}

static bool scn_comp_owner_leaves() {
    CompWorld c; World& w = c.w;
    c.reg.recruit(npcOf("Boone"), 1);
    c.reg.recruit(npcOf("Rex"), 1);
    w.fan(w.host.playerLeft(1, 0));
    CHECK(w.host.lastReleased().size() == 2);
    CHECK(c.reg.ownerOf(npcOf("Boone")) == fnvcoop::kNoPlayer && c.reg.partyCount() == 0);
    return true;
}

static bool scn_comp_reconcile() {
    CompWorld c;
    std::map<std::pair<FormRef, uint32_t>, int32_t> real;
    real[{kQuest, vnpc::kHumanoidInParty}] = 0;
    real[{kQuest, vnpc::kCurrentFollowers}] = 0;
    c.reg.setOwner(npcOf("Boone"), 1);                                     // replicated CompanionOwner effect
    auto fixes = c.reg.reconcile(real);
    bool h = false, n = false;
    for (auto& f : fixes) { if (f.idx == vnpc::kHumanoidInParty && f.value == 1) h = true; if (f.idx == vnpc::kCurrentFollowers && f.value == 1) n = true; }
    CHECK(h && n);
    real[{kQuest, vnpc::kHumanoidInParty}] = 1; real[{kQuest, vnpc::kCurrentFollowers}] = 1;
    c.reg.setOwner(npcOf("Boone"), fnvcoop::kNoPlayer);                    // dismissed
    bool back = false;
    for (auto& f : c.reg.reconcile(real)) if (f.idx == vnpc::kHumanoidInParty && f.value == 0) back = true;
    CHECK(back);
    return true;
}

static bool scn_comp_topic_file() {
    std::istringstream in("# comment\nownerOnly FalloutNV.esm:037071 Wait here.\nrecruit FalloutNV.esm:037084 I think...\nownerOnly HonestHearts.esm:009DCB x\nbogus line\n");
    auto s = loadOwnerOnlyTopics(in);
    CHECK(s.size() == 2 && s.count(FormRef("falloutnv.esm", 0x37071)) && s.count(FormRef("honesthearts.esm", 0x9DCB)));
    CHECK(!s.count(FormRef("falloutnv.esm", 0x37084)));
    return true;
}

// ---------- menus raised by host-run scripts
struct MenuWorld {
    World w{3};
    std::vector<std::tuple<uint32_t, int32_t, bool>> resumed;
    MenuWorld() { w.host.setMenuResume([this](uint32_t m, int32_t c, bool t) { resumed.emplace_back(m, c, t); }); }
    uint32_t raise(PlayerId origin, MenuKind k = MenuKind::MessageBox, std::vector<std::string> b = {"Yes", "No"}, int32_t def = 1) {
        uint32_t id = 0;
        w.fan(w.host.raiseMenu(origin, k, "Take the caps?", std::move(b), def, FormRef(), w.net.now, &id));
        w.pump();
        return id;
    }
};

static bool scn_menu_opens_on_origin_only() {
    MenuWorld m; World& w = m.w;
    uint32_t id = m.raise(2);                                              // script triggered by p2, running on the host
    CHECK(w.count(2, Event::MenuShow) == 1);                               // the real menu opens on p2
    CHECK(w.count(0, Event::MenuShow) == 0 && w.count(1, Event::MenuShow) == 0);   // not on the host, not on p1
    CHECK(w.count(0, Event::MenuNoticeShow) == 1 && w.count(1, Event::MenuNoticeShow) == 1 && w.count(2, Event::MenuNoticeShow) == 0);
    CHECK(m.resumed.empty());                                              // the script waits
    w.emit(2, w.cl[2].menuReply(id, 0));
    w.pump();
    CHECK(m.resumed.size() == 1 && std::get<1>(m.resumed[0]) == 0 && !std::get<2>(m.resumed[0]));
    CHECK(w.count(0, Event::MenuEnd) == 1 && w.count(1, Event::MenuEnd) == 1 && w.count(2, Event::MenuEnd) == 1);
    const auto* mc = std::get_if<MenuClosed>(&w.log[1].back().msg);
    CHECK(mc && mc->label == "Yes");                                       // spectators learn what was chosen
    CHECK(w.host.pendingMenus() == 0);
    return true;
}

static bool scn_menu_request_mask_is_origin_only() {
    MenuWorld m; World& w = m.w;
    auto outs = w.host.raiseMenu(2, MenuKind::Barter, "Barter", {}, -1, F(0x104C0F), 0);
    CHECK(outs.size() == 2);
    CHECK(std::holds_alternative<MenuRequest>(outs[0].msg) && outs[0].to == 0x04);   // slot 2 only
    CHECK(std::holds_alternative<MenuNotice>(outs[1].msg) && outs[1].to == 0x03);
    return true;
}

static bool scn_menu_only_origin_replies() {
    MenuWorld m; World& w = m.w;
    uint32_t id = m.raise(2);
    w.fan(w.host.onMessage(1, MenuReply{id, 0}, 0));                      // a bystander answers someone else's prompt
    w.fan(w.host.onMessage(0, MenuReply{id, 0}, 0));
    w.pump();
    CHECK(m.resumed.empty() && w.host.pendingMenus() == 1 && w.host.rejected() == 2);
    return true;
}

static bool scn_menu_reply_resumes_once() {
    MenuWorld m; World& w = m.w;
    uint32_t id = m.raise(2);
    for (int i = 0; i < 3; ++i) w.fan(w.host.onMessage(2, MenuReply{id, 0}, 0));
    w.pump();
    CHECK(m.resumed.size() == 1);
    return true;
}

static bool scn_menu_choice_range() {
    MenuWorld m; World& w = m.w;
    uint32_t id = m.raise(2);
    w.fan(w.host.onMessage(2, MenuReply{id, 5}, 0));                       // only 2 buttons
    w.fan(w.host.onMessage(2, MenuReply{id, -7}, 0));
    CHECK(m.resumed.empty() && w.host.rejected() == 2);
    w.fan(w.host.onMessage(2, MenuReply{id, -1}, 0));                      // -1 = escape / cancel
    CHECK(m.resumed.size() == 1 && std::get<1>(m.resumed[0]) == -1);
    return true;
}

static bool scn_menu_timeout_takes_default() {
    HostDialogue::Config c; c.messageMenuMs = 1000;
    World w(3, c);
    std::vector<std::tuple<uint32_t, int32_t, bool>> res;
    w.host.setMenuResume([&](uint32_t m, int32_t ch, bool t) { res.emplace_back(m, ch, t); });
    uint32_t id = 0;
    w.fan(w.host.raiseMenu(2, MenuKind::MessageBox, "x", {"A", "B"}, 1, FormRef(), 0, &id));
    w.pump();
    w.fan(w.host.tick(500));
    CHECK(res.empty());
    w.fan(w.host.tick(1000));
    w.pump();
    CHECK(res.size() == 1 && std::get<1>(res[0]) == 1 && std::get<2>(res[0]));
    CHECK(w.count(2, Event::MenuEnd) == 1 && w.log[2].back().timedOut);    // origin's UI is forced shut
    w.fan(w.host.onMessage(2, MenuReply{id, 0}, 1100));                    // the late answer is ignored
    CHECK(res.size() == 1);
    return true;
}

static bool scn_menu_origin_disconnects() {
    MenuWorld m; World& w = m.w;
    m.raise(2);
    w.fan(w.host.playerLeft(2, 10));
    w.pump();
    CHECK(m.resumed.size() == 1 && std::get<1>(m.resumed[0]) == 1 && std::get<2>(m.resumed[0]));   // script continues with the default
    CHECK(w.host.pendingMenus() == 0);
    return true;
}

static bool scn_menu_origin_already_gone() {
    MenuWorld m; World& w = m.w;
    w.fan(w.host.playerLeft(2, 0));
    uint32_t id = 0;
    auto outs = w.host.raiseMenu(2, MenuKind::MessageBox, "x", {"A"}, 0, FormRef(), 0, &id);
    CHECK(outs.empty() && m.resumed.size() == 1 && std::get<2>(m.resumed[0]));
    return true;
}

static bool scn_menu_host_is_origin() {
    MenuWorld m; World& w = m.w;
    uint32_t id = m.raise(0);                                              // the host player triggered it: normal local menu
    CHECK(w.count(0, Event::MenuShow) == 1 && w.count(1, Event::MenuNoticeShow) == 1);
    w.emit(0, w.cl[0].menuReply(id, 1));
    w.pump();
    CHECK(m.resumed.size() == 1 && std::get<1>(m.resumed[0]) == 1);
    return true;
}

static bool scn_menu_codec() {
    MenuRequest rq; rq.menu = 4; rq.kind = MenuKind::CompanionWheel; rq.origin = 2; rq.text = "wheel"; rq.buttons = {"a", "b", "c"};
    rq.defaultChoice = -1; rq.subject = F(0x104C0F);
    std::vector<Msg> ms = {rq, MenuReply{4, -1}, MenuNotice{4, MenuKind::Barter, 2, "Barter"}, MenuClosed{4, 1, true, "b"}};
    for (auto& m : ms) {
        auto b = encode(m);
        Msg d;
        CHECK(decode(b, d) == DecodeStatus::Ok && d.index() == m.index() && encode(d) == b);
        CHECK(decode(b.data(), b.size() - 1, d) != DecodeStatus::Ok);
    }
    MenuRequest big = rq; big.buttons.assign(kMaxButtons + 1, "x");       // oversize button list refused
    Msg d;
    CHECK(decode(encode(big), d) != DecodeStatus::Ok);
    return true;
}
