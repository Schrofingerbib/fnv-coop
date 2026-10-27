# In-game test steps (all modules)

Everything in this file is written and unit-tested without the game. These are the exact steps to prove each part in the
real game, collected from the agents that wrote it. Run one game copy at a time unless a step needs two. Every feature is
switched OFF by default in nvcoop.ini; each section says which switch turns it on.

Split-screen steps are in docs/tools/splitscreen.md section 2.

## Step 1-2 Enemy sync: actor-port

1. Setup (one game copy, nothing else needed): nvcoop.ini add [dev] actorport=1 (and leave [actors] engine=1). Load a save standing near an ordinary living NPC (Goodsprings works). Do NOT run a co-op session for this step.
2. Run: python -I tools/actors/proof_actor_port.py --instance 0 --game-dir "<Fallout New Vegas dir>" [--ref Plugin.esm:HEX: a hint for a harmless NPC, used when loaded with 3D, else the nearest loaded character of the cell after waiting --npc-wait seconds (tools/runner/npc_pick.py; --no-fallback refuses)] [--pick Plugin.esm:HEX of another base form to also test the leveled-pick swap]. PASS = exit 0 and the line 'proof_actor_port: all N checks passed'. Exit 3 = not wired (nothing judged), exit 1 prints the failed checks.
3. Read the report first for: place.heading_sign (PASS means SetAngle Z degrees == stored rotZ; a -57.3 reading means the sign is flipped and plugin/actors/engine/script_engine.cpp lines::setAngleZ must negate), wiring.pin.* (must all be true: placeAtMe, getActorValue, isDisabled), console.no_refused_lines (a failure means a console command name/argument is wrong; ForceActorValue limb names such as PerceptionCondition are the likeliest), clone.protect, read.heading_degrees.
4. Also call the bridge method actors.port and note stats.consoleLines / consoleFailed and drive_mean_roundtrip_ms from out/actor-port/proof/report.json (script-line cost per applied drive is unmeasured; the line budget maxLinesPerFrame=24 in nvcoop.ini [actors] may need tuning).
5. Two-copy check (after the single-copy proof passes; scripted, no human step): host + client in one session with [live] actors=1 and [dev] bots=1 on both, both near the NPC that the host owns. Run: python -I tools/actors/proof_actor_port_two_copy.py --host 0 --client 1 --ref FalloutNV.esm:104C70 (a hint: when coop.owner does not know it on both copies or the host does not own it, the first character of the host's cell that both copies know and the host owns is used; prove.py jumps both copies to the Goodsprings scenario first) [--away-ref Plugin.esm:HEX] [--outdir DIR]. It asserts, over bridge calls only: actors.port session=true on both and fed>0 on the non-owner; GetRestrained (exec/eval with the NPC as calling ref) is 0 on the owner's copy and 1 on the other; the host is moved to another cell with player.MoveTo (default the Novac map marker FalloutNV.esm:09289B) and both copies are sampled continuously while coop.owner moves the NPC to the client (epoch +1): GetRestrained flips with it, NO sample shows 0 on both copies (three reads bracket each pair), the NPC never steps more than 350 units between two samples on either copy and the two copies end within 200 units; the host comes back and the end state is judged again; KillActor on the machine that owns the NPC then: GetDead is 1 on both and actors.port stats.deathsRefused did not rise on either; coop.leave on the client and then the host: each copy's actors.port says session=false, the ledger (restrained, standIn, disabled, essential, stale) is all 0, GetRestrained on the NPC is 0 and no actor of world.actors is restrained (--allow-restrained Plugin.esm:HEX for a vanilla captive). A copy that cannot read the NPC (cell not loaded there) is skipped, never counted as 0. Exit 0 pass, 1 failed checks, 2 cannot reach the game, 3 not ready (host does not own the NPC, no session). Run it last in a boot: it ends the session.
6. Save check (ACLG cosave; scripted, no human step), one copy with [dev] actorport=1 and [actors] engine=1 (a session is ended by the script if there is one): python -I tools/actors/proof_actor_port_save_check.py --instance 0 --ref FalloutNV.esm:104C70 --inst-dir <instance dir> [--save-name autosaveaclgcheck]. It restrains the NPC through the port (actors.dev ai off; GetRestrained 1, ledger 1), saves with the bridge saveGame (overwrites only its own save name), ends the session (coop.leave when actors.port says session=true) and empties the live ledger (actors.dev release), loads that save with loadSave and waits until actors.port worldReady and a console eval work again. PASS = the new part of Data/NVSE/Plugins/nvcoop.log has 'actors: cosave of the loaded game added N ledger entries to undo' with N >= 1, GetRestrained on the NPC is 0 within 30 s, actors.port ledger restrained is 0 and stats.consoleFailed did not rise. The log is the only carrier of that line (there is no bridge counter for it), so --inst-dir (or --log) is needed.

## Step 1-2 Enemy sync: combat-core

1. Nothing in synccore/combat can be proven in game by itself, because it has no engine code. The in-game proof is plugin/combat (combat-hooks): run its playbook and proof checker and read docs/tools/combat-hooks.md for the exact commands.
2. What PASS looks like for that proof: a hit on an NPC is applied once by that NPC's owner, and both machines show the same health and crippled limbs. A second player's copy of a projectile does no damage. An explosion damages each actor once. A death is declared once. XP is paid once.

## Step 1-2 Enemy sync: combat-hooks

1. Setup: two copies, host + client in one session. nvcoop.ini: [live] actors=1 avatars=1 combat=1 and [combat] enabled=1 gate=1 native=1. Do not run anything else in the other copy's slot.
2. nvbridge combat.status on both instances. PASS: enabled true, running true, gate.sites == 6, gate.errors == 0, native.pinsOk true, native.faults 0. If gate.sites is 0 or pinsOk is false, the exe differs from the pinned bytes: stop and report.
3. python -I plugin/combat/proof/combat_proof.py snapshot --out out/combat/before.json (instances 0 and 1).
4. Run the fight: python -I tools/bots/run_coop.py fight, or by hand: both players shoot the same raider, one of them kills it, then a raider shoots a player.
5. python -I plugin/combat/proof/combat_proof.py snapshot --out out/combat/after.json, then python -I plugin/combat/proof/combat_proof.py check out/combat/before.json out/combat/after.json. PASS: every line says PASS (engineHits > 0, hitsApplied > 0, no double-applied hit, gate.errors 0, native.faults 0, deaths declared once, xpAwards <= deaths).
6. By eye: after each shot the raider's health is the same on both screens, the shooter sees a hit reaction without the copy dying early, the corpse and loot appear once on both, and the killer's XP arrives once. Compare one kill's XP with vanilla (kill XP is the raw +0x730 value, no tier or perk scaling).
7. Player as victim: let a raider shoot the client's player. PASS: the client's health drops once per bullet and only the client's machine shows combat.status stats.hitsApplied for it (a doubled drop would show hitsApplied doubling).
8. If the game crashes or native.faults > 0: set [combat] native=0 and repeat steps 2-5, which uses the console damage path, and report the crash address.
9. If gate.sites is 6 but no gate.calls appear while shooting, report it: the engine path may use a caller outside the six found (a scan of the image found none).

## Step 1-2 Enemy sync: dll-wiring

1. Default-off check: provision ONE instance with no live.* keys, then `python -I tools/runner/live_wiring_proof.py --instances 0 --expect none`. PASS = switches line reads actors=0 avatars=0 combat=0 spawns=0, every lane service disabled, coop.owner answers known:false, and nvcoop.log has only the 'live: switches ...' line from the live layer.
2. Actors: provision a host and a client in one co-op session with `python -I tools/runner/run.py provision --n 2 --ini coop.enable=1 --ini live.actors=1` (plus the usual role/port keys), then `python -I tools/runner/live_wiring_proof.py --instances 0,1 --expect actors`. PASS = every check prints PASS, both sides are in a session, and actors.port reports session:true.
3. Avatars: add `--ini live.avatars=1`, then `python -I tools/runner/live_wiring_proof.py --instances 0,1 --expect actors,avatars`. PASS = each side lists the other in its roster, has a nonzero puppet ref, snapshots accepted, forged == 0. Then `... --load <savename>` makes the client load: hub.loads rises by 1, and the puppets are back within 30 s.
4. Everything on: add `--ini live.combat=1 --ini live.spawns=1`, then `--expect actors,avatars,combat,spawns`. PASS = combat.status says running and coop.spawn answers.
5. Dependency rule: set only `live.combat=1` and call coop.live. PASS = switches shows combat=0 and warnings says it needs actors=1.
6. World query fix: `world.query {fn:"GetActorValue", ref:<any actor>, p1:16}` should now return the Health value (and GetItemCount with a form), with [live] switches on and with them off.
7. Hot reload / unload: after a session with avatars on, run dev.reload (or exit to menu); puppets must be gone and nvcoop.log must show no crash. On any failure send both coop.live answers and the tail of nvcoop.log.

## Step 1-2 Enemy sync: enemy-anim

1. Prepare: in nvcoop.ini set [dev] animprobe=1 and [anim] enabled=1 (optional: directknock=1 and directrate=1 for the optional steps). Load a save with an ordinary NPC standing near the player, and give that NPC a weapon with additem.
2. Run: python -I tools/animcheck/npc_proof.py caps --instance 0 --ref FalloutNV.esm:<npc id> --weapon   (add --kill only on a throw-away save). PASS: every line is [PASS], [INFO] or [SKIP], and the last line reads 'npc_proof caps: N steps, 0 FAIL'. It proves: the pinned direct calls exist, PlayGroup works on a restrained actor and the xNVSE numbering matches (Forward 3, FastForward 7, Left 5, TurnRight 16), hold versus non-hold, no T-pose after Idle, weapon draw and holster read back, aim flag, AttackRight 32 and Stagger 223 read back as actionGroup, and with --kill: KillActor reads dead, SetPos moves the body and it stays put. Send the [INFO] lines back (AnimData slot numbers, unsupported optional features).
3. Two-game proof: start a co-op session with NPCs the host owns (the fight scenario), run the fight playbook, and at the same time: python -I tools/animcheck/npc_proof.py sync --host 0 --client 1 --seconds 60 --out out/npc/run1. PASS: 'npc_proof sync: OK'; in the printed per-machine anim.npc lines refused and weapon_ladder_giveups are close to 0, and attack_missing, attack_extra, weapon_never_drawn, corpse_wrong_place and the eight glitch counters are 0.
4. Quick look at the live counters at any time: nvbridge call anim.npc (stats, pin status). With [dev] animprobe=1 also anim.npc.do {ref, op: read|play|weapon|aim|sneak|rate|knock|getup|kill|place|release} to try one primitive by hand.
5. Watch for two failure signs while fighting: a stray projectile or a hit on the local player coming from a remote copy that plays an attack (the animation's text keys might fire the weapon), and a copy standing in a T-pose after a menu closes.

## Step 1-2 Enemy sync: fight-test

1. Build and provision two instances, then start the pool: cmd /c tools\runner\build_l1.cmd ; python -I tools/runner/run.py provision --n 2 --ini coop.enable=1 --ini dev.bots=1 --ini dev.animprobe=1 ; python -I tools/pool/pool.py up --size 2. PASS: both copies form one co-op session (coop.status on instance 0 says role host, on instance 1 role client with client.joined true).
2. python -I tools/bots/run_fight.py --instances 0,1 --preflight-only. PASS: exit 0 and every REQUIRED method (coop.status, coop.owner, ref.info, dev.countRefs, anim.probe, bot.status) shows 'present'. Exit 3 means BLOCKED: it lists the missing methods and stops. 'actors.port ... enabled=True session=True fed=<n>' per instance shows the actor adapter is feeding ActorSync. coop.combat, combat.status, anim.npc, dev.refsOf and flight.dump are optional (the checks that need them are skipped with a note).
3. python -I tools/bots/run_fight.py --instances 0,1 --which fight --outdir out/fight-run1. PASS: 'PASS  enemy_fight  8 steps', exit 0. If step 2 fails with 'do not know this NPC's owner', the actor adapter is not in the build yet: report it and stop.
4. python -I tools/bots/run_fight.py --instances 0,1 --which handoff --outdir out/fight-run1. PASS: 'PASS  enemy_handoff  7 steps' (the host walks to Novac, so this one is slow).
5. python -I tools/bots/run_fight.py --instances 0,1 --which spawn --outdir out/fight-run1. PASS: 'PASS  enemy_spawn' (the host places 3 gang NPCs with the console 'placeatme' at the gang marker, UNVERIFIED).
6. python -I tools/bots/run_fight.py --instances 0,1 --which loot --outdir out/fight-run1. PASS: 'PASS  corpse_loot_race  6 steps'.
7. python -I tools/bots/run_fight.py --instances 0,1 --keep-going --outdir out/fight-run2 (all four in order). PASS: four PASS lines, exit 0, summary.json in the output dir. Each test also writes anim_report.json and engine_stats.json. A failure saves FAIL-<n>-<step>/ (failure.json, snapshots, events, flight dumps); run python -I tools/flight/flight.py diverge on the dumps.
8. Report per test: PASS or the failing step and reason. Also answer the UNVERIFIED lines: does 'player.setav Health 5000' work; does additem on a living ganger replicate to the guest and survive his death; can the VCG02 geckos be enabled by console; does placeatme spawn work for the spawn test; is the host's walk to Novac enough to leave the loaded area; does Bot.pos work on an ACRE. For each failure decide whether the game or the test is wrong. Fix the test or scenario and add a regression test; report game-side desyncs as issues instead of editing Track A code.
9. Optional later: repeat with --chaos bad_wifi --chaos-seed 7 --chaos-rpc runs/chaos.json. Thresholds you may need to tune from a clean run (all unmeasured): pos_tol 150 (pb_enemy.DEFAULT_POS_TOL), tools/animcheck/config.py FIGHT.state_grace_ms 250, FIGHT_NPC_SPEC in tools/bots/playbooks/animwatch.py. Loosen one only with a written reason.

## Step 1-2 Enemy sync: spawn-arb

1. Precondition: 2 or 3 instances in one co-op session (host = instance 0), standing within about 500 units of each other in an exterior cell, with `[live] spawns=1 actors=1` and `[actors] engine=1`. The base is a hex FormID (default 0001CF7C, CrRadroach): the bridge console does not resolve editor ids, and FalloutNV.esm has no creature with the EditorID RadRoach.
2. Run: python -I plugin/spawn/tools/spawn_proof.py --instances 0 1 2 --base 0001CF7C --type 60 --respawn. PASS looks like: every step prints PASS, the summary line reads '<n> checks passed, 0 failed', exit code 0.
3. Step 1 PASS: coop.spawn answers on every instance, link true, same digest.
4. Step 2 PASS: after one client runs player.PlaceAtMe, every instance has exactly one more actor, one new Spawned site with the same owner, epoch and 1 member.
5. Step 3 PASS: two clients roll the same spot at the same moment, and every instance still shows the same number of new actors, at most one.
6. Step 4 PASS: after Kill on the owner's actor, the site is Cleared on every instance.
7. Step 5 PASS: a client rolls again, no instance gains an actor, the site stays Cleared, and some machine's coop.spawn stats show rollsSuppressed >= 1.
8. Step 6 PASS (--respawn): the host's GameDaysPassed is moved on 4 days, the site becomes Respawnable everywhere, and the next roll gives +1 actor on every instance.
9. If step 2 FAILs with 'no actor appeared', the guest hook refused console PlaceAtMe: walk into a real random encounter instead. The rest of the script is unchanged.
10. By hand on each instance: bridge call coop.spawn {} (link true, equal digest), coop.spawn {"list":true} (same state, owner, epoch, members per site), coop.owner {"ref":"fnvcoop.created:FFC00001"} for an issued id.
11. Report the stats object from coop.spawn {} on every instance: rollsHeld, rollsSuppressed, rollsAdopted, rebaseFailed, healsSent and tracker.droppedBound. Healthy: healsSent 0, digests equal, pending 0, waiting 0, candidates 0 after settling.

## Step 3 Player avatars: avatar-port

1. Build and deploy nvcoop.dll as usual (python -I tools/build/build.py --target nvcoop, copies to out/L1/nvcoop.dll). The avatar module is compiled in by default and inert unless the ini turns it on.
2. In nvcoop.ini set: [avatar] enabled=1 ; [dev] animprobe=1 and bots=1. (template=player is the default: the puppet is made from the player's own base form, which needs no ESP but is UNVERIFIED to work with PlaceAtMe.) Provision with python -I tools/runner/run.py provision if the ini is regenerated by provisioning.
3. Start ONE game instance, load a save in an open area with ~15 m of free floor in front of the player and a weapon in the inventory. Check nvcoop.log contains 'avatar: enabled ([avatar] enabled=1)' and then 'avatar: runtime created'.
4. nvbridge avatar.status. PASS looks like: running:true, enabled:true, backend.consoleOk:true and every backend.pins[].ok:true (if any pin is false the exe differs from the reference 1.4.0.525: stop, run python -I tools/avatar/verify_engine_pins.py).
5. nvbridge avatar.loopback {"on":true,"offset":[160,0,0]}, then avatar.status again. PASS looks like: puppets[0].phase == "Driving", stats.spawnFailures 0, engine.spawnNoNewRef 0, engine.foreignRefused 0, engine.linesException 0. Look in game: a copy of the player 2.3 m to the side, restrained, copying the player's moves about 100-150 ms late. Note engine.costP50Us/costP95Us (cost of a console line) and engine.maxDrift3D (does SetPos move the body).
6. Run the proof: python -I tools/avatar/proof_puppet.py --loopback --out out/avatar/run1 . PASS looks like 'avatar counters: ...' with avatar.glitches=0, avatar.compared>=200, avatar.pose_err_p95<=12, avatar.pos_err_p95<=25, then 'PASS' and exit 0. Exit 3 means not ready (nothing judged: layer off, pin mismatch, no Driving puppet, or no bot input driver). Exit 1 prints 'FAIL <counter>' lines; report.json and report.html are in out/avatar/run1.
7. If it fails or looks wrong, send back: out/avatar/run1/report.json, the nvcoop.log 'avatar:' lines, and avatar.status after the run. The first things to read are engine.spawnNoNewRef (PlaceAtMe of the template did not produce a findable ref), engine.linesRefused/linesException (the console refuses commands on a restrained actor), engine.drift3D (SetPos moves the ref but not the body), avatar.wrong_weapon (SetAlert does not draw the weapon), and avatar.anim_mismatches / glitch.sliding (group numbering or speed matching).
8. Optional with no network: avatar.add {"slot":2} then avatar.feed {"slot":2,"x":..,"y":..,"z":..,"yaw":0,"vy":300,"loco":5} every 50 ms from a script to drive a synthetic remote player; avatar.loopback {"on":false} and avatar.remove {"slot":2} clean up. Before a save you start yourself call avatar.presave then avatar.postsave (the module cannot see an engine-started save coming); load a save afterwards and check avatar.status tagged equals the number of live puppets (no orphans).

## Step 3 Player avatars: avatars

1. Provision two instances: python -I tools/runner/run.py provision --n 2 --ini coop.enable=1 --ini live.actors=1 --ini live.avatars=1 --ini live.combat=1 --ini avatar.enabled=1 --ini dev.bots=1. Run one game pair at a time; both in an exterior with about 120 m of free ground along +X in front of the client.
2. The client's loadout is scripted: the look phase (with --loadout auto, the default) gives the client ArmorLeatherReinforced (FalloutNV.esm:1264FE) and the Trail Carbine (FalloutNV.esm:0CD539) with player.additem + player.EquipItem, sets mod slot 1 (the scope, ModNVTrailCarbineScope FalloutNV.esm:0EEA70) with the xNVSE function player.SetEquippedWeaponModFlags 1 and reads it back (GetEquipped, GetEquippedWeaponModFlags, avatar.look). Override with --armor / --weapon / --weapon-mod / --mod-flags, or pass --loadout off if the client already holds the gear. A non-default height or hair is optional.
3. Run: python -I tools/avatar/proof_avatars.py --host 0 --client 1 --interior <interior cell edid> --exterior <exterior cell edid> --save <save name> --enemy-ref <hex ref of a hostile actor the host owns>. Phases wiring, look, rates, lifecycle and hits can also be run on their own with --phases.
4. Read the look phase line 'WEAPON MODS read back from the engine equal the wanted ones': it decides whether the EnableWeaponMod hypothesis holds. The 'SetScale was sent' and 'no console exception' lines come right after it.
5. In the rates phase the client is teleported 60 m away and back. Expect 15 to 24 Hz near, 3.5 to 7.5 Hz far, near again.
6. avatar.status engine counters to watch: costP95Us, scaleApplied, modLines, appearanceApproximate, foreignRefused (must be 0). avatar.look on the client shows how many face coefficients validated; zero means the FaceGenData layout guess is wrong.
7. Do not edit the owner's Documents\My Games INIs or saves; use instance-local INIs only.

## Step 4 Full combat: companions-crime

1. Do not run before the wiring below exists. dll-wiring: add a SocialService in plugin/coop/live/live_glue.cpp next to CombatService (switch [live] social=1, needs actors=1): tick() calls nvcoop::social::glueFrame(g_rt), sessionEnd() calls glueFrame(nullptr). Call glueAllowActivation(slot, target) before running a guest's ActivateRef intent and glueGuestInteraction(slot, pickup, target) after a guest's ActivateRef or PickUp ran.
2. Combat, vats and avatars (one call each): combat calls glueReportKill, glueReportCrime and glueHitPolicy, and skips its own awardXp when glueXpHandled() is true; vats calls glueVatsTargetAllowed before offering a target; avatars call setPlayerRefMap. Exact snippets are in plugin/companions/companions_glue.h and docs/tools/companions-crime.md.
3. Two or three copies in one session with nvcoop.ini [live] actors=1 avatars=1 combat=1 social=1 and [companions] enabled=1 dev=1 xpmode=1. Run nvbridge social.status on each: running true, synced true on the guests, companionDigest and bountyDigest equal on all.
4. python -I plugin/companions/proof/social_proof.py snapshot --out out/social/before.json --instances 0,1,2
5. Companions (scripted, no human; items 5 and 6 are one command, item 6 below): `drive` recruits Boone (FalloutNV.esm:096BCE) for guest 1 through the bridge: the host enables him, moves him to its player and runs exec.ref SetPlayerTeammate 1 with scope=true and acting={ghost: guest 1's puppet from coop.status playerRefs.slots, slot: 1}, which the capture hook records as a CompanionOwner effect for slot 1. It asserts social.status companions=1 on every machine, social.dev {op:'cmd', kind:1, npc:Boone} on guest 1 makes stateApplied 1 there, and the same command on guest 2 is refused (cmdRefused +1, stateApplied unchanged). teammateUnset on the host is printed as info.
6. Crime (same command): `drive` places an NCR trooper (eldoradoNCRTrooper1, FalloutNV.esm:0E7E21) next to guest 1's puppet with placeatme run by the host, finds the new reference with dev.refsOf and reads its net id with coop.owner. Run: python -I plugin/companions/proof/social_proof.py drive --faction FalloutNV.esm:0A46E7 --instances 0,1,2 (0A46E7 = NCRFactionNV). The faction flags (GetPCFactionAttack and the other two) must agree on every instance. Passing --npc / --victim-net keeps the old manual drive (nothing is recruited or placed).
7. python -I plugin/companions/proof/social_proof.py snapshot --out out/social/after.json, then check out/social/before.json out/social/after.json --crimes 1 --kills 1 --alarms 1 --xp-grants 1. Every line must say PASS.
8. Watch for: kill XP arriving once on the killer's screen, karma moving once on every screen, guards reacting once. If engine_evalFaults is not 0 the GetDetected / IsTrespassing calling convention is wrong: set [companions] crime=0 and report it. If teammateFailed is not 0, report the console output.

## Step 4 Full combat: vats

1. Two copies, host + client in one session. nvcoop.ini on both: [live] actors=1 avatars=1 combat=1, [dev] bots=1, [vats] enabled=1 (leave pausesites=1 first).
2. nvbridge vats.status on both: pins must all be true and hooks sites must read 11 / 5 / 9 / 2 (time_scale / player_mult / target_mult / pause_main). A false pin or a group with 0 sites means this exe differs: stop and report.
3. python -I plugin/vats/proof/vats_proof.py snapshot --out out/vats/before.json
4. python -I tools/bots/run_vats.py --instances 0,1 --weapon gun=<item> --weapon melee=<item> --weapon explosive=<item> --weapon energy=<item> [--neighbor Plugin.esm:HEX] (add --slowmo once). The weapon items have no default on purpose: choose one real weapon per class. The explosive class needs the neighbour ref for its blast check.
5. python -I plugin/vats/proof/vats_proof.py snapshot --out out/vats/after.json, then check out/vats/before.json out/vats/after.json --expect-autofire --expect-hooks-fired [--expect-slowmo]. Every line must PASS.
6. Scripted, no hand step: python -I tools/bots/run_vats.py --instances 0,1 --playbook vats_world_moves (no --weapon needed: it uses the weapon the player already holds, or --weapon any=<item>). It measures, over bridge calls and bot.input only: (a) while one player sits in the VATS menu, the OTHER copy's game clock (worldtime.clock, else the GameHour / TimeScale globals) and the Powder Ganger's position on that copy (it chases a player the bot runs away from it) keep moving, against a baseline measured with nobody in VATS; (b) the VATS player does not move on bot.move while the menu is open and does after leaving it; (c) the limb chosen with vats.select_limb reads back as highlighted in vats.state and is the limb whose condition drops when the shot lands. The kill cam freeze is covered by the class playbooks of item 4. Every line must PASS; a baseline that does not move (no chase, TimeScale 0) is a FAIL, not a skip.
7. If the world still freezes in VATS: set [vats] pausesites=2 (nine more MenuMode() sites) and retry. realtime=0 gives vanilla VATS for comparison. If a crash: [vats] enabled=0 and report the address.
8. Read the Grim Reaper's Sprint perk text in game, then set [vats] grs_ap (default 20 is a placeholder) and grs_edid (default GrimReapersSprint is an unverified editor id; the log says once if the lookup fails). Confirm the party slow-mo feel with partyslowmo=1 on the host (host-only setting, it syncs to clients).

## Step 5 Loot and containers: loot

1. Precondition: 2 or 3 game instances in one co-op session standing next to a lootable container; nvcoop.ini on every instance has `[loot] enabled=1` and `[intents] pickup=0 container=0`; dll-wiring's one line from plugin/loot/loot_glue.h is in plugin/coop/live/live_glue.cpp.
2. Run `python -I plugin/loot/tools/loot_proof.py --instances 0 1 2 --container FalloutNV.esm:HEX --item Stimpak`. It closes the container menu itself (bridge method bot.menu.close, needs [dev] bots=1), so no step waits for a person. PASS means every step prints PASS and the exit code is 0.
3. By hand: `coop.loot {}` on each instance (session true, same digest); `coop.loot {"container": <net id>}` for holder, revision and digest; watch the `loot.notice` events.
4. Report `coop.loot {}` from every instance (client.rollbacks, client.busy, client.claimsLost, hostStats.closeMismatch, tick.actionsFailed) and the log lines starting `loot:`. Any actionsFailed or a non-zero closeMismatch in a clean run is a finding.

## Step 6 World, time and travel: world-time

1. Build the DLL: python -I tools/build/build.py --target nvcoop (artifact in out/L1). Set these in every instance's nvcoop.ini: [worldtime] enabled=1 and [dev] bots=1 ([coop] with dialogue enabled and rules loaded is needed for the party question). Set NVCOOP_TEST_HELPERS=1 in the game processes. Optionally set travel=party.
2. Start 2 or 3 instances in one co-op session (host = instance 0) near each other, then run: python -I plugin/world_time/tools/worldtime_proof.py --instances 0 1 2 --door falloutnv.esm:XXXXXX [--party-travel falloutnv.esm:YYYYYY]. Pick a closed, unlocked door in the player's cell for --door. Results go to out/world-time/proof.json.
3. Step 1: worldtime.status on every instance must show enabled, a session, pins true, and the host's hooked=true (the pending-travel detour installed). Clients must have received beacons.
4. Step 2: the host writes its clock +5 h and every client must follow within 3 game minutes; a client that writes +3 h must be pulled back.
5. Step 3: a client's wait is vetoed, the asker presses Yes (bot.menu.press button 1), the others answer Ready (bot.consent). All clocks must move about 120 minutes exactly once and the host skipsExecuted must go up by 1.
6. Step 4 (three instances): same question with one Not yet. No clock moves and the host sends a refusal.
7. Step 5 (--door): console Activate on the host's door (a native change, not SetOpenState). Every instance must show the same open state and the host's objects.emitted must be at least 1.
8. Step 6: autosavesOff must be true on clients and false on the host. Read bSaveOnTravel back with GetINISetting to confirm the SetINISetting name and quote syntax actually took effect.
9. Step 7 (--party-travel): a client's trip is vetoed and, after the answers, every instance starts exactly one trip. This also confirms the re-issued travel object works without the map menu.
10. Manual extras: lockpick a chest on the host and compare world.query GetLocked on a client; open a door on a client and watch it follow the host; load a save on the host and check worldtime.clock on the client; open the real Wait/Sleep menu and confirm it closes and the party question appears.
11. Ownership hooks: on the host run console SetOwnership <actorbase> on a ref, ClearOwnership, SetCellOwnership and SetCellPublicFlag, then check world.drain and the client's behaviour. If a hook never fires, check its status in world.status (prologue mismatch is skipped, not forced).

## Step 7 Desync check and repair: area-desync

1. In plugin/coop/coop_module.cpp, after the nvcoop::actors::glueFrame(g_rt.get()) call, add the #ifdef NVCOOP_WITH_AREADIGEST block that calls nvcoop::areadigest::glueFrame(g_rt.get()) (exact text in plugin/areadigest/area_glue.h), then rebuild nvcoop.dll.
2. Start 2 instances in one co-op session near the same NPC with nvcoop.ini [live] actors=1 and [areadigest] enabled=1 on both (or env NVCOOP_AREADIGEST_ENABLED=1). One game launch at a time; the PC is shared and low on RAM.
3. Confirm bridge areadigest.status on each instance says enabled and running with role host/client.
4. Run python -I plugin/areadigest/tools/area_proof.py --instances 0 1 --ref <Plugin.esm:HEX of an NPC the host owns>. Expected: 30 s quiet with no counter moving, then the client with a wrong health value gets the owner's value within 25 s with exactly one heal on that client, then 20 s stable.
5. Print the dashboard line from the status JSON or flight dumps with python -I tools/areadigest/dashboard.py status <files> or nvfr <files>; it must report 0 heals and 0 bug lines.
6. Check the three in-game unknowns: whether the actor port's per-frame drive overwrites a direct setPos on a non-owned actor, whether loadedActors plus netIdOf cover the actors both machines have loaded, and map setOwnerResyncHook to ActorSync::requestResync so death heals work.
7. Re-run the existing fight, handoff and loot playbooks and confirm the areaHeals desync counter stays 0.

## Step 8 Main story: guest-intents

1. Build and deploy nvcoop.dll to three instances (host and two guests) with nvcoop.ini [coop] enable=1 baseline=1 dialogue=1, [live] avatars=1 actors=1 on all three, and [intents] enable=1 on all three. Optionally add nvcoop_intents.json for storage redirects. Remove forward_activate=1: the intents observers replace it.
2. Run: python -I plugin/intents/tools/intents_proof.py --instances 0 1 2 --door <ref> --item <ref> --item-base <hex> --container <ref> [--terminal <ref> --entry 0] [--trigger <ref>]. Its step 0 stands all three players within 400 units of the door, the item and the container by itself (ref.info of the three references, then player.MoveTo the anchor reference and player.SetPos X/Y on every instance, then ref.info again; it retries 3 times and stops with the distances if a player stays out of range, or if the three references are too far apart for one standing point). --no-position skips it, --radius N changes the 400.
3. Check step 1 first: intents.status must show pinsOk, canSaveHook and service.up true on every instance. If pinsOk is false, stop and read the nvcoop log line 'pin MISMATCH'.
4. Step 2: in intents.status on the host, actedAs.puppet must rise by 1. If standIn rose instead, the avatar layer did not give that guest a puppet, so check [live] avatars=1 and the glueAvatarRef map.
5. Step 7: CanSaveNow must return false on every instance while SaveSuppress is on, and the game's own answer must be back when it is off. It only proves the CanSaveNow function itself, so also try a manual save and an autosave once by hand, in an instance-local save directory.
6. Dialogue results: with dialogue=1 on the host, run a guest conversation (the guest half is not wired, so drive it via coop's dialogue bridge or dlg events) and watch intents.status service.dialogueResults on the host. 'unsupported' means L5's TopicInfoRunResult hook was not installed.
7. Terminal: the TerminalEntryRun hook installs on the first guest session. After a real terminal pick on a guest, host intents.status byKind.TerminalUse should rise. If it shows Unsupported or Failed, the 0x501830 argument meaning needs a real check.
8. Verify ref.info on a disabled item shows flags & 0x800 (and 0x20 for a deleted one). If not, change kGoneFlags in game_engine.cpp.

## Step 8-9 Story, side quests, DLC: questline-sweeps

1. Start N (2 to 4) co-op instances joined as one session on a DISPOSABLE save, with [dev] bots=1 and NVCOOP_TEST_HELPERS=1 so test.quest.reset and the bot bridge methods exist.
2. Smoke test first: python -I tools/questsweep/run_questlines.py --line goodsprings --instances 0,1 (console stage setting only, no bot actions). Expect VCG00 and VCG01 to pass their barrier at every stage, because their Barrier rule has no stages list.
3. Then the small lines: --line yesman, --line prologue, --line house. yesman exercises the ready check on VMQYesMan03 100 and the exclusive group on VMQYesMan01a.
4. Try dialogue triggers with --natural (speaker refs are guesses; see trigger.speaker_ref in the plan) and bot actions with --actions. Actions can hit essential NPCs, so do not use a real save.
5. Per-player variable and ready-check answer steps are skipped with a note when the bridge methods coop.playerVar.set/get and coop.consent are missing. Report which ones are missing.
6. Any failed step saves FAIL-<n>-<step>/ under out/questsweep/<stamp>/<playbook>/ for tools/flight. Hand those back; do not loosen a check.
7. Then DLC lines (deadmoney, honesthearts, owb, lonesomeroad), side lines, companions, with --include-excluded only on a save the lane marked disposable.

## Step 8 Main story: story-capture

1. On every machine set nvcoop.ini [hooks] story_capture=1 (default 0). Start a host and a guest, then call hooks.status on each: the extra hooks must all say installed (none PrologueMismatch).
2. python -I plugin/hooks/tools/proof_story_capture.py --host 0 --guest 1 (written, never run). It runs EvaluatePackage, SetGhost 1 / 0 and SetScale 1.25 / 1 on an actor in a capture scope, checks the RefCommand effects, applies SetScale on the guest and reads it back with GetScale.
3. On a guest that does not own an actor, apply an AI order such as StartCombat to it: expect NotForMe, and the owner's machine to perform it. Needs the actors engine port in the build (story_owner_gate.cpp); without it every machine performs every order.
4. Trigger a host script that calls Player.AddSpell and one that calls PlayGroup: the first must reach the guest according to the reward rule, the second must show up on the guest through the actor snapshots.
5. Move a placed NPC to a cell the guest has not loaded: apply must answer Ok (parked), and after the guest enters that cell the NPC must be at the host's position within about a second.
6. Run a long session and watch unmapped().storyDropped: commands whose calling reference or form has no stable name are dropped, not guessed.

## Step 10 Release: release

1. On a second PC (or a clean copy of the game), unzip the release and run Check.cmd, then Install.cmd. Confirm it finds the game, reports 1.4.0.525 and xNVSE, and copies the files into Data\NVSE\Plugins.
2. Run Launcher.cmd on PC A, pick Host, press Check port and Start test listener. On PC B press Test connection with PC A's address; it should report 'answered'. Stop the listener.
3. On PC A press Start game as host and load a save. On PC B press Join with PC A's address and Start game. Check nvcoop.log on both for the 'coop: started role=...' line and for a handshake result or a 'JOIN REJECTED' text.
4. Tick 'Share kill XP' and confirm [combat] xpmode=1 lands in nvcoop.ini and the combat module logs it. Confirm [party] slowmo and tether are written, and note that no code reads them yet.
5. Run Uninstall.cmd on both PCs. Confirm nvcoop.dll is restored or removed, the previous nvcoop.ini is back, FNVCoOp is gone, and saves are untouched. Run Check.cmd afterwards to confirm 'not installed'.
6. Before publishing any zip, check the destination account and URL, grep the zip for the owner's name, and publish only under the co-op identities.
