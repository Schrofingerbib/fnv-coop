"""Writes internal questline notes (status summary) from the generated artifacts and a fresh test run.

    python -I questlines/honesthearts/make_status.py
"""
import collections
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import qlib  # noqa: E402

OUT = os.path.join(qlib.REPO, 'docs', 'questlines', 'honesthearts.md')


def main():
    r = subprocess.run([sys.executable, '-I', os.path.join(HERE, 'run_tests.py')], capture_output=True, text=True, encoding='utf-8')
    test_out = (r.stdout + r.stderr).rstrip()
    inv = qlib.jload(os.path.join(HERE, 'inventory.json'))
    sw = qlib.jload(os.path.join(HERE, 'sweep.json'))
    g = qlib.jload(os.path.join(HERE, 'gaps.json'))
    rp = qlib.jload(os.path.join(HERE, 'rules.patch.json'))
    bj = qlib.jload(os.path.join(HERE, 'blockers.json'))
    ds = qlib.jload(os.path.join(HERE, 'dialogue_specs.json'))
    c = inv['counts']
    gc = g['counts']
    ops = collections.Counter(p['op'] for p in rp['patches'])
    L = []
    L.append('# Questline status: Honest Hearts DLC (`honesthearts`)')
    L.append('')
    L.append('Agent Q-honesthearts. HonestHearts.esm, load-order prefix 02 (`tools/questdata/loadorder.json`). Policy file: `docs/quest-policies/dlc-honest-hearts.md`. '
             'Everything here is game-free: derived from `out/questdata`, `docs/quest-policies`, `synccore` and `plugin/hooks` sources. Nothing was run in the game, the game was not launched, nothing was committed.')
    L.append('')
    L.append('Run everything: `python -I questlines/honesthearts/build.py` (regenerate) then `python -I questlines/honesthearts/run_tests.py` (prints `ALL OK`).')
    L.append('')
    L.append('## Counts')
    L.append('')
    L.append('| item | count |')
    L.append('|---|---:|')
    L.append('| quests in HonestHearts.esm | %d (%d with stages, %d without: dialogue / system quests) |' % (c['quests'], c['with_stages'], c['quests'] - c['with_stages']))
    L.append('| by role | %s |' % ', '.join('%s %d' % kv for kv in c['by_role'].items()))
    L.append('| by risk tier | %s |' % ', '.join('%s %d' % kv for kv in c['by_tier'].items()))
    L.append('| stages | %d |' % c['stages'])
    L.append('| stages swept (sweep.json) | %d of %d (0 exclusions; %d quests without stages listed with a reason) |' % (sw['summary']['stages'], c['stages'], sw['summary']['excluded_quests']))
    L.append('| objectives | %d |' % c['objectives'])
    L.append('| INFOs / topics owned by HH quests | %d / %d |' % (c['infos'], c['topics']))
    L.append('| script units scanned / calls / distinct functions | %d / %d / %d |' % (gc['units'], gc['calls'], gc['functions']))
    L.append('| functions by capture/apply status | %s |' % ', '.join('%s %d' % kv for kv in sorted(gc['by_status'].items())))
    L.append('| gaps by severity (functions) | %s |' % ', '.join('%s %d' % (s, gc['by_severity'].get(s, 0)) for s in ('blocker', 'high', 'medium', 'low', 'none')))
    L.append('| gaps by severity (call occurrences) | %s |' % ', '.join('%s %d' % (s, gc['occurrences_by_severity'].get(s, 0)) for s in ('blocker', 'high', 'medium', 'low', 'none')))
    L.append('| mutating calls outside a capture scope (ambient) | %d |' % gc['ambient_mutating_calls'])
    L.append('| rules.json quests (before / after patch) | %d / %d of %d |' % (sum(1 for q in inv['quests'] if q['rules_existing']),
                                                                         sum(1 for q in inv['quests'] if q['rules_existing']) + ops['add_quest'], c['quests']))
    L.append('| rules patch operations | %s |' % ', '.join('%s %d' % kv for kv in sorted(ops.items())))
    L.append('| proposed new reductions / kinds / params | %d (see `rules.patch.json` proposed[]) |' % len(rp['proposed']))
    L.append('| key conversations (dialogue.md / dialogue_specs.json) | %d |' % len(ds['specs']))
    L.append('')
    L.append('## Files (all in `questlines/honesthearts/`)')
    L.append('')
    L.append('| file | what |')
    L.append('|---|---|')
    for f, w in (('inventory.json', 'every quest: EDID, FormID (02......), stages, objectives, prerequisites (conditions, who triggers it, graph edges), dialogue counts, tier'),
                 ('sweep.json', 'm1_goodsprings.json-format sweep config for L7; `setup` / `pre` / `variants` console prerequisites, `reset_ids`, run-last order'),
                 ('gaps.json / gaps.md', 'every function in stage, topic, quest and object scripts mapped to a synccore Effect and its L5 capture/apply status, with script + line'),
                 ('rules.patch.json / policy_review.md', 'rules.json corrections and additions (16 existing kinds only) plus proposals; review against the owner decisions'),
                 ('dialogue.md / dialogue_specs.json', 'key conversations in L8 scenario style; conversations the initiator-picks model breaks'),
                 ('blockers.json', 'the ranked top 10 and the "also" list with resolved evidence'),
                 ('build.py, scan.py, fnmap.py, curated.py, qlib.py', 'generator, script scanner, function-to-Effect table, hand-written judgment (evidence-anchored), loaders'),
                 ('run_tests.py (+ make_status.py)', 'validators + mutation twins; make_status.py writes this file')):
        L.append('| %s | %s |' % (f, w))
    L.append('')
    L.append('## Top 10 blockers for co-op in this questline (ranked)')
    L.append('')
    for b in bj['blockers']:
        ev = '; '.join('%s line %d' % (e['where'], e['line']) if e['line'] else '%s (%s)' % (e['where'], e['text']) for e in b['evidence'])
        L.append('%d. **%s** [%s, lane %s] %s' % (b['rank'], b['title'], b['severity'], b['lane'], b['text']))
        L.append('   - evidence: %s' % ev)
        L.append('   - fix: %s' % b['fix'])
    L.append('')
    L.append('Also noted (below the top 10): ' + ' | '.join('%s [%s] %s' % (a['id'], a['severity'], a['title']) for a in bj['also']))
    L.append('')
    L.append('## The four things the lead asked about')
    L.append('')
    L.append('- **Caravan ambush entry**: B3 (host choreography, `IsKiller Player`, MQ00 255 fail path) and B1 (the ambush runs entirely in ambient scripts). Moment M-ambush in `policy_review.md`.')
    L.append('- **Inventory weight limit on entry**: B2. Jed INFOs 009EB6 (>= 76) / 011525 (>= 101) read `GetActorValue InventoryWeight` of the initiator; Survival 50 (009EB9), a perk (00A0FB) or Ricky Barter bribe (00F5E6 / 00F5FF / 00F5D8) raise the limit to 100; companions refuse departure (009EB4 / 009EB5). '
             'Spec `dlg_hh_jed_departure_gate`, `dlg_hh_ricky_weight_bribe`. The existing MQ00 PartyTether (cells = GSDocMitchellHouse) would defer every MQ00 stage and is replaced in the patch.')
    L.append('- **Joshua vs Daniel ending choice**: B5. Daniel INFOs 010718 / 010723 (`StartQuest NVDLC02MQ04`) vs 0093AA (`StartQuest NVDLC02MQ05`) after 0093AF; the Salt-Upon-Wounds outcome (iJoshuaSequence 8 / 9 / 10) and MQ06 (B10) then pick the ending slides. Proposed ChoiceConsent.')
    L.append('- **Follows-Chalk / Waking Cloud companions**: B4 + B8. HireTrigger / FireTrigger: SetPlayerTeammate, FollowerFaction, `Player.AddPerk` / `RemovePerk`, shared hired/fired flags in NVDLC02FollowersQuest; Waking Cloud hire fires Follows-Chalk. Specs `dlg_hh_followschalk_hire_fire`, `dlg_hh_waking_cloud_hire_fire`, `dlg_hh_joshua_hire_fire`.')
    L.append('')
    L.append('## For other lanes')
    L.append('')
    L.append('- **L7 sweep**: `goodsprings_sweep.py` builds reset ids with `"%X" % local_id`, which drops the load-order prefix. Use `sweep.json["reset_ids"]` (8 digits, prefix 02) for HonestHearts quests. `setup` / `pre` / `variants` are meant to run before `capture.begin`; the `set <questId>.<var> to <n>` console syntax with a hex id is unverified. `order` puts MQ00 (teleport + control lock) last. No Honest Hearts start save exists yet.')
    L.append('- **L9**: apply `rules.patch.json` (replace_params / add_rule / add_quest). It removes the MQ00 GSDocMitchellHouse tether, fills the empty PartyWarp / PartyTether params, and adds rules for the %d quests that carry slideshows, timers, dialogue and companions.' % ops['add_quest'])
    L.append('- **L5**: statuses in `gaps.json` are source-level. B1 (ambient scope), B4 / B8 (companion, faction, perk, lock, spawn capture) and the unhooked control / fast-travel / autosave commands are the work items.')
    L.append('- **L4**: needs the proposed `sum-over-party` reduction, an anchor/radius param for open-world tethers, and ChoiceConsent / FriendlyFireGuard if the owner agrees.')
    L.append('- **L8**: %d conversation specs (`save: null`, run_mode `alternatives-from-same-save` where the choices are mutually exclusive).' % len(ds['specs']))
    L.append('')
    L.append('## Unknown / not verified (honest list)')
    L.append('')
    L.append('- Every capture/apply status comes from reading `plugin/hooks` and `internal lane notes`, not from a run. In particular the claim that GameMode / trigger blocks mutate outside a capture scope rests on `capture_hub.h` and the L5 notes.')
    L.append('- Whether NV:MP actor sync already replicates Enable / Disable / Kill / Resurrect of persistent White Legs and caravan actors (L6): unknown; the story and player part of B1 does not depend on it.')
    L.append('- Mapping of quest-variable index to name in the Ending INFO conditions (MQ04 var #4, MQ05 var #5): not resolved; only the numbers are quoted.')
    L.append('- Console syntax `set <hexQuestId>.<var> to <n>` and whether `startquest` is needed before `setstage` in the sweep instance: unverified.')
    L.append('- The owner decisions still open: companions across players, whether Jed pays once per party, how the fork is decided, friendly fire (see `policy_review.md` section 6).')
    L.append('')
    L.append('## Test output')
    L.append('')
    L.append('`python -I questlines/honesthearts/run_tests.py` (exit code %d):' % r.returncode)
    L.append('')
    L.append('```')
    L.append(test_out)
    L.append('```')
    L.append('')
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(L))
    print('wrote', OUT, 'tests exit', r.returncode)
    return r.returncode


if __name__ == '__main__':
    sys.exit(main())
