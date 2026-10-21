"""Port checks for the host and a connection test for the guests. Standard library only, no network calls to outside services.

The co-op session talks UDP (ENet) on one port, default 27960. A host needs that UDP port free on this PC, and, for friends outside
the home network, forwarded on the router. This module can:
  * tell whether the port is free on this PC, and list this PC's local network addresses;
  * run a tiny "test listener" on the port for a while, so a friend can press Test connection from their launcher and see whether
    the packets get through (do this BEFORE the game is started, the game needs the port itself);
  * send that test to a host and wait for the answer.
The test message is a plain UDP datagram; the listener answers only that exact message and keeps no data.
"""
import os
import socket
import sys
import threading
import time
from typing import Dict, List, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
for _d in (_HERE, os.path.join(os.path.dirname(_HERE), "installer")):   # installer\ sits next to launcher\ in the release and in the repo
    if _d not in sys.path:
        sys.path.insert(0, _d)
import fnv_mut  # noqa: E402

DEFAULT_PORT = 27960
PROBE = b"FNVCOOP-PROBE-1?"
REPLY = b"FNVCOOP-PROBE-1!"


def local_ips() -> List[str]:
    """IPv4 addresses of this PC on its networks. No packet is sent: connecting a UDP socket only picks a route."""
    ips: List[str] = []
    for target in ("192.0.2.1", "10.255.255.255"):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect((target, 9))
                ip = s.getsockname()[0]
                if ip and not ip.startswith("127.") and ip not in ips:
                    ips.append(ip)
        except OSError:
            continue
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
    except OSError:
        pass
    return ips


def bind_check(port: int, address: str = "0.0.0.0") -> Tuple[bool, str]:
    """Can this PC listen on UDP `port`? (False, reason) when another program already has it."""
    if not isinstance(port, int) or not 1 <= port <= 65535:
        return False, "The port must be a number from 1 to 65535."
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.bind((address, port))
        return True, ""
    except OSError as e:
        return False, (f"UDP port {port} is already in use on this PC ({e.strerror or e}). If the game is already hosting, that is "
                       "expected. Otherwise close the other program or choose another port.")
    finally:
        s.close()


def host_report(port: int) -> Dict[str, object]:
    """Everything the Host page shows: is the port free, what to tell friends."""
    ok, why = bind_check(port) if not fnv_mut.on("port_always_ok") else (True, "")
    ips = local_ips()
    lines = []
    if ok:
        lines.append(f"UDP port {port} is free on this PC.")
    else:
        lines.append(why)
    if ips:
        lines.append("Friends on your home network join with: " + ", ".join(f"{ip}:{port}" for ip in ips))
    lines.append(f"Friends over the internet need your public address and a router rule that forwards UDP port {port} to this PC "
                 "(your router's 'port forwarding' page). Searching the web for 'what is my IP' shows your public address.")
    if port < 1024:
        lines.append("Ports below 1024 are reserved for system services on many PCs; a port above 10000 is a safer choice.")
    return {"port": port, "free": ok, "local_ips": ips, "lines": lines}


class TestListener:
    """Answers the probe message on a UDP port until stop() or the time runs out."""

    def __init__(self, port: int, seconds: float = 60.0, address: str = "0.0.0.0"):
        self.port = port
        self.deadline = time.monotonic() + seconds
        self.address = address
        self.sock: Optional[socket.socket] = None
        self.thread: Optional[threading.Thread] = None
        self.answered = 0
        self._stop = threading.Event()

    def start(self) -> Tuple[bool, str]:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.bind((self.address, self.port))
        except OSError as e:
            s.close()
            return False, f"Cannot listen on UDP port {self.port}: {e.strerror or e}"
        s.settimeout(0.2)
        self.sock = s
        self.port = s.getsockname()[1]    # port 0 = any free port (tests)
        self.thread = threading.Thread(target=self._run, name="fnvcoop-portlistener", daemon=True)
        self.thread.start()
        return True, ""

    def _run(self) -> None:
        assert self.sock is not None
        while not self._stop.is_set() and time.monotonic() < self.deadline:
            try:
                data, addr = self.sock.recvfrom(64)
            except socket.timeout:
                continue
            except OSError:
                break
            if data == PROBE or fnv_mut.on("answer_anything"):
                try:
                    self.sock.sendto(REPLY, addr)
                    self.answered += 1
                except OSError:
                    pass

    def stop(self) -> None:
        self._stop.set()
        if self.thread:
            self.thread.join(timeout=2)
        if self.sock:
            self.sock.close()
            self.sock = None


def probe(host: str, port: int, timeout: float = 2.0, tries: int = 3) -> Tuple[bool, str]:
    """Send the test message to host:port and wait for the answer."""
    try:
        addr = (socket.gethostbyname(host), port)
    except OSError as e:
        return False, f"Cannot find '{host}': {e.strerror or e}. Check the address."
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    try:
        for _ in range(max(1, tries)):
            try:
                s.sendto(PROBE, addr)
                data, _from = s.recvfrom(64)
            except socket.timeout:
                continue
            except OSError as e:   # ICMP port unreachable shows up as a reset on Windows: nobody is listening there
                return False, f"{host}:{port} refused the test ({e.strerror or e}). Is the host's launcher listening?"
            if data == REPLY or fnv_mut.on("accept_any_reply"):
                return True, f"{host}:{port} answered. The path to the host works."
        return False, (f"No answer from {host}:{port}. Check: the host pressed 'Start test listener' (or is already hosting in game with the same port), "
                       "the router forwards UDP to the host, and a firewall allows the game and the launcher.")
    finally:
        s.close()
