// nvcoop hot-reload loader, game-free part (T3a-loader). No NVSE, no engine: usable from the real plugin
// (main.cpp built with NVCOOP_LOADER) and from host-process tests that load a stub or the real core DLL.
//
// One CoreHost per process. It owns: the private cache copy of the core DLL, the load/unload/reload
// sequence, the blob store that survives reloads, the in-flight gate for bridge calls, an optional file
// watcher, and the bridge methods dev.reload / dev.status.
//
// Config mirror: the core's modules read nvcoop.ini and their data files from the folder of their OWN module
// file. The core runs from the cache folder, so before every LoadLibrary (first load, reload and rollback) the
// host copies the config files (see IsCoreConfigFile) from configDir into the cache folder, and deletes cache
// copies whose source is gone. Edits made between reloads therefore reach the next core.
//
// Threading: Load, Reload, Service, OnFrame, OnMessage and Shutdown must be called on the game main thread
// between frames. RequestReload may be called from any thread.
#pragma once
#include <atomic>
#include <cstdint>
#include <map>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include "loader_abi.h"

namespace nvcoop::loader {

struct HostEnv {
    std::string gameDir;     // with trailing backslash ("" in tests)
    std::string corePath;    // the DLL the build writes, e.g. ...\Data\NVSE\Plugins\nvcoop_mod.dll
    std::string cacheDir;    // "" = <dir of corePath>\nvcoop_cache
    std::string configDir;   // where nvcoop.ini and nvcoop_*.json live; "" = <gameDir>Data\NVSE\Plugins\ (the loader's
                             // folder), or the dir of corePath when gameDir is "" (tests)
    void* nvse = nullptr;    // NVSEInterface*, passed through to the core
};

// True for the file names the loader mirrors into the cache folder: nvcoop.ini, nvcoop_*.json,
// nvcoop_consent_messages.txt and nvcoop_volatile_keys.txt (case-insensitive). These are the files the core's modules
// read beside their module.
bool IsCoreConfigFile(const std::string& fileName);

struct ReloadResult {
    bool ok = false;
    bool rolledBack = false;   // the new core failed and the previous one is running again
    bool coreDead = false;     // nothing is running (rollback failed too)
    std::string error;
    uint32_t generation = 0;   // generation now running (0 if dead)
    double msUnload = 0, msLoad = 0;
};

class CoreHost {
public:
    explicit CoreHost(HostEnv env);
    ~CoreHost();
    CoreHost(const CoreHost&) = delete;
    CoreHost& operator=(const CoreHost&) = delete;

    // First load (generation 1). Also registers dev.reload / dev.status with the bridge. Safe to call again
    // after a failure (e.g. the core was not built yet).
    bool Load(std::string& err);
    ReloadResult Reload(const char* reason);
    // Frame boundary: performs a pending reload request (from RequestReload, the watcher or dev.reload).
    // Returns true if a reload ran.
    bool Service();
    void OnFrame();                                         // Service() then core on_frame
    void OnMessage(uint32_t type, void* data, uint32_t len);
    // Orderly unload without a replacement (tests, or a future dev.unload). Same quiesce sequence as Reload.
    bool Unload(std::string& err);
    // Process is exiting: stops the watcher. Does not unload the core (the OS does).
    void Shutdown();

    void RequestReload(const char* reason);
    bool StartWatcher(int intervalMs);
    void StopWatcher();

    bool loaded() const { return loaded_; }
    uint32_t generation() const { return generation_; }
    uint32_t reloadCount() const { return reloadCount_; }
    const std::string& cachePathInUse() const { return cachePath_; }
    std::string StatusJson() const;   // includes configDir and configFiles (the names copied by the last load)

    // ---- internal: api callbacks need these ----
    struct Gate {
        std::atomic<int> inflight{0};
        std::atomic<bool> open{true};
    };
    struct Blob {
        std::vector<uint8_t> data;
        uint32_t version = 0;
    };
    std::shared_ptr<Gate> gate() const { return gate_; }
    std::mutex blobMu;
    std::map<std::string, Blob> blobs;
    std::atomic<bool> reloadPending{false};
    std::string pendingReason;
    std::mutex pendingMu;
    NvStateFn stateFn = nullptr;
    NvLoaderApi api{};

private:
    struct LoadedCore {
        void* module = nullptr;       // HMODULE
        std::string cachePath;
        NvCoreApi core{};
    };
    bool LoadCoreFrom(const std::string& sourcePath, bool isReload, uint32_t generation, LoadedCore& out,
                      std::string& err);
    void UnloadCore(LoadedCore& c);
    void CleanStaleCache();
    void SyncConfig();
    std::string MakeCachePath(uint32_t generation) const;
    void FillApi(uint32_t generation, bool isReload);
    void RegisterDevMethods();

    HostEnv env_;
    LoadedCore cur_;
    std::string cachePath_;
    std::vector<std::string> configFiles_;
    std::shared_ptr<Gate> gate_;
    bool loaded_ = false;
    bool devRegistered_ = false;
    uint32_t generation_ = 0;
    uint32_t reloadCount_ = 0;
    int depth_ = 0;                   // > 0 while inside a core call
    ReloadResult last_;
    std::string lastReason_;

    std::thread watcher_;
    std::atomic<bool> stopWatcher_{false};
};

CoreHost* Current();

}  // namespace nvcoop::loader
