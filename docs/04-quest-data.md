# 04 - Extracting all quest / dialogue / script data from FalloutNV.esm (+ DLC)

Status legend: [VERIFIED] = checked directly this session (repo/source), [KNOWN] = from prior knowledge, not re-verified, [ESTIMATE] = rough number, must be re-measured.

Note: the local Steam folder `C:\Program Files (x86)\Steam\steamapps\common\Fallout New Vegas\` exists but is EMPTY (no Data\*.esm), so no counts were measured from real files. Counts below are estimates; the PoC `esm_stats.py` below will give exact numbers once the game files are present.

## 1. Tools / libraries

| Tool | Link | FNV? | Records useful to us | Verdict |
|---|---|---|---|---|
| xEdit (FNVEdit = xEdit built with `-FNV`) | https://github.com/TES5Edit/TES5Edit | Yes, first-class [VERIFIED: Core/wbDefinitionsFNV.pas, 9123 lines] | Everything: QUST, DIAL, INFO, SCPT, GLOB, FACT, NPC_, CREA, REFR/ACHR/ACRE, NOTE, PACK, REPU, CHAL, TERM, IMOD, DOBJ [VERIFIED defs exist] | Authoritative spec + only fully correct reader. Use for ground truth and as the "oracle" to cross-check our own parser. |
| xEdit record definitions | https://github.com/TES5Edit/TES5Edit/blob/dev/Core/wbDefinitionsFNV.pas (also wbDefinitionsFO3.pas, wbDefinitionsCommon.pas) | Yes | Line refs in current dev: DIAL 4521, FACT 4735, GLOB 4772, SCPT 4919, TERM 4931, NOTE 5084, INFO 6383, PACK 6990, QUST 7292, CHAL 8249, REPU 8482; embedded-script struct ~3035-3110 | THE format spec. Port it to our parser's schema. |
| xEdit scripts | https://github.com/TES5Edit/xEditScripts (old, 2016) and `Edit Scripts\` folder shipped in xEdit zip | Pascal/JvInterpreter scripting works under FNV | Write a script that walks `FileByIndex(i)`, `GroupBySignature(f,'QUST')`, uses `ElementByPath(rec,'Stages')`, `GetElementEditValues`, `Add JSON via TStringList`. Community "export to JSON/CSV" scripts exist (mostly FO4/Skyrim; adapt paths for FNV). | Good for one-off dumps; slow (headless via `-script:`, `-IKnowWhatImDoing`, `-quickautoclean` style CLI). |
| Mutagen (.NET) | https://github.com/Mutagen-Modding/Mutagen | NO. Repo contains Oblivion/Skyrim/Fallout4/Starfield projects; a `Mutagen.Bethesda.Fallout3` folder exists in the tree but I found no `FalloutNV` project and no GameRelease for NV [VERIFIED dir listing; web search found nothing contrary]. | n/a | Not usable for FNV. Don't wait for it. |
| Spriggit | https://github.com/Mutagen-Modding/Spriggit | Built on Mutagen => no FNV | n/a | Not usable. |
| esplugin (Rust) | https://github.com/Ortham/esplugin | Yes for FNV (LOOT's lib) [KNOWN] | Header, masters, FormID lists, record-level iteration; does NOT decode subrecord semantics | Useful as a fast, correct record/group/compression walker; we decode subrecords ourselves. GPL-3 (license caution). |
| bethesda-structs (Python) | https://pypi.org/project/bethesda-structs/ (stephen-bunn) | Partial FNV plugin class [KNOWN from PyPI desc] | Generic record/subrecord framing | Old, partial; fine as framing reference only. |
| espa (Rust) | https://crates.io/crates/espa | unknown | ESM <-> text conversion | Unverified; skip. |
| libnvs (C++) | gamesas forum thread "libnvs 1.0" | FNV, 2015 | read/write | Unmaintained, skip. |
| xNVSE | https://github.com/xNVSE/NVSE | runtime, not an extractor | Provides new script functions that appear in modded (not vanilla) scripts | Needed to know the function set (`GetNumQuests` etc.) when scanning; vanilla ESM scripts only use vanilla opcodes + a few GECK-extender ones. |
| GECK itself | `GECK.exe` | yes | Scripts > "Export/Compile All"; Quest list view | Last-resort/GUI cross-check. |

Recommendation: write our OWN small parser (Python or Rust, ~400 lines for framing + the 8-10 record types we need), driven by the xEdit definitions, and validate it against an xEdit JSON/CSV dump on a sample of ~50 quests. The ESM container format is trivial (see section 3); the only complexity is subrecord schemas, and for quests they are few.

## 2. How quest/dialogue scripts are stored

All scripts share the same "embedded script" subrecord group (wbEmbeddedScript in xEdit) [VERIFIED]:

```
SCHR  (20 bytes)  u32 unused, u32 RefCount, u32 CompiledSize, u32 VariableCount, u16 Type(0 Object,1 Quest,0x100 Effect), u16 Flags
SCDA  byte[CompiledSize]   compiled bytecode (opcode u16, length u16, args...)
SCTX  string                source text (what we want; plain ASCII/cp1252, CRLF)
SLSD+SCVR  repeated         local variable index/type + name
SCRO  u32 formid  or  SCRV u32 local-var index   reference table used by bytecode
```

Where it occurs:
- QUST: `SCRI` = the quest's main (quest-script) -> formid of an SCPT record (a *separate* record, with SCHR/SCDA/SCTX/SLSD/SCVR/SCRO of its own, type Quest). Plus per stage: `Stages[] -> INDX (s16) -> Log Entries[] { QSDT flags(1=complete,2=fail), CTDA conditions, CNAM log text, embedded script (SCHR/SCDA/SCTX..), NAM0 next quest }`. The stage "result script" is the embedded script in each Log Entry. Objectives: `QOBJ index, NNAM text, QSTA target ref + flags, CTDA conds`.
- INFO (dialogue lines): `Script (Begin)` embedded script, then `NEXT` marker, then `Script (End)` embedded script [VERIFIED]. Quests are linked with `QSTI` (quest), `PNAM` previous INFO, `NAME` add-topics, `TCLT/TCLF/TCFU` links, `CTDA` conditions, `Responses[] {TRDT, NAM1 text, NAM2 notes}`. DATA flags give Goodbye/Say Once/Run Immediately (those run in dialogue context).
- SCPT: standalone object/quest scripts: `EDID, SCHR, SCDA, SCTX, SLSD/SCVR, SCRO` [VERIFIED]. Attached to objects via `SCRI` on NPC_/CREA/ACTI/DOOR/CONT/TERM/etc.
- TERM menu items, PACK (package begin/end/change scripts), PERK entries, MESG, etc. also embed scripts. TERM and PACK matter for co-op.
- Source text availability: SCTX is present in the shipped ESMs (Bethesda left it in), so NO decompiler is needed for vanilla. Compile-only mods that strip SCTX would need a bytecode disassembler (xEdit shows "Decompiled" view? FNVEdit has `wbScriptToStr`; not general). Extraction = read SCTX; if absent, fall back to SCDA + SCRO and disassemble (opcode table from NVSE `CommandTable`).
- Compression: records with flag 0x00040000 have `u32 decompressedSize + zlib stream`. INFO/QUST are often compressed.
- Strings: cp1252; localisation strings not used in FNV (inline).
- Quest *result scripts also run from topics*: INFO End script commonly does `SetStage QuestForm N`, `player.AddItem`, `AddTopic`, `SetEnemy`, etc.
- Formids: top byte is master index; resolve via TES4 header `MAST` list. DLC ESMs (DeadMoney, HonestHearts, OldWorldBlues, LonesomeRoad, GunRunnersArsenal, ClassicPack/Courier's Stash etc.) reference FalloutNV.esm by index 0x00.. in their masters.

## 3. ESM container (for our parser)

```
Record  : char[4] sig, u32 dataSize, u32 flags, u32 formid, u32 vc1, u16 version, u16 vc2  (24 bytes) + data (subrecords)
Group   : "GRUP", u32 groupSize(includes 24 hdr), char[4] label, u32 type, u32 stamp/version (24 bytes) + children
Subrec  : char[4] sig, u16 size, data   ; sig 'XXXX' (u32 size) then next sub with size 0 for oversized
```
Walk top-level: TES4 record, then GRUPs. Top-level groups: GMST,TXST,...,QUST,DIAL (DIAL group contains INFO in sub-GRUP type 7 "Topic Children"), CELL (nested groups type 6 persistent children etc.; REFR/ACHR/ACRE in cells), WRLD. A ~100 line walker is enough to enumerate everything.

## 4. Existing dumps / public data

- GECK Wiki (https://geck.bethsoft.com/index.php/Main_Page): function docs (exactly what each script function does, whether it takes `player`). Use as the function semantics table. Also NVSE function docs: https://geckwiki.com/index.php/Category:Functions (Category:Functions_-_Quest, _Actor, _Player, etc.).
- Fallout wiki (Nukapedia, https://fallout.fandom.com/wiki/Fallout:_New_Vegas_quests and per-quest pages): human-oriented walkthroughs; the "Quick walkthrough/Detailed" pages and "Behind the scenes" sections frequently list quest IDs, e.g. `0000XXXX`, and sometimes stage numbers with console `setstage`/`sqv` output. Useful for human sanity check, not machine-readable. dev.fallout.wiki also exists.
- Console command `sqv <questid>` prints stages/objectives; community "setstage" lists on Nukapedia/NexusMods ("Console Commands - complete quests") enumerate stages for main quests.
- I found NO ready CSV/JSON dump of all FNV quests with stage result scripts. Plan to generate it ourselves (cheap, deterministic, reproducible).

## 5. Counts (ESTIMATES - to be measured)

Base FalloutNV.esm [ESTIMATE, from memory of FNVEdit]: ~1,000 QUST records of which ~250-300 are "real" player-facing quests (the rest are ambient/dialogue-hosting/system quests like `VCG*`, `VFreeform*`, `VDialogue*` quest-per-location dialogue containers, radio, companion, tutorial, DLC-less system quests); ~15-20k INFO records; ~15-20k DIAL topics... (INFO more than DIAL); ~3,000-4,000 SCPT records; a few thousand terminal/package scripts. Dialogue is the largest set; most INFO scripts are empty or `SetStage`/`AddTopic`.
DLCs (each adds roughly 10-40 quests, 1-3k INFO): Dead Money, Honest Hearts, Old World Blues, Lonesome Road, plus Gun Runners' Arsenal and Courier's Stash (tiny).
Run `esm_stats.py` (below) for real numbers; also note each ESM's `NAM0`/`QSTI` classification of "dialogue-hosting" vs "story" quests (a quest with no stages and no objectives = container).

## 6. Co-op risk: dangerous script patterns

Assumption: one world, two (or N) players; each machine's engine runs quest/dialogue scripts locally against ITS player; quest state (stage, globals, ownership of refs) must be synced. The unit of danger is "script that makes an assumption that exactly one player exists, or that changes player-only state."

Risk categories and example tokens (case-insensitive, tokenised, `;` comments stripped). Level: 0 safe, 1 sync-state, 2 per-player replicate, 3 needs design, 4 hard.

| Cat | Tokens / patterns | Why | Level |
|---|---|---|---|
| Q-STATE | `SetStage`, `SetObjectiveCompleted`, `SetObjectiveDisplayed`, `CompleteQuest`, `FailQuest`, `StartQuest`, `StopQuest`, `GetStage`, `GetStageDone`, quest-var `Quest.var`, `SetQuestObject`, `SetGlobalValue`/globals | shared story state - core to sync (host-authoritative) | 1 |
| P-INV | `player.AddItem`, `player.RemoveItem`, `RemoveAllItems player`, `player.EquipItem`, `AddItemHealthPercent`, `AddItemNS`, `RemoveMe` to player | per-player inventory - replicate to the player who triggered (or both for shared rewards) | 2 |
| P-STAT | `player.ModAV`, `player.SetAV`, `ForceAV`, `RewardXP`, `RewardKarma`, `ModPCMiscStat`, `player.AddPerk`, `RemovePerk`, `AddTrait`, `ModReputation`, `SetReputation`... (`ModReputation`/`PlayerTotalKarma`) | per-player vs shared (karma/reputation: decision needed) | 2-3 |
| P-REF | `GetIsReference player`, `GetPlayerControlsDisabled`, `GetPlayerHasLastRiddenHorse`, comparing `ref == PlayerREF`, `GetActionRef == PlayerRef`, `GetSelf` with `player`, `GetInCell player`, `GetDistance player`, `GetPlayerTeammate`, `PlayerRef.GetPos`, any condition `GetIsID`, `GetQuestRunning` | single-player assumption: condition must be evaluated over "any player" or "the speaker" - need a reference mapping layer | 3 |
| MOVE | `player.MoveTo`, `MoveTo player`, `SetPos on player`, `player.CenterOnCell`, `PlayerSetPos`, `Load..`, `CenterOnWorld`, `Teleport`, `FastTravel` | teleports; must teleport all or only that player | 3-4 |
| CONV | `StartConversation`, `StopConversation`, `ShowBarterMenu`, `StartCombat player`, `SayTo`, `Say`, `ShowRaceMenu`, `ShowMenu`, `ShowMessage`/`ShowMessageBox`, `MessageEX`, `SetNPCRef` | UI/local presentation; show on correct client(s) | 2 |
| CTRL | `DisablePlayerControls`, `EnablePlayerControls`, `SetPlayerCanTravel`, `DisableControl`, `EnableControl`, `SetCameraOn`, `ForcePlayerCamera/ShowPlayerCam`, `SetPlayerAIDriven`, `SetIgnoreFriendlyHits`, `SetPlayerTeammate`, `StartCinematic`, `PlayBink`, `EnableFastTravel`, `SetInChargen`, `ForceThirdPerson` | cinematic locks and cutscenes - hardest. Must define whose cutscene, whether others freeze | 4 |
| WORLD | `ForceWeather`, `SetWeather`, `SetGameHour`, `SetTimescale`, `SetGlobalTimeMultiplier`, `PlayMusic`, `SetImageSpace`, `ImageSpaceModifier`, `SetCellFullName`, `SetNoRadio`, `Disable/Enable` of refs | shared world effect: host-authoritative, probably OK | 1 |
| ACTOR | `Disable`, `Enable`, `MoveTo`, `SetEssential`, `Kill`, `Resurrect`, `StartCombat`, `PayFine`, `SetAlly`, `SetEnemy`, `SetCombatStyle`, `AddScriptPackage`, `EvaluatePackage`, `SetFactionRank`, `Follow`/`SetPlayerTeammate`, `SetRestrained` | NPC state: host-authoritative sync | 1-2 |
| SAVE/GAME | `Save`, `QuickSave`, `SetPlayerSkipsSave`, `Autosave`, `Quit`, `LoadGame`, `PlayerSaveGame`, `SetDeadCountMult`, `ModPCMiscStat`, `SetChallengeCount`/`ModChallenge`, `ShowMessage` | engine-global | 2-4 |
| TIME/WAIT | `GetSecondsPassed`, `GameDaysPassed`, `Wait`/`Wait for` (stages) | fine if clocks synced | 0-1 |
| INFO ctx | INFO flag `Run Immediately`, `Goodbye`, `Say Once` (per-actor? global), INFO conditions `GetIsID`, `GetPlayerHasLastRiddenHorse`, `GetPCIsSex`, `GetPCInFaction`, `GetPCMiscStat`, `GetHasVoiceType`; `Speech Challenge` | dialogue is inherently 1:1; need "dialogue owner = initiating player; other player in/out of conversation" rule | 3 |

Hierarchy of dependency: a quest is as risky as its WORST tier-4 token reachable via (a) its quest script (SCRI -> SCPT), (b) every stage result script, (c) every INFO belonging to that quest (QSTI) with either begin or end script, (d) scripts of objects/NPCs referenced in objective targets (QSTA) and in stage scripts (SCRO), (e) packages of key NPCs (PACK scripts), (f) terminals.

## 7. Static analysis approach

Parse script source into a tiny AST/token stream (no full parser needed):
1. Strip `;` comments; split into statements by newline; track `Begin <Block> ... End` (GameMode, OnActivate, OnTrigger, OnLoad, OnDeath, OnHit, MenuMode, Result Script "Begin"/"End" etc.). `Begin MenuMode` / `OnActivate` blocks matter: activator is "the player" implicitly. Embedded result scripts have no `Begin` wrapper.
2. Tokenise with a function-name lexicon from NVSE `CommandTable` (xNVSE `GameAPI`/`CommandTable.cpp`) + GECK wiki function list; tag each function with: `targets` (needs ref), `playerOnly`, category (table in section 6).
3. Detect player references: literal `player`, `PlayerREF`, `Player.`, `GetPlayer`, `GetActionRef` (when `OnActivate`), `GetSelf` ... also references resolved through `SCRO` that point to the PLYR record (`0x00000014`) or REFR `0x14` (the player ref is formid 0x00000014, base 0x00000007). Include dereferenced alias variables (`ref rPlayer; set rPlayer to player`) via simple one-level def-use.
4. Output per script: set of categories, per-line findings, "max risk", has_cutscene (CTRL/camera/Bink), has_move (MOVE), mutates_player (P-*), mutates_world, mutates_quest.
5. Aggregation: build graph Quest -> {quest script, stage scripts, INFOs (via QSTI), targets (QSTA), refs (SCRO), packages, terminals}. Risk(quest) = max over reachable nodes + weighted count. Additionally compute "dependency fan-in": number of other quests' conditions that read this quest's stage (`GetStage`, `GetQuestVariable`, `GetStageDone` in CTDA) -> shared-state coupling.
6. Condition (CTDA) analysis too: CTDA struct = op/flags, comparison value (float or global), function index (u16), params, run-on (0 Subject, 1 Target, 2 Reference, 3 Combat Target, 4 Linked Ref), reference. `Run On = Reference 0x14`/ functions like `GetIsID`, `GetPCMiscStat`, `GetPCInFaction`, `GetPlayerTeammate`, `GetPCIsClass`, `GetPCFame`, `GetDisposition` : classify into "evaluated against player" -> needs per-player or aggregate evaluation policy.
7. Tiering outcome:
   - T0 Trivial (no player-specific effects; stage progression only) - e.g. ambient radio quests, container quests.
   - T1 Shared-state only (stage/global/NPC/world sync).
   - T2 Per-player rewards/items (replicate to both or the triggering player).
   - T3 Dialogue/conditions need design (reference mapping, ownership of conversation).
   - T4 Cutscenes / player-lock / teleports / forced camera / vendor of player-only mechanics (hacking? lockpick? speech check?) / companions / chargen (Doc Mitchell, intro) / Vault 21, Lucky 38 etc. Tutorial & prologue are T4 by definition.
8. Calibration: take a known-hard set (MQ "Ain't That a Kick in the Head", "Wild Card", Benny cutscene, Dead Money intro/gas) and known-easy (e.g. random fetch quests) and verify classifier.

## 8. Proposed pipeline

```
FalloutNV.esm + 5-6 DLC esm
        |
 [1] extract.py  (own parser; or xEdit script as oracle)
        -> out/<plugin>/quests.json, scripts.json, infos.jsonl, dials.json, packs.json, terms.json, globals.json
 [2] graph.py    resolves formids (master index remap), builds Quest->nodes edges, quest dependency graph
 [3] classify.py (lexicon.yaml + rules) -> risk.json per quest/stage/info
 [4] scenario_gen.py -> tests/<quest_editor_id>.yaml (per-stage assertions)
 [5] harness (see 00-test-harness-design.md) executes: setstage N on host, assert state on both clients
```

### 8.1 JSON schema (sketch)

```json
{
 "formid": "0x000A1234", "editor_id": "VMQ01", "plugin": "FalloutNV.esm",
 "name": "Ain't That a Kick in the Head",
 "flags": {"start_enabled": true, "allow_repeat_stages": false}, "priority": 100, "delay": 5.0,
 "script": {"formid":"0x...", "source":"scn VMQ01Script\n...", "vars":[{"idx":1,"type":"int","name":"x"}], "refs":["0x..."]},
 "conditions": [{"op":"==","value":1.0,"func":"GetStage","params":["0x.."],"run_on":"subject","or":false}],
 "stages": [{"index":10, "log_entries":[{"text":"...", "flags":["complete"],
        "conditions":[], "script":{"source":"...", "refs":[]}, "next_quest":null}]}],
 "objectives": [{"index":10,"text":"...", "targets":[{"ref":"0x..","conditions":[]}]}],
 "infos": ["0x..."], "topics":["0x.."],
 "risk": {"tier":"T3","cats":["P-INV","CONV"],"findings":[{"where":"stage 30","line":4,"text":"player.additem caps001 500","cat":"P-INV"}]}
}
```
INFO record: `{formid, topic, quest, speaker/conditions, flags, responses[{text,emotion}], script_begin, script_end, add_topics, link_to...}` (in JSONL since there are ~15-20k).

### 8.2 Proof-of-concept extractor (Python, no deps beyond zlib/struct)

```python
# extract.py <esm> <outdir>  -- PoC. Run with: python -I extract.py ...
import struct, zlib, sys, json, collections
def subrecs(d):
    o=0; big=None
    while o<len(d):
        sig=d[o:o+4]; sz=struct.unpack_from('<H',d,o+4)[0]; o+=6
        if sig==b'XXXX': big=struct.unpack_from('<I',d,o)[0]; o+=sz; continue
        if big is not None: sz=big; big=None
        yield sig.decode('latin1'), d[o:o+sz]; o+=sz
def walk(b,o,end):
    while o<end:
        sig=b[o:o+4]; sz=struct.unpack_from('<I',b,o+4)[0]
        if sig==b'GRUP':
            yield from walk(b,o+24,o+sz); o+=sz; continue
        flags,fid=struct.unpack_from('<II',b,o+8)
        d=b[o+24:o+24+sz]
        if flags&0x40000: d=zlib.decompress(d[4:])
        yield sig.decode(), fid, flags, d; o+=24+sz
def z(s): return s.split(b'\0')[0].decode('cp1252')
def parse_script(subs, i):  # subs: list of (sig,data); i: index of SCHR
    sc={'type':struct.unpack_from('<H',subs[i][1],16)[0],'source':'','vars':[],'refs':[]}
    j=i+1
    while j<len(subs) and subs[j][0] in('SCDA','SCTX','SLSD','SCVR','SCRO','SCRV'):
        s,d=subs[j]
        if s=='SCTX': sc['source']=d.decode('cp1252')
        elif s=='SLSD': sc['vars'].append({'idx':struct.unpack_from('<I',d)[0],'name':None})
        elif s=='SCVR': sc['vars'][-1]['name']=z(d)
        elif s=='SCRO': sc['refs'].append('%08X'%struct.unpack('<I',d)[0])
        j+=1
    return sc,j
def parse_qust(fid,d):
    subs=list(subrecs(d)); q={'formid':'%08X'%fid,'stages':[],'objectives':[],'conditions':[]}
    cur_stage=cur_entry=cur_obj=None; i=0
    while i<len(subs):
        s,x=subs[i]
        if s=='EDID': q['editor_id']=z(x)
        elif s=='FULL': q['name']=z(x)
        elif s=='SCRI': q['script']='%08X'%struct.unpack('<I',x)[0]
        elif s=='DATA': q['flags'],q['priority']=x[0],x[1]; q['delay']=struct.unpack_from('<f',x,4)[0] if len(x)>=8 else None
        elif s=='INDX': cur_stage={'index':struct.unpack('<h',x)[0],'entries':[]}; q['stages'].append(cur_stage); cur_obj=None
        elif s=='QSDT': cur_entry={'flags':x[0],'conditions':[]}; cur_stage['entries'].append(cur_entry)
        elif s=='CNAM' and cur_entry is not None: cur_entry['text']=z(x)
        elif s=='SCHR' and cur_entry is not None and cur_obj is None:
            cur_entry['script'],i=parse_script(subs,i); continue
        elif s=='NAM0': cur_entry['next_quest']='%08X'%struct.unpack('<I',x)[0]
        elif s=='QOBJ': cur_obj={'index':struct.unpack('<i',x)[0],'targets':[]}; q['objectives'].append(cur_obj); cur_stage=cur_entry=None
        elif s=='NNAM' and cur_obj is not None: cur_obj['text']=z(x)
        elif s=='QSTA': cur_obj['targets'].append({'ref':'%08X'%struct.unpack_from('<I',x)[0],'flags':x[4]})
        elif s=='CTDA':   # 28 bytes in FNV: op/flags(1)+3 pad,float|glob(4),func(u16)+2 pad,p1(4),p2(4),runon(4),ref(4)
            c={'op_flags':x[0],'cmp':struct.unpack_from('<f',x,4)[0],'func':struct.unpack_from('<H',x,8)[0],
               'p1':struct.unpack_from('<I',x,12)[0],'p2':struct.unpack_from('<I',x,16)[0],
               'run_on':struct.unpack_from('<I',x,20)[0] if len(x)>=24 else 0}
            tgt=cur_entry['conditions'] if cur_entry is not None else (cur_obj['targets'][-1].setdefault('conditions',[]) if cur_obj and cur_obj['targets'] else q['conditions'])
            tgt.append(c)
        i+=1
    return q
# main: iterate walk(), keep QUST + SCPT (index by formid) + INFO (group by QSTI), dump JSON.
```
(PoC is a sketch to be completed/validated against xEdit; CTDA function index -> name table from NVSE/GECK, e.g. 0x48 GetStage? use `Edit Scripts` dump to build map.)

Stats PoC: the 25-line `walk()` above plus a Counter of signatures per plugin and `d.count(b'SCTX')` gives exact numbers for section 5.

Oracle: xEdit script `ExportQuests.pas` iterating `GroupBySignature(f,'QUST')` -> `ElementByName(rec,'Stages')` and writing JSON; compare stage counts, script text hash with extract.py output. Run: `FNVEdit.exe -IKnowWhatImDoing -script:ExportQuests.pas -nobuildrefs FalloutNV.esm` (flags vary by version).

## 9. Risk classifier PoC design

`lexicon.yaml`:
```yaml
functions:
  setstage:        {cat: Q-STATE, level: 1}
  additem:         {cat: P-INV,  level: 2, player_if_target: [player, playerref]}
  moveto:          {cat: MOVE,   level: 3, level_if_target_player: 4}
  disableplayercontrols: {cat: CTRL, level: 4}
  forceweather:    {cat: WORLD, level: 1}
  ...
```
```python
def classify(src):
    findings=[]
    for n,line in enumerate(strip(src).splitlines(),1):
        toks=re.findall(r"[A-Za-z_]\w*|\.|\S",line)
        # find (target?) '.' func pattern; also 'func ... player' as argument
        ...
        level = lex[f].level; if target in PLAYER_NAMES or arg in PLAYER_NAMES: level=max(level, lex[f].get('level_if_target_player',level))
        findings.append((n,f,cat,level))
    return findings
```
Quest rollup: nodes = quest script + stage scripts + INFO begin/end scripts (+ linked via SCRO) ; `tier = max(level)`; output csv: quest, tier, cats, #stages, #infos, #cutscene-lines, deps-in, deps-out. Export a `risk_report.md` ranking, plus a manually curated override file `overrides.yaml` (humans fix mistakes).

## 10. Per-quest test scenario generator

For each quest produce `tests/<edid>.yaml`:
```yaml
quest: VMQ01
tier: T3
preconditions: {globals: {...}, stages_done: {...}}  # derived from quest CTDA + GetStageDone conditions
steps:
  - host: setstage VMQ01 10
    expect: {both: {stage VMQ01: 10, objective 10: displayed}}
  - host: setstage VMQ01 30      # stage with player.additem
    expect: {triggering_player: {inventory: {"caps001": "+500"}}, other_player: {inventory: "unchanged per policy"}}
policy_checks: [P-INV: reward-both-vs-owner, CTRL: cutscene-lock-policy]
```
Generation rules: one step per stage index in numerical order (respect `NAM0` next quest and QSDT complete/fail flags for final stage); expected outputs derive from findings (Q-STATE -> check `GetStage`; P-INV -> inventory diff; WORLD -> weather/time equality; ACTOR -> enable state equality); stage prerequisites from the quest/Log-Entry CTDA; dialogue tests: for each INFO of the quest with conditions satisfied, simulate "talk to X, choose line, end script effects". Tier 4 gets `manual: true` plus a stub. Use `sqv`/`getstage` console wrappers (via the harness) to read state.

Order of rollout: (1) make extractor + counts (day 1), (2) classifier + report (day 2), (3) generate tests for T0/T1 quests (probably 60%+ of "real" quests) and validate the harness, (4) sample T2/T3, (5) hand-design T4 (intro, MQ cutscenes, DLC intros).

## 11. Open questions

- Exact counts require the game files (not on this machine currently).
- Condition `Run On = Reference 0x14` semantics and FNV CTDA layout: verify against `wbConditions` in wbDefinitionsFNV.pas before trusting PoC.
- DLC load order/formid remap: handle each plugin's `MAST` list; Fallout NV Ultimate Edition ships all DLC as separate ESMs, FormID top byte = master index.
- Ambient dialogue quests (`VFreeform*`) are the bulk of INFO volume and mostly sit at T0-T3 (conversation ownership).
- Companion quests / `SetPlayerTeammate` / `Follow` -> shared companion policy needed.
- Hardcore mode, karma/reputation: per-player or shared policy decision.

## Sources
- https://github.com/TES5Edit/TES5Edit (Core/wbDefinitionsFNV.pas, verified by download)
- https://github.com/Mutagen-Modding/Mutagen (no FNV project found)
- https://github.com/Ortham/esplugin
- https://pypi.org/project/bethesda-structs/
- https://crates.io/crates/espa
- https://www.gamesas.com/libnvs-esp-and-esm-reading-library-now-available-t374308.html
- https://geckwiki.com (function docs), https://github.com/xNVSE/NVSE
