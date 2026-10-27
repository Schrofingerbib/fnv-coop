// Guest rollback of shared quest variables that object / NPC / effect scripts write inline (game-free, unit-tested).
//
// `set VMS16.nGangerDeathCount to VMS16.nGangerDeathCount + 1` in a ganger's OnDeath block is executed by the script interpreter itself:
// there is no function to detour, so rule 3 of suppress_policy.h (a shared-state MUTATOR called by a guest's object script is dropped) cannot
// stop it. The guest counted deaths on its own while the host did too, and a quest variable is story state: the host is authoritative.
//
// So a guest runs such a script inside a bracket: the variables it is known to write (data/guest_objvars.inc, generated from the game's own
// scripts by tools/gen_guest_objvars.py) are remembered before the script and put back afterwards. The host's value reaches the guest through
// the state replicator (polled on the host every frame), so the guest ends up with the host's number, not its own.
#pragma once
#include <cstdint>
#include <string>
#include <vector>

#include "fnvcoop/types.h"

namespace nvhooks {

struct ObjVarWrite {
    fnvcoop::FormRef quest;
    std::string var;   // lower case
};

// script form -> the quest variables it writes
class ObjectVarWrites {
public:
    // The built-in table (data/guest_objvars.inc).
    static const ObjectVarWrites& builtin();
    void add(const fnvcoop::FormRef& script, const fnvcoop::FormRef& quest, const std::string& var);
    // null when the script writes no shared quest variable
    const std::vector<ObjVarWrite>* of(const fnvcoop::FormRef& script) const;
    size_t scripts() const;
    size_t rows() const { return rows_; }

private:
    struct Entry {
        fnvcoop::FormRef script;
        std::vector<ObjVarWrite> writes;
    };
    std::vector<Entry> entries_;   // sorted by script
    size_t rows_ = 0;
};

// Remembers variable slots before a script runs and puts back every one the script changed.
class VarRollback {
public:
    void remember(double* slot) { if (slot) saved_.push_back({slot, *slot}); }
    // Restores the changed slots; returns how many it put back.
    size_t restore();
    size_t size() const { return saved_.size(); }

private:
    struct Slot { double* p; double v; };
    std::vector<Slot> saved_;
};

}  // namespace nvhooks
