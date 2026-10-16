#include "fnvcoop_snapshot/snapshot.h"
#include <algorithm>
#include <cctype>
#include <cstdio>
#include <fstream>
#include <set>
#include <sstream>

namespace fnvcoop::snapshot {

std::string FormRefString(const std::string& pluginName, unsigned localId) {
	char buf[16];
	std::snprintf(buf, sizeof buf, ":%06X", localId & 0x00FFFFFFu);
	return pluginName + buf;
}

unsigned long long Fnv1a64(const std::string& s) {
	unsigned long long h = 14695981039346656037ULL;
	for (unsigned char c : s) { h ^= c; h *= 1099511628211ULL; }
	return h;
}

std::string Hex64(unsigned long long v) {
	char buf[24];
	std::snprintf(buf, sizeof buf, "%016llx", v);
	return buf;
}

static bool validRef(const std::string& r) {
	size_t c = r.rfind(':');
	if (c == std::string::npos || c == 0 || r.size() - c - 1 != 6) return false;
	std::string plug = r.substr(0, c);
	size_t d = plug.rfind('.');
	if (d == std::string::npos) return false;
	std::string ext = plug.substr(d + 1);
	for (auto& ch : ext) ch = (char)std::tolower((unsigned char)ch);
	if (ext != "esm" && ext != "esp" && ext != "esl") return false;
	for (size_t i = c + 1; i < r.size(); ++i) if (!std::isxdigit((unsigned char)r[i])) return false;
	return true;
}

std::vector<std::string> ParseWatchRefs(const std::string& text) {
	std::set<std::string> out;
	std::istringstream in(text);
	std::string line;
	while (std::getline(in, line)) {
		size_t h = line.find('#');
		if (h != std::string::npos) line.erase(h);
		size_t a = line.find_first_not_of(" \t\r\n"), b = line.find_last_not_of(" \t\r\n");
		if (a == std::string::npos) continue;
		std::string r = line.substr(a, b - a + 1);
		if (!validRef(r)) continue;
		size_t c = r.rfind(':');
		for (size_t i = c + 1; i < r.size(); ++i) r[i] = (char)std::toupper((unsigned char)r[i]);
		out.insert(r);
	}
	return std::vector<std::string>(out.begin(), out.end());
}

std::vector<std::string> LoadWatchRefsFile(const std::string& path) {
	std::ifstream f(path, std::ios::binary);
	if (!f) return {};
	std::stringstream ss;
	ss << f.rdbuf();
	return ParseWatchRefs(ss.str());
}

} // namespace fnvcoop::snapshot
