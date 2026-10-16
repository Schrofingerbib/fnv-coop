// Loads the generated keys file through L4's fnvcoop::volatileKeys() and compares every answer with the Python
// reference model's expectations (queries.txt). Build with -DVOL_MUTANT to flip on L4's ignoreVolatile fault switch:
// the test MUST then fail.
#include "fnvcoop/mutations.h"
#include "fnvcoop/policy.h"
#include <cstdio>
#include <fstream>
#include <map>
#include <sstream>

using namespace fnvcoop;

static std::string slurp(const char* p) {
	std::ifstream f(p, std::ios::binary);
	std::stringstream ss;
	ss << f.rdbuf();
	return ss.str();
}

static FormRef parseRef(const std::string& s) {
	size_t c = s.rfind(':');
	return FormRef::make(s.substr(0, c), static_cast<uint32_t>(std::strtoul(s.c_str() + c + 1, nullptr, 16)));
}

int main(int argc, char** argv) {
	if (argc < 3) { std::printf("usage: keys.txt queries.txt\n"); return 2; }
	std::string keys = slurp(argv[1]);
	if (keys.empty()) { std::printf("FAIL: cannot read keys\n"); return 1; }
	VolatileKeys k;
	std::string err;
	if (!k.loadFromText(keys, &err)) { std::printf("FAIL: loader rejected the file: %s\n", err.c_str()); return 1; }
	std::map<std::pair<FormRef, std::string>, uint16_t> names;
	std::ifstream q(argv[2]);
	std::string line;
	std::vector<std::string> lines;
	while (std::getline(q, line)) {
		std::istringstream ls(line);
		std::string kind, ref, a;
		int n, idx;
		if (!(ls >> kind)) continue;
		if (kind == "name") { ls >> ref >> a >> idx; names[{parseRef(ref), a}] = static_cast<uint16_t>(idx); }
		else lines.push_back(line);
	}
	k.resolveNames([&](const FormRef& o, const std::string& nm, uint16_t& i) {
		std::string l = nm;
		for (auto& c : l) if (c >= 'A' && c <= 'Z') c = static_cast<char>(c - 'A' + 'a');
		auto it = names.find({o, l});
		if (it == names.end()) return false;
		i = it->second;
		return true;
	});
#ifdef VOL_MUTANT
	mutations().ignoreVolatile = true;
#endif
	int fails = 0, checked = 0;
	for (auto& l : lines) {
		std::istringstream ls(l);
		std::string kind, ref, a;
		int idx = 0, want = 0;
		ls >> kind;
		bool got = false;
		if (kind == "qvar") { ls >> ref >> idx >> want; got = k.isVolatileVar(parseRef(ref), static_cast<uint16_t>(idx)); }
		else if (kind == "qglobal") { ls >> ref >> want; got = k.isVolatileGlobal(parseRef(ref)); }
		else if (kind == "qfield") {
			ls >> ref >> a >> want;
			FormRef f = parseRef(ref);
			got = a == "running" ? k.isVolatile(Effect(StartQuest{f}))
			    : a == "stage" ? k.isVolatile(Effect(SetStage{f, 0}))
			                   : k.isVolatile(Effect(SetObjective{f, 0, 0}));
		} else if (kind == "qsize") { ls >> want; got = false; if (static_cast<int>(k.size()) != want) { std::printf("FAIL size %zu != %d\n", k.size(), want); ++fails; } ++checked; continue; }
		else if (kind == "qunresolved") { ls >> want; if (static_cast<int>(k.unresolvedNames()) != want) { std::printf("FAIL unresolved %zu != %d\n", k.unresolvedNames(), want); ++fails; } ++checked; continue; }
		else continue;
		++checked;
		if (got != (want != 0)) { std::printf("FAIL %s (loader says %d, reference says %d)\n", l.c_str(), got, want); ++fails; }
	}
	if (checked < 50) { std::printf("FAIL: only %d checks ran\n", checked); return 1; }
	std::printf(fails ? "FAILED (%d of %d)\n" : "OK (%d checks)\n", fails ? fails : checked, checked);
	return fails ? 1 : 0;
}
