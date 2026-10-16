// Unit test for the game-free snapshot core. Mutation twin: built with -DMUTANT the first
// expectation is deliberately wrong and the test MUST fail (run_tests.py asserts that).
#include "fnvcoop_snapshot/json.h"
#include "fnvcoop_snapshot/snapshot.h"
#include <cstdio>
#include <limits>
#include <string>

using namespace fnvcoop::snapshot;
static int fails = 0;
#define CHECK_EQ(a, b) do { std::string _a = (a), _b = (b); if (_a != _b) { std::printf("FAIL line %d\n  got:  %s\n  want: %s\n", __LINE__, _a.c_str(), _b.c_str()); ++fails; } } while (0)

int main() {
	// Insertion order must not affect output (sorted keys).
	Json a = Json::object(); a["zeta"] = 1; a["alpha"] = 2; a["mid"] = Json::array();
	Json b = Json::object(); b["mid"] = Json::array(); b["alpha"] = 2; b["zeta"] = 1;
#ifdef MUTANT
	CHECK_EQ(a.dump(), "{\"zeta\":1,\"alpha\":2,\"mid\":[]}"); // wrong on purpose
#else
	CHECK_EQ(a.dump(), "{\"alpha\":2,\"mid\":[],\"zeta\":1}");
#endif
	CHECK_EQ(a.dump(), b.dump());

	CHECK_EQ(Json::formatReal(1.5), "1.5");
	CHECK_EQ(Json::formatReal(2.0), "2");
	CHECK_EQ(Json::formatReal(-0.0), "0");
	CHECK_EQ(Json::formatReal(-0.0000001), "0");
	CHECK_EQ(Json::formatReal((double)0.1f), "0.1");
	CHECK_EQ(Json::formatReal(std::numeric_limits<double>::infinity()), "null");
	CHECK_EQ(Json::formatReal(std::numeric_limits<double>::quiet_NaN()), "null");

	Json s = std::string("a\"b\\c\n\x01");
	CHECK_EQ(s.dump(), "\"a\\\"b\\\\c\\n\\u0001\"");

	CHECK_EQ(FormRefString("FalloutNV.esm", 0xAB104C2A), "FalloutNV.esm:104C2A");
	CHECK_EQ(FormRefString("x.esp", 0x1), "x.esp:000001");
	CHECK_EQ(Hex64(Fnv1a64("a")), "af63dc4c8601ec8c");

	Json p = Json::object(); p["k"].push(1); p["k"].push("x");
	CHECK_EQ(p.dump(), "{\"k\":[1,\"x\"]}");
	CHECK_EQ(p.dump(2), "{\n  \"k\": [\n    1,\n    \"x\"\n  ]\n}\n");

	// Watch-ref parsing: comments, junk, case, duplicates, sorting.
	auto refs = ParseWatchRefs("# hdr\n  FalloutNV.esm:00abcd  # trailing\nbogus\nFalloutNV.esm:12345\nA.esp:000001\nFalloutNV.esm:00ABCD\r\n\r\nB.txt:000002\n");
	std::string joined;
	for (auto& r : refs) joined += r + "|";
#ifdef MUTANT
	CHECK_EQ(joined, "FalloutNV.esm:00abcd|bogus|");
#else
	CHECK_EQ(joined, "A.esp:000001|FalloutNV.esm:00ABCD|");
#endif
	CHECK_EQ(std::to_string(LoadWatchRefsFile("does/not/exist.txt").size()), "0");

	std::printf(fails ? "FAILED (%d)\n" : "OK\n", fails);
	return fails ? 1 : 0;
}
