#include "hostfilter.h"

#include <algorithm>
#include <cctype>

namespace nvmp::hook {

namespace {
std::string_view Trim(std::string_view s) {
    while (!s.empty() && std::isspace(static_cast<unsigned char>(s.front()))) s.remove_prefix(1);
    while (!s.empty() && std::isspace(static_cast<unsigned char>(s.back()))) s.remove_suffix(1);
    return s;
}
int Hex(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}
std::string Lower(std::string_view s) {
    std::string r(s);
    std::transform(r.begin(), r.end(), r.begin(), [](unsigned char c) { return (char)std::tolower(c); });
    return r;
}
bool ParseBool(std::string_view v, bool& out) {
    std::string l = Lower(v);
    if (l == "1" || l == "true" || l == "yes" || l == "on") { out = true; return true; }
    if (l == "0" || l == "false" || l == "no" || l == "off") { out = false; return true; }
    return false;
}
}  // namespace

int LoadOrder::IndexOf(std::string_view name) const {
    std::string l = Lower(Trim(name));
    for (size_t i = 0; i < plugins.size(); ++i)
        if (Lower(plugins[i]) == l) return (int)i;
    return -1;
}

static bool HasPluginExt(std::string_view n) {
    std::string l = Lower(n);
    auto ends = [&](const char* e) { size_t k = std::char_traits<char>::length(e); return l.size() > k && l.compare(l.size() - k, k, e) == 0; };
    return ends(".esm") || ends(".esp") || ends(".esl");
}

LoadOrder ParseLoadOrder(std::string_view text, std::string source) {
    LoadOrder lo;
    lo.source = std::move(source);
    lo.plugins.push_back("FalloutNV.esm");  // always index 0
    size_t pos = 0;
    while (pos <= text.size()) {
        size_t nl = text.find('\n', pos);
        std::string_view line = text.substr(pos, nl == std::string_view::npos ? std::string_view::npos : nl - pos);
        pos = nl == std::string_view::npos ? text.size() + 1 : nl + 1;
        if (line.size() >= 3 && (unsigned char)line[0] == 0xEF && (unsigned char)line[1] == 0xBB && (unsigned char)line[2] == 0xBF)
            line.remove_prefix(3);
        size_t c = line.find('#');
        if (c != std::string_view::npos) line = line.substr(0, c);
        line = Trim(line);
        while (!line.empty() && line.front() == '*') line.remove_prefix(1);
        line = Trim(line);
        if (line.empty() || !HasPluginExt(line)) continue;
        if (lo.IndexOf(line) >= 0) continue;
        lo.plugins.emplace_back(line);
    }
    return lo;
}

LoadOrder CanonicalAssumedOrder() {
    LoadOrder lo;
    lo.source = "canonical default (assumed)";
    lo.guessed = true;
    lo.plugins = {"FalloutNV.esm", "DeadMoney.esm", "HonestHearts.esm", "OldWorldBlues.esm", "LonesomeRoad.esm",
                  "TribalPack.esm", "MercenaryPack.esm", "ClassicPack.esm", "CaravanPack.esm", "GunRunnersArsenal.esm"};
    return lo;
}

namespace {
// "# load order: 00=FalloutNV.esm, 01=DeadMoney.esm, ..." => vector indexed by mod index
std::vector<std::string> ParseHeaderLoadOrder(std::string_view line) {
    std::vector<std::string> out;
    std::string l = Lower(line);
    size_t k = l.find("load order:");
    if (k == std::string::npos) return out;
    std::string_view rest = line.substr(k + 11);
    size_t pos = 0;
    while (pos < rest.size()) {
        size_t comma = rest.find(',', pos);
        std::string_view item = Trim(rest.substr(pos, comma == std::string_view::npos ? std::string_view::npos : comma - pos));
        pos = comma == std::string_view::npos ? rest.size() : comma + 1;
        size_t eq = item.find('=');
        if (eq == std::string_view::npos) continue;
        std::string_view idx = Trim(item.substr(0, eq)), name = Trim(item.substr(eq + 1));
        unsigned v = 0;
        bool ok = !idx.empty() && idx.size() <= 2;
        for (char ch : idx) { int h = Hex(ch); if (h < 0) { ok = false; break; } v = v * 16 + (unsigned)h; }
        if (!ok || name.empty() || v > 255) continue;
        if (out.size() <= v) out.resize(v + 1);
        out[v] = std::string(name);
    }
    return out;
}
}  // namespace

HostOwnedSet ParseHostOwned(std::string_view text, const LoadOrder* actual) {
    HostOwnedSet r;
    std::vector<std::string> headerOrder;  // plugin per mod index, from the file's own header comment
    std::unordered_set<std::string> warnedPlugins;
    size_t lineNo = 0;
    size_t pos = 0;
    while (pos <= text.size()) {
        size_t nl = text.find('\n', pos);
        std::string_view line = text.substr(pos, nl == std::string_view::npos ? std::string_view::npos : nl - pos);
        pos = nl == std::string_view::npos ? text.size() + 1 : nl + 1;
        ++lineNo;
        if (lineNo == 1 && line.size() >= 3 && (unsigned char)line[0] == 0xEF && (unsigned char)line[1] == 0xBB &&
            (unsigned char)line[2] == 0xBF)
            line.remove_prefix(3);  // UTF-8 BOM
        line = Trim(line);
        if (line.empty()) continue;
        if (line[0] == '#' || line[0] == ';') {
            auto h = ParseHeaderLoadOrder(line);
            if (!h.empty()) headerOrder = std::move(h);
            continue;
        }
        ++r.lines;
        size_t c = line.find_first_of("#;");
        std::string_view tok = Trim(c == std::string_view::npos ? line : line.substr(0, c));
        auto reject = [&]() {
            ++r.rejected;
            if (r.errors.size() < 8) r.errors.push_back("line " + std::to_string(lineNo) + ": " + std::string(line));
        };

        // "Plugin.esm:LOCALID"
        size_t colon = tok.find(':');
        if (colon != std::string_view::npos) {
            std::string_view pl = Trim(tok.substr(0, colon)), loc = Trim(tok.substr(colon + 1));
            if (loc.size() > 2 && loc[0] == '0' && (loc[1] == 'x' || loc[1] == 'X')) loc.remove_prefix(2);
            bool ok = HasPluginExt(pl) && !loc.empty() && loc.size() <= 8;
            uint32_t local = 0;
            for (char ch : loc) { int h = Hex(ch); if (h < 0) { ok = false; break; } local = (local << 4) | (uint32_t)h; }
            local &= 0x00FFFFFF;
            if (!ok || local == 0) { reject(); continue; }
            if (!actual) { ++r.unresolved; if (warnedPlugins.insert("*").second) r.warnings.push_back("named entries (Plugin.esm:ID) need a load order, none available: they are ignored"); continue; }
            int idx = actual->IndexOf(pl);
            if (idx < 0) {
                ++r.unresolved;
                if (warnedPlugins.insert(Lower(pl)).second)
                    r.warnings.push_back("plugin '" + std::string(pl) + "' is not in the server load order (" + actual->source + "): its entries are skipped");
                continue;
            }
            r.ids.insert(((uint32_t)idx << 24) | local);
            ++r.named;
            continue;
        }

        // plain hex runtime FormID
        if (tok.size() > 2 && tok[0] == '0' && (tok[1] == 'x' || tok[1] == 'X')) tok.remove_prefix(2);
        bool ok = !tok.empty() && tok.size() <= 8;
        uint32_t v = 0;
        for (char ch : tok) {
            int h = Hex(ch);
            if (h < 0) { ok = false; break; }
            v = (v << 4) | (uint32_t)h;
        }
        if (ok && v == 0) ok = false;
        if (!ok) { reject(); continue; }
        if (actual) {
            const uint32_t hi = v >> 24;
            if (hi < headerOrder.size() && !headerOrder[hi].empty()) {
                // re-resolve through the plugin the file itself says this index meant
                int idx = actual->IndexOf(headerOrder[hi]);
                if (idx < 0) {
                    ++r.unresolved;
                    if (warnedPlugins.insert(Lower(headerOrder[hi])).second)
                        r.warnings.push_back("plugin '" + headerOrder[hi] + "' (mod index " + std::to_string(hi) + " in the file header) is not in the server load order (" + actual->source + "): its entries are skipped");
                    continue;
                }
                if ((uint32_t)idx != hi) ++r.remapped;
                v = ((uint32_t)idx << 24) | (v & 0x00FFFFFF);
            } else if (hi >= actual->plugins.size()) {
                ++r.badIndex;
                if (warnedPlugins.insert("idx" + std::to_string(hi)).second)
                    r.warnings.push_back("FormID " + std::string(tok) + ": mod index " + std::to_string(hi) + " is not a loaded plugin (server has " + std::to_string(actual->plugins.size()) + ", " + actual->source + ")");
            }
        }
        r.ids.insert(v);
    }
    if (actual && r.remapped)
        r.warnings.push_back(std::to_string(r.remapped) + " plain-hex FormIDs were re-mapped because the server load order differs from the one assumed in the file header");
    if (actual && actual->guessed)
        r.warnings.push_back("server load order is a GUESS (" + actual->source + "): provide hostowned_loadorder.txt or a plugins.txt next to the server if indices look wrong");
    return r;
}

HookConfig ParseConfig(std::string_view text, std::vector<std::string>* warnings) {
    HookConfig c;
    auto warn = [&](const std::string& m) { if (warnings) warnings->push_back(m); };
    size_t pos = 0;
    while (pos <= text.size()) {
        size_t nl = text.find('\n', pos);
        std::string_view line = text.substr(pos, nl == std::string_view::npos ? std::string_view::npos : nl - pos);
        pos = nl == std::string_view::npos ? text.size() + 1 : nl + 1;
        line = Trim(line);
        if (line.empty() || line[0] == '#' || line[0] == ';') continue;
        size_t eq = line.find('=');
        if (eq == std::string_view::npos) { warn("no '=' in: " + std::string(line)); continue; }
        std::string key = Lower(Trim(line.substr(0, eq)));
        std::string_view val = Trim(line.substr(eq + 1));
        size_t cm = val.find('#');
        if (cm != std::string_view::npos) val = Trim(val.substr(0, cm));
        bool b = false;
        auto setb = [&](bool& dst) {
            if (ParseBool(val, b)) dst = b; else warn("bad boolean for " + key + ": " + std::string(val));
        };
        if (key == "enabled") setb(c.enabled);
        else if (key == "block_handover") setb(c.blockHandover);
        else if (key == "drop_actors") setb(c.dropActors);
        else if (key == "drop_references") setb(c.dropReferences);
        else if (key == "log_all") setb(c.logAll);
        else if (key == "host_mode") {
            std::string l = Lower(val);
            if (l == "auto") c.mode = HostMode::Auto;
            else if (l == "loopback") c.mode = HostMode::Loopback;
            else if (l == "first") c.mode = HostMode::First;
            else warn("bad host_mode: " + std::string(val));
        } else if (key == "host_address") {
            c.hostAddresses.emplace_back(val);
        } else if (key == "poll_ms") {
            unsigned long v = 0;
            bool ok = !val.empty();
            for (char ch : val) { if (!std::isdigit((unsigned char)ch)) { ok = false; break; } v = v * 10 + (ch - '0'); if (v > 600000) break; }
            if (ok && v >= 50 && v <= 600000) c.pollMs = (unsigned)v; else warn("bad poll_ms: " + std::string(val));
        } else {
            warn("unknown key: " + key);
        }
    }
    return c;
}

const char* ToString(EncounterVerdict v) {
    switch (v) {
        case EncounterVerdict::AllowDisabled: return "allow(disabled)";
        case EncounterVerdict::AllowNotFlagged: return "allow(not-flagged)";
        case EncounterVerdict::AllowTypeSkipped: return "allow(type-skipped)";
        case EncounterVerdict::AllowHostSender: return "allow(host-sender)";
        case EncounterVerdict::AllowHostUnknown: return "allow(host-unknown)";
        case EncounterVerdict::DropGuest: return "DROP(guest-encounter)";
    }
    return "?";
}
const char* ToString(HandoverVerdict v) {
    switch (v) {
        case HandoverVerdict::AllowDisabled: return "allow(disabled)";
        case HandoverVerdict::AllowNotFlagged: return "allow(not-flagged)";
        case HandoverVerdict::AllowHostAbsent: return "allow(host-absent)";
        case HandoverVerdict::Block: return "BLOCK(host-owned)";
    }
    return "?";
}

EncounterVerdict DecideEncounter(const HostOwnedSet& set, const HookConfig& cfg, uint32_t formId, uint32_t type,
                                 SenderKind sender) {
    if (!cfg.enabled) return EncounterVerdict::AllowDisabled;
    if (!set.Contains(formId)) return EncounterVerdict::AllowNotFlagged;
    const bool isActor = type == 0;
    if ((isActor && !cfg.dropActors) || (!isActor && !cfg.dropReferences)) return EncounterVerdict::AllowTypeSkipped;
#ifdef HOOK_MUTANT_INVERT_FILTER
    // mutation twin: host/guest swapped
    if (sender == SenderKind::Guest) return EncounterVerdict::AllowHostSender;
    if (sender == SenderKind::Unknown) return EncounterVerdict::AllowHostUnknown;
    return EncounterVerdict::DropGuest;
#else
    if (sender == SenderKind::Host) return EncounterVerdict::AllowHostSender;
    if (sender == SenderKind::Unknown) return EncounterVerdict::AllowHostUnknown;
    return EncounterVerdict::DropGuest;
#endif
}

HandoverVerdict DecideHandover(const HostOwnedSet& set, const HookConfig& cfg, uint32_t formId, bool hostPresent) {
    if (!cfg.enabled || !cfg.blockHandover) return HandoverVerdict::AllowDisabled;
    if (!set.Contains(formId)) return HandoverVerdict::AllowNotFlagged;
    if (!hostPresent) return HandoverVerdict::AllowHostAbsent;
    return HandoverVerdict::Block;
}

bool IsLoopbackAddress(std::string_view a) {
    std::string s = Lower(Trim(a));
    // strip a trailing :port for IPv4 "a.b.c.d:port" (IPv6 is left alone)
    if (std::count(s.begin(), s.end(), ':') == 1) s = s.substr(0, s.find(':'));
    if (s == "localhost" || s == "::1" || s == "[::1]") return true;
    if (s.rfind("::ffff:", 0) == 0) s = s.substr(7);
    if (s.rfind("127.", 0) == 0) {
        // 127.x.x.x with 4 numeric parts
        int parts = 1;
        for (char ch : s) {
            if (ch == '.') ++parts;
            else if (!std::isdigit((unsigned char)ch)) return false;
        }
        return parts == 4;
    }
    return false;
}

void PeerTable::Add(const PeerInfo& p) {
    for (auto& e : peers_)
        if (e.ptr == p.ptr) { e = p; return; }
    peers_.push_back(p);
}
void PeerTable::Remove(uintptr_t ptr) {
    peers_.erase(std::remove_if(peers_.begin(), peers_.end(), [&](const PeerInfo& e) { return e.ptr == ptr; }),
                 peers_.end());
}
const PeerInfo* PeerTable::Find(const SenderRef& s) const {
    for (const auto& e : peers_)
        if (s.ptr && e.ptr == s.ptr) return &e;
    for (const auto& e : peers_)
        if (s.key && (e.key0 == s.key || e.key1 == s.key)) return &e;
    return nullptr;
}

HostResolution ResolveSender(const PeerTable& peers, const SenderRef& sender, uint32_t firstCharKey, HostMode mode) {
    if (mode != HostMode::First) {
        if (const PeerInfo* p = peers.Find(sender))
            return {p->loopback ? SenderKind::Host : SenderKind::Guest, p->loopback ? "peer-table:loopback" : "peer-table:remote"};
        if (mode == HostMode::Loopback) return {SenderKind::Unknown, "peer-table:sender-unknown"};
        if (peers.HasLoopback()) return {SenderKind::Unknown, "peer-table:sender-unknown(host-known)"};
    }
    if (firstCharKey == 0) return {SenderKind::Unknown, "no-characters"};
    if (sender.key == 0) return {SenderKind::Unknown, "sender-key-unreadable"};
    return {sender.key == firstCharKey ? SenderKind::Host : SenderKind::Guest, "first-character"};
}

bool IsHostPresent(const PeerTable& peers, const std::vector<uint32_t>& connectedCharKeys, uint32_t firstCharKey,
                   HostMode mode) {
    if (mode != HostMode::First) {
        for (uint32_t k : connectedCharKeys) {
            SenderRef s;
            s.key = k;
            const PeerInfo* p = peers.Find(s);
            if (p && p->loopback) return true;
        }
        if (mode == HostMode::Loopback || peers.HasLoopback()) return false;
    }
    if (firstCharKey == 0) return false;
    return std::find(connectedCharKeys.begin(), connectedCharKeys.end(), firstCharKey) != connectedCharKeys.end();
}

HostOwnedStore::HostOwnedStore() : cur_(std::make_shared<const HostOwnedSet>()) {}
std::shared_ptr<const HostOwnedSet> HostOwnedStore::Get() const {
    std::lock_guard<std::mutex> g(mu_);
    return cur_;
}
void HostOwnedStore::Set(HostOwnedSet s) {
    auto p = std::make_shared<const HostOwnedSet>(std::move(s));
    std::lock_guard<std::mutex> g(mu_);
    cur_ = std::move(p);
    ++reloads_;
}

}  // namespace nvmp::hook
