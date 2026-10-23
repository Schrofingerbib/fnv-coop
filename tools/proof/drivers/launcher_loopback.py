#!/usr/bin/env python3
"""prove.py piece for `launcher_loopback`: the launcher's Host and Join paths and its test listener, headless, over 127.0.0.1.

    python -I tools/proof/drivers/launcher_loopback.py [--chaos bad_wifi] [--seed 7]

No game, no window, no second PC. Two fake game folders (a host and a joiner) get the plugin through the real installer; then the same
functions the launcher window calls run one after the other (`fnv_launcher_core.start_session`, `fnv_portcheck.TestListener`, `probe`):

  host      Host pressed on a free UDP port: the port check passes, nvcoop.ini says role=host and that port, the loader is started (fake starter)
  port      the host's page says the port is free; a program already holding the port makes Host refuse and leave nvcoop.ini alone
  listener  "Start test listener" answers the fixed probe message and nothing else; "Test connection" accepts only that answer
  join      Join with "127.0.0.1:<port>": nvcoop.ini says role=client, the host and the same port, a token; nothing on a refused address
  chaos     (--chaos P) the join side tests the connection through the seeded relay (tools/chaos, docs/tools/T5-chaos.md) that forwards to the
            host's listener: it must still answer under P, and a one-way partition must read as "No answer" until the partition ends

Exit 0 when every check passes, 1 otherwise. The twins in tools/proof/drivers/tests/ switch on the launcher's own in-process faults
(fnv_mut) and require this driver to fail on each.
"""
from __future__ import annotations

import argparse
import os
import shutil
import socket
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable, List, Optional, Sequence

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
for _p in (HERE, REPO / "tools" / "launcher", REPO / "tools" / "installer", REPO / "tools" / "installer" / "tests", REPO / "tools" / "chaos"):
    sys.path.insert(0, str(_p))
import drv_common as D  # noqa: E402
import fakegame as fg  # noqa: E402
import fnv_ini  # noqa: E402
import fnv_install as inst  # noqa: E402
import fnv_launcher_core as core  # noqa: E402
import fnv_portcheck as pc  # noqa: E402

NOT_RUNNING = lambda: False  # noqa: E731


def free_udp_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    try:
        return s.getsockname()[1]
    finally:
        s.close()


class FakeStarter:
    """Stands in for subprocess.Popen: records what the launcher would have started."""

    def __init__(self):
        self.calls: List[tuple] = []

    def __call__(self, argv, **kw):
        self.calls.append((list(argv), kw.get("cwd")))
        return type("P", (), {"pid": 4242 + len(self.calls)})()


def make_installed_game(root: Path, name: str) -> Path:
    """A fake New Vegas folder with the plugin installed by the real installer (the fake release of the installer tests)."""
    g = fg.make_game(root / name)
    rel = fg.make_release(root / (name + "-release"))
    r = inst.install(g, rel, running=NOT_RUNNING, mygames=fg.make_mygames(root / (name + "-docs")))
    if not r.ok:
        raise RuntimeError("fake install failed: " + "; ".join(r.lines if hasattr(r, "lines") else [str(r)]))
    return g


def ini_value(game: Path, section: str, key: str) -> Optional[str]:
    return fnv_ini.get(fnv_ini.read_text(core.ini_path(game)), section, key)


def check(c: D.Checks, work: Path, chaos: str = "", seed: int = 7) -> None:
    host_game, join_game = make_installed_game(work, "host"), make_installed_game(work, "join")
    port = free_udp_port()
    starter = FakeStarter()

    # ---- host: Host pressed
    hs = core.Settings(mode="host", name="HostGuy", port=port, game_dir=str(host_game), max_players=3)
    r = core.start_session(hs, running=NOT_RUNNING, popen=starter)
    c.check("host: Host pressed on a free port starts the game", r.ok, "; ".join(r.messages)[:160])
    c.check("host: the loader was started in the game folder", len(starter.calls) == 1 and Path(starter.calls[0][1] or "") == host_game,
            str(starter.calls)[:160])
    c.check("host: nvcoop.ini says role=host, that port, co-op on",
            (ini_value(host_game, "coop", "role"), ini_value(host_game, "coop", "port"), ini_value(host_game, "coop", "enable")) ==
            ("host", str(port), "1"), str([ini_value(host_game, "coop", k) for k in ("role", "port", "enable")]))
    c.check("host: maxplayers is written", ini_value(host_game, "coop", "maxplayers") == "3")

    # ---- port page
    rep = pc.host_report(port)
    c.check("port: the host page says a free port is free", rep["free"] is True, str(rep["lines"][:1]))
    blocker = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    blocker.bind(("0.0.0.0", 0))
    busy = blocker.getsockname()[1]
    try:
        rep = pc.host_report(busy)
        c.check("port: a port another program holds is reported busy", rep["free"] is False, str(rep["lines"][:1]))
        before = core.ini_path(host_game).read_bytes()
        calls = len(starter.calls)
        busy_set = core.Settings(mode="host", name="HostGuy", port=busy, game_dir=str(host_game))
        r = core.start_session(busy_set, running=NOT_RUNNING, popen=starter)
        c.check("port: Host on a busy port refuses", not r.ok and any("PROBLEM" in m for m in r.messages), "; ".join(r.messages)[:160])
        c.check("port: a refused Host leaves nvcoop.ini alone and starts nothing", core.ini_path(host_game).read_bytes() == before and len(starter.calls) == calls)
    finally:
        blocker.close()

    # ---- test listener on the host's port
    lis = pc.TestListener(port, seconds=60, address="127.0.0.1")
    ok, why = lis.start()
    c.check("listener: Start test listener binds the host's port", ok, why)
    if not ok:
        return
    try:
        ok, msg = pc.probe("127.0.0.1", port, timeout=1.0, tries=3)
        c.check("listener: Test connection reaches it and reads 'answered'", ok and "answered" in msg, msg)
        c.check("listener: it counted the answer", lis.answered >= 1, str(lis.answered))
        raw = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        raw.settimeout(0.4)
        try:
            raw.sendto(b"not the probe message", ("127.0.0.1", port))
            try:
                got = raw.recvfrom(64)[0]
            except socket.timeout:
                got = b""
        finally:
            raw.close()
        c.check("listener: it answers nothing but the probe message", got == b"", repr(got))
        # a "server" that answers with something else must not read as a working host
        fake = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        fake.bind(("127.0.0.1", 0))
        fake.settimeout(0.2)
        fport = fake.getsockname()[1]
        import threading
        stop = threading.Event()

        def junk():
            while not stop.is_set():
                try:
                    _d, a = fake.recvfrom(64)
                    fake.sendto(b"some other program", a)
                except (socket.timeout, OSError):
                    continue
        th = threading.Thread(target=junk, daemon=True)
        th.start()
        try:
            ok2, msg2 = pc.probe("127.0.0.1", fport, timeout=0.5, tries=1)
        finally:
            stop.set()
            th.join(1)
            fake.close()
        c.check("listener: an answer that is not the agreed one does not count", not ok2, msg2)
        nobody = free_udp_port()
        ok3, msg3 = pc.probe("127.0.0.1", nobody, timeout=0.4, tries=1)
        c.check("listener: nothing listening reads as no answer or refused", not ok3, msg3)

        # ---- join side
        js = core.Settings(mode="join", name="JoinGal", address=f"127.0.0.1:{port}", game_dir=str(join_game), token=987654)
        r = core.start_session(js, running=NOT_RUNNING, popen=starter)
        c.check("join: Join with 127.0.0.1:<port> starts the game", r.ok, "; ".join(r.messages)[:160])
        c.check("join: nvcoop.ini says role=client, the host's address and the SAME port",
                (ini_value(join_game, "coop", "role"), ini_value(join_game, "coop", "host"), ini_value(join_game, "coop", "port")) ==
                ("client", "127.0.0.1", str(port)), str([ini_value(join_game, "coop", k) for k in ("role", "host", "port")]))
        c.check("join: the player's token is kept", ini_value(join_game, "coop", "token") == "987654")
        back = core.read_settings_from_ini(join_game)
        c.check("join: the window reads the same session back from nvcoop.ini (a loopback host is not shown as an address)",
                back.mode == "join" and int(back.port) == port, f"{back.mode} {back.address!r} {back.port}")
        before = core.ini_path(join_game).read_bytes()
        calls = len(starter.calls)
        r = core.start_session(core.Settings(mode="join", name="JoinGal", address="not an address!", game_dir=str(join_game)), running=NOT_RUNNING, popen=starter)
        c.check("join: a bad address is refused with nothing written and nothing started",
                not r.ok and core.ini_path(join_game).read_bytes() == before and len(starter.calls) == calls, "; ".join(r.messages)[:160])
        c.check("join: the loader of the joiner's game was started", len(starter.calls) >= 2 and Path(starter.calls[1][1] or "") == join_game)

        if chaos:
            _chaos_leg(c, port, chaos, seed)
    finally:
        lis.stop()


def _chaos_leg(c: D.Checks, host_port: int, profile: str, seed: int) -> None:
    from relay import Relay
    relay = Relay(("127.0.0.1", host_port), 0, "lan", None, None).start()
    try:
        relay.set_profile(profile, seed)
        ok, msg = pc.probe("127.0.0.1", relay.port, timeout=2.0, tries=4)
        st = relay.get_stats()
        c.check(f"chaos: Test connection through the relay under {profile} (seed {seed}) still answers", ok, msg)
        c.check("chaos: the probe really went through the relay (both directions seen)", st["c2s"]["seen"] >= 1 and st["s2c"]["seen"] >= 1, str({d: st[d]["seen"] for d in st}))
        relay.partition("c2s", True)
        ok, msg = pc.probe("127.0.0.1", relay.port, timeout=0.3, tries=2)
        c.check("chaos: with the way to the host cut the test reads 'No answer'", not ok and "No answer" in msg, msg)
        relay.partition("c2s", False)
        ok, msg = pc.probe("127.0.0.1", relay.port, timeout=2.0, tries=4)
        c.check("chaos: after the partition ends the test answers again", ok, msg)
    finally:
        relay.close()


def run(a, out: Callable[[str], None] = lambda s: print(s, flush=True)) -> int:
    tmp = Path(tempfile.mkdtemp(prefix="fnv-loopback-"))
    c = D.Checks(out)
    try:
        check(c, tmp, a.chaos, a.seed)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    out(c.summary())
    return c.exit_code()


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--chaos", default="", help="also test the connection through the seeded relay under this profile (bad_wifi, hell, ...)")
    ap.add_argument("--seed", type=int, default=7)
    return run(ap.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
