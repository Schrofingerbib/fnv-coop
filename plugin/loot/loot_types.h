// plugin/loot: loot, containers, dropped items and physics clutter inside the plugin (FULL-SYNC-PLAN 3.5). Shared types and the engine port.
// Game-free. The engine is reached only through ILootEngine (in-game: loot_engine_real.cpp over xNVSE; tests: a fake world), the network only
// through the live hub's ILiveLink (plugin/coop/live/live_hub.h; tests: an in-memory link).
#pragma once
#include <cstdint>
#include <functional>
#include <string>
#include <vector>
#include "loot/loot.h"

namespace nvcoop::loot {

namespace lc = fnvcoop::loot;
using lc::Items;
using lc::NetId;
using fnvcoop::FormRef;
using fnvcoop::PlayerId;
using fnvcoop::kHostPlayer;
using fnvcoop::kNoPlayer;

using RefId = uint32_t;                 // runtime form id of a loaded reference (xNVSE refID); 0 = none
constexpr RefId kPlayerRef = 0x14;
constexpr uint8_t kChannelLoot = lc::kChannelLoot;

struct ContainerDesc {
    NetId net = 0;                      // 0 = not a syncable container (not lootable, or no identity can be derived)
    lc::ContainerKind kind = lc::ContainerKind::Container;
    lc::CellId cell = 0;
    lc::Vec3 pos;
};

struct ItemDesc {
    NetId net = 0;                      // placed: derived from the ref. dropped: the issued id it was bound to. 0 = unknown / not an item
    lc::WorldItem item;                 // base, count, meta, cell, pos, rot (id = net)
    bool boundDrop = false;             // a dropped item that carries an issued id
};

class ILootEngine {
public:
    virtual ~ILootEngine() = default;
    virtual bool worldReady() = 0;                                              // a game is loaded (false in menus / loading)
    virtual uint32_t worldEpoch() = 0;                                          // changes on every load / new game

    // ---- containers and corpses ----
    virtual bool describeContainer(RefId ref, ContainerDesc& out) = 0;          // false: not a lootable container / corpse
    virtual RefId refOfContainer(NetId net) = 0;                                // loaded ref of a known container; 0 = not loaded here
    // The engine copy's contents as TOTALS (base container + changes). exact=false: the read is partial (an unresolved leveled list is in it, or
    // the totals call was not available): such a read is never offered as the shared contents.
    virtual bool readContents(NetId net, Items& out, bool& exact) = 0;
    virtual bool setContents(NetId net, const Items& want) = 0;                 // make the engine copy hold exactly `want` (diff, then add / remove)
    virtual RefId openMenuRef() = 0;                                            // the container ref whose menu is up right now; 0 = no container menu
    virtual bool closeMenu() = 0;                                               // close the container menu (TODO-RE in the real engine)
    virtual bool adjustPlayer(const FormRef& base, int32_t delta) = 0;          // add / remove items of the LOCAL player; false when it could not
    virtual void notify(lc::Notice n, NetId id) = 0;                            // a short message to the player
    virtual void loadedContainers(std::vector<NetId>& out, size_t max) = 0;     // containers with a loaded ref here (audit / host sweep), at most `max`

    // ---- world items ----
    virtual bool describeItem(RefId ref, ItemDesc& out) = 0;
    virtual RefId createItem(const lc::WorldItem& it) = 0;                      // make a world ref for a remote drop; 0 = failed (cell not loaded: retried by the caller)
    virtual void bindItem(RefId ref, NetId net) = 0;                            // the local world ref IS item `net`
    virtual bool removeItem(NetId net) = 0;                                     // delete the world ref of item `net` (it was taken)

    // ---- clutter ----
    virtual bool readPose(NetId net, lc::Vec3& pos, lc::Vec3& rot) = 0;
    virtual bool setPose(NetId net, const lc::Vec3& pos, const lc::Vec3& rot, bool moving) = 0;
};

struct SyncConfig {
    bool enabled = false;
    uint32_t pollMs = 50;               // open container menu: engine copy read this often
    uint32_t corpseDelayMs = 700;       // after an actor died: its inventory has settled, register the corpse
    uint32_t resolveRetryMs = 1500;     // a container whose read was not exact is tried again after this long
    uint32_t sweepPerTick = 6;          // host: containers compared with the table per tick (scripts change them)
    uint32_t sweepEveryMs = 500;
    uint32_t auditPerTick = 6;          // every machine: engine copies of closed containers compared with the shared contents
    uint32_t auditEveryMs = 500;
    uint32_t clutterEveryMs = 66;       // owned clutter is sampled this often
    uint32_t createRetryMs = 1000;      // a remote drop whose cell is not loaded is retried
    uint32_t maxPendingCreates = 256;
    bool resolveOnLoad = true;          // register containers when their cell loads (the owner's roll becomes the shared one)
    bool hostSweep = true;
    lc::HostConfig host;
    lc::ClientConfig client;
};

struct Stats {
    uint64_t ticks = 0, datagramsIn = 0, datagramsOut = 0, undecodable = 0, spoofed = 0, wrongDirection = 0;
    uint64_t menusOpened = 0, menusClosed = 0, polls = 0, readFailures = 0, inexactReads = 0;
    uint64_t actionsRun = 0, actionsFailed = 0, rollbacksApplied = 0;
    uint64_t containersResolved = 0, corpsesResolved = 0, resolveRetries = 0;
    uint64_t dropsSeen = 0, dropsIgnored = 0, pickupsSeen = 0, pickupsIgnored = 0, createsDeferred = 0, createsDropped = 0;
    uint64_t sweepsCompared = 0, sweepsChanged = 0, auditsCompared = 0;
    uint64_t sessions = 0, loads = 0;
};

}  // namespace nvcoop::loot
