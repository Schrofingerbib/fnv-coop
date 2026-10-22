// Which of two engine reads of an actor value the adapter believes (A5, 2026-10-22). Game-free.
//
// In game 2026-10-22 the adapter read Health 60 from a Goodsprings powder ganger whose console `GetAV Health` said 40 (and 45 against 20 on a
// second one): both actors were not loaded (no 3D). The direct call of the avOwner vtable slot 3 (Actor::GetActorValue 0x66EF50) returned the
// base plus modifiers, without the damage that a low-process actor keeps in another place (slots 0-8 were probed: none returns 40). The
// condition evaluator behind the console command GetActorValue returns it for loaded and unloaded actors alike (loaded: both agree), so it is the
// first choice and the direct call is only the fallback for a build where the evaluator is not callable.
#pragma once
#include "fnvcoop_mut.h"

namespace nvcoop::actors::engine {

struct AvReads {
    bool evalOk = false;     // the condition evaluator answered
    float eval = 0;
    bool directOk = false;   // the direct vtable call answered
    float direct = 0;
};

inline bool pickActorValue(const AvReads& r, float& out) {
    if (FNV_MUT(58) && r.directOk) {   // twin 58: the direct call wins (misses the damage of an unloaded actor)
        out = r.direct;
        return true;
    }
    if (r.evalOk) {
        out = r.eval;
        return true;
    }
    if (r.directOk) {
        out = r.direct;
        return true;
    }
    return false;
}

}  // namespace nvcoop::actors::engine
