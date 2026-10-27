// Say-once ("already said") state across players (L8, owner decision D11). Game-free, header-only.
//
// The engine marks an INFO as said on the machine that speaks it (TESTopicInfo::MarkSaid 0x61F220, also called by
// RunResultScript on the host for a guest's pick). With initiator-picks that left the other machines out of step: player B could still
// choose a Speech-success line player A already used, and the host player never saw it as said.
// Policy: say-once state is SHARED, so every machine that watches a line (spectators and the host) marks the INFO said, EXCEPT the
// per-player lines in data/sayonce_per_player.txt (Speech-XP and trainer lines), which every player may use once for themselves.
// For a per-player line the host also undoes the mark its own RunResultScript made for the guest's pick, unless its own player
// had already said it.
#pragma once
#include <istream>
#include <set>
#include <sstream>
#include <string>
#include "fnvcoop/types.h"
#include "fnvcoop_dlg/mutations.h"

namespace fnvcoop_dlg {

struct SayOnceAction {
    enum Kind : uint8_t { None, Mark, Clear } kind = None;
};

class SayOncePolicy {
public:
    void setPerPlayer(std::set<fnvcoop::FormRef> s) { perPlayer_ = std::move(s); }
    size_t perPlayerCount() const { return perPlayer_.size(); }
    bool perPlayer(const fnvcoop::FormRef& info) const { return perPlayer_.count(info) != 0; }

    // The local player started a line themselves (their engine marked it): remembered so the host never undoes a mark it owns.
    void noteOwnSaid(const fnvcoop::FormRef& info) { own_.insert(info); }
    bool ownSaid(const fnvcoop::FormRef& info) const { return own_.count(info) != 0; }
    void reset() { own_.clear(); }

    // A line of someone else's conversation was received (spectator / host side). `isHost`: this machine ran the result script.
    SayOnceAction onSpectated(const fnvcoop::FormRef& info, bool isHost) const {
        SayOnceAction a;
        const bool per = perPlayer(info) && !mutations().sayOnceAllShared;
        if (mutations().sayOnceNeverShared) return a;                       // MUTANT: nothing is shared (the old behaviour)
        if (!per) { a.kind = SayOnceAction::Mark; return a; }
        if (isHost && !ownSaid(info) && !mutations().sayOnceKeepHostMark) a.kind = SayOnceAction::Clear;
        return a;
    }

private:
    std::set<fnvcoop::FormRef> perPlayer_, own_;
};

// "speech|trainer <plugin>:<HEXID>" lines (plugin/dialogue/tools/gen_player_data.py).
inline std::set<fnvcoop::FormRef> loadSayOncePerPlayer(std::istream& in) {
    std::set<fnvcoop::FormRef> s;
    std::string line;
    while (std::getline(in, line)) {
        if (line.empty() || line[0] == '#') continue;
        std::istringstream ls(line);
        std::string kind, ref;
        if (!(ls >> kind >> ref) || (kind != "speech" && kind != "trainer")) continue;
        size_t c = ref.rfind(':');
        if (c == std::string::npos) continue;
        std::string plugin = ref.substr(0, c);
        for (auto& ch : plugin) ch = char(tolower(static_cast<unsigned char>(ch)));
        s.insert(fnvcoop::FormRef(plugin, uint32_t(std::stoul(ref.substr(c + 1), nullptr, 16))));
    }
    return s;
}

}  // namespace fnvcoop_dlg
