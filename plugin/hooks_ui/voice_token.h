// L11 hooks_ui: the "voice file" string L8 ships with every Line. FNV runtime forms carry no editor ids for topics, so the real voice
// path (Sound\Voice\<plugin>\<voicetype>\<topicEdid>_<info>_<n>.ogg) cannot be built reliably. The token names the topic + INFO instead
// and the receiving machine plays it with `<npc>.Say <topic>` (voice and lip sync come from the engine). Game-free so it is unit tested.
#pragma once
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <string>
#include <vector>

#include "fnvcoop/types.h"

namespace nvui {

struct VoiceToken {
    fnvcoop::FormRef topic;
    fnvcoop::FormRef info;
    uint32_t response = 1;
};

inline std::string makeVoiceToken(const fnvcoop::FormRef& topic, const fnvcoop::FormRef& info, uint32_t response) {
    char b[64];
    std::snprintf(b, sizeof b, "|%X|%X|%u", topic.localId, info.localId, response);
    return "nvsay|" + topic.plugin + "|" + info.plugin + b;  // nvsay|<topic plugin>|<info plugin>|<topic hex>|<info hex>|<n>
}

inline bool parseVoiceToken(const std::string& s, VoiceToken& out) {
    std::vector<std::string> f;
    size_t from = 0;
    for (;;) {
        const size_t q = s.find('|', from);
        f.push_back(s.substr(from, q == std::string::npos ? std::string::npos : q - from));
        if (q == std::string::npos) break;
        from = q + 1;
    }
    if (f.size() != 6 || f[0] != "nvsay") return false;
    for (size_t i = 1; i < 6; ++i)
        if (f[i].empty()) return false;
    char* e = nullptr;
    const unsigned long t = std::strtoul(f[3].c_str(), &e, 16);
    if (*e || t > 0xFFFFFF) return false;
    const unsigned long i = std::strtoul(f[4].c_str(), &e, 16);
    if (*e || i > 0xFFFFFF) return false;
    const unsigned long r = std::strtoul(f[5].c_str(), &e, 10);
    if (*e || r == 0 || r > 255) return false;
    out.topic = fnvcoop::FormRef(f[1], uint32_t(t));
    out.info = fnvcoop::FormRef(f[2], uint32_t(i));
    out.response = uint32_t(r);
    return true;
}

}  // namespace nvui
