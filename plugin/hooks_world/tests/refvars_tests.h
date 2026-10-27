// Ref script variable poll tests (G15). Included by test_world.cpp (shares its CHECK / run helpers).
#pragma once

struct FakeVarSource : IRefVarSource {
    uint32_t cell = 1;
    std::vector<uint32_t> inCell, actors, watched;
    std::map<uint32_t, std::map<uint16_t, double>> vars;   // refs absent from here are "not loaded"
    uint32_t playerCell() override { return cell; }
    void cellRefs(std::vector<uint32_t>& o) override { o.insert(o.end(), inCell.begin(), inCell.end()); }
    void actorRefs(std::vector<uint32_t>& o) override { o.insert(o.end(), actors.begin(), actors.end()); }
    void watchedRefs(std::vector<uint32_t>& o) override { o.insert(o.end(), watched.begin(), watched.end()); }
    bool readVars(uint32_t r, std::vector<std::pair<uint16_t, double>>& o) override {
        auto it = vars.find(r);
        if (it == vars.end()) return false;
        for (auto& kv : it->second) o.emplace_back(kv.first, kv.second);
        return true;
    }
};
static size_t pollFrames(RefVarPoller& p, FakeVarSource& s, uint32_t from, uint32_t to, std::vector<RefVarChange>* out) {
    size_t n = 0;
    for (uint32_t f = from; f < to; ++f) {
        std::vector<RefVarChange> ch;
        p.tick(f, s, ch);
        n += ch.size();
        if (out) out->insert(out->end(), ch.begin(), ch.end());
    }
    return n;
}
static bool hasChange(const std::vector<RefVarChange>& v, uint32_t ref, uint16_t idx, double val) {
    for (auto& c : v)
        if (c.refID == ref && c.index == idx && c.value == val) return true;
    return false;
}

TEST(refvars_actor_in_another_cell_is_seen) {
    // Vulpes stands in the next exterior cell: the player's cell list never has him, the process list does
    FakeVarSource s;
    s.inCell = {0x100};
    s.vars[0x100][1] = 0;
    s.actors = {0x200};
    s.vars[0x200][4] = 0;
    RefVarPoller p;
    pollFrames(p, s, 1, 31, nullptr);          // baselines
    s.vars[0x200][4] = 1;                      // iVulpesLeaves = 1
    std::vector<RefVarChange> out;
    pollFrames(p, s, 31, 61, &out);
    CHECK(hasChange(out, 0x200, 4, 1));        // twin 56: only the player's cell is read
}

TEST(refvars_watched_ref_is_read_wherever_the_player_is_and_fast) {
    FakeVarSource s;
    s.watched = {0x300};
    s.vars[0x300][2] = 0;                      // a ref in a cell nobody stands in
    RefVarPoller p;
    pollFrames(p, s, 1, 10, nullptr);
    s.vars[0x300][2] = 1;                      // a flag is set and cleared again inside one sweep interval (15 frames)
    std::vector<RefVarChange> a, b;
    pollFrames(p, s, 10, 12, &a);
    s.vars[0x300][2] = 0;
    pollFrames(p, s, 12, 16, &b);
    CHECK(hasChange(a, 0x300, 2, 1));          // the fast lane (every 2 frames) saw the 1 ...
    CHECK(hasChange(b, 0x300, 2, 0));          // ... and the 0: both reach the guest, in order
    CHECK(p.fastReads() > 0);
}

TEST(refvars_change_made_while_the_player_changed_cell_is_not_adopted_as_baseline) {
    FakeVarSource s;
    s.inCell = {0x10};
    s.vars[0x10][0] = 0;
    s.watched = {0x300};
    s.vars[0x300][2] = 0;
    RefVarPoller p;
    pollFrames(p, s, 1, 31, nullptr);
    s.vars[0x300][2] = 1;                      // a quest script writes it ...
    s.cell = 2;                                // ... exactly as the player walks into another cell
    s.inCell = {0x20};
    s.vars[0x20][0] = 0;
    std::vector<RefVarChange> out;
    pollFrames(p, s, 31, 61, &out);
    CHECK(hasChange(out, 0x300, 2, 1));        // twin 55: the cell change forgot the watched ref, the 1 became its baseline
    // a ref that was only in the old cell re-baselines silently (its value may have been reset by the unload)
    s.cell = 1;
    s.inCell = {0x10};
    s.vars[0x10][0] = 5;
    out.clear();
    pollFrames(p, s, 61, 91, &out);
    CHECK(!hasChange(out, 0x10, 0, 5));
}

TEST(refvars_watched_ref_first_seen_with_a_value_reports_it_once) {
    // the cell of a watched ref loads after the write: nothing ever saw the change
    FakeVarSource s;
    s.watched = {0x400};
    RefVarPoller p;
    pollFrames(p, s, 1, 10, nullptr);          // not loaded yet
    s.vars[0x400][3] = 1;
    std::vector<RefVarChange> out;
    pollFrames(p, s, 10, 20, &out);
    int n = 0;
    for (auto& c : out)
        if (c.refID == 0x400 && c.index == 3 && c.value == 1) ++n;
    CHECK(n == 1);                             // twin 53: a watched ref's first value is only a baseline
    // a swept (not watched) ref's first value stays a baseline
    s.actors = {0x500};
    s.vars[0x500][1] = 1;
    out.clear();
    pollFrames(p, s, 20, 50, &out);
    CHECK(!hasChange(out, 0x500, 1, 1));
}

TEST(refvars_a_budget_that_runs_out_resumes_where_it_stopped_and_loses_nothing) {
    FakeVarSource s;
    RefVarPollCfg c;
    c.sweepBudget = 2;
    RefVarPoller p(c);
    for (uint32_t r = 0x600; r < 0x600 + 6; ++r) { s.actors.push_back(r); s.vars[r][0] = 0; }
    pollFrames(p, s, 1, 16, nullptr);
    for (uint32_t r = 0x600; r < 0x600 + 6; ++r) s.vars[r][0] = 1;
    std::vector<RefVarChange> out;
    pollFrames(p, s, 16, 16 + 15 * 5, &out);   // five sweeps of two
    for (uint32_t r = 0x600; r < 0x600 + 6; ++r) CHECK(hasChange(out, r, 0, 1));
    CHECK(out.size() == 6);                    // each exactly once (twin 57: every sweep starts over at the first ref)
}

TEST(refvars_a_ref_that_changes_every_sweep_does_not_starve_the_ones_behind_it) {
    FakeVarSource s;
    RefVarPollCfg c;
    c.sweepBudget = 1;
    RefVarPoller p(c);
    s.actors = {0xB00, 0xB01};
    s.vars[0xB00][0] = 0;
    s.vars[0xB01][0] = 0;
    pollFrames(p, s, 1, 16, nullptr);
    std::vector<RefVarChange> out;
    uint32_t seenAt = 0;
    for (uint32_t f = 16; f < 16 + 15 * 6; ++f) {
        if (f % 15 == 0) s.vars[0xB00][0] += 1;           // changes at every sweep ...
        if (f == 16 + 15) s.vars[0xB01][0] = 1;           // ... the flag behind it is set at the second sweep
        std::vector<RefVarChange> ch;
        p.tick(f, s, ch);
        for (auto& v : ch) if (v.refID == 0xB01 && !seenAt) seenAt = f;
    }
    CHECK(seenAt != 0 && seenAt <= 16 + 15 * 4);          // twin 57: every sweep starts over at the first ref, which spends the budget again
}

TEST(refvars_a_timer_variable_cannot_eat_the_budget_of_the_flags) {
    FakeVarSource s;
    RefVarPollCfg c;
    c.sweepBudget = 1;
    RefVarPoller p(c);
    s.actors = {0x700, 0x701};
    s.vars[0x700][1] = 0;                      // WastelandWindowScript: Time = GetCurrentTime, changes every poll
    s.vars[0x701][0] = 0;                      // a story flag behind it
    pollFrames(p, s, 1, 16, nullptr);
    std::vector<RefVarChange> out;
    for (uint32_t f = 16; f < 16 + 15 * 40; ++f) {
        if (f % 15 == 0) s.vars[0x700][1] += 1;
        if (f == 16 + 15 * 20) s.vars[0x701][0] = 1;
        std::vector<RefVarChange> ch;
        p.tick(f, s, ch);
        out.insert(out.end(), ch.begin(), ch.end());
    }
    CHECK(hasChange(out, 0x701, 0, 1));        // twin 54: the timer keeps the one-change budget every round
    int timer = 0;
    for (auto& v : out)
        if (v.refID == 0x700) ++timer;
    CHECK(timer < 20);                         // the timer is held back, not reported on every poll
}

TEST(refvars_script_run_snapshots_catch_two_runs_in_one_frame) {
    FakeVarSource s;
    s.watched = {0x800};
    s.vars[0x800][1] = 0;
    RefVarPoller p;
    pollFrames(p, s, 1, 6, nullptr);
    s.vars[0x800][1] = 1;
    p.afterScript(0x800, s);                   // the ref's script ran: flag set
    s.vars[0x800][1] = 0;
    p.afterScript(0x800, s);                   // another run in the same frame: cleared again
    std::vector<RefVarChange> ch;
    p.tick(7, s, ch);
    CHECK(hasChange(ch, 0x800, 1, 1) && hasChange(ch, 0x800, 1, 0));   // twin 58: the flip is invisible to the poll
    // only watched refs are read after a script run (every script of the cell would be too many)
    s.vars[0x900][1] = 0;
    p.afterScript(0x900, s);
    s.vars[0x900][1] = 1;
    p.afterScript(0x900, s);
    CHECK(p.pending() == 0);
}

TEST(refvars_sweep_without_a_ref_in_view_is_quiet_and_reset_forgets) {
    FakeVarSource s;
    RefVarPoller p;
    std::vector<RefVarChange> out;
    pollFrames(p, s, 1, 40, &out);
    CHECK(out.empty());
    s.actors = {0xA00};
    s.vars[0xA00][0] = 1;
    pollFrames(p, s, 40, 70, &out);
    CHECK(p.tracker().size() == 1);
    p.reset();
    CHECK(p.tracker().size() == 0);
}

TEST(ref_var_tracker_hot_variable_and_first_report) {
    RefVarTracker t;
    t.beginPoll(100);
    CHECK(!t.observe(1, 1, 0));
    int reported = 0;
    for (int i = 1; i <= 40; ++i) {
        t.beginPoll(100);
        reported += t.observe(1, 1, i) ? 1 : 0;
    }
    CHECK(reported < 40 && reported >= RefVarTracker::kHotStreak);
    t.beginPoll(100);
    CHECK(t.observe(2, 5, 3.0, true));          // watched: first non-zero value is reported
    t.beginPoll(100);
    CHECK(!t.observe(3, 5, 0.0, true));         // a zero is the script default: nothing to say
    RefVarTracker u;
    u.beginPoll(0);
    CHECK(!u.observe(4, 1, 7.0, true));         // over budget: not reported now ...
    u.beginPoll(5);
    CHECK(u.observe(4, 1, 7.0, true));          // ... but not lost
}

static void runRefVarTests() {
    run("refvars_actor_in_another_cell_is_seen", refvars_actor_in_another_cell_is_seen);
    run("refvars_watched_ref_is_read_wherever_the_player_is_and_fast", refvars_watched_ref_is_read_wherever_the_player_is_and_fast);
    run("refvars_change_made_while_the_player_changed_cell_is_not_adopted_as_baseline", refvars_change_made_while_the_player_changed_cell_is_not_adopted_as_baseline);
    run("refvars_watched_ref_first_seen_with_a_value_reports_it_once", refvars_watched_ref_first_seen_with_a_value_reports_it_once);
    run("refvars_a_budget_that_runs_out_resumes_where_it_stopped_and_loses_nothing", refvars_a_budget_that_runs_out_resumes_where_it_stopped_and_loses_nothing);
    run("refvars_a_ref_that_changes_every_sweep_does_not_starve_the_ones_behind_it", refvars_a_ref_that_changes_every_sweep_does_not_starve_the_ones_behind_it);
    run("refvars_a_timer_variable_cannot_eat_the_budget_of_the_flags", refvars_a_timer_variable_cannot_eat_the_budget_of_the_flags);
    run("refvars_script_run_snapshots_catch_two_runs_in_one_frame", refvars_script_run_snapshots_catch_two_runs_in_one_frame);
    run("refvars_sweep_without_a_ref_in_view_is_quiet_and_reset_forgets", refvars_sweep_without_a_ref_in_view_is_quiet_and_reset_forgets);
    run("ref_var_tracker_hot_variable_and_first_report", ref_var_tracker_hot_variable_and_first_report);
}
