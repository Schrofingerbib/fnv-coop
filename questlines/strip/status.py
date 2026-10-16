"""Writes internal questline notes (the only file outside questlines/strip/ this lane may write).
Run:  python -I questlines/strip/status.py      (rebuilds artifacts, runs run_tests.py, pastes the final test output)
"""
import json
import os
import subprocess
import sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..'))
DOC = os.path.join(REPO, 'docs', 'questlines', 'strip.md')
sys.path.insert(0, HERE)
import build as build_mod  # noqa: E402
import scope  # noqa: E402


def run_tests(skip_status):
    env = dict(os.environ)
    if skip_status:
        env['QSTRIP_SKIP_STATUS'] = '1'
    r = subprocess.run([sys.executable, '-I', os.path.join(HERE, 'run_tests.py')], capture_output=True, text=True, env=env, cwd=REPO)
    return r.returncode, r.stdout + r.stderr


def render(out, test_output):
    inv, sw, g, f, patch, dl = out['inventory'], out['sweep'], out['gaps'], out['facts'], out['patch'], out['dialogue']
    c = inv['counts']
    stages_swept = sum(1 for q in sw['quests'] for s in q['stages'] if s['sweep'])
    stages_with_setup = sum(1 for q in sw['quests'] for s in q['stages'] if s['setup'])
    sev = g['unsupported_by_severity']
    funcs_by_sev = {}
    for r in g['functions']:
        if r['gap']:
            funcs_by_sev[r['sev']] = funcs_by_sev.get(r['sev'], 0) + 1
    rows = {r['fn']: r for r in g['functions']}

    def n(fn):
        return rows[fn]['count'] if fn in rows else 0
    blocks = f['player_blocks']
    L = []
    A = L.append
    A('# Questline Q-strip: Freeside / Strip (status)')
    A('')
    A('Owner: questline agent Q-strip. Output dir `questlines/strip/`. Everything below is static analysis of `out/questdata` plus the L4/L5 sources; '
      'nothing was run in the game (the brief forbids it), so every "breaks" statement is "breaks according to the documents/code", flagged unverified in game.')
    A('')
    A('Baseline: the capture/apply columns describe `plugin/hooks/engine_hooks.cpp`, `apply_remote.cpp`, `engine_addrs.h`, `synccore/include/fnvcoop/effect.h` and '
      '`docs/protocol.md` section 12 as they were when this was built (2026-10-16; L5 added the "census backlog" hooks while this lane was running and the table was re-baselined). '
      '`run_tests.py` re-checks the table against those files and FAILS when they drift, so re-run `python -I questlines/strip/build.py` after L5/L10 change them.')
    A('')
    A('## Counts')
    A('')
    A(f'- quests: **{c["quests"]}** ({c["core"]} core = the 19 in `side-freeside-strip.md`, {c["extended"]} extended, {c["system"]} system/dialogue/casino)')
    A(f'- stages: **{c["stages"]}**, objectives: {c["objectives"]}, INFOs owned by these quests: {c["own_infos"]}; INFOs in dialogue scope (incl. container INFOs that drive them): {dl["infos_in_scope"]}')
    A(f'- stages swept: **{stages_swept} of {c["stages"]}** (sweep.json; {stages_with_setup} carry a heuristic `setup`, the rest need only `setstage`); '
      f'{len(sw["exclusions"])} quests have no stages and are excluded with a reason (containers/scripts only)')
    A(f'- scripts analysed: {g["scope"]["scripts"]} ({", ".join(f"{k} {v}" for k, v in sorted(g["scope"]["by_kind"].items()))}); statements classified: {g["scope"]["statements"]}')
    A(f'- gaps (capture/apply partial or missing): **{g["unsupported_functions"]} functions, {g["unsupported_occurrences"]} occurrences**; by severity (occurrences / functions): ' +
      ', '.join(f'{s} {sev.get(s, 0)} / {funcs_by_sev.get(s, 0)}' for s in ('blocker', 'high', 'medium', 'low', 'info')))
    A(f'- statuses (occurrences): ' + ', '.join(f'{k} {v}' for k, v in sorted(g['status_occurrences'].items())))
    A(f'- policy: {len(patch["amend"])} existing quests amended, {len(patch["new_quests"])} quests newly covered, {len(patch["proposals"]["kinds"])} new kind proposed (`PerPlayerVars`), '
      f'{len(patch["proposals"]["engine_changes"])} engine/contract changes (E1-E9); dialogue: {dl["key_infos"]} key INFOs, {len(out["specs"])} L8-style specs')
    A('')
    A('## Deliverables (all in `questlines/strip/`)')
    A('')
    A('| file | what |')
    A('|---|---|')
    for fn, what in [('inventory.json', 'every quest: ids, stages, objectives, prerequisites (conditions + questgraph), dialogue counts, tier, current rules'),
                     ('sweep.json', 'm1_goodsprings.json format for L7 + per-quest/per-stage `setup`, `order_last`, risk flags, oracle summary, exclusions'),
                     ('gaps.json / gaps.md', 'every command/read/var-write -> Effect + capture/apply status, hazards, exact locations (script EDID + line)'),
                     ('policy_review.md', 'findings F1-F12, barriers/tethers/cinematics/reward routing, per-quest table, proposals'),
                     ('rules.patch.json', 'amend/new_quests patch over docs/quest-policies/rules.json (16 kinds only) + proposals + volatile keys + blocked params'),
                     ('dialogue.md / dialogue.json / dialogue_specs.json', 'key conversations, where initiator-picks breaks, L8-style specs'),
                     ('run_tests.py', '`python -I questlines/strip/run_tests.py` -> ALL OK (real checks + mutation twin)'),
                     ('build.py / status.py', 'regenerate artifacts / this status doc')]:
        A(f'| `{fn}` | {what} |')
    A('')
    A('Use by L7: point `load_plan()` at `questlines/strip/sweep.json` (same fields as m1: `quests[].edid/ref/stages[].index/tier_direct`); run quests with '
      '`order_last: true` last; `stages[].command` is the exact console line (hex FormID); `setup` lines are optional pre-steps (all lower-case FNV console commands, '
      'hex ids, validated against quests.json); `unresolved` lists quest-variable gates that no console line was guessed for.')
    A('')
    A('## TOP 10 blockers for co-op in this questline (ranked)')
    A('')
    A(f'1. **Forwarded guest activation still acts on the host player** (E4). {len(f["activate_scripts"])} object/NPC scripts mutate shared state from `OnActivate` (+{len(f["trigger_scripts"])} from `OnTriggerEnter`): Lucky 38 elevators ({f["lucky38_moves"]} `player.MoveTo`), the AutoDoc that heals or kills Caesar, the Fort Howitzer that completes VMS32, terminals, Gomorrah/Tops doors, frisk triggers. '
      'L10 v1 (protocol section 12) forwards the activation and re-runs it with the HOST player as stand-in; the acting-player resolution (L5) and ghost refID (L6) are still open, and TriggerEnter/pickup forwarding is not done. Progress of vfreeformlucky38, VMS32, VMS35, VMS21 depends on it.')
    A(f'2. **Message-box choices open on the host screen** (E5). {len(f["button_scripts"])} scripts read `GetButtonPressed` after `ShowMessage` ({n("showmessage")} ShowMessage calls in scope); irreversible branches (Caesar surgery, VMS42 explosives, VMS30 troopers, VMS32 howitzer) can only be answered by the host. The coordinator decision covers Fort -> Dam only.')
    A(f'3. **Dialogue payments charge and serve every player** (E2 + `RemoveItemPerOwner` on the containers). {f["payments"]["total"]} INFOs pay caps/chips (VFreeformFreeside {f["payments"]["by_container"].get("VFreeformFreeside", 0)}, VMS38 {f["payments"]["by_container"].get("VMS38", 0)}, '
      f'clinic {f["payments"]["by_container"].get("vDialogueMedicalClinic", 0)}, cashier {f["payments"]["by_container"].get("vDialogueCasinoCashier", 0)}); a DialogueResult RemoveItem/AddItem/SetPerk defaults to ALL players (`policy.cpp`). Rules are keyed on the INFO-owning container, which has none today.')
    A('4. **The rules engine cannot yet carry the fixes** (E1, E7). `PerPlayerScene` has no code path, six `PartyTether` rules have empty `cells` (inert), 11 `PartyWarp` marker EDIDs in rules.json do not resolve in forms.json.gz, and `RemoveItemPerOwner` cannot route the matching `AddItem`.')
    A(f'5. **Player-filtered event blocks only see the host player** (E8). OnStartCombat {blocks.get("onstartcombat", 0)}, OnHit {blocks.get("onhit", 0)}, OnDeath {blocks.get("ondeath", 0)}'
      ': Pacer\'s death turns the Kings hostile, Julie\'s death turns the Followers hostile, Janet / Weathers / injured troopers fail or flag quests. A guest kill or hit is invisible.')
    A(f'6. **Casino state is per player but stored in shared variables, the games are engine-local, the frisk lockers are shared** (`PerPlayerVars`, E9). {n("read:getcasinowinningslevel")} `GetCasinoWinningsLevel` reads, {n("showblackjackmenuparams") + n("showroulettemenuparams") + n("showslotmachinemenuparams")} game-menu calls, '
      f'bans/comps in `Banned/SmallFry/BigWinner/Whale`, frisk and Gomorrah entry state (`iInGomorrah`, `bGunsAllowed`), {n("removealltypeditems") + n("removealltypeditems@npc")} `RemoveAllTypedItems` (no hook) and {n("removeallitems")} `RemoveAllItems` transfers into single shared lockers.')
    A(f'7. **Companions and scripted dialogue have no capture** (E6). `SetPlayerTeammate` x{n("setplayerteammate")} (Lily, Arcade, dismissals; CompanionOwner), NPC-subject `AddPerk CompanionSuite`, `StartConversation Player` x{n("startconversation")}, `SayTo` x{n("sayto")}, `Say` x{n("say")}: the Effects `CompanionOwner` / `ForcedDialogue` exist with no hook and no applier; the host player is dragged into forced conversations.')
    A(f'8. **Treatment scenes: heal/restore uncaptured, follow-up effects carry origin=host** (E3, E6). `ResetHealth`/`RestoreAV`/`RestoreActorValue` x{n("resethealth") + n("restoreav") + n("restoreactorvalue")} are not hooked; the doctor template (Julie, Alex Richards, Markland, Usanagi) runs on the host after the dialogue closed. '
      f'Control locks ARE captured now (Player::SetControlsDisabled, {n("disableplayercontrols")}/{n("enableplayercontrols")} uses) but the derived rules locked the whole party (fixed in the patch).')
    A(f'9. **Hostility and crime state follow the host only** (E6). `StartCombat` x{n("startcombat")} ({rows["startcombat"]["player_targeted"]} target the player; NV:MP actor sync assumed, unproven), `SendAssaultAlarm`/`SendTrespassAlarm` x{n("sendassaultalarm") + n("sendtrespassalarm")}, `SetPCEnemyOfFaction`/`ClearFactionPlayerEnemyFlag` x{n("setpcenemyoffaction") + n("clearfactionplayerenemyflag")}, `RemoveFromAllFactions` on NPCs x{n("removefromallfactions@npc")}.')
    A(f'10. **Remaining uncaptured world state.** Ref-script variables `Set Ref.var` x{n("set-refvar")} (RefScriptVar exists, nothing records it), `MarkForDelete` x{n("markfordelete")}, script `Activate` x{n("activate") + n("activate@player")}, `EnableFastTravel` x{n("enablefasttravel")}, `SwapTexture` x{n("swaptexture")}, `ShowMap` x{n("showmap")}; `PlaceAtMe` x{n("placeatme")} is captured (SpawnRef) but RemoteApplier has no apply path.')
    A('')
    A('## Findings that changed existing policy (see `policy_review.md`)')
    A('')
    A('- Derived rules over-applied the endgame scripts of Colonel Moore / Fort Lucius / Fort Caesar (message-box confirmation, SystemSave, MoveTo Oliver / Legate camp) to VMS29, VMS32, VMS35, VMS45; removed.')
    A('- The doctor template got a party-wide control lock plus inert tether on VMS24, VMS47, VFSHighTimes, VMS31, VMS36; replaced by `PerPlayerScene`.')
    A('- `vfreeformlucky38` had `PartyWarp` on elevators, which would drag the party on every ride; replaced by `PerPlayerScene`.')
    A(f'- {len(patch["new_quests"])} quests with player-affecting scripts or dialogue had no rules at all (dialogue containers, casino comps, security, clinic, cashier, companions); now covered.')
    A('')
    A('## Open questions for the coordinator / owner')
    A('')
    A('1. Casino ledger: per player (recommended) or shared Courier ledger. 2. Are guest choices in host-affecting message boxes allowed, or host/vote only? 3. Companion ownership (already open).')
    A('4. `rules.json` files VMS21/VMS21a under Boulder City although they are Gomorrah/Omertas; this lane reviewed them (nobody else is assigned Boulder City / Novac / Goodsprings-other side quests).')
    A('5. Unassigned orphans I noticed but did not take: VMS12, VMS22, VMS26, VMS37, VMS43, VMS44, VMS46, VMS57 (no questline in rules.json, no agent).')
    A('')
    A('## Cross-lane notes')
    A('')
    A('- Q-systems: all doctors step the shared `VDoctors` quest (stage 10/20) from `VCFHAlexRichards`, `VBSLtMarkland`, Julie, Usanagi scripts; a per-patient flow through one shared quest.')
    A('- Q-ncr / Q-legion: Colonel Moore, Fort Lucius and Fort Caesar run the endgame confirmation (SystemSave, GetButtonPressed, MoveTo, DisablePlayerControls) in GameMode; this lane only reaches it through shared NPCs.')
    A('- Q-act2: VMS29a (Birds of a Feather), VMS18 (Beyond the Beef), VMQTops, VDialogueVegasNorth, VDialogueLucky38Entrance are cross-lane references (listed in inventory.json `cross_lane`).')
    A('- L3: forms.json.gz needs the cells/markers listed in `rules.patch.json` `blocked_params` (E7). L4: E1, E2, E3, E9. L5: E4, E5, E6, E8. L6: ghost refID for E4. L10: E4, E9. L8: E3, E5 and the dialogue specs.')
    A('')
    A('## Test output (`python -I questlines/strip/run_tests.py`)')
    A('')
    A('```')
    A(test_output.strip())
    A('```')
    return '\n'.join(L) + '\n'


def main():
    out = build_mod.build_all()
    code, txt = run_tests(skip_status=True)
    if code != 0:
        print(txt)
        raise SystemExit('run_tests failed before the status doc was written')
    os.makedirs(os.path.dirname(DOC), exist_ok=True)
    with open(DOC, 'w', encoding='utf-8', newline='\n') as f:
        f.write(render(out, txt))
    code, txt = run_tests(skip_status=False)
    with open(DOC, 'w', encoding='utf-8', newline='\n') as f:
        f.write(render(out, txt))
    print(txt)
    return code


if __name__ == '__main__':
    sys.exit(main())
