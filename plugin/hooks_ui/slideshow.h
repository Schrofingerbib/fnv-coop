// L11 hooks_ui: ending slideshow replication (game-free). The host runs the ending scripts (VEnding / NarratorScript / the INFO result
// scripts); every `SwapTexture <slideRef> "<node>" "<texture>"` they execute is captured by ui_hooks and fed to SlideHost::onSwap. The host
// broadcasts it on its own mux channel (first byte kSlideChannel); SlideClient::onPacket plays the swaps in order through a callback
// (ops::swapTexture) and keeps the player's controls locked from the first ENDING slide until the show ends:
//   * End packet from the host (SlideHost::end, called when the SlideshowEnd effect is committed), or
//   * the SlideshowEnd effect itself applied on the client (SlideClient::onSlideshowEndEffect), or
//   * the host disconnects / the lock watchdog expires (nobody may stay locked forever).
// Narration (SayTo player ... from the narrator) travels through L8's forcedSay (scene = everybody hears it), not through this channel.
// Both are ordered against each other by showorder.h: every slide carries the host's ShowClock stamp (ord/after) and the client plays it
// through a ShowSequencer shared with the narration gate, so slide N never shows before the sentence that precedes it (and vice versa).
// Wire: [kSlideChannel][ver][type][body][crc32 LE]; varints like the dialogue codec (same detail::W/R helpers).
#pragma once
#include <cstdint>
#include <functional>
#include <map>
#include <string>
#include <vector>

#include "fnvcoop/types.h"
#include "fnvcoop_dlg/messages.h"
#include "showorder.h"

#ifndef NVUI_MUTANT
#define NVUI_MUTANT 0
#endif

namespace nvui {

constexpr uint8_t kSlideChannel = 0xE5;  // protocol.md section 2 (0xFC story, 0xD1 dialogue, 0xA1 reserved)
constexpr uint8_t kSlideVersion = 2;  // v2: SlideItem carries the ShowClock stamp (ord, after)
constexpr size_t kMaxSlides = 1024;  // per show (the biggest ending has 198 + 3 DLC shows)

enum class SlideKind : uint8_t { None = 0, Intro = 1, Ending = 2 };

inline std::string normTexture(const std::string& s) {
    std::string o;
    o.reserve(s.size());
    for (char c : s) o.push_back(c == '/' ? '\\' : char(c >= 'A' && c <= 'Z' ? c - 'A' + 'a' : c));
    return o;
}

// Which SwapTexture calls are slides. Measured over the 767 SwapTexture statements of the vanilla + DLC scripts (out/questdata/effects.json.gz):
// 198 OnScreenSlideRef + NVDLCnSlide* ending/intro screens live under interface\endgame\ and interface\intro*\; the rest (Rorschach test
// clutter, Euclid screens on weapons) are ordinary texture swaps and must not lock anyone's controls.
inline SlideKind classifySlide(const std::string& newTexture) {
    const std::string t = "\\" + normTexture(newTexture);
    if (t.find("\\interface\\endgame\\") != std::string::npos) return SlideKind::Ending;
    if (t.find("\\interface\\intro") != std::string::npos) return SlideKind::Intro;  // intromovie, intro
    return SlideKind::None;
}

struct SlideItem {
    uint32_t seq = 0;  // 1.. within a show
    SlideKind kind = SlideKind::None;
    uint32_t atMs = 0;  // host time since the show began (informational: playback is immediate, the channel is ordered)
    uint32_t ord = 0, after = 0;  // ShowClock stamp (0 = unstamped, e.g. a late-joiner replay: plays right away)
    fnvcoop::FormRef ref;
    std::string oldTex, newTex;
};

struct SlideMsg {
    enum Type : uint8_t { Slide = 1, End = 2 } type = Slide;
    uint32_t show = 0;
    uint32_t total = 0;  // End only: how many slides the show had, so a reordered End does not cut the show short
    SlideItem item;      // Slide only
};

inline std::vector<uint8_t> encodeSlide(const SlideMsg& m) {
    fnvcoop_dlg::detail::W w;
    w.u8(kSlideChannel);
    w.u8(kSlideVersion);
    w.u8(m.type);
    w.var(m.show);
    if (m.type == SlideMsg::Slide) {
        w.var(m.item.seq);
        w.u8(uint8_t(m.item.kind));
        w.var(m.item.atMs);
        w.var(m.item.ord);
        w.var(m.item.after);
        w.form(m.item.ref);
        w.str(m.item.oldTex);
        w.str(m.item.newTex);
    } else {
        w.var(m.total);
    }
    const uint32_t c = fnvcoop_dlg::detail::crc32(w.b.data(), w.b.size());
    for (int i = 0; i < 4; ++i) w.u8(uint8_t(c >> (8 * i)));
    return w.b;
}

enum class SlideDecode { Ok, TooShort, BadMagic, BadVersion, BadCrc, Malformed };

inline SlideDecode decodeSlide(const std::vector<uint8_t>& b, SlideMsg& out) {
    using namespace fnvcoop_dlg::detail;
    if (b.size() < 8) return SlideDecode::TooShort;
    if (b[0] != kSlideChannel) return SlideDecode::BadMagic;
    if (b[1] != kSlideVersion) return SlideDecode::BadVersion;
    uint32_t want = 0;
    for (int i = 0; i < 4; ++i) want |= uint32_t(b[b.size() - 4 + i]) << (8 * i);
    if (crc32(b.data(), b.size() - 4) != want) return SlideDecode::BadCrc;
    R r(b.data(), b.size() - 4);
    r.pos = 2;
    SlideMsg m;
    const uint8_t t = r.u8();
    if (t != SlideMsg::Slide && t != SlideMsg::End) return SlideDecode::Malformed;
    m.type = SlideMsg::Type(t);
    m.show = r.v32();
    if (m.type == SlideMsg::Slide) {
        m.item.seq = r.v32();
        const uint8_t k = r.u8();
        if (k < 1 || k > 2) return SlideDecode::Malformed;
        m.item.kind = SlideKind(k);
        m.item.atMs = r.v32();
        m.item.ord = r.v32();
        m.item.after = r.v32();
        m.item.ref = r.form();
        m.item.oldTex = r.str();
        m.item.newTex = r.str();
        if (m.item.seq == 0 || (m.item.ord != 0 && m.item.after >= m.item.ord)) return SlideDecode::Malformed;
    } else {
        m.total = r.v32();
    }
    if (!r.ok || r.pos != r.n) return SlideDecode::Malformed;
    out = m;
    return SlideDecode::Ok;
}

// ------------------------------------------------------------------ host
class SlideHost {
public:
    using Send = std::function<void(fnvcoop::PlayerId to, std::vector<uint8_t> bytes)>;
    struct Config {
        uint64_t showTimeoutMs = 20 * 60 * 1000;  // an ending show that never produces SlideshowEnd is closed after this long without a new slide
    };

    explicit SlideHost(Send s) : send_(std::move(s)) {}
    SlideHost(Send s, Config c) : send_(std::move(s)), cfg_(c) {}
    SlideHost(const SlideHost&) = delete;
    SlideHost& operator=(const SlideHost&) = delete;

    void playerJoined(fnvcoop::PlayerId p, uint64_t nowMs) {
        (void)nowMs;
        if (p == fnvcoop::kHostPlayer || p >= 8) return;
        connected_ |= uint8_t(1u << p);
        if (!active_ || NVUI_MUTANT == 14) return;
        for (SlideItem it : items_) {  // late joiner: the whole show so far, in order. Narration is not replayed, so the replay is unstamped.
            it.after = 0;
            emit(p, SlideMsg::Slide, it);
        }
    }
    void playerLeft(fnvcoop::PlayerId p) {
        if (p < 8) connected_ &= uint8_t(~(1u << p));
    }

    // SwapTexture ran on the host. Returns true if it is a slide (and was broadcast).
    // The clock is shared with the router, which stamps scene narration from the same counter (ui_bridge.h connects them).
    ShowClock& clock() { return *clock_; }
    void setClock(ShowClock* c) { clock_ = c ? c : &ownClock_; }

    bool onSwap(const fnvcoop::FormRef& ref, const std::string& oldTex, const std::string& newTex, uint64_t nowMs) {
        const SlideKind k = classifySlide(newTex);
        if (k == SlideKind::None) return false;
        if (active_ && items_.size() >= kMaxSlides) return true;
        if (!active_) {
            active_ = true;
            ++show_;
            startMs_ = nowMs;
            items_.clear();
            seq_ = 0;
        }
        lastMs_ = nowMs;
        SlideItem it;
        it.seq = ++seq_;
        {
            const ShowStamp st = clock_->next();
            it.ord = st.ord;
            it.after = st.after;
        }
        it.kind = k;
        it.atMs = uint32_t(nowMs - startMs_);
        it.ref = ref;
        it.oldTex = oldTex;
        it.newTex = newTex;
        items_.push_back(it);
        if (k == SlideKind::Ending) endingShow_ = true;
        broadcast(SlideMsg::Slide, it);
        return true;
    }

    // The SlideshowEnd effect was committed on the host (or the host's own ending script finished).
    void end() {
        if (!active_) return;
        broadcast(SlideMsg::End, SlideItem());
        active_ = false;
        endingShow_ = false;
        items_.clear();
        ++ended_;
    }

    void tick(uint64_t nowMs) {
        if (active_ && nowMs - lastMs_ > cfg_.showTimeoutMs) end();
    }

    bool active() const { return active_; }
    uint32_t show() const { return show_; }
    size_t slides() const { return items_.size(); }
    uint32_t shows() const { return ended_; }

private:
    void emit(fnvcoop::PlayerId to, SlideMsg::Type t, const SlideItem& it) {
        SlideMsg m;
        m.type = t;
        m.show = show_;
        m.item = it;
        m.total = seq_;
        send_(to, encodeSlide(m));
    }
    void broadcast(SlideMsg::Type t, const SlideItem& it) {
        for (fnvcoop::PlayerId p = 1; p < 8; ++p)
            if (connected_ & (1u << p)) emit(p, t, it);
    }

    Send send_;
    Config cfg_;
    ShowClock ownClock_;
    ShowClock* clock_ = &ownClock_;
    uint8_t connected_ = 0;
    bool active_ = false, endingShow_ = false;
    uint32_t show_ = 0, seq_ = 0, ended_ = 0;
    uint64_t startMs_ = 0, lastMs_ = 0;
    std::vector<SlideItem> items_;
};

// ------------------------------------------------------------------ client
class SlideClient {
public:
    struct Callbacks {
        std::function<bool(const fnvcoop::FormRef&, const std::string&, const std::string&)> swap;  // ops::swapTexture
        std::function<void(bool locked)> setLocked;                                                  // controls (nvhooks::SetPlayerControlsLocked)
        std::function<void(const std::string&)> log;
        std::function<void(uint64_t nowMs)> onTick;  // optional: runs on every tick (the narration gate's flush)
    };
    struct Config {
        uint64_t endGraceMs = 5000;               // End seen but slides missing for this long -> play the rest and finish
        uint64_t lockTimeoutMs = 25 * 60 * 1000;  // no packet from the host for this long while locked -> unlock
    };

    // `seq`: the sequencer shared with the narration gate (nullptr = a private one: slides are then only ordered among themselves).
    explicit SlideClient(Callbacks cb, ShowSequencer* seq = nullptr) : cb_(std::move(cb)), seq_(seq ? seq : &ownSeq_) {}
    SlideClient(Callbacks cb, Config c, ShowSequencer* seq = nullptr) : cb_(std::move(cb)), cfg_(c), seq_(seq ? seq : &ownSeq_) {}
    SlideClient(const SlideClient&) = delete;
    SlideClient& operator=(const SlideClient&) = delete;

    // Returns true if the packet was a slideshow packet (consumed, even when malformed).
    bool onPacket(const std::vector<uint8_t>& b, uint64_t nowMs) {
        if (b.empty() || b[0] != kSlideChannel) return false;
        SlideMsg m;
        if (decodeSlide(b, m) != SlideDecode::Ok) {
            ++bad_;
            return true;
        }
        lastMs_ = nowMs;
        nowMs_ = nowMs;
        if (m.show < show_ || (m.show == show_ && done_)) return true;  // a stale show / a duplicate after End
        if (m.show > show_) startShow(m.show);
        if (m.type == SlideMsg::End) {
            endTotal_ = m.total;
            endSeen_ = true;
            endAtMs_ = nowMs;
            flush(false);
            maybeFinish();
            return true;
        }
        if (m.item.seq < next_ && NVUI_MUTANT != 13) {
            ++dups_;
            return true;
        }
        pending_[m.item.seq] = m.item;
        flush(false);
        maybeFinish();
        return true;
    }

    // SlideshowEnd effect applied on this machine (L4 sticky flag): releases the lock even if the End packet was lost.
    void onSlideshowEndEffect() {
        if (show_ == 0 || done_) {
            if (locked_) setLock(false);
            return;
        }
        forceFinish();
    }
    void onHostLost() {
        if (locked_) setLock(false);
        if (show_ && !done_) forceFinish();
    }
    void tick(uint64_t nowMs) {
        nowMs_ = nowMs;
        seq_->tick(nowMs);
        if (cb_.onTick) cb_.onTick(nowMs);
        if (endSeen_ && !done_ && nowMs - endAtMs_ > cfg_.endGraceMs) {  // End arrived but a slide never did: play what we have and release
            forceFinish();
        }
        if (locked_ && nowMs - lastMs_ > cfg_.lockTimeoutMs) {
            log("slideshow lock watchdog expired");
            onHostLost();
        }
    }

    bool locked() const { return locked_; }
    uint32_t show() const { return show_; }
    uint32_t played() const { return played_; }
    uint32_t dups() const { return dups_; }
    uint32_t bad() const { return bad_; }
    bool finished() const { return done_; }

private:
    void startShow(uint32_t id) {
        if (locked_) setLock(false);  // a newer show replaces a show that never ended
        show_ = id;
        next_ = 1;
        pending_.clear();
        done_ = false;
        endSeen_ = false;
        endTotal_ = 0;
    }
    void maybeFinish() {
        if (endSeen_ && !done_ && next_ > endTotal_ && inflight_ == 0) finish();
    }
    // Nothing may stay queued behind a narration line that will never come.
    void forceFinish() {
        flush(true);
        seq_->flushAll(nowMs_);
        finish();
    }
    // Plays contiguous slides; on `force` (End) plays what is buffered even across gaps (a lost slide must not hide the rest).
    void flush(bool force) {
        for (;;) {
            auto it = pending_.begin();
            if (it == pending_.end()) return;
            if (it->first != next_ && !force) return;
            play(it->second);
            next_ = it->first + 1;
            pending_.erase(it);
        }
    }
    // In-order slide: it still waits in the sequencer until the narration line before it (if any) was released.
    void play(const SlideItem& s) {
        ++inflight_;
        ShowStamp st;
        st.ord = s.ord;
        st.after = s.after;
        if (!seq_->submit(st, nowMs_, [this, s]() { execute(s); })) --inflight_;  // a stale/duplicate stamp: this slide was already shown
    }
    void execute(const SlideItem& s) {
        if (s.kind == SlideKind::Ending && !locked_ && NVUI_MUTANT != 11) setLock(true);
        if (cb_.swap && NVUI_MUTANT != 12) cb_.swap(s.ref, s.oldTex, s.newTex);
        ++played_;
        if (inflight_ > 0) --inflight_;
        maybeFinish();
    }
    void finish() {
        done_ = true;
        pending_.clear();
        if (locked_ && NVUI_MUTANT != 10) setLock(false);
    }
    void setLock(bool l) {
        locked_ = l;
        if (cb_.setLocked) cb_.setLocked(l);
    }
    void log(const std::string& s) {
        if (cb_.log) cb_.log(s);
    }

    Callbacks cb_;
    Config cfg_;
    ShowSequencer ownSeq_;
    ShowSequencer* seq_ = &ownSeq_;
    bool locked_ = false, done_ = false;
    uint32_t show_ = 0, next_ = 1, played_ = 0, dups_ = 0, bad_ = 0, inflight_ = 0;
    uint64_t lastMs_ = 0, endAtMs_ = 0, nowMs_ = 0;
    bool endSeen_ = false;
    uint32_t endTotal_ = 0;
    std::map<uint32_t, SlideItem> pending_;
};

}  // namespace nvui
