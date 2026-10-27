// Tests for the world-state additions of fixer P5 (G13 relation flags, G20 object state copy, G39 presentation). Included by test_world.cpp.
#pragma once

#include "world_present_addrs.h"

TEST(present_specs_match_the_static_dump) {
    std::string dir = NVW_DUMP_PATH;
    dir = dir.substr(0, dir.find_last_of("/\\") + 1);
    auto rows = readDumpFile(dir + "present_prologues.txt");
    CHECK(rows.size() == 4);
    CHECK(world_present_addrs::kCount == 4);
    for (size_t i = 0; i < world_present_addrs::kCount; ++i) {
        nvhooks::HookSpec s = *world_present_addrs::kAll[i];
        if (FNV_MUT(64)) s.expected[3] ^= 0x01;   // MUTANT 64: a spec that drifted from the evidence
        const DumpRow* r = nullptr;
        for (auto& x : rows) if (x.name == s.name) r = &x;
        CHECK(r != nullptr);
        if (!r) continue;
        CHECK(r->addr == s.target);
        CHECK(r->stolen == s.stolen);
        CHECK(std::memcmp(r->bytes.data(), s.expected, 16) == 0);
        CHECK(s.stolen >= 5 && s.expected[0] == 0x55 && s.expected[1] == 0x8b && s.expected[2] == 0xec);
        CHECK(s.retBytes == 0);
    }
}

TEST(present_effects_lines_json_plan_and_no_replay) {
    Present fade;
    fade.kind = PresentKind::ApplyImageSpace;
    fade.form = esm(0x1234);
    fade.hasValue = true;
    fade.value = 0.5f;
    Present off;
    off.kind = PresentKind::RemoveImageSpace;
    off.form = esm(0x1234);
    Present music;
    music.kind = PresentKind::PlayMusic;
    music.form = esm(0x77);
    Present snd;
    snd.kind = PresentKind::PlaySound;
    snd.form = esm(0x88);
    snd.hasValue = true;
    snd.value = 1;
    CHECK(presentLine(fade, 0x1234) == "ApplyImageSpaceModifier 00001234 0.5");
    CHECK(presentLine(off, 0x1234) == "RemoveImageSpaceModifier 00001234");
    CHECK(presentLine(music, 0x77) == "PlayMusic 00000077");
    CHECK(presentLine(snd, 0x88) == "PlaySound 00000088 1");
    for (WorldEffect e : {WorldEffect(fade), WorldEffect(off), WorldEffect(music), WorldEffect(snd)}) {
        WorldEffect back;
        std::string err;
        CHECK(worldEffectFromJson(worldEffectToJson(e), &back, &err));
        CHECK(back == e);
        CHECK(std::string(worldEffectName(e)) == "Present");
        CHECK(isEphemeral(e));                                 // twin 59
        CHECK(worldEffectKey(e).empty());
    }
    WorldEffect bad;
    std::string err;
    CHECK(!worldEffectFromJson("{\"type\":\"Present\",\"kind\":9,\"form\":\"falloutnv.esm:000001\"}", &bad, &err));
    // plan: a console line without a calling reference; a missing form is reported
    PlanFx f;
    auto p = planWorldEffect(fade, f.env);
    CHECK(p.status == PlanStatus::Ok && p.steps.size() == 1 && p.steps[0].kind == StepKind::ConsoleGlobal && p.steps[0].line == "ApplyImageSpaceModifier 00001234 0.5");
    Present gone = music;
    gone.form = FormRef("nosuchmod.esp", 1);
    CHECK(planWorldEffect(gone, f.env).status == PlanStatus::FormMissing);
    // a late joiner is not shown yesterday's fade: the baseline never keeps it, the state effects stay
    WorldBaseline b;
    b.onWorld(fade);
    b.onWorld(snd);
    b.onWorld(SetIgnoreCrime{esm(5), true});
    CHECK(b.worldEntries() == 1);
    CHECK(!isEphemeral(WorldEffect(SetIgnoreCrime{})));
}

TEST(relation_flags_travel_with_the_effect_and_into_the_line) {
    // SetEnemy A B 1 1 was captured, its two integers parsed and dropped: the other machines ran SetEnemy A B
    CHECK(setRelationLine(Relation::Enemy, 0x10, 0x20) == "SetEnemy 00000010 00000020");
    CHECK(setRelationLine(Relation::Enemy, 0x10, 0x20, 1, 1) == "SetEnemy 00000010 00000020 1 1");   // twin 61
    CHECK(setRelationLine(Relation::Ally, 0x10, 0x20, 0, 1) == "SetAlly 00000010 00000020 0 1");
    SetRelation r{esm(0x10), esm(0x20), Relation::Enemy};
    r.flags1 = 1;
    r.flags2 = 1;
    WorldEffect back;
    std::string err;
    CHECK(worldEffectFromJson(worldEffectToJson(WorldEffect(r)), &back, &err));
    CHECK(back == WorldEffect(r));
    CHECK(std::get<SetRelation>(back).flags1 == 1 && std::get<SetRelation>(back).flags2 == 1);
    // an older sender (no flags in the JSON) means "no flags"
    CHECK(worldEffectFromJson("{\"type\":\"SetRelation\",\"a\":\"falloutnv.esm:000010\",\"b\":\"falloutnv.esm:000020\",\"kind\":1}", &back, &err));
    CHECK(std::get<SetRelation>(back).flags1 == 0 && std::get<SetRelation>(back).flags2 == 0);
    PlanFx f;
    auto p = planWorldEffect(WorldEffect(r), f.env);
    CHECK(p.status == PlanStatus::Ok && p.steps.size() == 1 && p.steps[0].line == "SetEnemy 00000010 00000020 1 1");
    // the baseline keeps the flags a joiner replays
    WorldBaseline b;
    b.onWorld(r);
    auto snap = b.snapshot();
    CHECK(snap.world.size() == 1 && std::get<SetRelation>(snap.world[0]).flags2 == 1);
}

TEST(a_scripts_lock_change_also_travels_as_a_world_effect_a_joiner_replays) {
    // The lab door: the terminal's result script ran getLinkedRef.unlock on the host. The ObjectState txn is the first copy; the world copy does not
    // depend on any state filter and is replayed to a client that joins later.
    fnvcoop::ObjectState os;
    os.ref = esm(0x842);
    os.mask = fnvcoop::kObjLock;
    os.locked = false;
    auto w = worldCopyOfObjectState(os, 0);
    CHECK(w.has_value());                                      // twin 62
    if (!w) return;
    CHECK(w->ref == esm(0x842) && w->mask == fnvcoop::kObjLock && !w->locked);
    CHECK(!worldCopyOfObjectState(os, kObjectPollAux).has_value());   // twin 63: the poll's own change is not repeated
    fnvcoop::ObjectState none;
    none.ref = esm(1);
    none.mask = fnvcoop::kObjActivated;                        // not a bit the world copy carries
    CHECK(!worldCopyOfObjectState(none, 0).has_value());
    // wire
    WorldEffect back;
    std::string err;
    CHECK(worldEffectFromJson(worldEffectToJson(WorldEffect(*w)), &back, &err));
    CHECK(back == WorldEffect(*w));
    CHECK(!worldEffectFromJson("{\"type\":\"SetObjectState\",\"ref\":\"nvcooplab.esp:000842\",\"mask\":0}", &back, &err));
    // coalescing key and log: the second lock change of the same door replaces the first, an open change is another key
    SetObjectState lockAgain = *w;
    lockAgain.locked = true;
    lockAgain.level = 50;
    SetObjectState open = *w;
    open.mask = fnvcoop::kObjOpen;
    open.open = true;
    CHECK(worldEffectKey(WorldEffect(*w)) == worldEffectKey(WorldEffect(lockAgain)));   // twin 60
    CHECK(worldEffectKey(WorldEffect(*w)) != worldEffectKey(WorldEffect(open)));
    WorldLog l;
    l.push(*w);
    l.push(lockAgain);
    l.push(open);
    auto v = l.drain();
    CHECK(v.size() == 2);
    // baseline: a client that joins after the unlock is told (net state, last write wins)
    WorldBaseline b;
    b.onWorld(lockAgain);
    b.onWorld(*w);
    b.onWorld(open);
    auto snap = b.snapshot();
    CHECK(snap.world.size() == 2);
    bool sawUnlock = false;
    for (auto& e : snap.world)
        if (auto* s = std::get_if<SetObjectState>(&e))
            if (s->mask == fnvcoop::kObjLock && !s->locked) sawUnlock = true;
    CHECK(sawUnlock);
    // plan: the same console forms and order as the ObjectState effect
    PlanFx f;
    auto p = planWorldEffect(WorldEffect(*w), f.env);
    CHECK(p.status == PlanStatus::Ok && p.steps.size() == 1 && p.steps[0].kind == StepKind::ConsoleOnRef && p.steps[0].refID == 0x842 && p.steps[0].line == "UnLock");
    SetObjectState both = open;
    both.mask = static_cast<uint8_t>(fnvcoop::kObjLock | fnvcoop::kObjOpen);
    both.locked = true;
    both.level = 25;
    p = planWorldEffect(WorldEffect(both), f.env);
    CHECK(p.steps.size() == 2 && p.steps[0].line == "Lock 25" && p.steps[1].line == "SetOpenState 1");
    SetObjectState gone = *w;
    gone.ref = FormRef("nosuchmod.esp", 1);
    CHECK(planWorldEffect(WorldEffect(gone), f.env).status == PlanStatus::FormMissing);
}

static void runPresentationTests() {
    run("present_specs_match_the_static_dump", present_specs_match_the_static_dump);
    run("present_effects_lines_json_plan_and_no_replay", present_effects_lines_json_plan_and_no_replay);
    run("relation_flags_travel_with_the_effect_and_into_the_line", relation_flags_travel_with_the_effect_and_into_the_line);
    run("a_scripts_lock_change_also_travels_as_a_world_effect_a_joiner_replays", a_scripts_lock_change_also_travels_as_a_world_effect_a_joiner_replays);
}
