"""pipe_relay and NvBridgeTcp against a fake pipe (no game)."""
import json
import os
import socket
import sys
import threading
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import nvbridge  # noqa: E402
import pipe_relay  # noqa: E402


class FakePipe:
    """Answers each request line like the game: ping -> pong, echo -> its params, hang -> never."""

    def __init__(self, instance=0):
        self.out = b""

    def write(self, b):
        req = json.loads(b.decode())
        m = req["method"]
        if m == "hang":
            return
        res = {"pong": True, "pid": 4242} if m == "ping" else {"echo": req.get("params", {})}
        self.out += (json.dumps({"id": req["id"], "result": res}) + "\n").encode()

    def available(self):
        return len(self.out)

    def read(self, n):
        d, self.out = self.out[:n], self.out[n:]
        return d

    def close(self):
        pass


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Relay(unittest.TestCase):
    def start(self):
        port, stop = free_port(), threading.Event()
        t = threading.Thread(target=pipe_relay.serve, args=(0, port, FakePipe, "127.0.0.1", stop), daemon=True)
        t.start()
        time.sleep(0.3)
        self.addCleanup(stop.set)
        return port

    def test_a_call_goes_through_and_the_reply_comes_back(self):
        port = self.start()
        nv = nvbridge.NvBridgeTcp.connect_tcp(f"127.0.0.1:{port}", wait_s=3)
        self.addCleanup(nv.close)
        self.assertEqual(nv.call("echo", {"a": 1}), {"echo": {"a": 1}})
        self.assertEqual(nv.call("echo", {"b": [1, 2]}), {"echo": {"b": [1, 2]}})

    def test_the_remote_game_pid_is_not_tracked_so_a_drop_is_not_a_local_crash(self):
        port = self.start()
        nv = nvbridge.NvBridgeTcp.connect_tcp(f"127.0.0.1:{port}", wait_s=3)
        self.assertEqual(nv.game_pid, 0)
        nv.close()
        with self.assertRaises(nvbridge.ConnectionLost) as cm:
            nv.call("echo", {})
        self.assertNotIsInstance(cm.exception, nvbridge.GameCrashed)

    def test_two_connections_at_once_each_get_their_own_replies(self):
        port = self.start()
        a = nvbridge.NvBridgeTcp.connect_tcp(f"127.0.0.1:{port}", wait_s=3)
        b = nvbridge.NvBridgeTcp.connect_tcp(f"127.0.0.1:{port}", wait_s=3)
        self.addCleanup(a.close)
        self.addCleanup(b.close)
        self.assertEqual(a.call("echo", {"who": "a"})["echo"]["who"], "a")
        self.assertEqual(b.call("echo", {"who": "b"})["echo"]["who"], "b")

    def test_a_game_that_never_answers_gives_an_error_reply_not_a_hang(self):
        a, b = socket.socketpair()
        t = threading.Thread(target=pipe_relay.relay_lines, args=(a, FakePipe(), 0.3), daemon=True)
        t.start()
        b.sendall(b'{"id":1,"method":"hang"}\n')
        b.settimeout(5)
        data = b.recv(4096)
        self.assertIn(b"did not reply in time", data)
        b.close()

    def test_connect_uses_tcp_when_the_env_names_a_remote_copy(self):
        port = self.start()
        os.environ["NVCOOP_REMOTE_7"] = f"127.0.0.1:{port}"
        try:
            nv = nvbridge.NvBridge.connect(instance=7, wait_s=3)
        finally:
            del os.environ["NVCOOP_REMOTE_7"]
        self.addCleanup(nv.close)
        self.assertIsInstance(nv, nvbridge.NvBridgeTcp)
        self.assertEqual(nv.call("echo", {"x": 1}), {"echo": {"x": 1}})

    def test_twin_without_the_timeout_reply_the_relay_would_hang(self):
        """Twin of the hang test: with a reply timeout far longer than the test waits, nothing comes back in time (what the old code would do)."""
        a, b = socket.socketpair()
        threading.Thread(target=pipe_relay.relay_lines, args=(a, FakePipe(), 30.0), daemon=True).start()
        b.sendall(b'{"id":1,"method":"hang"}\n')
        b.settimeout(0.8)
        with self.assertRaises(socket.timeout):
            b.recv(4096)
        b.close()


if __name__ == "__main__":
    unittest.main()
