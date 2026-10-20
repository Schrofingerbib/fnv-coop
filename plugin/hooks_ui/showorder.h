// L11 hooks_ui: one total order for everything a cutscene shows (game-free).
//
// The ending slideshow travels on its own mux channel (0xE5, slideshow.h) and the narrator's voice-over travels as L8 scene
// `ForcedLines` (0xD1). Each channel is ordered by itself, but nothing orders the two against each other, so a slide could change
// before (or long after) the sentence that belongs to it. The host therefore stamps every slide and every scene line from ONE counter
// (ShowClock): `ord` = 1, 2, 3 ... in the order the host script produced them, `after` = the ord of the item just before. A receiver
// feeds both kinds into a ShowSequencer, which releases an item only when its predecessor was released (or after `gapMs`, so a lost
// item can never freeze the show). Duplicates (a retransmitted packet, the 60% duplication test) are dropped by ord.
//
// Narration has no spare field in L8's wire format, so the stamp rides in `Line.voice` as a suffix:  "<voice>#o<ord>.<after>" (a line
// without voice becomes just "#o<ord>.<after>"). NarrationGate pairs the subtitle call and the voice call L8's glue makes for each line
// (subtitle first, then voice) and runs both when the sequencer releases the stamp.
#pragma once
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <functional>
#include <map>
#include <string>

#include "fnvcoop/types.h"

#ifndef NVUI_MUTANT
#define NVUI_MUTANT 0
#endif

namespace nvui {

struct ShowStamp {
    uint32_t ord = 0;    // 0 = not stamped (plays immediately)
    uint32_t after = 0;  // ord of the item that precedes this one on the host (0 = start of a chain / replayed item)
    bool valid() const { return ord != 0; }
    bool operator==(const ShowStamp& o) const { return ord == o.ord && after == o.after; }
};

// Host side: the single counter shared by SlideHost and the router's scene-line stamping.
class ShowClock {
public:
    ShowStamp next() {
        ShowStamp s;
        s.after = last_;
        s.ord = ++last_;
        return s;
    }
    uint32_t last() const { return last_; }

private:
    uint32_t last_ = 0;
};

// ---- stamp <-> Line.voice -------------------------------------------------------------------------------------------------------
inline std::string withOrder(const std::string& voice, const ShowStamp& s) {
    if (!s.valid()) return voice;
    char b[40];
    std::snprintf(b, sizeof b, "#o%u.%u", s.ord, s.after);
    return voice + b;
}

// Splits "<rest>#o<ord>.<after>". An unstamped (or malformed-suffix) string is returned whole with an invalid stamp.
inline ShowStamp parseOrder(const std::string& voice, std::string& rest) {
    rest = voice;
    const size_t p = voice.rfind("#o");
    if (p == std::string::npos) return ShowStamp();
    const char* s = voice.c_str() + p + 2;
    char* e = nullptr;
    const unsigned long ord = std::strtoul(s, &e, 10);
    if (e == s || *e != '.') return ShowStamp();
    const char* s2 = e + 1;
    const unsigned long after = std::strtoul(s2, &e, 10);
    if (e == s2 || *e != '\0' || ord == 0 || ord > 0x7FFFFFFFUL || after >= ord) return ShowStamp();
    rest = voice.substr(0, p);
    ShowStamp st;
    st.ord = uint32_t(ord);
    st.after = uint32_t(after);
    return st;
}

// ---- receiver -----------------------------------------------------------------------------------------------------------------------
class ShowSequencer {
public:
    struct Config {
        uint64_t gapMs = 3000;   // the predecessor of the oldest waiting item never arrived: release it anyway after this long
        size_t maxPending = 512;
    };
    using Action = std::function<void()>;

    ShowSequencer() = default;
    explicit ShowSequencer(Config c) : cfg_(c) {}

    // Returns false when the item was dropped (already released, already waiting, or the queue is full).
    bool submit(const ShowStamp& s, uint64_t nowMs, Action a) {
        if (!s.valid()) {  // not part of an ordered show
            if (a) a();
            return true;
        }
        if (s.ord <= released_ && NVUI_MUTANT != 18) {
            ++dups_;
            return false;
        }
        if (pending_.count(s.ord) || pending_.size() >= cfg_.maxPending) {
            ++dups_;
            return false;
        }
        pending_[s.ord] = Item{s.after, nowMs, std::move(a)};
        drain(nowMs);
        return true;
    }

    void tick(uint64_t nowMs) {
        if (NVUI_MUTANT != 19 && !pending_.empty() && !draining_) {  // MUTANT 19: a missing predecessor blocks the show forever
            auto it = pending_.begin();
            if (nowMs - it->second.at >= cfg_.gapMs) {
                ++gapSkips_;
                released_ = it->first - 1 > released_ ? it->first - 1 : released_;  // jump over the hole
                drain(nowMs);
            }
        }
    }

    // End of a show / host lost: nothing may stay queued. Releases everything in ord order.
    void flushAll(uint64_t nowMs) {
        for (;;) {
            if (pending_.empty() || draining_) return;
            auto it = pending_.begin();
            released_ = it->first - 1 > released_ ? it->first - 1 : released_;
            drain(nowMs);
        }
    }

    void reset() {
        pending_.clear();
        released_ = 0;
    }

    uint32_t released() const { return released_; }
    size_t pending() const { return pending_.size(); }
    uint32_t dups() const { return dups_; }
    uint32_t gapSkips() const { return gapSkips_; }

private:
    struct Item {
        uint32_t after;
        uint64_t at;
        Action act;
    };

    void drain(uint64_t nowMs) {
        (void)nowMs;
        if (draining_) return;  // an action submitted a follow-up: the outer loop picks it up
        draining_ = true;
        for (;;) {
            auto it = pending_.begin();
            if (it == pending_.end()) break;
            const bool ready = it->second.after == 0 || it->second.after <= released_;
            if (!ready) break;
            released_ = it->first;
            Action a = std::move(it->second.act);
            pending_.erase(it);
            if (a) a();
        }
        draining_ = false;
    }

    Config cfg_;
    std::map<uint32_t, Item> pending_;
    uint32_t released_ = 0, dups_ = 0, gapSkips_ = 0;
    bool draining_ = false;
};

// Pairs the two engine calls L8's glue makes per line (showSubtitle, then playVoice when the line has a voice string) and hands the
// pair to the sequencer. Lines whose voice string has no stamp are delivered at once, like before.
class NarrationGate {
public:
    struct Sink {
        std::function<void(const fnvcoop::FormRef& npc, const std::string& text, float seconds)> subtitle;
        std::function<void(const fnvcoop::FormRef& npc, const std::string& token)> voice;  // token without the order suffix; never empty
    };

    NarrationGate(ShowSequencer& seq, Sink sink) : seq_(seq), sink_(std::move(sink)) {}

    void onSubtitle(const fnvcoop::FormRef& npc, const std::string& text, float seconds) {
        flushStash();  // a subtitle that was not followed by a voice call is a plain line
        stash_ = Stash{true, npc, text, seconds};
    }

    void onVoice(const fnvcoop::FormRef& npc, const std::string& voice, uint64_t nowMs) {
        std::string rest;
        const ShowStamp st = NVUI_MUTANT == 17 ? ShowStamp() : parseOrder(voice, rest);  // MUTANT 17: the stamp is ignored (no ordering)
        if (NVUI_MUTANT == 17) rest = voice;
        Stash s = stash_;
        stash_.has = false;
        if (!st.valid()) {
            deliver(s, npc, rest);
            return;
        }
        seq_.submit(st, nowMs, [this, s, npc, rest]() { deliver(s, npc, rest); });
    }

    void tick(uint64_t nowMs) {
        (void)nowMs;
        flushStash();
        seq_.tick(nowMs);
    }

private:
    struct Stash {
        bool has = false;
        fnvcoop::FormRef npc;
        std::string text;
        float seconds = 0;
    };
    void deliver(const Stash& s, const fnvcoop::FormRef& npc, const std::string& token) {
        if (s.has && sink_.subtitle) sink_.subtitle(s.npc, s.text, s.seconds);
        if (!token.empty() && sink_.voice) sink_.voice(npc, token);
    }
    void flushStash() {
        if (!stash_.has) return;
        Stash s = stash_;
        stash_.has = false;
        if (sink_.subtitle) sink_.subtitle(s.npc, s.text, s.seconds);
    }

    ShowSequencer& seq_;
    Sink sink_;
    Stash stash_;
};

}  // namespace nvui
