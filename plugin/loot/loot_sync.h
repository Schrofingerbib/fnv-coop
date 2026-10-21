// LootSync: the loot service of one machine. A live-hub service (plugin/coop/live/live_hub.h) on channel kChannelLoot (0xB1).
//
//   host machine : LootHost (the table) + LootClient (its own player) joined in-process. The client's requests call the host directly, the
//                  host's answers to slot 0 go into a local inbox that is pumped at the end of every entry point (no re-entrancy).
//   guest        : LootClient only; requests and answers travel over the hub's link (reliable, except clutter snapshots).
//
// What this class adds on top of synccore/loot (all of it unit-tested against a fake engine in plugin/loot/tests, none of it run in a game):
//   * the container watcher: while the engine says a container menu is up, the engine copy is read every pollMs and every difference becomes a
//     take / put request; it is also read BEFORE every message from the host is handled (a correction would swallow an unread change);
//   * action execution (rollback of the player's inventory, replacing a container's contents, closing a menu, binding / creating / removing
//     world items, clutter poses) with the engine, re-reading the engine copy after a correction so the watcher does not see it as a player move;
//   * registering containers when their cell loads (the first resolver's real items become the shared ones) and corpses shortly after death;
//   * the host's engine sweep (a script changed an unheld container: the table follows) and every machine's audit of its closed containers;
//   * owned clutter sampling; save / load of the host table; status for the bridge.
// Threading: main thread only.
#pragma once
#include <deque>
#include <map>
#include <memory>
#include <set>
#include <string>
#include <vector>
#include "live_hub.h"
#include "loot/loot_types.h"

namespace nvcoop::loot {

class LootSync : public nvcoop::livewire::ILiveService {
public:
    LootSync(ILootEngine& eng, SyncConfig cfg = {});
    ~LootSync() override;

    // ---- ILiveService ----
    const char* name() const override { return "loot"; }
    std::vector<uint8_t> channels() const override { return {kChannelLoot}; }
    void sessionBegin(const nvcoop::livewire::SessionInfo& info, nvcoop::livewire::ILiveLink& link) override;
    void sessionEnd() override;
    void playerJoined(PlayerId p) override;
    void playerLeft(PlayerId p) override;
    void hostLinkUp() override;
    void hostLinkDown() override;
    void receive(PlayerId from, const uint8_t* data, size_t size) override;
    void tick(uint64_t nowMs) override;
    void idleTick(uint64_t nowMs) override;
    void loadBegin() override;
    void loadEnd() override;
    void saved() override {}
    void gameExit() override;

    // ---- engine events (main thread; ignored while no session runs) ----
    void onContainerLoaded(RefId ref);                       // a container's cell attached: resolve it (first resolver wins)
    void onActorDied(NetId actor, RefId ref);                // this machine owns the actor and it died: its corpse becomes a shared container
    void onDrop(RefId dropper, RefId dropped);               // xNVSE OnDrop
    void onPickup(RefId picker, RefId item);                 // the player picked a world item up
    void onTouch(RefId ref);                                 // the local player is pushing a physics object (TODO-RE: no verified detector; bots / glue call it)
    void onCellReset(lc::CellId cell);                       // host: the engine reset a cell (TODO-RE: no verified hook)

    // ---- host save sidecar ----
    std::vector<uint8_t> saveState() const;
    bool loadState(const std::vector<uint8_t>& bytes);       // before the host session starts, or while it runs

    // ---- status ----
    bool inSession() const { return link_ != nullptr; }
    bool isHost() const { return host_ != nullptr; }
    const Stats& stats() const { return stats_; }
    const SyncConfig& config() const { return cfg_; }
    lc::LootClient* client() { return client_.get(); }
    lc::LootHost* host() { return host_.get(); }
    NetId openNet() const { return menuNet_; }
    std::string statusJson() const;
    void setCellOwnerHook(std::function<bool(lc::CellId)> f) { ownsCell_ = std::move(f); }   // true: this machine resolves that cell's containers right away

private:
    void makeClient(PlayerId self);
    void sendUp(const lc::Body& b);
    void deliver(lc::LootHost::Out& out);
    void pump();                                             // local inbox -> client, then actions
    void poll();                                             // read the open container now
    void pollMenu(uint64_t now);
    void openMenu(RefId ref);
    void closeMenu();
    void run(const std::vector<lc::Action>& acts);
    void drain();
    void applyContents(NetId net, const Items& want);
    void tickResolve(uint64_t now);
    void tickCreates(uint64_t now);
    void tickSweep(uint64_t now);
    void tickAudit(uint64_t now);
    void tickClutter(uint64_t now);
    bool readExact(NetId net, Items& out);

    struct Resolve { RefId ref = 0; bool corpse = false; NetId actor = 0; uint64_t due = 0; int tries = 0; bool deferredForOwner = false; };

    ILootEngine& eng_;
    SyncConfig cfg_;
    Stats stats_;
    nvcoop::livewire::ILiveLink* link_ = nullptr;
    PlayerId self_ = kHostPlayer;
    std::unique_ptr<lc::LootHost> host_;
    std::unique_ptr<lc::LootClient> client_;
    std::deque<lc::Body> inbox_;
    bool pumping_ = false;
    bool linkUp_ = false;
    uint64_t now_ = 0;
    std::vector<uint8_t> pendingSidecar_;
    // container menu watcher
    RefId menuRef_ = 0;
    NetId menuNet_ = 0;
    uint64_t lastPollMs_ = 0;
    std::set<NetId> suppressClose_;                          // menus WE closed (busy, lock lost): the close is not a player close
    // scheduled work
    std::deque<Resolve> resolves_;
    std::vector<lc::WorldItem> creates_;
    std::vector<uint64_t> createsDue_;
    std::set<NetId> ownedClutter_;
    std::function<bool(lc::CellId)> ownsCell_;
    uint64_t nextSweep_ = 0, nextAudit_ = 0, nextClutter_ = 0;
    size_t sweepCursor_ = 0, auditCursor_ = 0;
};

}  // namespace nvcoop::loot
