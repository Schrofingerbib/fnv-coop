"""Find Fallout: New Vegas installs and check what the co-op plugin needs from them.

Where it looks (in this order, duplicates removed): the folder given on the command line, the FNV_GAME_DIR environment
variable, the Steam registry entries and Steam library folders, the GOG and retail (Bethesda) registry entries, and a list
of common folders on every drive. A folder only counts when it holds FalloutNV.exe.

Everything that touches Windows (registry, drives, the process list) goes through small objects so the tests can fake it.
"""
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import fnv_mut
import fnv_pe

GAME_EXE = "FalloutNV.exe"
SUPPORTED_GAME = (1, 4, 0, 525)
MIN_NVSE = (6, 3, 0)          # plugin/main.cpp refuses older xNVSE (the APIs it uses exist since 6.3)
NVSE_DLL = "nvse_1_4.dll"
NVSE_LOADER = "nvse_loader.exe"
PLUGINS_REL = "Data/NVSE/Plugins"
# Official download pages, shown to the player. The installer itself never downloads anything.
LINK_NVSE = "https://github.com/xNVSE/NVSE/releases"
LINK_PYTHON = "https://www.python.org/downloads/"
STEAM_NAMES = ("Fallout New Vegas", "Fallout New Vegas PCR")
GOG_IDS = ("1454587428", "1454315831")    # Ultimate Edition, base game; the registry scan below also finds other ids


class Registry:
    """Read-only view of the Windows registry. Real one below; tests pass a fake with the same two methods."""

    def get(self, hive: str, key: str, value: str) -> Optional[str]:
        return None

    def subkeys(self, hive: str, key: str) -> List[str]:
        return []


class WinRegistry(Registry):
    def _open(self, hive, key, wow):
        import winreg
        h = {"HKLM": winreg.HKEY_LOCAL_MACHINE, "HKCU": winreg.HKEY_CURRENT_USER}[hive]
        return winreg.OpenKey(h, key, 0, winreg.KEY_READ | wow)

    def _views(self):
        import winreg
        return (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY)

    def get(self, hive, key, value):
        if os.name != "nt":
            return None
        import winreg
        for wow in self._views():
            try:
                with self._open(hive, key, wow) as k:
                    v, _t = winreg.QueryValueEx(k, value)
                    if isinstance(v, str) and v:
                        return v
            except OSError:
                continue
        return None

    def subkeys(self, hive, key):
        if os.name != "nt":
            return []
        import winreg
        out: List[str] = []
        for wow in self._views():
            try:
                with self._open(hive, key, wow) as k:
                    i = 0
                    while True:
                        try:
                            out.append(winreg.EnumKey(k, i))
                        except OSError:
                            break
                        i += 1
            except OSError:
                continue
        return sorted(set(out))


def real_drives() -> List[str]:
    if os.name != "nt":
        return []
    import string
    return [f"{c}:\\" for c in string.ascii_uppercase if os.path.exists(f"{c}:\\")]


@dataclass
class Candidate:
    path: Path
    source: str          # "argument" "env" "steam" "gog" "retail" "common"
    edition: str = ""    # "steam" "gog" "retail" or ""


def _norm(p: Path) -> str:
    return os.path.normcase(os.path.normpath(str(p)))


def parse_libraryfolders(text: str) -> List[str]:
    """Steam library paths from libraryfolders.vdf (new format: "path" "D:\\\\Lib"; old format: "1" "D:\\\\Lib")."""
    out = []
    for m in re.finditer(r'"(?:path|\d+)"\s+"([^"]+)"', text):
        p = m.group(1).replace("\\\\", "\\")
        if re.match(r"^[A-Za-z]:[\\/]", p) or p.startswith(("/", "\\\\")):
            out.append(p)
    return out


def parse_appmanifest_installdir(text: str) -> Optional[str]:
    m = re.search(r'"installdir"\s+"([^"]+)"', text)
    return m.group(1) if m else None


def _steam_roots(reg: Registry, env: Dict[str, str]) -> List[Path]:
    roots = []
    for hive, key, value in (("HKCU", r"Software\Valve\Steam", "SteamPath"),
                             ("HKLM", r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
                             ("HKLM", r"SOFTWARE\Valve\Steam", "InstallPath")):
        v = reg.get(hive, key, value)
        if v:
            roots.append(Path(v.replace("/", "\\")))
    pf = env.get("ProgramFiles(x86)")
    if pf:
        roots.append(Path(pf) / "Steam")
    return roots


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def candidates(arg: Optional[str] = None, env: Optional[Dict[str, str]] = None, reg: Optional[Registry] = None,
               drives: Optional[Callable[[], List[str]]] = None) -> List[Candidate]:
    """Every folder that holds FalloutNV.exe, best guess first, no duplicates."""
    env = dict(os.environ) if env is None else env
    reg = reg if reg is not None else WinRegistry()
    drives = drives or real_drives
    found: List[Candidate] = []
    seen = set()

    def add(p, source, edition=""):
        p = Path(p)
        if (p / GAME_EXE).is_file() and (_norm(p) not in seen or fnv_mut.on("find_no_dedup")):
            seen.add(_norm(p))
            found.append(Candidate(p, source, edition))

    if arg:
        add(arg, "argument")
    if env.get("FNV_GAME_DIR"):
        add(env["FNV_GAME_DIR"], "env")
    libs: List[Path] = []
    for root in _steam_roots(reg, env):
        libs.append(root)
        text = _read(root / "steamapps" / "libraryfolders.vdf")
        if not fnv_mut.on("find_ignore_libraries"):
            libs += [Path(x) for x in parse_libraryfolders(text)]
    for lib in libs:
        common = lib / "steamapps" / "common"
        for appid in ("22380", "22490"):
            inst = parse_appmanifest_installdir(_read(lib / "steamapps" / f"appmanifest_{appid}.acf"))
            if inst:
                add(common / inst, "steam", "steam")
        for name in STEAM_NAMES:
            add(common / name, "steam", "steam")
    for gid in GOG_IDS:
        v = reg.get("HKLM", rf"SOFTWARE\WOW6432Node\GOG.com\Games\{gid}", "path")
        if v:
            add(v, "gog", "gog")
    for sub in reg.subkeys("HKLM", r"SOFTWARE\WOW6432Node\GOG.com\Games"):
        name = reg.get("HKLM", rf"SOFTWARE\WOW6432Node\GOG.com\Games\{sub}", "gameName") or ""
        if "new vegas" in name.lower():
            v = reg.get("HKLM", rf"SOFTWARE\WOW6432Node\GOG.com\Games\{sub}", "path")
            if v:
                add(v, "gog", "gog")
    for key in (r"SOFTWARE\WOW6432Node\Bethesda Softworks\FalloutNV", r"SOFTWARE\Bethesda Softworks\FalloutNV"):
        v = reg.get("HKLM", key, "Installed Path")
        if v:
            add(v, "retail", "retail")
    for d in drives():
        d = d.rstrip("\\/") + "\\"
        for rel in (r"GOG Games\Fallout New Vegas", r"GOG Games\Fallout New Vegas Ultimate Edition",
                    r"Program Files (x86)\GOG Galaxy\Games\Fallout New Vegas", r"Program Files (x86)\GOG Galaxy\Games\Fallout New Vegas Ultimate Edition",
                    r"Program Files (x86)\Steam\steamapps\common\Fallout New Vegas", r"Program Files\Steam\steamapps\common\Fallout New Vegas",
                    r"SteamLibrary\steamapps\common\Fallout New Vegas", r"Steam\steamapps\common\Fallout New Vegas",
                    r"Games\Fallout New Vegas", r"Games\Steam\steamapps\common\Fallout New Vegas",
                    r"Program Files (x86)\Bethesda Softworks\Fallout New Vegas", r"Program Files\Bethesda Softworks\Fallout New Vegas"):
            add(Path(d) / rel, "common")
    return found


@dataclass
class GameInfo:
    path: Path
    exe_version: Optional[fnv_pe.Version] = None
    nvse_loader: bool = False
    nvse_dll: bool = False
    nvse_version: Optional[fnv_pe.Version] = None
    data_dir: bool = False
    edition: str = ""
    problems: List[str] = field(default_factory=list)       # block the install, --force cannot override them
    overridable: List[str] = field(default_factory=list)    # block the install, --force overrides them (wrong game or xNVSE version)
    warnings: List[str] = field(default_factory=list)       # shown, do not block

    @property
    def ok(self) -> bool:
        return not self.problems and not self.overridable


def nvse_triple(v: Optional[fnv_pe.Version]) -> Optional[Tuple[int, int, int]]:
    """xNVSE writes its file version as 0.6.3.4 meaning release 6.3.4: drop the leading zero."""
    if v is None:
        return None
    return (v[1], v[2], v[3]) if v[0] == 0 else (v[0], v[1], v[2])


def inspect_game(path, edition: str = "") -> GameInfo:
    """Read-only checks of a game folder: game version, xNVSE, folders. Fills problems (block) and warnings."""
    p = Path(path)
    g = GameInfo(path=p, edition=edition)
    exe = p / GAME_EXE
    if not exe.is_file():
        g.problems.append(f"{GAME_EXE} not found in {p}. Pick the folder that holds {GAME_EXE}.")
        return g
    g.exe_version = fnv_pe.file_version(exe)
    if g.exe_version is None:
        g.overridable.append(f"Cannot read the version of {GAME_EXE}. Co-op needs the 1.4.0.525 release; verify the game files in "
                          "Steam or GOG Galaxy, then try again. (--force skips this check at your own risk.)")
    elif g.exe_version != SUPPORTED_GAME and not fnv_mut.on("skip_version_check"):
        g.overridable.append(f"{GAME_EXE} is version {fnv_pe.format_version(g.exe_version)}, co-op needs "
                          f"{fnv_pe.format_version(SUPPORTED_GAME)} (the latest patch). Update or verify the game files, then try again.")
    g.data_dir = (p / "Data").is_dir()
    if not g.data_dir:
        g.problems.append(f"There is no Data folder in {p}; this does not look like a complete install.")
    elif not (p / "Data" / "FalloutNV.esm").is_file():
        g.problems.append("Data\\FalloutNV.esm is missing. Verify the game files, then try again.")
    g.nvse_loader = (p / NVSE_LOADER).is_file()
    g.nvse_dll = (p / NVSE_DLL).is_file()
    if not (g.nvse_loader and g.nvse_dll) and not fnv_mut.on("skip_nvse_check"):
        missing = [n for n, ok in ((NVSE_LOADER, g.nvse_loader), (NVSE_DLL, g.nvse_dll)) if not ok]
        g.problems.append(f"xNVSE is not installed ({', '.join(missing)} missing). Install xNVSE from {LINK_NVSE}, "
                          "unpack it into the game folder, then run this again.")
    else:
        g.nvse_version = fnv_pe.file_version(p / NVSE_DLL)
        t = nvse_triple(g.nvse_version)
        if t is None:
            g.warnings.append(f"Could not read the xNVSE version; co-op needs xNVSE {'.'.join(map(str, MIN_NVSE))} or newer.")
        elif t < MIN_NVSE and not fnv_mut.on("nvse_no_min"):
            g.overridable.append(f"xNVSE {'.'.join(map(str, t))} is too old, co-op needs {'.'.join(map(str, MIN_NVSE))} or newer. "
                              f"Get the latest from {LINK_NVSE}.")
    if edition == "steam" and not (p / "nvse_steam_loader.dll").is_file() and g.nvse_loader:
        g.warnings.append("nvse_steam_loader.dll is missing; the Steam edition of xNVSE needs it next to nvse_loader.exe.")
    return g


def game_is_running(runner: Optional[Callable[[], str]] = None) -> bool:
    """True when FalloutNV.exe is in the Windows process list. The installer will not touch plugin files then."""
    if os.name != "nt" and runner is None:
        return False
    try:
        out = runner() if runner else subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {GAME_EXE}", "/FO", "CSV", "/NH"],
                                                     capture_output=True, text=True, errors="replace", timeout=15).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return GAME_EXE.lower() in out.lower()
