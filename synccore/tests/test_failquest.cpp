// FailQuest (wire tag 32): sticky, implies completed, diffs and baselines consistently. L5 request.
#include "fnvcoop/baseline.h"
#include "fnvcoop/mutations.h"
#include "fnvcoop/shadow.h"
#include "fnvcoop/wire.h"
#include "minitest.h"

using namespace fnvcoop;

namespace {
FormRef Q(uint32_t i) { return FormRef("falloutnv.esm", i); }

bool failQuestHolds() {
    ShadowState s;
    EffectTxn t; t.seq = 1; t.effects = {FailQuest{Q(7)}, CompleteQuest{Q(8)}};
    std::vector<Effect> emitted; s.applyTxn(t, kNoPlayer, &emitted);
    if (!s.questFailed.count(Q(7)) || !s.questCompleted.count(Q(7))) return false;      // failed implies completed
    if (s.questFailed.count(Q(8)) || !s.questCompleted.count(Q(8))) return false;
    EffectTxn t2; t2.seq = 2; t2.effects = {FailQuest{Q(7)}, CompleteQuest{Q(7)}};      // repeats are no-ops
    { std::vector<Effect> o2; s.applyTxn(t2, kNoPlayer, &o2); if (!o2.empty()) return false; }
    // a quest completed first and failed later becomes failed too
    EffectTxn t3; t3.seq = 3; t3.effects = {FailQuest{Q(8)}};
    { std::vector<Effect> o3; s.applyTxn(t3, kNoPlayer, &o3); if (o3.size() != 1 || !s.questFailed.count(Q(8))) return false; }
    // diff from empty reproduces the state exactly
    ShadowState replay;
    std::vector<Effect> d; ShadowState::diffEffects(ShadowState(), s, kNoPlayer, d);
    for (auto& e : d) { EffectTxn x; x.seq = replay.seq + 1; x.effects = {e}; replay.applyTxn(x); }
    replay.seq = s.seq;
    if (!(replay == s)) return false;
    // wire round trip of the state and of the effect
    Archive w = Archive::writer(); io(w, s);
    std::vector<uint8_t> b = w.finish();
    Archive r = Archive::reader(b.data(), b.size());
    ShadowState back; io(r, back);
    if (!r.ok() || !(back == s)) return false;
    // baseline entries: failed is its own entry and the completed entry is not duplicated
    int failed = 0, completed = 0;
    for (auto& e : baselineEntries(s)) { failed += std::holds_alternative<FailQuest>(e.effect); completed += std::holds_alternative<CompleteQuest>(e.effect); }
    if (failed != 2 || completed != 0) return false;
    return !emitted.empty();
}
}  // namespace

TEST(core, fail_quest_is_sticky_implies_completed_and_survives_diff_wire_baseline) { CHECK(failQuestHolds()); }

TEST(twin, fail_not_completed) {
    MutationScope m([] { Mutations x; x.failNotCompleted = true; return x; }());
    CHECK(!failQuestHolds());
}
