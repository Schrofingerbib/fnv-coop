// Point-of-no-return message boxes (VEndGameWarningMsg*): which MESG forms need party consent and which buttons mean what.
// File written by tools/gen_consent_messages.py: "<plugin>:<HEXID> <edid> confirm=<n> decline=<n> buttons=a|b".
#pragma once
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
        m.confirmButton = std::stoi(a.substr(8));
        m.declineChoice = std::stoi(b.substr(8));
        out[fnvcoop::FormRef(plugin, uint32_t(std::stoul(ref.substr(c + 1), nullptr, 16)))] = m;
    }
    return out;
}

}  // namespace fnvcoop_dlg
