// Round 5 scenarios (campaign fix wave 1, gap G08): who decides an irreversible fork (owner decision D3).
//   vote   a story fork: the party votes, the origin counts as yes, the majority wins, the HOST breaks a tie
//   actor  a personal decision: the acting player alone, nobody is asked
//   fork INFOs (Joshua or Daniel, Elijah's offer): the pick is held before any script runs
using ConsentMode = HostDialogue::ConsentMode;

static HostDialogue::ModalOptions voteOpts(bool anyButton = false) {
    HostDialogue::ModalOptions o;
    o.partyConsent = true; o.confirmButton = 1; o.declineChoice = 0; o.mode = ConsentMode::Vote; o.anyButton = anyButton;
    return o;
}
static uint32_t raiseWith(MenuWorld& m, PlayerId origin, const HostDialogue::ModalOptions& o) {
    uint32_t id = 0;
    m.w.fan(m.w.host.raiseMenu(origin, MenuKind::MessageBox, "Launch the missiles?", {"No.", "Yes.", "Maybe."}, 0, FormRef(), m.w.net.now, &id, o));
    m.w.pump();
    return id;
}
static void reply(MenuWorld& m, PlayerId who, uint32_t id, int32_t choice) {
    m.w.emit(who, m.w.cl[who].menuReply(id, choice));
    m.w.pump();
}

// 3 players, origin = 2: the host agrees, which is a majority (origin + host = 2 of 3): approved at once, the last player is not needed
static bool scn_vote_majority_approves() {
    MenuWorld m; World& w = m.w;
    uint32_t id = raiseWith(m, 2, voteOpts());
    reply(m, 2, id, 1);
    CHECK(m.resumed.empty() && w.count(0, Event::MenuShow) == 1 && w.count(1, Event::MenuShow) == 1);   // both others are asked
    reply(m, 0, id, 0);                                                    // agree
    CHECK(m.resumed.size() == 1 && std::get<1>(m.resumed[0]) == 1 && !std::get<2>(m.resumed[0]));
    return true;
}
// a vote is not unanimity: one "not yet" does not stop a majority
static bool scn_vote_one_dissent_does_not_veto() {
    MenuWorld m; World& w = m.w;
    uint32_t id = raiseWith(m, 2, voteOpts());
    reply(m, 2, id, 1);
    reply(m, 1, id, 1);                                                    // dissent
    CHECK(m.resumed.empty());                                              // 1 yes (origin), 1 no: undecided
    reply(m, 0, id, 0);                                                    // the host agrees
    CHECK(m.resumed.size() == 1 && std::get<1>(m.resumed[0]) == 1);
    (void)w;
    return true;
}
static bool scn_vote_majority_declines() {
    MenuWorld m;
    uint32_t id = raiseWith(m, 2, voteOpts());
    reply(m, 2, id, 1);
    reply(m, 1, id, 1);
    reply(m, 0, id, 1);                                                    // two of three say not yet
    CHECK(m.resumed.size() == 1 && std::get<1>(m.resumed[0]) == 0);      // the script gets the decline choice (0 = leave it)
    return true;
}
// two players, a tie: the host decides
static bool scn_vote_tie_goes_to_the_host() {
    for (int hostIsOrigin = 0; hostIsOrigin < 2; ++hostIsOrigin) {
        World w(2);
        std::vector<std::tuple<uint32_t, int32_t, bool>> res;
        w.host.setMenuResume([&](uint32_t mnu, int32_t c, bool t) { res.emplace_back(mnu, c, t); });
        const PlayerId origin = hostIsOrigin ? 0 : 1, other = hostIsOrigin ? 1 : 0;
        uint32_t id = 0;
        w.fan(w.host.raiseMenu(origin, MenuKind::MessageBox, "Go?", {"No.", "Yes."}, 0, FormRef(), 0, &id, voteOpts()));
        w.pump();
        w.fan(w.host.onMessage(origin, MenuReply{id, 1}, 0));
        w.fan(w.host.onMessage(other, MenuReply{id, 1}, 0));               // the other player says not yet
        // host = origin: its yes wins the tie; host = the dissenter: its no wins it
        CHECK(res.size() == 1 && std::get<1>(res[0]) == (hostIsOrigin ? 1 : 0));
    }
    return true;
}
// silence is an abstention: whoever answered decides; a tie with the host silent is a no
static bool scn_vote_timeout_counts_the_answers() {
    HostDialogue::Config c; c.messageMenuMs = 1000;
    World w(3, c);
    std::vector<std::tuple<uint32_t, int32_t, bool>> res;
    w.host.setMenuResume([&](uint32_t mnu, int32_t ch, bool t) { res.emplace_back(mnu, ch, t); });
    uint32_t id = 0;
    w.fan(w.host.raiseMenu(2, MenuKind::MessageBox, "Go?", {"No.", "Yes."}, 0, FormRef(), 0, &id, voteOpts()));
    w.pump();
    w.fan(w.host.onMessage(2, MenuReply{id, 1}, 100));
    w.fan(w.host.onMessage(1, MenuReply{id, 1}, 200));                     // player 1: not yet; the host never answers
    w.fan(w.host.tick(900));
    CHECK(res.empty());
    w.fan(w.host.tick(1200));                                              // 1 yes (origin) vs 1 no, host silent: the tie is a no
    CHECK(res.size() == 1 && std::get<1>(res[0]) == 0 && std::get<2>(res[0]));
    return true;
}
// VMS42: either of the two charge buttons is irreversible; "Leave" asks nobody
static bool scn_vote_any_button_is_irreversible() {
    MenuWorld m; World& w = m.w;
    HostDialogue::ModalOptions o = voteOpts(true);
    o.confirmButton = -1;                                                  // consent.h: confirm=* means "no single confirm button"
    uint32_t id = raiseWith(m, 2, o);
    reply(m, 2, id, 2);                                                    // the second charge button
    CHECK(m.resumed.empty() && w.count(0, Event::MenuShow) == 1);
    reply(m, 0, id, 0);
    CHECK(m.resumed.size() == 1 && std::get<1>(m.resumed[0]) == 2);      // the script gets the button the origin actually chose
    uint32_t id2 = raiseWith(m, 2, o);
    const int shown = w.count(0, Event::MenuShow);
    reply(m, 2, id2, 0);                                                   // "Leave": nobody is asked
    CHECK(m.resumed.size() == 2 && std::get<1>(m.resumed[1]) == 0 && w.count(0, Event::MenuShow) == shown);
    return true;
}
// a personal decision (Caesar's surgery) is never put to the party
static bool scn_actor_decides_alone() {
    MenuWorld m; World& w = m.w;
    HostDialogue::ModalOptions o = voteOpts();
    o.mode = ConsentMode::Actor;
    uint32_t id = raiseWith(m, 2, o);
    reply(m, 2, id, 1);
    CHECK(m.resumed.size() == 1 && std::get<1>(m.resumed[0]) == 1);
    CHECK(w.count(0, Event::MenuShow) == 0 && w.count(1, Event::MenuShow) == 0);
    return true;
}

// ---------- fork INFOs held at the pick
struct ForkWorld {
    World w{3};
    FormRef npc = F(0x2AAA), topic = F(0x1000), fork = F(0x2000);
    ForkWorld() {
        std::map<FormRef, HostDialogue::ChoiceRule> rules;
        HostDialogue::ChoiceRule r; r.mode = ConsentMode::Vote;
        rules[fork] = r;
        w.host.setChoiceRules(rules);
        w.emit(2, w.cl[2].requestStart(npc));
        w.pump();
    }
    void pickFork() {
        w.emit(2, w.cl[2].pick(topic, fork, 0, false, {L("I will lead the caravan through.")}, someFacts()));
        w.pump();
    }
};
static bool scn_fork_info_waits_for_the_party() {
    ForkWorld f; World& w = f.w;
    f.pickFork();
    CHECK(w.runs.empty());                                                 // no script ran: the party has not agreed
    CHECK(w.count(0, Event::MenuShow) == 1 && w.count(1, Event::MenuShow) == 1 && w.count(2, Event::MenuShow) == 0);   // the initiator is not asked
    CHECK(w.cl[0].history().empty() && w.cl[1].history().empty());         // nobody saw the line as a decision yet
    // the initiator's own line ends while the party is still voting
    w.emit(2, w.cl[2].lineDone(w.net.now));
    w.pump();
    CHECK(w.runs.empty());
    const auto* rq = std::get_if<MenuRequest>(&w.log[0].back().msg);
    CHECK(rq && rq->consent && rq->buttons.size() == 2);
    w.emit(0, w.cl[0].menuReply(rq->menu, 0));                             // the host agrees: a majority with the initiator
    w.pump();
    CHECK(w.runsOf(Phase::Begin) == 1 && w.runsOf(Phase::End) == 1);      // approved: Begin, and the End that was waiting
    CHECK(w.cl[1].history().size() == 1 && w.cl[1].history()[0].lines[0].text == "I will lead the caravan through.");
    CHECK(w.cl[2].topicsReady());
    return true;
}
static bool scn_fork_info_denied_runs_nothing() {
    ForkWorld f; World& w = f.w;
    f.pickFork();
    const auto* rq0 = std::get_if<MenuRequest>(&w.log[0].back().msg);
    const auto* rq1 = std::get_if<MenuRequest>(&w.log[1].back().msg);
    CHECK(rq0 && rq1);
    w.emit(0, w.cl[0].menuReply(rq0->menu, 1));
    w.emit(1, w.cl[1].menuReply(rq1->menu, 1));
    w.pump();
    CHECK(w.runs.empty());                                                 // neither the Begin nor the End script ever ran
    CHECK(w.cl[2].role() == ClientDialogue::Role::Idle && !w.host.npcBusy(f.npc));
    bool closedDenied = false;
    for (auto& e : w.log[2]) if (e.kind == Event::CloseMenu && e.reason == CloseReason::ConsentDenied) closedDenied = true;
    CHECK(closedDenied);                                                   // the initiator is told why the conversation ended
    CHECK(w.host.pendingMenus() == 0);
    return true;
}
static bool scn_fork_info_other_infos_are_not_held() {
    ForkWorld f; World& w = f.w;
    w.emit(2, w.cl[2].pick(f.topic, F(0x2001), 0, false, {L("Hello.")}, {}));
    w.pump();
    CHECK(w.runsOf(Phase::Begin) == 1 && w.count(0, Event::MenuShow) == 0);
    return true;
}
static bool scn_fork_info_leaver_does_not_block() {
    ForkWorld f; World& w = f.w;
    f.pickFork();
    const auto* rq0 = std::get_if<MenuRequest>(&w.log[0].back().msg);
    CHECK(rq0);
    w.fan(w.host.playerLeft(1, 5));                                        // player 1 drops: 2 players left, the origin + the host agreeing wins
    w.emit(0, w.cl[0].menuReply(rq0->menu, 0));
    w.pump();
    CHECK(w.runsOf(Phase::Begin) == 1);
    return true;
}
static bool scn_consent_table_modes() {
    std::istringstream in(
        "# c\n"
        "FalloutNV.esm:157247 FortCaesarComaMsg confirm=* decline=0 mode=actor buttons=Leave Caesar alone.|Perform the operation.\n"
        "FalloutNV.esm:125C2D FortHowitzerMsg confirm=1 decline=0 mode=vote timeout=90 buttons=Leave.|Replace.\n"
        "HonestHearts.esm:010718 INFO_010718 confirm=0 decline=-1 mode=vote kind=info buttons=\n"
        "FalloutNV.esm:171DC2 VEndGameWarningMsgLegion confirm=1 decline=0 buttons=I'm not ready yet.|Let's do this!\n");
    auto t = loadConsentMessages(in);
    CHECK(t.size() == 4);
    const auto& caesar = t.at(FormRef("falloutnv.esm", 0x157247));
    CHECK(caesar.anyButton && caesar.mode == 2 && !caesar.isInfo && caesar.declineChoice == 0);
    const auto& how = t.at(FormRef("falloutnv.esm", 0x125C2D));
    CHECK(!how.anyButton && how.confirmButton == 1 && how.mode == 1 && how.timeoutMs == 90000);
    const auto& info = t.at(FormRef("honesthearts.esm", 0x010718));
    CHECK(info.isInfo && info.mode == 1 && info.declineChoice == -1);
    const auto& legion = t.at(FormRef("falloutnv.esm", 0x171DC2));
    CHECK(legion.mode == 0 && legion.timeoutMs == 0 && !legion.isInfo);   // the old rows keep meaning "unanimous"
    return true;
}
static bool scn_consent_denied_codec() {
    Closed c; c.conv = 7; c.reason = CloseReason::ConsentDenied;
    Msg out;
    auto bytes = encode(Msg(c));
    CHECK(decode(bytes, out) == DecodeStatus::Ok);
    CHECK(std::get<Closed>(out).reason == CloseReason::ConsentDenied);
    EndReq e; e.conv = 1; e.reason = CloseReason::ConsentDenied;
    CHECK(decode(encode(Msg(e)), out) == DecodeStatus::Ok);
    return true;
}
