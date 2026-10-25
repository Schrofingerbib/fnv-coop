"""Off Windows, provision finds the game and its Proton prefix by itself (steamlib.py), so a Steam Deck needs neither
FNV_GAME_DIR nor FNV_DOCUMENTS_DIR. The fake home has the layout of a Deck with the game on an SD card library: the
Steam root lists the card in libraryfolders.vdf, the card has the game's appmanifest, the game and its prefix. A stale
prefix in the Steam root (the game was moved) must not be picked.
Twins: libraryfolders.vdf ignored, the prefix taken from the first library instead of the game's, and the Windows
Program Files default used off Windows must each fail the check.
Run: python -I tools/runner/tests/test_steamlib.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import instances  # noqa: E402
import mygames  # noqa: E402
import steamlib  # noqa: E402

DOCS = ("pfx", "drive_c", "users", "steamuser", "Documents")


def vdf_path(p: Path) -> str:
    return str(p).replace("\\", "\\\\")   # Steam escapes backslashes; a Linux path has none


class Deck:
    """home/.local/share/Steam (root, a stale prefix) and card/ (the game, its appmanifest and its prefix)."""

    def __init__(self, base: Path, appmanifest: bool = True):
        self.home = base / "home"
        self.root = self.home / ".local" / "share" / "Steam"
        self.card = base / "card"
        (self.root / "steamapps").mkdir(parents=True)
        (self.root / "steamapps" / "libraryfolders.vdf").write_text(
            '"libraryfolders"\n{\n\t"0"\n\t{\n\t\t"path"\t\t"%s"\n\t}\n\t"1"\n\t{\n\t\t"path"\t\t"%s"\n\t}\n}\n'
            % (vdf_path(self.root), vdf_path(self.card)), encoding="utf-8")
        self.stale_docs = self.root / "steamapps" / "compatdata" / "22380" / Path(*DOCS)
        self.stale_docs.mkdir(parents=True)
        self.game = self.card / "steamapps" / "common" / "FNV Custom"
        self.game.mkdir(parents=True)
        (self.game / "FalloutNV.exe").write_bytes(b"MZ")
        if appmanifest:
            (self.card / "steamapps" / "appmanifest_22380.acf").write_text(
                '"AppState"\n{\n\t"appid"\t\t"22380"\n\t"installdir"\t\t"FNV Custom"\n}\n', encoding="utf-8")
        self.docs = self.card / "steamapps" / "compatdata" / "22380" / Path(*DOCS)
        (self.docs / "My Games" / "FalloutNV").mkdir(parents=True)


def deck_errors(deck: Deck) -> list:
    """What the runner gets wrong on the fake Deck with no FNV_GAME_DIR / FNV_DOCUMENTS_DIR set."""
    errs = []
    with mock.patch.object(Path, "home", lambda *a: deck.home), mock.patch.object(steamlib, "OFF_WINDOWS", True), \
            mock.patch.dict(os.environ):
        os.environ.pop("FNV_GAME_DIR", None)
        os.environ.pop("FNV_DOCUMENTS_DIR", None)
        game, docs = instances.game_dir(), mygames.documents_dir()
    if game != deck.game:
        errs.append(f"game dir {game}, want the card's {deck.game}")
    if docs != deck.docs:
        errs.append(f"Documents {docs}, want the game's prefix {deck.docs}" + (" (the stale one)" if docs == deck.stale_docs else ""))
    return errs


class SteamLib(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.base = Path(td.name)

    def test_deck_finds_game_and_prefix(self):
        self.assertEqual(deck_errors(Deck(self.base)), [])

    def test_without_appmanifest_the_common_folder_names_still_count(self):
        d = Deck(self.base, appmanifest=False)
        (d.game.parent / "Fallout New Vegas").mkdir()
        (d.game.parent / "Fallout New Vegas" / "FalloutNV.exe").write_bytes(b"MZ")
        self.assertEqual(steamlib.find_game(d.home), d.game.parent / "Fallout New Vegas")

    def test_linked_root_is_listed_once(self):
        d = Deck(self.base)
        (d.home / ".steam").mkdir()
        try:
            os.symlink(d.root, d.home / ".steam" / "steam", target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("no symlinks here")
        self.assertEqual(steamlib.steam_roots(d.home), [d.home / ".steam" / "steam"])
        self.assertEqual(len(steamlib.libraries(d.home)), 2)

    def test_flatpak_root(self):
        home = self.base / "home"
        root = home / ".var" / "app" / "com.valvesoftware.Steam" / ".local" / "share" / "Steam"
        game = root / "steamapps" / "common" / "Fallout New Vegas"
        game.mkdir(parents=True)
        (game / "FalloutNV.exe").write_bytes(b"MZ")
        self.assertEqual(steamlib.find_game(home), game)

    def test_env_overrides_still_win(self):
        d = Deck(self.base)
        other = self.base / "other"
        with mock.patch.object(Path, "home", lambda *a: d.home), mock.patch.object(steamlib, "OFF_WINDOWS", True), \
                mock.patch.dict(os.environ, {"FNV_GAME_DIR": str(other), "FNV_DOCUMENTS_DIR": str(other / "Docs")}):
            self.assertEqual(instances.game_dir(), other)
            self.assertEqual(mygames.documents_dir(), (other / "Docs").resolve())

    def test_game_dir_env_points_at_its_own_prefix(self):
        """FNV_GAME_DIR set, FNV_DOCUMENTS_DIR not: the Documents of that install's library."""
        d = Deck(self.base)
        with mock.patch.object(Path, "home", lambda *a: self.base / "nobody"), mock.patch.object(steamlib, "OFF_WINDOWS", True), \
                mock.patch.dict(os.environ, {"FNV_GAME_DIR": str(d.game)}):
            os.environ.pop("FNV_DOCUMENTS_DIR", None)
            self.assertEqual(mygames.documents_dir(), d.docs)

    def test_no_steam_keeps_the_old_fallbacks(self):
        home = self.base / "empty"
        home.mkdir()
        with mock.patch.object(Path, "home", lambda *a: home), mock.patch.object(steamlib, "OFF_WINDOWS", True), \
                mock.patch.dict(os.environ):
            os.environ.pop("FNV_GAME_DIR", None)
            os.environ.pop("FNV_DOCUMENTS_DIR", None)
            self.assertEqual(instances.game_dir(), Path(instances.DEFAULT_GAME))
            self.assertEqual(mygames.documents_dir(), home / "Documents")

    def test_windows_does_not_look_in_steam_libraries(self):
        d = Deck(self.base)
        with mock.patch.object(Path, "home", lambda *a: d.home), mock.patch.object(steamlib, "OFF_WINDOWS", False), \
                mock.patch.object(steamlib, "find_game", lambda *a, **k: self.fail("Steam libraries searched on Windows")), \
                mock.patch.object(mygames, "shell_documents", lambda: Path("W:/Docs")), mock.patch.dict(os.environ):
            os.environ.pop("FNV_GAME_DIR", None)
            os.environ.pop("FNV_DOCUMENTS_DIR", None)
            self.assertEqual(instances.game_dir(), Path(instances.DEFAULT_GAME))
            self.assertEqual(mygames.documents_dir(), Path("W:/Docs"))

    # ---- twins
    def test_twin_libraryfolders_ignored_is_caught(self):
        with mock.patch.object(steamlib.fnv_find, "parse_libraryfolders", lambda text: []):
            self.assertNotEqual(deck_errors(Deck(self.base)), [])

    def test_twin_prefix_from_the_first_library_is_caught(self):
        d = Deck(self.base)
        with mock.patch.object(steamlib, "prefix_documents", lambda game: d.stale_docs):
            errs = deck_errors(d)
        self.assertTrue(any("stale" in e for e in errs), errs)

    def test_twin_windows_default_off_windows_is_caught(self):
        with mock.patch.object(steamlib, "find_game", lambda *a, **k: None):
            self.assertNotEqual(deck_errors(Deck(self.base)), [])


if __name__ == "__main__":
    unittest.main()
