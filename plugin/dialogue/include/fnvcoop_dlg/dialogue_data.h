// The data tables the guest-dialogue module needs (L8), loaded from named text blobs so the same code runs in the game (embedded in the
// DLL, optionally overridden by a file next to it) and in the game-free tests. Header-only.
//
//   companion_topics.txt     owner-only companion command topics        (tools/gen_companion_topics.py)
//   sayonce_per_player.txt   D11: the say-once lines that stay per player (tools/gen_player_data.py)
//   info_facts.txt           the player facts each INFO reads            (tools/gen_player_data.py)
//   follower_rules.txt       follower quest variable rules, G33          (tools/gen_follower_rules.py)
#pragma once
#include <functional>
#include <set>
#include <sstream>
#include <string>
#include "fnvcoop_dlg/adapter.h"
#include "fnvcoop_dlg/companions.h"
#include "fnvcoop_dlg/facts.h"
#include "fnvcoop_dlg/sayonce.h"

namespace fnvcoop_dlg {

// name -> file text; false when the table is not available.
using DataProvider = std::function<bool(const std::string& name, std::string& text)>;

struct DialogueData {
    SayOncePolicy sayOnce;
    FactTable facts;
    std::set<FormRef> ownerOnly;
    std::string followerRulesText;
    struct Report {
        size_t sayOncePerPlayer = 0, facts = 0, ownerOnlyTopics = 0, followerBytes = 0;
        std::string missing;   // names that could not be loaded, comma separated
    } report;
};

inline void loadDialogueData(const DataProvider& get, DialogueData& d) {
    std::string t;
    auto miss = [&](const char* n) { d.report.missing += (d.report.missing.empty() ? "" : ",") + std::string(n); };
    if (get("companion_topics.txt", t)) { std::istringstream in(t); d.ownerOnly = loadOwnerOnlyTopics(in); } else miss("companion_topics.txt");
    if (get("sayonce_per_player.txt", t)) { std::istringstream in(t); d.sayOnce.setPerPlayer(loadSayOncePerPlayer(in)); } else miss("sayonce_per_player.txt");
    if (get("info_facts.txt", t)) { std::istringstream in(t); d.facts = loadFactTable(in); } else miss("info_facts.txt");
    if (get("follower_rules.txt", t)) d.followerRulesText = t; else miss("follower_rules.txt");
    d.report.ownerOnlyTopics = d.ownerOnly.size();
    d.report.sayOncePerPlayer = d.sayOnce.perPlayerCount();
    d.report.facts = d.facts.size();
    d.report.followerBytes = d.followerRulesText.size();
}

// Applies the tables to a freshly built node: owner-only topics (so a non-owner's command pick is rejected by the host and refused by the
// glue) and the follower quest variable rules of the per-owner companion registry (G33). Call once per node.
inline FollowerRulesStats configureNode(DialogueNode& node, const DialogueData& d) {
    node.setOwnerOnlyTopics(d.ownerOnly);
    std::istringstream in(d.followerRulesText);
    return loadFollowerRules(in, node.companions());
}

}  // namespace fnvcoop_dlg
