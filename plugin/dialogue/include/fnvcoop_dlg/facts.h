// Which player facts an INFO reads (L8). Game-free, header-only. The table comes from data/info_facts.txt
// (plugin/dialogue/tools/gen_player_data.py: the INFO's conditions that run on the player plus its result scripts' player.GetItemCount /
// HasPerk / GetEquipped reads). The initiator's machine evaluates each entry for ITS player and ships the values in Pick.facts, so the
// host can answer the same reads in the result script as the initiator instead of as the host player (dialogue-design.md 3.2).
#pragma once
#include <cstdlib>
#include <istream>
#include <map>
#include <sstream>
#include <string>
#include <vector>
#include "fnvcoop_dlg/messages.h"

namespace fnvcoop_dlg {

enum class FactKind : uint8_t { ActorValue = 1, ItemCount = 2, HasPerk = 3, Equipped = 4 };  // Fact::kind values

struct FactSpec {
    FactKind kind;
    FormRef what;   // ActorValue: ("falloutnv.esm", AV code); the others: the item / perk form
    bool operator==(const FactSpec& o) const { return kind == o.kind && what == o.what; }
};

class FactTable {
public:
    void set(const FormRef& info, std::vector<FactSpec> specs) { by_[info] = std::move(specs); }
    size_t size() const { return by_.size(); }
    const std::vector<FactSpec>* find(const FormRef& info) const {
        auto it = by_.find(info);
        return it == by_.end() ? nullptr : &it->second;
    }
    // The wire facts for `info`, capped at kMaxFacts; `eval` computes one value on the local player (false = not readable: skipped).
    template <class Eval>
    std::vector<Fact> collect(const FormRef& info, Eval&& eval) const {
        std::vector<Fact> out;
        const std::vector<FactSpec>* v = find(info);
        if (!v) return out;
        for (const FactSpec& s : *v) {
            int32_t value = 0;
            if (out.size() >= kMaxFacts) break;
            if (!eval(s, value)) continue;
            Fact f;
            f.kind = uint8_t(s.kind);
            f.what = s.what;
            f.value = value;
            out.push_back(std::move(f));
        }
        return out;
    }

private:
    std::map<FormRef, std::vector<FactSpec>> by_;
};

inline bool parseFormRef(const std::string& tok, FormRef& out) {
    size_t c = tok.rfind(':');
    if (c == std::string::npos || c == 0 || c + 1 >= tok.size()) return false;
    std::string plugin = tok.substr(0, c);
    for (auto& ch : plugin) ch = char(tolower(static_cast<unsigned char>(ch)));
    char* end = nullptr;
    unsigned long v = std::strtoul(tok.c_str() + c + 1, &end, 16);
    if (!end || *end) return false;
    out = FormRef(plugin, uint32_t(v));
    return true;
}

// "<plugin>:<HEXID> av=23,item=<plugin>:<HEXID>,perk=...,equip=..." per line.
inline FactTable loadFactTable(std::istream& in) {
    FactTable t;
    std::string line;
    while (std::getline(in, line)) {
        if (line.empty() || line[0] == '#') continue;
        std::istringstream ls(line);
        std::string ref, list;
        FormRef info;
        if (!(ls >> ref >> list) || !parseFormRef(ref, info)) continue;
        std::vector<FactSpec> specs;
        size_t pos = 0;
        while (pos < list.size()) {
            size_t comma = list.find(',', pos);
            std::string item = list.substr(pos, comma == std::string::npos ? std::string::npos : comma - pos);
            pos = comma == std::string::npos ? list.size() : comma + 1;
            size_t eq = item.find('=');
            if (eq == std::string::npos) continue;
            std::string k = item.substr(0, eq), v = item.substr(eq + 1);
            FactSpec s;
            if (k == "av") {
                s.kind = FactKind::ActorValue;
                s.what = FormRef("falloutnv.esm", uint32_t(std::strtoul(v.c_str(), nullptr, 10)));
            } else {
                s.kind = k == "item" ? FactKind::ItemCount : k == "perk" ? FactKind::HasPerk : FactKind::Equipped;
                if ((k != "item" && k != "perk" && k != "equip") || !parseFormRef(v, s.what)) continue;
            }
            specs.push_back(std::move(s));
        }
        if (!specs.empty()) t.set(info, std::move(specs));
    }
    return t;
}

}  // namespace fnvcoop_dlg
