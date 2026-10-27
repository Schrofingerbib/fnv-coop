"""The real ENet transport and the full session protocol through the chaos relay, game-free.

python -I tools/chaos/enet_relay.py --loopback PATH/fnvcoop_loopback --profile bad_wifi [--clients 2] [--txns 150] [--timeout 90] [--set NAME=VALUE ...] [--twin NAME]

Starts `fnvcoop_loopback host` on a free loopback port, puts the UDP relay (relay.py, the profile's seeded impairment) in front of it and
points the client processes at the relay. The loopback programs run with their own fault wrapper off (--loss 0), so every lost, late,
reordered or duplicated datagram comes from the relay. Passes (exit 0) when the host and every client finish, all digests are equal,
and the relay saw traffic in both directions (and dropped some, for a profile with loss). Prints one line per check.

--twin NAME is a mutation twin: it breaks the setup on purpose, and exits 0 only when the run fails BECAUSE of that breakage
(any other failure, such as a host that never started, makes the twin exit 1):
  bypass     the clients connect to the host directly: the session must still work and the relay must forward nothing
  partition  the relay drops everything in both directions: it must drop datagrams as a partition and no client may finish
"""
import argparse
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from relay import Relay  # noqa: E402


def free_udp_port():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def read_result(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return ""


def field(text, key):
    parts = text.split()
    return parts[parts.index(key) + 1] if key in parts else None


def run(a):
    host_port = free_udp_port()
    tmp = Path(tempfile.mkdtemp(prefix="fnvcoop_relay_"))
    common = ["--seed", "7", "--txns", str(a.txns), "--loss", "0"]
    relay = Relay(("127.0.0.1", host_port), 0, a.profile).start()
    if a.set:
        relay.set_params({k: float(v) for k, v in (kv.split("=", 1) for kv in a.set)})
    if a.twin == "partition":
        relay.partition("both", True)
    procs = []
    blocker = None
    host_started = False
    if a.occupy_host_port:   # test of the twins: the host's port is taken, so the host cannot listen and exits at once
        blocker = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        blocker.bind(("127.0.0.1", host_port))
    try:
        procs.append(subprocess.Popen([a.loopback, "host", "--port", str(host_port), *common, "--clients", str(a.clients),
                                       "--out", str(tmp / "host.txt")], stdout=subprocess.DEVNULL))
        time.sleep(0.3)   # the host must be listening before the first client Hello
        host_started = procs[0].poll() is None   # a host that cannot listen has exited by now
        client_port = host_port if a.twin == "bypass" else relay.port
        for i in range(a.clients):
            procs.append(subprocess.Popen([a.loopback, "client", "--port", str(client_port), *common, "--token", str(1000 + i),
                                           "--out", str(tmp / ("c%d.txt" % i))], stdout=subprocess.DEVNULL))
        deadline = time.monotonic() + a.timeout
        timed_out = False
        for p in procs:
            try:
                p.wait(max(0.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                timed_out = True
        stats = relay.get_stats()
    finally:
        for p in procs:
            if p.poll() is None:
                p.kill()
                p.wait()
        relay.close()
        if blocker:
            blocker.close()

    problems = []
    facts = {"host_started": host_started}
    host = read_result(tmp / "host.txt")
    clients = [read_result(tmp / ("c%d.txt" % i)) for i in range(a.clients)]
    print("profile %s: host    %s" % (a.profile, host or "(no result)"))
    for i, c in enumerate(clients):
        print("profile %s: client%d %s" % (a.profile, i, c or "(no result)"))
    if timed_out:
        problems.append("timed out after %ss" % a.timeout)
    if not host.startswith("OK"):
        problems.append("host did not finish OK")
    digest = field(host, "DIGEST")
    for i, c in enumerate(clients):
        if not c.startswith("OK"):
            problems.append("client%d did not finish OK" % i)
        elif digest is None or field(c, "DIGEST") != digest:
            problems.append("client%d digest %s differs from the host's %s" % (i, field(c, "DIGEST"), digest))
    seen = {d: s["forwarded"] for d, s in stats.items()}
    dropped = sum(s["dropped"] for s in stats.values())
    print("relay: forwarded %s, dropped %d, duplicated %d, drop reasons %s" % (
        seen, dropped, sum(s["duplicated"] for s in stats.values()), {d: s["reasons"] for d, s in stats.items()}))
    if not all(n > 0 for n in seen.values()):
        problems.append("the relay forwarded nothing in at least one direction: the traffic did not go through it")
    if a.profile not in ("lan",) and dropped == 0:
        problems.append("profile %s dropped nothing: the impairment was not applied" % a.profile)
    # What a twin needs to tell its own planted failure from any other one (a host that never started fails every check too).
    facts["session_ok"] = host.startswith("OK") and all(c.startswith("OK") and field(c, "DIGEST") == digest for c in clients) and digest is not None
    facts["no_client_ok"] = not any(c.startswith("OK") for c in clients)
    facts["forwarded_nothing"] = all(n == 0 for n in seen.values())
    facts["partition_drops"] = sum(s["reasons"].get("partition", 0) for s in stats.values())
    for f in tmp.glob("*"):
        f.unlink()
    tmp.rmdir()
    return problems, facts


def twin_caught(name, problems, facts):
    """(caught, why). A twin counts as caught only by the failure it plants, not by any failure."""
    if not problems:
        return False, "no check failed"
    if name == "bypass":
        if not facts["session_ok"]:
            return False, "the direct session itself failed, so the relay checks were never what caught it"
        if not facts["forwarded_nothing"]:
            return False, "the relay forwarded traffic although the clients bypassed it"
        return True, "the session worked directly and the relay forwarded nothing"
    if name == "partition":
        if not facts["host_started"]:   # the clients' connect datagrams alone would also count as partition drops
            return False, "the host never started listening, so the partition was not what stopped the session"
        if facts["partition_drops"] == 0:
            return False, "the relay dropped nothing as a partition"
        if not facts["no_client_ok"]:
            return False, "a client finished OK through a partitioned relay"
        return True, "the relay dropped %d datagrams as a partition and no client finished" % facts["partition_drops"]
    raise ValueError(name)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--loopback", required=True, help="path to the built fnvcoop_loopback executable")
    ap.add_argument("--profile", default="bad_wifi")
    ap.add_argument("--clients", type=int, default=2)
    ap.add_argument("--txns", type=int, default=150)
    ap.add_argument("--timeout", type=float, default=90)
    ap.add_argument("--set", action="append", default=[], metavar="NAME=VALUE",
                    help="override one impairment parameter of the profile in both directions (loss, reorder, duplicate, bandwidth_bps, latency_ms, ...)")
    ap.add_argument("--twin", choices=["bypass", "partition"])
    ap.add_argument("--occupy-host-port", action="store_true",
                    help="test of the twins only: bind the host's port first, so the real host fails to listen while the clients run")
    a = ap.parse_args(argv)
    if not os.path.exists(a.loopback):
        print("no such executable: %s" % a.loopback)
        return 2
    problems, facts = run(a)
    for p in problems:
        print("FAIL: " + p)
    if a.twin:
        caught, why = twin_caught(a.twin, problems, facts)
        print("TWIN %s: '%s' %s (%s)" % ("OK" if caught else "FAIL", a.twin, "was caught" if caught else "was not caught by its own failure", why))
        return 0 if caught else 1
    print("FAIL" if problems else "PASS (%s through the relay)" % a.profile)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
