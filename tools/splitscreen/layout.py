"""Split-screen window layouts: pure geometry, no Windows calls.

    cells("2lr", Rect(0, 0, 1920, 1080))   -> [Rect(0,0,960,1080), Rect(960,0,960,1080)]
    cells("2tb", ...)                      -> top / bottom halves
    cells("3", ...) and cells("4", ...)    -> the quad: top-left, top-right, bottom-left, bottom-right (3 players use the first three)

Seat 0 (player one, the host, keyboard and mouse) is always the first cell. The cells tile the area exactly (no gaps, no overlap)
unless gap > 0. The size of a cell is also the game's render size (iSize W / iSize H in FalloutPrefs.ini), so a borderless window that
fills its cell shows the game 1:1.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List

import split_mut

MIN_W, MIN_H = 640, 360     # below this the menus are unusable (tools/splitscreen/check_layout.py explains why)
MODES = {"2lr": 2, "2tb": 2, "3": 3, "4": 4}


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    w: int
    h: int

    @property
    def right(self) -> int:
        return self.x + self.w

    @property
    def bottom(self) -> int:
        return self.y + self.h

    def overlaps(self, o: "Rect") -> bool:
        return self.x < o.right and o.x < self.right and self.y < o.bottom and o.y < self.bottom

    def inside(self, o: "Rect") -> bool:
        return self.x >= o.x and self.y >= o.y and self.right <= o.right and self.bottom <= o.bottom

    def as_dict(self) -> dict:
        return {"x": self.x, "y": self.y, "w": self.w, "h": self.h}


def mode_for(players: int, orientation: str = "lr") -> str:
    """players 2..4 and (for 2 players) "lr" (left/right) or "tb" (top/bottom) -> a layout mode name."""
    if players == 2:
        if orientation not in ("lr", "tb"):
            raise ValueError("orientation must be lr or tb")
        return "2" + orientation
    if players in (3, 4):
        return str(players)
    raise ValueError("players must be 2, 3 or 4")


def cells(mode: str, area: Rect, gap: int = 0) -> List[Rect]:
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}; one of {', '.join(MODES)}")
    if area.w <= 0 or area.h <= 0 or gap < 0:
        raise ValueError("empty area")
    g = 0 if split_mut.on("layout_ignore_gap") else gap

    def split(total: int):
        a = (total - g) // 2
        return a, total - g - a          # the second part takes the odd pixel: no pixel is lost

    if mode == "2lr":
        w1, w2 = split(area.w)
        out = [Rect(area.x, area.y, w1, area.h), Rect(area.x + w1 + g, area.y, w2, area.h)]
    elif mode == "2tb":
        h1, h2 = split(area.h)
        out = [Rect(area.x, area.y, area.w, h1), Rect(area.x, area.y + h1 + g, area.w, h2)]
    else:
        w1, w2 = split(area.w)
        h1, h2 = split(area.h)
        if split_mut.on("layout_swap_rows"):
            h1, h2 = h2, h1
        quad = [Rect(area.x, area.y, w1, h1), Rect(area.x + w1 + g, area.y, w2, h1),
                Rect(area.x, area.y + h1 + g, w1, h2), Rect(area.x + w1 + g, area.y + h1 + g, w2, h2)]
        out = quad[:MODES[mode]]
    return out


def fit(rect: Rect, ratio: str) -> Rect:
    """The largest window of the given aspect ratio ("16:9", "4:3", "16:10") that fits in rect, centred, with even sides.
    "fill" returns rect unchanged. A 1920x1080 monitor split left/right gives 960x1080 cells (0.89:1); fitting 16:9 inside gives
    960x540, which the game's HUD is designed for (the rest of the cell shows the desktop)."""
    if ratio in ("fill", "", None):
        return rect
    try:
        a, b = (int(x) for x in ratio.split(":"))
    except ValueError:
        raise ValueError(f"fit must be fill or W:H like 16:9, not {ratio!r}") from None
    if a <= 0 or b <= 0:
        raise ValueError("bad ratio")
    w = rect.w
    h = w * b // a
    if h > rect.h:
        h = rect.h
        w = h * a // b
    w -= w % 2
    h -= h % 2
    if split_mut.on("fit_no_center"):
        return Rect(rect.x, rect.y, w, h)
    return Rect(rect.x + (rect.w - w) // 2, rect.y + (rect.h - h) // 2, w, h)


def too_small(rect: Rect) -> List[str]:
    """Reasons a cell is too small to play in (empty list = fine)."""
    why = []
    if rect.w < MIN_W:
        why.append(f"width {rect.w} is below {MIN_W}")
    if rect.h < MIN_H:
        why.append(f"height {rect.h} is below {MIN_H}")
    return why


def describe(mode: str, rects: List[Rect]) -> str:
    return f"{mode}: " + ", ".join(f"P{i + 1} {r.w}x{r.h}+{r.x}+{r.y}" for i, r in enumerate(rects))
