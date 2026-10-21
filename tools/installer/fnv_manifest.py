"""The release manifest: which files a release holds, where each one is installed, and its SHA-256.

manifest.json (in the root of the release folder) is written by package_release.py and read by the installer. The installer
treats it as untrusted input, like the rest of the zip: every destination is checked to stay inside Data\\NVSE\\Plugins of the
game folder (a manifest that says "../../FalloutNV.exe" is refused), and every payload file is hashed before anything is
copied.
"""
import hashlib
import json
import posixpath
from pathlib import Path
from typing import Dict, List, Optional

import fnv_mut

SCHEMA = 1
DEST_PREFIX = "Data/NVSE/Plugins/"
PAYLOAD_DIR = "payload"
MANIFEST_NAME = "manifest.json"
SUMS_NAME = "SHA256SUMS.txt"


class ManifestError(ValueError):
    pass


def sha256_file(path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def safe_dest(dest: str) -> str:
    """Normalise a destination from a manifest and refuse anything that could leave Data/NVSE/Plugins."""
    if not isinstance(dest, str) or not dest:
        raise ManifestError("empty destination")
    d = dest.replace("\\", "/")
    if "\0" in d or d.startswith("/") or (len(d) > 1 and d[1] == ":") or d.startswith("//"):
        raise ManifestError(f"destination {dest!r} is not a relative path")
    if not fnv_mut.on("allow_traversal"):
        if any(part in ("..", "") for part in d.split("/")):
            raise ManifestError(f"destination {dest!r} contains '..' or an empty part")
        n = posixpath.normpath(d)
        if n != d:
            raise ManifestError(f"destination {dest!r} is not in normal form")
    if not fnv_mut.on("allow_any_dest"):
        if not d.lower().startswith(DEST_PREFIX.lower()) or "/" in d[len(DEST_PREFIX):] or len(d) == len(DEST_PREFIX):
            raise ManifestError(f"destination {dest!r} is not a file directly inside {DEST_PREFIX}")
    for part in d.split("/"):
        if not fnv_mut.on("allow_traversal") and part.rstrip(". ") != part:
            raise ManifestError(f"destination {dest!r} has a name Windows would change")
        if any(c in part for c in '<>:"|?*'):
            raise ManifestError(f"destination {dest!r} has a character that is not allowed in a Windows file name")
    return d


def load(release_root) -> dict:
    p = Path(release_root) / MANIFEST_NAME
    try:
        m = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ManifestError(f"cannot read {p}: {e}") from None
    if not isinstance(m, dict) or m.get("schema") != SCHEMA or not isinstance(m.get("files"), list) or not m["files"]:
        raise ManifestError(f"{p} is not a release manifest this installer understands")
    seen = set()
    for e in m["files"]:
        if not isinstance(e, dict) or not all(k in e for k in ("src", "dest", "sha256", "size")):
            raise ManifestError("a manifest entry lacks src, dest, sha256 or size")
        e["dest"] = safe_dest(e["dest"])
        src = e["src"].replace("\\", "/")
        if src.startswith("/") or ".." in src.split("/") or (len(src) > 1 and src[1] == ":"):
            raise ManifestError(f"source {e['src']!r} leaves the release folder")
        e["src"] = src
        if e["dest"].lower() in seen:
            raise ManifestError(f"destination {e['dest']} listed twice")
        seen.add(e["dest"].lower())
    return m


def verify_payload(release_root, manifest: dict) -> List[str]:
    """Problems with the payload files: missing, wrong size or wrong hash. Empty list = the payload is exactly what was built."""
    problems = []
    root = Path(release_root)
    for e in manifest["files"]:
        p = root / e["src"]
        if not p.is_file():
            problems.append(f"{e['src']}: missing")
            continue
        if p.stat().st_size != e["size"]:
            problems.append(f"{e['src']}: size {p.stat().st_size}, manifest says {e['size']}")
            continue
        if not fnv_mut.on("skip_payload_hash") and sha256_file(p) != e["sha256"]:
            problems.append(f"{e['src']}: SHA-256 differs from the manifest (damaged download?)")
    return problems


def sums_text(entries: List[Dict[str, str]]) -> str:
    """SHA256SUMS.txt in the format `sha256sum -c` reads: '<hash> *<path>' per line, sorted, LF endings."""
    return "".join(f"{e['sha256']} *{e['path']}\n" for e in sorted(entries, key=lambda x: x["path"]))


def check_sums(root, text: str) -> List[str]:
    """Verify a SHA256SUMS.txt against the files under root. Returns the problems."""
    problems = []
    for ln in text.splitlines():
        if not ln.strip():
            continue
        h, _, rel = ln.partition(" *")
        p = Path(root) / rel
        if not p.is_file():
            problems.append(f"{rel}: missing")
        elif sha256_file(p) != h:
            problems.append(f"{rel}: hash differs")
    return problems


def get_version(manifest: dict) -> str:
    return str(manifest.get("version", "?"))


def find_release_root(start: Optional[Path] = None) -> Optional[Path]:
    """The release folder that holds manifest.json: the tool's parent folder inside a release zip, else None (source checkout)."""
    here = Path(start or __file__).resolve().parent
    for cand in (here.parent, here):
        if (cand / MANIFEST_NAME).is_file():
            return cand
    return None
