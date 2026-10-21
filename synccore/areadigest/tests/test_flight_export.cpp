// The heal counters reach the flight recorder: header Meta ("heals.<category>.total|perMin|mismatches") and kMarkHeal markers.
// Twins 41 (the counters are not written to the Meta record) and 42 (a healed key leaves no marker) are flight-recorder faults that this
// test switches on itself from FNV_MUT.
#include <cstdio>
#include <filesystem>
#include <map>
#include <string>
#include "flight/flight.h"
#include "fnvcoop_mut.h"
#include "th.h"

using namespace adt;
namespace fl = fnvcoop::flight;

namespace {

std::map<std::string, std::string> metaOf(const fl::Trace& t) {
    std::map<std::string, std::string> m;
    for (const auto& r : t.records) {
        if (r.type != fl::kMeta) continue;
        size_t i = 0;
        auto str = [&]() -> std::string {
            if (i + 2 > r.payload.size()) return {};
            const size_t n = r.payload[i] | (r.payload[i + 1] << 8);
            i += 2;
            if (i + n > r.payload.size()) return {};
            std::string s(reinterpret_cast<const char*>(r.payload.data() + i), n);
            i += n;
            return s;
        };
        while (i < r.payload.size()) {
            std::string k = str();
            std::string v = str();
            m[k] = v;
        }
    }
    return m;
}

std::vector<std::string> healMarkers(const fl::Trace& t) {
    std::vector<std::string> out;
    for (const auto& r : t.records) {
        if (r.type != fl::kMarker || r.payload.size() < 4) continue;
        const uint16_t kind = static_cast<uint16_t>(r.payload[0] | (r.payload[1] << 8));
        if (kind != fl::kMarkHeal) continue;
        const size_t n = r.payload[2] | (r.payload[3] << 8);
        if (4 + n <= r.payload.size()) out.emplace_back(reinterpret_cast<const char*>(r.payload.data() + 4), n);
    }
    return out;
}

}  // namespace

TEST(flightexport, heal_counters_and_healed_keys_are_in_the_dump) {
    fl::flightMutations().healNoMeta = FNV_MUT(41);
    fl::flightMutations().healedNoMarker = FNV_MUT(42);

    struct Obs : IAreaObserver {
        fl::FlightRecorder* rec = nullptr;
        void onHeal(const HealNote& n) override { rec->healed(static_cast<uint8_t>(n.cat), n.text); }
    } obs;

    Rig r(2);
    fl::Config cfg;
    cfg.ringBytes = 1 << 20;
    cfg.label = "areadigest-test";
    fl::FlightRecorder rec(cfg);
    obs.rec = &rec;
    r.cl(1).sync->setObserver(&obs);
    r.addActor(100, 0x100, V(0, 0, 0), 0);
    r.run(4000);
    r.cl(1).actors.a[100].hp = 40;
    r.run(9000);
    REQUIRE(r.healsApplied(1, Cat::Actors) == 1);

    std::vector<fl::FlightRecorder::HealRow> rows;
    for (const auto& c : r.cl(1).sync->catRows(r.bus.now)) rows.push_back({static_cast<uint8_t>(c.cat), c.total, c.perMinute, c.mismatches});
    rec.healCounters(rows);
    rec.healCounters(rows);                                   // the same totals again: no second summary marker

    const std::string path = (std::filesystem::temp_directory_path() / "areadigest_flight_export.nvfr").string();
    REQUIRE(rec.dump(path, fl::kDumpTest));
    fl::Trace t;
    std::string err;
    REQUIRE(fl::readTrace(path, t, &err));
    std::remove(path.c_str());

    const auto meta = metaOf(t);
    CHECK(meta.count("heals.actors.total") == 1 && meta.at("heals.actors.total") == "1");     // twin 41
    CHECK(meta.count("heals.actors.perMin") == 1 && meta.at("heals.actors.perMin") == "1");
    CHECK(meta.count("heals.story.total") == 1 && meta.at("heals.story.total") == "0");
    CHECK(meta.count("heals.total") == 1 && meta.at("heals.total") == "1" && meta.at("heals.perMin") == "1");
    CHECK(meta.count("heals.actors.mismatches") == 1 && meta.count("heals.objects.total") == 1);

    const auto marks = healMarkers(t);
    int perKey = 0, summaries = 0;
    for (const auto& m : marks) {
        if (m.rfind("heal actors ", 0) == 0) {
            ++perKey;
            CHECK(m.find("hp 40") != std::string::npos && m.find("hp 100") != std::string::npos);   // both values are in the line
        }
        if (m.rfind("heals/min", 0) == 0) ++summaries;
    }
    CHECK(perKey == 1);                                        // twin 42
    CHECK(summaries == 1);                                     // moved once, not on the repeated call
    fl::flightMutations() = fl::FlightMutations();
}
