// nvcoop public in-process API (owned by L1). Other lanes include this to talk to nvbridge
// and to hook into the plugin lifecycle. Game-free: does not include any NVSE header.
//
//  - PushEvent():          append to the event ring buffer read by the "events" RPC.
//  - SetStateProvider():   L7 registers the function behind the "state" RPC.
//  - RegisterMethod():     add a custom JSON-RPC method (runs on the game main thread by default).
//  - NVCOOP_MODULE():      static registration of load/message/frame callbacks; sources added to the
//                          nvcoop target by a lane's own CMakeLists.txt get them called automatically.
#pragma once
#include <cstdint>
#include <string>

namespace nvcoop {

constexpr int kBridgeProtocolVersion = 1;

// ---- events ---------------------------------------------------------------
// type: short dotted name, e.g. "quest.setstage". dataJson: a JSON value text ("" == null).
// Thread-safe; callable from any thread. Never blocks on I/O.
void PushEvent(const char* type, const std::string& dataJson = std::string());

// ---- state (L7) -----------------------------------------------------------
// Called on the main thread. sectionsCsv is "" for "all". Returns JSON text of the snapshot.
using StateProvider = std::string (*)(const std::string& sectionsCsv);
void SetStateProvider(StateProvider fn);

// ---- custom methods -------------------------------------------------------
// paramsJson: JSON text of "params" (object or null). On success write JSON text of the result to
// `out` and return true; on failure write a human readable message to `out` and return false.
using MethodFn = bool (*)(const std::string& paramsJson, std::string& out);
// mainThread=true: handler is invoked from the game main loop (safe for engine calls).
// Replaces an existing registration of the same name. Built-in methods cannot be overridden.
void RegisterMethod(const char* name, MethodFn fn, bool mainThread = true);

// ---- module lifecycle -----------------------------------------------------
// nvseInterface is the NVSEInterface* passed to NVSEPlugin_Load (cast it yourself).
// onMessage mirrors NVSEMessagingInterface::Message (type, data, dataLen).
struct Module {
    const char* name;
    void (*onLoad)(void* nvseInterface);                         // optional
    void (*onMessage)(uint32_t type, void* data, uint32_t len);  // optional
    void (*onFrame)();                                           // optional, main thread, every frame
};
void RegisterModule(const Module& m);

struct ModuleRegistrar {
    explicit ModuleRegistrar(const Module& m) { RegisterModule(m); }
};
#define NVCOOP_MODULE(ident, ...) \
    static const ::nvcoop::ModuleRegistrar nvcoop_module_##ident(::nvcoop::Module{__VA_ARGS__})

// ---- logging --------------------------------------------------------------
void Log(const char* fmt, ...);

}  // namespace nvcoop
