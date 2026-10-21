// Per-player storage (docs/protocol.md section 12, "Coordinator decisions"): shared player storage containers (NVCCPlayerStorageREF, companion
// containers) become one container per slot. The host keeps one container per slot; the guest's ContainerOpen on the shared reference is
// answered with the container to open instead (Verdict.redirect). Data, not code: nvcoop_intents.json next to the dll.
//
//   {"redirects":[{"target":"falloutnv.esm:12AB34","slots":{"0":"falloutnv.esm:12AB34","1":"mymod.esp:000801","2":"mymod.esp:000802"}}]}
//
// A slot the entry does not list has no storage there: the open is refused (Denied) rather than leaking into somebody else's container.
#pragma once
#include <map>
#include <string>

#include "intents/intent_types.h"

namespace nvcoop::intents {

class ContainerRedirect {
public:
    bool load(const std::string& jsonText, std::string* err);   // replaces the table; on error the old table stays
    bool isShared(const FormRef& target) const { return table_.count(target) != 0; }
    // true: `*out` is the container `slot` opens (it may equal `target`). false: not a shared container, or `slot` has no storage there
    // (use isShared to tell the two apart).
    bool resolve(const FormRef& target, PlayerId slot, FormRef* out) const;
    size_t size() const { return table_.size(); }

private:
    std::map<FormRef, std::map<PlayerId, FormRef>> table_;
};

// "plugin.esm:00ABCD" (hex id, at most 24 bits, plugin name lower-cased) -> FormRef. false on anything else.
bool parseFormRef(const std::string& text, FormRef* out);

}  // namespace nvcoop::intents
