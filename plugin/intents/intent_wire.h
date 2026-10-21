// Wire format of the intents channel (docs/protocol.md section 19).
//
// Datagram: [kChannelIntents 0x8D][kIntentsWire][type u8][Archive payload (string table + body)]
//   type 1  Intent   guest -> host   session, id, kind, target, hasAux [aux], value, path
//   type 2  Verdict  host -> guest   session, id, result, holder, hasRedirect [redirect]
// Always reliable-ordered (ENet channel 0 through the coop MuxTransport). No CRC: the transport already checksums.
// Receivers treat every byte as hostile: unknown kinds / results, over-long paths, trailing bytes and non-finite values reject the datagram.
#pragma once
#include "intents/intent_types.h"

namespace nvcoop::intents {

enum class WireType : uint8_t { Bad = 0, Intent = 1, Verdict = 2 };

Bytes encode(const Intent& m);
Bytes encode(const Verdict& m);
// Decodes exactly one message; fills the matching out parameter. Anything malformed returns WireType::Bad and leaves both untouched.
WireType decode(const uint8_t* data, size_t size, Intent* intent, Verdict* verdict);
inline WireType decode(const Bytes& b, Intent* i, Verdict* v) { return decode(b.data(), b.size(), i, v); }

}  // namespace nvcoop::intents
