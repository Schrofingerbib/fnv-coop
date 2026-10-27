#include "info_reader.h"
#include <algorithm>
#include <cctype>
#include <cstdio>
#include "fnvcoop_dlg/mutations.h"

namespace fndlg {

bool readMenu(const IMemory& m, Addr32 menu, MenuView& out) {
    using namespace dialog_menu;
    out = MenuView();
    if (!menu) return false;
    if (!readU32(m, menu + kPartnerRef, out.partnerRef) || !readU32(m, kSpokenInfoGlobal, out.spokenInfo) || !readU32(m, menu + kState, out.state))
        return false;
    uint8_t b = 0;
    if (!readU8(m, menu + kIsBarterShown, b)) return false;
    out.barterShown = b != 0;
    Addr32 lb = 0;
    if (!readU32(m, menu + kListBox, lb)) return false;
    if (!lb) return true;                                   // menu still opening: no list yet
    Addr32 node = 0;
    if (!readU32(m, lb + list_box::kHead, node)) return false;
    for (uint32_t guard = 0; node; ++guard) {
        if (guard >= list_box::kMaxNodes) return false;     // cyclic or runaway list: do not trust it
        Addr32 item = 0, next = 0;
        if (!readU32(m, node + list_box::kNodeItem, item) || !readU32(m, node + list_box::kNodeNext, next)) return false;
        if (item) {
            MenuItem it;
            if (!readU32(m, item + list_box::kItemInfo, it.info) || !readU32(m, item + list_box::kItemTopic, it.topic)) return false;
            if (it.info) {
                out.items.push_back(it);
                out.offered.push_back(it.info);
            }
        }
        node = next;
    }
    return true;
}

bool readInfoFlags(const IMemory& m, Addr32 info, InfoFlags& out) {
    using namespace topic_info;
    out = InfoFlags();
    if (!info) return false;
    bool ok = readU8(m, info + kSaidOnce, out.saidOnce) && readU8(m, info + kType, out.type) &&
              readU8(m, info + kFlags1, out.flags1) && readU8(m, info + kFlags2, out.flags2) &&
              readU32(m, info + kSpeechChallenge, out.speechChallenge) &&
              readU32(m, info + kActorValueOrPerk, out.actorValueOrPerk) && readString(m, info + kPrompt, out.prompt);
    out.goodbye = (out.flags1 & kFlagGoodbye) != 0;
    out.sayOnceFlag = (out.flags1 & kFlagSayOnce) != 0;
    return ok;
}

static bool looksLikeResponse(const IMemory& m, Addr32 p) {
    using namespace response;
    if (p < 0x10000) return false;
    uint32_t emo = 0, emoVal = 0, next = 0;
    uint8_t flags = 0;
    if (!readU32(m, p + kEmotionType, emo) || !readU32(m, p + kEmotionValue, emoVal) ||
        !m.read(p + kFlags, &flags, 1) || !readU32(m, p + kNext, next))
        return false;
    if (emo > 16 || emoVal > 100 || !(flags & 1)) return false;
    std::string s;
    return readString(m, p + kText, s);
}

int32_t probeResponseOffset(const IMemory& m, Addr32 info, const std::string& expectText) {
    for (uint32_t off = 0; off + 4 <= topic_info::kSize; off += 4) {
        uint32_t p = 0;
        if (!readU32(m, info + off, p) || !looksLikeResponse(m, p)) continue;
        std::string s;
        readString(m, p + response::kText, s);
        if (!expectText.empty() && s.compare(0, expectText.size(), expectText) != 0) continue;
        return int32_t(off);
    }
    return -1;
}

namespace {
ResponseLoader g_loader;
ReaderStats g_stats;

// Walks a response chain starting at `p`.
bool walkChain(const IMemory& m, Addr32 p, std::vector<fnvcoop_dlg::Line>& out) {
    out.clear();
    for (int guard = 0; p && guard < int(fnvcoop_dlg::kMaxLines); ++guard) {
        fnvcoop_dlg::Line l;
        uint32_t emo = 0, emoVal = 0, next = 0;
        if (!looksLikeResponse(m, p) || !readU32(m, p + response::kEmotionType, emo) ||
            !readU32(m, p + response::kEmotionValue, emoVal) || !readString(m, p + response::kText, l.text) ||
            !readU32(m, p + response::kNext, next)) {
            out.clear();
            return false;
        }
        l.emotion = uint8_t(emo);
        l.emotionValue = uint8_t(emoVal);
        out.push_back(std::move(l));
        p = next;
    }
    return true;
}
}  // namespace

void setResponseLoader(ResponseLoader f) { g_loader = std::move(f); }
const ResponseLoader& responseLoader() { return g_loader; }
ReaderStats& readerStats() { return g_stats; }

bool responseCacheHolds(const IMemory& m, Addr32 info) {
    uint32_t cached = 0, head = 0;
    if (!info || !readU32(m, response_cache::kCachedInfoGlobal, cached) || cached != info) return false;
    return readU32(m, response_cache::kCell, head) && head != 0;
}

bool readCachedLines(const IMemory& m, Addr32 info, std::vector<fnvcoop_dlg::Line>& out) {
    out.clear();
    // MUTANT (dlgReadWrongInfo): trust whatever the cache holds, even when it is another INFO's chain
    if (!fnvcoop_dlg::mutations().dlgReadWrongInfo && !responseCacheHolds(m, info)) return false;
    uint32_t head = 0;
    if (!readU32(m, response_cache::kCell, head)) return false;
    return walkChain(m, head, out);
}

bool readLines(const IMemory& m, Addr32 info, const ResponseLayout& layout, std::vector<fnvcoop_dlg::Line>& out) {
    out.clear();
    if (!info) return false;
    if (layout.useEngineCache) {
        if (readCachedLines(m, info, out)) { ++g_stats.cacheHits; return true; }
        if (g_loader) {
            if (g_loader(info)) {
                ++g_stats.loaderCalls;
                if (readCachedLines(m, info, out)) return true;
            } else {
                ++g_stats.loaderRefused;
            }
        }
    }
    if (layout.headOffset >= 0) {
        uint32_t p = 0;
        if (readU32(m, info + uint32_t(layout.headOffset), p) && walkChain(m, p, out)) { ++g_stats.legacyReads; return true; }
    }
    ++g_stats.failed;
    out.clear();
    return false;
}

std::string voiceFile(const std::string& pluginFile, const std::string& voiceType, const std::string& topicEdid,
                      uint32_t infoLocalId, uint32_t responseNumber) {
    char id[16];
    std::snprintf(id, sizeof id, "%08x", infoLocalId);
    std::string s = pluginFile + "\\" + voiceType + "\\" + topicEdid + "_" + id + "_" + std::to_string(responseNumber) + ".ogg";
    std::transform(s.begin(), s.end(), s.begin(), [](unsigned char c) { return char(std::tolower(c)); });
    return s;
}

std::function<std::vector<fnvcoop_dlg::Line>(uint32_t)> makeInfoReader(const IMemory& mem, const ResponseLayout* layout) {
    return [&mem, layout](uint32_t info) {
        std::vector<fnvcoop_dlg::Line> lines;
        const ResponseLayout def;
        if (!readLines(mem, info, layout ? *layout : def, lines)) lines.clear();
        return lines;
    };
}

}  // namespace fndlg
