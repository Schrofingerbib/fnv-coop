// Engine reader for the world baseline (see baseline_dump.h). Same xNVSE build flags as snapshot_nvse.cpp.
// Fields marked VERIFY are unconfirmed in a live game (docs/snapshot-schema.md section 10).
#include "fnvcoop_snapshot/baseline_dump.h"
#include "fnvcoop_snapshot/snapshot.h"
#include "fnvcoop/policy.h"   // shared fnvcoop::volatileKeys() (L4): the SAME list the session filter uses

#include "nvse/GameAPI.h"
#include "nvse/GameData.h"
#include "nvse/GameExtraData.h"
#include "nvse/GameForms.h"
#include "nvse/GameObjects.h"
#include "nvse/GameScript.h"

#include <cstdio>
#include <fstream>
#include <sstream>
#include <cstdlib>
#include <map>
#include <set>

namespace fnvcoop::snapshot {
namespace {

constexpr UInt32 kRepOffFame = 0x44, kRepOffInfamy = 0x48;   // VERIFY (docs/02-engine-re.md)
constexpr UInt32 kRefFlagDisabled = 0x800;
constexpr UInt32 kAvHealth = 16, kAvKarma = 23;

const char* Str(const char* s) { return s ? s : ""; }

BaselineRef Ref(UInt32 formId) {
	BaselineRef r;
	DataHandler* dh = DataHandler::Get();
	UInt32 idx = formId >> 24;
	if (dh && idx < dh->modList.loadedModCount && dh->modList.loadedMods[idx]) r.plugin = dh->modList.loadedMods[idx]->name;
	else r.plugin = "dyn";   // runtime-created ref: not stable across machines; callers should not sync these
	r.local = formId & 0x00FFFFFF;
	return r;
}

struct Filter {
	BaselineDumpStats stats;
};

fnvcoop::FormRef Fr(const BaselineRef& r) { return fnvcoop::FormRef::make(r.plugin, r.local); }

void Vars(Script* sc, ScriptEventList* evl, const BaselineRef& owner, Filter& f, std::map<uint16_t, double>& out) {
	if (!sc || !evl) return;
	for (auto v = sc->varList.Begin(); !v.End(); ++v) {
		VariableInfo* vi = v.Get();
		if (!vi || (vi->type != 0 && vi->type != 1)) continue;   // float/int only
		ScriptLocal* sl = evl->GetVariable(vi->idx);
		if (!sl) continue;
		// Names from the volatile file were resolved to indices by ResolveVolatileNames() (shared list).
		if (fnvcoop::volatileKeys().isVolatileVar(Fr(owner), static_cast<uint16_t>(vi->idx))) { ++f.stats.excludedVars; continue; }
		out[static_cast<uint16_t>(vi->idx)] = sl->data;
	}
}

bool IsClock(const char* name) {
	static const char* k[] = {"GameYear", "GameMonth", "GameDay", "GameHour", "GameDaysPassed", "TimeScale"};
	for (auto n : k) if (!_stricmp(n, name)) return true;
	return false;
}

// Maps (owner, variable name) from the volatile file to the script variable index using the live script.
bool ResolveVar(const fnvcoop::FormRef& owner, const std::string& name, uint16_t& index) {
	DataHandler* dh = DataHandler::Get();
	if (!dh) return false;
	UInt8 mi = dh->GetModIndex(owner.plugin.c_str());   // plugin names are lower-cased by FormRef::make; the lookup is case-insensitive
	if (mi == 0xFF) return false;
	TESForm* f = LookupFormByID(((UInt32)mi << 24) | (owner.localId & 0xFFFFFF));
	if (!f) return false;
	Script* sc = nullptr;
	if (TESQuest* q = DYNAMIC_CAST(f, TESForm, TESQuest)) sc = q->scriptable.script;
	else if (TESObjectREFR* r = DYNAMIC_CAST(f, TESForm, TESObjectREFR)) {
		if (ScriptEventList* evl = r->GetEventList()) sc = evl->m_script;
		if (!sc && r->baseForm) if (TESScriptableForm* sf = DYNAMIC_CAST(r->baseForm, TESForm, TESScriptableForm)) sc = sf->script;
	}
	if (!sc) return false;
	for (auto v = sc->varList.Begin(); !v.End(); ++v) {
		VariableInfo* vi = v.Get();
		if (vi && vi->name.m_data && !_stricmp(vi->name.m_data, name.c_str())) { index = static_cast<uint16_t>(vi->idx); return true; }
	}
	return false;
}

void ReadInto(const BaselineDumpOptions* o, BaselineInput* in, Filter* flt) {
	fnvcoop::volatileKeys().resolveNames(ResolveVar);   // idempotent; also benefits the session filter
	DataHandler* dh = DataHandler::Get();
	PlayerCharacter* pc = g_thePlayer ? *g_thePlayer : nullptr;
	if (!dh || !pc) return;   // leaves plugins/quests empty -> BuildShadowFromBaseline fails (never vacuous)
	in->includeVolatileGlobals = o->includeVolatileGlobals;
	in->player = Ref(pc->refID);   // VERIFY: 0x14 (PlayerRef) vs 0x7 (Player base)
	for (UInt32 i = 0; i < dh->modList.loadedModCount; ++i)
		if (dh->modList.loadedMods[i]) in->plugins.push_back(dh->modList.loadedMods[i]->name);

	for (auto it = dh->questList.Begin(); !it.End(); ++it) {
		TESQuest* q = it.Get();
		if (!q) continue;
		BaselineQuestIn b;
		b.ref = Ref(q->refID);
		b.stage = q->currentStage;
		b.running = (q->flags & 0x01) != 0;      // VERIFY bit meanings (snapshot-schema section 8)
		b.completed = (q->flags & 0x02) != 0;
		for (auto l = q->lVarOrObjectives.Begin(); !l.End(); ++l) {
			BGSQuestObjective* ob = reinterpret_cast<BGSQuestObjective*>(l.Get());
			if (ob && ob->quest == q) b.objectives[ob->objectiveId] = static_cast<uint8_t>(ob->status & 3);
		}
		// Field-level entries are queried through the same Effect predicate the session uses.
		auto fq = Fr(b.ref);
		auto count = [&](bool x) { if (x) ++flt->stats.excludedFields; return x; };
		b.excludeRunning = count(fnvcoop::volatileKeys().isVolatile(fnvcoop::Effect(fnvcoop::StartQuest{fq})));
		b.excludeStage = count(fnvcoop::volatileKeys().isVolatile(fnvcoop::Effect(fnvcoop::SetStage{fq, 0})));
		b.excludeObjectives = count(fnvcoop::volatileKeys().isVolatile(fnvcoop::Effect(fnvcoop::SetObjective{fq, 0, 0})));
		Vars(q->scriptable.script, q->scriptEventList, b.ref, *flt, b.vars);
		in->quests.push_back(std::move(b));
	}
	for (auto it = dh->globalList.Begin(); !it.End(); ++it) {
		TESGlobal* g = it.Get();
		if (!g) continue;
		BaselineGlobalIn b;
		b.ref = Ref(g->refID);
		b.value = g->data;
		b.volatileClock = IsClock(Str(g->name.m_data));
		if (b.volatileClock) ++flt->stats.excludedClockGlobals;
		if (!b.volatileClock && fnvcoop::volatileKeys().isVolatileGlobal(Fr(b.ref))) { ++flt->stats.excludedGlobals; continue; }
		in->globals.push_back(b);
	}
	for (auto it = dh->reputationList.Begin(); !it.End(); ++it) {
		TESReputation* t = it.Get();
		if (!t) continue;
		BaselineRepIn b;
		b.faction = Ref(t->refID);
		b.fame = *(float*)((UInt8*)t + kRepOffFame);
		b.infamy = *(float*)((UInt8*)t + kRepOffInfamy);
		in->reputations.push_back(b);
	}
	in->karma = pc->avOwner.Fn_03(kAvKarma);   // VERIFY float return

	// Player faction ranks for EVERY faction (-1 = none), base list overridden by ExtraFactionChanges. VERIFY order.
	std::map<UInt32, int> ranks;
	TESActorBaseData* bd = DYNAMIC_CAST(pc->baseForm, TESForm, TESActorBaseData);
	if (bd)
		for (auto f = bd->factionList.Begin(); !f.End(); ++f)
			if (f.Get() && f.Get()->faction) ranks[f.Get()->faction->refID] = f.Get()->rank;
	auto* fc = (ExtraFactionChanges*)pc->extraDataList.GetByType(kExtraData_FactionChanges);
	if (fc && fc->data)
		for (auto f = fc->data->Begin(); !f.End(); ++f)
			if (f.Get() && f.Get()->faction) ranks[f.Get()->faction->refID] = f.Get()->rank;
	for (auto it = dh->factionList.Begin(); !it.End(); ++it) {
		TESFaction* f = it.Get();
		if (!f) continue;
		auto r = ranks.find(f->refID);
		BaselineFactionIn b;
		b.faction = Ref(f->refID);
		b.rank = r == ranks.end() ? -1 : r->second;
		in->factionRanks.push_back(b);
	}

	// INFOs with the "said once" flag (TESTopicInfo+0x22). VERIFY: flag is set by the engine when an INFO is spoken.
	for (auto it = dh->topicInfoList.Begin(); !it.End(); ++it) {
		TESTopicInfo* ti = it.Get();
		if (ti && ti->saidOnce) in->saidOnceInfos.push_back(Ref(ti->refID));
	}

	// Watched refs: enabled/alive/lock/script vars.
	std::vector<std::string> watch = o->watchRefs;
	std::string wf = o->watchRefsFile;
	if (wf.empty()) { const char* e = std::getenv("FNVCOOP_WATCHREFS"); if (e) wf = e; }
	if (!wf.empty()) { auto more = LoadWatchRefsFile(wf); watch.insert(watch.end(), more.begin(), more.end()); }
	std::set<UInt32> seen;
	for (auto& s : watch) {
		size_t c = s.rfind(':');
		if (c == std::string::npos) continue;
		UInt8 idx = dh->GetModIndex(s.substr(0, c).c_str());
		if (idx == 0xFF) continue;
		UInt32 id = ((UInt32)idx << 24) | ((UInt32)std::strtoul(s.c_str() + c + 1, nullptr, 16) & 0xFFFFFF);
		if (!seen.insert(id).second) continue;
		TESForm* f = LookupFormByID(id);
		TESObjectREFR* r = f ? DYNAMIC_CAST(f, TESForm, TESObjectREFR) : nullptr;
		if (!r) continue;   // a ref that does not exist on this machine is simply absent
		BaselineRefIn b;
		b.ref = Ref(id);
		b.enabled = (r->flags & kRefFlagDisabled) == 0;
		Actor* a = DYNAMIC_CAST(f, TESForm, Actor);
		if (a) { b.isActor = true; b.alive = a->avOwner.Fn_03(kAvHealth) > 0.0f; }   // VERIFY: IsDead vfunc
		auto* lk = (ExtraLock*)r->extraDataList.GetByType(kExtraData_Lock);
		if (lk && lk->data) { b.hasLock = true; b.lockLevel = (int32_t)lk->data->lockLevel; b.locked = (lk->data->flags & 1) != 0; }   // VERIFY flag bit
		if (ScriptEventList* evl = r->GetEventList()) Vars(evl->m_script, evl, b.ref, *flt, b.vars);
		in->refs.push_back(std::move(b));
	}
}

bool GuardedRead(const BaselineDumpOptions* o, BaselineInput* in, Filter* flt) {
	__try {
		ReadInto(o, in, flt);
		return true;
	} __except (1) {
		return false;
	}
}

// Default volatile-keys file when neither the option nor FNVCOOP_VOLATILE_KEYS names one:
//   1. nvcoop_volatile_keys.txt next to nvcoop.dll (deployed copy of plugin/snapshot/data/volatile_keys.txt),
//   2. the in-repo copy baked in at build time (FNVCOOP_DEFAULT_VOLATILE_KEYS, set by plugin/snapshot/CMakeLists.txt).
// Returns "" if neither exists (then nothing but the clock globals is filtered, and the stats line says file=(none)).
bool FileExists(const std::string& p) { std::ifstream f(p, std::ios::binary); return static_cast<bool>(f); }

std::string DefaultVolatileKeysPath() {
	HMODULE mod = nullptr;
	if (GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
	                       reinterpret_cast<LPCSTR>(&DefaultVolatileKeysPath), &mod)) {
		char buf[MAX_PATH] = {0};
		if (GetModuleFileNameA(mod, buf, MAX_PATH)) {
			std::string dir = buf;
			size_t sl = dir.find_last_of("/");
			size_t bs = dir.find_last_of(static_cast<char>(92));
			if (bs != std::string::npos && (sl == std::string::npos || bs > sl)) sl = bs;
			if (sl != std::string::npos) {
				std::string cand = dir.substr(0, sl + 1) + "nvcoop_volatile_keys.txt";
				if (FileExists(cand)) return cand;
			}
		}
	}
#ifdef FNVCOOP_DEFAULT_VOLATILE_KEYS
	if (FileExists(FNVCOOP_DEFAULT_VOLATILE_KEYS)) return FNVCOOP_DEFAULT_VOLATILE_KEYS;
#endif
	return std::string();
}

BaselineDumpStats g_lastStats;
}  // namespace

const BaselineDumpStats& LastBaselineStats() { return g_lastStats; }

bool DumpBaselineShadowEx(fnvcoop::ShadowState* out, uint64_t* contentHash, std::string* err,
                          const BaselineDumpOptions& opts) {
	Filter flt;
	std::string vf = opts.volatileKeysFile;
	const char* how = "option";
	if (vf.empty()) { const char* e = std::getenv("FNVCOOP_VOLATILE_KEYS"); if (e) { vf = e; how = "env"; } }
	if (vf.empty()) { vf = DefaultVolatileKeysPath(); how = "default"; }
	if (!vf.empty()) {   // a named file must load: never silently unfiltered
		std::ifstream fin(vf, std::ios::binary);
		if (!fin) { if (err) *err = "cannot read volatile keys file: " + vf; return false; }
		std::stringstream ss;
		ss << fin.rdbuf();
		if (!fnvcoop::volatileKeys().loadFromText(ss.str(), err)) return false;   // L4's loader: the single grammar
	}
	flt.stats.volatileFileEntries = fnvcoop::volatileKeys().size();
	BaselineInput in;
	bool ok = GuardedRead(&opts, &in, &flt);
	char buf[512];
	std::snprintf(buf, sizeof buf, "volatile keys: file=%s (%s) entries=%zu excluded vars=%zu fields=%zu globals=%zu clock-globals=%zu unresolved-names=%zu",
	              vf.empty() ? "(none)" : vf.c_str(), vf.empty() ? "-" : how, flt.stats.volatileFileEntries, flt.stats.excludedVars,
	              flt.stats.excludedFields, flt.stats.excludedGlobals, flt.stats.excludedClockGlobals,
	              fnvcoop::volatileKeys().unresolvedNames());
	flt.stats.summary = buf;
	g_lastStats = flt.stats;
	if (!ok) {
		if (err) *err = "access violation while reading engine state";
		return false;
	}
	return BuildShadowFromBaseline(in, out, contentHash, err);
}

bool DumpBaselineShadow(fnvcoop::ShadowState* out, uint64_t* contentHash, std::string* err) {
	return DumpBaselineShadowEx(out, contentHash, err, BaselineDumpOptions());
}

}  // namespace fnvcoop::snapshot
