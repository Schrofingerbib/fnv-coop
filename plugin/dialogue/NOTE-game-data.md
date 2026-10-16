# Game data left out of this folder

`scenarios/*.json`, `data/companion_topics.txt` and `tools/info_stats.*` are generated from the game's own
dialogue records and are not included. Regenerate them from your install after running
`python -I tools/questdata/run_all.py`: `tools/gen_m1_dialogue.py`, `tools/gen_companion_topics.py`,
`tools/info_stats.py`.
