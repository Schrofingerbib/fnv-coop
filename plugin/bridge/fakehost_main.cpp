// Test executable: the real bridge core driven by a fake game host (no game needed).
//   nvbridge_fakehost.exe [--instance N] [--autoload NAME] [--autoload-frames N]
// Fake console grammar (Exec):
//   set <name> <number>        store a variable
//   printc "NVCOOP_EVAL=%g" (<name>)   what waitFor{expr} sends; prints the stored variable
//   echo <text>                prints <text>
//   event <type> [json]        pushes a bridge event
//   stall <ms>                 blocks the "main thread" (tests main-thread timeouts)
//   anything else              ok=false, output "unknown command"
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
    bridge::ExecResult Exec(const std::string& line) override {
        std::istringstream in(line);
        std::string cmd;
        in >> cmd;
        bridge::ExecResult r;
        if (cmd == "set") {
            std::string name;
            double v = 0;
            in >> name >> v;
            vars_[name] = v;
            r.ok = true;
        } else if (cmd == "printc") {
            size_t lp = line.find('('), rp = line.rfind(')');
            std::string name = (lp != std::string::npos && rp > lp) ? line.substr(lp + 1, rp - lp - 1) : "";
            char buf[64];
            std::snprintf(buf, sizeof buf, "NVCOOP_EVAL=%g", vars_.count(name) ? vars_[name] : 0.0);
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
