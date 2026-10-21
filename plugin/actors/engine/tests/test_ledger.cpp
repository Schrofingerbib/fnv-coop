// Ledger: what the port changed in the world survives a restart in a small file. Twins: 7 (a new entry is never written), 8 (remove ignored),
// 9 (stand-in entries dropped on load), 10 (disabled-real entries dropped on load).
#include <string>

#include "actors/engine/ledger.h"
#include "minitest.h"

using namespace nvcoop::actors::engine;

namespace {
struct MemStore : IFileStore {
    std::string text;
    bool has = false, failWrite = false;
    int writes = 0;
    bool read(std::string& out) override { if (!has) return false; out = text; return true; }
    bool write(const std::string& t) override { if (failWrite) return false; text = t; has = true; ++writes; return true; }
};
}  // namespace

TEST(ledger, entries_are_kept_per_kind) {
    Ledger l;
    l.add(LedgerKind::Restrained, 0x100);
    l.add(LedgerKind::Restrained, 0x100);
    l.add(LedgerKind::StandIn, 0xFF000001);
    l.add(LedgerKind::Restrained, 0);   // 0 is never a ref
    CHECK(l.has(LedgerKind::Restrained, 0x100) && !l.has(LedgerKind::StandIn, 0x100));
    CHECK(l.size() == 2 && l.all(LedgerKind::Restrained).size() == 1);
    l.remove(LedgerKind::Restrained, 0x100);
    CHECK(!l.has(LedgerKind::Restrained, 0x100) && l.size() == 1);
}

TEST(ledger, the_file_text_is_pinned) {
    Ledger l;
    l.add(LedgerKind::Restrained, 0x0012AB34);
    l.add(LedgerKind::StandIn, 0xFF000042);
    CHECK(l.serialize() == "nvcoop-actors-ledger 1\nr 0012AB34\ns FF000042\n");
}

TEST(ledger, a_change_is_written_and_a_restart_reads_it_back) {
    MemStore st;
    {
        Ledger l(&st);
        l.add(LedgerKind::Restrained, 0x111);
        l.add(LedgerKind::StandIn, 0xFF000001);
        l.add(LedgerKind::Disabled, 0x222);
        l.add(LedgerKind::Essential, 0x333);
        CHECK(l.dirty());
        CHECK(l.flush(100));
        CHECK(!l.dirty() && st.writes == 1);
    }
    Ledger b(&st);
    CHECK(b.load() == 4);
    CHECK(b.has(LedgerKind::Restrained, 0x111));
    CHECK(b.has(LedgerKind::StandIn, 0xFF000001));
    CHECK(b.has(LedgerKind::Disabled, 0x222));
    CHECK(b.has(LedgerKind::Essential, 0x333));
}

TEST(ledger, removal_is_written_too) {
    MemStore st;
    Ledger l(&st);
    l.add(LedgerKind::Restrained, 0x111);
    l.flush(0);
    l.remove(LedgerKind::Restrained, 0x111);
    CHECK(l.dirty());
    CHECK(l.flush(5000));
    Ledger b(&st);
    CHECK(b.load() == 0 && b.empty());
}

TEST(ledger, writes_are_rate_limited_unless_forced) {
    MemStore st;
    Ledger l(&st);
    l.add(LedgerKind::Restrained, 1);
    CHECK(l.flush(1000));
    l.add(LedgerKind::Restrained, 2);
    CHECK(!l.flush(1500));            // 500 ms after the last write: not yet
    CHECK(l.dirty());
    CHECK(l.flush(2000));
    l.add(LedgerKind::Restrained, 3);
    CHECK(l.flush(2001, true));       // forced (a save is about to happen)
    CHECK(st.writes == 3);
    CHECK(!l.flush(9000));            // nothing changed
}

TEST(ledger, a_failed_write_is_retried) {
    MemStore st;
    st.failWrite = true;
    Ledger l(&st);
    l.add(LedgerKind::Restrained, 7);
    CHECK(!l.flush(0) && l.dirty());
    st.failWrite = false;
    CHECK(l.flush(10));
    CHECK(!l.dirty());
}

TEST(ledger, damaged_files_never_break_a_load) {
    MemStore st;
    st.has = true;
    st.text = "nvcoop-actors-ledger 1\r\nr 0000ABCD\r\nx 00000001\r\ngarbage\r\nr zzzz\r\nr 00000000\r\ns 0000BEEF\r\n\r\n";
    Ledger l(&st);
    CHECK(l.load() == 2);
    CHECK(l.has(LedgerKind::Restrained, 0xABCD) && l.has(LedgerKind::StandIn, 0xBEEF));
    st.text = "not a ledger\nr 00000001\n";
    CHECK(l.load() == 0 && l.empty());
    st.has = false;
    CHECK(l.load() == 0);
    Ledger none;   // no store at all
    none.add(LedgerKind::Restrained, 1);
    CHECK(!none.flush(0) && none.load() == 0);
}

TEST(ledger, a_loaded_games_cosave_is_merged_not_swapped_in) {
    MemStore st;
    Ledger l(&st);
    l.add(LedgerKind::Restrained, 0x111);
    l.flush(0);
    const std::string saved = "nvcoop-actors-ledger 1\nr 00000222\ns FF000005\nr 00000111\n";
    CHECK(l.merge(saved) == 2);                         // 0x111 was known already
    CHECK(l.has(LedgerKind::Restrained, 0x111) && l.has(LedgerKind::Restrained, 0x222) && l.has(LedgerKind::StandIn, 0xFF000005));
    CHECK(l.dirty());
    CHECK(l.merge("garbage") == 0 && l.size() == 3);
    // a changed load order: the saved id is mapped, an id whose mod is gone is skipped
    Ledger m;
    const size_t n = m.merge("nvcoop-actors-ledger 1\nr 01000010\nr 02000020\ns FF000001\n", [](uint32_t& id) {
        if ((id >> 24) == 0xFF) return true;
        if ((id >> 24) == 1) { id = (id & 0xFFFFFFu) | 0x05000000u; return true; }
        return false;
    });
    CHECK(n == 2 && m.has(LedgerKind::Restrained, 0x05000010) && !m.has(LedgerKind::Restrained, 0x02000020) && m.has(LedgerKind::StandIn, 0xFF000001));
}
