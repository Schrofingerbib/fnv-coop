"""Tests plus mutation twin for tools/flight. One command: python -I tools/flight/run_tests.py  (prints ALL OK).

  (default)   fast tests only, under 20 s
  --full      also the slow shrink/generation/CLI tests and the mutation twin
  --no-twin   with --full: skip the mutation twin
  --only A,B  run only the test classes A and B (e.g. --only TraceReplayTests,FromFailureTests)
  The twin copies the tool sources to out/T1b/tmp, breaks one thing at a time and requires this suite to fail.
  NVFLIGHT_SRC=<dir> makes the suite test the sources in <dir> (used by the twin).
"""
import json
import os
import shutil
import subprocess
import sys
import struct
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SRC = os.environ.get("NVFLIGHT_SRC") or HERE
sys.path.insert(0, SRC)
import nvfr as N  # noqa: E402
import analyze as A  # noqa: E402
import shrink as S  # noqa: E402
import gentest as G  # noqa: E402
import report as R  # noqa: E402
import regress as RG  # noqa: E402
import trace_replay as TR  # noqa: E402
import from_failure as FF  # noqa: E402

FULL = "--full" in sys.argv
slow = unittest.skipUnless(FULL, "slow: run with --full")
TMP_ROOT = os.path.join(ROOT, "out", "T1b", "tmp")
TOY = os.path.join(HERE, "toy_replay.py")
REGRESS = os.environ.get("NVFLIGHT_REGRESS") or os.path.join(HERE, "regress")   # the twin runs a copy of this file elsewhere
REPLAY = [sys.executable, "-I", TOY, "{fixture}"]


# ---------------------------------------------------------------- synthetic traces

class TB:
    def __init__(self, slot, wall_skew_ms=0, instance=None, reason=1):
        self.h = {"slot": slot, "role": 0 if slot == 0 else 1, "reason": reason, "session": 9, "epoch": 1,
                  "wall_unix_ms": 1_000_000 + wall_skew_ms, "records_dropped": 0}
        self.recs = []
        self.add(N.T_META, 0, meta={"label": instance or ("inst%d" % slot)})

    def add(self, ty, t_ms, **f):
        r = {"type": ty, "mono_us": int(t_ms * 1000), "frame": int(t_ms / 16)}
        r.update(f)
        self.recs.append(r)

    def net(self, out, t_ms, peer, mtype, seq, raw, epoch=1, flags=0):
        self.add(N.T_NET_OUT if out else N.T_NET_IN, t_ms, peer=peer, channel=0 if out else 0xFF, mux=0xFC, msg_type=mtype, decode=0,
                 session=9, epoch=epoch, seq=seq, seq_hi=seq, raw=raw, truncated=bool(flags))

    def txn(self, ty, t_ms, seq, h, flags=0, origin=0):
        """flags are the appliedFlags of a TxnApplied record (1 snapshot, 2 repair, 4 baseline, 8 catchUp)."""
        if ty == N.T_TXN_CAPTURED:
            self.add(ty, t_ms, kind=0, intent_id=0, seq=seq, effect_count=1, hash=h, cause=0, epoch=1)
        else:
            self.add(ty, t_ms, seq=seq, epoch=1, origin=origin, index=0, applied_flags=flags, cause=0, effect_tag=0, hash=h)

    def digest(self, t_ms, cat, seq, hash_, keys=None, shards=None):
        self.add(N.T_DIGEST, t_ms, category=cat, epoch=1, seq=seq, hash=hash_, item_count=0, shards=shards or [], keys=keys or {})

    def own(self, t_ms, op, actor, epoch, frm, to):
        self.add(N.T_OWNERSHIP, t_ms, op=op, actor=actor, epoch=epoch, from_slot=frm, to_slot=to, reason=0, txn_id=0)

    def data(self):
        return N.dumps(self.h, sorted(self.recs, key=lambda r: r["mono_us"]))

    def trace(self):
        return N.loads(self.data(), "slot%d" % self.h["slot"])


def good_hash(seq):
    return 0x1000 + seq


def scenario(bad_seq=None, lost=(), digest2="differ", dup_seq=None, clean_extras=False, instance1=None, late_tail=0):
    """3 machines, host slot 0, clients 1 and 2; slot 2's wall clock is 500 ms fast. 5 ms latency each way."""
    tb = [TB(0), TB(1, instance=instance1), TB(2, wall_skew_ms=500)]
    for seq, t in ((1, 100), (2, 200), (3, 300), (4, 400)):
        tb[0].txn(N.T_TXN_CAPTURED, t - 1, seq, good_hash(seq))
        tb[0].txn(N.T_TXN_APPLIED, t, seq, good_hash(seq))
        for c in (1, 2):
            raw = bytes([4, seq, c, 0xAB])
            tb[0].net(True, t, c, 4, seq, raw)
            if (seq, c) in lost:
                continue
            tb[c].net(False, t + 5, 0, 4, seq, raw)
            h = 0xBAD if (bad_seq == seq and c == 1) else good_hash(seq)
            tb[c].txn(N.T_TXN_APPLIED, t + 6, seq, h)
            ack = bytes([5, seq, c])
            tb[c].net(True, t + 7, 0, 5, seq, ack)
            tb[0].net(False, t + 12, c, 5, seq, ack)
            if dup_seq == seq and c == 1:
                tb[c].net(False, t + 9, 0, 4, seq, raw)
    for seq, c in lost:  # retransmit 30 ms later
        raw = bytes([4, seq, c, 0xAB])
        t = seq * 100 + 30
        tb[0].net(True, t, c, 4, seq, raw)
        tb[c].net(False, t + 5, 0, 4, seq, raw)
        tb[c].txn(N.T_TXN_APPLIED, t + 6, seq, good_hash(seq))
        tb[c].net(True, t + 7, 0, 5, seq, bytes([5, seq, c]))
        tb[0].net(False, t + 12, c, 5, seq, bytes([5, seq, c]))
    for t in (250,):
        for s in range(3):
            tb[s].digest(t, 0, 1, 0xAAAA, keys={0x10: 5, 0x20: 6})
    if digest2 == "differ":
        tb[0].digest(700, 0, 2, 0x1111, keys={0x10: 5, 0x20: 6})
        tb[1].digest(700, 0, 2, 0x2222, keys={0x10: 5, 0x20: 7})
        tb[2].digest(700, 0, 2, 0x1111, keys={0x10: 5, 0x20: 6})
    elif digest2 == "same":
        for s in range(3):
            tb[s].digest(700, 0, 2, 0x1111, keys={0x10: 5, 0x20: 6})
    if clean_extras:
        tb[1].txn(N.T_TXN_APPLIED, 215, 2, 0, flags=N.AP_REPAIR)   # a repair restating state, with a different hash: not a divergence
        for s in range(3):
            tb[s].own(500, 0, 5, 3, 255, 1)
            tb[s].own(510, 0, 5, 3, 255, 1)                          # idempotent regrant, same epoch
    if late_tail:
        for s in range(3):
            tb[s].add(N.T_MARKER, late_tail, kind=0, text="tick")
    return [t.trace() for t in tb]


# ---------------------------------------------------------------- tests

class FormatTests(unittest.TestCase):
    def test_roundtrip_all_types(self):
        recs = [
            {"type": 1, "mono_us": 0, "frame": 0, "meta": {"label": "A B", "build": "7"}},
            {"type": 3, "mono_us": 5, "frame": 1, "peer": 2, "channel": 0xFF, "mux": 0xFC, "msg_type": 4, "decode": 0, "session": 300,
             "epoch": 70000, "seq": 1 << 40, "seq_hi": (1 << 40) + 2, "raw_len": 99, "raw": b"\x00\x01\xfe", "truncated": True},
            {"type": 4, "mono_us": 9, "frame": 2, "kind": 1, "intent_id": 129, "seq": 300, "effect_count": 3, "hash": 1 << 63, "cause": 2, "epoch": 4},
            {"type": 5, "mono_us": 10, "frame": 2, "seq": 300, "epoch": 4, "origin": 1, "index": 2, "applied_flags": 9, "cause": 3, "effect_tag": 40,
             "hash": (1 << 64) - 1},
            {"type": 6, "mono_us": 11, "frame": 3, "op": 3, "actor": 0xFF00000000001001, "epoch": 2, "from_slot": 1, "to_slot": 255, "reason": 9, "txn_id": 77},
            {"type": 7, "mono_us": 12, "frame": 3, "category": 2, "epoch": 3, "seq": 8, "hash": 0xFEEDFACE, "item_count": 5,
             "shards": [1, 2, 3], "keys": {1 << 63: 5}, "derived": True},
            {"type": 8, "mono_us": 13, "frame": 3, "kind": 4, "text": "desync é"},
            {"type": 99, "mono_us": 14, "frame": 4, "payload": b"\x09\x09"},
        ]
        h = {"slot": 3, "role": 1, "reason": 2, "wire_version": 5, "wall_unix_ms": 1234567890123, "dump_t_us": 99, "records_dropped": 8,
             "session": 4, "epoch": 6}
        tr = N.loads(N.dumps(h, recs))
        self.assertFalse(tr["truncated"])
        self.assertEqual(tr["header"]["reason_name"], "crash")
        self.assertEqual(tr["header"]["role_name"], "client")
        self.assertEqual((tr["header"]["session"], tr["header"]["epoch"], tr["header"]["record_count"]), (4, 6, 8))
        self.assertEqual(tr["header"]["records_dropped"], 8)
        self.assertEqual(tr["header"]["instance"], "A B")
        got = tr["records"]
        self.assertEqual(len(got), 8)
        for want, g in zip(recs, got):
            for k, v in want.items():
                self.assertEqual(g[k], v, (want["type"], k))
        self.assertEqual(got[4]["ref"], "actor:FF00000000001001")
        self.assertEqual(got[7]["name"], "Unknown99")
        self.assertEqual(got[1]["flags"], 1)
        self.assertEqual(got[5]["flags"], 2)
        self.assertIn("seq=300", N.describe(got[2]))
        self.assertIn("peer 2", N.describe(got[1]))
        self.assertIn("[test-step]", N.describe(got[6]))
        self.assertIn("rejectedStale", N.describe(got[4]))

    def test_net_crc_differs_for_different_datagrams(self):
        # every story datagram ends in its own crc32, so a plain crc32 of the whole datagram would be a constant
        recs = [{"type": 2, "mono_us": i, "frame": 0, "peer": 1, "raw": bytes([i, 7, 7, 7])} for i in range(3)]
        got = N.loads(N.dumps({"slot": 0}, recs))["records"]
        self.assertEqual(len({r["crc"] for r in got}), 3)

    def test_golden_files_from_the_recorder(self):
        base = os.path.join(ROOT, "synccore", "flight", "golden")
        if not os.path.isdir(base):
            self.skipTest("no golden files")
        trs = [N.load(os.path.join(base, n)) for n in ("sample_host.nvfr", "sample_client.nvfr")]
        self.assertEqual([t["header"]["role_name"] for t in trs], ["host", "client"])
        self.assertTrue(all(not t["truncated"] and not t["warnings"] for t in trs))
        self.assertEqual({r["name"] for t in trs for r in t["records"]} >= {"Meta", "NetIn", "NetOut", "TxnApplied", "Digest", "Marker"}, True)
        a = A.align(trs)
        self.assertEqual(len(a.lost), 0)
        self.assertGreaterEqual(len(a.messages), 10)

    def test_truncated_tail_is_tolerated(self):
        tr = scenario()[0]
        data = N.dumps(tr["header"], [dict(r) for r in tr["records"]])
        for cut in (1, 3, 7):
            t2 = N.loads(data[:-cut])
            self.assertTrue(t2["truncated"])
            self.assertEqual(len(t2["records"]), len(tr["records"]) - 1)
            self.assertTrue(any("middle of a record" in w for w in t2["warnings"]))

    def test_bad_files(self):
        with self.assertRaises(N.NvfrError):
            N.loads(b"XXXX" + bytes(80))
        good = N.dumps({"slot": 1}, [])
        bad = bytearray(good)
        bad[4] = 2
        with self.assertRaises(N.NvfrError):
            N.loads(bytes(bad))
        with self.assertRaises(N.NvfrError):
            N.loads(good[:10])
        with self.assertRaises(N.NvfrError):
            N.loads(good[:40])
        self.assertEqual(N.loads(good)["records"], [])
        self.assertEqual(len(good), N.HEADER_SIZE)

    def test_malformed_body_is_kept(self):
        h = {"slot": 1}
        marker = N.dumps(h, [{"type": 8, "mono_us": 5, "frame": 0, "kind": 0, "text": "ok"}])[N.HEADER_SIZE:]
        broken = struct.pack("<BBHIQ", 3, 0, 5, 0, 0) + b"\x00" * 5     # a NetIn whose body is too short for its fields
        tr = N.loads(N.dumps(h, []) + broken + marker)
        self.assertEqual(len(tr["records"]), 2)
        self.assertTrue(tr["records"][0].get("malformed"))
        self.assertEqual(tr["records"][1]["name"], "Marker")
        self.assertTrue(any("malformed" in w for w in tr["warnings"]))

    def test_pretty(self):
        text = N.pretty(scenario()[0], types={"digest"})
        self.assertIn("Digest", text)
        self.assertNotIn("NetIn", text)
        self.assertIn("role=host", text)
        self.assertEqual(N.pretty(scenario()[0], limit=2).count("\n"), 4)


class AlignTests(unittest.TestCase):
    def test_clock_offset_and_matching(self):
        a = A.align(scenario())
        self.assertEqual(a.slots, [0, 1, 2])
        self.assertEqual(a.ref_slot, 0)
        self.assertAlmostEqual(a.offset_us[2], 500000, delta=1000)
        self.assertLess(abs(a.offset_us[1]), 1000)
        self.assertEqual(len(a.lost), 0)
        self.assertEqual(len(a.orphans), 0)
        # 4 txn batches + 4 acks, per client
        self.assertEqual(len(a.messages), 16)

    def test_lost_and_duplicate(self):
        a = A.align(scenario(lost={(2, 2)}, dup_seq=4))
        self.assertEqual(len(a.lost), 1)
        self.assertEqual(a.lost[0][:2], (0, 2))
        self.assertEqual(len(a.duplicates), 1)
        self.assertEqual(a.duplicates[0][:2], (0, 1))
        self.assertIn("lost: 1", A.format_alignment(a))

    def test_enet_peer_ids_are_mapped_to_slots(self):
        welcome = bytes([0xFC, 0x4E, 5, 2, 0, 9, 2, 0, 0, 0, 0, 0])   # no strings, session 9, slot 2
        other = bytes([0xFC, 0x4E, 5, 4, 0, 9, 1, 1, 1, 1, 1, 1])
        h, c = TB(0), TB(2)
        h.net(True, 10, 7, 2, 0, welcome)       # host sent the Welcome to ENet peer 7
        h.net(True, 20, 7, 4, 1, other)
        c.net(False, 15, 0, 2, 0, welcome)      # client sees the host as peer 0
        c.net(False, 25, 0, 4, 1, other)
        c.net(True, 30, 0, 5, 1, other[::-1])
        h.net(False, 35, 7, 5, 1, other[::-1])
        a = A.align([h.trace(), c.trace()])
        self.assertEqual((len(a.messages), len(a.lost), len(a.orphans)), (3, 0, 0))
        self.assertEqual({(m["from"], m["to"]) for m in a.messages}, {(0, 2), (2, 0)})

    def test_orphan_input(self):
        t0, t1 = TB(0), TB(1)
        t0.add(N.T_MARKER, 1, kind=0, text="x")
        t1.net(False, 50, 0, 4, 99, b"\x04\x63\x00")  # arrived from slot 0, slot 0 never sent it
        a = A.align([t0.trace(), t1.trace()])
        self.assertEqual(len(a.orphans), 1)

    def test_duplicate_slot_and_dropped_notes(self):
        t = scenario()
        t[1]["header"]["slot"] = 2
        t[0]["header"]["records_dropped"] = 5
        a = A.align(t)
        self.assertEqual(len(a.slots), 3)
        self.assertTrue(any("two traces claim slot 2" in x for x in a.anomalies))
        self.assertTrue(any("lost 5 old records" in x for x in a.anomalies))


class DivergenceTests(unittest.TestCase):
    def test_clean_run_has_no_divergence(self):
        a = A.align(scenario(digest2="same", clean_extras=True, dup_seq=4))
        self.assertIsNone(A.find_divergence(a))
        self.assertIn("No divergence", A.format_divergence(a, None))

    def test_bad_txn_is_first_and_names_the_culprit(self):
        a = A.align(scenario(bad_seq=3, lost={(2, 2)}))
        d = A.find_divergence(a)
        self.assertEqual(d["kind"], "txn-hash")
        self.assertEqual(d["key"], "txn:3[0]")
        self.assertEqual(d["suspects"], [1])
        self.assertEqual(d["slots"], [0, 1, 2])
        direct = [e for e in d["chain"]["records"] if e["role"] == "direct"]
        self.assertTrue({0, 1, 2} <= {e["slot"] for e in direct})
        self.assertTrue(all(e["rec"].get("seq") == 3 for e in direct if e["rec"]["name"].startswith("Txn")))
        self.assertTrue(any("lost in transit" in n for n in d["chain"]["notes"]))
        self.assertTrue(any(c["kind"] == "digest" for c in d["also"]))
        ts = [e["aligned_us"] for e in d["chain"]["records"]]
        self.assertEqual(ts, sorted(ts))
        text = A.format_divergence(a, d)
        self.assertIn("FIRST DIVERGENCE (txn-hash)", text)
        self.assertIn("suspect slot(s): [1]", text)

    def test_digest_divergence_names_the_key_and_window(self):
        a = A.align(scenario())
        d = A.find_divergence(a)
        self.assertEqual(d["kind"], "digest")
        self.assertEqual(d["suspects"], [1])
        self.assertEqual(d["diff_keys"], ["0x0000000000000020"])
        self.assertEqual(d["key"], "story:0x0000000000000020")
        lo, hi = d["chain"]["window_us"]
        agree = max(a.t(s, r) for s in a.slots for r in a.traces[s]["records"] if r["type"] == N.T_DIGEST and r["seq"] == 1)
        self.assertEqual(lo, agree)
        self.assertEqual(hi, d["aligned_us"])
        for e in d["chain"]["records"]:
            self.assertGreaterEqual(e["aligned_us"], lo)
            self.assertLessEqual(e["aligned_us"], hi)

    def test_shard_level_digest(self):
        tb = [TB(0), TB(1)]
        tb[0].digest(10, 1, 3, 1, shards=[5, 6, 7])
        tb[1].digest(10, 1, 3, 2, shards=[5, 9, 7])
        d = A.find_divergence(A.align([t.trace() for t in tb]))
        self.assertEqual(d["diff_shards"], [1])
        self.assertEqual(d["key"], "actors:shard:1")

    def test_per_key_detail_records_and_entry_markers(self):
        key = 0xAA650F118B7CB748
        tb = [TB(0), TB(1)]
        for t, h in zip(tb, (11, 22)):
            t.add(N.T_DIGEST, 900, category=4, epoch=1, seq=0, hash=h, item_count=3, shards=[0] * 255 + [h], keys={})
        # detail records (flag bit2): the entries arrive split over two records on each machine
        for i, (t, val) in enumerate(zip(tb, (5, 77))):
            t.add(N.T_DIGEST, 901, category=4, epoch=1, seq=0, hash=0, item_count=3, shards=[], keys={0x10: 1, key: val}, detail=True)
            t.add(N.T_DIGEST, 902, category=4, epoch=1, seq=0, hash=0, item_count=3, shards=[], keys={0x20: 2}, detail=True)
            t.add(N.T_MARKER, 903, kind=0, text="entry %016x %d SetStage 203@falloutnv.esm = %d" % (key, val, val))
        tb[1].keys_extra = None
        d = A.find_divergence(A.align([t.trace() for t in tb]))
        self.assertEqual(d["kind"], "digest")
        self.assertEqual(d["diff_keys"], ["0x%016X" % key])
        self.assertEqual(d["diff_shards"], [255])
        self.assertIn("SetStage 203@falloutnv.esm = 5", d["detail"])
        self.assertIn("SetStage 203@falloutnv.esm = 77", d["detail"])
        self.assertEqual(N.loads(tb[0].data())["records"][2]["detail"], True)
        nw = d["narrowing"]
        self.assertEqual(nw["buckets"], [255])
        self.assertEqual([k["key"] for k in nw["keys"]], ["0x%016X" % key])
        k = nw["keys"][0]
        self.assertEqual(k["bucket"], 0x48)
        self.assertEqual(k["values"], {0: 5, 1: 77})
        self.assertIn("= 77", k["texts"][1])
        text = A.format_divergence(A.align([t.trace() for t in tb]), d)
        self.assertIn("narrowing: bucket(s) 255 of 256", text)
        self.assertIn("slot 1: value hash 77", text)
        self.assertIn("NOT in a differing bucket", text)    # the test's bucket 255 does not match key & 0xFF (0x48)

    def test_story_bucket_is_narrowed_to_a_key_by_a_later_desync_dump(self):
        key = 0xAA650F118B7CB748
        tb = [TB(0), TB(1)]
        for t, h in zip(tb, (11, 22)):   # a story digest: buckets only, bucket 0x48 differs
            t.add(N.T_DIGEST, 100, category=0, epoch=1, seq=7, hash=h, item_count=3, shards=[0] * 0x48 + [h] + [0] * (255 - 0x48), keys={})
        for t, val in zip(tb, (0, 77)):   # later, the desync dump of the real world with key detail (and an extra identical key)
            t.add(N.T_DIGEST, 900, category=4, epoch=1, seq=0, hash=val + 1, item_count=2, shards=[0] * 0x48 + [val] + [0] * (255 - 0x48), keys={})
            t.add(N.T_DIGEST, 901, category=4, epoch=1, seq=0, hash=0, item_count=2, shards=[], keys={0x10: 1, 0x21: val + 1, key: val}, detail=True)
            t.add(N.T_MARKER, 903, kind=0, text="entry %016x %d SetStage 203@falloutnv.esm = %d" % (key, val, val))
        a = A.align([t.trace() for t in tb])
        cands = [c for c in A.find_all(a) if c["kind"] == "digest" and c["category"] == 0]
        self.assertEqual(len(cands), 1)
        nw = cands[0]["narrowing"]
        self.assertEqual(nw["buckets"], [0x48])
        self.assertEqual([k["key"] for k in nw["keys"]], ["0x%016X" % key])
        self.assertTrue(nw["keys"][0]["in_diff_bucket"])
        self.assertTrue(nw["key_source"].startswith("other digest"))   # category 4 (the desync dump) supplied the keys
        self.assertIn("= 77", nw["keys"][0]["texts"][1])

    def test_narrowing_without_key_detail_says_so(self):
        tb = [TB(0), TB(1)]
        tb[0].digest(10, 1, 3, 1, shards=[0] * 256)
        tb[1].digest(10, 1, 3, 2, shards=[0] * 9 + [4] + [0] * 246)
        d = A.find_divergence(A.align([t.trace() for t in tb]))
        self.assertEqual(d["narrowing"]["buckets"], [9])
        self.assertEqual(d["narrowing"]["keys"], [])
        self.assertIn("no per-key detail recorded", A.format_divergence(A.align([t.trace() for t in tb]), d))

    def test_host_commit_is_not_reported_as_a_missing_apply(self):
        tb = [TB(0), TB(1)]
        tb[0].txn(N.T_TXN_CAPTURED, 100, 1, 5)
        tb[1].txn(N.T_TXN_APPLIED, 150, 1, 5)
        tb[0].add(N.T_MARKER, 5_000_000, kind=0, text="late")   # the host's trace goes on for seconds
        a = A.align([t.trace() for t in tb])
        self.assertEqual([c for c in A.find_all(a) if c["kind"] == "txn-missing"], [])

    def test_earliest_wins_over_later(self):
        a = A.align(scenario(bad_seq=2))
        d = A.find_divergence(a)
        self.assertEqual(d["key"], "txn:2[0]")
        a = A.align(scenario(bad_seq=4))
        self.assertEqual(A.find_divergence(a)["key"], "txn:4[0]")  # digest at 700 is later than seq 4 at 400

    def test_ownership_conflict_and_stale(self):
        tb = [TB(0), TB(1), TB(2)]
        tb[0].own(100, 0, 5, 3, 255, 1)
        tb[2].own(110, 0, 5, 3, 255, 2)
        d = A.find_divergence(A.align([t.trace() for t in tb]))
        self.assertEqual(d["kind"], "ownership-conflict")
        self.assertEqual(d["suspects"], [1, 2])
        tb = [TB(0), TB(1)]
        tb[1].own(100, 0, 6, 4, 255, 1)
        tb[1].own(120, 1, 6, 3, 1, 0)
        d = A.find_divergence(A.align([t.trace() for t in tb]))
        self.assertEqual(d["kind"], "ownership-stale")
        self.assertEqual(d["suspects"], [1])
        # rejected stale records are the system working, not a divergence
        tb = [TB(0), TB(1)]
        tb[1].own(100, 0, 6, 4, 255, 1)
        tb[1].own(120, 3, 6, 3, 1, 0)
        tb[1].own(130, 4, 6, 3, 1, 0)
        tb[1].own(140, 5, 6, 2, 1, 0)
        tb[0].own(150, 5, 6, 1, 0, 2)
        self.assertIn("rejectedNonOwner", N.describe(tb[1].trace()["records"][-1]))
        self.assertIsNone(A.find_divergence(A.align([t.trace() for t in tb])))

    def test_desync_marker_is_reported_with_its_actor_history(self):
        tb = [TB(0), TB(1)]
        tb[0].own(100, 1, 0x100A, 16, 1, 0)
        tb[0].own(200, 1, 0x100A, 22, 0, 1)
        tb[0].own(210, 1, 0x200B, 3, 0, 1)
        tb[1].add(N.T_MARKER, 900, kind=1, text="stale_applied: update at epoch 11 accepted: ref=#100a epoch=11")
        tb[1].add(N.T_MARKER, 910, kind=1, text="stale_applied: again: ref=#100a")
        d = A.find_divergence(A.align([t.trace() for t in tb]))
        self.assertEqual((d["kind"], d["suspects"], d["key"]), ("desync-marker", [1], "marker:stale_applied"))
        self.assertEqual(d["total_candidates"], 1)   # the repeat is folded
        direct = [e["rec"]["ref"] for e in d["chain"]["records"] if e["role"] == "direct" and e["rec"]["type"] == N.T_OWNERSHIP]
        self.assertEqual(direct, ["actor:100A", "actor:100A"])
        # a real state divergence found earlier outranks a later marker
        self.assertIn("slot 1 reported", d["detail"])

    def test_double_apply(self):
        tb = [TB(0), TB(1)]
        for t in (100, 110):
            tb[1].txn(N.T_TXN_APPLIED, t, 5, 9)
        tb[0].txn(N.T_TXN_APPLIED, 90, 5, 9)
        d = A.find_divergence(A.align([t.trace() for t in tb]))
        self.assertEqual(d["kind"], "txn-double")
        self.assertEqual(d["suspects"], [1])

    def test_missing_txn_needs_a_gap_or_grace(self):
        tb = [TB(0), TB(1)]
        tb[0].txn(N.T_TXN_APPLIED, 100, 5, 9)
        tb[0].txn(N.T_TXN_APPLIED, 200, 6, 9)
        tb[1].txn(N.T_TXN_APPLIED, 210, 6, 9)
        d = A.find_divergence(A.align([t.trace() for t in tb]))
        self.assertEqual((d["kind"], d["key"], d["suspects"]), ("txn-missing", "txn:5", [1]))
        # no later seq, and the trace ends within the grace time: just lag, not a bug
        tb = [TB(0), TB(1)]
        tb[0].txn(N.T_TXN_APPLIED, 100, 5, 9)
        tb[1].add(N.T_MARKER, 900, kind=0, text="x")
        self.assertIsNone(A.find_divergence(A.align([t.trace() for t in tb])))
        # same, but the machine kept recording well past the grace time
        tb[1].add(N.T_MARKER, 3500, kind=0, text="x")
        d = A.find_divergence(A.align([t.trace() for t in tb]))
        self.assertEqual(d["kind"], "txn-missing")
        self.assertIn("no apply within", d["detail"])


class ShrinkTests(unittest.TestCase):
    def setUp(self):
        os.makedirs(TMP_ROOT, exist_ok=True)
        self.tmp = tempfile.mkdtemp(dir=TMP_ROOT)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def fixture(self, n=120, with_dup=True):
        steps = [{"op": "deliver", "to": 1 + (i % 2), "from": 0, "hex": "01%02x" % (i % 200)} for i in range(n)]
        steps[n // 7] = {"op": "deliver", "to": 1, "from": 0, "hex": "ee01"}
        steps[n * 5 // 6] = {"op": "deliver", "to": 1, "from": 0, "hex": "ff01"}
        if with_dup:
            steps[n // 3] = {"op": "inject", "kind": "dup", "slot": 2}
        steps.append({"op": "quiesce", "pin": True})
        return {"version": 1, "config": {"seed": 3, "clients": 2}, "steps": steps}

    def test_ddmin_finds_exact_subset(self):
        need = {3, 77, 150}
        calls = []

        def fails(sel):
            calls.append(len(sel))
            return need <= set(sel)
        kept, runs, complete = S.ddmin(300, fails)
        self.assertEqual(sorted(kept), sorted(need))
        self.assertTrue(complete)
        self.assertLess(runs, 150)

    def test_ddmin_budget_and_trivial(self):
        kept, runs, complete = S.ddmin(300, lambda sel: {3, 77} <= set(sel), max_runs=5)
        self.assertFalse(complete)
        self.assertEqual(runs, 5)
        self.assertEqual(S.ddmin(10, lambda sel: True)[0], [])
        self.assertEqual(S.ddmin(0, lambda sel: True)[0], [])

    @slow
    def test_shrink_toy_to_two_steps_plus_pin(self):
        orc = S.CommandOracle(REPLAY, tmpdir=self.tmp)
        out = S.shrink_fixture(self.fixture(), orc)
        ops = [(s["op"], s.get("hex")) for s in out["steps"]]
        self.assertEqual(ops, [("deliver", "ee01"), ("deliver", "ff01"), ("quiesce", None)])
        self.assertEqual(out["shrunk"]["original_steps"], 121)
        self.assertEqual(out["shrunk"]["reduced_steps"], 3)
        self.assertEqual(out["shrunk"]["signature"], "stale echo applied on slot #")
        self.assertTrue(out["shrunk"]["complete"])
        self.assertEqual(out["shrunk"]["violations"], ["stale echo applied on slot 1"])
        self.assertEqual(os.listdir(self.tmp), [])  # temp fixtures cleaned up

    def test_pinned_steps_survive(self):
        def orc(fx):
            ops = [(s.get("hex")) for s in fx["steps"]]
            bad = "ee01" in ops and "ff01" in ops
            return {"diverged": bad, "violations": ["x"] if bad else []}
        out = S.shrink_fixture(self.fixture(with_dup=False), orc)
        self.assertEqual(out["steps"][-1], {"op": "quiesce", "pin": True})
        self.assertEqual(len(out["steps"]), 3)

    @slow
    def test_different_failure_is_rejected(self):
        # the dup bug alone is a different failure; the shrink must keep the original one
        out = S.shrink_fixture(self.fixture(), S.CommandOracle(REPLAY, tmpdir=self.tmp))
        self.assertFalse(any(s.get("op") == "inject" for s in out["steps"]))

    @slow
    def test_budget_marks_incomplete(self):
        out = S.shrink_fixture(self.fixture(), S.CommandOracle(REPLAY, tmpdir=self.tmp), max_runs=4)
        self.assertFalse(out["shrunk"]["complete"])
        self.assertGreater(out["shrunk"]["reduced_steps"], 3)

    @slow
    def test_nothing_to_shrink_and_errors(self):
        fx = self.fixture(with_dup=False)
        fx["steps"] = [s for s in fx["steps"] if s.get("hex") != "ff01"]
        with self.assertRaises(S.OracleError):
            S.shrink_fixture(fx, S.CommandOracle(REPLAY, tmpdir=self.tmp))
        with self.assertRaises(S.OracleError):
            S.CommandOracle(REPLAY, tmpdir=self.tmp)({"version": 2, "steps": []})   # replay exits 2
        with self.assertRaises(S.OracleError):
            S.CommandOracle([sys.executable, "-I", TOY])                              # no {fixture}
        with self.assertRaises(S.OracleError):
            S.CommandOracle(["definitely-not-a-program-xyz", "{fixture}"], tmpdir=self.tmp)({"version": 1, "steps": []})

    @slow
    def test_simfuzz_scenarios_shrink_via_ops(self):
        fx = self.fixture()
        sim = {"format": "simfuzz-1", "seed": 5, "machines": 3, "ops": fx["steps"]}
        cmd = [sys.executable, "-I", TOY, "--replay", "{fixture}"]
        out = S.shrink_fixture(sim, S.CommandOracle(cmd, tmpdir=self.tmp))
        self.assertNotIn("steps", out)
        self.assertEqual([o.get("hex") for o in out["ops"]], ["ee01", "ff01", None])
        self.assertEqual(out["shrunk"]["signature"], "stale_echo_applied")
        self.assertEqual(out["format"], "simfuzz-1")
        paths = G.generate(out, "sim case", self.tmp)
        with open(paths[1]) as f:
            cm = f.read()
        self.assertIn("fnvcoop_simfuzz", cm)
        self.assertIn("--replay", cm)
        self.assertIn("Steps: 3", cm)

    def test_signature_normalises_numbers(self):
        r = {"diverged": True, "violations": ["actor 0xDEADBEEF dup on slot 3 hash 0123456789abcdef"]}
        self.assertEqual(S.signature(r), "actor # dup on slot # hash #")
        self.assertEqual(S.signature({"diverged": True, "first": {"category": "story"}}), "diverged:story")
        self.assertIsNone(S.signature({"diverged": False}))

    def test_fixture_from_traces(self):
        trs = scenario(bad_seq=3)
        # one truncated datagram is skipped and counted
        tb = TB(1)
        tb.net(False, 5, 0, 4, 1, b"\x04\x01\x01", flags=1)
        trs[1]["records"] += [dict(r, idx=999 + i) for i, r in enumerate(N.loads(tb.data())["records"])]
        fx = S.fixture_from_traces(trs)
        deliver = [s for s in fx["steps"] if s["op"] == "deliver"]
        n_in = sum(1 for t in trs for r in t["records"] if r["type"] == N.T_NET_IN and not r["truncated"])
        self.assertEqual(len(deliver), n_in)
        self.assertEqual(fx["source"]["skipped_steps"], 1)
        self.assertEqual([s for s in fx["steps"] if s["op"] not in ("deliver", "quiesce")], [])
        self.assertEqual(fx["steps"][-1], {"op": "quiesce", "pin": True})
        ts = [s["t"] for s in fx["steps"] if "t" in s]
        self.assertEqual(ts, sorted(ts))
        self.assertEqual(fx["config"]["clients"], 2)
        self.assertEqual(bytes.fromhex(deliver[0]["hex"])[0] in (4, 5), True)


@slow
class GentestTests(unittest.TestCase):
    def setUp(self):
        os.makedirs(TMP_ROOT, exist_ok=True)
        self.tmp = tempfile.mkdtemp(dir=TMP_ROOT)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def shrunk(self):
        fx = ShrinkTests("test_ddmin_budget_and_trivial").fixture(n=20, with_dup=False)
        fx["steps"] = [fx["steps"][0], {"op": "deliver", "to": 1, "from": 0, "hex": "ee01"},
                       {"op": "deliver", "to": 1, "from": 0, "hex": "ff01"}, fx["steps"][-1]]
        fx["shrunk"] = {"signature": "stale echo applied on slot #"}
        return fx

    def test_generate_and_run(self):
        paths = G.generate(self.shrunk(), "Stale Echo!", self.tmp)
        self.assertEqual([os.path.basename(p) for p in paths], ["stale_echo.json", "stale_echo.cmake"])
        with open(paths[0]) as f:
            fx = json.load(f)
        self.assertEqual(fx["expect"], {"converges": True, "bug_signature": "stale echo applied on slot #"})
        with open(paths[1]) as f:
            cm = f.read()
        self.assertIn("add_test(NAME flight_stale_echo", cm)
        self.assertIn("fnvcoop_replay", cm)
        self.assertEqual(G.safe_name("../../x y"), "x_y")
        self.assertEqual(G.safe_name("!!!"), "case")
        # the bug is still present in the toy, so the regression test must FAIL (it guards a bug that is not fixed yet)
        n, bad = RG.run_dir(self.tmp, S.CommandOracle(REPLAY, tmpdir=self.tmp))
        self.assertEqual((n, bad), (1, 1))

    def test_regress_expectations(self):
        d = self.tmp
        good = {"version": 1, "steps": [{"op": "quiesce"}], "expect": {"converges": True}}
        known_bad = dict(self.shrunk(), expect={"converges": False})
        for name, fx in (("a_good", good), ("b_known_bad", known_bad)):
            with open(os.path.join(d, name + ".json"), "w") as f:
                json.dump(fx, f)
        n, bad = RG.run_dir(d, S.CommandOracle(REPLAY, tmpdir=d))
        self.assertEqual((n, bad), (2, 0))
        self.assertEqual(RG.main([d, "--replay-cmd", subprocess.list2cmdline(REPLAY), "--tmpdir", d]), 0)


class ReportTests(unittest.TestCase):
    def test_report_content_and_escaping(self):
        evil = "<script>alert(1)</script>"
        html = R.render(scenario(bad_seq=3, instance1=evil), {"steps": [{"op": "deliver", "hex": "<u>"}],
                                                              "shrunk": {"original_steps": 9, "reduced_steps": 1, "oracle_runs": 4, "signature": "<i>x"}})
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)
        self.assertNotIn("<u>", html)
        self.assertNotIn("<i>x", html)
        self.assertIn("First divergence", html)
        self.assertIn("txn:3", html)
        self.assertIn("<svg", html)
        self.assertIn("Minimal reproduction", html)
        self.assertNotIn("http://", html.replace("http://www.w3.org", ""))
        self.assertTrue(html.startswith("<!doctype html>"))

    def test_report_shows_the_narrowing(self):
        key = 0xAA650F118B7CB748
        tb = [TB(0), TB(1)]
        for t, val in zip(tb, (0, 77)):
            t.add(N.T_DIGEST, 900, category=4, epoch=1, seq=0, hash=val + 1, item_count=1, shards=[0] * 0x48 + [val] + [0] * (255 - 0x48), keys={})
            t.add(N.T_DIGEST, 901, category=4, epoch=1, seq=0, hash=0, item_count=1, shards=[], keys={key: val}, detail=True)
            t.add(N.T_MARKER, 903, kind=0, text="entry %016x %d SetStage 203@falloutnv.esm = <b>%d" % (key, val, val))
        html = R.render([t.trace() for t in tb])
        self.assertIn("Narrowing: digest, bucket, key, value", html)
        self.assertIn("0xAA650F118B7CB748", html)
        self.assertIn('<td class="m">SetStage 203@falloutnv.esm = &lt;b&gt;77</td>', html)
        self.assertNotIn("<b>77", html)

    def test_clean_report(self):
        html = R.render(scenario(digest2="same"))
        self.assertIn("No divergence found", html)
        self.assertNotIn("Minimal reproduction", html)


@slow
class CliTests(unittest.TestCase):
    def setUp(self):
        os.makedirs(TMP_ROOT, exist_ok=True)
        self.tmp = tempfile.mkdtemp(dir=TMP_ROOT)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_cli(self, *args):
        p = subprocess.run([sys.executable, "-I", os.path.join(SRC, "flight.py")] + list(args), capture_output=True, text=True, errors="replace")
        return p.returncode, p.stdout + p.stderr

    def test_end_to_end(self):
        files = []
        for tr in scenario(bad_seq=3):
            p = os.path.join(self.tmp, "s%d.nvfr" % tr["header"]["slot"])
            N.dump(p, tr["header"], [dict(r) for r in tr["records"]])
            files.append(p)
        rc, out = self.run_cli("print", files[0], "--type", "digest")
        self.assertEqual(rc, 0)
        self.assertIn("Digest", out)
        rc, out = self.run_cli("align", *files)
        self.assertIn("slots: [0, 1, 2]", out)
        rc, out = self.run_cli("diverge", *files, "--check")
        self.assertEqual(rc, 1)
        self.assertIn("FIRST DIVERGENCE (txn-hash)", out)
        rc, out = self.run_cli("diverge", *files, "--json")
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["key"], "txn:3[0]")
        fx = os.path.join(self.tmp, "fx.json")
        rc, out = self.run_cli("fixture", *files, "-o", fx)
        self.assertEqual(rc, 0, out)
        # a hand-made failing fixture for the shrinker (the toy replay does not understand real datagrams)
        toyfx = ShrinkTests("test_ddmin_budget_and_trivial").fixture(n=60, with_dup=False)
        with open(fx, "w") as f:
            json.dump(toyfx, f)
        sh = os.path.join(self.tmp, "sh.json")
        rc, out = self.run_cli("shrink", fx, "--replay-cmd", subprocess.list2cmdline(REPLAY), "-o", sh, "--tmpdir", self.tmp)
        self.assertEqual(rc, 0, out)
        self.assertIn("-> 3 steps", out)
        rd = os.path.join(self.tmp, "reg")
        rc, out = self.run_cli("gentest", sh, "--name", "echo", "--out", rd)
        self.assertEqual(rc, 0, out)
        self.assertTrue(os.path.exists(os.path.join(rd, "echo.json")))
        rc, out = self.run_cli("regress", rd, "--replay-cmd", subprocess.list2cmdline(REPLAY), "--tmpdir", self.tmp)
        self.assertEqual(rc, 1)  # bug still present in the toy
        self.assertIn("FAIL echo.json", out)
        rep = os.path.join(self.tmp, "r.html")
        rc, out = self.run_cli("report", *files, "-o", rep, "--shrink", sh)
        self.assertEqual(rc, 0, out)
        with open(rep, encoding="utf-8") as f:
            self.assertIn("Minimal reproduction", f.read())
        rc, out = self.run_cli("diverge", files[0])
        self.assertNotEqual(rc, 0)
        self.assertIn("at least 2 traces", out)
        rc, out = self.run_cli("print", os.path.join(self.tmp, "nope.nvfr"))
        self.assertNotEqual(rc, 0)


# ---------------------------------------------------------------- failure dir -> offline repro (from_failure.py, trace_replay.py)

BS = chr(92)


def pair(skip=(), heal=False, marker=False, digest_bad=False, n=6, bad_seq=None, far=False):
    """Host and one client. skip: seqs the client never applies. heal: a snapshot apply after the last txn. marker: the host's own desync marker, early.
    far: plenty of unrelated note markers minutes later (the part of a long recording a shrink should cut away)."""
    h, c = TB(0), TB(1)
    if marker:
        h.add(N.T_MARKER, 5, kind=1, text="host: a client digest did not match (mismatches=1)")
    for seq in range(1, n + 1):
        t = seq * 100
        raw = bytes([4, seq, 1, 0xAB])
        h.txn(N.T_TXN_CAPTURED, t - 1, seq, good_hash(seq))
        h.txn(N.T_TXN_APPLIED, t, seq, good_hash(seq))
        h.net(True, t, 1, 4, seq, raw)
        if seq in skip:
            continue
        c.net(False, t + 5, 0, 4, seq, raw)
        c.txn(N.T_TXN_APPLIED, t + 6, seq, 0xBAD if bad_seq == seq else good_hash(seq))
    if heal:
        c.txn(N.T_TXN_APPLIED, n * 100 + 50, 0, 0x77, flags=N.AP_SNAPSHOT)
    if far:
        for k in range(40):
            h.add(N.T_MARKER, 5000 + 5000 * k, kind=0, text="note %d" % k)
    h.digest(900, 0, 1, 0xAAAA, keys={0x10: 5})
    c.digest(900, 0, 1, 0xBBBB if digest_bad else 0xAAAA, keys={0x10: 6 if digest_bad else 5})
    return [h, c]


def make_run(root, tbs, run="fight-x", fail="FAIL-1-01-the_step", status=None, same_names=False, dump_paths=True):
    """Lay out what run_fight leaves behind: <root>/instances/<n>/nvcoop_flight/*.nvfr and <root>/out/<run>/enemy_fight/<fail>/failure.json.
    -> (failure_dir, instances_dir)."""
    inst = os.path.join(root, "instances")
    fdir = os.path.join(root, "out", run, "enemy_fight", fail)
    os.makedirs(fdir, exist_ok=True)
    machines = []
    for i, tb in enumerate(tbs):
        d = os.path.join(inst, str(i), "nvcoop_flight")
        os.makedirs(d, exist_ok=True)
        fn = "same.nvfr" if same_names else "nvcoop-manual-%d.nvfr" % i
        with open(os.path.join(d, fn), "wb") as f:
            f.write(tb.data())
        m = {"name": "host" if i == 0 else "client%d" % i, "role": "host" if i == 0 else "client", "instance": i, "status": (status or {}).get(i, {})}
        if dump_paths:
            m["flight_dump"] = {"path": "nvcoop_flight" + BS + fn}
        else:
            m["flight_dump_error"] = "flight.dump: [-32000] dump failed (no dir configured or not writable)"
        machines.append(m)
    with open(os.path.join(fdir, "failure.json"), "w", encoding="utf-8") as f:
        json.dump({"playbook": "enemy_fight", "step": "01-the step", "reason": "desync counters rose", "kind": "counters", "machines": machines}, f)
    return fdir, inst


def run_ff(*args):
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = FF.main(list(args))
    return rc, buf.getvalue()


class TraceReplayTests(unittest.TestCase):
    def test_split_roundtrip_and_truncated_tail(self):
        data = pair()[0].data()
        hdr, recs, h = TR.split_nvfr(data)
        self.assertEqual(hdr + b"".join(recs), data)
        self.assertEqual(len(recs), len(N.loads(data)["records"]))
        self.assertEqual(h["slot"], 0)
        self.assertGreater(len(recs[0]), 18)
        hdr2, recs2, _ = TR.split_nvfr(data + recs[0][:18])      # a half-written record at the end (whole header, cut payload) is dropped
        self.assertEqual(recs2, recs)

    def classify(self, **kw):
        return TR.classify([t.trace() for t in pair(**kw)])

    def test_classes(self):
        cl = self.classify(bad_seq=3)
        self.assertEqual((cl["primary"]["class"], cl["primary"]["key"]), ("divergence", "txn:3[0]"))
        cl = self.classify(digest_bad=True)
        self.assertEqual(cl["primary"]["class"], "digest-mismatch")
        self.assertEqual(cl["primary"]["suspects"], [0, 1])
        cl = self.classify(skip={2})
        self.assertEqual((cl["primary"]["class"], cl["primary"]["key"], cl["primary"]["suspects"]), ("stuck-sync", "txn:2", [1]))
        self.assertFalse(cl["primary"]["healed"])
        self.assertFalse(self.classify()["found"])
        self.assertEqual(self.classify()["counts"], {})

    def test_gap_healed_by_a_snapshot_is_not_a_failure(self):
        cl = self.classify(skip={2}, heal=True)
        self.assertFalse(cl["found"])
        self.assertIsNone(cl["primary"])
        self.assertEqual(cl["counts"], {"transient-gap": 1})
        self.assertTrue(cl["items"][0]["healed"])
        # a snapshot BEFORE the missing txn heals nothing
        h, c = pair(skip={5})
        c.txn(N.T_TXN_APPLIED, 20, 0, 0x77, flags=N.AP_SNAPSHOT)
        cl = TR.classify([h.trace(), c.trace()])
        self.assertEqual(cl["primary"]["class"], "stuck-sync")

    def test_own_marker_leads_only_when_nothing_else_is_found(self):
        cl = self.classify(marker=True)
        self.assertEqual((cl["primary"]["class"], cl["primary"]["key"]), ("desync-marker", "marker:host"))
        cl = self.classify(marker=True, digest_bad=True)       # the marker comes first in time, the digest explains it
        self.assertEqual(cl["primary"]["class"], "digest-mismatch")
        self.assertEqual(cl["counts"]["desync-marker"], 1)
        res = TR.result_of(cl)
        self.assertTrue(res["violations"][0].startswith("digest-mismatch"), res["violations"])
        self.assertEqual(res["first"]["category"], "digest-mismatch")

    def fixture(self, **kw):
        tbs = pair(**kw)
        trs = [t.trace() for t in tbs]
        raw = [(t.h["slot"],) + TR.split_nvfr(t.data())[:2] for t in tbs]
        return FF.build_fixture(raw, trs, {}), trs

    def test_fixture_replays_like_the_traces(self):
        fx, trs = self.fixture(skip={2})
        self.assertEqual(TR.run(fx), TR.result_of(TR.classify(trs)))
        self.assertTrue(TR.run(fx)["diverged"])
        fx["steps"].append({"op": "quiesce", "pin": True})        # steps that are not records are ignored
        self.assertTrue(TR.run(fx)["diverged"])
        self.assertTrue(all(st["op"] == "rec" for st in fx["steps"][:-1]))
        clean, _ = self.fixture()
        self.assertEqual(TR.run(clean), {"diverged": False, "violations": [], "first": None, "counts": {}})

    def test_bad_and_thin_fixtures(self):
        for bad in ({}, {"format": "trace-1"}, {"format": "toy", "steps": [], "headers": {}}, {"format": "trace-1", "steps": [{"op": "rec", "slot": 7, "hex": "00"}], "headers": {"0": "00"}}):
            with self.assertRaises(TR.BadFixture):
                TR.run(bad)
        fx, _ = self.fixture(skip={2})
        thin = dict(fx, steps=[s for s in fx["steps"] if s["slot"] == 0])     # a machine with no records left: nothing to compare, no crash
        res = TR.run(thin)
        self.assertFalse(res["diverged"])
        real_align = TR.A.align
        TR.A.align = lambda traces: [][0]         # an analysis that cannot cope with a thin candidate counts as "no failure", not a crash
        try:
            res = TR.run(fx)
        finally:
            TR.A.align = real_align
        self.assertFalse(res["diverged"])
        self.assertIn("IndexError", res["error"])

    @slow
    def test_command_line_contract(self):
        d = tempfile.mkdtemp(dir=TMP_ROOT)
        try:
            fx, _ = self.fixture(skip={2})
            p = os.path.join(d, "a.json")
            with open(p, "w") as f:
                json.dump(fx, f)
            r = subprocess.run([sys.executable, "-I", os.path.join(SRC, "trace_replay.py"), p], capture_output=True, text=True)
            self.assertEqual(r.returncode, 1)
            self.assertTrue(json.loads(r.stdout)["diverged"])
            with open(p, "w") as f:
                f.write("{")
            r = subprocess.run([sys.executable, "-I", os.path.join(SRC, "trace_replay.py"), p], capture_output=True, text=True)
            self.assertEqual(r.returncode, 2)
            clean, _ = self.fixture()
            with open(p, "w") as f:
                json.dump(clean, f)
            r = subprocess.run([sys.executable, "-I", os.path.join(SRC, "trace_replay.py"), p], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0)
        finally:
            shutil.rmtree(d, ignore_errors=True)


class ShippedFixtureTests(unittest.TestCase):
    """The recorded in-game failures in tools/flight/regress/ (from_failure.py output, trace-1, no game needed to replay):
    each one must still be found, with the bug signature written in it. An analyzer change that stops seeing a real failure fails here."""
    def test_every_shipped_fixture_still_reproduces_its_signature(self):
        names = sorted(n for n in os.listdir(REGRESS) if n.endswith(".json"))
        self.assertTrue(names, "no fixtures in %s" % REGRESS)
        for n in names:
            with open(os.path.join(REGRESS, n), encoding="utf-8") as f:
                fx = json.load(f)
            want = fx.get("expect") or {}
            r = TR.oracle(fx)
            self.assertEqual(not r["diverged"], want.get("converges", True), n)
            if "bug_signature" in want:
                self.assertEqual(S.signature(r), want["bug_signature"], n)

    def test_the_regress_runner_agrees(self):
        n, bad = RG.run_dir(REGRESS, None)
        self.assertEqual((n, bad), (len([x for x in os.listdir(REGRESS) if x.endswith(".json")]), 0))


class FromFailureTests(unittest.TestCase):
    def setUp(self):
        os.makedirs(TMP_ROOT, exist_ok=True)
        self.tmp = tempfile.mkdtemp(dir=TMP_ROOT)
        self.cases = os.path.join(self.tmp, "cases")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_on(self, fdir, inst, *extra):
        return run_ff(fdir, "--instances", inst, "--out", self.cases, *extra)

    def test_end_to_end_reproduce_shrink_and_regress(self):
        fdir, inst = make_run(self.tmp, pair(skip={2}))
        rc, out = self.run_on(fdir, inst)
        self.assertEqual(rc, 0, out)
        self.assertIn("REPRODUCED: stuck-sync (txn:2)", out)
        cases = os.listdir(self.cases)
        self.assertEqual(len(cases), 1)         # one json, no cmake: trace cases replay in process
        self.assertTrue(cases[0].endswith(".json"))
        with open(os.path.join(self.cases, cases[0])) as f:
            text = f.read()
        fx = json.loads(text)
        self.assertEqual(fx["format"], "trace-1")
        self.assertEqual(fx["expect"]["converges"], False)
        self.assertEqual(fx["expect"]["bug_signature"], "stuck-sync txn:#")
        self.assertLessEqual(len(fx["steps"]), 3)
        self.assertGreater(fx["shrunk"]["original_steps"], 30)
        self.assertEqual(fx["source"]["playbook"], "enemy_fight")
        self.assertNotIn(self.tmp.replace(BS, "/"), text.replace(BS + BS, "/").replace(BS, "/"))     # no machine paths in a case that gets shared
        self.assertNotIn("nvcoop_flight", text)
        self.assertEqual(RG.run_dir(self.cases, None), (1, 0))      # the regression runner picks it up with no replay command
        self.assertEqual(RG.main([self.cases]), 0)

    def test_a_fixed_run_does_not_reproduce(self):
        fdir, inst = make_run(self.tmp, pair())
        rc, out = self.run_on(fdir, inst)
        self.assertEqual(rc, 1, out)
        self.assertIn("NOT REPRODUCED", out)
        self.assertFalse(os.path.isdir(self.cases))
        # healed by a snapshot: also not a failure
        fdir, inst = make_run(os.path.join(self.tmp, "b"), pair(skip={2}, heal=True))
        rc, out = self.run_on(fdir, inst)
        self.assertEqual(rc, 1, out)
        self.assertIn("transient gap", out)

    def test_expectation_flag_and_regress_flip(self):
        fdir, inst = make_run(self.tmp, pair(digest_bad=True))
        self.assertEqual(self.run_on(fdir, inst, "--expect-converges")[0], 0)
        self.assertEqual(RG.run_dir(self.cases, None), (1, 1))      # the recordings still show the failure, so a "converges" case fails
        shutil.rmtree(self.cases)
        self.assertEqual(self.run_on(fdir, inst)[0], 0)
        self.assertEqual(RG.run_dir(self.cases, None), (1, 0))
        p = os.path.join(self.cases, os.listdir(self.cases)[0])
        with open(p) as f:
            fx = json.load(f)
        fx["steps"] = [s for s in fx["steps"] if s["slot"] == 0]    # a case that stops reproducing is reported
        with open(p, "w") as f:
            json.dump(fx, f)
        self.assertEqual(RG.run_dir(self.cases, None), (1, 1))

    def test_shrink_keeps_the_same_failure(self):
        # a marker alone would also "fail", with fewer records; the shrink must keep the stuck sync it started from
        fdir, inst = make_run(self.tmp, pair(skip={2}, marker=True, far=True))
        rc, out = self.run_on(fdir, inst)
        self.assertEqual(rc, 0, out)
        fx = json.load(open(os.path.join(self.cases, os.listdir(self.cases)[0])))
        self.assertEqual(fx["shrunk"]["signature"], "stuck-sync txn:#")
        self.assertEqual(len(fx["steps"]), 2)
        self.assertEqual({s["ty"] for s in fx["steps"]}, {"TxnApplied"})
        self.assertTrue(any(s.startswith("window") for s in fx["shrunk"]["stages"]), fx["shrunk"]["stages"])
        self.assertTrue(any(s.startswith("dropped all NetIn") for s in fx["shrunk"]["stages"]), fx["shrunk"]["stages"])
        self.assertTrue(fx["shrunk"]["complete"])

    def test_a_stage_that_changes_the_failure_is_refused(self):
        fx, trs = TraceReplayTests("test_classes").fixture(skip={2}, digest_bad=True)
        primary = TR.classify(trs)["primary"]
        self.assertEqual(primary["class"], "stuck-sync")        # earlier than the digest mismatch
        old = FF.DROP_ORDER
        FF.DROP_ORDER = ("TxnApplied",)       # dropping them would leave only the digest mismatch: another failure
        try:
            out = FF.reduce_fixture(fx, primary, 500, 10)
        finally:
            FF.DROP_ORDER = old
        self.assertEqual(out["shrunk"]["signature"], "stuck-sync txn:#")
        self.assertEqual({s["ty"] for s in out["steps"]}, {"TxnApplied"})
        self.assertFalse(any(s.startswith("dropped") for s in out["shrunk"]["stages"]))

    def test_no_shrink_and_no_write(self):
        fdir, inst = make_run(self.tmp, pair(skip={2}))
        rc, out = self.run_on(fdir, inst, "--no-shrink")
        self.assertEqual(rc, 0, out)
        fx = json.load(open(os.path.join(self.cases, os.listdir(self.cases)[0])))
        self.assertEqual(len(fx["steps"]), fx["shrunk"]["original_steps"])
        self.assertGreater(len(fx["steps"]), 30)
        self.assertEqual(RG.run_dir(self.cases, None), (1, 0))
        shutil.rmtree(self.cases)
        rc, out = self.run_on(fdir, inst, "--no-write")
        self.assertEqual(rc, 0, out)
        self.assertFalse(os.path.isdir(self.cases))
        self.assertNotIn("wrote ", out)

    def test_case_names_are_stable_and_distinct(self):
        a = FF.case_name("fight-base", "FAIL-1-04-both_kill_enemy_1_every_hit_once_one_death_one_credit")
        b = FF.case_name("fight-base", "FAIL-1-04-both_kill_enemy_1_every_hit_once_one_death_one_credit_x")
        self.assertEqual(a, FF.case_name("fight-base", "FAIL-1-04-both_kill_enemy_1_every_hit_once_one_death_one_credit"))
        self.assertNotEqual(a, b)
        self.assertEqual(a[:30], b[:30])        # the readable part is cut short, the tag keeps them apart
        self.assertLessEqual(len(a), 48)
        self.assertEqual(FF.case_name("r", "f", "My Case!"), "my_case")

    def test_same_file_name_in_both_instances(self):
        fdir, inst = make_run(self.tmp, pair(skip={2}), same_names=True)
        rc, out = self.run_on(fdir, inst, "--no-write")
        self.assertEqual(rc, 0, out)            # each machine's recording is taken from its own instance folder
        self.assertIn("REPRODUCED", out)

    def test_missing_recordings_are_reported(self):
        fdir, inst = make_run(self.tmp, pair(skip={2}), dump_paths=False)
        for i in (0, 1):      # recordings far older than the failure are nobody's
            p = os.path.join(inst, str(i), "nvcoop_flight", "nvcoop-manual-%d.nvfr" % i)
            os.utime(p, (1_000_000, 1_000_000))
        rc, out = self.run_on(fdir, inst, "--no-write")
        self.assertEqual(rc, 2, out)
        self.assertIn("NO REPRO", out)
        self.assertIn("no dir configured", out)
        # one machine unreadable, the other fine: still not enough
        fdir, inst = make_run(os.path.join(self.tmp, "b"), pair(skip={2}))
        with open(os.path.join(inst, "1", "nvcoop_flight", "nvcoop-manual-1.nvfr"), "wb") as f:
            f.write(b"junk")
        rc, out = self.run_on(fdir, inst, "--no-write")
        self.assertEqual(rc, 2, out)
        self.assertIn("cannot read nvcoop-manual-1.nvfr", out)
        self.assertIn("got 1", out)

    def test_dump_guessed_from_file_time(self):
        import time
        fdir, inst = make_run(self.tmp, pair(skip={2}), dump_paths=False)
        now = time.time()
        for i in (0, 1):       # an old recording and a recent one per machine: the recent one is the failure's
            d = os.path.join(inst, str(i), "nvcoop_flight")
            old = os.path.join(d, "old.nvfr")
            shutil.copy(os.path.join(d, "nvcoop-manual-%d.nvfr" % i), old)
            os.utime(old, (now - 7200, now - 7200))
            os.utime(os.path.join(d, "nvcoop-manual-%d.nvfr" % i), (now - 60, now - 60))
        os.utime(os.path.join(fdir, "failure.json"), (now, now))
        c = FF.collect(os.path.join(fdir, "failure.json"), inst)
        self.assertEqual([os.path.basename(m["path"]) for m in c["machines"]], ["nvcoop-manual-0.nvfr", "nvcoop-manual-1.nvfr"])
        self.assertEqual({m["how"] for m in c["machines"]}, {"guessed from file time"})
        newer = os.path.join(inst, "0", "nvcoop_flight", "newer.nvfr")      # of two recent ones, the newest
        shutil.copy(os.path.join(inst, "0", "nvcoop_flight", "nvcoop-manual-0.nvfr"), newer)
        os.utime(newer, (now - 10, now - 10))
        c = FF.collect(os.path.join(fdir, "failure.json"), inst)
        self.assertEqual(os.path.basename(c["machines"][0]["path"]), "newer.nvfr")
        os.utime(newer, (now + 3600, now + 3600))        # written long after the failure: not its recording
        c = FF.collect(os.path.join(fdir, "failure.json"), inst)
        self.assertEqual(os.path.basename(c["machines"][0]["path"]), "nvcoop-manual-0.nvfr")

    def test_finding_failures(self):
        import time
        a, _ = make_run(self.tmp, pair(), run="fight-a")
        b, _ = make_run(self.tmp, pair(), run="fight-b")
        now = time.time()
        os.utime(os.path.join(a, "failure.json"), (now - 100, now - 100))
        os.utime(os.path.join(b, "failure.json"), (now, now))
        out_dir = os.path.join(self.tmp, "out")
        self.assertEqual(FF.find_failures([out_dir]), [os.path.join(b, "failure.json"), os.path.join(a, "failure.json")])      # newest first
        self.assertEqual(FF.find_failures([a]), [os.path.join(a, "failure.json")])
        self.assertEqual(FF.find_failures([os.path.join(a, "failure.json")]), [os.path.join(a, "failure.json")])
        old_root = FF.ROOT
        FF.ROOT = self.tmp
        try:
            self.assertEqual(FF.find_failures([]), [os.path.join(b, "failure.json")])        # no argument: the newest one
        finally:
            FF.ROOT = old_root
        rc, out = run_ff(os.path.join(self.tmp, "nowhere"))
        self.assertEqual(rc, 2)

    def test_recordings_given_directly(self):
        _, inst = make_run(self.tmp, pair(skip={2}))
        files = [os.path.join(inst, str(i), "nvcoop_flight", "nvcoop-manual-%d.nvfr" % i) for i in (0, 1)]
        rc, out = run_ff(*files, "--out", self.cases)
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(os.listdir(self.cases)), 1)
        rc, out = run_ff(files[0], files[0], "--no-write")      # the same machine twice is not a pair
        self.assertEqual(rc, 2, out)
        self.assertIn("recorded twice", out)

    def test_status_evidence_and_agreement(self):
        sick = {"desyncs": {"digests": {"mismatch": 2, "ok": 5, "repairs": 2, "effectsRepaired": 11},
                            "desyncs": [{"entries": [{"key": "SetStage 1@x.esm = 5", "host": "SetStage 1@x.esm = 5", "client": "SetStage 1@x.esm = 0"}]}]}}
        host = {"name": "host", "role": "host", "status": dict(sick, host={"lastSeq": 16, "allSynced": False, "peers": [{"slot": 1, "acked": 12}, {"slot": 2, "acked": 16}]})}
        cli = {"name": "client1", "role": "client", "status": dict(sick, connected=0)}
        ev = FF.status_evidence({"machines": [host, cli]})
        self.assertEqual((ev["digest_mismatch"], ev["digest_ok"], ev["repairs"], ev["effects_repaired"]), (4, 10, 4, 22))
        self.assertEqual(ev["lag"], ["slot 1 acked 12 of 16"])
        self.assertEqual(ev["disconnected"], ["client1"])
        self.assertEqual(len(ev["desync_keys"]), 1)
        self.assertEqual(len(ev["symptoms"]), 4)
        quiet = FF.status_evidence({"machines": [{"name": "host", "role": "host", "status": {"host": {"lastSeq": 3, "allSynced": True, "peers": [{"slot": 1, "acked": 3}]}}}]})
        self.assertEqual(quiet["symptoms"], [])
        unsure = FF.status_evidence({"machines": [{"name": "host", "role": "host", "status": {"host": {"lastSeq": 3, "allSynced": False, "peers": [{"slot": 1, "acked": 3}]}}}]})
        self.assertEqual(unsure["lag"], ["host reports allSynced=false"])
        self.assertEqual(FF.status_evidence({})["symptoms"], [])
        found = {"found": True, "items": [{"class": "digest-mismatch"}]}
        self.assertIn("digest mismatch in status and in recordings", FF.agreement(ev, found))
        self.assertIn("sync lag in status but not in recordings", FF.agreement(ev, found))
        self.assertIn("NOT visible", FF.agreement(ev, {"found": False, "items": []}))
        self.assertIn("no sync symptoms", FF.agreement(quiet, found))

    def test_status_symptoms_reach_the_report(self):
        sick = {"desyncs": {"digests": {"mismatch": 1, "repairs": 1, "effectsRepaired": 3}}}
        fdir, inst = make_run(self.tmp, pair(digest_bad=True), status={0: sick})
        rc, out = self.run_on(fdir, inst, "--no-write")
        self.assertEqual(rc, 0, out)
        self.assertIn("live status: digest mismatch x1", out)
        self.assertIn("check: digest mismatch in status and in recordings", out)

    def test_json_output_and_report(self):
        fdir, inst = make_run(self.tmp, pair(skip={2}))
        html = os.path.join(self.tmp, "r.html")
        rc, out = run_ff(fdir, "--instances", inst, "--no-write", "--json", "--report", html)
        self.assertEqual(rc, 0, out)
        rep = json.loads(out[:out.rindex("]") + 1])
        self.assertEqual(rep[0]["verdict"], "reproduced")
        self.assertEqual(rep[0]["primary"]["class"], "stuck-sync")
        self.assertNotIn("_traces", rep[0])
        self.assertTrue(os.path.getsize(html) > 100)

    @slow
    def test_flight_cli_subcommand(self):
        fdir, inst = make_run(self.tmp, pair(skip={2}))
        r = subprocess.run([sys.executable, "-I", os.path.join(SRC, "flight.py"), "from-failure", fdir, "--instances", inst, "--out", self.cases],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("REPRODUCED", r.stdout)
        r = subprocess.run([sys.executable, "-I", os.path.join(SRC, "flight.py"), "regress", self.cases], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("ALL OK", r.stdout)

    def test_trace_cases_do_not_get_cmake(self):
        fx, _ = TraceReplayTests("test_classes").fixture(skip={2})
        fx["shrunk"] = {"signature": "stuck-sync txn:#"}
        paths = G.generate(fx, "Case A", self.tmp, expect={"converges": False, "bug_signature": "x"})
        self.assertEqual([os.path.basename(p) for p in paths], ["case_a.json"])
        self.assertEqual(json.load(open(paths[0]))["expect"], {"converges": False, "bug_signature": "x"})
        plain = dict(fx, format="other")
        self.assertEqual(len(G.generate(plain, "Case B", self.tmp)), 2)
        self.assertEqual(json.load(open(os.path.join(self.tmp, "case_b.json")))["expect"]["converges"], True)

    def test_regress_needs_no_command_for_trace_cases_only(self):
        fx, _ = TraceReplayTests("test_classes").fixture(skip={2})
        os.makedirs(self.cases)
        with open(os.path.join(self.cases, "t.json"), "w") as f:
            json.dump(dict(fx, expect={"converges": False}), f)
        with open(os.path.join(self.cases, "u.json"), "w") as f:
            json.dump({"version": 1, "steps": [{"op": "quiesce"}]}, f)
        self.assertEqual(RG.run_dir(self.cases, None), (2, 1))       # the toy-style fixture has nobody to replay it
        self.assertEqual(RG.run_dir(self.cases, S.CommandOracle(REPLAY, tmpdir=self.tmp)), (2, 0))


# ---------------------------------------------------------------- mutation twin

MUTATIONS = [
    ("nvfr", "        if len(data) - pos - RECORD_HEADER < plen:\n            break", "        if False:\n            break", "truncated tail not detected"),
    ("nvfr", "rec[\"malformed\"] = True", "rec[\"malformed\"] = False", "malformed bodies hidden"),
    ("nvfr", "if h[\"version\"] != VERSION:", "if False:", "version not checked"),
    ("analyze", "a.offset_us[s] = int((fwd - back) / 2)", "a.offset_us[s] = int(fwd - back)", "clock offset wrong"),
    ("analyze", "c.sort(key=lambda x: (x[\"aligned_us\"], _PRIO[x[\"kind\"]], x[\"key\"]))", "c.sort(key=lambda x: (-x[\"aligned_us\"], _PRIO[x[\"kind\"]], x[\"key\"]))", "latest instead of earliest"),
    ("analyze", "if len(set(vals.values())) <= 1 and not diff_keys", "if len(set(vals.values())) < 0 and not diff_keys", "agreeing digests flagged"),
    ("analyze", "if len(lst) > 1:", "if len(lst) > 2:", "double apply missed"),
    ("analyze", "return not r[\"applied_flags\"] & N.APPLY_SPECIAL", "return True", "repair restatements counted as divergence"),
    ("analyze", "if len(owners) > 1:", "if len(owners) > 2:", "owner conflict missed"),
    ("analyze", "r[\"epoch\"] < prev", "r[\"epoch\"] <= prev", "idempotent regrant flagged"),
    ("analyze", "if gap or late:", "if gap:", "late txn missed"),
    ("analyze", "if gap or late:", "if late:", "gap txn missed"),
    ("analyze", "            if key[1] in a.traces:\n                a.lost.append", "            if False:\n                a.lost.append", "lost messages not found"),
    ("analyze", "known[r[\"peer\"]] = w", "pass", "ENet peer ids not mapped"),
    ("analyze", "if (s, head) in seen:", "if False:", "repeated markers not folded"),
    ("analyze", "r[\"type\"] == N.T_MARKER and not r.get(\"malformed\") and r[\"kind\"] == 1:", "r[\"type\"] == N.T_MARKER and not r.get(\"malformed\") and r[\"kind\"] == 0:", "desync markers ignored"),
    ("analyze", "t_lo = _last_agree(a, cand)\n        if t_lo is None:", "t_lo = None\n        if t_lo is None:", "window ignores last agreeing digest"),
    ("analyze", "elif r[\"peer_slot\"] in a.traces:\n            a.orphans.append", "elif False:\n            a.orphans.append", "orphans ignored"),
    ("shrink", "return r.get(\"diverged\") and signature(r) == sig","return r.get(\"diverged\")", "different failure accepted"),
    ("shrink", "pinned = [i for i, s in enumerate(steps) if s.get(\"pin\")]", "pinned = []", "pinned steps removed"),
    ("shrink", "if line.startswith(\"VIOLATION \"):", "if False:", "simfuzz violation line ignored"),
    ("shrink", "if comp and len(comp) < len(cur) and test(comp):", "if False:", "complements never tried"),
    ("shrink", "if r[\"truncated\"] or not r[\"raw\"]:", "if False:", "truncated datagrams replayed"),
    ("shrink", "if p.returncode not in (0, 1):", "if p.returncode not in (0, 1, 2):", "bad fixture treated as converged"),
    ("shrink", "if runs[0] >= max_runs or", "if False or", "budget ignored"),
    ("shrink", "if count and test([]):", "if False:", "empty failing set not found"),
    ("gentest", "\"converges\": True", "\"converges\": False", "wrong expectation emitted"),
    ("gentest", "re.sub(r\"[^a-z0-9_]+\", \"_\", s.lower())", "s.lower()", "unsafe file names"),
    ("analyze", "if k & 0xFF in diff_shards]", "if True]", "cross-digest keys not limited to the differing buckets"),
    ("analyze", "or d[\"captured\"]:", ":", "host commit counted as a missing apply"),
    ("analyze", "\"texts\": {s: a.entries.get(s, {}).get(ki) for s in slots}", "\"texts\": {s: None for s in slots}", "value text dropped"),
    ("report", "if nw and (nw[\"buckets\"] or nw[\"keys\"]):", "if False:", "narrowing missing from the report"),
    ("report", "(k[\"texts\"].get(s) or", "(\"\" or", "value text missing from the report"),
    ("report", "e(hd[\"instance\"])", "hd[\"instance\"]", "report not escaped"),
    ("report", "e(_step_text(st))", "_step_text(st)", "steps not escaped"),
    ("regress", "ok = got == want", "ok = got", "expected-divergence fixtures fail"),
    ("trace_replay", 'if c["kind"] == "txn-missing" and later:', "if False:", "gap healed by a snapshot counted as a failure"),
    ("trace_replay", 'any(t >= c["aligned_us"] for s', 'any(t <= c["aligned_us"] for s', "snapshot before the gap heals it"),
    ("trace_replay", 'FAILURE_CLASSES = ("digest-mismatch", "divergence", "stuck-sync", "desync-marker")',
     'FAILURE_CLASSES = ("digest-mismatch", "divergence", "desync-marker")', "a recorded in-game stuck sync is no longer a failure"),
    ("trace_replay", 'own = [i for i in real if i["class"] != "desync-marker"]', "own = []", "own desync marker leads over a real finding"),
    ("trace_replay", "real = ([p] + [i for i in real if i is not p]) if p else real", "real = real", "failure signature taken from the marker"),
    ("trace_replay", "if len(data) - pos - N.RECORD_HEADER < plen:\n            break\n        end", "if False:\n            break\n        end", "half-written record kept"),
    ("trace_replay", 'if st.get("op") == "rec":', "if True:", "non-record steps replayed"),
    ("trace_replay", 'fx.get("format") != FORMAT or', "False or", "foreign fixture format accepted"),
    ("trace_replay", "except Exception as ex:   # noqa: BLE001", "except ZeroDivisionError as ex:   # noqa: BLE001", "thin shrink candidate crashes the replay"),
    ("trace_replay", 'return 1 if res["diverged"] else 0', "return 0", "replay exit code ignores divergence"),
    ("trace_replay", '"diverged": cl["found"],', '"diverged": True,', "clean traces reported as failing"),
    ("from_failure", "cands.append(os.path.join(root, str(instance), rel))", "pass", "recording taken from the wrong instance folder"),
    ("from_failure", "return max(near)[1] if near else None", "return min(near)[1] if near else None", "oldest recording guessed"),
    ("from_failure", "0 <= failure_time - os.path.getmtime(f) <= GUESS_WINDOW_S or abs(failure_time - os.path.getmtime(f)) < 5", "True", "any recording guessed"),
    ("from_failure", 'if h["slot"] in slots:', "if False:", "same machine accepted twice"),
    ("from_failure", 'ev["digest_mismatch"] += int(dg.get("mismatch") or 0)', 'ev["digest_mismatch"] = int(dg.get("mismatch") or 0)', "digest mismatches not summed over machines"),
    ("from_failure", 'if (p.get("acked") or 0) < last:', 'if (p.get("acked") or 0) > last:', "sync lag not seen"),
    ("from_failure", 'if host.get("allSynced") is False and not ev["lag"]:', "if False:", "allSynced=false not seen"),
    ("from_failure", 'if m.get("role") != "host" and st.get("connected") in (0, False):', "if False:", "lost link not seen"),
    ("from_failure", '"and" if "digest-mismatch" in classes else "but not"', '"and" if False else "but not"', "status/recording agreement wrong for digests"),
    ("from_failure", '"and" if "stuck-sync" in classes else "but not"', '"and" if True else "but not"', "status/recording agreement wrong for lag"),
    ("from_failure", 'expect = {"converges": bool(opts.get("expect_converges")),', 'expect = {"converges": True,', "case expectation ignores the flag"),
    ("from_failure", 'return bool(r.get("diverged")) and S.signature(r) == sig', 'return bool(r.get("diverged"))', "shrink accepts a different failure"),
    ("from_failure", 'tag = hashlib.sha1(("%s/%s" % (run, fail)).encode()).hexdigest()[:8]', 'tag = ""', "case names collide"),
    ("from_failure", "found.sort(key=lambda f: (-os.path.getmtime(f), f))", "found.sort(key=lambda f: (os.path.getmtime(f), f))", "oldest failure first"),
    ("from_failure", "if len(traces) < 2:", "if len(traces) < 1:", "one recording treated as enough"),
    ("from_failure", 'return 1 if any(r["verdict"] == "not-reproduced" for r in reps) else 0', "return 0", "exit code ignores not-reproduced"),
    ("from_failure", '    if any(r["verdict"] == "no-recordings" for r in reps):\n        return 2', '    if any(r["verdict"] == "no-recordings" for r in reps):\n        return 0', "exit code ignores missing recordings"),
    ("from_failure", "for m in MARGINS_MS:", "for m in ():", "time window stage skipped"),
    ("from_failure", 'abs(s["at_ms"] - at) <= m', 'abs(s["at_ms"] - at) <= -1', "time window keeps nothing"),
    ("from_failure", "for ty in DROP_ORDER:", "for ty in ():", "record-type stage skipped"),
    ("regress", "if fx.get(\"format\") == TR.FORMAT:", "if False:", "trace cases need a replay command"),
    ("gentest", 'if fx.get("format") == "trace-1":', "if False:", "trace cases get a cmake file"),
    ("gentest", "dict(expect) if expect else", "None if expect else", "expectation override ignored"),
    ("flight", 'if argv and argv[0] == "from-failure":', "if False:", "from-failure subcommand missing"),
]


def _one_mutation(i, mod, old, new, why):
    d = tempfile.mkdtemp(dir=TMP_ROOT, prefix="mut%d_" % i)
    try:
        for m in TWIN_MODS:
            shutil.copy(os.path.join(HERE, m + ".py"), d)
        p = os.path.join(d, mod + ".py")
        with open(p, encoding="utf-8") as f:
            text = f.read()
        if text.count(old) != 1:
            return "TWIN BROKEN: %s: %s matches %d times" % (mod, why, text.count(old)), False
        with open(p, "w", encoding="utf-8") as f:
            f.write(text.replace(old, new))
        env = dict(os.environ, NVFLIGHT_SRC=d, NVFLIGHT_REGRESS=REGRESS)
        only = ["--only", ONLY_NEW] if mod in ("trace_replay", "from_failure") else []   # their tests are all in these classes: no need to run the rest per mutation
        r = subprocess.run([sys.executable, "-I", os.path.join(d, "run_tests.py"), "--full", "--no-twin"] + only, capture_output=True, text=True, env=env, errors="replace")
        if r.returncode == 0:
            return "SURVIVED: %s: %s" % (mod, why), False
        return "caught  : %s: %s" % (mod, why), True
    finally:
        shutil.rmtree(d, ignore_errors=True)


ONLY_NEW = "TraceReplayTests,FromFailureTests,ShippedFixtureTests"
TWIN_MODS = ["nvfr", "analyze", "shrink", "gentest", "report", "regress", "flight", "toy_replay", "trace_replay", "from_failure", "run_tests"]


def twin():
    from concurrent.futures import ThreadPoolExecutor
    os.makedirs(TMP_ROOT, exist_ok=True)
    survived = []
    with ThreadPoolExecutor(max_workers=4) as ex:
        for msg, ok in ex.map(lambda t: _one_mutation(*t), [(i,) + m for i, m in enumerate(MUTATIONS)]):
            print(msg)
            if not ok:
                survived.append(msg)
    return survived


def main():
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    if "--only" in sys.argv:    # --only ClassA,ClassB : run just those test classes (used by the twin, handy while working on one tool)
        want = sys.argv[sys.argv.index("--only") + 1].split(",")
        suite = unittest.TestSuite(t for group in suite for t in group if type(t).__name__ in want)
    res = unittest.TextTestRunner(verbosity=1, failfast="--no-twin" in sys.argv).run(suite)
    if not res.wasSuccessful():
        print("TESTS FAILED")
        return 1
    if "--no-twin" in sys.argv:
        return 0
    if not FULL:
        print("fast tests only (use --full for shrink, CLI and the mutation twin)")
        print("ALL OK")
        return 0
    survived = twin()
    if survived:
        print("%d mutation(s) survived" % len(survived))
        return 1
    print("%d mutations, all caught" % len(MUTATIONS))
    print("ALL OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
