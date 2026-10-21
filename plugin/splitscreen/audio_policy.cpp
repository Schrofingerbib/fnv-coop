#include "audio_policy.h"

#include "fnvcoop_mut.h"

// Runtime mutation twins (FNVCOOP_MUT=<n>), numbered after focus_fake.cpp (27..29):
//   30 = also mutes the sessions of other processes (a browser would go silent)
//   31 = player one (AudioMode::Play) mutes too
//   32 = release() leaves the sessions muted
//   33 = release() un-mutes every session of this process, including one the user muted by hand
//   34 = a session that is already muted by someone else is claimed as ours (and then un-muted by release)
namespace nvsplit {

void AudioMuter::tick(ISessions& s) {
    ++passes_;
    std::vector<AudioSession> all;
    if (!s.list(&all)) return;
    const bool wantMute = mode_ == AudioMode::Mute || FNV_MUT(31);
    for (const AudioSession& a : all) {
        if (a.pid != pid_ && !FNV_MUT(30)) continue;
        const bool ourMute = ours_.count(a.id) != 0;
        if (wantMute) {
            if (a.muted && !ourMute && !FNV_MUT(34)) continue;  // somebody else's mute: leave it alone
            if (a.muted && ourMute) continue;
            if (s.setMute(a.id, true)) ours_.insert(a.id);
        } else if (ourMute) {
            if (s.setMute(a.id, false)) ours_.erase(a.id);
        }
    }
}

void AudioMuter::release(ISessions& s) {
    if (FNV_MUT(32)) { ours_.clear(); return; }
    std::vector<AudioSession> all;
    const bool have = s.list(&all);
    if (FNV_MUT(33) && have) {
        for (const AudioSession& a : all) {
            if (a.pid == pid_) s.setMute(a.id, false);
        }
        ours_.clear();
        return;
    }
    for (auto it = ours_.begin(); it != ours_.end();) {
        if (s.setMute(*it, false) || !have) it = ours_.erase(it);
        else ++it;
    }
}

}  // namespace nvsplit
