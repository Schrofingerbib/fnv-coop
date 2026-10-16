#!/bin/sh
# usage: run_slot.sh script1.py [script2.py ...]   -- acquires ONE game slot (shared with other lanes), runs the scripts against the instance
# that slot maps to (FNV_INSTANCE=n parsed from `SLOTS n`), registers/kills only our own pids (the scripts do), releases the slot.
cd "$(dirname "$0")/../../.." || exit 1
export FNV_LANE=L5
OUT=$(py -3 -I tools/gameslot.py acquire --owner L5 --slots 1 --ttl 1500 --wait 3600 2>&1)
echo "$OUT"
N=$(echo "$OUT" | sed -n 's/.*SLOTS \([0-9]\).*/\1/p' | tail -1)
[ -z "$N" ] && { echo "no slot"; exit 3; }
export FNV_INSTANCE=$N
for s in "$@"; do
  echo "=== $s (instance $N)"
  py -3 -u -I -X utf8 "plugin/hooks/tools/$s"
  echo "exit($s)=$?"
done
py -3 -I tools/gameslot.py release --owner L5
