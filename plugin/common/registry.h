// Internal to L1: process-wide registries behind nvcoop_api.h. Bridge and plugin glue read these.
#pragma once
#include <cstdint>
#include <string>
#include <vector>

#include "nvcoop_api.h"

namespace nvcoop::registry {

struct EventRec {
    uint64_t seq;
    uint64_t tMs;          // ms since process registry init
    std::string type;
    std::string dataJson;  // never empty ("null" if none)
};

struct EventPage {
    std::vector<EventRec> events;
    uint64_t next = 0;     // pass as "since" to continue
    uint64_t dropped = 0;  // events lost to ring overflow before `since`
};

constexpr size_t kEventCapacity = 4096;

// Events with seq > since (seq starts at 1). limit==0 -> no limit.
EventPage ReadEvents(uint64_t since, size_t limit);
uint64_t LastEventSeq();
void ClearEvents();  // test hook

StateProvider GetStateProvider();

struct MethodEntry {
    MethodFn fn;
    bool mainThread;
};
bool FindMethod(const std::string& name, MethodEntry& out);

const std::vector<Module>& Modules();

void SetLogFile(const std::string& path);

}  // namespace nvcoop::registry
