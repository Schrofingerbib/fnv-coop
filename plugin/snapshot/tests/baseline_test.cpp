// Game-free test of BuildShadowFromBaseline against the real synccore (hash parity, summarizeWorld).
#include "fnvcoop/baseline.h"
#include "fnvcoop_snapshot/baseline_dump.h"
#include <cstdio>
#include <cstring>

using namespace fnvcoop;
using namespace fnvcoop::snapshot;
static int fails = 0;
#define CHECK(c) do { if (!(c)) { std::printf("FAIL line %d: %s\n", __LINE__, #c); ++fails; } } while (0)

static BaselineInput world() {
	BaselineInput in;
	in.plugins = {"FalloutNV.esm", "HonestHearts.esm"};
	BaselineQuestIn q0; q0.ref = {"FalloutNV.esm", 0x104C2A}; q0.stage = 20; q0.running = true;
	q0.objectives = {{10, 3}, {20, 1}}; q0.vars = {{0, 1.0}, {3, 2.5}};
	BaselineQuestIn q1; q1.ref = {"FalloutNV.esm", 0x104C2B};   // untouched quest: stage 0, no objectives
	BaselineQuestIn q2; q2.ref = {"FalloutNV.esm", 0x104C2C}; q2.stage = 100; q2.completed = true; q2.objectives = {{10, 3}};
	in.quests = {q0, q1, q2};
	in.globals = {{{"FalloutNV.esm", 0xA0000}, 5.0f, false}, {{"FalloutNV.esm", 0x38}, 9.5f, true}};
	BaselineRefIn doc; doc.ref = {"FalloutNV.esm", 0x104C8F}; doc.isActor = true; doc.alive = false; doc.vars = {{1, 4.0}};
	BaselineRefIn sunny; sunny.ref = {"FalloutNV.esm", 0x104C90}; sunny.isActor = true; sunny.alive = true; sunny.enabled = false;
	BaselineRefIn door; door.ref = {"FalloutNV.esm", 0x104C91}; door.hasLock = true; door.locked = true; door.lockLevel = 50;
	in.refs = {doc, sunny, door};
	in.factionRanks = {{{"FalloutNV.esm", 0xE5B3C}, 1}, {{"FalloutNV.esm", 0xE5B3D}, -1}};
	in.reputations = {{{"FalloutNV.esm", 0xF7F85}, 12.4, 0.0}, {{"FalloutNV.esm", 0xF7F86}, 0.0, 3.6}};
	in.saidOnceInfos = {{"FalloutNV.esm", 0x1234}};
	in.karma = 24.6;
	return in;
}

int main() {
	ShadowState s; uint64_t h = 0; std::string err;
	CHECK(BuildShadowFromBaseline(world(), &s, &h, &err));
	auto F = [](const char* p, uint32_t id) { return FormRef::make(p, id); };

	CHECK(s.stages.size() == 3);                                          // zero-stage quest present
	CHECK(s.stages.at(F("FalloutNV.esm", 0x104C2B)) == 0);
	CHECK(s.stages.at(F("FalloutNV.esm", 0x104C2A)) == 20);
	CHECK((s.objectives.at({F("FalloutNV.esm", 0x104C2A), 10}) == 3));
	CHECK((s.objectives.at({F("FalloutNV.esm", 0x104C2A), 20}) == 1));
	CHECK(s.questRunning.at(F("FalloutNV.esm", 0x104C2A)) && !s.questRunning.at(F("FalloutNV.esm", 0x104C2B)));
	CHECK(s.questCompleted.count(F("FalloutNV.esm", 0x104C2C)) == 1 && s.questCompleted.size() == 1);
	CHECK(s.allObjectivesDone.count(F("FalloutNV.esm", 0x104C2C)) == 1 && s.allObjectivesDone.size() == 1);
	CHECK(s.scriptVars.size() == 3);                                       // 2 quest vars + 1 ref var
	CHECK((s.scriptVars.at({F("FalloutNV.esm", 0x104C2A), 3}) == 2.5));
	CHECK(s.globals.size() == 1 && s.globals.count(F("FalloutNV.esm", 0xA0000)) == 1);   // clock excluded
	CHECK(s.kills.size() == 1 && s.kills.count(F("FalloutNV.esm", 0x104C8F)) == 1);       // only the dead one
	CHECK(s.enabled.size() == 3 && !s.enabled.at(F("FalloutNV.esm", 0x104C90)) && s.enabled.at(F("FalloutNV.esm", 0x104C8F)));
	CHECK(s.locks.at(F("FalloutNV.esm", 0x104C91)) == std::make_pair(true, 50));
	CHECK(s.factionRank.size() == 2);
	CHECK((s.factionRank.at({F("FalloutNV.esm", 0x14), F("FalloutNV.esm", 0xE5B3D)}) == -1));
	CHECK(s.sayOnce.size() == 1);
	CHECK(s.ledger[0].karma == 25);
	CHECK((s.ledger[0].rep.at(F("FalloutNV.esm", 0xF7F85)) == std::make_pair<int64_t, int64_t>(12, 0)));
	CHECK((s.ledger[0].rep.at(F("FalloutNV.esm", 0xF7F86)) == std::make_pair<int64_t, int64_t>(0, 4)));
	CHECK(s.ledger[1].empty());

	// hash parity with synccore
	CHECK(h == contentHashOf({"falloutnv.esm", "honesthearts.esm"}));
	CHECK(BaselineContentHash({"A.ESM"}) == contentHashOf({"a.esm"}));
	CHECK(h != BaselineContentHash({"HonestHearts.esm", "FalloutNV.esm"}));   // order matters

	// determinism + volatile globals opt-in
	ShadowState s2; uint64_t h2 = 0;
	CHECK(BuildShadowFromBaseline(world(), &s2, &h2, &err) && s2 == s && h2 == h);
	BaselineInput w = world(); w.includeVolatileGlobals = true;
	ShadowState s3; BuildShadowFromBaseline(w, &s3, &h2, &err);
	CHECK(s3.globals.size() == 2);

	// per-field exclusions (from the empirical volatile-keys file): the quest itself is NEVER dropped
	{
		BaselineInput v = world();
		v.quests[0].excludeRunning = true;
		v.quests[0].excludeObjectives = true;
		v.quests[1].excludeStage = true;
		ShadowState sv; uint64_t hv = 0;
		CHECK(BuildShadowFromBaseline(v, &sv, &hv, &err));
		CHECK(sv.stages.count(F("FalloutNV.esm", 0x104C2A)) == 1);           // stage kept
		CHECK(sv.questRunning.count(F("FalloutNV.esm", 0x104C2A)) == 0);     // running excluded
		CHECK(sv.objectives.count({F("FalloutNV.esm", 0x104C2A), 10}) == 0);
		CHECK(sv.objectives.count({F("FalloutNV.esm", 0x104C2C), 10}) == 1); // other quests untouched
		CHECK(sv.stages.count(F("FalloutNV.esm", 0x104C2B)) == 0 && sv.questRunning.count(F("FalloutNV.esm", 0x104C2B)) == 1);
		CHECK(sv.stages.size() == 2);
		CHECK(sv.scriptVars.size() == 3);                                    // vars unaffected by field flags
		CHECK(sv.allObjectivesDone.count(F("FalloutNV.esm", 0x104C2C)) == 1);
	}

	// Values are stored EXACTLY as read (bit-identical): rounding, if any, belongs in the hash/compare only
	// (synccore baseline.cpp add()), never in the value that is sent and applied to a joiner.
	{
		BaselineInput a = world();
		const double jitter = 8.99999976;
		a.quests[0].vars[3] = jitter;
		a.refs[0].vars[1] = jitter;
		ShadowState sa; uint64_t ha = 0;
		CHECK(BuildShadowFromBaseline(a, &sa, &ha, &err));
		double got = sa.scriptVars.at({F("FalloutNV.esm", 0x104C2A), 3});
		double gotRef = sa.scriptVars.at({F("FalloutNV.esm", 0x104C8F), 1});
		CHECK(std::memcmp(&got, &jitter, sizeof got) == 0);
		CHECK(std::memcmp(&gotRef, &jitter, sizeof gotRef) == 0);
		CHECK(got != 9.0);
	}

	// summaries: identical worlds equal, one stage apart differ (L4 diff machinery accepts our output)
	auto sum = summarizeWorld(s, h), sum2 = summarizeWorld(s2, h2), sum3 = summarizeWorld(s3, h2);
	CHECK(sum.entries.size() == sum2.entries.size() && sum.entries.size() > 0);
	CHECK(sum3.entries.size() != sum.entries.size());

	// never vacuous
	BaselineInput empty;
	CHECK(!BuildShadowFromBaseline(empty, &s, &h, &err) && !err.empty());
	BaselineInput noq = world(); noq.quests.clear();
	CHECK(!BuildShadowFromBaseline(noq, &s, &h, &err));
	BaselineInput nop = world(); nop.plugins.clear();
	CHECK(!BuildShadowFromBaseline(nop, &s, &h, &err));

	std::printf(fails ? "FAILED (%d)\n" : "OK\n", fails);
	return fails ? 1 : 0;
}
