// Wire messages of the loot module (first byte kChannelLoot on the coop mux). docs/protocol.md section 15.
//
// Datagram: [kChannelLoot][Archive: version u8, src u8, body index u8, body]
//   client -> host : ReqRegister, ReqOpen, ReqMove, ReqClose, ReqHold, ReqDrop, ReqClaim, ReqResync, ReqDigest, ReqTouch, MClutter
//   host -> client : Result (to the sender), MContainer, MDelta, MLock, MItemNew, MItemGone, MContainerGone, MTableBegin/End, MClutter (relay), MClutterOwn
// Everything is reliable-ordered except MClutter (unreliable sequenced). Every message is idempotent: txn (requests), rev / lockSeq
// (container state), the item id (world items), (epoch, seq) (clutter).
#pragma once
#include <vector>
#include "loot/loot.h"

namespace fnvcoop::loot {

struct Envelope {
    PlayerId src = kNoPlayer;
    Body body;
};

std::vector<uint8_t> encodeEnvelope(const Envelope& e);                  // full datagram, kChannelLoot included. Empty when a field cannot be encoded
bool decodeEnvelope(const uint8_t* data, size_t size, Envelope& out);    // data = the full datagram; false on ANY malformed input

void io(fnvcoop::Archive& a, Vec3& v);
void io(fnvcoop::Archive& a, Items& v);
void io(fnvcoop::Archive& a, ContainerRec& v);
void io(fnvcoop::Archive& a, WorldItem& v);
void io(fnvcoop::Archive& a, Result& v);

}  // namespace fnvcoop::loot
