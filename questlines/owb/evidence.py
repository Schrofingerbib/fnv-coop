"""Evidence lookup: turn (unit id, source substring) into `unit:line` so prose never carries hand-typed line numbers."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import scan  # noqa: E402


class Evidence:
    def __init__(self, d):
        self.units = {}
        for u in scan.units(d):
            self.units[u['id']] = u['source'].replace('\r', '').split('\n')
        self.d = d

    def line(self, unit, needle, nth=1):
        """1-based raw line of the nth line of `unit` containing `needle` (case-insensitive, comments not stripped)."""
        n = 0
        for i, l in enumerate(self.units[unit], 1):
            if needle.lower() in l.lower():
                n += 1
                if n == nth:
                    return i
        raise KeyError('%s: %r not found (nth=%d)' % (unit, needle, nth))

    def ref(self, unit, needle, nth=1):
        return '%s:%d' % (unit, self.line(unit, needle, nth))

    def info_unit(self, local, which):
        return 'INFO %s %s' % (local.upper(), which)
