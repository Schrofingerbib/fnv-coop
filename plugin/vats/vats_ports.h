// plugin/vats: the two ports the game-free VATS logic talks through.
//   IVatsEngine  everything VATS needs from the game (reads of the VATS state, the few engine calls, the time scale, the player's AP and perks).
//                Production: vats_module.cpp (static RE, SEH-guarded, main thread only); tests: a scripted fake engine.
//   IVatsNet     datagram out (the coop MuxTransport; the first byte is already kChannelVats).
#pragma once
#include <cstdint>
#include <string>
#include <vector>
#include "fnvcoop/transport.h"
#include "vats/vats_types.h"

namespace nvcoop::vats {

using fnvcoop::Bytes;

// First byte of every datagram this module sends through the coop MuxTransport (docs/protocol.md section 2 / 15).
constexpr uint8_t kChannelVats = 0xB7;
constexpr uint8_t kVatsModuleWire = 1;

// One selectable body part of the selected target, in the order of the engine's part list (the order Left/Right-style navigation walks).
struct PartInfo {
    uint32_t rec = 0;          // the engine's part record address (an opaque handle; only ever given back to the engine after being re-read from its list)
    int32_t bodyIndex = -1;    // the engine's body-part index (part record +0x20)
    LimbId limb = LimbId::None;
};

// One entry of the engine's VATS target list.
struct TargetInfo {
    uint32_t actorRef = 0;     // the Actor's reference id
    uint32_t rec = 0;          // the target record (opaque handle)
    uint32_t kind = 0;         // record +4 (LIKELY a type; 1 and 2 are special in the engine)
    std::vector<PartInfo> parts;
};

class IVatsEngine {
public:
    virtual ~IVatsEngine() = default;
    // ---- reads (cheap; called every frame) ----
    virtual Mode mode() = 0;                                  // VATSCameraData +8
    virtual bool vatsMenuOpen() = 0;                          // the top menu is the VATS menu (0x420)
    virtual uint32_t selectedTargetRef() = 0;                 // *(0x11F21CC) -> reference id; 0 = none
    virtual std::vector<TargetInfo> targets() = 0;            // the engine's target list (empty outside VATS)
    virtual uint32_t highlightedPartRec() = 0;                // VATSMenu +0x100; 0 = none
    virtual LimbId rememberedLimb() = 0;                      // 0x11A59F0
    virtual int queuedCount() = 0;                            // length of VATSCameraData's queued action list
    virtual float timeScaleCurrent() = 0;                     // 0x11AC3A0
    virtual float timeScaleTarget() = 0;                      // 0x11AC3A4
    virtual uint32_t localPlayerRef() = 0;                    // 0x14
    virtual uint32_t netIdOfRef(uint32_t ref) = 0;            // the actor port's network id of a tracked actor; 0 = not tracked
    virtual int actionPoints() = 0;                           // the local player's current AP; -1 = unknown
    virtual int maxActionPoints() = 0;                        // -1 = unknown
    virtual bool playerHasPerk(const std::string& editorId) = 0;
    virtual bool isPartyRef(uint32_t ref) = 0;                // another player's puppet (or a party companion): never a VATS target
    // ---- engine calls (main thread; false = refused or faulted) ----
    virtual bool commitHighlight(uint32_t partRec, LimbId limb) = 0;   // VATSMenu +0x100 := partRec, 0x11A59F0 := limb (rec must come from targets())
    virtual bool cycleTarget(bool forward) = 0;               // VATSMenu::HandleClick(tile 3 / 2, null)
    virtual bool accept() = 0;                                // 0x705780
    virtual bool unclickLast() = 0;                           // 0x9C88A0
    virtual bool setTimeScale(float scale, bool immediate) = 0;   // TimeGlobal::SetTimeScale
    virtual bool restoreActionPoints(int amount) = 0;         // RestoreActorValue ActionPoints n on the player, capped at the maximum
    virtual bool applyCameraTimes(bool shortened) = 0;        // kill cam setting: shortened = clamp fVATSCamera*Time; false = restore the saved defaults
};

class IVatsNet {
public:
    virtual ~IVatsNet() = default;
    // `peer`: a client sends to kHostPlayer only; the host sends to a slot, or to kNoPlayer = every joined client.
    virtual void send(PlayerId peer, Bytes data) = 0;
};

}  // namespace nvcoop::vats
