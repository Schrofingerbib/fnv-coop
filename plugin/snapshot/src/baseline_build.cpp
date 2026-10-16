#include "fnvcoop_snapshot/baseline_dump.h"

#include <cmath>
#include <set>

namespace fnvcoop::snapshot {
namespace {

std::string lower(std::string s) {
	for (auto& c : s) if (c >= 'A' && c <= 'Z') c = static_cast<char>(c - 'A' + 'a');
	return s;
}
// Same normalisation as FormRef::make (lower-cased plugin), without needing the synccore library.
fnvcoop::FormRef fr(const BaselineRef& r) { return fnvcoop::FormRef(lower(r.plugin), r.local); }
int64_t rnd(double v) { return static_cast<int64_t>(std::llround(v)); }

}  // namespace

uint64_t BaselineContentHash(const std::vector<std::string>& plugins) {
	// Must stay byte-identical to fnvcoop::contentHashOf (synccore/src/baseline.cpp), including its constants.
	uint64_t h = 1469598103934665603ull;
	for (auto& n : plugins) {
		for (char c : lower(n)) { h ^= static_cast<uint8_t>(c); h *= 1099511628211ull; }
		h ^= 0xFF; h *= 1099511628211ull;
	}
	return h;
}

bool BuildShadowFromBaseline(const BaselineInput& in, fnvcoop::ShadowState* out, uint64_t* hash, std::string* err) {
	auto fail = [&](const char* m) { if (err) *err = m; return false; };
	if (!out || !hash) return fail("null output");
	if (in.plugins.empty()) return fail("no plugins reported");
	if (in.quests.empty()) return fail("no quests read: world not loaded?");
	*out = fnvcoop::ShadowState();

	for (auto& q : in.quests) {
		auto qr = fr(q.ref);
#ifdef BASELINE_MUTANT   // mutation twin: drops default-valued quests (completeness rule violated)
		if (q.stage == 0) continue;
#endif
		if (!q.excludeStage) out->stages[qr] = q.stage;   // every quest, zeros included (minus empirically volatile fields)
		if (!q.excludeRunning) out->questRunning[qr] = q.running;
		if (q.completed) out->questCompleted.insert(qr);
		bool all = !q.objectives.empty() && !q.excludeObjectives;   // done-ness of excluded objectives is unknown
		for (auto& o : q.objectives) {
			if (q.excludeObjectives) break;
			out->objectives[{qr, o.first}] = static_cast<uint8_t>(o.second & 3);
			if (!(o.second & 2)) all = false;
		}
		if (all) out->allObjectivesDone.insert(qr);
		for (auto& v : q.vars) out->scriptVars[{qr, v.first}] = v.second;
	}
	for (auto& g : in.globals) {
		if (g.volatileClock && !in.includeVolatileGlobals) continue;
		out->globals[fr(g.ref)] = g.value;
	}
	for (auto& r : in.refs) {
		auto rr = fr(r.ref);
		out->enabled[rr] = r.enabled;
#ifdef BASELINE_MUTANT   // mutation twin: reports every actor as killed
		if (r.isActor) out->kills.insert(rr);
#else
		if (r.isActor && !r.alive) out->kills.insert(rr);   // dead ones ONLY
#endif
		if (r.hasLock) out->locks[rr] = {r.locked, r.lockLevel};
		for (auto& v : r.vars) out->scriptVars[{rr, v.first}] = v.second;
	}
	auto player = fr(in.player);
	for (auto& f : in.factionRanks) out->factionRank[{player, fr(f.faction)}] = f.rank;
	for (auto& s : in.saidOnceInfos) out->sayOnce.insert(fr(s));
	auto& led = out->ledger[0];
	led.karma = rnd(in.karma);
	for (auto& r : in.reputations) led.rep[fr(r.faction)] = {rnd(r.fame), rnd(r.infamy)};
	*hash = BaselineContentHash(in.plugins);
	if (err) err->clear();
	return true;
}

}  // namespace fnvcoop::snapshot
