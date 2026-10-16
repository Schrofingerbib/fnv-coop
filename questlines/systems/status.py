"""python -I questlines/systems/status.py -> regenerates deliverables, runs the tests and writes internal questline notes (with the test output pasted)."""
import collections
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import docgen  # noqa: E402
import generate  # noqa: E402
import qlib  # noqa: E402
import scope  # noqa: E402


def main():
    d = generate.compute()
    generate.write_all(d)
    cen = {r['fn']: r for r in d['census']['rows']}
    gaps = d['gaps']
    recs = d['recs']
    inv = d['inventory']

    def n(fn):
        return cen[fn]['count'] if fn in cen else 0

    def sc(fn):
        return cen[fn]['scripts'] if fn in cen else 0

    gf = {r['fn']: r for r in gaps['by_function']}

    def g(fn):
        return gf[fn]['count'] if fn in gf else 0

    per_player_scripts = sum(1 for r in recs if r['policy'] == 'per-player' and r['type'] != 'radio')
    own = sum(1 for r in recs if r['own_line'])
    ov = len(recs) - own
    by_type = collections.Counter(r['type'] for r in recs)
    by_pol = collections.Counter(r['policy'] for r in recs)
    sw = d['sweep']
    st_total = inv['scope']['stages']
    sev = gaps['counts']['by_severity']

    blockers = [
        ('Guest gate silences per-player quests',
         'L5 round 4 rule 1 skips EVERY quest-owned script on a guest, but %d in-scope quest scripts are per-player by nature (achievements, casino winnings, perk effects, '
         'rumble, DLC/GRA setup, chargen helpers, fade timers) and %d radio quests are local audio. Without a local-run allow-list (`GuestLocalRun`, see policy_review.md) they simply stop on clients.'
         % (per_player_scripts, by_type['radio'])),
        ('SetPlayerTeammate is not hooked',
         '%d calls in %d scripts dataset-wide (%d in scope). No engine address is recorded, so CompanionOwner is never emitted: hire/fire/wheel/limit are unsynced. '
         'OpenTeammateContainer (%d calls) is likewise unhooked.' % (n('setplayerteammate'), sc('setplayerteammate'), g('setplayerteammate'), n('openteammatecontainer'))),
        ('Shared faction rank / per-owner perks are block-only',
         'AddToFaction %d, RemoveFromFaction %d, SetFactionRank %d, AddPerk %d, RemovePerk %d calls: L5 only drops them on guests. The owner decision "faction rank shared" and the '
         'eight recruit perks (Spotting, WhiskeyRose, ScribeAssistant, StealthGirl, EnhancedSensors, FullMaintenance, BetterHealing, SearchAndMark) cannot work until SetFactionRank/SetPerk capture+apply exist.'
         % (n('addtofaction'), n('removefromfaction'), n('setfactionrank'), n('addperk'), n('removeperk'))),
        ('Follower cap and auto-fire assume one Courier',
         'VNPCFollowers keeps ONE humanoid + ONE critter slot (bHumanoidInParty/bCritterInParty) and fires waiting companions when `GetInSameCell Player == 0`. In co-op that means one companion pair for the '
         'whole party and companions fired next to player B. Needs the per-owner cap decision and PartyAwareCond over the owner\'s party.'),
        ('Cinematics, control locks and time skips',
         'DisablePlayerControls %d / EnablePlayerControls %d calls are only partially modelled (7-flag lock, no mask); host-run timers (VHDOliverDeathTimer, VMQ03a/b, BennyToArena, vBennySex, '
         'VGenericTimer, FadeToCreditsTimer) fade, lock, MoveTo the player and skip time (SetPCSleepHours %d, GameHour/GameDaysPassed writes). Endings: VEnding disables 11 companion refs, locks all controls and '
         'moves the player to the slideshow marker.' % (n('disableplayercontrols'), n('enableplayercontrols'), n('setpcsleephours'))),
        ('PlaceAtMe spawns have no effect type',
         '%d PlaceAtMe/PlaceLeveledActorAtMe calls (%d in scope); the 14 random/special encounter quests (VER*, VES*) depend on them. A client copy would duplicate every spawn; needs host-owned spawn + NV:MP '
         'ghost (L6) and a Spawn effect.' % (n('placeatme') + n('placeleveledactoratme'), g('placeatme') + g('placeleveledactoratme'))),
        ('Menus and messages open on the host',
         'ShowMessage %d, ShowBarterMenu %d, ShowCaravanMenu %d, ShowRepairMenu %d, ShowMap %d: a host-only quest/timer script that calls them opens the UI on the HOST machine. Needs routing to the '
         'initiator / owning player (service sub-session, per-player message).' % (n('showmessage'), n('showbartermenu'), n('showcaravanmenu'), n('showrepairmenu'), n('showmap'))),
        ('Object-script variable writes are not captured',
         '%d `set <local> to` writes inside object/NPC scripts and %d unparsed variable-style statements: only quest vars (diff) replicate. Companion/NPC script state (Waiting, IsFollowingDefault, bPlayerRep ...) stays local.'
         % (n('setobjectvar'), d['census']['garbage_tokens_folded'])),
        ('NPC-targeted AddItem/RemoveItem and non-player ActorValue changes',
         'Only player inventory/AV deltas are captured. 332 AddItem and 262 RemoveItem calls target other refs (vendor containers, companion weapon restore in VNPCFollowers, Doc Mitchell-style hand-ins) and '
         'non-player SetAV/ModAV (companion Assistance/Aggression, wheel tactics) hit the `avOnNonPlayer` counter only.'),
        ('Per-player journal and progress state',
         'AddNote %d, RemoveNote %d, AddAchievement %d, IncrementScriptedChallenge %d, UnlockChallenge %d, ForceActiveQuest %d, AddTopic %d: unhooked, no Effect, undecided per-player vs shared. '
         'Achievements/challenges are per machine by nature; notes/topics need a decision.'
         % (n('addnote'), n('removenote'), n('addachievement'), n('incrementscriptedchallenge'), n('unlockchallenge'), n('forceactivequest'), n('addtopic'))),
    ]

    L = ['# Questline status: systems (companions + unassigned quests)', '',
         'Owner: Q-systems. Deliverables: `questlines/systems/` (run `python -I questlines/systems/run_tests.py`; regenerate with `python -I questlines/systems/generate.py`, or this file with `status.py`).', '',
         '## Scope', '',
         '- (a) policy files `side-misc-events.md` and `systems-misc.md` (15 quests with hand/derived policy);',
         '- (b) companions: personal quests, wheel, hire/fire, follower limit, recruit perks: `companions.md/json`;',
         '- (c) every quest on the questgraph lines "Systems & misc" (212), "Side: Misc / events" (77) and "DLC: Gun Runners\' Arsenal" (1) = the quests no other questline lane owns. '
         'Companion quests that live on other lines (%d: %s) are included and flagged `overlap_with_other_line`; the lane that owns that line should treat this analysis as input, not as a duplicate decision.'
         % (ov, ', '.join(r['edid'] for r in recs if r['overlap'])), '',
         '## Counts', '',
         docgen.table(['metric', 'value'], [
             ['quests in scope', len(recs)], ['  own buckets', own], ['  companion overlap', ov],
             ['stages (all in-scope quests)', st_total], ['quests with stages swept', sw['summary']['quests_with_stages']],
             ['stages swept (sweep.json)', sw['summary']['stages']], ['stages excluded', sum(len(e['stages']) for e in sw['exclusions'])],
             ['stageless quests (no setstage possible)', sw['summary']['stageless_quests']],
             ['excluded quests (debug, owner decision)', sw['summary']['excluded_quests']],
             ['scripts in census', d['census']['meta']['scripts']], ['statement-level calls', d['census']['meta']['statements']],
             ['distinct functions', d['census']['distinct_functions']],
             ['calls by L5 status', ', '.join('%s %d' % kv for kv in d['census']['calls_by_status'].items())],
             ['gap calls in scope (not supported)', gaps['counts']['unsupported_calls']],
             ['gaps by severity', ', '.join('%s %d' % kv for kv in sorted(sev.items(), key=lambda x: -x[1]))],
             ['rules.patch.json', '%d new rule sets, %d modified' % (len(d['patch']['add']), len(d['patch']['modify']))],
         ]), '',
         '### Types and policies', '',
         docgen.table(['policy', 'quests'], [[k, v] for k, v in by_pol.most_common()]), '',
         docgen.table(['type', 'quests', 'policies'], [[t['type'], t['quests'], ', '.join('%s %d' % kv for kv in t['policies'].items())] for t in d['types']['types']]), '',
         '## Top 10 blockers for co-op in this questline', '']
    for i, (t, body) in enumerate(blockers, 1):
        L.append('%d. **%s.** %s' % (i, t, body))
    L += ['', '## Deliverable map', '',
          docgen.table(['file', 'what'], [
              ['inventory.json', 'every in-scope quest: EDID, FormID, stages, objectives, prerequisites, dialogue counts, risk tier, type, policy'],
              ['types.json / types.md', 'classification (radio, encounters, achievements, timers, freeform dialogue, debug, ...) + sync policy per quest'],
              ['companions.json / companions.md', 'eight companions: refs, perks, hired flags, recruit/dismiss INFOs, wheel topics, follower limit, hazards'],
              ['census.json / census.md', 'MASTER effect-function census over all %d scripts + hook backlog (function, count, Effect, L5 status)' % d['census']['meta']['scripts']],
              ['gaps.json / gaps.md', 'unsupported calls in scope with script, line, hazard, severity'],
              ['sweep.json', 'm1_goodsprings-compatible stage sweep (setup + setstage commands with hex FormIDs), exclusions, stageless list, companion probes'],
              ['policy_review.md / rules.patch.json', 'review against owner decisions + rule patch (16 existing kinds only; 2 proposals)'],
              ['dialogue.md', 'key conversations, companion recruit INFOs, initiator-picks breakages'],
              ['run_tests.py', 'validators + 14 mutation twins']]), '',
          '## Known limits (honest)', '',
          '- Nothing here was run in the game. L5 status comes from reading `plugin/hooks` and `internal lane notes` (rounds 1-8), not from a live probe.',
          '- Raul has no stage-bearing personal quest in the data; whether one exists elsewhere (script-driven or in a DLC) is unknown.',
          '- Console syntax `set <hexid>.<var> to N` in sweep setup/probes is unverified in xNVSE; `setstage`/`startquest` with hex FormIDs are standard.',
          '- Classification of 71 `dialogue_freeform` and 68 `dialogue_generic` quests is rule-based (result-script presence); the 3 debug quests and ~15 overrides were read by hand.',
          '- Census L5 statuses are static judgements; "supported" means a hook and an applier exist, not that the game-side behaviour was proven for that call.',
          '- Dependency scripts (object/NPC scripts that a quest touches) are not in `gaps.json`; `risk.json` already lists them per quest.', '',
          '## Test output', '', '```']
    env = dict(os.environ)
    p = subprocess.run([sys.executable, '-I', os.path.join(qlib.HERE, 'run_tests.py')], capture_output=True, text=True, encoding='utf-8')
    out = (p.stdout + p.stderr).strip()
    L.append(out)
    L.append('```')
    os.makedirs(os.path.join(qlib.ROOT, 'docs', 'questlines'), exist_ok=True)
    with open(os.path.join(qlib.ROOT, 'docs', 'questlines', 'systems.md'), 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(L) + '\n')
    print(out)
    return p.returncode


if __name__ == '__main__':
    sys.exit(main())
