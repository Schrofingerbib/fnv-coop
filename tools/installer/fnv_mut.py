"""Mutation switches for the release tools (installer, launcher, packager) and their tests.

A "twin" is the same code with one fault switched on; the test suite must fail on every twin. Production code asks
`fnv_mut.on("name")` at the place where the fault would go. Nothing here reads the environment or a file: only a test
turns a switch on, in process, with `fnv_mut.use("name")`. A shipped installer therefore can never be put into a faulty
mode by a stray environment variable.
"""
from contextlib import contextmanager

_active = set()


def on(name: str) -> bool:
    return name in _active


@contextmanager
def use(*names: str):
    """Switch the named faults on for the duration of the with block (tests only)."""
    before = set(_active)
    _active.update(names)
    try:
        yield
    finally:
        _active.clear()
        _active.update(before)
