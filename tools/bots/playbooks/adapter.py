"""Wraps T6a's tools/bots/bot.py `Bot` so it has the method names and argument order the playbooks use.

Playbooks were written against the small surface in fakecoop.FakeBot. T6a's real Bot differs in a few signatures
(take(container, item, count), fire(ref, shots), choose_topic, inventory_count(item, holder)...). This class translates and
leaves everything else to the wrapped Bot (`__getattr__`). Actions that have no primitive yet return a failed result whose
reason says which bridge method is missing; they are listed in docs/tools/T6b-playbooks.md.
"""
from __future__ import annotations

from typing import Any, Optional

from .probe import MissingBridge, Result


class PlaybookBot:
    def __init__(self, bot: Any):
        self._b = bot
        self._aim: Optional[str] = None
        self.name = getattr(bot, "name", "bot")

    def __getattr__(self, item):          # everything not overridden: the wrapped Bot
        return getattr(self._b, item)

    # ---- actions whose signature differs ----
    def aim_at(self, ref, timeout_s=5.0):
        r = self._b.aim_at(ref)
        if r:
            self._aim = ref
        return r

    def fire(self, n=1, timeout_s=5.0):
        if self._aim is None:
            return Result(False, "nothing aimed at")
        return self._b.fire(self._aim, shots=n)

    def vats_target(self, ref, limb=None, timeout_s=10.0):
        return self._b.vats_target(ref, limb)

    def take(self, item, count=1, from_ref=None, timeout_s=10.0):
        return self._b.take(from_ref, item, count)

    def put(self, item, count, to_ref, timeout_s=10.0):
        return self._b.put(to_ref, item, count)

    def pickup(self, ref, timeout_s=10.0):
        return self._b.pickup(ref)

    def talk(self, npc, timeout_s=10.0):
        return self._b.talk(npc)

    def pick_topic(self, info_id, timeout_s=10.0):
        return self._b.choose_topic(info_id)

    def close_container(self, ref):
        # the menu closes with the next action; T6a has no close primitive
        return Result(True, "", {"note": "no explicit close"})

    def unlock(self, ref, key=None, timeout_s=10.0):
        """UNVERIFIED console primitive: `unlock` with `ref` as the calling reference (exec {cmd, ref}; the console has
        no `<hex>.unlock` form). The key item is not consumed or checked."""
        try:
            self._b._exec_on(ref, "unlock", "unlock")      # noqa: SLF001 (T6a's checked console call)
        except Exception as e:  # noqa: BLE001
            return Result(False, f"unlock failed: {e}")
        return Result(not self._b.locked(ref), "still locked after unlock")

    def use_terminal(self, ref, entry=0, timeout_s=10.0):
        """T6a Bot.use_terminal(ref, entry): `entry` is the 0-based menu index."""
        return self._b.use_terminal(ref, int(entry or 0), timeout_s)

    def terminal_entry_used(self, ref, entry):
        """What THIS bot picked (the engine keeps no used flag, so other machines cannot be asked)."""
        used = self._b.terminal_used(ref)
        return any(u.get("index") == int(entry or 0) for u in used)

    # ---- queries ----
    def inventory(self):
        return {}                       # not available; probe.item_count uses inventory_count

    def door_open(self, ref):
        return self._b.door_state(ref) == "open"

    def is_locked(self, ref):
        return self._b.locked(ref)

    def distance_to(self, ref):
        a, b = self._b.pos(), self._b.pos(ref)
        return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5

    def ref_count(self, base, cell=""):
        if not hasattr(self._b, "count_refs"):
            raise MissingBridge("bridge method dev.countRefs is not implemented in this build")
        return self._b.count_refs(base, cell or None)

    def consent(self, kind, quest="", info="", yes=True):
        return self._b.consent("ready" if yes else "notyet")

    def menu_press(self, button):
        return self._b.press_box(button)

    def snapshot(self, path):
        return self._b.snapshot(path)
