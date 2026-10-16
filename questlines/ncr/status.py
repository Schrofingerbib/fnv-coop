"""internal questline notes writer (status summary with counts, top-10 blockers and the pasted test output).

python -I questlines/ncr/status.py     (normally through build_all.py --status)
Two passes: write the document with a placeholder, run run_tests.py, rewrite with the real output.
The ranking only lists blockers that are still open in the data (gaps.json is derived from the L5 code at build time).
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import common  # noqa: E402
import fntable  # noqa: E402

DOC = os.path.join(common.REPO, 'docs', 'questlines', 'ncr.md')


def load(n):
    with open(os.path.join(HERE, n), encoding='utf-8') as f:
        return json.load(f)


def cnt(gp, *fns):
    return sum(g['count'] for g in gp['gaps'] if g['fn'] in fns)


def counts_block(inv, sw, gp):
    s, sv = inv['summary'], sw['summary']
    sev = gp['summary']['by_severity']
    roles = s['by_role']
    rows = [
        ('Quests inventoried', f'{s["quests"]} ({roles.get("core", 0)} core, {roles.get("support", 0)} support, {roles.get("system", 0)} system containers, '
                               f'{roles.get("cross_lane", 0)} cross-lane, {roles.get("touched", 0)} touched)'),
        ('Stages in swept scope', str(s['stages'])),
        ('Stages swept (sweep.json)', f'{sv["stages"]} of {s["stages"]} ({sv["quests"]} quests)'),
        ('Stages explicitly excluded', f'{sv["excluded_stages"]} stages in {sv["excluded_quests"]} touched / cross-lane quests (each with a reason)'),
        ('Objectives (swept scope)', str(sv['objectives'])),
        ('Dialogue (core + support + system)', f'{s["infos"]} INFOs, {s["topics"]} topics'),
        ('Stages with conditional-branch setups', str(sv['stages_with_branches'])),
        ('Risky stages (run last / disposable save)', str(sv['risky_stages'])),
        ('Scripts scanned for gaps', f'{gp["summary"]["scripts_scanned"]} ({gp["summary"]["script_events"]} function/effect occurrences)'),
        ('Ambient occurrences dropped by L5 (no capture scope)', f'{gp["ambient"]["dropped_events"]} in {gp["ambient"]["scripts_with_dropped_events"]} scripts'),
        ('Gaps: blocker', f'{sev["blocker"]["functions"]} functions, {sev["blocker"]["occurrences"]} occurrences'),
        ('Gaps: high', f'{sev["high"]["functions"]} functions, {sev["high"]["occurrences"]} occurrences'),
        ('Gaps: medium', f'{sev["medium"]["functions"]} functions, {sev["medium"]["occurrences"]} occurrences'),
        ('Gaps: low', f'{sev["low"]["functions"]} functions, {sev["low"]["occurrences"]} occurrences'),
        ('Fully supported occurrences', str(gp['summary']['supported_occurrences'])),
    ]
    out = ['| Metric | Value |', '|---|---|'] + [f'| {a} | {b} |' for a, b in rows]
    return '\n'.join(out)


def candidates(gp):
    c = lambda *f: cnt(gp, *f)  # noqa: E731
    amb = gp['ambient']
    reads = sum(g['count'] for g in gp['gaps'] if g['kind'] == 'read')
    npc = sum(g['count'] for g in gp['gaps'] if 'npc-ai' in g['hazards'] or 'combat' in g['hazards'])
    return [
        ('Ambient scripts are not captured', amb['dropped_events'] > 0,
         f'L5 opens a capture scope only for stage and INFO result scripts. Quest GameMode blocks and object scripts run ambient and their mutations are dropped: {amb["dropped_events"]} occurrences in '
         f'{amb["scripts_with_dropped_events"]} scripts here - Moore\'s ready-check teleport/lock/autosave (`VHDColonelMoore`), the whole `VEnding` ending block, `vHDLegateBattleEndTrigger`, Lucky 38 doors, '
         '`VMQNCRFail` (`SetReputation ... 9999`), quest-script objective updates of VMQ05/VMS21/VMS45. Rules select by `cause.source` quest, so an object script needs an owner quest too.',
         'gaps.md "Ambient"; policy_review.md verdict 1'),
        ('Ending slides cannot replicate; NCR ending has no rule', c('swaptexture') > 0,
         f'`SwapTexture` x{c("swaptexture")} in `VEnding` (Effect exists in L4; no L5 hook or applier). rules.json has no `SlideshowEnd`/`VEnding` entry for the NCR ending '
         '(`ENDING_SLIDESHOW` lists only House, Independent, Legion and NVDLC02MQ06).', 'gaps.md `swaptexture`; policy_review.md verdict 2'),
        ('Barrier / PartyTether are per quest, not per stage', True,
         '`RulesEngine::onStageRequested` defers EVERY stage of a quest that carries either rule. The curated `vHDBattleController` tether (ODeck/PowerPlant) would also gate stage 20 (Legate camp) and 30 '
         '(Oliver greeting); a Barrier on `VMQ05` would hold stages 10-70. Patch adds a backward-compatible `stages` param. Also `FastTravelLock` is scene-bound while the battle lock is state-bound.',
         'policy_review.md verdict 3, section 2'),
        ('Point of no return is a message box', c('getbuttonpressed') > 0,
         f'Moore\'s "are you ready?" (`GetButtonPressed`, `VHDColonelMoore:126`, x{c("getbuttonpressed")} on the line) commits `VMQ05` stage 100 + the dam battle; Oliver\'s post-battle greeting commits the ending. '
         'The click is local, the script is host-side; no client prompt raises the ScenePass the Barrier waits for.', 'policy_review.md verdict 4; dialogue.md section 4'),
        ('Faction relations have no Effect', c('setenemy', 'setally', 'clearfactionplayerenemyflag') > 0,
         f'`SetEnemy/SetAlly/ClearFactionPlayerEnemyFlag` x{c("setenemy", "setally", "clearfactionplayerenemyflag")}: battle start sets PlayerFaction vs Legion/NCR, VMQNCRFail makes NCR hostile. '
         'Guests keep stale relations. (Membership/rank is captured now via `SetFactionRank`.)', 'gaps.md `setenemy`'),
        ('Party-wide fast-travel lock not wired', c('enablefasttravel') > 0,
         f'`EnableFastTravel` x{c("enablefasttravel")}: `vHDBattleController` calls it every frame while the battle runs (ambient) and `SetFastTravelLock` has no recorder or applier.',
         'gaps.md `enablefasttravel`'),
        ('Party-aware conditions are never evaluated', reads > 0,
         f'{reads} player-bound reads (GetDistance, GetInCell, GetItemCount, IsActionRef, GetPlayerTeammate, GetButtonPressed ...) are evaluated for the host player only: `RulesEngine::reduceCondition` exists but '
         'nothing in plugin/ calls it. Concrete damage: `VMS21` completes the Gomorrah boss-meeting objectives as soon as the HOST leaves `Gomorrah01`.', 'gaps.md; rules PartyAwareCond'),
        ('Spawns and NPC/object script variables', ('SpawnRef' not in fntable.APPLIED or 'RefScriptVar' not in fntable.RECORDED) and c('placeatme', 'placeleveledactoratme', 'set refvar') > 0,
         f'`PlaceAtMe/PlaceLeveledActorAtMe` x{c("placeatme", "placeleveledactoratme")} (Dam reinforcements, Kimball crowd, radio squads): `SpawnRef` is '
         f'{"recorded" if "SpawnRef" in fntable.RECORDED else "not recorded"} and {"applied" if "SpawnRef" in fntable.APPLIED else "NOT applied (Unsupported until a place-at-coordinates primitive is found)"}; '
         f'`set Ref.var` x{c("set refvar")}: `RefScriptVar` is {"recorded" if "RefScriptVar" in fntable.RECORDED else "not recorded"}.', 'gaps.md; internal lane notes round 9'),
        ('Objective hides cannot be represented', c('setobjectivedisplayed(0)') > 0,
         f'`SetObjectiveDisplayed ... 0` x{c("setobjectivedisplayed(0)")} (VMQ05 25->26, VMS21 stage 20 clears 15 objectives, VMS45). The hook records flags 0 but the L4 merge rule is OR, so hides return on resync / late join.',
         'gaps.md `setobjectivedisplayed(0)`; shadow.h'),
        ('Script-driven scenes and grief paths', c('sayto', 'say') > 0,
         f'`SayTo/Say` x{c("sayto", "say")} (Moore speech chain, Karl ambush, Cachino/Big Sal/Nero) have no initiator and `ForcedDialogue` has no hook; about {npc} NPC AI/combat commands rely on NV:MP '
         '(all 26 objective-target actors and 108 script-subject lines ARE in hostowned.json). One player killing Moore/Crocker (`OnDeath` -> `VMQNCRFail.iObjTrack = 3`) fails the line for everyone (O1); '
         '"respawn at the last zone checkpoint" and "late joiners warp to the current zone" (main-ncr.md) have no rule kind and no code.', 'dialogue.md section 4; policy_review.md O1'),
    ]


def top10(gp):
    return [(t, d, w) for t, ok, d, w in candidates(gp) if ok][:10]


def landed():
    out = []
    for eff, what in (('SetPlayerControls', 'player-control locks (`Disable/EnablePlayerControls`)'), ('CompleteAllObjectives', '`CompleteAllObjectives`'),
                      ('SetFactionRank', 'faction membership/rank (`AddToFaction`, `SetFactionRank`)'), ('SetPerk', '`AddPerk/RemovePerk`'),
                      ('AddNote', 'Pipboy notes'), ('AddTopic', 'journal topics'), ('RefAddItem', 'container/NPC inventory'), ('RequestSave', 'scripted saves'),
                      ('SetWeather', 'weather / time scale')):
        if eff in fntable.RECORDED:
            out.append(what)
    return out


def render(inv, sw, gp, test_output, n_ops=0):
    L = ['# Questline NCR (Main: NCR through the Hoover Dam ending): status', '',
         'Lane Q-ncr. Everything here is game-free analysis over `out/questdata` and the current L4/L5 code; nothing was run in FalloutNV.exe. The L7 sweep runs `questlines/ncr/sweep.json` next; '
         'the two-game test proves it. Files: `questlines/ncr/` (`inventory.json`, `sweep.json`, `gaps.json`, `gaps.md`, `policy_review.md`, `rules.patch.json`, `dialogue.md`, '
         '`dialogue_specs.json`, generators + `run_tests.py`). Regenerate after hook changes: `python -I questlines/ncr/build_all.py --status` (capture/apply statuses are derived from `plugin/hooks/*.cpp`).', '',
         '## Counts', '', counts_block(inv, sw, gp), '',
         '## Scope in one paragraph', '',
         'Core (swept): `VMS45`, `VMQNCRFail`, `VMS21` (the three script-graph-derived prerequisites), `VMQ05`, `vHDBattleController` (Hoover Dam for NCR), `VMQ06` (an unimplemented stub), '
         'and the epilogue quests `VEFR00/01/02NCR*`. Support (swept): `VMS21a`. 20 stage-less containers hold the dialogue/ending. The `VMQ03a`/`VMQ04`/`VMQTops` prerequisites belong to Q-act2 '
         'and the prologue to Q-prologue (listed as cross-lane, excluded with a reason). Hand check of the `*` critical-path nodes: `VMS45` is an optional branch (the Khans can also be killed), '
         '`VMS21` is a required step of VMQ05 objective 30, `VMQNCRFail` is an anti-dependency (a failure tracker: the NCR ending test must assert it did NOT fire).', '',
         '## TOP 10 blockers for co-op in this questline (ranked, still open at build time)', '', '| # | Blocker | Detail | Where |', '|---|---|---|---|']
    for i, (t, d, w) in enumerate(top10(gp), 1):
        L.append(f'| {i} | **{t}** | {d} | {w} |')
    ld = landed()
    L += ['', '## Closed by L4/L5 while this lane was being written', '',
          ('Recorded by `plugin/hooks` at build time: ' + ', '.join(ld) + '. Status is "captured + applied in code"; none of it is proven live on this questline yet.') if ld else '(none)', '',
          '## Findings that change existing policy (details in `policy_review.md`)', '',
          '- rules.json has no NCR-ending rule; `VMQNCRFail` carries five misattributed rules (one tethers to an empty cell list); curated `VMQ05` omits the MOVE/CTRL lines `main-ncr.md` itself cites.',
          '- Barrier/PartyTether gate every stage of a quest: the curated vHDBC tether would gate the Legate and ending stages (stage-scoping proposed).',
          f'- {n_ops} rule ops proposed in `rules.patch.json` with the 16 existing kinds; three backward-compatible param extensions (`stages`, `from_stage/until_stage`, `factions`), no new kind; 4 owner questions (O1-O4).',
          '- All 26 objective-target actors and all 108 actor-subject lines in the line\'s stage/quest scripts are already in `out/coverage/hostowned.json`.', '',
          '## Unknown / unverified (stated, not guessed)', '',
          '- Whether a Moore/Hoover Dam tether cell is `HooverDam` (copied from the curated VMQ03a rule, not read from placement data), and which interiors Oliver/the Legate stand in.',
          '- Whether Moore\'s next-topic gating (which topic appears after a stage) is stage-based or lives in the DIAL link graph + `VFreeformHooverDam` variables (INFO conditions show only part of it).',
          '- Whether guests\' ghosts fire the zone triggers that feed `iCurrentZone` / `iMaxZone` (NV:MP behaviour).',
          '- The L5 statuses come from reading the code, not from a live run on this questline.',
          '- Mappings marked UNVERIFIED in `fntable.py` (`ShowMap` -> `SetMapMarker`, `SetGameHour` -> `AdvanceTime`).', '',
          '## Open scenario work (not done here)', '',
          '- Dialogue specs for the Kimball assassination branch, Liza O\'Malley (Embassy), the Brotherhood talks and the Khans persuasion chain (greeting selection is not derivable from the data).',
          '- L7 must produce the NCR checkpoint saves (Moore\'s office, `VMQ05` stage 10; party of 3) referenced as `ncr_checkpoint_pending`.',
          '- The sweep drives `setstage`; the natural paths (dialogue, GameMode scripts, object scripts) are covered only by the dialogue specs and the `quest_script_branches` setups.', '',
          '## Test output (`python -I questlines/ncr/run_tests.py`)', '', '```', test_output.rstrip(), '```', '']
    return '\n'.join(L)


def run_tests():
    p = subprocess.run([sys.executable, '-I', os.path.join(HERE, 'run_tests.py')], capture_output=True, text=True, cwd=common.REPO)
    return p.returncode, (p.stdout + p.stderr)


def main():
    inv, sw, gp = load('inventory.json'), load('sweep.json'), load('gaps.json')
    n_ops = len(load('rules.patch.json')['ops'])
    os.makedirs(os.path.dirname(DOC), exist_ok=True)
    doc = render(inv, sw, gp, 'ALL OK (placeholder for the first pass; replaced below)', n_ops)
    with open(DOC, 'w', encoding='utf-8', newline='\n') as f:
        f.write(doc)
    rc, out = run_tests()
    doc = render(inv, sw, gp, out, n_ops)
    with open(DOC, 'w', encoding='utf-8', newline='\n') as f:
        f.write(doc)
    print(out)
    print(f'wrote {DOC} (tests rc={rc})')
    return rc


if __name__ == '__main__':
    sys.exit(main())
