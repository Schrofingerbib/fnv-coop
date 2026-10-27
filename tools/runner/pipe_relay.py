"""Forward a game's bridge pipe to a TCP port, so another machine can drive the copy (through an ssh tunnel).

    python -I tools/runner/pipe_relay.py --instance 0 --port 27971

The bridge protocol is one JSON line out, one JSON line back, so the relay moves whole lines: a request line from the TCP client goes to the pipe,
and the reply line goes back. It listens on 127.0.0.1 only; reach it with `ssh -N -L <port>:127.0.0.1:<port> <host>`. The client side is
nvbridge.NvBridgeTcp (NVCOOP_REMOTE_<instance>=host:port makes NvBridge.connect use it).
"""
import argparse
import socket
import sys
import threading
import time


def request_id(line):
    try:
        import json
        return json.loads(line.decode("utf-8")).get("id")
    except (ValueError, AttributeError):
        return None


def relay_lines(sock, pipe, reply_timeout_s=600.0, poll_s=0.002):
    """Serve one TCP client: request line -> pipe, reply line -> client, until the client closes. `pipe` has write(bytes), available() -> int and
    read(n) -> bytes (the real one wraps the Windows pipe; tests use a fake)."""
    buf = b""
    try:
        while True:
            while b"\n" not in buf:
                chunk = sock.recv(65536)
                if not chunk:
                    return
                buf += chunk
            line, buf = buf.split(b"\n", 1)
            pipe.write(line + b"\n")
            reply, end = b"", time.time() + reply_timeout_s
            while b"\n" not in reply:
                n = pipe.available()
                if n:
                    reply += pipe.read(min(n, 1 << 20))
                    continue
                if time.time() > end:
                    import json
                    reply = (json.dumps({"id": request_id(line), "error": {"code": -32099, "message": "relay: the game did not reply in time"}}) + "\n").encode()
                    break
                time.sleep(poll_s)
            sock.sendall(reply)
    except OSError:
        return
    finally:
        try:
            sock.close()
        except OSError:
            pass


class WinPipe:
    """The game's pipe, opened like nvbridge does (a synchronous handle: poll PeekNamedPipe, read only what is there)."""

    def __init__(self, instance):
        import ctypes
        import msvcrt
        from ctypes import wintypes
        self._ct, self._wt = ctypes, wintypes
        self._k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._k32.PeekNamedPipe.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD),
                                            ctypes.c_void_p]
        self._f = open(rf"\\.\pipe\nvcoop-{int(instance)}", "r+b", buffering=0)
        self._h = msvcrt.get_osfhandle(self._f.fileno())

    def write(self, b):
        self._f.write(b)

    def available(self):
        avail = self._wt.DWORD(0)
        if not self._k32.PeekNamedPipe(self._h, None, 0, None, self._ct.byref(avail), None):
            raise OSError("pipe closed by the game")
        return avail.value

    def read(self, n):
        return self._f.read(n)

    def close(self):
        self._f.close()


def serve(instance, port, open_pipe=WinPipe, host="127.0.0.1", stop=None):
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, port))
    srv.listen(8)
    srv.settimeout(0.5)
    print(f"pipe_relay: instance {instance} pipe on {host}:{port}", flush=True)
    while not (stop and stop.is_set()):
        try:
            conn, _ = srv.accept()
        except socket.timeout:
            continue

        def one(c=conn):
            try:
                pipe = open_pipe(instance)
            except OSError:
                c.close()
                return
            try:
                relay_lines(c, pipe)
            finally:
                try:
                    pipe.close()
                except OSError:
                    pass
        threading.Thread(target=one, daemon=True).start()
    srv.close()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--instance", type=int, default=0)
    ap.add_argument("--port", type=int, required=True)
    a = ap.parse_args(argv)
    serve(a.instance, a.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
