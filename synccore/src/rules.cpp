#include "fnvcoop/rules.h"
#include <algorithm>
#include <cctype>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <limits>
#include <sstream>

namespace fnvcoop {

namespace {

// ---- minimal strict JSON (objects, arrays, strings, numbers, true/false/null) --------------------------------------
struct J {
    enum T { Null, Bool, Num, Str, Arr, Obj } t = Null;
    bool b = false;
    double n = 0;
    std::string s;
    std::vector<J> a;
    std::vector<std::pair<std::string, J>> o;
    const J* get(const std::string& k) const {
        for (auto& kv : o) if (kv.first == k) return &kv.second;
        return nullptr;
    }
};

class Parser {
public:
    Parser(const std::string& s) : s_(s) {}
    bool parse(J& out, std::string* err) {
        ws();
        if (!value(out, 0)) { fail(err); return false; }
        ws();
        if (p_ != s_.size()) { why_ = "trailing data"; fail(err); return false; }
        return true;
    }
private:
    const std::string& s_;
    size_t p_ = 0;
    std::string why_;
    void fail(std::string* err) { if (err) *err = "json: " + (why_.empty() ? std::string("syntax error") : why_) + " at byte " + std::to_string(p_); }
    void ws() { while (p_ < s_.size() && std::isspace(static_cast<unsigned char>(s_[p_]))) ++p_; }
    bool lit(const char* w) {
        size_t n = std::char_traits<char>::length(w);
        if (s_.compare(p_, n, w) != 0) return false;
        p_ += n;
        return true;
    }
    bool str(std::string& out) {
        if (p_ >= s_.size() || s_[p_] != '"') return false;
        ++p_;
        out.clear();
        while (p_ < s_.size()) {
            char c = s_[p_++];
            if (c == '"') return true;
            if (static_cast<unsigned char>(c) < 0x20) { why_ = "control char in string"; return false; }
            if (c != '\\') { out.push_back(c); continue; }
            if (p_ >= s_.size()) return false;
            char e = s_[p_++];
            switch (e) {
                case '"': out.push_back('"'); break;
                case '\\': out.push_back('\\'); break;
                case '/': out.push_back('/'); break;
                case 'b': out.push_back('\b'); break;
                case 'f': out.push_back('\f'); break;
                case 'n': out.push_back('\n'); break;
                case 'r': out.push_back('\r'); break;
                case 't': out.push_back('\t'); break;
                case 'u': {
                    if (p_ + 4 > s_.size()) return false;
                    unsigned cp = 0;
                    for (int i = 0; i < 4; ++i) {
                        char h = s_[p_++];
                        cp <<= 4;
                        if (h >= '0' && h <= '9') cp |= unsigned(h - '0');
                        else if (h >= 'a' && h <= 'f') cp |= unsigned(h - 'a' + 10);
                        else if (h >= 'A' && h <= 'F') cp |= unsigned(h - 'A' + 10);
                        else return false;
                    }
                    if (cp < 0x80) out.push_back(char(cp));
                    else if (cp < 0x800) { out.push_back(char(0xC0 | (cp >> 6))); out.push_back(char(0x80 | (cp & 0x3F))); }
                    else { out.push_back(char(0xE0 | (cp >> 12))); out.push_back(char(0x80 | ((cp >> 6) & 0x3F))); out.push_back(char(0x80 | (cp & 0x3F))); }
                    break;
                }
                default: why_ = "bad escape"; return false;
            }
        }
        return false;
    }
    bool value(J& v, int depth) {
        if (depth > 32) { why_ = "too deep"; return false; }
        ws();
        if (p_ >= s_.size()) return false;
        char c = s_[p_];
        if (c == '{') {
            ++p_; v.t = J::Obj; ws();
            if (p_ < s_.size() && s_[p_] == '}') { ++p_; return true; }
            for (;;) {
                ws();
                std::string k;
                if (!str(k)) return false;
                ws();
                if (p_ >= s_.size() || s_[p_++] != ':') return false;
                J child;
                if (!value(child, depth + 1)) return false;
                v.o.emplace_back(std::move(k), std::move(child));
                ws();
                if (p_ >= s_.size()) return false;
                char d = s_[p_++];
                if (d == '}') return true;
                if (d != ',') return false;
            }
        }
        if (c == '[') {
            ++p_; v.t = J::Arr; ws();
            if (p_ < s_.size() && s_[p_] == ']') { ++p_; return true; }
            for (;;) {
                J child;
                if (!value(child, depth + 1)) return false;
                v.a.push_back(std::move(child));
                ws();
                if (p_ >= s_.size()) return false;
                char d = s_[p_++];
                if (d == ']') return true;
                if (d != ',') return false;
            }
        }
        if (c == '"') { v.t = J::Str; return str(v.s); }
        if (lit("true")) { v.t = J::Bool; v.b = true; return true; }
        if (lit("false")) { v.t = J::Bool; v.b = false; return true; }
        if (lit("null")) { v.t = J::Null; return true; }
        // number
        size_t st = p_;
        if (p_ < s_.size() && (s_[p_] == '-' || s_[p_] == '+')) ++p_;
        while (p_ < s_.size() && (std::isdigit(static_cast<unsigned char>(s_[p_])) || s_[p_] == '.' || s_[p_] == 'e' || s_[p_] == 'E' || s_[p_] == '-' || s_[p_] == '+')) ++p_;
        if (p_ == st) return false;
        char* end = nullptr;
        std::string num = s_.substr(st, p_ - st);
        v.n = std::strtod(num.c_str(), &end);
        if (!end || *end != 0 || !std::isfinite(v.n)) { why_ = "bad number"; return false; }
        v.t = J::Num;
        return true;
    }
};

std::string scalarText(const J& j) {
    switch (j.t) {
        case J::Str: return j.s;
        case J::Bool: return j.b ? "true" : "false";
        case J::Num: { std::ostringstream o; o << j.n; return o.str(); }
        default: return "";
    }
}

std::string lower(std::string s) {
    for (auto& c : s) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    return s;
}

bool truthy(double v) { return v != 0.0; }

}  // namespace

// ================================ RulesDb ======================================================================

bool RulesDb::load(const std::string& text, std::string* err) {
    J root;
    Parser p(text);
    if (!p.parse(root, err)) return false;
    auto bad = [&](const std::string& why) { if (err) *err = "rules: " + why; return false; };
    if (root.t != J::Obj) return bad("root must be an object");
    const J* schema = root.get("schema");
    if (!schema || schema->t != J::Num || schema->n != 1) return bad("unsupported schema");
    const J* quests = root.get("quests");
    if (!quests || quests->t != J::Obj) return bad("missing quests");
    std::map<std::string, QuestRules> out;
    for (auto& [edid, q] : quests->o) {
        if (q.t != J::Obj) return bad("quest " + edid + " is not an object");
        QuestRules qr;
        qr.edid = edid;
        if (auto* t = q.get("tier")) qr.tier = scalarText(*t);
        if (auto* t = q.get("questline")) qr.questline = scalarText(*t);
        if (auto* t = q.get("curated")) qr.curated = t->t == J::Bool && t->b;
        const J* rules = q.get("rules");
        if (!rules || rules->t != J::Arr) return bad("quest " + edid + " has no rules array");
        for (auto& r : rules->a) {
            if (r.t != J::Obj) return bad("rule in " + edid + " is not an object");
            const J* kind = r.get("kind");
            if (!kind || kind->t != J::Str || kind->s.empty()) return bad("rule in " + edid + " has no kind");
            Rule rule;
            rule.kind = kind->s;
            if (auto* o = r.get("origin")) rule.origin = scalarText(*o);
            if (auto* params = r.get("params")) {
                if (params->t != J::Obj) return bad("params of " + edid + "/" + rule.kind + " must be an object");
                for (auto& [k, v] : params->o) {
                    if (v.t == J::Arr) {
                        for (auto& e : v.a) {
                            if (e.t == J::Arr || e.t == J::Obj) return bad("nested arrays in params of " + edid);
                            rule.lists[k].push_back(scalarText(e));
                        }
                        if (v.a.empty()) rule.lists[k];
                    } else if (v.t == J::Obj) {
                        if (k != "funcs") return bad("unexpected object param " + k + " in " + edid);
                        for (auto& [fk, fv] : v.o) rule.funcs[lower(fk)] = scalarText(fv);
                    } else {
                        rule.scalars[k] = scalarText(v);
                    }
                }
            }
            qr.rules.push_back(std::move(rule));
        }
        out.emplace(edid, std::move(qr));
    }
    quests_ = std::move(out);
    textHash_ = hash64(text.data(), text.size());
    return true;
}

bool RulesDb::loadFile(const std::string& path, std::string* err) {
    std::ifstream f(path, std::ios::binary);
    if (!f) { if (err) *err = "cannot open " + path; return false; }
    std::stringstream ss;
    ss << f.rdbuf();
    return load(ss.str(), err);
}

const QuestRules* RulesDb::find(const std::string& edid) const {
    auto it = quests_.find(edid);
    return it == quests_.end() ? nullptr : &it->second;
}

std::map<std::string, size_t> RulesDb::kindCounts() const {
    std::map<std::string, size_t> c;
    for (auto& [e, q] : quests_) for (auto& r : q.rules) ++c[r.kind];
    return c;
}

// ================================ RulesEngine ==================================================================

RulesEngine::RulesEngine(HostSession& host, const RulesDb& db, Resolver resolve) : RulesEngine(host, db, std::move(resolve), Options()) {}

RulesEngine::RulesEngine(HostSession& host, const RulesDb& db, Resolver resolve, Options opt)
    : host_(host), db_(db), opt_(opt), resolve_(std::move(resolve)) {
    static const std::set<std::string> handled = {"Barrier", "PartyTether", "SetPlayerControls", "SaveSuppress", "RewardAll", "QuestItemDup",
                                                  "RemoveItemPerOwner", "ShareKarmaRep", "CompanionOwner", "SlideshowEnd", "PartyAwareCond",
                                                  "ExcludeFromGate", "PerPlayerScene", "FastTravelLock", "InitiatorDialogue", "PartyWarp", "GuestLocalRun"};
    for (auto& [edid, q] : db_.quests()) {
        FormRef f;
        if (resolve_ && resolve_(edid, f)) byForm_[f] = &q;
        for (auto& r : q.rules) if (!handled.count(r.kind)) unsupported_.insert(r.kind);
    }
    host_.txnFilter = [this](EffectTxn& t) { filter(t); };
}

RulesEngine::~RulesEngine() { host_.txnFilter = nullptr; }

bool RulesEngine::guestLocalRun(const FormRef& quest) const {
    const QuestRules* q = rulesFor(quest);
    return q && q->find("GuestLocalRun");
}
std::vector<FormRef> RulesEngine::guestLocalQuests() const {
    std::vector<FormRef> v;
    for (auto& [f, q] : byForm_) if (q->find("GuestLocalRun")) v.push_back(f);
    return v;
}
std::vector<std::string> RulesEngine::guestLocalScripts() const {
    std::vector<std::string> v;
    for (auto& [edid, q] : db_.quests())
        if (const Rule* r = q.find("GuestLocalRun")) {
            auto it = r->lists.find("scripts");
            if (it != r->lists.end()) for (auto& s : it->second) v.push_back(s);
        }
    return v;
}

const QuestRules* RulesEngine::rulesFor(const FormRef& quest) const {
    auto it = byForm_.find(quest);
    return it == byForm_.end() ? nullptr : it->second;
}

bool RulesEngine::excludedFromGate(const FormRef& quest) const {
    auto* q = rulesFor(quest);
    return q && q->find("ExcludeFromGate");
}

FormRef RulesEngine::barrierId(const FormRef& quest, uint32_t stage) {
    char buf[16];
    std::snprintf(buf, sizeof buf, "%08X", quest.localId);
    return FormRef(quest.plugin + "#b" + buf, stage);
}

uint8_t RulesEngine::joinedMask() const {
    uint8_t m = 1u << kHostPlayer;
    for (auto& c : host_.clients()) if ((c.joined || mutations().departedHoldBarrier) && c.slot < 8) m = static_cast<uint8_t>(m | (1u << c.slot));
    return m;
}

bool RulesEngine::tetherSatisfied(const std::set<FormRef>& cells) const {
    if (cells.empty()) return true;
    uint8_t m = joinedMask();
    for (unsigned s = 0; s < kMaxSlots; ++s) {
        if (!(m >> s & 1u)) continue;
        auto it = cellOf_.find(PlayerId(s));
        if (it == cellOf_.end() || !cells.count(it->second)) return false;
    }
    return true;
}

StageDecision RulesEngine::onStageRequested(const FormRef& quest, uint32_t stage, PlayerId initiator) {
    if (mutations().rulesDisabled) return StageDecision::Proceed;
    const QuestRules* q = rulesFor(quest);
    if (!q) return StageDecision::Proceed;
    for (auto& p : pending_) if (p.quest == quest && p.stage == stage) return StageDecision::Deferred;   // already waiting
    if (q->find("Barrier")) {
        Pending p;
        p.quest = quest; p.stage = stage; p.initiator = initiator; p.barrier = barrierId(quest, stage); p.since = now_;
        auto early = earlyPasses_.find(quest);
        pending_.push_back(std::move(p));
        ++stats_.stagesDeferred;
        syncTravelLock(quest);
        if (early != earlyPasses_.end()) {   // players that finished before the host asked
            std::set<PlayerId> slots = std::move(early->second);
            earlyPasses_.erase(early);
            for (PlayerId s : slots) onPlayerPassed(s, quest);
        }
        return StageDecision::Deferred;
    }
    if (const Rule* tr = q->find("PartyTether")) {
        std::set<FormRef> cells;
        auto it = tr->lists.find("cells");
        if (it != tr->lists.end())
            for (auto& edid : it->second) { FormRef f; if (resolve_ && resolve_(edid, f)) cells.insert(f); }
        if (tetherSatisfied(cells)) return StageDecision::Proceed;
        Pending p;
        p.quest = quest; p.stage = stage; p.initiator = initiator; p.tether = true; p.since = now_; p.cells = cells;
        Cause c; c.kind = CauseKind::Script; c.source = quest; c.aux = static_cast<int32_t>(stage);
        host_.commit(kHostPlayer, c, {PartyTether{*cells.begin(), true}});   // clients gather / hold
        ++stats_.tetherGathers;
        pending_.push_back(std::move(p));
        ++stats_.stagesDeferred;
        syncTravelLock(quest);
        return StageDecision::Deferred;
    }
    return StageDecision::Proceed;
}

void RulesEngine::onPlayerPassed(PlayerId slot, const FormRef& quest) {
    if (slot >= kMaxSlots) return;
    for (size_t i = 0; i < pending_.size(); ++i) {
        Pending& p = pending_[i];
        if (p.quest != quest || p.tether) continue;
        if (!p.passed.insert(slot).second) { ++stats_.ignoredPasses; return; }   // duplicate report
        ++stats_.arrivals;
        Cause c; c.kind = CauseKind::Script; c.source = quest; c.aux = static_cast<int32_t>(p.stage);
        host_.commit(slot, c, {BarrierArrive{p.barrier, joinedMask()}});
        const BarrierState* b = nullptr;
        auto it = host_.state().barriers.find(p.barrier);
        if (it != host_.state().barriers.end()) b = &it->second;
        bool done = b && b->released;
        if (mutations().barrierReleaseEarly) done = true;   // TEST ONLY: first arrival releases
        if (done) release(i, false);
        return;
    }
    earlyPasses_[quest].insert(slot);   // nobody asked for this barrier yet
}

void RulesEngine::onCellReported(PlayerId slot, const FormRef& cell) {
    cellOf_[slot] = cell;
    checkPending();
}

bool RulesEngine::handleAction(PlayerId slot, IntentKind kind, const FormRef& ref) {
    if (kind == IntentKind::ScenePass) { onPlayerPassed(slot, ref); return true; }
    if (kind == IntentKind::ReportCell) { onCellReported(slot, ref); return true; }
    return false;
}

void RulesEngine::release(size_t index, bool timedOut) {
    Pending p = std::move(pending_[index]);
    pending_.erase(pending_.begin() + static_cast<std::ptrdiff_t>(index));
    if (p.tether) {
        Cause c; c.kind = CauseKind::Script; c.source = p.quest; c.aux = static_cast<int32_t>(p.stage);
        if (!p.cells.empty()) host_.commit(kHostPlayer, c, {PartyTether{*p.cells.begin(), false}});
    } else if (timedOut || !host_.state().barriers.count(p.barrier) || !host_.state().barriers.at(p.barrier).released) {
        Cause c; c.kind = CauseKind::Script; c.source = p.quest; c.aux = static_cast<int32_t>(p.stage);
        host_.commit(kHostPlayer, c, {BarrierRelease{p.barrier}});   // explicit release (timeout, or players left)
    }
    ++stats_.stagesReleased;
    syncTravelLock(p.quest);
    host_.requestDigest();   // verify every client's world right after a barrier / scene
    if (applyStage) applyStage(p.quest, p.stage);
}

bool RulesEngine::desiredTravelLock(const FormRef& quest) const {
    if (locks_.count(quest)) return true;
    for (auto& p : pending_) if (p.quest == quest) return true;
    return false;
}

void RulesEngine::syncTravelLock(const FormRef& quest) {
    const QuestRules* q = rulesFor(quest);
    if (!q || !q->find("FastTravelLock")) return;
    bool want = desiredTravelLock(quest), have = travelLocked_.count(quest) > 0;
    if (want == have) return;
    if (want) travelLocked_.insert(quest); else travelLocked_.erase(quest);
    ++stats_.travelLockChanges;
    Cause c; c.kind = CauseKind::Script; c.source = quest;
    host_.commit(kHostPlayer, c, {SetFastTravelLock{quest, want}});
}

void RulesEngine::checkPending() {
    for (size_t i = 0; i < pending_.size();) {
        Pending& p = pending_[i];
        bool go = false;
        if (p.tether) {
            go = tetherSatisfied(p.cells);
        } else {
            uint8_t joined = joinedMask(), arrived = 0;
            for (PlayerId s : p.passed) arrived = static_cast<uint8_t>(arrived | (1u << s));
            go = (arrived & joined) == joined;   // everyone still present has passed (leavers do not hold the party)
        }
        if (go) release(i, false); else ++i;
    }
}

void RulesEngine::tick(uint64_t nowMs) {
    now_ = nowMs;
    checkPending();
    for (size_t i = 0; i < pending_.size();) {
        if (now_ >= pending_[i].since + opt_.barrierTimeoutMs) { ++stats_.barrierTimeouts; release(i, true); } else ++i;
    }
    for (auto it = locks_.begin(); it != locks_.end();) {
        if (now_ >= it->second.since + opt_.controlTimeoutMs) {
            ++stats_.controlTimeouts;
            FormRef quest = it->first;
            uint32_t mask = it->second.mask;
            it = locks_.erase(it);
            Cause c; c.kind = CauseKind::Script; c.source = quest;
            std::vector<Effect> eff{SetPlayerControls{mask, true, Recipient::all()}};
            if (auto* q = rulesFor(quest); q && q->find("SaveSuppress")) eff.push_back(SaveSuppress{false});
            host_.commit(kHostPlayer, c, std::move(eff));
            syncTravelLock(quest);
        } else {
            ++it;
        }
    }
}

double RulesEngine::reduceCondition(const FormRef& quest, const std::string& func, const std::vector<PartyValue>& values,
                                    PlayerId initiator, CondPhase phase) {
    auto initiatorValue = [&]() {
        for (auto& v : values) if (v.slot == initiator) return v.value;
        return values.empty() ? 0.0 : values.front().value;
    };
    if (values.empty()) return 0.0;
    std::string red = "initiator";
    if (!mutations().rulesDisabled) {
        if (const QuestRules* q = rulesFor(quest))
            if (const Rule* r = q->find("PartyAwareCond")) {
                auto it = r->funcs.find(lower(func));
                if (it != r->funcs.end()) red = it->second;
            }
    }
    if (red == "initiator") return initiatorValue();
    if (red == "any-party-member" || red == "any") {
        for (auto& v : values) if (truthy(v.value)) return 1.0;
        return 0.0;
    }
    if (red == "min-over-party") {
        double m = std::numeric_limits<double>::infinity();
        for (auto& v : values) m = std::min(m, v.value);
        return m;
    }
    if (red == "entry:all-in,exit:any-in") {
        if (phase == CondPhase::Entry) {
            for (auto& v : values) if (!truthy(v.value)) return 0.0;
            return 1.0;
        }
        for (auto& v : values) if (truthy(v.value)) return 1.0;
        return 0.0;
    }
    ++stats_.unknownReductions;
    return initiatorValue();
}

void RulesEngine::filter(EffectTxn& t) {
    if (mutations().rulesDisabled) return;
    const QuestRules* q = rulesFor(t.cause.source);
    if (!q) return;
    if (q->find("GuestLocalRun") && !mutations().guestLocalReplicated) {
        // Per-machine scripts (achievements, rumble, DLC setup, fades) run locally on every machine: nothing of theirs is replicated.
        t.effects.clear();
        ++stats_.guestLocalDropped;
        return;
    }
    if (q->find("InitiatorDialogue") && t.cause.kind == CauseKind::DialogueResult && dialogueInitiator_ != kNoPlayer &&
        t.origin != dialogueInitiator_) {
        t.effects.clear();   // not your conversation: only the initiator's choices advance it
        ++stats_.dialogueRejected;
        return;
    }
    if (q->find("PartyWarp") && opt_.playerRef) {
        std::vector<Effect> out;
        for (auto& e : t.effects) {
            auto* mv = std::get_if<MoveRef>(&e);
            bool isPlayer = false;
            if (mv) for (unsigned s = 0; s < kMaxSlots && !isPlayer; ++s) { FormRef pr; isPlayer = opt_.playerRef(PlayerId(s), pr) && pr == mv->ref; }
            if (!isPlayer) { out.push_back(e); continue; }
            ++stats_.warpsExpanded;
            uint8_t joined = joinedMask();
            unsigned k = 0;
            for (unsigned s = 0; s < kMaxSlots; ++s) {   // absent players are held where they are
                if (!(joined >> s & 1u)) continue;
                FormRef pr;
                if (!opt_.playerRef(PlayerId(s), pr)) continue;
                MoveRef m = *mv;
                m.ref = pr;
                m.x += opt_.warpSpacing * static_cast<float>(k++);
                m.partyWarp = true;
                out.push_back(m);
            }
        }
        t.effects = std::move(out);
    }
    const bool rewardAll = q->find("RewardAll") != nullptr;
    const bool itemDup = q->find("QuestItemDup") != nullptr;
    const bool perOwner = q->find("RemoveItemPerOwner") != nullptr;
    const bool shareKR = q->find("ShareKarmaRep") != nullptr;
    const bool ctrlAll = q->find("SetPlayerControls") != nullptr;
    const bool saveRule = q->find("SaveSuppress") != nullptr;
    bool lockedNow = false, unlockedNow = false;
    for (auto& e : t.effects) {
        std::visit([&](auto& x) {
            using T = std::decay_t<decltype(x)>;
            auto setTo = [&](Recipient& to, Recipient r) {
                if (to.mode == Recipient::Default) { to = r; ++stats_.recipientRewrites; }
            };
            if constexpr (std::is_same_v<T, AddItem>) {
                if (rewardAll || itemDup) setTo(x.to, Recipient::all());
            } else if constexpr (std::is_same_v<T, ModActorValue>) {
                if (rewardAll) setTo(x.to, Recipient::all());
            } else if constexpr (std::is_same_v<T, RemoveItem>) {
                if (perOwner) setTo(x.to, Recipient::origin());
                else if (itemDup) setTo(x.to, Recipient::all());
            } else if constexpr (std::is_same_v<T, ModKarma> || std::is_same_v<T, ModReputation>) {
                if (shareKR) setTo(x.to, Recipient::all());
            } else if constexpr (std::is_same_v<T, SetPlayerControls>) {
                if (ctrlAll) {
                    setTo(x.to, Recipient::all());
                    Lock& l = locks_[t.cause.source];
                    if (!x.enable) {
                        if (l.mask == 0) { l.since = now_; ++stats_.locksTracked; }
                        l.mask |= x.controls;
                        lockedNow = true;
                    } else {
                        l.mask &= ~x.controls;
                        if (l.mask == 0) { locks_.erase(t.cause.source); unlockedNow = true; }
                    }
                }
            } else if constexpr (std::is_same_v<T, CompanionOwner>) {
                if (q->find("CompanionOwner")) x.owner = t.origin;   // owned by the recruiter
            }
        }, e);
    }
    if (q->find("FastTravelLock")) {
        bool want = desiredTravelLock(t.cause.source), have = travelLocked_.count(t.cause.source) > 0;
        if (want != have) {
            if (want) travelLocked_.insert(t.cause.source); else travelLocked_.erase(t.cause.source);
            ++stats_.travelLockChanges;
            t.effects.push_back(SetFastTravelLock{t.cause.source, want});
        }
    }
    // a scene that locks controls also blocks saving until it releases (or times out)
    if (saveRule && lockedNow && !unlockedNow) t.effects.push_back(SaveSuppress{true});
    if (saveRule && unlockedNow && !lockedNow) t.effects.push_back(SaveSuppress{false});
    // SlideshowEnd: the host runs it once
    if (q->find("SlideshowEnd") && host_.state().slideshowEnded)
        t.effects.erase(std::remove_if(t.effects.begin(), t.effects.end(), [](const Effect& e) { return std::holds_alternative<SlideshowEnd>(e); }), t.effects.end());
}

}  // namespace fnvcoop
