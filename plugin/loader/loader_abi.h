// nvcoop loader <-> core DLL stable C ABI (T3a-loader owns this file; T3b-state builds on it).
//
// Split build (cmake -DNVCOOP_SPLIT_LOADER=ON): nvcoop.dll is a tiny permanent loader (NVSE registration, the
// bridge pipe, console host, crash filter). Everything else lives in the reloadable core DLL
// (nvcoop_mod.dll, built from the old `nvcoop` target). The loader copies the core to a private cache file
// before LoadLibrary so the build can overwrite the original while the game runs.
//
// Rules:
//  * Plain C types only across this boundary: no std::string, no exceptions, no CRT objects (loader and core
//    each have their own static CRT).
//  * Both structs start with {abi_version, size}. New fields are only ever appended; check `size` before use.
//  * Every function the core receives stays valid until the core returns from `shutdown`-time unload.
//  * All core entry points are called on the game main thread, between frames (never from inside a core call).
#pragma once
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define NVLOADER_ABI_VERSION 1u

// Output sink for methods and the state provider: call out->set(out->h, text) once with the result text
// (JSON on success, a human readable message on failure). The text is copied.
typedef struct NvOut {
    void* h;
    void (*set)(void* h, const char* text);
} NvOut;

// Bridge method implemented by the core. Return 1 = ok, 0 = failure (message in out). `user` is the value
// passed at registration.
typedef int (*NvMethodFn)(void* user, const char* paramsJson, NvOut* out);
// State provider (the "state" RPC). Return 1 = ok.
typedef int (*NvStateFn)(const char* sectionsCsv, NvOut* out);

// ---- loader -> core ----
typedef struct NvLoaderApi {
    uint32_t abi_version;          // NVLOADER_ABI_VERSION
    uint32_t size;                 // sizeof(NvLoaderApi) as the loader built it
    uint32_t generation;           // 1 for the first load, +1 per reload
    uint32_t is_reload;            // 0 = first load of this process, 1 = hot reload (blobs may exist)
    const char* game_dir;          // with trailing backslash
    void* nvse_interface;          // NVSEInterface* from NVSEPlugin_Load (valid for the whole process)

    void (*log)(const char* line);
    void (*push_event)(const char* type, const char* dataJson);   // dataJson may be NULL
    // Register a bridge method. Methods and the state provider are removed by the loader before unload, so the
    // core does not need to unregister them. mainThread != 0: runs from the game main loop.
    void (*register_method)(const char* name, NvMethodFn fn, void* user, int mainThread);
    void (*set_state_provider)(NvStateFn fn);

    // Blob store owned by the loader; survives reloads (not process restarts). `version` is opaque to the
    // loader. blob_get copies min(cap, len) bytes, always sets *len to the full length, returns 1 if the key
    // exists (len and version are optional), 0 if not.
    int (*blob_put)(const char* key, const void* data, uint32_t len, uint32_t version);
    int (*blob_get)(const char* key, void* buf, uint32_t cap, uint32_t* len, uint32_t* version);
    void (*blob_erase)(const char* key);

    // Ask for a reload; performed by the loader at the next frame boundary (safe to call from any thread).
    void (*request_reload)(const char* reason);
    // In-process access to the bridge method registry (methods of the loader, of any core module, dev.*).
    // has_method: 1 if registered. call_method: -1 = no such method, else the method's own 1/0 result, with
    // its text in `out`. Runs the method on the calling thread (call from the main thread for main-thread methods).
    int (*has_method)(const char* name);
    int (*call_method)(const char* name, const char* paramsJson, NvOut* out);
    // Cosave (NVSE serialization) callbacks of the current core; NULL clears. NVSE accepts serialization callbacks only during the
    // first plugin load and can never drop them, so the loader registers its own once and forwards to these, and clears them
    // before a core is unloaded. `intfc` is the NVSESerializationInterface*. (A core that called GetPluginHandle / SetSaveCallback
    // itself crashed NVSE on a hot reload, and its callbacks would have pointed into the unloaded image.)
    void (*set_cosave)(void (*save)(void* intfc), void (*load)(void* intfc));
    // Appended fields go below this line.
} NvLoaderApi;

// ---- core -> loader ----
typedef struct NvCoreApi {
    uint32_t abi_version;
    uint32_t size;
    // Called once after load. Return 1 = ok. For a hot reload, restore from blobs here (api->is_reload).
    int (*init)(void);
    // Same meaning as NVSEMessagingInterface::Message (type, data, dataLen). Includes the per-frame message
    // (kMessage_MainGameLoop) only through on_frame, as in the monolith.
    void (*on_message)(uint32_t type, void* data, uint32_t len);
    void (*on_frame)(void);
    // Called before unload, main thread, between frames. The core must: stop its threads, close or hand over
    // sockets, uninstall every engine hook (restoring original bytes), write its state with api->blob_put.
    // Return 1 = ready to unload. Return 0 = veto (reason via api->log); the loader keeps the core loaded
    // (the core must then still be fully functional).
    int (*before_unload)(void);
    // Appended fields go below this line.
} NvCoreApi;

// Exports of the core DLL (`__declspec(dllexport)`, undecorated C names; see nvcoop_core_exports in loader).
//   int NvCore_Init(const NvLoaderApi* api, NvCoreApi* out);   // fill `out`, return 1 on success
// The loader checks api->abi_version == NVLOADER_ABI_VERSION and out->size >= offsetof(NvCoreApi, before_unload)+ptr.
typedef int (*NvCoreInitFn)(const NvLoaderApi* api, NvCoreApi* out);
#define NVCORE_INIT_EXPORT "NvCore_Init"

#ifdef __cplusplus
}
#endif
