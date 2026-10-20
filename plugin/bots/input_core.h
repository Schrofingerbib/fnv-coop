// Forced game input for test bots (T6a). Game-free: no Win32, no NVSE.
//
// The game decides "is this control down?" through OSInputGlobals::GetControlState(ctrl, state) (thiscall at 0xA24660, `ret 8`),
// the single function the player controls, menus and weapon code all ask. A detour on it lets a bot hold W, tap Activate, press
// the Attack button and so on *inside our own process*: no SendInput, no desktop events, works while the window is minimized.
// `state` values (read from the call sites in the exe, see docs/tools/T6a-actions.md): 0 = held now, 1 = pressed this frame
// (edge), 2 = released this frame (edge).
//
// Edge rules: toggles such as Crouch fire on the edge, so an edge must reach the game exactly ONCE. The first frame in which the
// game asks for an edge fixes the frame the edge is served in; every query in that frame sees it, later frames never do.
//
// Mutants (NVBOTS_MUTANT=n) are compiled into the tests' twin builds; every one must make the tests fail:
//   1 press edge served on every frame while held   2 held override ignored          3 releaseAll leaves controls held
//   4 tap never releases                            5 out-of-range control forced    6 strafe axes swapped
//   7 timed hold (ms) never expires                 8 release edge never served
#pragma once
#include <array>
#include <cstdint>

#ifndef NVBOTS_MUTANT
#define NVBOTS_MUTANT 0
#endif

namespace nvbots {

// OSInputGlobals control ids (28 bindings, 0x1C). Forward..Rest are proven from the exe call sites; the rest follow the NVSE/JIP table.
enum Ctrl : int {
    kForward = 0, kBackward = 1, kLeft = 2, kRight = 3, kAttack = 4, kActivate = 5, kBlock = 6, kReadyItem = 7, kCrouch = 8,
    kRun = 9, kAlwaysRun = 10, kAutoMove = 11, kJump = 12, kTogglePOV = 13, kMenuMode = 14, kRest = 15, kVats = 16,
    kCount = 0x1C,
};
enum State : int { kHeld = 0, kPressed = 1, kReleased = 2 };

class ForcedInput {
public:
    // Call once per game frame (the nvcoop OnFrame). Counts down timed holds and retires served edges.
    void advanceFrame() {
        ++frame_;
        for (int c = 0; c < kCount; ++c) {
            Slot& s = s_[c];
            if (s.pressServed >= 0 && s.pressServed < frame_) { s.pressPending = false; s.pressServed = -1; }
            if (s.relServed >= 0 && s.relServed < frame_) { s.relPending = false; s.relServed = -1; }
            #if NVBOTS_MUTANT == 7
            if (false) {}
#else
            if (s.held && s.framesLeft > 0 && --s.framesLeft == 0) releaseSlot(s);
#endif
        }
    }

    void hold(int c, int frames = -1) {  // frames < 0: until release()
        if (!ok(c)) return;
        Slot& s = s_[c];
        if (!s.held) { s.held = true; s.pressPending = true; s.pressServed = -1; }
        s.framesLeft = frames > 0 ? frames : -1;
    }
    void tap(int c, int frames = 3) {
#if NVBOTS_MUTANT == 4
        hold(c, -1);
#else
        hold(c, frames < 1 ? 1 : frames);
#endif
    }
    void release(int c) {
        if (ok(c)) releaseSlot(s_[c]);
    }
    void releaseAll() {
#if NVBOTS_MUTANT == 3
        return;
#endif
        for (int c = 0; c < kCount; ++c) releaseSlot(s_[c]);
    }
    bool isHeld(int c) const { return ok(c) && s_[c].held; }
    int heldCount() const {
        int n = 0;
        for (const Slot& s : s_) n += s.held ? 1 : 0;
        return n;
    }
    int64_t frame() const { return frame_; }

    // Digital movement from axes in [-1, 1]. forward > 0 walks forward; strafe > 0 steps right. `run` holds the Run control.
    // The dead zone keeps small noise from moving the avatar.
    void setMove(double forward, double strafe, bool run, int frames = -1) {
        setAxis(kBackward, kForward, forward, frames);
#if NVBOTS_MUTANT == 6
        setAxis(kRight, kLeft, strafe, frames);
#else
        setAxis(kLeft, kRight, strafe, frames);
#endif
        if (run) hold(kRun, frames);
        else release(kRun);
    }
    void stopMove() {
        release(kForward); release(kBackward); release(kLeft); release(kRight); release(kRun);
    }

    // The detour: what the game should see for (ctrl, state), given what the real devices said.
    int filter(int c, int state, int real) {
#if NVBOTS_MUTANT == 5
        if (c >= kCount) return 1;
#endif
        if (!ok(c)) return real;
        Slot& s = s_[c];
        if (state == kHeld) {
#if NVBOTS_MUTANT == 2
            return real;
#endif
            return s.held ? 1 : real;
        }
        if (state == kPressed) {
#if NVBOTS_MUTANT == 1
            if (s.held) return 1;
#endif
            if (s.pressPending) {
                if (s.pressServed < 0) s.pressServed = frame_;
                if (s.pressServed == frame_) return 1;
            }
            return s.held ? 0 : real;   // while we hold a control the real device's edges are hidden
        }
        if (state == kReleased) {
#if NVBOTS_MUTANT == 8
            return real;
#endif
            if (s.relPending) {
                if (s.relServed < 0) s.relServed = frame_;
                if (s.relServed == frame_) return 1;
            }
            return real;
        }
        return real;
    }

private:
    struct Slot {
        bool held = false, pressPending = false, relPending = false;
        int64_t pressServed = -1, relServed = -1;
        int framesLeft = -1;
    };
    static bool ok(int c) { return c >= 0 && c < kCount; }
    void releaseSlot(Slot& s) {
        if (s.held) { s.held = false; s.relPending = true; s.relServed = -1; s.pressPending = false; }
        s.framesLeft = -1;
    }
    void setAxis(int neg, int pos, double v, int frames) {
        constexpr double kDead = 0.3;
        if (v > kDead) { release(neg); hold(pos, frames); }
        else if (v < -kDead) { release(pos); hold(neg, frames); }
        else { release(neg); release(pos); }
    }
    std::array<Slot, kCount> s_{};
    int64_t frame_ = 0;
};

}  // namespace nvbots
