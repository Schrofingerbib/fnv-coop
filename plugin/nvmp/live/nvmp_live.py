#!/usr/bin/env python3
"""First live NV:MP + nvcoop session: local offline NV:MP story server + 2 private FNV instances (L6).

    FNV_INSTANCES_ROOT=<...>\\fnv-instances-l6 python -I -X utf8 plugin/nvmp/live/nvmp_live.py check      # never launches anything
    FNV_INSTANCES_ROOT=<...>\\fnv-instances-l6 python -I -X utf8 plugin/nvmp/live/nvmp_live.py go --confirm-firewall

`check` validates every precondition and prints the exact commands. `go` additionally REFUSES to run unless
  * --confirm-firewall is given AND an enabled inbound Allow rule for exactly <root>\\0\\nvmp_storyserver.exe exists
    (verified through Get-NetFirewallApplicationFilter; starting the server without it raises a desktop prompt), and
  * the game slots are acquired through tools/gameslot.py (owner L6, 2 slots).
Sequence: server (offline, cwd = instance 0) -> instance 0 -> instance 1 via nvmp_start.exe (offline args, distinct names)
-> wait for the nvcoop pipes -> collect nvmp.patches / nvmp.players / events from both -> teardown (kills only our pids,
releases the slots). All output goes under out/L6/live/. Windows are started minimized without activation.
"""
import argparse, json, os, re, subprocess, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
NV = HERE.parent
REPO = NV.parents[1]
sys.path.insert(0, str(REPO / "tools" / "runner"))
sys.path.insert(0, str(REPO / "tools" / "questdata"))

OWNER = "L6"
PORT = 27015
GAME_ID = "22380"            # LookupGameID(): Steam FalloutNV.exe sha1 D068F394...
OUT = REPO / "out" / "L6" / "live"
STEAM_ENV = {"SteamAppId": "22380", "SteamGameId": "22380", "SteamClientLaunch": "1"}
NAMES = {0: "HostCourier", 1: "GuestCourier"}


def root() -> Path:
    r = os.environ.get("FNV_INSTANCES_ROOT")
    if not r:
        raise SystemExit("set FNV_INSTANCES_ROOT")
    return Path(r)


def ps(cmd: str) -> str:
    return subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True).stdout.strip()


def firewall_rule_ok(server: Path) -> bool:
    """Enabled inbound Allow rule whose program is exactly this exe (case-insensitive)."""
    script = (
        "$p='%s'; Get-NetFirewallApplicationFilter -ErrorAction SilentlyContinue | Where-Object { $_.Program -ieq $p } | "
        "ForEach-Object { $r = Get-NetFirewallRule -AssociatedNetFirewallApplicationFilter $_; "
        "if ($r.Enabled -eq 'True' -and $r.Direction -eq 'Inbound' -and $r.Action -eq 'Allow') { 'OK' } }" % str(server).replace("'", "''")
    )
    return "OK" in ps(script)


def mods_list(r: Path) -> str:
    import loadorder
    present = {p.name.lower() for p in (r / "0" / "Data").glob("*") if p.suffix.lower() in (".esm", ".esp")}
    return ",".join(p for p in loadorder.load() if p.lower() in present)


def udp_port_busy(port: int) -> bool:
    out = subprocess.run(["netstat", "-ano", "-p", "UDP"], capture_output=True, text=True).stdout
    return any(f":{port} " in l for l in out.splitlines())


def preflight(r: Path):
    problems, notes = [], []
    import instances
    server = r / "0" / "nvmp_storyserver.exe"
    for i in (0, 1):
        d = r / str(i)
        for rel in ("instance.json", "FalloutNV.exe", "nvse_loader.exe", "nvmp_start.exe", "nvmp/client.dll", "Data/NVSE/Plugins/nvcoop.dll",
                    "Data/NVSE/Plugins/nvcoop.ini"):
            if not (d / rel).exists():
                problems.append(f"instance {i}: missing {rel}")
        ini = d / "Data/NVSE/Plugins/nvcoop.ini"
        if ini.exists() and "minimized=1" not in ini.read_text():
            problems.append(f"instance {i}: nvcoop.ini lacks minimized=1")
    if not server.exists():
        problems.append("server exe missing at " + str(server))
    pins = json.loads((NV / "pinned_builds.json").read_text())["9.4"]
    if (r / "0" / "nvmp/client.dll").exists() and instances.sha256_file(r / "0" / "nvmp/client.dll") != pins["client.dll"]:
        problems.append("client.dll is not the pinned 9.4 build")
    built = REPO / "out" / "L6" / "plugin" / "nvcoop.dll"
    deployed = r / "0" / "Data/NVSE/Plugins/nvcoop.dll"
    if built.exists() and deployed.exists() and instances.sha256_file(built) != instances.sha256_file(deployed):
        notes.append("instance nvcoop.dll differs from out/L6/plugin/nvcoop.dll (re-provision/copy to refresh)")
    mods = mods_list(r)
    if not mods.startswith("FalloutNV.esm"):
        problems.append("mods list does not start with FalloutNV.esm: " + mods)
    if udp_port_busy(PORT):
        problems.append(f"UDP {PORT} already in use")
    return problems, notes, server, mods


def cmds(r: Path, mods: str):
    server = [str(r / "0" / "nvmp_storyserver.exe"), "-eos_disable"]
    clients = [[str(r / str(i) / "nvmp_start.exe"), "127.0.0.1", str(PORT), GAME_ID, "invalid_jwt_token", "invalid_product_id", mods, NAMES[i]]
               for i in (0, 1)]
    return server, clients


def read_tail(p: Path, n: int = 400000) -> str:
    try:
        with open(p, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - n))
            return f.read().decode("utf-8", "replace")
    except OSError:
        return ""


def spawn(cmd, cwd, env=None, log=None, minimized=True):
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 7 if minimized else 0     # SW_SHOWMINNOACTIVE / SW_HIDE
    out = open(log, "wb") if log else subprocess.DEVNULL
    e = dict(os.environ)
    e.update(env or {})
    return subprocess.Popen(cmd, cwd=str(cwd), env=e, startupinfo=si, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                            creationflags=0x00000008 | 0x00000200 | 0x08000000)   # DETACHED | NEW_PROCESS_GROUP | NO_WINDOW


def go(r: Path, a) -> int:
    import nvbridge, run, winproc
    problems, notes, server, mods = preflight(r)
    if problems:
        print("preflight FAILED:", *problems, sep="\n  ")
        return 2
    if not a.confirm_firewall or not firewall_rule_ok(server):
        print("REFUSING to start the server: needs --confirm-firewall AND an enabled inbound Allow rule for exactly\n  %s" % server)
        return 3
    OUT.mkdir(parents=True, exist_ok=True)
    result = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "mods": mods, "steps": {}}
    pids = {}
    srv_cmd, cli_cmds = cmds(r, mods)
    print(run.slot_acquire(OWNER, ttl=a.ttl, wait=a.slot_wait))
    srv = None
    try:
        before = set(winproc.list_pids("FalloutNV.exe"))
        srv = spawn(srv_cmd, r / "0", log=OUT / "server.log")
        pids["server"] = srv.pid
        time.sleep(4)
        result["steps"]["server_alive"] = srv.poll() is None
        if srv.poll() is not None:
            print("server exited early; see", OUT / "server.log")
            return 4
        for i in (0, 1):
            known = set(winproc.list_pids("FalloutNV.exe"))
            env = dict(STEAM_ENV, NVCOOP_INSTANCE=str(i))
            spawn(cli_cmds[i], r / str(i), env=env, log=OUT / f"nvmp_start_{i}.log")
            pid = None
            for _ in range(120):
                new = set(winproc.list_pids("FalloutNV.exe")) - known - before
                if new:
                    pid = sorted(new)[0]
                    break
                time.sleep(0.5)
            if not pid:
                print(f"instance {i}: FalloutNV.exe did not appear")
                return 5
            run.register_pid(pid, OWNER)
            pids[str(i)] = pid
            import threading
            threading.Thread(target=winproc.minimize_windows, args=(pid,), daemon=True).start()
            t0 = time.time()
            ok = False
            while time.time() - t0 < a.wait_pipe:
                try:
                    with nvbridge.NvBridge.connect(i, wait_s=1.0) as b:
                        result["steps"][f"ping{i}"] = b.call("ping", timeout_s=5)
                        ok = True
                        break
                except Exception:
                    time.sleep(1)
            if not ok:
                print(f"instance {i}: nvcoop pipe never answered")
                return 6
        # NV:MP has no auto world entry: the client sits at the main menu (the server creates the NetPlayer anyway) and
        # the player enters the world by loading a save, so drive nvbridge loadSave on both clients once the menu is up.
        if a.save:
            for i in (0, 1):
                src = Path(a.save_src)
                dst = r / str(i) / "Saves" / (a.save + ".fos")
                dst.parent.mkdir(exist_ok=True)
                if not dst.exists():
                    import shutil
                    shutil.copyfile(src, dst)
                result["steps"][f"savecopy{i}"] = str(dst)
            t0 = time.time()
            while time.time() - t0 < a.wait_ready:
                try:
                    with nvbridge.NvBridge.connect(0, wait_s=2.0) as b0, nvbridge.NvBridge.connect(1, wait_s=2.0) as b1:
                        if b0.call("ping", timeout_s=15).get("menuReady") and b1.call("ping", timeout_s=15).get("menuReady"):
                            break
                except Exception:
                    pass
                time.sleep(5)
            for i in (0, 1):
                try:
                    with nvbridge.NvBridge.connect(i, wait_s=5.0) as b:
                        result["steps"][f"loadSave{i}"] = b.load_save(a.save, timeout_ms=300000)
                except Exception as e:
                    result["steps"][f"loadSave{i}"] = f"ERROR {e}"
        # Wait for the game to be fully up (menuReady + a loaded game) on both instances; the PC is slow (5-17 fps).
        t0 = time.time()
        ready = {0: False, 1: False}
        last = {}
        while time.time() - t0 < a.wait_ready and not all(ready.values()):
            for i in (0, 1):
                try:
                    with nvbridge.NvBridge.connect(i, wait_s=2.0) as b:
                        pg = b.call("ping", timeout_s=15)
                    last[i] = pg
                    ready[i] = bool(pg.get("menuReady")) and int(pg.get("loadedCount", 0)) >= 1
                except Exception as e:
                    last[i] = f"ERROR {e}"
            time.sleep(5)
        result["steps"]["ready"] = ready
        result["steps"]["last_ping"] = last
        # Confirm both clients connected from the logs before querying.
        conn_re = re.compile(r"(connected|joined|accepted|authenticated|welcome)", re.I)
        t1 = time.time()
        while time.time() - t1 < a.wait_connect:
            txt = " ".join(read_tail(OUT / f) for f in ("server.log", "nvmp_start_0.log", "nvmp_start_1.log"))
            if all(n in txt for n in NAMES.values()) and conn_re.search(read_tail(OUT / "server.log")):
                break
            time.sleep(5)
        result["steps"]["server_log_hits"] = [l for l in read_tail(OUT / "server.log").splitlines() if conn_re.search(l) or "Strict" in l or "NVSE" in l][-40:]
        for i in (0, 1):
            result["steps"][f"client_log_hits_{i}"] = [l for l in read_tail(OUT / f"nvmp_start_{i}.log").splitlines()
                                                       if re.search(r"connect|reject|kick|denied|mismatch|plugin|disconnect", l, re.I)][-40:]
        for i in (0, 1):
            for m, params in (("nvmp.patches", None), ("nvmp.players", None), ("events", {"since": 0})):
                try:   # fresh connection per call so a timeout cannot desync reply ids
                    with nvbridge.NvBridge.connect(i, wait_s=5.0) as b:
                        result["steps"][f"{m}_{i}"] = b.call(m, params, timeout_s=60) if params else b.call(m, timeout_s=60)
                except Exception as e:
                    result["steps"][f"{m}_{i}"] = f"ERROR {e}"
        result["ok"] = True
        return 0
    finally:
        (OUT / "result.json").write_text(json.dumps(result, indent=1, default=str))
        for k, pid in pids.items():
            if k != "server" and pid in winproc.list_pids("FalloutNV.exe"):
                winproc.kill_pid(pid)
        if srv and srv.poll() is None:
            srv.kill()
        print(run.slot_release(OWNER))
        print("result:", OUT / "result.json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["check", "go"])
    ap.add_argument("--confirm-firewall", action="store_true")
    ap.add_argument("--save", default="autosave_l6nvmp", help="save name to load on both clients (copied into Saves/ if absent); empty = none")
    ap.add_argument("--save-src", default=str(REPO.parent / "fnv-instances-l10" / "0" / "Saves" / "autosave.fos"))
    ap.add_argument("--ttl", type=int, default=1800)
    ap.add_argument("--slot-wait", type=int, default=1800)
    ap.add_argument("--wait-pipe", type=int, default=240)
    ap.add_argument("--wait-ready", type=int, default=900)
    ap.add_argument("--wait-connect", type=int, default=120)
    a = ap.parse_args()
    r = root()
    if a.cmd == "go":
        return go(r, a)
    problems, notes, server, mods = preflight(r)
    srv_cmd, cli_cmds = cmds(r, mods)
    print("server  :", " ".join(srv_cmd), "\n  cwd", r / "0")
    for i, c in enumerate(cli_cmds):
        print(f"client {i}:", " ".join(f'"{x}"' if " " in x or "," in x else x for x in c), "\n  cwd", r / str(i))
    print("mods    :", mods)
    print("firewall rule for the server exe:", "PRESENT" if firewall_rule_ok(server) else "ABSENT (go would refuse)")
    for n in notes:
        print("note:", n)
    if problems:
        print("PROBLEMS:", *problems, sep="\n  ")
        return 2
    print("preflight OK (nothing launched)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
