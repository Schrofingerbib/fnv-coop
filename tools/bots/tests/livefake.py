"""A FakeBridge with what the say-why checks and the live inspector read: a weapon that can be drawn or missing, a reserve ammo count that
drains per shot, a message-box queue in bot.menu (msgIdx / msgCount), and canned coop.status / ui.status / coop.owner answers.

It lives in the tests so tools/bots/fake.py stays as it is. Menu mode eats the Attack pulses (a shot needs no box up and a weapon
equipped), the way the game does: that is what makes "fired, but nothing happened" reproducible offline.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from bots import FakeBridge  # noqa: E402
from bots import primitives as P  # noqa: E402
from bots.bot import BridgeError, RpcError  # noqa: E402

ROLE_HOST, ROLE_CLIENT = "host", "client"


def coop_status_host(last_seq=120, acked=(118,), all_synced=False, ok=12, mismatch=1, repairs=1, entries=None):
    peers = [{"token": str(i), "slot": i + 1, "joined": True, "acked": a} for i, a in enumerate(acked)]
    return {"role": "host", "running": True, "lastSeq": last_seq, "connected": len(peers),
            "host": {"lastSeq": last_seq, "peers": peers, "connected": len(peers), "allSynced": all_synced, "badMessages": 0},
            "desyncs": {"digests": {"received": ok + mismatch, "ok": ok, "mismatch": mismatch, "repairs": repairs, "effectsRepaired": 1},
                        "desyncs": [{"id": 1, "slot": 1, "repaired": True, "entries": entries if entries is not None else [
                            {"key": "quest:00104C70:stage", "host": "20", "client": "10", "clientOnly": False},
                            {"key": "global:GameDaysPassed", "host": "3", "client": "2", "clientOnly": False}]}]}}


def coop_status_client(applied=118, joined=True, rebases=0):
    return {"role": "client", "running": True, "lastSeq": applied, "connected": 1,
            "client": {"joined": joined, "applied": applied, "rebases": rebases, "slot": 1}}


class LiveFake(FakeBridge):
    """role: 'host' or 'client'. Set the canned answers on the instance: coop, ui, owner (None = the method does not exist)."""

    def __init__(self, *a, role=ROLE_HOST, **kw):
        super().__init__(*a, **kw)
        self.role = role
        self.weapon_equipped = True
        self.weapon_out = False
        self.ammo_form = None            # form id whose reserve count drains one per shot (None: the clip model of FakeBridge)
        self.coop = coop_status_host() if role == ROLE_HOST else coop_status_client()
        self.ui = {"role": "host" if role == ROLE_HOST else "guest", "recentBoxes": []}
        self.owner = {"owner": 0, "epoch": 2, "known": True, "mine": role == ROLE_HOST}
        self.menu_kind = None            # force a menu kind (bot.menu) without queued boxes

    # ---- menu mode eats the Attack pulses ----
    def _shoot(self):
        if self.native_boxes or self.box is not None or not self.weapon_equipped:
            return
        if self.ammo_form is not None:
            if self.me.items.get(self.ammo_form, 0) < 1:
                return
            self.me.items[self.ammo_form] -= 1
        self.weapon_out = True
        super()._shoot()

    # ---- expressions the base fake does not know ----
    def eval(self, expr, ref=None):
        if re.match(r"\s*player\.GetEquippedObject\b", expr):      # a bare Form: printc refuses it (in game 2026-10-22)
            raise BridgeError(f"cannot evaluate {expr!r}: 'Error on line 1\r\n\r\nInvalid expression for parameter 2. Expected variable (got Form).'")
        m = re.match(r"\s*(?:\(\s*)?player\.(IsWeaponOut|GetEquippedObject)\b", expr)
        if m:
            self.calls.append(("eval", expr))
            return float(self.weapon_out if m.group(1) == "IsWeaponOut" else self.weapon_equipped)
        return super().eval(expr, ref)

    # ---- bridge methods ----
    def call(self, method, params=None, timeout_s=35.0):
        params = params or {}
        if method == "coop.status":
            self.calls.append((method, params))
            if self.coop is None:
                raise RpcError(-32601, "unknown method: coop.status", method)
            return self.coop
        if method == "ui.status":
            self.calls.append((method, params))
            if self.ui is None:
                raise RpcError(-32601, "unknown method: ui.status", method)
            return self.ui
        if method == "coop.owner":
            self.calls.append((method, params))
            if self.owner is None:
                raise RpcError(-32601, "unknown method: coop.owner", method)
            return self.owner
        if method == P.M_BOT_MENU:
            r = dict(super().call(method, params, timeout_s))
            if self.menu_kind and r.get("kind") in ("none", None):
                r["kind"] = self.menu_kind
            if self.native_boxes:
                r["msgIdx"], r["msgCount"] = 0, len(self.native_boxes)
            return r
        return super().call(method, params, timeout_s)
