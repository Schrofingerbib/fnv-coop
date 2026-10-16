"""Machine-readable co-op rules for the runtime (docs/quest-policies/rules.json).

Rule = {"kind", "params", "origin": "curated"|"derived"}.  Kinds (runtime vocabulary, aligned with the
proposed L4 effect/hook types):
  PartyTether        stage/scene gated on party presence        params: cells[], gather ("auto-warp"|"wait")
  PartyWarp          player.MoveTo -> move every party member   params: markers[], mode ("slot-offset"), absent ("hold"|"warp")
  SetPlayerControls  cutscene lock for all party members        params: scope "all", release "host-effect", timeout_s, masks[]
  Barrier            shared stage advances when all players pass params: step "all-players-pass"
  PerPlayerScene     scene/effects target the originating player only (chargen, AutoDoc)
  SlideshowEnd       ending slideshow: host runs once; lock/move/release together
  CompanionOwner     SetPlayerTeammate effects owned by recruiter  params: owner "recruiter"
  SaveSuppress       no autosave/quicksave during scene
  FastTravelLock     party-wide fast-travel flag, host authoritative
  PartyAwareCond     host evaluates player-ref conditions over the party  params: funcs{fn: reduction}
  QuestItemDup       quest items duplicated to every player (AddItem Recipient=All)
  RewardAll          caps/items/xp rewards to every player in full
  RemoveItemPerOwner hand-in removes from the handing player only
  ShareKarmaRep      karma/reputation shared (one Courier)
  InitiatorDialogue  initiator chooses; line + result broadcast
  ExcludeFromGate    debug content, not required for any ending
"""
import re

CAT_KINDS = [   # (category, min level, kind)
    ('CTRL', 3, 'SetPlayerControls'), ('CTRL', 3, 'PartyTether'),
    ('CTRL-DIALOG', 2, 'SetPlayerControls'), ('CTRL-RELEASE', 2, 'SetPlayerControls'),
    ('MOVE', 4, 'PartyWarp'), ('COMPANION', 3, 'CompanionOwner'), ('SAVE', 2, 'SaveSuppress'),
    ('TRAVEL', 1, 'FastTravelLock'), ('P-REF', 3, 'PartyAwareCond'), ('S-STAT', 1, 'ShareKarmaRep'),
    ('REWARD', 1, 'RewardAll'), ('P-INV', 2, 'QuestItemDup'), ('CONV', 2, 'InitiatorDialogue'),
]
HOOK_KEYWORDS = [   # (substring lowercased, kind)
    ('partytether', 'PartyTether'), ('party-warp', 'PartyWarp'), ('moveref', 'PartyWarp'),
    ('setplayercontrols', 'SetPlayerControls'), ('slideshowend', 'SlideshowEnd'),
    ('companionowner', 'CompanionOwner'), ('savesuppress', 'SaveSuppress'), ('travel flag', 'FastTravelLock'),
    ('party-aware', 'PartyAwareCond'), ('isactionref', 'PartyAwareCond'), ('additem(all)', 'RewardAll'),
    ('removeitem(per-owner)', 'RemoveItemPerOwner'), ('modreputation', 'ShareKarmaRep'),
    ('modkarma', 'ShareKarmaRep'), ('forceddialogue', 'InitiatorDialogue'), ('barrier', 'Barrier'),
    ('modactorvalue(target)', 'PerPlayerScene'),
    ('showracemenu', 'PerPlayerScene'),
]
COND_REDUCTION = {'getdistance': 'min-over-party', 'getincell': 'entry:all-in,exit:any-in',
                  'getinsamecell': 'any', 'isactionref': 'initiator', 'iskiller': 'any-party-member',
                  'getinworldspace': 'any', 'getav': 'initiator', 'getequipped': 'initiator'}
# curated overrides that cannot be recovered from the hook text
KIND_EXTRA = {'VCG00': ['Barrier', 'PerPlayerScene'], 'VCG01': ['Barrier', 'PerPlayerScene'],
              'PressDemoQuest': ['ExcludeFromGate'], 'NVDLC01EnemyTest': ['ExcludeFromGate']}
ENDING_SLIDESHOW = {'VHDHouseBattle', 'VHDIndependentBattle', 'VHDLegionBattle', 'NVDLC02MQ06'}


def _findings(risk):
    return risk['findings'] + risk['stage_findings']


def _params(kind, risk, edid):
    fs = _findings(risk)
    uniq = lambda xs: list(dict.fromkeys(xs))[:16]
    if kind == 'PartyWarp':
        return {'markers': uniq(m.group(1) for f in fs if f['cat'] == 'MOVE'
                                for m in [re.search(r'move\s*to\s+(\w+)', f['text'], re.I)] if m),
                'mode': 'slot-offset', 'absent': 'hold'}
    if kind == 'PartyTether':
        cells = uniq(m.group(1) for f in fs for m in [re.search(r'getincell\s+(\w+)', f['text'], re.I)] if m)
        return {'cells': cells, 'gather': 'auto-warp'}
    if kind == 'SetPlayerControls':
        masks = uniq(' '.join(m.group(1).split()) for f in fs if f['cat'].startswith('CTRL')
                     for m in [re.search(r'disableplayercontrols\s+([01 ]+)$', f['text'].strip(), re.I)] if m)
        return {'scope': 'all', 'release': 'host-effect', 'timeout_s': 120, 'masks': masks}
    if kind == 'PartyAwareCond':
        funcs = {}
        for f in fs:
            if f['cat'] == 'P-REF':
                for fn in re.findall(r'\b(getdistance|getincell|getinsamecell|isactionref|iskiller|getav|'
                                     r'getequipped|getinworldspace)\b', f['text'], re.I):
                    funcs[fn] = COND_REDUCTION[fn.lower()]
        return {'funcs': funcs}
    if kind == 'CompanionOwner':
        return {'owner': 'recruiter'}
    if kind == 'FastTravelLock':
        return {'scope': 'party'}
    if kind == 'SlideshowEnd':
        return {'run': 'host-once', 'lock': 'all', 'release': 'together'}
    if kind == 'Barrier':
        return {'step': 'all-players-pass'}
    if kind == 'QuestItemDup':
        return {'recipient': 'all'}
    return {}


def machine_rules(edid, risk, curated=None):
    kinds, origin = [], {}

    def add(k, o):
        if k not in origin:
            kinds.append(k)
            origin[k] = o
    if curated:
        text = (curated['hooks'] + ' ' + curated['rule']).lower()
        for sub, k in HOOK_KEYWORDS:
            if sub in text:
                add(k, 'curated')
        if curated.get('tether'):
            add('PartyTether', 'curated')
        for k in KIND_EXTRA.get(edid, []):
            add(k, 'curated')
        if 'quest item' in text:
            add('QuestItemDup', 'curated')
        if 'Barrier' in kinds and 'PartyTether' in kinds:      # per-player scenes have no spatial tether
            kinds.remove('PartyTether')
            origin.pop('PartyTether')
        if edid in ENDING_SLIDESHOW:
            add('SlideshowEnd', 'curated')
        if 'ExcludeFromGate' in origin:          # excluded content carries no other rules
            kinds = ['ExcludeFromGate']
    else:
        for cat, lvl, k in CAT_KINDS:
            if risk['cat_max_level'].get(cat, -1) >= lvl:
                add(k, 'derived')
    return [{'kind': k, 'params': _params(k, risk, edid), 'origin': origin[k]} for k in kinds]
