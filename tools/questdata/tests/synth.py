"""Builders for synthetic ESM bytes used by the unit tests."""
import os
import struct
import sys
import zlib

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))


def sub(sig, data=b''):
    return sig.encode('latin1') + struct.pack('<H', len(data)) + data


def sub_big(sig, data):
    return b'XXXX' + struct.pack('<HI', 4, len(data)) + sig.encode() + struct.pack('<H', 0) + data


def z(s):
    return s.encode('cp1252') + b'\0'


def rec(sig, formid, subs, flags=0):
    body = b''.join(subs)
    if flags & 0x40000:
        body = struct.pack('<I', len(body)) + zlib.compress(body)
    return sig.encode() + struct.pack('<IIIIHH', len(body), flags, formid, 0, 15, 0) + body


def grup(label, children, gtype=0):
    body = b''.join(children)
    lab = label.encode() if isinstance(label, str) else struct.pack('<I', label)
    return b'GRUP' + struct.pack('<I', 24 + len(body)) + lab + struct.pack('<IHHI', gtype, 0, 0, 0) + body


def tes4(masters=(), count=0):
    subs = [sub('HEDR', struct.pack('<fII', 1.34, count, 0x800))] + [sub('MAST', z(m)) + sub('DATA', b'\0' * 8) for m in masters]
    return rec('TES4', 0, subs, flags=1)


def schr(ref_count=0, size=0, nvars=0, typ=0):
    return sub('SCHR', struct.pack('<IIIIHH', 0, ref_count, size, nvars, typ, 0))


def script(src, refs=(), size=1, typ=0):
    return [schr(len(refs), size, 0, typ), sub('SCDA', b'\0' * size), sub('SCTX', src.encode('cp1252'))] + \
           [sub('SCRO', struct.pack('<I', r)) for r in refs]


def ctda(func, p1=0, p2=0, op=0, value=1.0, run_on=0, ref=0, flags=0):
    return sub('CTDA', struct.pack('<B3xfH2xIIII', (op << 5) | flags, value, func, p1, p2, run_on, ref))


def synth_quest(formid=0x00001000, edid='TESTQ', stage_src='SetObjectiveDisplayed TESTQ 10 1\nplayer.AddItem Caps001 100'):
    return rec('QUST', formid, [
        sub('EDID', z(edid)), sub('FULL', z('Test Quest')), sub('DATA', bytes([1, 50, 0, 0]) + struct.pack('<f', 5.0)),
        ctda(58, 0x1000, 0, op=2, value=0.0),
        sub('INDX', struct.pack('<h', 10)), sub('QSDT', b'\0'), sub('CNAM', z('Stage ten')), *script(stage_src),
        sub('INDX', struct.pack('<h', 20)), sub('QSDT', b'\1'), sub('CNAM', z('Done')),
        sub('QOBJ', struct.pack('<i', 10)), sub('NNAM', z('Do the thing')),
        sub('QSTA', struct.pack('<IB3x', 0x2000, 0)), ctda(59, 0x1000, 10),
    ], flags=0x40000)


def synth_file(masters=('Master.esm',)):
    """TES4 + QUST group (1 quest) + DIAL group (1 topic w/ 1 INFO). Returns bytes."""
    qgrp = grup('QUST', [synth_quest(0x01001000)])
    info = rec('INFO', 0x01003000, [
        sub('DATA', bytes([0, 0, 1, 0])), sub('QSTI', struct.pack('<I', 0x01001000)),
        sub('TRDT', struct.pack('<IiIB3xIB3x', 0, 50, 0, 1, 0, 0)), sub('NAM1', z('Hello courier')), sub('NAM2', z('')),
        ctda(72, 0, 0), *script('player.AddItem Caps001 5'), sub('NEXT'),
        *script('SetStage TESTQ 20\nDisablePlayerControls'),
    ], flags=0x40000)
    dial = rec('DIAL', 0x01002000, [sub('EDID', z('TESTTopic')), sub('QSTI', struct.pack('<I', 0x01001000)),
                                    sub('DATA', bytes([0, 0]))])
    dgrp = grup('DIAL', [dial, grup(0x01002000, [info], gtype=7)])
    body = qgrp + dgrp
    # HEDR count = records + groups (excluding TES4): quest 1 + dial 1 + info 1 + 3 groups
    return tes4(masters, count=6) + body
