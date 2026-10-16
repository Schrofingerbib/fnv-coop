// TEST-ONLY bridge helpers (plugin/snapshot). Registered as bridge methods; every call refuses unless the process was
// started with env NVCOOP_TEST_HELPERS=1 (set by tools/goodsprings_sweep.py), so a normal game session cannot reach them.
//
//   test.quest.reset {"quests":["104C1C", ...]}   (hex form ids, FalloutNV.esm = index 0 form ids)
//     Puts each quest back into its pristine state: every stage isDone=0, currentStage=0, running/completed/failed flag
//     bits cleared (0x01, 0x02, 0x40), every objective status=0. Script variables, refs and inventories are NOT touched.
//     Purpose: the sweep loads a mid-game save in which most Goodsprings stages are already done; the engine does not
//     re-run a done stage's result script, so those stages could not be exercised otherwise.
#include "nvcoop_api.h"

#include "nvse/GameAPI.h"
#include "nvse/GameData.h"
#include "nvse/GameForms.h"
#include "nvse/GameObjects.h"
#include "nvse/GameScript.h"

#include <cctype>
#include <cstdio>
#include <cstdlib>
#include <string>
#include <vector>

namespace {

struct ResetArgs {
	std::vector<UInt32> ids;
	int reset = 0;
	int stagesCleared = 0;
	int objectivesCleared = 0;
	int logEntriesCleared = 0;
};

void DoReset(ResetArgs* a) {
	for (UInt32 id : a->ids) {
		TESForm* f = LookupFormByID(id);
		TESQuest* q = f ? DYNAMIC_CAST(f, TESForm, TESQuest) : nullptr;
		if (!q) continue;
		for (auto s = q->stages.Begin(); !s.End(); ++s)
			if (s.Get()) {
				if (s.Get()->isDone) { s.Get()->isDone = 0; ++a->stagesCleared; }
				// HYPOTHESIS (verify in game): each stage log entry remembers that it already ran through its logDate
				// (QuestStageItem+0x64); stages executed earlier in the save would otherwise not re-run their result script.
				for (auto e = s.Get()->logEntries.Begin(); !e.End(); ++e)
					if (e.Get() && e.Get()->logDate) { e.Get()->logDate = 0; ++a->logEntriesCleared; }
			}
		q->currentStage = 0;
		q->flags = static_cast<UInt8>(q->flags & ~0x43);   // running | completed | failed
		for (auto l = q->lVarOrObjectives.Begin(); !l.End(); ++l) {
			BGSQuestObjective* ob = reinterpret_cast<BGSQuestObjective*>(l.Get());
			if (ob && ob->quest == q && ob->status) { ob->status = 0; ++a->objectivesCleared; }
		}
		++a->reset;
	}
}

bool Guarded(ResetArgs* a) {
	__try {
		DoReset(a);
		return true;
	} __except (1) {
		return false;
	}
}

bool MQuestReset(const std::string& params, std::string& out) {
	const char* gate = std::getenv("NVCOOP_TEST_HELPERS");
	if (!gate || gate[0] != '1') { out = "test helpers disabled (start the game with NVCOOP_TEST_HELPERS=1)"; return false; }
	ResetArgs a;
	// params: {"quests":["104C1C","10A214"]}: every quoted run of hex digits inside the "quests" array
	size_t p = params.find("quests");
	size_t lb = p == std::string::npos ? p : params.find('[', p);
	size_t rb = lb == std::string::npos ? lb : params.find(']', lb);
	if (rb == std::string::npos) { out = "expected {\"quests\":[\"HEX\",...]}"; return false; }
	size_t i = lb;
	while ((i = params.find('"', i)) != std::string::npos && i < rb) {
		size_t j = params.find('"', i + 1);
		if (j == std::string::npos || j > rb) break;
		std::string tok = params.substr(i + 1, j - i - 1);
		i = j + 1;
		if (tok.empty() || tok.size() > 8) continue;
		bool hex = true;
		for (char c : tok) if (!std::isxdigit(static_cast<unsigned char>(c))) hex = false;
		if (hex) a.ids.push_back(static_cast<UInt32>(std::strtoul(tok.c_str(), nullptr, 16)));
	}
	if (a.ids.empty()) { out = "no quest ids"; return false; }
	if (!Guarded(&a)) { out = "access violation while resetting quests"; return false; }
	char buf[200];
	std::snprintf(buf, sizeof buf, "{\"reset\":%d,\"requested\":%d,\"stagesCleared\":%d,\"objectivesCleared\":%d,\"logEntriesCleared\":%d}", a.reset,
	              static_cast<int>(a.ids.size()), a.stagesCleared, a.objectivesCleared, a.logEntriesCleared);
	out = buf;
	return true;
}


// test.quest.setvar {"quest":"HEX","var":"bName","value":1}: write a quest script variable by NAME (case-insensitive).
// Equivalent of the console form `set <quest>.<var> to <value>`, which the sweep does not rely on (EDIDs do not resolve in
// the console; hex-id quest variable syntax is unverified).
struct SetVarArgs {
	UInt32 id = 0;
	std::string var;
	double value = 0;
	int status = 0;   // 1 ok, 2 no such quest, 3 no script, 4 no event list, 5 no such var
	UInt32 idx = 0;
	double old = 0;
};

void DoSetVar(SetVarArgs* a) {
	TESForm* f = LookupFormByID(a->id);
	TESQuest* q = f ? DYNAMIC_CAST(f, TESForm, TESQuest) : nullptr;
	if (!q) { a->status = 2; return; }
	Script* sc = q->scriptable.script;
	if (!sc) { a->status = 3; return; }
	if (!q->scriptEventList) { a->status = 4; return; }
	for (auto v = sc->varList.Begin(); !v.End(); ++v) {
		VariableInfo* vi = v.Get();
		if (!vi || !vi->name.m_data || _stricmp(vi->name.m_data, a->var.c_str()) != 0) continue;
		ScriptLocal* sl = q->scriptEventList->GetVariable(vi->idx);
		if (!sl) { a->status = 5; return; }
		a->idx = vi->idx;
		a->old = sl->data;
		sl->data = a->value;
		a->status = 1;
		return;
	}
	a->status = 5;
}

bool GuardedSetVar(SetVarArgs* a) {
	__try {
		DoSetVar(a);
		return true;
	} __except (1) {
		return false;
	}
}

std::string JsonField(const std::string& params, const char* key) {
	std::string k = std::string("\"") + key + "\"";
	size_t p = params.find(k);
	if (p == std::string::npos) return std::string();
	p = params.find(':', p);
	if (p == std::string::npos) return std::string();
	++p;
	while (p < params.size() && (params[p] == ' ' || params[p] == '"')) ++p;
	size_t e = p;
	while (e < params.size() && params[e] != '"' && params[e] != ',' && params[e] != '}') ++e;
	return params.substr(p, e - p);
}

bool MQuestSetVar(const std::string& params, std::string& out) {
	const char* gate = std::getenv("NVCOOP_TEST_HELPERS");
	if (!gate || gate[0] != '1') { out = "test helpers disabled (start the game with NVCOOP_TEST_HELPERS=1)"; return false; }
	SetVarArgs a;
	std::string q = JsonField(params, "quest"), v = JsonField(params, "var"), val = JsonField(params, "value");
	if (q.empty() || v.empty() || val.empty()) { out = "expected {\"quest\":\"HEX\",\"var\":\"name\",\"value\":N}"; return false; }
	a.id = static_cast<UInt32>(std::strtoul(q.c_str(), nullptr, 16));
	a.var = v;
	a.value = std::atof(val.c_str());
	if (!GuardedSetVar(&a)) { out = "access violation while setting the variable"; return false; }
	static const char* why[] = {"", "ok", "no such quest", "quest has no script", "quest script has no event list (never ran)", "no such variable"};
	if (a.status != 1) { out = std::string("setvar failed: ") + why[a.status < 6 ? a.status : 0]; return false; }
	char buf[160];
	std::snprintf(buf, sizeof buf, "{\"set\":true,\"index\":%u,\"old\":%g,\"new\":%g}", (unsigned)a.idx, a.old, a.value);
	out = buf;
	return true;
}

void OnLoad(void*) {
	nvcoop::RegisterMethod("test.quest.reset", MQuestReset, true);
	nvcoop::RegisterMethod("test.quest.setvar", MQuestSetVar, true);
}

}  // namespace

NVCOOP_MODULE(l7_testhelpers, "l7_testhelpers", OnLoad, nullptr, nullptr);
