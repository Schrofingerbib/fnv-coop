"""Two-copy pairs on one PC: which instances form a pair, which co-op port a pair uses, whether the provisioned
nvcoop.ini files keep the pairs apart, and whether the PC has the RAM for them. No game, no process, no pipe here.

    pair k = instances 2k (host) and 2k+1 (client), co-op port PORT_BASE + k on 127.0.0.1.

Why: every copy joins the host named in its own nvcoop.ini, and the default is port 27960 on 127.0.0.1. Three pairs
provisioned the old way would all use that one port: the second host fails to bind (or two clients join one host)
and the pairs talk to each other. `instances.py provision --pairs N` writes the per-pair port, and
tools/bots/run_parallel.py calls validate_pairs() on the files before it starts anything.
"""
from __future__ import annotations

import ctypes
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple

PORT_BASE = 27960          # the plugin's default [coop] port: pair 0 keeps working with the old single-pair setup
LOOPBACK = ("127.0.0.1", "localhost", "::1")
COPY_GB = 0.7              # resident memory of one game copy (measured by the lead)
RESERVE_GB = 1.2           # left for Windows, the lead's tools and the test processes (16 GB PC; 2.0 stalled the sweep while agents built)
GIB = 2 ** 30
TRUE_WORDS = ("1", "true", "yes", "on")


# ---------------------------------------------------------------- layout
def pair_of(i: int) -> int:
    return int(i) // 2


def role_of(i: int) -> str:
    return "host" if int(i) % 2 == 0 else "client"


def pair_ids(k: int) -> Tuple[int, int]:
    """(host instance, client instance) of pair k."""
    return 2 * int(k), 2 * int(k) + 1


def port_of(k: int, base: int = PORT_BASE) -> int:
    return int(base) + int(k)


def check_port_base(base: int, pairs: int) -> None:
    """Raise ValueError if the ports base .. base+pairs-1 are not all usable (1024..65535)."""
    if pairs < 1:
        raise ValueError(f"--pairs must be 1 or more, got {pairs}")
    if base < 1024 or base + pairs - 1 > 65535:
        raise ValueError(f"--port-base {base}: ports {base}..{base + pairs - 1} must lie within 1024..65535")


def pair_ini(i: int, base: int = PORT_BASE) -> Dict[str, Dict[str, str]]:
    """The nvcoop.ini entries that tie instance i to its pair: the host listens on the pair port and the client joins
    that port on this PC."""
    k = pair_of(i)
    return {"coop": {"port": str(port_of(k, base)), "host": "127.0.0.1", "listen": "127.0.0.1"}}


# Keys of nvcoop.ini that --pairs sets itself. Lower case.
PAIR_INI_KEYS = (("coop", "port"), ("coop", "host"), ("coop", "hostaddress"), ("coop", "listen"))


def check_pair_ini(extra_ini: Optional[Dict[str, Dict[str, str]]]) -> None:
    """Raise ValueError if extra_ini sets a key that --pairs sets per pair: one --ini value applies to every pair."""
    for s, kv in (extra_ini or {}).items():
        for k in kv:
            if (str(s).lower(), str(k).lower()) in PAIR_INI_KEYS:
                raise ValueError(f"--ini {s}.{k}: with --pairs this is a per-pair key (port, host address), and one --ini "
                                 "value applies to every pair, so the pairs would join each other. Remove it")


def resolve_n(n: Optional[int], pairs: int) -> int:
    """The instance count of provision / show / destroy: --pairs N means 2N; --n alone keeps its meaning (default 2)."""
    if pairs:
        if n is not None and n != 2 * pairs:
            raise ValueError(f"--n {n} does not match --pairs {pairs} (that is {2 * pairs} instances); give only one")
        return 2 * pairs
    return 2 if n is None else n


# ---------------------------------------------------------------- reading the provisioned files
def read_instance_config(i: int, root: Optional[Path] = None) -> Dict[str, Optional[str]]:
    """The values of instance i's nvcoop.ini that decide who talks to whom. A missing key is None. A missing file gives
    {"present": None}."""
    import instances
    p = instances.instance_dir(i, root) / "Data" / "NVSE" / "Plugins" / "nvcoop.ini"
    if not p.is_file():
        return {"present": None}
    text = p.read_bytes().decode("latin-1")

    def g(section: str, key: str) -> Optional[str]:
        return instances.ini_get(text, section, key)

    host = g("coop", "host")
    if host is None:
        host = g("coop", "hostaddress")
    return {"present": "1", "enable": g("coop", "enable"), "role": g("coop", "role"), "port": g("coop", "port"),
            "host": host, "listen": g("coop", "listen"), "instanceid": g("coop", "instanceId"),
            "bridge": g("bridge", "instance"), "pipe": g("coop", "pipe")}


def effective_port(cfg: Dict[str, Optional[str]]) -> Optional[int]:
    """The port the plugin uses: the [coop] port, else its default. None if the value is not a number in 1..65535."""
    v = cfg.get("port")
    if v is None or v == "":
        return PORT_BASE
    try:
        n = int(v)
    except ValueError:
        return None
    return n if 0 < n <= 65535 else None


def validate_pairs(pair_list: Iterable[int], cfgs: Dict[int, Dict[str, Optional[str]]]) -> List[str]:
    """Problems that would make pairs talk to each other, or a pair not talk at all. Empty list: the files are fine.

    pair_list: the pair numbers to run. cfgs: instance id -> read_instance_config()."""
    problems: List[str] = []
    host_port: Dict[int, int] = {}
    pipes: Dict[str, int] = {}
    for k in pair_list:
        h, c = pair_ids(k)
        for i in (h, c):
            cfg = cfgs.get(i) or {"present": None}
            where = f"pair {k} instance {i}"
            if not cfg.get("present"):
                problems.append(f"{where}: no nvcoop.ini (run: run.py provision --pairs N)")
                continue
            if (cfg.get("enable") or "").lower() not in TRUE_WORDS:
                problems.append(f"{where}: [coop] enable is {cfg.get('enable')!r}, co-op is off (provision with --ini coop.enable=1)")
            want = role_of(i)
            role = (cfg.get("role") or "").lower()
            if role != want and not (want == "client" and role == "guest"):
                problems.append(f"{where}: [coop] role is {cfg.get('role')!r}, this instance must be the {want}")
            for what, v in (("[coop] instanceId", cfg.get("instanceid")), ("[bridge] instance", cfg.get("bridge"))):
                if v is not None and v != str(i):
                    problems.append(f"{where}: {what} is {v}, expected {i}")
            pipe = cfg.get("pipe")
            if pipe:
                if pipe in pipes and pipes[pipe] != i:
                    problems.append(f"{where}: pipe {pipe} is also used by instance {pipes[pipe]}")
                pipes[pipe] = i
            port = effective_port(cfg)
            if port is None:
                problems.append(f"{where}: [coop] port {cfg.get('port')!r} is not a port number")
        hc, cc = cfgs.get(h) or {}, cfgs.get(c) or {}
        hp, cp = effective_port(hc) if hc.get("present") else None, effective_port(cc) if cc.get("present") else None
        if hp is not None and cp is not None and hp != cp:
            problems.append(f"pair {k}: the host listens on port {hp} but the client joins port {cp}")
        if hp is not None:
            host_port[k] = hp
        if cc.get("present"):
            addr = (cc.get("host") or "127.0.0.1").lower()
            if addr not in LOOPBACK:
                problems.append(f"pair {k} instance {c}: [coop] host is {addr!r}, not this PC; the client would join another machine")
    by_port: Dict[int, List[int]] = {}
    for k, p in host_port.items():
        by_port.setdefault(p, []).append(k)
    for p, ks in sorted(by_port.items()):
        if len(ks) > 1:
            problems.append("pairs " + " and ".join(str(k) for k in sorted(ks)) + f" share co-op port {p}: they would join each "
                            "other (provision with --pairs, which gives pair k port base+k)")
    return problems


# ---------------------------------------------------------------- RAM
def free_ram_bytes() -> int:
    """Available physical memory (GlobalMemoryStatusEx). 0 where it cannot be read, which makes the guard refuse."""
    if not hasattr(ctypes, "windll"):
        return 0

    class MemStatus(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong), ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong), ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong), ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]
    st = MemStatus()
    st.dwLength = ctypes.sizeof(st)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
        return 0
    return int(st.ullAvailPhys)


def max_pairs_by_ram(free_bytes: int, up_copies: int = 0, copy_gb: float = COPY_GB, reserve_gb: float = RESERVE_GB) -> int:
    """How many pairs fit: the copies already running count (their memory is already taken out of free_bytes), and each
    further copy needs copy_gb out of what is free beyond reserve_gb."""
    spare = free_bytes / GIB - reserve_gb
    more = int(spare // copy_gb) if spare > 0 else 0
    return (max(0, up_copies) + more) // 2


def ram_problem(pairs: int, up_copies: int, free_bytes: int, copy_gb: float = COPY_GB,
                reserve_gb: float = RESERVE_GB) -> Optional[str]:
    """None if the PC has the RAM for `pairs` pairs, else the reason (to print before refusing)."""
    fit = max_pairs_by_ram(free_bytes, up_copies, copy_gb, reserve_gb)
    if pairs <= fit:
        return None
    new = max(0, 2 * pairs - up_copies)
    return (f"refusing {pairs} pair(s): {new} more game copies need {new * copy_gb:.1f} GB plus {reserve_gb:.1f} GB reserve, "
            f"but only {free_bytes / GIB:.1f} GB is free ({up_copies} copies already running); at most {fit} pair(s) fit. "
            "Close other programs, run fewer pairs, or override with --no-ram-check")


def pipe_up(i: int) -> bool:
    """True if instance i's bridge pipe exists (the copy is running). Busy still counts as existing."""
    if not hasattr(ctypes, "windll"):
        return False
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    ctypes.set_last_error(0)
    if k32.WaitNamedPipeW(r"\\.\pipe\nvcoop-%d" % int(i), 1):
        return True
    return ctypes.get_last_error() != 2  # 2 == ERROR_FILE_NOT_FOUND


def count_up(ids: Iterable[int], is_up: Callable[[int], bool] = pipe_up) -> int:
    return sum(1 for i in ids if is_up(i))
