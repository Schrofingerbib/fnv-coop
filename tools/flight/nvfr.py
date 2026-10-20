"""Reader, writer and pretty-printer for the .nvfr flight trace format written by synccore/flight (spec: docs/tools/flight-format.md).

A trace is {"header": {...}, "records": [ {...}, ... ], "truncated": bool, "warnings": [str], "name": str}.
Every record is a dict with at least: idx, type, name, flags, mono_us (the recorder's tUs), frame.
The reader never raises on a damaged tail: a half-written last record sets truncated=True.
It raises NvfrError only when the file is not a v1 trace at all (bad magic, unsupported version, short header).
dump(path, header, records) writes the same layout (used by tests and by tools such as animcheck that add Marker records).
"""
import hashlib
import struct

MAGIC = b"NVFR"
VERSION = 1
HEADER_SIZE = 64
RECORD_HEADER = 16
NET_PREFIX = 36

T_META, T_NET_OUT, T_NET_IN, T_TXN_CAPTURED, T_TXN_APPLIED, T_OWNERSHIP, T_DIGEST, T_MARKER = 1, 2, 3, 4, 5, 6, 7, 8
TYPE_NAMES = {1: "Meta", 2: "NetOut", 3: "NetIn", 4: "TxnCaptured", 5: "TxnApplied", 6: "Ownership", 7: "Digest", 8: "Marker"}
REASONS = {0: "manual", 1: "desync", 2: "crash", 3: "shutdown", 4: "test"}
ROLES = {0: "host", 1: "client", 2: "sim"}
OWN_OPS = {0: "grant", 1: "transfer", 2: "release", 3: "rejectedStale", 4: "rejectedEcho", 5: "rejectedNonOwner"}
BUCKETS = 256   # bucket hashes in a bucketed digest (bucket = key & 0xFF)
CATEGORIES = {0: "story", 1: "actors", 2: "containers", 3: "objects", 4: "other"}
MARKER_KINDS = {0: "note", 1: "desync", 2: "join", 3: "leave", 4: "test-step", 5: "save", 6: "load", 7: "rebase"}
NONE_SLOT = 255
FLAG_TRUNCATED, FLAG_DERIVED, FLAG_DETAIL = 1, 2, 4                      # record flags
AP_SNAPSHOT, AP_REPAIR, AP_BASELINE, AP_CATCHUP = 1, 2, 4, 8   # TxnApplied appliedFlags
APPLY_SPECIAL = AP_SNAPSHOT | AP_REPAIR | AP_BASELINE        # applies that restate state rather than replay a txn


class NvfrError(Exception):
    pass


class _Short(Exception):
    pass


class Cursor:
    def __init__(self, data, pos=0, end=None):
        self.d = data
        self.p = pos
        self.end = len(data) if end is None else end

    def left(self):
        return self.end - self.p

    def take(self, n):
        if n < 0 or self.p + n > self.end:
            raise _Short()
        b = self.d[self.p:self.p + n]
        self.p += n
        return b

    def u8(self):
        return self.take(1)[0]

    def u16(self):
        return struct.unpack("<H", self.take(2))[0]

    def u32(self):
        return struct.unpack("<I", self.take(4))[0]

    def u64(self):
        return struct.unpack("<Q", self.take(8))[0]

    def str16(self):
        return self.take(self.u16()).decode("utf-8", "replace")


class Buf:
    def __init__(self):
        self.b = bytearray()

    def u8(self, v):
        self.b.append(v & 0xFF)
        return self

    def u16(self, v):
        self.b += struct.pack("<H", v & 0xFFFF)
        return self

    def u32(self, v):
        self.b += struct.pack("<I", v & 0xFFFFFFFF)
        return self

    def u64(self, v):
        self.b += struct.pack("<Q", v & 0xFFFFFFFFFFFFFFFF)
        return self

    def str16(self, v):
        raw = v.encode("utf-8")[:0xFFFF]
        return self.u16(len(raw)).raw(raw)

    def raw(self, v):
        self.b += v
        return self


# ---------------------------------------------------------------- record bodies

def _dec_meta(c, r):
    r["meta"] = {}
    while c.left() >= 4:
        k = c.str16()
        r["meta"][k] = c.str16()


def _enc_meta(b, r):
    for k, v in r.get("meta", {}).items():
        b.str16(k).str16(v)


def _dec_net(c, r):
    r["peer"] = c.u32()
    r["channel"] = c.u8()
    r["mux"] = c.u8()
    r["msg_type"] = c.u8()
    r["decode"] = c.u8()          # 0 decoded, 1 decode failed, 2 not a story datagram, 255 not yet decoded
    r["session"] = c.u32()
    r["epoch"] = c.u32()
    r["seq"] = c.u64()
    r["seq_hi"] = c.u64()
    r["raw_len"] = c.u32()
    r["raw"] = c.take(c.left())
    # every story datagram ends in its own crc32, which makes a plain crc32 of the whole datagram a constant
    r["crc"] = int.from_bytes(hashlib.blake2b(r["raw"], digest_size=4).digest(), "little")
    r["truncated"] = bool(r["flags"] & FLAG_TRUNCATED)


def _enc_net(b, r):
    raw = r.get("raw", b"")
    b.u32(r["peer"]).u8(r.get("channel", 0)).u8(r.get("mux", raw[0] if raw else 0)).u8(r.get("msg_type", 0)).u8(r.get("decode", 0))
    b.u32(r.get("session", 0)).u32(r.get("epoch", 0)).u64(r.get("seq", 0)).u64(r.get("seq_hi", r.get("seq", 0)))
    b.u32(r.get("raw_len", len(raw))).raw(raw)


def _dec_captured(c, r):
    r["kind"] = c.u8()            # 0 host commit input, 1 client intent
    r["intent_id"] = c.u32()
    r["seq"] = c.u64()
    r["effect_count"] = c.u32()
    r["hash"] = c.u64()
    r["cause"] = c.u8()
    r["epoch"] = c.u32()


def _enc_captured(b, r):
    b.u8(r.get("kind", 0)).u32(r.get("intent_id", 0)).u64(r.get("seq", 0)).u32(r.get("effect_count", 1)).u64(r.get("hash", 0))
    b.u8(r.get("cause", 0)).u32(r.get("epoch", 0))


def _dec_applied(c, r):
    r["seq"] = c.u64()
    r["epoch"] = c.u32()
    r["origin"] = c.u8()
    r["index"] = c.u32()
    r["applied_flags"] = c.u8()
    r["cause"] = c.u8()
    r["effect_tag"] = c.u8()
    r["hash"] = c.u64()


def _enc_applied(b, r):
    b.u64(r.get("seq", 0)).u32(r.get("epoch", 0)).u8(r.get("origin", 0)).u32(r.get("index", 0)).u8(r.get("applied_flags", 0))
    b.u8(r.get("cause", 0)).u8(r.get("effect_tag", 0)).u64(r.get("hash", 0))


def _dec_own(c, r):
    r["op"] = c.u8()
    r["actor"] = c.u64()
    r["ref"] = "actor:%X" % r["actor"]
    r["epoch"] = c.u32()
    r["from_slot"] = c.u8()
    r["to_slot"] = c.u8()
    r["reason"] = c.u8()
    r["txn_id"] = c.u64()


def _enc_own(b, r):
    b.u8(r["op"]).u64(r["actor"]).u32(r.get("epoch", 0)).u8(r.get("from_slot", NONE_SLOT)).u8(r.get("to_slot", NONE_SLOT))
    b.u8(r.get("reason", 0)).u64(r.get("txn_id", 0))


def _dec_digest(c, r):
    r["category"] = c.u8()
    r["epoch"] = c.u32()
    r["seq"] = c.u64()
    r["hash"] = c.u64()
    r["item_count"] = c.u32()
    n = c.u16()
    if n * 4 > c.left():
        raise _Short()
    r["shards"] = [c.u32() for _ in range(n)]
    n = c.u32()
    if n * 12 > c.left():
        raise _Short()
    r["keys"] = {}
    for _ in range(n):
        k = c.u64()
        r["keys"][k] = c.u32()
    r["derived"] = bool(r["flags"] & FLAG_DERIVED)
    r["detail"] = bool(r["flags"] & FLAG_DETAIL)   # carries only per-key entries (several records may belong to one digest)


def _enc_digest(b, r):
    b.u8(r["category"]).u32(r.get("epoch", 0)).u64(r.get("seq", 0)).u64(r.get("hash", 0)).u32(r.get("item_count", 0))
    shards = r.get("shards", [])
    b.u16(len(shards))
    for s in shards:
        b.u32(s)
    keys = r.get("keys", {})
    b.u32(len(keys))
    for k, h in keys.items():
        b.u64(k).u32(h)


def _dec_marker(c, r):
    r["kind"] = c.u16()
    r["text"] = c.str16()


def _enc_marker(b, r):
    b.u16(r.get("kind", 0)).str16(r.get("text", ""))


_DEC = {1: _dec_meta, 2: _dec_net, 3: _dec_net, 4: _dec_captured, 5: _dec_applied, 6: _dec_own, 7: _dec_digest, 8: _dec_marker}
_ENC = {1: _enc_meta, 2: _enc_net, 3: _enc_net, 4: _enc_captured, 5: _enc_applied, 6: _enc_own, 7: _enc_digest, 8: _enc_marker}


# ---------------------------------------------------------------- file level

def parse_header(data):
    if len(data) < 4 or data[:4] != MAGIC:
        raise NvfrError("not an .nvfr file (bad magic)")
    if len(data) < HEADER_SIZE:
        raise NvfrError("header is cut short")
    c = Cursor(data, 4)
    h = {}
    h["version"] = c.u16()
    h["header_size"] = c.u16()
    if h["version"] != VERSION:
        raise NvfrError("unsupported .nvfr version %d (this reader handles %d)" % (h["version"], VERSION))
    if h["header_size"] < HEADER_SIZE or h["header_size"] > len(data):
        raise NvfrError("bad header size %d" % h["header_size"])
    h["slot"] = c.u8()
    h["role"] = c.u8()
    h["reason"] = c.u8()
    c.u8()
    h["wire_version"] = c.u32()
    h["wall_unix_ms"] = c.u64()
    h["dump_t_us"] = c.u64()
    h["records_dropped"] = c.u64()
    h["record_count"] = c.u64()
    h["session"] = c.u32()
    h["epoch"] = c.u32()
    h["role_name"] = ROLES.get(h["role"], "role%d" % h["role"])
    h["reason_name"] = REASONS.get(h["reason"], "reason%d" % h["reason"])
    h["instance"] = ""
    h["build_number"] = h["wire_version"]
    return h


def loads(data, name="<memory>"):
    data = bytes(data)
    header = parse_header(data)
    records = []
    warnings = []
    truncated = False
    pos = header["header_size"]
    while len(data) - pos >= RECORD_HEADER:
        rtype, flags, plen, frame, t_us = struct.unpack_from("<BBHIQ", data, pos)
        if len(data) - pos - RECORD_HEADER < plen:
            break
        body = Cursor(data, pos + RECORD_HEADER, pos + RECORD_HEADER + plen)
        rec = {"idx": len(records), "type": rtype, "name": TYPE_NAMES.get(rtype, "Unknown%d" % rtype), "flags": flags,
               "mono_us": t_us, "frame": frame, "offset": pos}
        pos += RECORD_HEADER + plen
        dec = _DEC.get(rtype)
        if dec is None:
            rec["payload"] = body.take(body.left())
        else:
            try:
                dec(body, rec)
            except _Short:
                warnings.append("record %d (%s) at offset %d has a malformed body; kept without fields" % (rec["idx"], rec["name"], rec["offset"]))
                rec["malformed"] = True
        records.append(rec)
    if len(data) - pos > 0:
        truncated = True
        warnings.append("file ends in the middle of a record (crash dump?); records before it are intact")
    for r in records:
        if r["type"] == T_META and "meta" in r:
            header["instance"] = r["meta"].get("label", header["instance"])
    return {"header": header, "records": records, "truncated": truncated, "warnings": warnings, "name": name}


def load(path):
    with open(path, "rb") as f:
        return loads(f.read(), name=str(path))


def dumps(header, records):
    """Encode a trace. header keys: slot, role, reason, wire_version, wall_unix_ms, dump_t_us, records_dropped, session, epoch.
    Each record: type (int), mono_us, frame, flags (optional) plus the fields of its type. A Meta record carries
    {"meta": {"label": "..."}}; if none is given the file has none (the header has no name field)."""
    body = Buf()
    for r in records:
        p = Buf()
        enc = _ENC.get(r["type"])
        if enc is None:
            p.raw(r.get("payload", b""))
        else:
            enc(p, r)
        flags = r.get("flags", 0)
        if r["type"] in (T_NET_OUT, T_NET_IN) and r.get("truncated"):
            flags |= FLAG_TRUNCATED
        if r["type"] == T_DIGEST and r.get("derived"):
            flags |= FLAG_DERIVED
        if r["type"] == T_DIGEST and r.get("detail"):
            flags |= FLAG_DETAIL
        body.u8(r["type"]).u8(flags).u16(len(p.b)).u32(r.get("frame", 0)).u64(r.get("mono_us", 0)).raw(p.b)
    b = Buf()
    b.raw(MAGIC).u16(VERSION).u16(HEADER_SIZE)
    b.u8(header.get("slot", 0)).u8(header.get("role", 0)).u8(header.get("reason", 0)).u8(0)
    b.u32(header.get("wire_version", 5)).u64(header.get("wall_unix_ms", 0)).u64(header.get("dump_t_us", 0))
    b.u64(header.get("records_dropped", 0)).u64(len(records)).u32(header.get("session", 0)).u32(header.get("epoch", 0)).u64(0)
    return bytes(b.b) + bytes(body.b)


def dump(path, header, records):
    with open(path, "wb") as f:
        f.write(dumps(header, records))


# ---------------------------------------------------------------- pretty printing

def _hex(b, n=24):
    s = bytes(b[:n]).hex()
    return s + ("..." if len(b) > n else "")


def describe(r):
    t = r["type"]
    if r.get("malformed"):
        return "malformed body"
    if t in (T_NET_IN, T_NET_OUT):
        arrow = "from" if t == T_NET_IN else "to"
        return "%s peer %d msg=%d epoch=%d seq=%d..%d len=%d crc=%08x%s raw=%s" % (
            arrow, r["peer"], r["msg_type"], r["epoch"], r["seq"], r["seq_hi"], r["raw_len"], r["crc"],
            " (cut)" if r["truncated"] else "", _hex(r["raw"]))
    if t == T_TXN_CAPTURED:
        return "%s seq=%d intent=%d effects=%d hash=%016x epoch=%d" % (
            "hostCommit" if r["kind"] == 0 else "clientIntent", r["seq"], r["intent_id"], r["effect_count"], r["hash"], r["epoch"])
    if t == T_TXN_APPLIED:
        fl = [n for bit, n in ((AP_SNAPSHOT, "snapshot"), (AP_REPAIR, "repair"), (AP_BASELINE, "baseline"), (AP_CATCHUP, "catchUp")) if r["applied_flags"] & bit]
        return "seq=%d[%d] origin=%d epoch=%d tag=%d hash=%016x %s" % (
            r["seq"], r["index"], r["origin"], r["epoch"], r["effect_tag"], r["hash"], ",".join(fl))
    if t == T_OWNERSHIP:
        sl = lambda s: "-" if s == NONE_SLOT else str(s)
        return "%s %s epoch=%d %s->%s reason=%d txn=%d" % (
            OWN_OPS.get(r["op"], "op%d" % r["op"]), r["ref"], r["epoch"], sl(r["from_slot"]), sl(r["to_slot"]), r["reason"], r["txn_id"])
    if t == T_DIGEST:
        return "%s epoch=%d seq=%d hash=%016x items=%d shards=%d keys=%d%s" % (
            CATEGORIES.get(r["category"], "cat%d" % r["category"]), r["epoch"], r["seq"], r["hash"], r["item_count"], len(r["shards"]),
            len(r["keys"]), " (derived from SyncDigest)" if r["derived"] else (" (key detail)" if r["detail"] else ""))
    if t == T_MARKER:
        return "[%s] %s" % (MARKER_KINDS.get(r["kind"], "kind%d" % r["kind"]), r["text"])
    if t == T_META:
        return " ".join("%s=%s" % kv for kv in r.get("meta", {}).items())
    return "payload %s" % _hex(r.get("payload", b""))


def format_header(h):
    return "slot=%d role=%s reason=%s instance=%r wire=%d session=%d dropped=%d wall_ms=%d" % (
        h["slot"], h["role_name"], h["reason_name"], h["instance"], h["wire_version"], h["session"], h["records_dropped"], h["wall_unix_ms"])


def format_record(r):
    return "%6d %10.3fms f%-6d %-12s %s" % (r["idx"], r["mono_us"] / 1000.0, r["frame"], r["name"], describe(r))


def pretty(trace, types=None, limit=None):
    lines = ["# " + trace["name"], "# " + format_header(trace["header"])]
    n = 0
    for r in trace["records"]:
        if types and r["name"].lower() not in types:
            continue
        lines.append(format_record(r))
        n += 1
        if limit and n >= limit:
            lines.append("... (limit %d)" % limit)
            break
    for w in trace["warnings"]:
        lines.append("# warning: " + w)
    return "\n".join(lines)
