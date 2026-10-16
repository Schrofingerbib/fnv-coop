#include "world_plan.h"

namespace nvworld {

using fnvcoop::FormRef;

const char* planStatusName(PlanStatus s) {
    switch (s) {
        case PlanStatus::Ok: return "Ok";
        case PlanStatus::FormMissing: return "FormMissing";
        case PlanStatus::Queued: return "Queued";
        case PlanStatus::Unsupported: return "Unsupported";
    }
    return "?";
}

bool handlesEffect(const fnvcoop::Effect& e) {
    return std::holds_alternative<fnvcoop::SpawnRef>(e) || std::holds_alternative<fnvcoop::DespawnRef>(e) ||
           std::holds_alternative<fnvcoop::RefScriptVar>(e);
}

namespace {

Plan missing(const FormRef& r) {
    Plan p;
    p.status = PlanStatus::FormMissing;
    p.error = "form missing: " + r.plugin;
    return p;
}

// Does the form exist here? (resolver maps the ref and the engine finds the form)
bool exists(PlanEnv& env, const FormRef& r, uint32_t* id) {
    uint32_t i = env.res.fromRef(r);
    if (!i || !env.res.lookup(i)) return false;
    if (id) *id = i;
    return true;
}

Plan createStep(PlanEnv& env, const fnvcoop::SpawnRef& s) {
    Plan p;
    uint32_t base = 0, cell = 0;
    if (!exists(env, s.base, &base)) return missing(s.base);
    cell = env.res.fromRef(s.cell);  // may be 0 when the cell form is not loaded yet; the glue then places relative to the player
    Step st;
    st.kind = StepKind::CreateSpawn;
    st.spawn = s;
    st.baseID = base;
    st.cellID = cell;
    p.steps.push_back(st);
    return p;
}

}  // namespace

Plan planEffect(const fnvcoop::Effect& e, PlanEnv& env) {
    if (auto* x = std::get_if<fnvcoop::SpawnRef>(&e)) {
        bool ready = env.cellReady ? env.cellReady(x->cell) : true;
#if NVWORLD_MUT == 25
        ready = true;  // MUTANT: never queues, creates into the wrong (unloaded) cell
#endif
        SpawnAction a = env.ledger.onSpawn(*x, ready);
        if (a == SpawnAction::Duplicate) return Plan{};  // replay: already have it
        if (a == SpawnAction::Queued) {
            Plan p;
            p.status = PlanStatus::Queued;
            return p;
        }
        return createStep(env, *x);
    }
    if (auto* x = std::get_if<fnvcoop::DespawnRef>(&e)) {
        DespawnResult r = env.ledger.onDespawn(*x);
        Plan p;
        switch (r.action) {
            case DespawnAction::CancelledPending:
            case DespawnAction::Unknown:
                return p;
            case DespawnAction::DeleteLocal: {
                Step st;
                st.kind = StepKind::ConsoleOnRef;
#if NVWORLD_MUT == 23
                st.refID = x->id;  // MUTANT: host id used on the client instead of the local object
#else
                st.refID = r.localRefID;
#endif
                st.line = markForDeleteLine();
                p.steps.push_back(st);
                return p;
            }
        }
        return p;
    }
    if (auto* x = std::get_if<fnvcoop::RefScriptVar>(&e)) {
        uint32_t id = 0;
        if (!exists(env, x->ref, &id)) return missing(x->ref);
        Plan p;
        Step st;
        st.kind = StepKind::SetRefVar;
        st.refID = id;
#if NVWORLD_MUT == 22
        st.varIndex = (uint16_t)(x->index + 1);  // MUTANT
#else
        st.varIndex = x->index;
#endif
        st.varValue = x->value;
        p.steps.push_back(st);
        return p;
    }
    Plan p;
    p.status = PlanStatus::Unsupported;
    p.error = "not a world-lane effect";
    return p;
}

std::vector<Plan> planQueuedSpawns(PlanEnv& env) {
    std::vector<Plan> out;
    auto ready = [&](const fnvcoop::SpawnRef& s) { return env.cellReady ? env.cellReady(s.cell) : true; };
    for (auto& s : env.ledger.takeReady(ready)) out.push_back(createStep(env, s));
    return out;
}

Plan planWorldEffect(const WorldEffect& e, PlanEnv& env) {
    Plan p;
    auto onRef = [&](const FormRef& actor, const std::string& line) -> Plan {
        uint32_t id = 0;
        if (!exists(env, actor, &id)) return missing(actor);
        if (line.empty()) {
            Plan u;
            u.status = PlanStatus::Unsupported;
            u.error = "no console form for this effect";
            return u;
        }
        Plan q;
        Step st;
        st.kind = StepKind::ConsoleOnRef;
        st.refID = id;
        st.line = line;
        q.steps.push_back(st);
        return q;
    };
    if (auto* x = std::get_if<SetActorValueRef>(&e)) return onRef(x->actor, avLine(*x));
    if (auto* x = std::get_if<SetRestrained>(&e)) return onRef(x->actor, setRestrainedLine(x->on));
    if (auto* x = std::get_if<SetScriptPackage>(&e)) {
        uint32_t pk = 0;
        if (!exists(env, x->package, &pk)) return missing(x->package);
        return onRef(x->actor, scriptPackageLine(x->add, pk));
    }
    if (auto* x = std::get_if<SetEssential>(&e)) {
        uint32_t id = 0;
        if (!exists(env, x->actor, &id)) return missing(x->actor);
#if NVWORLD_MUT == 24
        if (false)  // MUTANT: ref-only essential applied to the base form
#else
        if (x->refOnly)
#endif
            return onRef(x->actor, setEssentialLine(*x, id));
        Step st;
        st.kind = StepKind::ConsoleGlobal;
        st.line = setEssentialLine(*x, id);
        p.steps.push_back(st);
        return p;
    }
    if (auto* x = std::get_if<SetRelation>(&e)) {
        uint32_t a = 0, b = 0;
        if (!exists(env, x->a, &a)) return missing(x->a);
        if (!exists(env, x->b, &b)) return missing(x->b);
        Step st;
        st.kind = StepKind::ConsoleGlobal;
        st.line = setRelationLine(x->kind, a, b);
        p.steps.push_back(st);
        return p;
    }
    p.status = PlanStatus::Unsupported;
    return p;
}

}  // namespace nvworld
