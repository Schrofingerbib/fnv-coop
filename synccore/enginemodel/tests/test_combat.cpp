// Combat on the fake engine (combat-core): limbs, deaths, projectiles, explosions, kill XP and forged hits, on clean and lossy links.
#include "scenarios.h"
#include "minitest.h"

using namespace fnvcoop;
using namespace fnvcoop::engine;
using namespace fnvcoop::testing;

namespace {
#define SCENARIO(group, name, expr) \
    TEST(group, name) { std::string why; const bool ok = (expr); CHECK_MSG(ok, why); }
}  // namespace

SCENARIO(combat, limbs_cripple_by_accumulated_damage_and_survive_a_handoff, limbsSync(1, &why))
SCENARIO(combat, limbs_seed_2, limbsSync(2, &why))
SCENARIO(combat, two_lethal_hits_at_once_kill_once, deathHappensOnce(1, &why))
SCENARIO(combat, two_lethal_hits_seed_2, deathHappensOnce(2, &why))
SCENARIO(combat, story_kill_racing_a_combat_kill_ends_in_one_death, storyKillRace(1, &why))
SCENARIO(combat, a_projectile_hurts_only_through_its_shooters_machine, projectileVisualOnly(1, &why))
SCENARIO(combat, projectile_seed_2, projectileVisualOnly(2, &why))
SCENARIO(combat, an_explosion_hurts_each_actor_in_range_once_through_its_owner, blastViaOwners(1, &why))
SCENARIO(combat, explosion_seed_2, blastViaOwners(2, &why))
SCENARIO(combat, kill_xp_goes_to_the_killing_blow_or_is_shared_and_is_paid_once, killXpOnce(1, &why))
SCENARIO(combat, forged_stale_spoofed_and_far_hits_are_rejected, forgedCombatRejected(1, &why))
SCENARIO(combat, forged_hits_seed_2, forgedCombatRejected(2, &why))
