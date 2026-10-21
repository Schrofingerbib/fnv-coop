"""Mutation switches for the split-screen Python tools (the Python half of the Track B twins; the C++ half uses FNV_MUT 1..34).

A twin is the same code with one fault switched on; tools/splitscreen/tests/test_tools.py must fail on every twin. Production code asks
`split_mut.on("name")` where the fault would go. Nothing here reads the environment: only a test turns a switch on, in process, with
`split_mut.use("name")`, so a shipped launcher can never be put into a faulty mode by a stray variable.
"""
from contextlib import contextmanager

_active = set()


def on(name: str) -> bool:
    return name in _active


@contextmanager
def use(*names: str):
    before = set(_active)
    _active.update(names)
    try:
        yield
    finally:
        _active.clear()
        _active.update(before)
