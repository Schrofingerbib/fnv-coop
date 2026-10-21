// Test executable: the real bridge core driven by a fake game host (no game needed).
//   nvbridge_fakehost.exe [--instance N] [--autoload NAME] [--autoload-frames N]
// Fake console grammar (Exec, and ExecOn for exec {cmd, ref}):
//   set <name> <number>        store a variable
//   printc "NVCOOP_EVAL=%g" (<name>)   what waitFor{expr} and eval send; prints the stored variable.
//                              (refid) prints the calling reference id, and fails without a calling reference
//   echo <text>                prints <text>
//   event <type> [json]        pushes a bridge event
//   stall <ms>                 blocks the "main thread" (tests main-thread timeouts)
//   getref                     prints REF=<calling reference id, 8 hex digits>; fails without a calling reference
//   player.<line>              runs <line> with the player (00000014) as the calling reference
//   <X>.<word> ...             fails like the game's console: Script command "<X>.<word>" not found
//                              (the console has no "<hexFormID>.Command" form; exec {cmd, ref} names the reference)
//   anything else              ok=false, output "unknown command"
// Load order and forms for exec {cmd, ref}: FalloutNV.esm (index 0), DeadMoney.esm (index 1). Forms: 00000014 the player
// (ACHR), 00103E61 a door (REFR), 01000ABC a creature (ACRE), 0000000F Caps001 (MISC, a form that is not a reference).
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <map>
#include <sstream>
#include <thread>

#include "bridge.h"
#include "nvcoop_api.h"
#include "registry.h"

using namespace nvcoop;

namespace {

bridge::Bridge* g_bridge = nullptr;

class FakeHost : public bridge::Host {
public:
    bridge::ExecResult Exec(const std::string& line) override { return Run(line, 0); }
    int PluginIndex(const std::string& plugin) override {
        static const char* const kPlugins[] = {"FalloutNV.esm", "DeadMoney.esm"};
        for (int i = 0; i < 2; ++i)
            if (_stricmp(plugin.c_str(), kPlugins[i]) == 0) return i;
        return -1;
    }
    int FormType(uint32_t id) override {
        switch (id) {
            case 0x00000014: return 0x3B;  // ACHR: the player
            case 0x00103E61: return 0x3A;  // REFR: a door
            case 0x01000ABC: return 0x3C;  // ACRE: a creature of DeadMoney.esm
            case 0x0000000F: return 0x1F;  // MISC: Caps001, a form but not a reference
            default: return -1;
        }
    }
    bridge::ExecResult ExecOn(const std::string& line, uint32_t id) override { return Run(line, id); }

    // One console line; `self` is the calling reference (0 = none).
    bridge::ExecResult Run(const std::string& line, uint32_t self) {
        std::istringstream in(line);
        std::string cmd;
        in >> cmd;
        bridge::ExecResult r;
        const size_t dot = cmd.find('.');
        if (dot != std::string::npos && dot > 0) {
            if (_stricmp(cmd.substr(0, dot).c_str(), "player") == 0) return Run(line.substr(line.find(cmd) + dot + 1), 0x14);
            r.output = "Script command \"" + cmd + "\" not found";  // the game's reply to "<hexFormID>.Command"
            return r;
        }
        if (cmd == "getref") {
            if (!self) {
                r.output = "getref: no calling reference";
                return r;
            }
            char buf[32];
            std::snprintf(buf, sizeof buf, "REF=%08X", self);
            r.ok = true;
            r.output = buf;
            return r;
        }
        if (cmd == "set") {
            std::string name;
            double v = 0;
            in >> name >> v;
            vars_[name] = v;
            r.ok = true;
        } else if (cmd == "printc") {
            size_t lp = line.find('('), rp = line.rfind(')');
            std::string name = (lp != std::string::npos && rp > lp) ? line.substr(lp + 1, rp - lp - 1) : "";
            if (name == "refid" && !self) {
                r.output = "refid: no calling reference";
                return r;
            }
            char buf[64];
            std::snprintf(buf, sizeof buf, "NVCOOP_EVAL=%g", name == "refid" ? static_cast<double>(self) : vars_.count(name) ? vars_[name] : 0.0);
            r.ok = true;
            r.output = buf;
        } else if (cmd == "echo") {
            r.ok = true;
            std::getline(in >> std::ws, r.output);
        } else if (cmd == "event") {
            std::string type, data;
            in >> type;
            std::getline(in >> std::ws, data);
            PushEvent(type.c_str(), data);
            r.ok = true;
        } else if (cmd == "stall") {
            int ms = 0;
            in >> ms;
            std::this_thread::sleep_for(std::chrono::milliseconds(ms));
            r.ok = true;
        } else {
            r.output = "unknown command";
        }
        return r;
    }
    bool LoadSave(const std::string& name, std::string& err) override {
        if (name == "missing") { err = "no such save"; return false; }
        loadIn_ = 5;
        return true;
    }
    bool SaveExists(const std::string& name) override { return name == "existing"; }
    bool SaveGame(const std::string& name, std::string& err) override {
        if (name == "missing") { err = "cannot save"; return false; }
        saveIn_ = 5;
        saveName_ = name;
        return true;
    }
    bool Screenshot(std::string&, std::string& err) override { err = "screenshot not implemented"; return false; }
    void Quit() override { std::exit(0); }

    void Tick() {
        if (loadIn_ > 0 && --loadIn_ == 0) g_bridge->NotifyGameLoaded();
        if (saveIn_ > 0 && --saveIn_ == 0) g_bridge->NotifySaved("Saves\\" + saveName_ + ".fos");
    }

private:
    std::map<std::string, double> vars_;
    int loadIn_ = 0;
    int saveIn_ = 0;
    std::string saveName_;
};

bool EchoMethod(const std::string& params, std::string& out) {
    out = "{\"echo\":" + (params.empty() ? std::string("null") : params) + "}";
    return true;
}
bool FailMethod(const std::string&, std::string& out) {
    out = "deliberate failure";
    return false;
}
std::string FakeState(const std::string& sections) { return "{\"sections\":\"" + sections + "\"}"; }

}  // namespace

int main(int argc, char** argv) {
    bridge::Config cfg;
    cfg.quitDelayMs = 100;
    bool withState = false;
    for (int i = 1; i < argc; ++i) {
        if (!std::strcmp(argv[i], "--instance") && i + 1 < argc) cfg.instance = std::atoi(argv[++i]);
        else if (!std::strcmp(argv[i], "--autoload") && i + 1 < argc) cfg.autoloadSave = argv[++i];
        else if (!std::strcmp(argv[i], "--autoload-frames") && i + 1 < argc) cfg.autoloadDelayFrames = std::atoi(argv[++i]);
        else if (!std::strcmp(argv[i], "--state-provider")) withState = true;
        else if (!std::strcmp(argv[i], "--default-timeout") && i + 1 < argc) cfg.defaultTimeoutMs = std::atoi(argv[++i]);
    }
    registry::SetLogFile("nvbridge_fakehost.log");
    RegisterMethod("test.echo", EchoMethod, true);
    RegisterMethod("test.echoThread", EchoMethod, false);
    RegisterMethod("test.fail", FailMethod, true);
    if (withState) SetStateProvider(FakeState);

    bridge::Bridge b;
    g_bridge = &b;
    if (!b.Start(cfg)) return 2;
    std::printf("READY %s\n", bridge::Bridge::PipeName(cfg.instance).c_str());
    std::fflush(stdout);

    FakeHost host;
    int ticks = 0;
    for (;;) {
        std::this_thread::sleep_for(std::chrono::milliseconds(5));
        if (++ticks == 20) b.NotifyMainMenuReady();
        host.Tick();
        b.Pump(host);
    }
}
