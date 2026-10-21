#include "wt_save.h"

#include "fnvcoop_mut.h"

namespace nvwt {

const char* saveKindName(SaveKind k) {
    switch (k) {
        case SaveKind::Auto: return "auto";
        case SaveKind::Quick: return "quick";
        case SaveKind::Manual: return "manual";
        case SaveKind::System: return "system";
        case SaveKind::Force: return "force";
        case SaveKind::Unknown: return "unknown";
    }
    return "?";
}

bool engineAutosavesOff(const SaveContext& c) {
    if (!c.inSession) return false;
    if (c.role == Role::Client && !FNV_MUT(146)) return true;   // MUTANT 146: clients autosave (and write a world nobody wants)
    if (c.saveSuppressed && !FNV_MUT(147)) return true;          // MUTANT 147: the engine keeps autosaving through a no-save story moment
    return false;
}

SaveVerdict decideSave(SaveKind kind, const SaveContext& c) {
    if (!c.inSession) return SaveVerdict::Allow;
    const bool automatic = kind == SaveKind::Auto || kind == SaveKind::System || kind == SaveKind::Force || kind == SaveKind::Unknown;
    if (c.saveSuppressed && !FNV_MUT(148)) return automatic ? SaveVerdict::DenyQuiet : SaveVerdict::DenyNotify;   // MUTANT 148: SaveSuppress is ignored
    if (c.role == Role::Client) {
        if (automatic && !FNV_MUT(149)) return SaveVerdict::DenyQuiet;   // MUTANT 149: a client's scripted / automatic save goes through
        if (c.syncing && !FNV_MUT(150)) return SaveVerdict::DenyNotify;  // MUTANT 150: a client saves the half-built world while the baseline arrives
    }
    return SaveVerdict::Allow;
}

namespace {
const char* const kIni[4] = {"bSaveOnTravel:GamePlay", "bSaveOnWait:GamePlay", "bSaveOnRest:GamePlay", "bSaveOnInteriorExteriorSwitch:GamePlay"};
}

const char* const* SaveGuard::iniNames(size_t* n) {
    if (n) *n = 4;
    return kIni;
}

std::string SaveGuard::lineFor(const char* name, bool on) {
    return std::string("SetINISetting \"") + name + "\" " + (on ? "1" : "0");
}

void SaveGuard::setOriginal(bool a, bool b, bool c, bool d) {
    orig_[0] = a; orig_[1] = b; orig_[2] = c; orig_[3] = d;
}

std::vector<std::string> SaveGuard::update(const SaveContext& c) {
    std::vector<std::string> out;
    const bool wantOff = engineAutosavesOff(c);
    if (wantOff == off_) return out;
    off_ = wantOff;
    for (int i = 0; i < 4; ++i) out.push_back(lineFor(kIni[i], wantOff ? false : (FNV_MUT(151) ? true : orig_[i])));   // MUTANT 151: leaving restores "on" even where the player had it off
    return out;
}

std::vector<std::string> SaveGuard::restore() {
    std::vector<std::string> out;
    if (!off_) return out;
    off_ = false;
    for (int i = 0; i < 4; ++i) out.push_back(lineFor(kIni[i], orig_[i]));
    if (FNV_MUT(152)) out.clear();   // MUTANT 152: the INI stays switched off after the session
    return out;
}

}  // namespace nvwt
