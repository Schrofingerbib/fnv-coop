// Irreversible choices that need the party (L8, D3): which MESG boxes and which fork INFOs, and how each is decided.
// File written by tools/gen_consent_messages.py, one row per form:
//   <plugin>:<HEXID> <edid> confirm=<n|*> decline=<n> [mode=unanimous|vote|actor] [timeout=<seconds>] [kind=mesg|info] buttons=a|b
// confirm=* means every button except `decline` is the irreversible one. mode defaults to unanimous (a point of no return: every other
// player must say "Ready", 120 s window), vote = a story fork (party majority, the host breaks ties), actor = a personal decision
// (the player who acts decides alone, nobody is asked). kind=info rows are fork INFOs held at the pick (HostDialogue::setChoiceRules).
#pragma once
#include <cstdlib>
#include <istream>
#include <map>
#include <sstream>
#include <string>
#include "fnvcoop/types.h"

namespace fnvcoop_dlg {

struct ConsentMessage {
    int32_t confirmButton = 0;
    int32_t declineChoice = -1;
    std::string edid;
    uint8_t mode = 0;          // 0 unanimous, 1 vote, 2 actor (HostDialogue::ConsentMode)
    uint32_t timeoutMs = 0;    // 0 = the host's message-menu default
    bool anyButton = false;    // confirm=*
    bool isInfo = false;       // kind=info
};

inline std::map<fnvcoop::FormRef, ConsentMessage> loadConsentMessages(std::istream& in) {
    std::map<fnvcoop::FormRef, ConsentMessage> out;
    std::string line;
    while (std::getline(in, line)) {
        if (line.empty() || line[0] == '#') continue;
        std::istringstream ls(line);
        std::string ref, edid, a, b;
        if (!(ls >> ref >> edid >> a >> b)) continue;
        size_t c = ref.rfind(':');
        if (c == std::string::npos || a.rfind("confirm=", 0) != 0 || b.rfind("decline=", 0) != 0) continue;
        std::string plugin = ref.substr(0, c);
        for (auto& ch : plugin) ch = char(tolower(static_cast<unsigned char>(ch)));
        ConsentMessage m;
        m.edid = edid;
        const std::string conf = a.substr(8);
        if (conf == "*") { m.anyButton = true; m.confirmButton = -1; } else m.confirmButton = std::stoi(conf);
        m.declineChoice = std::stoi(b.substr(8));
        std::string tok;
        while (ls >> tok) {                                   // optional fields come before buttons= (whose text may contain spaces)
            if (tok.rfind("buttons=", 0) == 0) break;
            if (tok.rfind("mode=", 0) == 0) {
                const std::string v = tok.substr(5);
                m.mode = v == "vote" ? 1 : v == "actor" ? 2 : 0;
            } else if (tok.rfind("timeout=", 0) == 0) {
                m.timeoutMs = uint32_t(std::strtoul(tok.c_str() + 8, nullptr, 10) * 1000u);
            } else if (tok == "kind=info") {
                m.isInfo = true;
            }
        }
        out[fnvcoop::FormRef(plugin, uint32_t(std::stoul(ref.substr(c + 1), nullptr, 16)))] = m;
    }
    return out;
}

}  // namespace fnvcoop_dlg
