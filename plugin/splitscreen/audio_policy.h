#pragma once
// Split-screen audio: only player one's copy plays sound. The others are muted at the Windows audio SESSION of their own process
// (the volume mixer's per-app mute), so the game keeps its sound system running: voice-line end events and timers that dialogue
// sync waits on still fire (bEnableAudio=0 would remove them, tools/pool/lowcost.py). Nothing else on the PC is touched: the muter
// only changes sessions that belong to this process, and only un-mutes what it muted itself.
// The Windows side (Core Audio, audio_win.cpp inside splitscreen_module.cpp) sits behind ISessions; the policy is game-free.
#include <cstdint>
#include <set>
#include <string>
#include <vector>

#include "split_config.h"

namespace nvsplit {

struct AudioSession {
    std::string id;     // opaque, stable per session (the Core Audio instance identifier)
    uint32_t pid = 0;
    bool muted = false;
};

class ISessions {
public:
    virtual ~ISessions() = default;
    virtual bool list(std::vector<AudioSession>* out) = 0;  // sessions of the default render device
    virtual bool setMute(const std::string& id, bool mute) = 0;
};

class AudioMuter {
public:
    AudioMuter(AudioMode mode, uint32_t selfPid) : mode_(mode), pid_(selfPid) {}
    // One pass. Call about once a second from a worker thread (sessions appear when the game first plays a sound).
    void tick(ISessions& s);
    // Un-mutes everything this muter muted (shutdown, hot reload).
    void release(ISessions& s);
    int mutedCount() const { return static_cast<int>(ours_.size()); }
    uint64_t passes() const { return passes_; }

private:
    AudioMode mode_;
    uint32_t pid_;
    std::set<std::string> ours_;
    uint64_t passes_ = 0;
};

}  // namespace nvsplit
