# Quest policies

`rules.json` (plus `rules.base.json`, `rules.pending.json`, `rules.provenance.json`) is our own per-quest co-op
rule data, keyed by quest EditorID: party tethers, shared karma and reputation, rewards to every player,
initiator-picks dialogue. See `tools/coverage/rules.py` for the rule vocabulary.

The per-questline policy documents and `policies.json` quote Bethesda script lines, so they are not included.
Regenerate them from your own game data with `python -I tools/coverage/build.py`.
