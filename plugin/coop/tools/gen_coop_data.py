#!/usr/bin/env python3
"""Generate the data files the coop module loads from the folder of nvcoop.dll:
  nvcoop_edids.json      {"VCG01": "falloutnv.esm:0xxxxx", ...}   quests (all scenarios) + every form with an EDID in forms.json (cells, ...)
  nvcoop_infoquest.json  {"<info ref>": "<quest ref>"}            INFOs that run a begin/end result script (cause.source -> owning quest)
  nvcoop_rules.json      copy of docs/quest-policies/rules.json
Refs are lower-cased `plugin:HEX` (6 digits), the same text form as nvhooks::refToStr. Output dir: out/L10/data (default).
    python -I plugin/coop/tools/gen_coop_data.py [--out DIR]
"""
import argparse, gzip, json, shutil
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
QD = REPO / "out" / "questdata"


def norm(ref):
    plugin, local = ref.rsplit(":", 1)
    return "%s:%06x" % (plugin.lower(), int(local, 16))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(REPO / "out" / "L10" / "data"))
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    edids = {}
    for p in (QD / "scenarios").glob("*.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if d.get("quest") and d.get("ref"):
            edids[d["quest"]] = norm(d["ref"])
    forms = json.load(gzip.open(QD / "forms.json.gz", "rt", encoding="utf-8"))
    for ref, v in forms.items():
        if v.get("edid"):
            edids.setdefault(v["edid"], norm(ref))
    info_quest = {}
    with gzip.open(QD / "infos.jsonl.gz", "rt", encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            if (d.get("begin") or d.get("end")) and d.get("quest"):
                info_quest[norm(d["ref"])] = norm(d["quest"])
    (out / "nvcoop_edids.json").write_text(json.dumps(edids, sort_keys=True))
    (out / "nvcoop_infoquest.json").write_text(json.dumps(info_quest, sort_keys=True))
    shutil.copy2(REPO / "docs" / "quest-policies" / "rules.json", out / "nvcoop_rules.json")
    print("edids=%d infoquest=%d -> %s" % (len(edids), len(info_quest), out))
    for k in ("VCG01", "VMS01", "GSDocMitchellHouse"):
        print(" ", k, edids.get(k))


if __name__ == "__main__":
    main()
