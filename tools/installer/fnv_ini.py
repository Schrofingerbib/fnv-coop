"""Small, careful INI editing shared by the installer and the launcher.

nvcoop.ini is read by the plugin with GetPrivateProfileStringA, so it is written in the ANSI code page. Every value is
checked before it is written: a value with a line break would otherwise add keys of its own (a player name of
"x<newline>[coop]<newline>role=host" must not change the role). Writes are atomic (temp file, then replace), so a crash
cannot leave half an ini.
"""
import locale
import os
import re
import time
from typing import Dict, Optional

import fnv_mut


def ini_encoding() -> str:
    getencoding = getattr(locale, "getencoding", None)   # Python 3.11+: the ANSI code page even in UTF-8 mode
    enc = getencoding() if getencoding else locale.getpreferredencoding(False)
    return enc if enc and enc.lower() not in ("utf-8", "utf8", "ascii", "ansi_x3.4-1968") else "cp1252"


class IniError(ValueError):
    pass


def check_value(section: str, key: str, value: str) -> str:
    """Return the value to write, or raise IniError. No control characters (a line break would add keys), no section brackets
    at the start of a name, and it must be encodable in the ANSI code page the plugin reads."""
    for what, s in (("section", section), ("key", key)):
        if not s or any(c in s for c in "[]=\r\n\0;") or s != s.strip():
            raise IniError(f"bad ini {what} {s!r}")
    if not fnv_mut.on("ini_allow_newline"):
        if any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise IniError(f"[{section}] {key}: a value cannot contain control characters or line breaks")
    try:
        value.encode(ini_encoding())
    except UnicodeEncodeError:
        raise IniError(f"[{section}] {key}: {value!r} has characters the game's ini code page ({ini_encoding()}) cannot hold") from None
    return value.strip()


def read_text(path) -> str:
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except FileNotFoundError:
        return ""
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    return raw.decode(ini_encoding(), errors="replace")


_SEC = re.compile(r"^\s*\[(.+?)\]\s*$")


def get(text: str, section: str, key: str) -> Optional[str]:
    cur = None
    found = None
    kre = re.compile(r"^\s*" + re.escape(key) + r"\s*=\s*(.*?)\s*$", re.I)
    for ln in text.splitlines():
        m = _SEC.match(ln)
        if m:
            cur = m.group(1)
            continue
        if cur is not None and cur.lower() == section.lower():
            m = kre.match(ln)
            if m:
                found = m.group(1)   # Windows returns the first match; the last one wins here only if the first is absent
                return found
    return found


def set_value(text: str, section: str, key: str, value: str) -> str:
    """Set key in [section] (names are case-insensitive), keeping every other line, comment and the line ending."""
    value = check_value(section, key, value)
    nl = "\r\n" if "\r\n" in text else "\n"
    lines = text.split(nl) if text else []
    key_re = re.compile(r"^\s*" + re.escape(key) + r"\s*=", re.I)
    cur = None
    sec_end = None
    for idx, ln in enumerate(lines):
        m = _SEC.match(ln)
        if m:
            if cur is not None and cur.lower() == section.lower():
                break
            cur = m.group(1)
            if cur.lower() == section.lower():
                sec_end = idx + 1
            continue
        if cur is not None and cur.lower() == section.lower():
            if key_re.match(ln):
                lines[idx] = f"{key}={value}"
                return nl.join(lines)
            if ln.strip():
                sec_end = idx + 1
    if sec_end is None:
        while lines and not lines[-1].strip():
            lines.pop()
        if lines:
            lines.append("")
        lines += [f"[{section}]", f"{key}={value}", ""]
    else:
        lines.insert(sec_end, f"{key}={value}")
    return nl.join(lines)


def apply(text: str, values: Dict[str, Dict[str, str]]) -> str:
    for sec, kv in values.items():
        for k, v in kv.items():
            text = set_value(text, sec, k, v)
    return text


def replace_retry(src, dst, tries: int = 6) -> None:
    """os.replace that waits and tries again when Windows says the file is busy (a virus scanner or indexer holds it for a moment)."""
    for i in range(tries):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if i == tries - 1:
                raise
            time.sleep(0.25 * (i + 1))


def write_atomic(path, text: str) -> None:
    """Write text in the ANSI code page: temp file next to it, then replace."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = str(path) + ".fnvcoop-tmp"
    with open(tmp, "wb") as f:
        f.write(text.encode(ini_encoding(), errors="strict"))
        f.flush()
        os.fsync(f.fileno())
    replace_retry(tmp, path)
