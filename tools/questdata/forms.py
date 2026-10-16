"""Resolve a set of FormRefs ('Plugin.esm:LOCALID') to {sig, plugin, edid, name, base, script} by sweeping plugins."""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from esm import EsmFile  # noqa: E402

PLACED = {'REFR', 'ACHR', 'ACRE'}


def lookup(paths, refs):
    refs = set(refs)
    out = {}
    if not refs:
        return out
    for path in paths:
        e = EsmFile.open(path)
        try:
            ref = e.resolver()
            nm = e.name
            for rec in e.records():
                r = ref(rec.formid)
                if r not in refs or rec.deleted:
                    continue
                own = (rec.formid >> 24) >= len(e.masters)
                if r in out and not own:
                    continue
                o = {'sig': rec.sig, 'plugin': nm}
                for s, x in rec.subrecords():
                    if s == 'EDID':
                        o['edid'] = x.split(b'\0')[0].decode('cp1252')
                    elif s == 'FULL' and len(x) > 1 and rec.sig not in PLACED:
                        o['name'] = x.split(b'\0')[0].decode('cp1252', 'replace')
                    elif s == 'NAME' and rec.sig in PLACED and len(x) == 4:
                        o['base'] = ref(struct.unpack('<I', x)[0])
                    elif s == 'SCRI' and len(x) == 4:
                        o['script'] = ref(struct.unpack('<I', x)[0])
                out[r] = o
        finally:
            e.close()
    return out
