// Reads dialogue data out of game memory (L8). Game-free: works on fndlg::IMemory.
#pragma once
#include <functional>
#include <string>
#include <vector>
#include "dialog_layout.h"
#include "fnvcoop_dlg/messages.h"

namespace fndlg {

struct MenuView {
    Addr32 partnerRef = 0;
    Addr32 spokenInfo = 0;
    std::vector<Addr32> offered;   // TESTopicInfo* choices, in menu order, nulls removed
    bool barterShown = false;
};

// Reads the live DialogMenu (menuAddr = *(Addr32*)dialog_menu::kSingletonPtr). False if memory is unreadable.
bool readMenu(const IMemory& m, Addr32 menuAddr, MenuView& out);

struct InfoFlags {
    bool goodbye = false;
    uint8_t flags1 = 0, flags2 = 0, type = 0, saidOnce = 0;
    uint32_t speechChallenge = 0, actorValueOrPerk = 0;
    std::string prompt;
};
bool readInfoFlags(const IMemory& m, Addr32 info, InfoFlags& out);

// Offset inside TESTopicInfo where the response list head lives. -1 = not yet known (it is not named in the xNVSE
// headers); probeResponseOffset finds it from live memory by structure and cross-checks against known ESM text.
struct ResponseLayout { int32_t headOffset = -1; };

// Scans the 0x50-byte info object for a pointer to something shaped like a TESTopicInfoResponse chain
// (emotionValue 0..100, flags bit0, plausible string). Returns the offset or -1. If `expectText` is non-empty the
// first response text must start with it (live check against the ESM data from L3).
int32_t probeResponseOffset(const IMemory& m, Addr32 info, const std::string& expectText = std::string());

// Extracts the response chain: texts and emotion. Voice paths are added by the caller via voiceFile().
bool readLines(const IMemory& m, Addr32 info, const ResponseLayout& layout, std::vector<fnvcoop_dlg::Line>& out);

// Callable form of readLines for nvui::setInfoReader(...): INFO address -> its response lines (empty on failure or while the
// response layout is still unknown). `layout` is read at call time, so a later probeResponseOffset takes effect without
// re-installing the reader; both `mem` and `layout` must outlive the returned function.
std::function<std::vector<fnvcoop_dlg::Line>(uint32_t)> makeInfoReader(const IMemory& mem, const ResponseLayout* layout);

// Candidate voice file path for response n of an INFO (UNVERIFIED naming convention; callers must confirm the file
// exists before trusting it, else spectators fall back to subtitle only).
std::string voiceFile(const std::string& pluginFile, const std::string& voiceType, const std::string& topicEdid,
                      uint32_t infoLocalId, uint32_t responseNumber);

}  // namespace fndlg
