# questlines

Generator and test scripts for each questline. The generated output (script inventories, gap reports, sweep
plans, dialogue sets, census files) is derived from Fallout: New Vegas game data and is **not** included.

Regenerate it from your own install:

```
python -I tools/questdata/run_all.py        # parse your .esm files into out/questdata
python -I questlines/<name>/build.py        # per-questline scripts (names vary; see each folder)
```

Scripts that need that data stop with a clear error when `out/questdata` is missing.
