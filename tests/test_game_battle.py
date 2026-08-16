"""戦闘処理（Battle Engine）が仕様どおりに動くかを確認するテスト。

Discordを使わずに10人分の戦闘を再現できるようにするためのテストです
（GAME_SPEC 32.8節）。数値を変更した場合は仕様書の該当節も更新してください。
"""

from __future__ import annotations

import random
import unittest

from game import battle_engine, effects, skill_engine
from game.master_data import load_master_data
from game.models import (
    ACTION_ATTACK,
    BATTLE_STATUS_FINISHED,
    EFFECT_ATK_MODIFIER,
    RESULT_ABORTED,
    RESULT_DRAW,
    RESULT_GUILD_A,
    RESULT_GUILD_B,
    STATUS_CHARM,
    STATUS_PETRIFY,
    STATUS_POISON,
    BattleAction,
    BattleState,
    BattleUnit,
)


MASTER = load_master_data()

GUILD_A = 10
GUILD_B = 20


class NoCriticalRandom(random.Random):
    """クリティカルを起こさず、同値の並び順も固定する乱数。"""

    def randrange(self, start, stop=None, step=1):  # type: ignore[override]
        upper = start if stop is None else stop
        return max(0, int(upper) - 1)


class AlwaysCriticalRandom(random.Random):
    """必ずクリティカルになる乱数。"""

    def randrange(self, start, stop=None, step=1):  # type: ignore[override]
        return 0


def build_state(
    team_a: list[tuple[str, int]],
    team_b: list[tuple[str, int]],
    *,
    rng: random.Random | None = None,
    guild_time: int = 3600,
) -> BattleState:
    """テスト用の戦闘状態を作る。1ギルドの人数は自由に指定できる。"""

    battle_engine.set_random(rng or NoCriticalRandom())

    state = BattleState(battle_id=1, guild_a_id=GUILD_A, guild_b_id=GUILD_B)
    state.remaining_seconds = {GUILD_A: guild_time, GUILD_B: guild_time}

    unit_id = 0
    for guild_id, team in ((GUILD_A, team_a), (GUILD_B, team_b)):
        for slot, (familiar_id, level) in enumerate(team, start=1):
            unit_id += 1
            familiar = MASTER.get_familiar(familiar_id)
            stats = MASTER.level_stats(familiar_id, level)

            state.units[unit_id] = BattleUnit(
                battle_unit_id=unit_id,
                battle_id=1,
                guild_id=guild_id,
                player_id=1000 + unit_id,
                familiar_instance_id=2000 + unit_id,
                familiar_id=familiar_id,
                level=level,
                max_hp=stats.max_hp,
                current_hp=stats.max_hp,
                base_atk=stats.atk,
                current_atk=stats.atk,
                speed=stats.speed,
                cost=familiar.cost,
                slot=slot,
                gender=familiar.gender,
            )

    return state


def unit_of(state: BattleState, familiar_id: str, guild_id: int) -> BattleUnit:
    for unit in state.units.values():
        if unit.familiar_id == familiar_id and unit.guild_id == guild_id:
            return unit
    raise AssertionError(f"{familiar_id} が見つかりません")


def force_turn(state: BattleState, unit: BattleUnit) -> None:
    """指定した使い魔の行動順へ強制的に移す。"""

    state.current_unit_id = unit.battle_unit_id
    if unit.battle_unit_id in state.turn_order:
        state.turn_index = state.turn_order.index(unit.battle_unit_id)


def attack(state: BattleState, attacker: BattleUnit, target: BattleUnit) -> None:
    force_turn(state, attacker)
    battle_engine.submit_action(
        state,
        BattleAction(
            action_type=ACTION_ATTACK,
            actor_unit_id=attacker.battle_unit_id,
            target_unit_id=target.battle_unit_id,
        ),
    )


def use_skill(
    state: BattleState,
    caster: BattleUnit,
    skill_id: str,
    selections: dict | None = None,
) -> None:
    skill = MASTER.get_skill(skill_id)
    skill_engine.use_active_skill(state, caster, skill, selections or {})


# ==================================================
# 行動順（15節）
# ==================================================
class TurnOrderTests(unittest.TestCase):
    def test_units_act_in_speed_order(self) -> None:
        state = build_state(
            [("fenrir", 0), ("hel", 0)],  # SPD 95 / 5
            [("griffin", 0), ("behemoth", 0)],  # SPD 89 / 12
        )
        battle_engine.start_battle(state)

        order = [state.units[uid].familiar_id for uid in state.turn_order]
        self.assertEqual(order, ["fenrir", "griffin", "behemoth", "hel"])

    def test_defeated_units_are_skipped_without_using_guild_time(self) -> None:
        state = build_state([("griffin", 0)], [("behemoth", 0), ("cyclops", 0)])
        battle_engine.start_battle(state)

        cyclops = unit_of(state, "cyclops", GUILD_B)
        cyclops.alive = False
        cyclops.current_hp = 0

        before = state.remaining_seconds[GUILD_B]
        griffin = unit_of(state, "griffin", GUILD_A)
        attack(state, griffin, unit_of(state, "behemoth", GUILD_B))

        self.assertEqual(state.remaining_seconds[GUILD_B], before)
        self.assertNotEqual(state.current_unit_id, cyclops.battle_unit_id)


# ==================================================
# 通常攻撃・クリティカル（18.1節・18.2節）
# ==================================================
class NormalAttackTests(unittest.TestCase):
    def test_damage_equals_current_atk(self) -> None:
        state = build_state([("cyclops", 0)], [("behemoth", 0)])
        battle_engine.start_battle(state)

        attacker = unit_of(state, "cyclops", GUILD_A)
        target = unit_of(state, "behemoth", GUILD_B)
        before = target.current_hp

        battle_engine.perform_attack(state, attacker, target)

        self.assertEqual(before - target.current_hp, attacker.base_atk)

    def test_critical_is_one_and_a_half_times_rounded_half_up(self) -> None:
        state = build_state(
            [("cerberus", 0)], [("behemoth", 0)], rng=AlwaysCriticalRandom()
        )
        battle_engine.start_battle(state)

        attacker = unit_of(state, "cerberus", GUILD_A)  # ATK 9
        target = unit_of(state, "behemoth", GUILD_B)
        before = target.current_hp

        battle_engine.perform_attack(state, attacker, target)

        # 9 × 1.5 = 13.5 → 14（18.2節の例）
        self.assertEqual(before - target.current_hp, 14)

    def test_zero_atk_deals_zero_damage_even_on_critical(self) -> None:
        state = build_state(
            [("behemoth", 0)], [("minotaur", 0)], rng=AlwaysCriticalRandom()
        )
        battle_engine.start_battle(state)

        attacker = unit_of(state, "behemoth", GUILD_A)  # ATK 6
        target = unit_of(state, "minotaur", GUILD_B)

        effects.apply_effect(
            state, attacker, effect_type=EFFECT_ATK_MODIFIER, value=-10,
            duration_type="permanent", source_skill_id="test_debuff",
        )
        self.assertEqual(effects.compute_atk(state, attacker), 0)

        before = target.current_hp
        battle_engine.perform_attack(state, attacker, target)
        self.assertEqual(target.current_hp, before)


# ==================================================
# バフ・デバフ（20節）
# ==================================================
class EffectTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state = build_state([("chimera", 0)], [("chimera", 0)])
        battle_engine.start_battle(self.state)
        self.unit = unit_of(self.state, "chimera", GUILD_A)

    def test_buff_total_is_capped(self) -> None:
        for index in range(4):
            effects.apply_effect(
                self.state, self.unit, effect_type=EFFECT_ATK_MODIFIER, value=5,
                duration_type="permanent", source_skill_id=f"test_buff_{index}",
            )

        # 合計+20だが、上限の+10までしか乗らない
        self.assertEqual(
            effects.compute_atk(self.state, self.unit), self.unit.base_atk + 10
        )

    def test_debuff_total_is_capped_and_atk_never_goes_negative(self) -> None:
        for index in range(4):
            effects.apply_effect(
                self.state, self.unit, effect_type=EFFECT_ATK_MODIFIER, value=-5,
                duration_type="permanent", source_skill_id=f"test_debuff_{index}",
            )

        self.assertEqual(
            effects.compute_atk(self.state, self.unit),
            max(0, self.unit.base_atk - 10),
        )

    def test_same_skill_can_stack_only_three_times(self) -> None:
        applied = [
            effects.apply_effect(
                self.state, self.unit, effect_type=EFFECT_ATK_MODIFIER, value=1,
                duration_type="permanent", source_skill_id="same_skill",
            )
            for _ in range(5)
        ]

        self.assertEqual(sum(1 for item in applied if item is not None), 3)

    def test_no_stack_per_target_blocks_the_second_application(self) -> None:
        first = effects.apply_effect(
            self.state, self.unit, effect_type=EFFECT_ATK_MODIFIER, value=-1,
            duration_type="turns", duration_turns=1, source_skill_id="unique_skill",
            params={"no_stack_per_target": True},
        )
        second = effects.apply_effect(
            self.state, self.unit, effect_type=EFFECT_ATK_MODIFIER, value=-1,
            duration_type="turns", duration_turns=1, source_skill_id="unique_skill",
            params={"no_stack_per_target": True},
        )

        self.assertIsNotNone(first)
        self.assertIsNone(second)

    def test_duration_decreases_at_the_owners_turn_end(self) -> None:
        target = unit_of(self.state, "chimera", GUILD_B)
        effects.apply_effect(
            self.state, target, effect_type=EFFECT_ATK_MODIFIER, value=-2,
            duration_type="turns", duration_turns=2, source_skill_id="test_debuff",
        )
        self.assertEqual(effects.compute_atk(self.state, target), target.base_atk - 2)

        # 対象自身の行動を2回終えると解除される
        attack(self.state, target, self.unit)
        self.assertEqual(effects.compute_atk(self.state, target), target.base_atk - 2)
        attack(self.state, target, self.unit)
        self.assertEqual(effects.compute_atk(self.state, target), target.base_atk)


# ==================================================
# 状態異常（20節・21節）
# ==================================================
class StatusTests(unittest.TestCase):
    def test_poison_hits_at_the_owners_turn_end_and_expires(self) -> None:
        state = build_state([("chimera", 0)], [("chimera", 0)])
        battle_engine.start_battle(state)

        attacker = unit_of(state, "chimera", GUILD_A)
        target = unit_of(state, "chimera", GUILD_B)

        skill_engine.apply_status(
            state, attacker, target, STATUS_POISON,
            duration_turns=2, damage=2, skill_id="test_poison",
        )
        before = target.current_hp

        attack(state, target, attacker)
        self.assertEqual(target.current_hp, before - 2)

        attack(state, target, attacker)
        self.assertEqual(target.current_hp, before - 4)

        # 2回発生したら解除される
        attack(state, target, attacker)
        self.assertEqual(target.current_hp, before - 4)
        self.assertFalse(effects.has_status(state, target, STATUS_POISON))

    def test_poison_applied_during_the_owners_turn_does_not_tick_early(self) -> None:
        # 34.5節：自分のターン中に付いた効果は同じターンで残りを減らさないため、
        # 毒もそのターンでは発生させない（規定回数より多く当たらないこと）。
        state = build_state([("chimera", 0)], [("chimera", 0)])
        battle_engine.start_battle(state)

        actor = unit_of(state, "chimera", GUILD_A)
        enemy = unit_of(state, "chimera", GUILD_B)

        force_turn(state, actor)
        skill_engine.apply_status(
            state, enemy, actor, STATUS_POISON,
            duration_turns=2, damage=2, skill_id="test_poison",
        )

        before = actor.current_hp
        battle_engine._finish_turn(state, actor, advance=False)
        self.assertEqual(actor.current_hp, before, "付与したターンでは発生しない")

        # 以降のラウンドで、指定回数だけ発生して解除される
        actor.current_hp = actor.max_hp
        enemy.current_hp = enemy.max_hp

        for _ in range(6):
            if battle_engine.is_finished(state):
                break
            battle_engine.auto_action(state, elapsed_seconds=1)

        ticks = [
            log
            for log in state.logs
            if log.event_type == "DAMAGE"
            and (log.detail or {}).get("attack_type") == "poison"
        ]
        self.assertEqual(len(ticks), 2, [log.detail for log in ticks])
        self.assertFalse(effects.has_status(state, actor, STATUS_POISON))

    def test_poison_never_criticals(self) -> None:
        state = build_state(
            [("chimera", 0)], [("chimera", 0)], rng=AlwaysCriticalRandom()
        )
        battle_engine.start_battle(state)

        attacker = unit_of(state, "chimera", GUILD_A)
        target = unit_of(state, "chimera", GUILD_B)
        skill_engine.apply_status(
            state, attacker, target, STATUS_POISON,
            duration_turns=1, damage=2, skill_id="test_poison",
        )

        before = target.current_hp
        force_turn(state, target)
        battle_engine._finish_turn(state, target, advance=False)

        self.assertEqual(target.current_hp, before - 2)

    def test_charm_blocks_the_next_action_without_using_guild_time(self) -> None:
        state = build_state([("chimera", 0)], [("griffin", 0), ("garm", 0)])
        battle_engine.start_battle(state)

        attacker = unit_of(state, "chimera", GUILD_A)
        target = unit_of(state, "griffin", GUILD_B)
        skill_engine.apply_status(
            state, attacker, target, STATUS_CHARM,
            duration_turns=1, skill_id="test_charm",
        )

        blocked, reason = effects.is_action_blocked(state, target)
        self.assertTrue(blocked)
        self.assertEqual(reason, STATUS_CHARM)

        before_time = state.remaining_seconds[GUILD_B]
        attack(state, attacker, unit_of(state, "garm", GUILD_B))

        # 行動不能の自動スキップでは持ち時間を消費しない（15節・22節）
        self.assertEqual(state.remaining_seconds[GUILD_B], before_time)
        # スキップも行動終了として扱われ、状態異常は解除される（20節）
        self.assertFalse(effects.has_status(state, target, STATUS_CHARM))

    def test_status_immunity_blocks_new_statuses(self) -> None:
        state = build_state([("astaroth", 0), ("garm", 0)], [("medusa", 0)])
        battle_engine.start_battle(state)

        astaroth = unit_of(state, "astaroth", GUILD_A)
        ally = unit_of(state, "garm", GUILD_A)
        medusa = unit_of(state, "medusa", GUILD_B)

        skill_engine.apply_status(
            state, medusa, ally, STATUS_POISON,
            duration_turns=2, damage=2, skill_id="test_poison",
        )
        use_skill(state, astaroth, "astaroth_active",
                  {"main": (ally.battle_unit_id,)})

        # 解除されたうえで、新たな状態異常も受けない
        self.assertFalse(effects.has_status(state, ally, STATUS_POISON))
        applied = skill_engine.apply_status(
            state, medusa, ally, STATUS_PETRIFY,
            duration_turns=1, skill_id="medusa_active",
        )
        self.assertFalse(applied)

    def test_only_status_effects_are_cleansed(self) -> None:
        state = build_state([("astaroth", 0), ("garm", 0)], [("kyubi", 0)])
        battle_engine.start_battle(state)

        ally = unit_of(state, "garm", GUILD_A)
        kyubi = unit_of(state, "kyubi", GUILD_B)
        astaroth = unit_of(state, "astaroth", GUILD_A)

        use_skill(state, kyubi, "kyubi_active", {"main": (ally.battle_unit_id,)})
        self.assertEqual(effects.compute_atk(state, ally), ally.base_atk - 4)

        use_skill(state, astaroth, "astaroth_active",
                  {"main": (ally.battle_unit_id,)})

        # 20節「能力値低下は状態異常に含めません」
        self.assertEqual(effects.compute_atk(state, ally), ally.base_atk - 4)


# ==================================================
# 戦闘不能・蘇生（18.5節）
# ==================================================
class DefeatTests(unittest.TestCase):
    def test_dullahan_survives_lethal_damage_once(self) -> None:
        state = build_state([("cyclops", 0)], [("dullahan", 0)])
        battle_engine.start_battle(state)

        attacker = unit_of(state, "cyclops", GUILD_A)
        dullahan = unit_of(state, "dullahan", GUILD_B)
        dullahan.current_hp = 3

        battle_engine.perform_attack(state, attacker, dullahan)
        self.assertTrue(dullahan.alive)
        self.assertEqual(dullahan.current_hp, 1)

        # 1バトル1回だけなので2度目は耐えない
        battle_engine.perform_attack(state, attacker, dullahan)
        self.assertFalse(dullahan.alive)

    def test_instant_defeat_ignores_damage_based_survival(self) -> None:
        # 18.5節「ダメージを与えない即時戦闘不能では発動しません」
        state = build_state([("hel", 0)], [("dullahan", 0)])
        battle_engine.start_battle(state)

        hel = unit_of(state, "hel", GUILD_A)
        dullahan = unit_of(state, "dullahan", GUILD_B)

        use_skill(state, hel, "hel_active", {"main": (dullahan.battle_unit_id,)})
        self.assertFalse(dullahan.alive)
        self.assertEqual(dullahan.current_hp, 0)

    def test_phoenix_revives_itself_once(self) -> None:
        state = build_state([("cyclops", 0)], [("phoenix", 0), ("garm", 0)])
        battle_engine.start_battle(state)

        attacker = unit_of(state, "cyclops", GUILD_A)
        phoenix = unit_of(state, "phoenix", GUILD_B)
        phoenix.current_hp = 2

        battle_engine.perform_attack(state, attacker, phoenix)
        self.assertTrue(phoenix.alive)
        self.assertEqual(phoenix.current_hp, 14)

        phoenix.current_hp = 2
        battle_engine.perform_attack(state, attacker, phoenix)
        self.assertFalse(phoenix.alive)

    def test_hel_revives_the_first_fallen_ally_but_not_itself(self) -> None:
        state = build_state([("cyclops", 0)], [("hel", 0), ("garm", 0)])
        battle_engine.start_battle(state)

        attacker = unit_of(state, "cyclops", GUILD_A)
        hel = unit_of(state, "hel", GUILD_B)
        garm = unit_of(state, "garm", GUILD_B)

        garm.current_hp = 2
        battle_engine.perform_attack(state, attacker, garm)
        self.assertTrue(garm.alive)
        self.assertEqual(garm.current_hp, 10)

        hel.current_hp = 2
        battle_engine.perform_attack(state, attacker, hel)
        self.assertFalse(hel.alive)

    def test_effects_survive_defeat_and_revive(self) -> None:
        state = build_state([("cyclops", 0), ("kyubi", 0)], [("phoenix", 0), ("garm", 0)])
        battle_engine.start_battle(state)

        kyubi = unit_of(state, "kyubi", GUILD_A)
        attacker = unit_of(state, "cyclops", GUILD_A)
        phoenix = unit_of(state, "phoenix", GUILD_B)

        use_skill(state, kyubi, "kyubi_active", {"main": (phoenix.battle_unit_id,)})
        phoenix.current_hp = 2
        battle_engine.perform_attack(state, attacker, phoenix)

        self.assertTrue(phoenix.alive)
        # 18.5節「残っているバフ・デバフを引き継ぎます」
        self.assertEqual(effects.compute_atk(state, phoenix), phoenix.base_atk - 4)


# ==================================================
# パッシブ（19.3節）
# ==================================================
class PassiveTests(unittest.TestCase):
    def test_fenrir_boosts_only_the_first_attack(self) -> None:
        state = build_state([("fenrir", 0)], [("behemoth", 0)])
        battle_engine.start_battle(state)

        fenrir = unit_of(state, "fenrir", GUILD_A)
        target = unit_of(state, "behemoth", GUILD_B)

        before = target.current_hp
        battle_engine.perform_attack(state, fenrir, target)
        self.assertEqual(before - target.current_hp, fenrir.base_atk + 5)

        before = target.current_hp
        battle_engine.perform_attack(state, fenrir, target)
        self.assertEqual(before - target.current_hp, fenrir.base_atk)

    def test_surtr_gains_atk_below_half_hp(self) -> None:
        state = build_state([("surtr", 0)], [("behemoth", 0)])
        battle_engine.start_battle(state)

        surtr = unit_of(state, "surtr", GUILD_A)
        self.assertEqual(effects.compute_atk(state, surtr), surtr.base_atk)

        surtr.current_hp = surtr.max_hp // 2
        self.assertEqual(effects.compute_atk(state, surtr), surtr.base_atk + 3)

        surtr.current_hp = surtr.max_hp
        self.assertEqual(effects.compute_atk(state, surtr), surtr.base_atk)

    def test_paimon_only_gains_atk_during_round_one(self) -> None:
        state = build_state([("paimon", 0)], [("behemoth", 0)])
        battle_engine.start_battle(state)

        paimon = unit_of(state, "paimon", GUILD_A)
        self.assertEqual(effects.compute_atk(state, paimon), paimon.base_atk + 2)

        state.current_round = 2
        self.assertEqual(effects.compute_atk(state, paimon), paimon.base_atk)

    def test_jormungandr_debuff_expires_at_the_end_of_round_one(self) -> None:
        state = build_state([("jormungandr", 0)], [("garm", 0)])
        battle_engine.start_battle(state)

        target = unit_of(state, "garm", GUILD_B)
        self.assertEqual(effects.compute_atk(state, target), target.base_atk - 1)

        effects.expire_round_effects(state)
        self.assertEqual(effects.compute_atk(state, target), target.base_atk)

    def test_loki_reflects_a_status_back_to_its_source(self) -> None:
        state = build_state([("loki", 0)], [("medusa", 0)])
        battle_engine.start_battle(state)

        loki = unit_of(state, "loki", GUILD_A)
        medusa = unit_of(state, "medusa", GUILD_B)

        use_skill(state, medusa, "medusa_active", {"main": (loki.battle_unit_id,)})

        self.assertFalse(effects.has_status(state, loki, STATUS_PETRIFY))
        self.assertTrue(effects.has_status(state, medusa, STATUS_PETRIFY))

    def test_sphinx_nullifies_only_the_first_status(self) -> None:
        state = build_state([("sphinx", 0)], [("medusa", 0)])
        battle_engine.start_battle(state)

        sphinx = unit_of(state, "sphinx", GUILD_A)
        medusa = unit_of(state, "medusa", GUILD_B)

        first = skill_engine.apply_status(
            state, medusa, sphinx, STATUS_PETRIFY,
            duration_turns=1, skill_id="medusa_active",
        )
        self.assertFalse(first)

        second = skill_engine.apply_status(
            state, medusa, sphinx, STATUS_POISON,
            duration_turns=1, damage=2, skill_id="beelzebub_passive",
        )
        self.assertTrue(second)

    def test_hydra_heals_at_the_end_of_its_own_turn(self) -> None:
        state = build_state([("hydra", 0)], [("garm", 0)])
        battle_engine.start_battle(state)

        hydra = unit_of(state, "hydra", GUILD_A)
        garm = unit_of(state, "garm", GUILD_B)
        hydra.current_hp = 10

        attack(state, hydra, garm)
        self.assertEqual(hydra.current_hp, 13)

    def test_nidhogg_debuff_does_not_stack_on_the_same_target(self) -> None:
        state = build_state([("nidhogg", 0)], [("behemoth", 0)])
        battle_engine.start_battle(state)

        nidhogg = unit_of(state, "nidhogg", GUILD_A)
        target = unit_of(state, "behemoth", GUILD_B)

        battle_engine.perform_attack(state, nidhogg, target)
        battle_engine.perform_attack(state, nidhogg, target)

        self.assertEqual(effects.compute_atk(state, target), target.base_atk - 1)

    def test_belphegor_weakens_the_attackers_next_attack(self) -> None:
        # 「通常攻撃を受けるたび、攻撃した敵の次の攻撃のみATK-2」
        # 効果を付けた攻撃自身では消費されず、次の攻撃に乗ること。
        state = build_state([("cyclops", 0)], [("belphegor", 0)])
        battle_engine.start_battle(state)

        attacker = unit_of(state, "cyclops", GUILD_A)
        belphegor = unit_of(state, "belphegor", GUILD_B)

        before = belphegor.current_hp
        battle_engine.perform_attack(state, attacker, belphegor)
        self.assertEqual(before - belphegor.current_hp, attacker.base_atk)

        before = belphegor.current_hp
        battle_engine.perform_attack(state, attacker, belphegor)
        self.assertEqual(before - belphegor.current_hp, attacker.base_atk - 2)

        # 攻撃を受けるたび付与し直すため、以降も弱体化が続く
        before = belphegor.current_hp
        battle_engine.perform_attack(state, attacker, belphegor)
        self.assertEqual(before - belphegor.current_hp, attacker.base_atk - 2)

    def test_next_attack_buff_is_not_stacked_by_repeated_attacks(self) -> None:
        # 「同一対象には重複しない」ので、ATK-2が二重に乗ることはない
        state = build_state([("cyclops", 0)], [("belphegor", 0)])
        battle_engine.start_battle(state)

        attacker = unit_of(state, "cyclops", GUILD_A)
        belphegor = unit_of(state, "belphegor", GUILD_B)

        for _ in range(4):
            battle_engine.perform_attack(state, attacker, belphegor)

        self.assertGreaterEqual(
            effects.compute_atk(state, attacker, include_attack_modifiers=True),
            attacker.base_atk - 2,
        )

    def test_lilith_nullifies_the_first_attack_from_the_opposite_sex(self) -> None:
        # サイクロプス（male）→ リリス（female）は異性なので発動する（21節）
        state = build_state([("cyclops", 0)], [("lilith", 0)])
        battle_engine.start_battle(state)

        attacker = unit_of(state, "cyclops", GUILD_A)
        lilith = unit_of(state, "lilith", GUILD_B)

        before = lilith.current_hp
        battle_engine.perform_attack(state, attacker, lilith)
        self.assertEqual(lilith.current_hp, before, "最初の1回はダメージ0")

        before = lilith.current_hp
        battle_engine.perform_attack(state, attacker, lilith)
        self.assertEqual(before - lilith.current_hp, attacker.base_atk)

    def test_same_sex_attacks_are_not_nullified(self) -> None:
        # メドゥーサ（female）→ リリス（female）は同性なので発動しない
        state = build_state([("medusa", 0)], [("lilith", 0)])
        battle_engine.start_battle(state)

        attacker = unit_of(state, "medusa", GUILD_A)
        lilith = unit_of(state, "lilith", GUILD_B)

        before = lilith.current_hp
        battle_engine.perform_attack(state, attacker, lilith)
        self.assertEqual(before - lilith.current_hp, attacker.base_atk)

    def test_genderless_familiars_are_never_opposite(self) -> None:
        # ペガサス（male）→ フェニックス（none）は異性にならない（21節）
        state = build_state([("pegasus", 0)], [("phoenix", 0)])
        battle_engine.start_battle(state)

        pegasus = unit_of(state, "pegasus", GUILD_A)
        phoenix = unit_of(state, "phoenix", GUILD_B)

        self.assertEqual(phoenix.gender, "none")
        self.assertFalse(
            skill_engine.selectable_targets(
                state,
                pegasus,
                MASTER.get_skill("siren_active").targets[0],
            )
        )


# ==================================================
# アクティブスキル（19.2節）
# ==================================================
class ActiveSkillTests(unittest.TestCase):
    def test_active_skill_can_be_used_once_per_battle(self) -> None:
        state = build_state([("ifrit", 0)], [("behemoth", 0)])
        battle_engine.start_battle(state)

        ifrit = unit_of(state, "ifrit", GUILD_A)
        target = unit_of(state, "behemoth", GUILD_B)

        use_skill(state, ifrit, "ifrit_active", {"main": (target.battle_unit_id,)})
        self.assertEqual(ifrit.active_skill_uses["ifrit_active"], 1)
        self.assertEqual(battle_engine.available_skills(state, ifrit), [])

    def test_fixed_damage_skill_deals_its_value(self) -> None:
        state = build_state([("ifrit", 0)], [("behemoth", 0)])
        battle_engine.start_battle(state)

        ifrit = unit_of(state, "ifrit", GUILD_A)
        target = unit_of(state, "behemoth", GUILD_B)
        before = target.current_hp

        use_skill(state, ifrit, "ifrit_active", {"main": (target.battle_unit_id,)})
        self.assertEqual(before - target.current_hp, 12)

    def test_area_skill_hits_every_living_enemy(self) -> None:
        state = build_state(
            [("abaddon", 0)], [("behemoth", 0), ("minotaur", 0), ("garm", 0)]
        )
        battle_engine.start_battle(state)

        abaddon = unit_of(state, "abaddon", GUILD_A)
        targets = state.living_units(GUILD_B)
        before = {unit.battle_unit_id: unit.current_hp for unit in targets}

        use_skill(state, abaddon, "abaddon_active")

        for unit in targets:
            self.assertEqual(before[unit.battle_unit_id] - unit.current_hp, 5)

    def test_two_attacks_can_target_different_enemies(self) -> None:
        state = build_state([("surtr", 0)], [("behemoth", 0), ("minotaur", 0)])
        battle_engine.start_battle(state)

        surtr = unit_of(state, "surtr", GUILD_A)
        first = unit_of(state, "behemoth", GUILD_B)
        second = unit_of(state, "minotaur", GUILD_B)
        before = (first.current_hp, second.current_hp)

        use_skill(
            state, surtr, "surtr_active",
            {"main": (first.battle_unit_id, second.battle_unit_id)},
        )

        self.assertEqual(before[0] - first.current_hp, surtr.base_atk)
        self.assertEqual(before[1] - second.current_hp, surtr.base_atk)

    def test_second_attack_is_cancelled_when_the_only_target_falls(self) -> None:
        # 18.4節「1回目の攻撃で対象が戦闘不能になった場合、2回目の攻撃は無効」
        state = build_state([("surtr", 0)], [("behemoth", 0), ("minotaur", 0)])
        battle_engine.start_battle(state)

        surtr = unit_of(state, "surtr", GUILD_A)
        weak = unit_of(state, "behemoth", GUILD_B)
        other = unit_of(state, "minotaur", GUILD_B)
        weak.current_hp = 1
        other_before = other.current_hp

        use_skill(
            state, surtr, "surtr_active",
            {"main": (weak.battle_unit_id, weak.battle_unit_id)},
        )

        self.assertFalse(weak.alive)
        self.assertEqual(other.current_hp, other_before)
        # 戦闘不能にした敵1体につきHP10回復
        self.assertEqual(surtr.current_hp, surtr.max_hp)

    def test_taunt_forces_the_next_normal_attack_target(self) -> None:
        state = build_state([("kraken", 0), ("garm", 0)], [("cyclops", 0)])
        battle_engine.start_battle(state)

        kraken = unit_of(state, "kraken", GUILD_A)
        cyclops = unit_of(state, "cyclops", GUILD_B)

        use_skill(state, kraken, "kraken_active", {"main": (cyclops.battle_unit_id,)})

        choices = battle_engine.attack_target_choices(state, cyclops)
        self.assertEqual([unit.battle_unit_id for unit in choices],
                         [kraken.battle_unit_id])

    def test_active_lock_prevents_using_active_skills(self) -> None:
        state = build_state([("fenrir", 0)], [("ifrit", 0)])
        battle_engine.start_battle(state)

        fenrir = unit_of(state, "fenrir", GUILD_A)
        ifrit = unit_of(state, "ifrit", GUILD_B)

        use_skill(state, fenrir, "fenrir_active", {"main": (ifrit.battle_unit_id,)})

        self.assertTrue(effects.is_active_locked(state, ifrit))
        self.assertEqual(battle_engine.available_skills(state, ifrit), [])

    def test_opposite_gender_skill_is_unusable_while_gender_is_unset(self) -> None:
        state = build_state([("asmodeus", 0)], [("garm", 0)])
        battle_engine.start_battle(state)

        asmodeus = unit_of(state, "asmodeus", GUILD_A)
        skill = MASTER.get_skill("asmodeus_active")

        self.assertFalse(skill_engine.is_skill_usable(state, asmodeus, skill))

    def test_atk_swap_exchanges_current_atk(self) -> None:
        state = build_state([("loki", 0), ("cyclops", 0)], [("behemoth", 0)])
        battle_engine.start_battle(state)

        loki = unit_of(state, "loki", GUILD_A)
        ally = unit_of(state, "cyclops", GUILD_A)  # ATK 10
        enemy = unit_of(state, "behemoth", GUILD_B)  # ATK 6

        use_skill(
            state, loki, "loki_active",
            {"enemy": (enemy.battle_unit_id,), "ally": (ally.battle_unit_id,)},
        )

        self.assertEqual(effects.compute_atk(state, enemy), 10)
        self.assertEqual(effects.compute_atk(state, ally), 6)

    def test_atk_swap_does_not_double_count_existing_modifiers(self) -> None:
        # 34.4節：交換時点の現在ATKを基準値とし、既存の補正を二重に足さない
        state = build_state([("loki", 0), ("cyclops", 0)], [("behemoth", 0)])
        battle_engine.start_battle(state)

        loki = unit_of(state, "loki", GUILD_A)
        ally = unit_of(state, "cyclops", GUILD_A)  # ATK 10
        enemy = unit_of(state, "behemoth", GUILD_B)  # ATK 6

        effects.apply_effect(
            state, ally, effect_type=EFFECT_ATK_MODIFIER, value=-3,
            duration_type="permanent", source_skill_id="test_debuff",
        )
        self.assertEqual(effects.compute_atk(state, ally), 7)

        use_skill(
            state, loki, "loki_active",
            {"enemy": (enemy.battle_unit_id,), "ally": (ally.battle_unit_id,)},
        )

        # 交換後は互いの「交換時点の現在ATK」になる
        self.assertEqual(effects.compute_atk(state, ally), 6)
        self.assertEqual(effects.compute_atk(state, enemy), 7)

    def test_modifiers_after_the_swap_apply_to_the_swapped_value(self) -> None:
        state = build_state([("loki", 0), ("cyclops", 0)], [("behemoth", 0)])
        battle_engine.start_battle(state)

        loki = unit_of(state, "loki", GUILD_A)
        ally = unit_of(state, "cyclops", GUILD_A)
        enemy = unit_of(state, "behemoth", GUILD_B)

        use_skill(
            state, loki, "loki_active",
            {"enemy": (enemy.battle_unit_id,), "ally": (ally.battle_unit_id,)},
        )
        self.assertEqual(effects.compute_atk(state, ally), 6)

        effects.apply_effect(
            state, ally, effect_type=EFFECT_ATK_MODIFIER, value=2,
            duration_type="permanent", source_skill_id="test_buff",
        )
        self.assertEqual(effects.compute_atk(state, ally), 8)

    def test_heal_never_exceeds_max_hp_and_skips_defeated_units(self) -> None:
        state = build_state([("lucifer", 0), ("garm", 0)], [("cyclops", 0)])
        battle_engine.start_battle(state)

        lucifer = unit_of(state, "lucifer", GUILD_A)
        ally = unit_of(state, "garm", GUILD_A)
        ally.current_hp = ally.max_hp - 2

        use_skill(state, lucifer, "lucifer_active", {"main": (ally.battle_unit_id,)})
        self.assertEqual(ally.current_hp, ally.max_hp)

        ally.alive = False
        ally.current_hp = 0
        healed = battle_engine.heal_unit(state, lucifer, ally, 10)
        self.assertEqual(healed, 0)


# ==================================================
# 決着（26.1節）
# ==================================================
class BattleEndTests(unittest.TestCase):
    def test_wiping_the_enemy_wins_the_battle(self) -> None:
        state = build_state([("cyclops", 0)], [("garm", 0)])
        battle_engine.start_battle(state)

        attacker = unit_of(state, "cyclops", GUILD_A)
        target = unit_of(state, "garm", GUILD_B)
        target.current_hp = 1

        attack(state, attacker, target)

        self.assertTrue(battle_engine.is_finished(state))
        self.assertEqual(state.status, BATTLE_STATUS_FINISHED)
        self.assertEqual(state.result, RESULT_GUILD_A)
        self.assertEqual(state.end_reason, "wipe")

    def test_simultaneous_wipe_is_a_draw(self) -> None:
        state = build_state([("garm", 0)], [("garm", 0)])
        battle_engine.start_battle(state)

        for unit in state.units.values():
            unit.alive = False
            unit.current_hp = 0

        battle_engine._check_wipe(state)
        self.assertEqual(state.result, RESULT_DRAW)

    def test_running_out_of_guild_time_loses(self) -> None:
        state = build_state([("garm", 0)], [("garm", 0)])
        battle_engine.start_battle(state)

        state.remaining_seconds[GUILD_A] = 0
        self.assertTrue(battle_engine.check_time_over(state))
        self.assertEqual(state.result, RESULT_GUILD_B)
        self.assertEqual(state.end_reason, "time_over")

    def test_surrender_gives_the_win_to_the_opponent(self) -> None:
        state = build_state([("garm", 0)], [("garm", 0)])
        battle_engine.start_battle(state)

        battle_engine.surrender(state, GUILD_B)
        self.assertEqual(state.result, RESULT_GUILD_A)
        self.assertEqual(state.end_reason, "surrender")

    def test_abort_records_no_winner(self) -> None:
        state = build_state([("garm", 0)], [("garm", 0)])
        battle_engine.start_battle(state)

        battle_engine.abort(state, "運営による中止")
        # 勝敗なしの aborted として保存し、通常の勝敗数へは反映しない（26.1節）
        self.assertEqual(state.status, "aborted")
        self.assertEqual(state.result, RESULT_ABORTED)
        self.assertEqual(state.end_reason, "運営による中止")


# ==================================================
# 持ち時間と自動処理（17節・22節）
# ==================================================
class TimeTests(unittest.TestCase):
    def test_only_the_acting_guild_loses_time(self) -> None:
        state = build_state([("griffin", 0)], [("behemoth", 0)])
        battle_engine.start_battle(state)

        actor = state.current_unit()
        opponent_guild = state.enemy_guild_id(actor.guild_id)
        before_opponent = state.remaining_seconds[opponent_guild]

        battle_engine.submit_action(
            state,
            BattleAction(
                action_type=ACTION_ATTACK,
                actor_unit_id=actor.battle_unit_id,
                target_unit_id=state.living_units(opponent_guild)[0].battle_unit_id,
            ),
            elapsed_seconds=30,
        )

        self.assertEqual(state.remaining_seconds[actor.guild_id], 3600 - 30)
        self.assertEqual(state.remaining_seconds[opponent_guild], before_opponent)

    def test_auto_target_picks_the_lowest_hp_enemy(self) -> None:
        state = build_state(
            [("griffin", 0)], [("behemoth", 0), ("minotaur", 0), ("garm", 0)]
        )
        battle_engine.start_battle(state)

        griffin = unit_of(state, "griffin", GUILD_A)
        weakest = unit_of(state, "minotaur", GUILD_B)
        weakest.current_hp = 3

        self.assertEqual(
            battle_engine.auto_target(state, griffin).battle_unit_id,
            weakest.battle_unit_id,
        )

    def test_auto_action_attacks_and_ends_the_turn(self) -> None:
        state = build_state([("griffin", 0)], [("behemoth", 0)])
        battle_engine.start_battle(state)

        actor = state.current_unit()
        target = state.living_units(GUILD_B)[0]
        before = target.current_hp

        battle_engine.auto_action(state, elapsed_seconds=180)

        self.assertEqual(before - target.current_hp, actor.base_atk)
        self.assertEqual(state.remaining_seconds[actor.guild_id], 3600 - 180)

    def test_auto_action_finishes_the_battle_when_time_runs_out(self) -> None:
        state = build_state([("griffin", 0)], [("behemoth", 0)], guild_time=100)
        battle_engine.start_battle(state)

        actor = state.current_unit()
        battle_engine.auto_action(state, elapsed_seconds=180)

        self.assertTrue(battle_engine.is_finished(state))
        self.assertEqual(state.end_reason, "time_over")
        self.assertEqual(
            state.result,
            RESULT_GUILD_B if actor.guild_id == GUILD_A else RESULT_GUILD_A,
        )


# ==================================================
# 操作の検証（16節・29節）
# ==================================================
class ActionValidationTests(unittest.TestCase):
    def test_only_the_current_actor_can_act(self) -> None:
        state = build_state([("griffin", 0), ("garm", 0)], [("behemoth", 0)])
        battle_engine.start_battle(state)

        other = next(
            unit
            for unit in state.guild_units(GUILD_A)
            if unit.battle_unit_id != state.current_unit_id
        )
        target = state.living_units(GUILD_B)[0]

        with self.assertRaises(Exception):
            battle_engine.submit_action(
                state,
                BattleAction(
                    action_type=ACTION_ATTACK,
                    actor_unit_id=other.battle_unit_id,
                    target_unit_id=target.battle_unit_id,
                ),
            )

    def test_attacking_an_invalid_target_is_rejected(self) -> None:
        state = build_state([("griffin", 0), ("garm", 0)], [("behemoth", 0)])
        battle_engine.start_battle(state)

        actor = state.current_unit()
        ally = next(
            unit
            for unit in state.guild_units(actor.guild_id)
            if unit.battle_unit_id != actor.battle_unit_id
        )

        with self.assertRaises(Exception):
            battle_engine.submit_action(
                state,
                BattleAction(
                    action_type=ACTION_ATTACK,
                    actor_unit_id=actor.battle_unit_id,
                    target_unit_id=ally.battle_unit_id,
                ),
            )

    def test_skill_selection_is_revalidated(self) -> None:
        state = build_state([("ifrit", 0)], [("behemoth", 0), ("garm", 0)])
        battle_engine.start_battle(state)

        ifrit = unit_of(state, "ifrit", GUILD_A)
        dead = unit_of(state, "garm", GUILD_B)
        dead.alive = False
        dead.current_hp = 0

        with self.assertRaises(Exception):
            use_skill(state, ifrit, "ifrit_active", {"main": (dead.battle_unit_id,)})


# ==================================================
# 5対5の通し確認（32.8節）
# ==================================================
class FullBattleTests(unittest.TestCase):
    def test_five_versus_five_reaches_a_result(self) -> None:
        team_a = [("loki", 0), ("surtr", 0), ("fenrir", 1), ("jormungandr", 0), ("hel", 0)]
        team_b = [("garm", 0), ("hydra", 2), ("dullahan", 0), ("phoenix", 0), ("kraken", 0)]

        for seed in range(5):
            state = build_state(team_a, team_b, rng=random.Random(seed))
            battle_engine.start_battle(state)

            for _ in range(400):
                if battle_engine.is_finished(state):
                    break

                actor = state.current_unit()
                self.assertIsNotNone(actor)
                battle_engine.auto_action(state, elapsed_seconds=10)

            self.assertTrue(battle_engine.is_finished(state), f"seed={seed}")
            self.assertIn(
                state.result, {RESULT_GUILD_A, RESULT_GUILD_B, RESULT_DRAW}, f"seed={seed}"
            )
            # 所有使い魔の永続データを触らずに完走できていること
            self.assertTrue(all(unit.max_hp > 0 for unit in state.units.values()))


if __name__ == "__main__":
    unittest.main()
