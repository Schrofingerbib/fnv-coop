#include "loot/loot_sync.h"

#include <algorithm>
#include <cstdio>
#include "fnvcoop_mut.h"
#include "loot/loot_wire.h"

namespace nvcoop::loot {

LootSync::LootSync(ILootEngine& eng, SyncConfig cfg) : eng_(eng), cfg_(cfg) {}
LootSync::~LootSync() = default;

// ================================================================================================================ session

void LootSync::makeClient(PlayerId self) {
    lc::ClientConfig cc = cfg_.client;
    cc.self = self;
    client_ = std::make_unique<lc::LootClient>(cc, [this](const lc::Body& b) { sendUp(b); });
}

void LootSync::sessionBegin(const nvcoop::livewire::SessionInfo& info, nvcoop::livewire::ILiveLink& link) {
    if (!cfg_.enabled) return;
    link_ = &link;
    self_ = info.isHost ? kHostPlayer : info.self;
    ++stats_.sessions;
    inbox_.clear();
    menuRef_ = 0; menuNet_ = 0;
    suppressClose_.clear();
    ownedClutter_.clear();
    if (!FNV_MUT(153)) { resolves_.clear(); creates_.clear(); createsDue_.clear(); }
    host_.reset();
    if (info.isHost) {
        host_ = std::make_unique<lc::LootHost>(cfg_.host);
        if (!pendingSidecar_.empty()) {
            host_->load(pendingSidecar_);
            pendingSidecar_.clear();
        }
    }
    makeClient(self_);
    if (info.isHost) {
        linkUp_ = true;
        client_->hostLinkUp(now_);   // the host's own client: its table request is answered in-process
        pump();
    } else {
        linkUp_ = false;             // a guest waits for hostLinkUp()
    }
}

void LootSync::sessionEnd() {
    host_.reset();
    client_.reset();
    link_ = nullptr;
    linkUp_ = false;
    inbox_.clear();
    menuRef_ = 0; menuNet_ = 0;
    ownedClutter_.clear();
    if (!FNV_MUT(153)) { resolves_.clear(); creates_.clear(); createsDue_.clear(); }   // twin 153: scheduled work of a dead session survives into the next one
}

void LootSync::playerJoined(PlayerId p) {
    if (!host_) return;
    lc::LootHost::Out out;
    host_->playerJoined(p, out);
    deliver(out);
}

void LootSync::playerLeft(PlayerId p) {
    if (!host_ || FNV_MUT(157)) return;   // twin 157: a leaver's locks and clutter stay
    lc::LootHost::Out out;
    host_->playerLeft(p, now_, out);
    deliver(out);
    pump();
}

void LootSync::hostLinkUp() {
    if (!client_ || host_) return;
    linkUp_ = true;
    client_->hostLinkUp(now_);
    pump();
}

void LootSync::hostLinkDown() {
    if (!client_ || host_) return;
    linkUp_ = false;
    if (!FNV_MUT(156)) client_->hostLinkDown();   // twin 156: pending requests are never abandoned
    pump();
}

void LootSync::loadBegin() {
    ++stats_.loads;
    menuRef_ = 0; menuNet_ = 0;
    suppressClose_.clear();
    ownedClutter_.clear();
    resolves_.clear(); creates_.clear(); createsDue_.clear();
    inbox_.clear();
    if (client_ && !FNV_MUT(152)) client_->worldReset();   // twin 152: the view (and its "already has a ref" memory) survives a load
}

void LootSync::loadEnd() {
    if (!client_) return;
    if (linkUp_) {
        client_->hostLinkUp(now_);   // fetch the table again; world items get refs again
        pump();
    }
}

void LootSync::gameExit() { loadBegin(); }

// ================================================================================================================ network

void LootSync::sendUp(const lc::Body& b) {
    if (host_) {
        lc::LootHost::Out out;
        host_->onRequest(kHostPlayer, b, now_, out);
        deliver(out);
        return;
    }
    if (!link_) return;
    lc::Envelope e;
    e.src = self_;
    e.body = b;
    std::vector<uint8_t> bytes = lc::encodeEnvelope(e);
    if (bytes.empty()) return;
    ++stats_.datagramsOut;
    link_->send(kHostPlayer, !lc::isUnreliable(b), std::move(bytes));
}

void LootSync::deliver(lc::LootHost::Out& out) {
    for (auto& o : out) {
        if (o.to == kHostPlayer) {
            if (!FNV_MUT(149)) inbox_.push_back(std::move(o.body));   // twin 149: the host's own answers are dropped
            continue;
        }
        if (!link_) continue;
        lc::Envelope e;
        e.src = kHostPlayer;
        e.body = std::move(o.body);
        std::vector<uint8_t> bytes = lc::encodeEnvelope(e);
        if (bytes.empty()) continue;
        ++stats_.datagramsOut;
        link_->send(o.to, !lc::isUnreliable(e.body), std::move(bytes));
    }
    out.clear();
}

void LootSync::receive(PlayerId from, const uint8_t* data, size_t size) {
    if (!link_ || !client_) return;
    ++stats_.datagramsIn;
    lc::Envelope e;
    if (!lc::decodeEnvelope(data, size, e)) { ++stats_.undecodable; return; }
    if (host_) {
        // the slot is the transport peer's, never the one the datagram claims; a request that claims another sender is counted
        if (e.src != from) ++stats_.spoofed;
        const PlayerId who = FNV_MUT(146) ? e.src : from;   // twin 146: the claimed sender is believed (a guest can act as another player)
        if (!lc::isRequest(e.body)) { ++stats_.wrongDirection; return; }
        poll();
        lc::LootHost::Out out;
        host_->onRequest(who, e.body, now_, out);
        deliver(out);
        pump();
        return;
    }
    if (from != kHostPlayer) { ++stats_.spoofed; return; }   // a guest only listens to the host
    if (lc::isRequest(e.body) && !std::holds_alternative<lc::MClutter>(e.body)) { ++stats_.wrongDirection; return; }
    if (!FNV_MUT(141)) poll();   // twin 141: the container is not read before a correction from the host is handled (it can swallow the player's last move)
    client_->onMessage(e.body, now_);
    drain();
}

void LootSync::pump() {
    if (pumping_ || !client_) return;
    pumping_ = true;
    for (int guard = 0; guard < 256 && !inbox_.empty(); ++guard) {
        lc::Body b = std::move(inbox_.front());
        inbox_.pop_front();
        poll();
        client_->onMessage(b, now_);
        drain();
    }
    pumping_ = false;
}

// ================================================================================================================ the container watcher

bool LootSync::readExact(NetId net, Items& out) {
    bool exact = true;
    if (!eng_.readContents(net, out, exact)) { ++stats_.readFailures; return false; }
    if (!exact && !FNV_MUT(145)) { ++stats_.inexactReads; return false; }   // twin 145: a partial read (an unresolved leveled list inside) is offered as the shared contents
    return true;
}

void LootSync::poll() {
    if (!client_ || !menuNet_) return;
    Items now;
    bool exact = true;
    if (!eng_.readContents(menuNet_, now, exact)) { ++stats_.readFailures; return; }   // an inexact read of an OPEN container still shows what the player moved
    ++stats_.polls;
    client_->observed(menuNet_, now, now_);
}

void LootSync::openMenu(RefId ref) {
    menuRef_ = ref;
    menuNet_ = 0;
    ContainerDesc d;
    if (!eng_.describeContainer(ref, d) || !d.net) return;
    Items items;
    bool exact = true;
    if (!eng_.readContents(d.net, items, exact)) { ++stats_.readFailures; return; }
    menuNet_ = d.net;
    ++stats_.menusOpened;
    lastPollMs_ = now_;
    client_->opened(d.net, d.kind, d.cell, d.pos, items, now_);
}

void LootSync::closeMenu() {
    const NetId net = menuNet_;
    menuRef_ = 0;
    menuNet_ = 0;
    if (!net) return;
    ++stats_.menusClosed;
    if (suppressClose_.erase(net) && !FNV_MUT(144)) return;   // twin 144: a menu WE closed (busy / lock lost) is reported as the player closing it
    Items items;
    bool exact = true;
    if (!eng_.readContents(net, items, exact)) { ++stats_.readFailures; items = Items(); }
    client_->closed(net, items, now_);
}

void LootSync::pollMenu(uint64_t now) {
    const RefId top = eng_.openMenuRef();
    if (top != menuRef_) {
        if (menuRef_) closeMenu();
        if (top) openMenu(top);
        return;
    }
    if (menuNet_ && now - lastPollMs_ >= cfg_.pollMs) {
        lastPollMs_ = now;
        poll();
    }
}

// ================================================================================================================ actions

void LootSync::applyContents(NetId net, const Items& want) {
    const bool ok = eng_.setContents(net, want);
    if (!ok) ++stats_.actionsFailed;
    // what the engine really holds now is the new baseline: if it could not be set, the watcher must not mistake the difference for a player move
    Items now;
    bool exact = true;
    if (!FNV_MUT(143) && eng_.readContents(net, now, exact)) client_->rebaseline(net, now);
    else if (!FNV_MUT(143)) client_->rebaseline(net, want);
}

void LootSync::run(const std::vector<lc::Action>& acts) {
    for (const lc::Action& a : acts) {
        ++stats_.actionsRun;
        switch (a.kind) {
        case lc::Action::PlayerAdjust:
            if (!FNV_MUT(142)) {   // twin 142: a refused transfer is never taken back
                if (!eng_.adjustPlayer(a.base, a.delta)) ++stats_.actionsFailed;
                ++stats_.rollbacksApplied;
            }
            break;
        case lc::Action::ContainerSet: applyContents(a.id, a.items); break;
        case lc::Action::CloseMenu:
            if (menuNet_ == a.id) suppressClose_.insert(a.id);
            eng_.closeMenu();
            break;
        case lc::Action::Notify: eng_.notify(a.notice, a.id); break;
        case lc::Action::BindDrop: eng_.bindItem(a.ref, a.id); break;
        case lc::Action::CreateItem:
            if (eng_.createItem(a.item) == 0) {
                if (creates_.size() < cfg_.maxPendingCreates) {
                    creates_.push_back(a.item);
                    createsDue_.push_back(now_ + cfg_.createRetryMs);
                    ++stats_.createsDeferred;
                } else {
                    ++stats_.createsDropped;
                }
            }
            break;
        case lc::Action::RemoveItem:
            eng_.removeItem(a.id);
            for (size_t i = 0; i < creates_.size();) {
                if (creates_[i].id == a.id) { creates_.erase(creates_.begin() + static_cast<long>(i)); createsDue_.erase(createsDue_.begin() + static_cast<long>(i)); }
                else ++i;
            }
            break;
        case lc::Action::SetPose: eng_.setPose(a.id, a.pos, a.rot, a.flag); break;
        case lc::Action::ReadyToClose: {
            Items items;
            bool exact = true;
            if (!eng_.readContents(a.id, items, exact)) { ++stats_.readFailures; items = Items(); }
            client_->closeNow(a.id, items, now_);
            break;
        }
        case lc::Action::ClutterOwner:
            if (a.flag) ownedClutter_.insert(a.id); else ownedClutter_.erase(a.id);
            break;
        }
    }
}

void LootSync::drain() {
    if (!client_) return;
    for (int guard = 0; guard < 8; ++guard) {
        std::vector<lc::Action> acts = client_->takeActions();
        if (acts.empty()) return;
        run(acts);
        poll();
    }
}

// ================================================================================================================ time

void LootSync::idleTick(uint64_t nowMs) { now_ = nowMs; }

void LootSync::tick(uint64_t nowMs) {
    if (!link_ || !client_) return;
    now_ = nowMs;
    ++stats_.ticks;
    if (!eng_.worldReady()) return;
    pump();
    pollMenu(nowMs);
    drain();
    client_->tick(nowMs);
    drain();
    if (host_) {
        lc::LootHost::Out out;
        host_->tick(nowMs, out);
        deliver(out);
        pump();
    }
    tickResolve(nowMs);
    tickCreates(nowMs);
    tickSweep(nowMs);
    tickAudit(nowMs);
    tickClutter(nowMs);
    pump();
}

void LootSync::onContainerLoaded(RefId ref) {
    if (!client_ || !cfg_.resolveOnLoad || !ref) return;
    Resolve r;
    r.ref = ref; r.due = now_;
    resolves_.push_back(r);
}

void LootSync::onActorDied(NetId actor, RefId ref) {
    if (!client_ || !ref) return;
    Resolve r;
    r.ref = ref; r.corpse = true; r.actor = actor; r.due = now_ + cfg_.corpseDelayMs;
    resolves_.push_back(r);
}

void LootSync::tickResolve(uint64_t now) {
    size_t budget = 8;
    for (size_t n = resolves_.size(); n > 0 && budget > 0; --n) {
        Resolve r = resolves_.front();
        resolves_.pop_front();
        if (r.due > now) { resolves_.push_back(r); continue; }
        --budget;
        ContainerDesc d;
        if (!eng_.describeContainer(r.ref, d) || !d.net) continue;
        if (r.corpse) d.kind = lc::ContainerKind::Corpse;
        if (client_->container(d.net) != nullptr) continue;   // somebody resolved it already: ours is replaced when we open it
        if (!r.corpse && !r.deferredForOwner && ownsCell_ && !ownsCell_(d.cell)) {   // not the cell's owner: give the owner's real items time to arrive first
            r.deferredForOwner = true;
            r.due = now + 3000;
            resolves_.push_back(r);
            continue;
        }
        Items items;
        if (!readExact(d.net, items)) {
            if (++r.tries < 6) { r.due = now + cfg_.resolveRetryMs; resolves_.push_back(r); ++stats_.resolveRetries; }
            continue;
        }
        client_->resolved(d.net, d.kind, d.cell, d.pos, items, now);
        if (r.corpse) ++stats_.corpsesResolved; else ++stats_.containersResolved;
    }
}

void LootSync::tickCreates(uint64_t now) {
    if (FNV_MUT(154)) { creates_.clear(); createsDue_.clear(); return; }   // twin 154: a remote drop whose cell was not loaded is forgotten
    for (size_t i = 0; i < creates_.size();) {
        if (createsDue_[i] > now) { ++i; continue; }
        if (eng_.createItem(creates_[i]) != 0) {
            creates_.erase(creates_.begin() + static_cast<long>(i));
            createsDue_.erase(createsDue_.begin() + static_cast<long>(i));
        } else {
            createsDue_[i] = now + cfg_.createRetryMs;
            ++i;
        }
    }
}

void LootSync::tickSweep(uint64_t now) {
    if (!host_ || !cfg_.hostSweep || FNV_MUT(147) || now < nextSweep_) return;   // twin 147: scripts' changes to a container never reach the table
    nextSweep_ = now + cfg_.sweepEveryMs;
    std::vector<NetId> ids;
    eng_.loadedContainers(ids, 512);
    if (ids.empty()) return;
    std::sort(ids.begin(), ids.end());
    for (uint32_t n = 0; n < cfg_.sweepPerTick && n < ids.size(); ++n) {
        const NetId id = ids[(sweepCursor_ + n) % ids.size()];
        const lc::ContainerRec* c = host_->container(id);
        if (!c || c->holder != lc::kNoPlayer || id == menuNet_) continue;
        Items items;
        if (!readExact(id, items)) continue;
        ++stats_.sweepsCompared;
        if (lc::digestOf(items) == lc::digestOf(c->items)) continue;
        lc::LootHost::Out out;
        if (host_->externalSet(id, items, now, out)) ++stats_.sweepsChanged;
        deliver(out);
    }
    sweepCursor_ = (sweepCursor_ + cfg_.sweepPerTick) % ids.size();
}

void LootSync::tickAudit(uint64_t now) {
    if (host_) return;   // the host's engine is the authority for what scripts do (tickSweep carries it into the table); it is never corrected from the table
    if (FNV_MUT(148) || now < nextAudit_) return;   // twin 148: engine copies are never compared with the shared contents
    nextAudit_ = now + cfg_.auditEveryMs;
    std::vector<NetId> ids;
    eng_.loadedContainers(ids, 512);
    if (ids.empty()) return;
    std::sort(ids.begin(), ids.end());
    for (uint32_t n = 0; n < cfg_.auditPerTick && n < ids.size(); ++n) {
        const NetId id = ids[(auditCursor_ + n) % ids.size()];
        if (id == menuNet_ || !client_->container(id)) continue;
        Items items;
        if (!readExact(id, items)) continue;
        ++stats_.auditsCompared;
        client_->audit(id, items, now);
    }
    auditCursor_ = (auditCursor_ + cfg_.auditPerTick) % ids.size();
    drain();
}

void LootSync::tickClutter(uint64_t now) {
    if (FNV_MUT(155) || ownedClutter_.empty() || now < nextClutter_) return;   // twin 155: owned clutter is never sampled (it never moves on anybody else's screen)
    nextClutter_ = now + cfg_.clutterEveryMs;
    std::vector<lc::LootClient::Sample> samples;
    for (NetId id : ownedClutter_) {
        lc::LootClient::Sample s;
        s.net = id;
        if (eng_.readPose(id, s.pos, s.rot)) samples.push_back(s);
    }
    if (!samples.empty()) client_->clutterSamples(samples, now);
}

// ================================================================================================================ items

void LootSync::onDrop(RefId dropper, RefId dropped) {
    if (!client_ || !dropped) return;
    ++stats_.dropsSeen;
    if (dropper != kPlayerRef && !FNV_MUT(150)) { ++stats_.dropsIgnored; return; }   // twin 150: any actor's drop is announced as the local player's (and takes the item from that player)
    ItemDesc d;
    if (!eng_.describeItem(dropped, d) || d.item.base.plugin.empty() || d.item.count <= 0) { ++stats_.dropsIgnored; return; }
    client_->dropped(dropped, d.item.base, d.item.count, d.item.meta, d.item.cell, d.item.pos, d.item.rot, now_);
    pump();
}

void LootSync::onPickup(RefId picker, RefId item) {
    if (!client_ || !item) return;
    ++stats_.pickupsSeen;
    if (picker != kPlayerRef && !FNV_MUT(151)) { ++stats_.pickupsIgnored; return; }   // twin 151: an NPC's pickup is claimed for the local player
    ItemDesc d;
    if (!eng_.describeItem(item, d) || d.item.base.plugin.empty()) { ++stats_.pickupsIgnored; return; }
    client_->pickedUp(item, d.net, d.item.base, d.item.count, d.item.meta, d.item.cell, d.item.pos, now_);
    pump();
}

void LootSync::onTouch(RefId ref) {
    if (!client_) return;
    ItemDesc d;
    if (!eng_.describeItem(ref, d) || !d.net) return;
    lc::Vec3 pos, rot;
    if (!eng_.readPose(d.net, pos, rot)) { pos = d.item.pos; rot = d.item.rot; }
    client_->touch(d.net, pos, rot, now_);
    pump();
}

void LootSync::onCellReset(lc::CellId cell) {
    if (!host_) return;
    lc::LootHost::Out out;
    host_->resetCell(cell, out);
    deliver(out);
    pump();
}

// ================================================================================================================ sidecar and status

std::vector<uint8_t> LootSync::saveState() const { return host_ ? host_->save() : pendingSidecar_; }

bool LootSync::loadState(const std::vector<uint8_t>& bytes) {
    if (host_) return host_->load(bytes);
    pendingSidecar_ = bytes;
    return true;
}

std::string LootSync::statusJson() const {
    char b[1400];
    const lc::ClientStats* cs = client_ ? &client_->stats() : nullptr;
    const lc::HostStats* hs = host_ ? &host_->stats() : nullptr;
    std::snprintf(b, sizeof b,
                  "{\"enabled\":%s,\"session\":%s,\"host\":%s,\"link\":%s,\"openContainer\":%u,\"containers\":%zu,\"items\":%zu,\"pending\":%zu,"
                  "\"digest\":\"%016llx\",\"hostDigest\":\"%016llx\",\"tick\":{\"in\":%llu,\"out\":%llu,\"undecodable\":%llu,\"spoofed\":%llu,\"polls\":%llu,"
                  "\"actions\":%llu,\"actionsFailed\":%llu,\"rollbacks\":%llu,\"resolved\":%llu,\"corpses\":%llu,\"createsDeferred\":%llu,\"sweepsChanged\":%llu},"
                  "\"client\":{\"rollbacks\":%llu,\"busy\":%llu,\"claimsLost\":%llu,\"resent\":%llu,\"gaps\":%llu,\"tables\":%llu,\"closeMismatch\":%llu,\"audit\":%llu},"
                  "\"hostStats\":{\"requests\":%llu,\"replays\":%llu,\"busy\":%llu,\"closeMismatch\":%llu,\"claimsWon\":%llu,\"claimsLost\":%llu,\"heals\":%llu}}",
                  cfg_.enabled ? "true" : "false", link_ ? "true" : "false", host_ ? "true" : "false", linkUp_ ? "true" : "false", menuNet_,
                  client_ ? client_->containerCount() : 0, client_ ? client_->itemCount() : 0, client_ ? client_->pendingCount() : 0,
                  (unsigned long long)(client_ ? client_->digest() : 0), (unsigned long long)(host_ ? host_->digest() : 0),
                  (unsigned long long)stats_.datagramsIn, (unsigned long long)stats_.datagramsOut, (unsigned long long)stats_.undecodable,
                  (unsigned long long)stats_.spoofed, (unsigned long long)stats_.polls, (unsigned long long)stats_.actionsRun,
                  (unsigned long long)stats_.actionsFailed, (unsigned long long)stats_.rollbacksApplied, (unsigned long long)stats_.containersResolved,
                  (unsigned long long)stats_.corpsesResolved, (unsigned long long)stats_.createsDeferred, (unsigned long long)stats_.sweepsChanged,
                  (unsigned long long)(cs ? cs->rollbacks : 0), (unsigned long long)(cs ? cs->busy : 0), (unsigned long long)(cs ? cs->claimsLost : 0),
                  (unsigned long long)(cs ? cs->resent : 0), (unsigned long long)(cs ? cs->gaps : 0), (unsigned long long)(cs ? cs->tablesApplied : 0),
                  (unsigned long long)(cs ? cs->closeMismatch : 0), (unsigned long long)(cs ? cs->auditCorrections : 0),
                  (unsigned long long)(hs ? hs->requests : 0), (unsigned long long)(hs ? hs->replays : 0), (unsigned long long)(hs ? hs->busy : 0),
                  (unsigned long long)(hs ? hs->closeMismatch : 0), (unsigned long long)(hs ? hs->claimsWon : 0), (unsigned long long)(hs ? hs->claimsLost : 0),
                  (unsigned long long)(hs ? hs->healsSent : 0));
    return b;
}

}  // namespace nvcoop::loot
