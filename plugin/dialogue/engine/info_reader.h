// Reads dialogue data out of game memory (L8). Game-free: works on fndlg::IMemory.
#pragma once
#include <functional>
#include <string>
#include <vector>
#include "dialog_layout.h"
#include "fnvcoop_dlg/messages.h"

namespace fndlg {

// One row of the DialogMenu choice ListBox (see list_box in dialog_layout.h).
struct MenuItem {
    Addr32 info = 0;    // TESTopicInfo*
    Addr32 topic = 0;   // TESTopic*
};

struct MenuView {
    Addr32 partnerRef = 0;
    Addr32 spokenInfo = 0;         // *0x11D9518
    uint32_t state = 0;            // DialogMenu+0x28 (dialog_menu::kState*)
    std::vector<MenuItem> items;   // choice rows in on-screen order (null INFOs kept out)
    std::vector<Addr32> offered;   // items[i].info, kept for callers that only need the INFOs
    bool barterShown = false;
};

// Reads the live DialogMenu (menuAddr = *(Addr32*)dialog_menu::kSingletonPtr). False if memory is unreadable or the list is cyclic.
bool readMenu(const IMemory& m, Addr32 menuAddr, MenuView& out);

struct InfoFlags {
    bool goodbye = false;
    bool sayOnceFlag = false;      // flags1 & 0x04: the INFO is not offered again once saidOnce is set
    uint8_t flags1 = 0, flags2 = 0, type = 0, saidOnce = 0;
    uint32_t speechChallenge = 0, actorValueOrPerk = 0;
    std::string prompt;
};
bool readInfoFlags(const IMemory& m, Addr32 info, InfoFlags& out);

// How response chains are found. The INFO object has no head pointer (dialog_layout.h response_cache): the chain is read from the
// engine's cache cell when the cache holds `info`, else loaded through the registered loader (the engine's own
// TESTopicInfo::GetResponses, see engine_xnvse.cpp), else - only for old synthetic layouts and tests - from a fixed offset inside the INFO.
struct ResponseLayout {
    int32_t headOffset = -1;       // legacy fallback: offset of a head pointer inside the INFO; -1 = none
    bool useEngineCache = true;    // false: only headOffset (the legacy path, used by tests)
};
// The loader makes `info`'s chain current in the engine cache (calls 0x61E780(info, 0)). Registered by the live engine module; returns
// false when it may not run (a line of ANOTHER INFO is being spoken: evicting the cache would free nodes the engine still walks).
using ResponseLoader = std::function<bool(Addr32 info)>;
void setResponseLoader(ResponseLoader f);
const ResponseLoader& responseLoader();
// Counters for the status line / tests.
struct ReaderStats { uint64_t cacheHits = 0, loaderCalls = 0, loaderRefused = 0, legacyReads = 0, failed = 0; };
ReaderStats& readerStats();

// Scans the 0x50-byte info object for a pointer to something shaped like a TESTopicInfoResponse chain
// (emotionValue 0..100, flags bit0, plausible string). Returns the offset or -1. If `expectText` is non-empty the
// first response text must start with it (live check against the ESM data from L3). LEGACY: the live INFO has no such field
// (statically verified), so the live probe is expected to answer -1; kept as the cross-check that proves it and for old layouts.
int32_t probeResponseOffset(const IMemory& m, Addr32 info, const std::string& expectText = std::string());

// True when the engine cache currently holds `info` and its chain head is set.
bool responseCacheHolds(const IMemory& m, Addr32 info);
// Reads the cached chain for `info` (no engine call). False if the cache holds another INFO or the chain is malformed.
bool readCachedLines(const IMemory& m, Addr32 info, std::vector<fnvcoop_dlg::Line>& out);
// Extracts the response chain: texts and emotion. Cache first, then the loader, then the legacy offset. Voice paths are added by the caller.
bool readLines(const IMemory& m, Addr32 info, const ResponseLayout& layout, std::vector<fnvcoop_dlg::Line>& out);

// Callable form of readLines for nvui::setInfoReader(...): INFO address -> its response lines (empty on failure). `layout` is read at
// call time; both `mem` and `layout` must outlive the returned function. A default-constructed layout is enough in the live game.
std::function<std::vector<fnvcoop_dlg::Line>(uint32_t)> makeInfoReader(const IMemory& mem, const ResponseLayout* layout);

// Candidate voice file path for response n of an INFO (UNVERIFIED naming convention; callers must confirm the file
// exists before trusting it, else spectators fall back to subtitle only).
std::string voiceFile(const std::string& pluginFile, const std::string& voiceType, const std::string& topicEdid,
                      uint32_t infoLocalId, uint32_t responseNumber);

}  // namespace fndlg
