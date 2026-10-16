// Engine-side dumper. Compiles against xNVSE headers (RUNTIME, FalloutNV.exe 1.4.0.525, x86).
// The whole build runs under SEH (GuardedBuild); a fault yields meta.error + zero counts, which
// the snapdiff empty-snapshot guard rejects.
// Fields marked VERIFY are unconfirmed against a live game: see docs/snapshot-schema.md section 8.
#include "fnvcoop_snapshot/snapshot.h"
#include "fnvcoop_snapshot/json.h"

#include "nvse/GameAPI.h"
#include "nvse/GameData.h"
#include "nvse/GameExtraData.h"
#include "nvse/GameForms.h"
#include "nvse/GameObjects.h"
#include "nvse/GameScript.h"

#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <map>
#include <set>

namespace fnvcoop::snapshot {
namespace {

// TESReputation layout from docs/02-engine-re.md (JIP GameForms.h): VERIFY in-game.
constexpr UInt32 kRepOffFame = 0x44;
constexpr UInt32 kRepOffInfamy = 0x48;
constexpr UInt32 kRefFlagDisabled = 0x800; // TESForm::flags, same bit Cmd_GetDisabled tests
constexpr UInt32 kFormIdCaps = 0x0000000F; // Caps001 in FalloutNV.esm
constexpr UInt32 kAvHealth = 16;
constexpr UInt32 kAvKarma = 23;

const char* Str(const char* s) { return s ? s : ""; }

UInt32 g_lastId = 0;   // last form/code touched, reported when a section faults

std::string RefOf(UInt32 formId) {
	if (!formId) return "";
	DataHandler* dh = DataHandler::Get();
	UInt32 idx = formId >> 24;
	if (dh && idx < dh->modList.loadedModCount && dh->modList.loadedMods[idx])
		return FormRefString(dh->modList.loadedMods[idx]->name, formId);
	char buf[24];
	std::snprintf(buf, sizeof buf, "dyn:%08X", formId); // runtime-created (0xFF) refs: not stable across machines
	return buf;
}

std::string RefOf(const TESForm* f) { return f ? RefOf(f->refID) : std::string(); }

float AvCurrent(Actor* a, UInt32 code) { return a->avOwner.Fn_03(code); } // GetActorValue, VERIFY float return
float AvBase(Actor* a, UInt32 code) { return a->avOwner.Fn_01(code); }    // GetBaseActorValue, VERIFY

struct AvName { UInt32 code; const char* name; };
const AvName kAvs[] = {
	{5, "Strength"}, {6, "Perception"}, {7, "Endurance"}, {8, "Charisma"}, {9, "Intelligence"},
	{10, "Agility"}, {11, "Luck"}, {12, "ActionPoints"}, {13, "CarryWeight"}, {16, "Health"},
	{23, "Karma"}, {24, "XP"}, {25, "Head"}, {26, "Torso"}, {27, "LeftArm"}, {28, "RightArm"},
	{29, "LeftLeg"}, {30, "RightLeg"}, {31, "Brain"}, {32, "Barter"}, {33, "BigGuns"},
	{34, "EnergyWeapons"}, {35, "Explosives"}, {36, "Lockpick"}, {37, "Medicine"},
	{38, "MeleeWeapons"}, {39, "Repair"}, {40, "Science"}, {41, "Guns"}, {42, "Sneak"},
	{43, "Speech"}, {44, "Survival"}, {45, "Unarmed"}, {46, "InventoryWeight"}, {54, "RadLevel"},
};

 // Script variables (name from the script's variable list = SCVR/SLSD names; "var<idx>" if unnamed).
// Only numeric variables (float/int; ints are stored as double in ScriptLocal) are reported. A script whose
// event list does not exist yet (script never ran) yields no vars. VERIFY name availability at runtime.
void DumpScriptVars(Script* sc, ScriptEventList* evl, Json& vars) {
	if (!sc || !evl) return;
	for (auto v = sc->varList.Begin(); !v.End(); ++v) {
		VariableInfo* vi = v.Get();
		if (!vi) continue;
		ScriptLocal* sl = evl->GetVariable(vi->idx);
		if (!sl) continue;
		std::string name = vi->name.m_data && *vi->name.m_data ? vi->name.m_data : "var" + std::to_string(vi->idx);
		if (vi->type == 0 /*float*/ || vi->type == 1 /*int*/) vars[name] = sl->data;
	}
}

int g_skippedTargets = 0;       // objective targets whose pointer could not be read (quest 000842DD in game): skipped, counted

// Reads tg->target->refID under its own SEH frame; returns 0 on a bad pointer. Targets only feed the automatic
// watch-ref list, so a bad one is skipped and counted (meta.skippedObjectiveTargets) instead of failing the dump.
UInt32 SafeTargetRefId(BGSQuestObjective::Target* tg) {
	__try {
		return tg && tg->target ? tg->target->refID : 0;
	} __except (1) {
		return 0xFFFFFFFFu;
	}
}

int g_step = 0;                 // 1 basic, 2 stages, 3 objectives, 4 objective targets, 5 vars (fault diagnostics)
std::string g_questFaults;      // per-quest faults of the last AddQuests call: "EDIDless form step"

struct QuestArgs {
	TESQuest* q;
	Json* quests;
	std::set<UInt32>* targets;
	const SnapshotOptions* o;
	bool added;
};

void OneQuest(QuestArgs* a) {
	TESQuest* q = a->q;
	const SnapshotOptions& o = *a->o;
	g_step = 1;
	std::string edid = Str(q->editorName.m_data);
	if (!o.questEdidPrefixes.empty()) {
		bool ok = false;
		for (auto& p : o.questEdidPrefixes) if (edid.compare(0, p.size(), p) == 0) ok = true;
		if (!ok) return;
	}
	Json& j = (*a->quests)[RefOf(q)];
	j["edid"] = edid;
	j["name"] = Str(q->fullName.name.m_data);
	j["rawFlags"] = (int)q->flags;
	j["running"] = (q->flags & 0x01) != 0;   // bit0: started/running (NVSE comment: startGameEnabled/isRunning)
	j["completed"] = (q->flags & 0x02) != 0; // bit1: completed (set when all objectives reach 3), VERIFY
	j["failed"] = (q->flags & 0x40) != 0;    // in game: failing stages leave 0x42 (0x02|0x40), completing ones 0x02
	j["currentStage"] = (int)q->currentStage;
	g_step = 2;
	std::vector<int> ds;
	for (auto s = q->stages.Begin(); !s.End(); ++s)
		if (s.Get() && s.Get()->isDone) ds.push_back(s.Get()->stage);
	std::sort(ds.begin(), ds.end());
	ds.erase(std::unique(ds.begin(), ds.end()), ds.end());
	Json done = Json::array();
	for (int v : ds) done.push(v);
	j["completedStages"] = done;

	g_step = 3;
	Json objs = Json::object();
	for (auto l = q->lVarOrObjectives.Begin(); !l.End(); ++l) {
		// The list's data pointers ARE BGSQuestObjective* (NVSE GetNthQuestObjective reinterpret_casts the item;
		// dereferencing it through the header's union read the vtable = 0 objectives, found by L10 in game).
		// The ->quest back-pointer check stays as a sanity filter.
		BGSQuestObjective* ob = reinterpret_cast<BGSQuestObjective*>(l.Get());
		if (!ob || ob->quest != q) continue;
		Json& oj = objs[std::to_string(ob->objectiveId)];
		oj["displayed"] = (ob->status & 1) != 0;
		oj["completed"] = (ob->status & 2) != 0;
		if (ob->status & 1) {
			g_step = 4;
			for (auto t = ob->targets.Begin(); !t.End(); ++t) {
				BGSQuestObjective::Target* tg = t.Get() ? *t.Get() : nullptr; // tList<Target*>: Get() is Target**
				UInt32 id = SafeTargetRefId(tg);
				if (id == 0xFFFFFFFFu) ++g_skippedTargets;
				else if (id) a->targets->insert(id);
			}
			g_step = 3;
		}
	}
	j["objectives"] = objs;

	g_step = 5;
	Json vars = Json::object();
	DumpScriptVars(q->scriptable.script, q->scriptEventList, vars);
	j["vars"] = vars;
	a->added = true;
}

// One SEH frame per quest: a bad quest is named and skipped, the rest of the dump survives (and meta.error is set).
bool GuardQuest(QuestArgs* a) {
	__try {
		OneQuest(a);
		return true;
	} __except (1) {
		return false;
	}
}

void AddQuests(Json& quests, const SnapshotOptions& o, std::set<UInt32>& objectiveTargets, int& count) {
	DataHandler* dh = DataHandler::Get();
	g_questFaults.clear();
	g_skippedTargets = 0;
	for (auto it = dh->questList.Begin(); !it.End(); ++it) {
		TESQuest* q = it.Get();
		if (!q) continue;
		g_lastId = q->refID;
		QuestArgs a{q, &quests, &objectiveTargets, &o, false};
		if (!GuardQuest(&a)) {
			char b[64];
			std::snprintf(b, sizeof b, "%s%08X@step%d", g_questFaults.empty() ? "" : ",", (unsigned)q->refID, g_step);
			g_questFaults += b;
			continue;
		}
		if (a.added) ++count;
	}
}

void AddGlobals(Json& g, int& count) {
	DataHandler* dh = DataHandler::Get();
	for (auto it = dh->globalList.Begin(); !it.End(); ++it) {
		TESGlobal* t = it.Get();
		if (!t) continue;
		Json& j = g[RefOf(t)];
		j["edid"] = Str(t->name.m_data);
		j["type"] = std::string(1, (char)t->type);
		j["value"] = (double)t->data;
		++count;
	}
}

void AddReputations(Json& r, int& count) {
	DataHandler* dh = DataHandler::Get();
	for (auto it = dh->reputationList.Begin(); !it.End(); ++it) {
		TESReputation* t = it.Get();
		if (!t) continue;
		Json& j = r[RefOf(t)];
		j["edid"] = Str(t->GetEditorID());
		j["fame"] = (double)*(float*)((UInt8*)t + kRepOffFame);
		j["infamy"] = (double)*(float*)((UInt8*)t + kRepOffInfamy);
		++count;
	}
}

void AddFactionRanks(Json& out, PlayerCharacter* pc) {
	// Base ranks from the player's TESNPC, overridden by runtime ExtraFactionChanges. VERIFY merge order.
	std::map<UInt32, int> ranks;
	TESActorBaseData* bd = DYNAMIC_CAST(pc->baseForm, TESForm, TESActorBaseData);
	if (bd)
		for (auto f = bd->factionList.Begin(); !f.End(); ++f)
			if (f.Get() && f.Get()->faction) ranks[f.Get()->faction->refID] = f.Get()->rank;
	auto* fc = (ExtraFactionChanges*)pc->extraDataList.GetByType(kExtraData_FactionChanges);
	if (fc && fc->data)
		for (auto f = fc->data->Begin(); !f.End(); ++f)
			if (f.Get() && f.Get()->faction) ranks[f.Get()->faction->refID] = f.Get()->rank;
	out = Json::object();
	for (auto& kv : ranks) {
		if (kv.second < 0) continue; // -1 = not a member
		TESForm* ff = LookupFormByID(kv.first);
		Json& j = out[RefOf(kv.first)];
		j["edid"] = ff ? Str(ff->GetEditorID()) : "";
		j["rank"] = kv.second;
	}
}

void AddRef(Json& refs, UInt32 formId) {
	g_lastId = formId;
	TESForm* f = LookupFormByID(formId);
	Json& j = refs[RefOf(formId)];
	if (!f) { j["exists"] = false; return; }
	j["exists"] = true;
	j["edid"] = Str(f->GetEditorID());
	TESObjectREFR* r = DYNAMIC_CAST(f, TESForm, TESObjectREFR);
	if (!r) return;
	j["enabled"] = (r->flags & kRefFlagDisabled) == 0;
	j["cell"] = r->parentCell ? RefOf(r->parentCell) : "";
	Actor* a = DYNAMIC_CAST(f, TESForm, Actor);
	if (a) j["alive"] = AvCurrent(a, kAvHealth) > 0.0f; // VERIFY: prefer the engine IsDead vfunc (Unk_8B)
	ScriptEventList* evl = r->GetEventList(); // attached object/actor script (ExtraScript); null if none/not run
	if (evl) {
		Json vars = Json::object();
		DumpScriptVars(evl->m_script, evl, vars);
		if (vars.size()) j["vars"] = vars;
	}
}

void SetEmptyCounts(Json& meta) {
	Json counts = Json::object();
	counts["quests"] = 0; counts["globals"] = 0; counts["reputations"] = 0;
	meta["counts"] = counts;
}

// ---- per-section SEH guards: a fault in one section is reported by name (meta.error) instead of hiding the whole dump.
struct Ctx {
	const SnapshotOptions* o = nullptr;
	Json* root = nullptr;
	DataHandler* dh = nullptr;
	PlayerCharacter* pc = nullptr;
	std::set<UInt32> targets;
	int nq = 0, ng = 0, nr = 0;
};
typedef void (*SecFn)(Ctx*);

void SecQuests(Ctx* c) { AddQuests((*c->root)["shared"]["quests"], *c->o, c->targets, c->nq); }
void SecGlobals(Ctx* c) { AddGlobals((*c->root)["shared"]["globals"], c->ng); }
void SecReps(Ctx* c) { AddReputations((*c->root)["shared"]["reputations"], c->nr); }
void SecKarma(Ctx* c) { (*c->root)["shared"]["karma"] = (double)AvCurrent(c->pc, kAvKarma); }
void SecFactions(Ctx* c) { AddFactionRanks((*c->root)["shared"]["factionRanks"], c->pc); }
void SecRefs(Ctx* c) {
	Json& refs = (*c->root)["shared"]["refs"];
	refs = Json::object();
	std::vector<std::string> watch = c->o->watchRefs;
	std::string wf = c->o->watchRefsFile;
	if (wf.empty()) { const char* e = std::getenv("FNVCOOP_WATCHREFS"); if (e) wf = e; }
	if (!wf.empty()) { auto more = LoadWatchRefsFile(wf); watch.insert(watch.end(), more.begin(), more.end()); }
	for (auto& s : watch) {
		size_t col = s.rfind(':');
		if (col == std::string::npos) continue;
		std::string plug = s.substr(0, col);
		UInt32 local = (UInt32)std::strtoul(s.c_str() + col + 1, nullptr, 16);
		UInt8 idx = c->dh->GetModIndex(plug.c_str());
		if (idx != 0xFF) AddRef(refs, ((UInt32)idx << 24) | (local & 0xFFFFFF));
	}
	for (UInt32 id : c->targets) AddRef(refs, id);
}

Json& Player(Ctx* c) { return (*c->root)["perPlayer"][c->o->playerId]; }

void SecPlayerAv(Ctx* c) {
	Json avs = Json::object();
	for (const auto& a : kAvs) {
		g_lastId = a.code;
		Json& j = avs[a.name];
		j["base"] = (double)AvBase(c->pc, a.code);
		j["current"] = (double)AvCurrent(c->pc, a.code);
	}
	Player(c)["actorValues"] = avs;
}
void SecPlayerInv(Ctx* c) {
	Json inv = Json::object();
	long long caps = 0;
	InventoryItemsMap items;
	if (c->pc->GetInventoryItems(items)) {
		for (InventoryItemsMap::Iterator it(items); !it.End(); ++it) {
			TESForm* item = it.Key();
			if (!item) continue;
			g_lastId = item->refID;
			int n = it.Get().count;
			if (n <= 0) continue;
			if (item->refID == kFormIdCaps) { caps = n; continue; }
			Json& j = inv[RefOf(item)];
			j["edid"] = Str(item->GetEditorID());
			j["count"] = n;
		}
	}
	Player(c)["inventory"] = inv;
	Player(c)["caps"] = caps;
}
void SecPlayerPerks(Ctx* c) {
	Json perks = Json::object();
	for (auto it = c->dh->perkList.Begin(); !it.End(); ++it) {
		BGSPerk* pk = it.Get();
		if (!pk) continue;
		g_lastId = pk->refID;
		UInt8 rank = c->pc->GetPerkRank(pk, false); // VERIFY: alt=false => normal perks
		if (rank) perks[RefOf(pk)] = (int)rank;
	}
	Player(c)["perks"] = perks;
}
void SecPlayerPos(Ctx* c) {
	Json pos = Json::object();
	pos["x"] = (double)c->pc->posX; pos["y"] = (double)c->pc->posY; pos["z"] = (double)c->pc->posZ;
	pos["rotZ"] = (double)c->pc->rotZ;
	pos["cell"] = c->pc->parentCell ? RefOf(c->pc->parentCell) : "";
	Player(c)["position"] = pos;
}

// SEH frame in a function with no objects needing unwinding (MSVC C2712).
bool GuardSec(SecFn fn, Ctx* c) {
	__try {
		fn(c);
		return true;
	} __except (1 /*EXCEPTION_EXECUTE_HANDLER*/) {
		return false;
	}
}

void Run(Ctx& c, const char* name, SecFn fn, std::string& faults) {
	g_lastId = 0;
	if (GuardSec(fn, &c)) {
		if (fn == SecQuests && !g_questFaults.empty()) {
			faults += std::string(faults.empty() ? "" : "; ") + "quests faulted: " + g_questFaults;
		}
		return;
	}
	char b[96];
	std::snprintf(b, sizeof b, "%s%s (near form %08X)", faults.empty() ? "" : "; ", name, (unsigned)g_lastId);
	faults += b;
}

void BuildInto(const SnapshotOptions& o, Json* out) {
	Json& root = *out;
	root = Json::object();
	root["schemaVersion"] = 1;
	Json& meta = root["meta"];
	meta["gameVersion"] = "1.4.0.525";
	meta["instance"] = o.instance;
	if (!o.timestamp.empty()) meta["timestamp"] = o.timestamp;

	DataHandler* dh = DataHandler::Get();
	PlayerCharacter* pc = g_thePlayer ? *g_thePlayer : nullptr;
	if (!dh || !pc) {
		meta["error"] = "DataHandler or player not available";
		SetEmptyCounts(meta);
		return;
	}

	Json plugins = Json::array();
	std::string joined;
	for (UInt32 i = 0; i < dh->modList.loadedModCount; ++i) {
		ModInfo* mi = dh->modList.loadedMods[i];
		if (!mi) continue;
		plugins.push(std::string(mi->name));
		joined += mi->name; joined += '\n';
	}
	meta["plugins"] = plugins;
	meta["pluginsHash"] = Hex64(Fnv1a64(joined)); // load ORDER matters: FormRefs are only comparable if equal

	Ctx c;
	c.o = &o; c.root = &root; c.dh = dh; c.pc = pc;
	std::string faults;
	if (o.shared) {
		Run(c, "quests", SecQuests, faults);
		Run(c, "globals", SecGlobals, faults);
		Run(c, "reputations", SecReps, faults);
		Run(c, "karma", SecKarma, faults);
		Run(c, "factionRanks", SecFactions, faults);
		Run(c, "refs", SecRefs, faults);
	}
	if (o.perPlayer) {
		Run(c, "player.actorValues", SecPlayerAv, faults);
		Run(c, "player.inventory", SecPlayerInv, faults);
		Run(c, "player.perks", SecPlayerPerks, faults);
		Run(c, "player.position", SecPlayerPos, faults);
	}
	Json counts = Json::object();
	counts["quests"] = c.nq; counts["globals"] = c.ng; counts["reputations"] = c.nr;
	meta["counts"] = counts;
	if (g_skippedTargets) meta["skippedObjectiveTargets"] = g_skippedTargets;
	// A faulted section leaves a partial snapshot: flag it so every consumer (guard) rejects it, and name the culprit.
	if (!faults.empty()) meta["error"] = "access violation in: " + faults;
}

bool GuardedBuild(const SnapshotOptions* o, Json* out) {
	__try {
		BuildInto(*o, out);
		return true;
	} __except (1 /*EXCEPTION_EXECUTE_HANDLER*/) {
		return false;
	}
}

} // namespace

std::string SnapshotJson(const SnapshotOptions& opts) {
	Json root;
	if (!GuardedBuild(&opts, &root)) {
		root = Json::object();
		root["schemaVersion"] = 1;
		root["meta"]["error"] = "access violation while reading engine state";
		SetEmptyCounts(root["meta"]);
	}
	return root.dump(opts.indent);
}

} // namespace fnvcoop::snapshot
