"""One-command test run: python tools/questdata/run_tests.py  (stdlib unittest; no pytest needed)."""
import os
import sys
import unittest

here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, here)
sys.path.insert(0, os.path.join(here, 'tests'))
suite = unittest.defaultTestLoader.discover(os.path.join(here, 'tests'), pattern='test_*.py')
res = unittest.TextTestRunner(verbosity=2).run(suite)
sys.exit(0 if res.wasSuccessful() else 1)
