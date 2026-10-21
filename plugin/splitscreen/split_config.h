#pragma once
// Split-screen settings of ONE game copy: the [splitscreen] section of nvcoop.ini (docs/tools/splitscreen.md). Game-free, no Win32.
//
//   [splitscreen]
//   enable=1        master switch, default 0 (everything below is inert when it is 0)
//   player=0        0-based seat of this copy (0 = player one, the host)
//   players=2       how many copies share the screen (1..4)
//   pad=none        physical XInput pad this copy answers to: 0..3, or none. Missing = leave XInput alone (no routing).
//   kbm=1           1 = this copy is meant to be driven by the keyboard and mouse. Default: 1 for player 0, else 0.
//   ghostPad=0      1 = answer virtual pad 0 with a neutral "connected" pad when no real pad is assigned or the assigned one is
//                   unplugged. Default: the opposite of kbm. Why: FalloutNV.exe reads KB/M for gameplay only while no pad answers
//                   (docs/tools/splitscreen.md, "Input facts"), so a copy that must ignore KB/M keeps a neutral pad plugged in.
//   audio=mute      play | mute. Default: play for player 0, mute for the others.
//   focusFake=1     GetActiveWindow answers with the game window when the copy is not the foreground window.
//   devInject=0     1 = register the split.inject test method (a synthetic physical pad, for the smoke playbook). Never on by default.
#include <string>

namespace nvsplit {

enum class AudioMode { Play, Mute };

struct Config {
    bool enable = false;
    int player = 0;
    int players = 1;
    // -2 = not set (XInput is left alone), -1 = no pad, 0..3 = physical pad
    int pad = -2;
    bool kbm = true;
    bool ghostPad = false;
    AudioMode audio = AudioMode::Play;
    bool focusFake = true;
    bool devInject = false;

    bool routesPad() const { return enable && pad != -2; }
};

// Parses the [splitscreen] section of nvcoop.ini text. Other sections and unknown keys are ignored (case-insensitive).
// Returns false and fills *err when a value is invalid; *out then holds the defaults with enable=false, so a bad file never
// routes input by accident. Missing keys take the defaults documented above.
bool ParseConfig(const std::string& iniText, Config* out, std::string* err);

}  // namespace nvsplit
