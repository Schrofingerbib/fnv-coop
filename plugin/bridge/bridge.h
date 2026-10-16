// nvbridge core: JSON-RPC (newline-delimited JSON) over a named pipe, marshalled to the game main
// thread through Bridge::Pump(). Game-free: everything engine-specific is behind Host.
//
// Wire protocol (v1): one JSON object per line (UTF-8, '\n' terminated) in each direction.
//   -> {"id":1,"method":"exec","params":{"cmd":"player.getpos x"}}
//   <- {"id":1,"result":{...}}   or   {"id":1,"error":{"code":-32602,"message":"..."}}
// Responses on one connection come back in request order. Multiple connections are allowed.
#pragma once
#include <cstdint>
#include <memory>
#include <string>

namespace nvcoop::bridge {

struct ExecResult {
    bool ok = false;         // command was accepted/run by the engine
    std::string output;      // captured console output ("" if capture unavailable)
};

// Everything the bridge needs from the game. All methods are called on the main thread, from Pump().
class Host {
public:
    virtual ~Host() = default;
    virtual ExecResult Exec(const std::string& consoleLine) = 0;
    // Issue the load; completion is signalled later through Bridge::NotifyGameLoaded().
    virtual bool LoadSave(const std::string& name, std::string& err) = 0;
    // path: in = requested file ("" = host chooses), out = file written
    // Issue a save; completion is signalled later through Bridge::NotifySaved(path).
    // True if a save with this name already exists (any save dir the game may use).
    virtual bool SaveExists(const std::string& /*name*/) { return false; }
    virtual bool SaveGame(const std::string& name, std::string& err) = 0;
    virtual bool Screenshot(std::string& path, std::string& err) = 0;
    virtual void Quit() = 0;
};

struct Config {
    int instance = 0;                  // pipe is \\.\pipe\nvcoop-<instance>
    std::string autoloadSave;          // "" == disabled
    int autoloadDelayFrames = 300;     // frames after the main menu is ready
    int defaultTimeoutMs = 30000;      // for methods that do not pass timeoutMs
    int quitDelayMs = 300;             // lets the quit reply reach the client first
};

class Bridge {
public:
    Bridge();
    ~Bridge();
    Bridge(const Bridge&) = delete;
    Bridge& operator=(const Bridge&) = delete;

    // Start the pipe server thread(s). Returns false (and logs) if the pipe cannot be created.
    bool Start(const Config& cfg);
    void Stop();

    // Main-thread tick: executes queued requests, advances pending waits, autoload, delayed quit.
    void Pump(Host& host);

    // Lifecycle notifications (main thread).
    void NotifyMainMenuReady();   // e.g. NVSE kMessage_DeferredInit
    void NotifySaved(const std::string& path);  // e.g. NVSE kMessage_SaveGame (data = .fos path)
    void NotifyGameLoaded();      // e.g. NVSE kMessage_PostLoadGame

    uint64_t frames() const;
    static std::string PipeName(int instance);

    struct Impl;
private:
    std::unique_ptr<Impl> impl_;
};

}  // namespace nvcoop::bridge
