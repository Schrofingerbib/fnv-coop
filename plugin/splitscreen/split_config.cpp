#include "split_config.h"

#include <cctype>
#include <cstdlib>
#include <sstream>

#include "fnvcoop_mut.h"

// Runtime mutation twins (FNVCOOP_MUT=<n>, tools/mut/fnvcoop_mut.h), numbered after the focus guard twins 1..7:
//   8  = an invalid file still leaves enable on (a bad value would route input by accident)
//   9  = pad=none parses as pad 0 (the copy would grab player one's pad)
//   10 = key lookup is case-sensitive
//   11 = the defaults ignore the seat (player 3 would get KB/M and sound)
// (the pad router twins start at 12, see pad_router.cpp)
namespace nvsplit {
namespace {
std::string Trim(std::string s) {
    size_t a = 0, b = s.size();
    while (a < b && std::isspace(static_cast<unsigned char>(s[a]))) ++a;
    while (b > a && std::isspace(static_cast<unsigned char>(s[b - 1]))) --b;
    return s.substr(a, b - a);
}
std::string Lower(std::string s) {
    for (char& c : s) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    return s;
}
bool ParseInt(const std::string& v, int lo, int hi, int* out) {
    if (v.empty()) return false;
    char* end = nullptr;
    const long n = std::strtol(v.c_str(), &end, 10);
    if (*end != '\0' || n < lo || n > hi) return false;
    *out = static_cast<int>(n);
    return true;
}
bool ParseBool(const std::string& v, bool* out) {
    const std::string l = Lower(v);
    if (l == "1" || l == "true" || l == "yes" || l == "on") { *out = true; return true; }
    if (l == "0" || l == "false" || l == "no" || l == "off") { *out = false; return true; }
    return false;
}
}  // namespace

bool ParseConfig(const std::string& iniText, Config* out, std::string* err) {
    Config c;
    bool haveKbm = false, haveGhost = false, haveAudio = false;
    std::string section, error;
    std::istringstream in(iniText);
    std::string line;
    int n = 0;
    auto bad = [&](const std::string& m) {
        if (error.empty()) error = "nvcoop.ini line " + std::to_string(n) + ": " + m;
    };
    while (std::getline(in, line)) {
        ++n;
        line = Trim(line);
        if (line.empty() || line[0] == ';' || line[0] == '#') continue;
        if (line[0] == '[') {
            const size_t e = line.find(']');
            section = Lower(Trim(e == std::string::npos ? line.substr(1) : line.substr(1, e - 1)));
            continue;
        }
        if (section != "splitscreen") continue;
        const size_t eq = line.find('=');
        if (eq == std::string::npos) continue;
        std::string k = Trim(line.substr(0, eq));
        if (!FNV_MUT(10)) k = Lower(k);
        const std::string v = Trim(line.substr(eq + 1));
        int i = 0;
        bool b = false;
        if (k == "enable") { if (!ParseBool(v, &c.enable)) bad("enable must be 0 or 1"); }
        else if (k == "player") { if (!ParseInt(v, 0, 3, &i)) bad("player must be 0..3"); else c.player = i; }
        else if (k == "players") { if (!ParseInt(v, 1, 4, &i)) bad("players must be 1..4"); else c.players = i; }
        else if (k == "pad") {
            const std::string l = Lower(v);
            if (l == "none" || l == "-1") c.pad = FNV_MUT(9) ? 0 : -1;
            else if (ParseInt(v, 0, 3, &i)) c.pad = i;
            else bad("pad must be 0..3 or none");
        }
        else if (k == "kbm") { if (!ParseBool(v, &b)) bad("kbm must be 0 or 1"); else { c.kbm = b; haveKbm = true; } }
        else if (k == "ghostpad") { if (!ParseBool(v, &b)) bad("ghostPad must be 0 or 1"); else { c.ghostPad = b; haveGhost = true; } }
        else if (k == "audio") {
            const std::string l = Lower(v);
            if (l == "play") { c.audio = AudioMode::Play; haveAudio = true; }
            else if (l == "mute") { c.audio = AudioMode::Mute; haveAudio = true; }
            else bad("audio must be play or mute");
        }
        else if (k == "focusfake") { if (!ParseBool(v, &c.focusFake)) bad("focusFake must be 0 or 1"); }
        else if (k == "devinject") { if (!ParseBool(v, &c.devInject)) bad("devInject must be 0 or 1"); }
    }
    if (error.empty() && c.player >= c.players) error = "player must be smaller than players";
    if (!error.empty()) {
        Config off;
        off.enable = FNV_MUT(8) ? c.enable : false;
        *out = off;
        if (err) *err = error;
        return false;
    }
    const int seat = FNV_MUT(11) ? 0 : c.player;
    if (!haveKbm) c.kbm = seat == 0;
    if (!haveGhost) c.ghostPad = !c.kbm;
    if (!haveAudio) c.audio = seat == 0 ? AudioMode::Play : AudioMode::Mute;
    *out = c;
    return true;
}

}  // namespace nvsplit
