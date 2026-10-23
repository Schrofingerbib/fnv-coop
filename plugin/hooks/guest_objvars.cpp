#include "guest_objvars.h"

#include <algorithm>

namespace nvhooks {

#ifdef NVHOOKS_MUTANT
constexpr bool kMutNoRollback = true;   // the guest keeps what the object script wrote (the guest counts its own deaths)
#else
constexpr bool kMutNoRollback = false;
#endif

namespace {
struct Row {
    const char* scriptPlugin;
    uint32_t scriptId;
    const char* questPlugin;
    uint32_t questId;
    const char* var;
};
const Row kRows[] = {
#include "data/guest_objvars.inc"
};
}  // namespace

const ObjectVarWrites& ObjectVarWrites::builtin() {
    static const ObjectVarWrites t = [] {
        ObjectVarWrites w;
        for (const Row& r : kRows) w.add(fnvcoop::FormRef(r.scriptPlugin, r.scriptId), fnvcoop::FormRef(r.questPlugin, r.questId), r.var);
        return w;
    }();
    return t;
}

void ObjectVarWrites::add(const fnvcoop::FormRef& script, const fnvcoop::FormRef& quest, const std::string& var) {
    auto it = std::lower_bound(entries_.begin(), entries_.end(), script, [](const Entry& e, const fnvcoop::FormRef& s) { return e.script < s; });
    if (it == entries_.end() || it->script != script) {
        Entry e;
        e.script = script;
        it = entries_.insert(it, std::move(e));
    }
    for (const ObjVarWrite& w : it->writes)
        if (w.quest == quest && w.var == var) return;
    it->writes.push_back({quest, var});
    ++rows_;
}

const std::vector<ObjVarWrite>* ObjectVarWrites::of(const fnvcoop::FormRef& script) const {
    auto it = std::lower_bound(entries_.begin(), entries_.end(), script, [](const Entry& e, const fnvcoop::FormRef& s) { return e.script < s; });
    return it != entries_.end() && it->script == script ? &it->writes : nullptr;
}

size_t ObjectVarWrites::scripts() const { return entries_.size(); }

size_t VarRollback::restore() {
    size_t n = 0;
    if (!kMutNoRollback) {
        for (const Slot& s : saved_) {
            if (*s.p != s.v) {
                *s.p = s.v;
                ++n;
            }
        }
    }
    saved_.clear();
    return n;
}

}  // namespace nvhooks
