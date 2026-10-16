#include "info_reader.h"
#include <algorithm>
#include <cctype>
#include <cstdio>

namespace fndlg {

bool readMenu(const IMemory& m, Addr32 menu, MenuView& out) {
    using namespace dialog_menu;
    out = MenuView();
    if (!menu) return false;
    if (!readU32(m, menu + kPartnerRef, out.partnerRef) || !readU32(m, menu + kInfo078, out.spokenInfo)) return false;
    uint8_t b = 0;
    if (!readU8(m, menu + kIsBarterShown, b)) return false;
    out.barterShown = b != 0;
    struct { uint32_t off, n; } arrays[] = {{kInfos094, kInfos094Count}, {kInfos0E4, kInfos0E4Count}, {kInfos110, kInfos110Count}};
    for (auto& a : arrays) {
        for (uint32_t i = 0; i < a.n; ++i) {
            uint32_t p = 0;
            if (!readU32(m, menu + a.off + 4 * i, p)) return false;
            if (p) out.offered.push_back(p);
        }
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

bool readLines(const IMemory& m, Addr32 info, const ResponseLayout& layout, std::vector<fnvcoop_dlg::Line>& out) {
    out.clear();
    if (layout.headOffset < 0) return false;
    uint32_t p = 0;
    if (!readU32(m, info + uint32_t(layout.headOffset), p)) return false;
    for (int guard = 0; p && guard < int(fnvcoop_dlg::kMaxLines); ++guard) {
        fnvcoop_dlg::Line l;
        uint32_t emo = 0, emoVal = 0, next = 0;
        if (!looksLikeResponse(m, p) || !readU32(m, p + response::kEmotionType, emo) ||
            !readU32(m, p + response::kEmotionValue, emoVal) || !readString(m, p + response::kText, l.text) ||
            !readU32(m, p + response::kNext, next))
            return false;
        l.emotion = uint8_t(emo);
        l.emotionValue = uint8_t(emoVal);
        out.push_back(std::move(l));
        p = next;
    }
    return true;
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
        if (!layout || !readLines(mem, info, *layout, lines)) lines.clear();
        return lines;
    };
}

}  // namespace fndlg
