// A fake game for the intents tests: a table of references, positions, a real CaptureHub behind the acting scopes, and a log of every engine call
// with the acting context it saw. Nothing here touches a real engine.
#pragma once
#include <functional>
#include <map>
#include <string>
#include <vector>

#include "capture_hub.h"
#include "intents/intent_engine.h"

namespace nvcoop::intents::testing {

class FakeEngine : public IIntentEngine {
public:
    struct Call {
        std::string what;       // "activate" | "event" | "terminal" | "disable"
        RefId actor = 0;        // the actor argument of the call
        FormRef target;
        uint32_t arg = 0;       // event mask
        RefId contextActor = 0; // the acting-player context that was open when the call came (what `player` means to a script)
        PlayerId contextSlot = kNoPlayer;
        int depth = 0;          // acting scopes open at the time (0 = a call outside any scope: a bug)
    };

    nvhooks::CaptureHub* hub = nullptr;
    std::map<FormRef, TargetInfo> world;
    std::map<RefId, Vec3> positions;
    bool activateOk = true, eventOk = true, disableOk = true, terminalOk = true, terminalSupported = true;
    bool disableMarksWorld = true;   // false: the reference's disabled flag is not visible yet (the claim table must still hold the line)
    // what the engine's own hooks and the scripts would do inside the call (record effects, mutate the fake world)
    std::function<void(const Call&)> onCall;

    std::vector<Call> calls;
    std::vector<std::string> ui;   // "closeMenus" | "notify:<text>" | "open:<ref>"
    int depth = 0;
    int outsideScope = 0;          // engine calls made with no scope open
    RefId ctxActor = 0;
    PlayerId ctxSlot = kNoPlayer;
    std::vector<Cause> causes;     // one per beginActing

    // ---- IIntentEngine ----
    bool describe(const FormRef& target, TargetInfo& out) override {
        auto it = world.find(target);
        if (it == world.end()) return false;
        out = it->second;
        return true;
    }
    bool position(RefId ref, Vec3& pos, uint32_t* cell) override {
        auto it = positions.find(ref);
        if (it == positions.end()) return false;
        pos = it->second;
        if (cell) *cell = 0;
        return true;
    }

    class Scope : public IActingScope {
    public:
        Scope(FakeEngine& e, RefId actor, PlayerId slot, const Cause& c) : e_(e), prevActor_(e.ctxActor), prevSlot_(e.ctxSlot) {
            ++e_.depth;
            e_.ctxActor = actor;
            e_.ctxSlot = slot;
            e_.causes.push_back(c);
            if (e_.hub) cap_.reset(new nvhooks::ScopedCapture(*e_.hub, c));
        }
        ~Scope() override {
            cap_.reset();   // closes the capture scope: the txn is emitted while the acting context is still set
            --e_.depth;
            e_.ctxActor = prevActor_;
            e_.ctxSlot = prevSlot_;
        }
        bool record(const fnvcoop::Effect& x) override { return e_.hub && e_.hub->record(x); }

    private:
        FakeEngine& e_;
        RefId prevActor_;
        PlayerId prevSlot_;
        std::unique_ptr<nvhooks::ScopedCapture> cap_;
    };

    std::unique_ptr<IActingScope> beginActing(RefId actor, PlayerId slot, const Cause& cause) override {
        return std::unique_ptr<IActingScope>(new Scope(*this, actor, slot, cause));
    }
    bool activate(RefId actor, const FormRef& target) override { return log("activate", actor, target, 0, activateOk); }
    bool raiseEvent(RefId actor, const FormRef& target, uint32_t mask) override { return log("event", actor, target, mask, eventOk); }
    bool runTerminalEntry(RefId actor, const FormRef& terminal, const std::vector<uint8_t>& path, bool* supported) override {
        if (supported) *supported = terminalSupported;
        return log("terminal", actor, terminal, static_cast<uint32_t>(path.size()), terminalOk && terminalSupported);
    }
    bool infoSupported = true, infoOk = true;
    bool runInfoResult(RefId actor, PlayerId slot, const FormRef& info, const FormRef& npc, bool onEnd, bool* supported) override {
        if (supported) *supported = infoSupported;
        if (!infoSupported) return false;
        // the engine's hook on RunResultScript opens the capture scope (cause DialogueResult, source = the INFO); the acting context is ours
        RefId prevActor = ctxActor;
        PlayerId prevSlot = ctxSlot;
        ctxActor = actor;
        ctxSlot = slot;
        ++depth;
        std::unique_ptr<nvhooks::ScopedCapture> cap;
        if (hub) cap.reset(new nvhooks::ScopedCapture(*hub, Cause{CauseKind::DialogueResult, info, onEnd ? 1 : 0}));
        bool ok = log("infoResult", actor, npc, onEnd ? 1u : 0u, infoOk);
        cap.reset();
        --depth;
        ctxActor = prevActor;
        ctxSlot = prevSlot;
        return ok;
    }
    bool disableRef(const FormRef& target) override {
        bool ok = log("disable", ctxActor, target, 0, disableOk);
        if (ok && disableMarksWorld) { auto it = world.find(target); if (it != world.end()) it->second.disabled = true; }
        return ok;
    }
    void closeMenus() override { ui.push_back("closeMenus"); }
    void notify(const std::string& t) override { ui.push_back("notify:" + t); }
    bool openContainer(const FormRef& c) override { ui.push_back("open:" + c.plugin + ":" + std::to_string(c.localId)); return true; }

    size_t count(const std::string& what) const {
        size_t n = 0;
        for (const auto& c : calls) n += c.what == what;
        return n;
    }
    bool uiHas(const std::string& prefix) const {
        for (const auto& s : ui) if (s.rfind(prefix, 0) == 0) return true;
        return false;
    }

private:
    bool log(const char* what, RefId actor, const FormRef& target, uint32_t arg, bool ok) {
        Call c;
        c.what = what;
        c.actor = actor;
        c.target = target;
        c.arg = arg;
        c.contextActor = ctxActor;
        c.contextSlot = ctxSlot;
        c.depth = depth;
        if (depth == 0) ++outsideScope;
        calls.push_back(c);
        if (onCall) onCall(c);
        return ok;
    }
};

inline TargetInfo mkTarget(TargetClass cls, RefId ref, float x = 0, float y = 0, float z = 0) {
    TargetInfo t;
    t.ref = ref;
    t.cls = cls;
    t.loaded = true;
    t.hasPos = true;
    t.pos = {x, y, z};
    return t;
}

}  // namespace nvcoop::intents::testing
