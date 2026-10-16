// Applies the NV:MP 9.4 client.dll runtime patches (plugin/nvmp/patches.h) from inside nvcoop.dll.
// Game-free core: module lookup, memory writes and event output are injected, so it is tested against the real
// client.dll image without starting anything. Windows glue (module watcher, ini, RPC) is client_patches_module.cpp.
//
// Lifecycle: client.dll is injected by nvmp_start.exe before NVSE plugins run, so it is normally present at the
// first frame; if it is not (older launch order, delayed load) the patcher keeps polling and is also woken by
// LdrRegisterDllNotification. Patches are only ever applied on the game main thread (OnFrame), never inside the
// loader-lock notification callback.
#pragma once
#include <atomic>
#include <cstdint>
#include <functional>
#include <map>
#include <set>
#include <string>
#include <vector>

#include "../patches.h"

namespace nvmp::client {

struct ModuleView {
    uint8_t* base = nullptr;
    size_t size = 0;  // SizeOfImage
};
class ModuleSource {
public:
    virtual ~ModuleSource() = default;
    // name is matched case-insensitively against the module's file name ("client.dll").
    virtual bool Find(const std::string& name, ModuleView& out) = 0;
};

// (event type, JSON object text) -> nvcoop::PushEvent in the plugin.
using EventSink = std::function<void(const char* type, const std::string& json)>;

struct Settings {
    std::set<std::string> enabled;  // full patch names from PatchTable()
    int pollEveryFrames = 60;       // fallback polling period until the module is found
    int giveUpAfterFrames = 36000;  // ~10 min at 60 fps; then an event and silence
    bool autoApply = true;          // false: only the RPC applies patches
};

// Short names accepted in config: quested, questedRef, questedRefScan, scriptedRef, ghostDamage (both ghost patches).
// `get(key, default)` returns the config string for [nvmp] key. Also accepts `patches=name1,name2` (full or short names).
// Defaults: only `client.quested_not_localized` (the recommended first patch) is enabled.
using ConfigGetter = std::function<std::string(const char* key, const char* def)>;
Settings ParseSettings(const ConfigGetter& get, std::vector<std::string>* warnings = nullptr);
// Full names for a short name (empty if unknown).
std::vector<std::string> ExpandName(const std::string& n);

enum class Phase { Waiting, Done, GaveUp };

class ClientPatcher {
public:
    ClientPatcher(Settings s, ModuleSource& mods, MemoryWriter& writer, EventSink sink);

    // Thread-safe, cheap: called from the DLL-load notification (loader lock held!). Only sets a flag.
    void NotifyModuleLoaded() { notified_.store(true); }

    // Game main thread, every frame.
    void OnFrame();

    // Manual control (RPC, main thread). Returns the status; the patch must be a client.dll patch of the table.
    PatchStatus Apply(const std::string& name);
    PatchStatus Revert(const std::string& name);

    Phase phase() const { return phase_; }
    uint64_t frames() const { return frame_; }
    // JSON: {"phase":..., "module":{...}, "patches":[{"name","status","rva","enabled","applied"}]}
    std::string StatusJson() const;

private:
    bool FindClient(ModuleView& v);
    void ApplyAllEnabled();
    void Emit(const char* type, const std::string& json) { if (sink_) sink_(type, json); }
    PatchStatus DoApply(const PatchDef& d, uint32_t* rvaOut);

    Settings s_;
    ModuleSource& mods_;
    MemoryWriter& writer_;
    EventSink sink_;
    std::atomic<bool> notified_{false};
    Phase phase_ = Phase::Waiting;
    uint64_t frame_ = 0;
    bool announced_ = false;
    ModuleView mod_;
    struct Rec { PatchStatus status = PatchStatus::NotApplied; uint32_t rva = 0; PatchState state; };
    std::map<std::string, Rec> recs_;
};

}  // namespace nvmp::client
