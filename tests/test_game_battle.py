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
    EFFECT_HEAL_BLOCK,
    EFFECT_SPEED_MODIFIER,
    RESULT_ABORTED,
    RESULT_DRAW,
    RESULT_GUILD_A,
    RESULT_GUILD_B,
    STATUS_ACTIVE_LOCK,
    STATUS_POISON,
    STATUS_STUN,
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
                base_speed=stats.speed,
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
            [("garm", 1)], [("behemoth", 1)], rng=AlwaysCriticalRandom()
        )
        battle_engine.start_battle(state)

        attacker = unit_of(state, "garm", GUILD_A)  # ATK 9
        target = unit_of(state, "behemoth", GUILD_B)
        before = target.current_hp

        battle_engine.perform_attack(state, attacker, target)

        # 9 × 1.5 = 13.5 → 14（BATTLE_RULES.md 4節）
        self.assertEqual(before - target.current_hp, 14)

    def test_critical_is_applied_before_damage_reduction(self) -> None:
        # 4節の例：ATK10のクリティカル15 → 攻撃ダメージ-2 → 13
        state = build_state(
            [("cyclops", 1)], [("surtr", 1)], rng=AlwaysCriticalRandom()
        )
        battle_engine.start_battle(state)

        attacker = unit_of(state, "cyclops", GUILD_A)
        target = unit_of(state, "surtr", GUILD_B)
        before = target.current_hp

        battle_engine.perform_attack(state, attacker, target)

        self.assertEqual(before - target.current_hp, 13)

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
# 状態異常・デバフ・保護効果（BATTLE_RULES.md 8節）
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
    def test_stun_blocks_the_next_action_without_using_guild_time(self) -> None:
        state = build_state([("garm", 1)], [("medusa", 1)])
        battle_engine.start_battle(state)

        target = unit_of(state, "garm", GUILD_A)
        skill_engine.apply_status(
            state, None, target, STATUS_STUN, duration_turns=1
        )

        before = state.remaining_seconds[GUILD_A]
        force_turn(state, target)
        battle_engine.auto_action(state, elapsed_seconds=0)

        self.assertEqual(state.remaining_seconds[GUILD_A], before)
        self.assertFalse(effects.has_status(state, target, STATUS_STUN))

    def test_active_lock_only_blocks_active_skills(self) -> None:
        state = build_state([("ifrit", 1)], [("fenrir", 1)])
        battle_engine.start_battle(state)

        caster = unit_of(state, "fenrir", GUILD_B)
        target = unit_of(state, "ifrit", GUILD_A)

        use_skill(state, caster, "fenrir_active", {"main": [target.battle_unit_id]})

        self.assertTrue(effects.is_active_locked(state, target))
        self.assertTrue(effects.has_status(state, target, STATUS_ACTIVE_LOCK))

        blocked, _ = effects.is_action_blocked(state, target)
        self.assertFalse(blocked, "ACTIVE使用不能でも通常攻撃はできる")

        with self.assertRaises(Exception):
            use_skill(
                state, target, "ifrit_active", {"main": [caster.battle_unit_id]}
            )

    def test_status_immunity_blocks_new_statuses_only(self) -> None:
        state = build_state([("lucifer", 1), ("garm", 1)], [("medusa", 1)])
        battle_engine.start_battle(state)

        caster = unit_of(state, "lucifer", GUILD_A)
        ally = unit_of(state, "garm", GUILD_A)

        use_skill(state, caster, "lucifer_active", {"main": [ally.battle_unit_id]})
        self.assertTrue(effects.is_status_immune(state, ally))

        self.assertFalse(
            skill_engine.apply_status(
                state, None, ally, STATUS_STUN, duration_turns=1
            )
        )

        # 状態異常無効はATK低下を防がない（8節）
        base = effects.compute_atk(state, ally)
        effects.apply_effect(
            state, ally, effect_type=EFFECT_ATK_MODIFIER, value=-2,
            duration_type="permanent", source_skill_id="test_debuff",
        )
        self.assertEqual(effects.compute_atk(state, ally), base - 2)

    def test_cleanse_status_removes_statuses_but_keeps_debuffs(self) -> None:
        state = build_state([("astaroth", 1), ("garm", 1)], [("medusa", 1)])
        battle_engine.start_battle(state)

        caster = unit_of(state, "astaroth", GUILD_A)
        ally = unit_of(state, "garm", GUILD_A)
        base = effects.compute_atk(state, ally)

        skill_engine.apply_status(state, None, ally, STATUS_STUN, duration_turns=2)
        effects.apply_effect(
            state, ally, effect_type=EFFECT_ATK_MODIFIER, value=-3,
            duration_type="turns", duration_turns=3, source_skill_id="test_debuff",
        )

        use_skill(state, caster, "astaroth_active")

        self.assertFalse(effects.has_status(state, ally, STATUS_STUN))
        self.assertEqual(effects.compute_atk(state, ally), base - 3)

    def test_cleanse_debuff_removes_debuffs_but_keeps_statuses(self) -> None:
        state = build_state([("satan", 1), ("garm", 1)], [("medusa", 1)])
        battle_engine.start_battle(state)

        caster = unit_of(state, "satan", GUILD_A)
        ally = unit_of(state, "garm", GUILD_A)
        base = effects.compute_atk(state, ally)

        skill_engine.apply_status(state, None, ally, STATUS_STUN, duration_turns=2)
        effects.apply_effect(
            state, ally, effect_type=EFFECT_ATK_MODIFIER, value=-3,
            duration_type="turns", duration_turns=3, source_skill_id="test_debuff",
        )
        effects.apply_effect(
            state, ally, effect_type=EFFECT_SPEED_MODIFIER, value=-5,
            duration_type="turns", duration_turns=2, source_skill_id="test_slow",
        )

        use_skill(state, caster, "satan_active")

        self.assertTrue(effects.has_status(state, ally, STATUS_STUN))
        self.assertEqual(effects.compute_atk(state, ally), base)
        self.assertEqual(effects.compute_speed(state, ally), ally.base_speed)

    def test_buffs_are_not_removed_by_cleanse(self) -> None:
        state = build_state([("satan", 1), ("garm", 1)], [("medusa", 1)])
        battle_engine.start_battle(state)

        caster = unit_of(state, "satan", GUILD_A)
        ally = unit_of(state, "garm", GUILD_A)
        base = effects.compute_atk(state, ally)

        effects.apply_effect(
            state, ally, effect_type=EFFECT_ATK_MODIFIER, value=2,
            duration_type="permanent", source_skill_id="test_buff",
        )

        use_skill(state, caster, "satan_active")
        self.assertEqual(effects.compute_atk(state, ally), base + 2)

    def test_poison_from_different_sources_stacks(self) -> None:
        # 7節：異なる使い魔が付与した毒は別効果として重複する
        state = build_state([("garm", 1)], [("beelzebub", 1), ("jormungandr", 1)])
        battle_engine.start_battle(state)

        target = unit_of(state, "garm", GUILD_A)
        caster = unit_of(state, "jormungandr", GUILD_B)
        use_skill(state, caster, "jormungandr_active")

        poisons = [
            effect
            for effect in effects.status_effects(state, target)
            if effect.status_name == STATUS_POISON
        ]
        self.assertEqual(len(poisons), 2, poisons)
        self.assertEqual(
            sorted(int(effect.params["damage"]) for effect in poisons), [2, 3]
        )

    def test_hydra_amplifies_each_poison_once(self) -> None:
        # 7節：ヨルムンガンド毒 2×2 → 3×3、ベルゼブブ毒 3×2 → 4×3
        state = build_state(
            [("garm", 1)], [("hydra", 1), ("beelzebub", 1), ("jormungandr", 1)]
        )
        battle_engine.start_battle(state)

        target = unit_of(state, "garm", GUILD_A)
        caster = unit_of(state, "jormungandr", GUILD_B)
        use_skill(state, caster, "jormungandr_active")

        poisons = [
            effect
            for effect in effects.status_effects(state, target)
            if effect.status_name == STATUS_POISON
        ]
        self.assertEqual(
            sorted(
                (int(effect.params["damage"]), int(effect.remaining))
                for effect in poisons
            ),
            [(3, 3), (4, 3)],
        )

    def test_a_single_amplifier_applies_once_per_poison(self) -> None:
        state = build_state([("garm", 1)], [("hydra", 1), ("beelzebub", 1)])
        battle_engine.start_battle(state)

        target = unit_of(state, "garm", GUILD_A)
        poisons = [
            effect
            for effect in effects.status_effects(state, target)
            if effect.status_name == STATUS_POISON
        ]
        self.assertEqual(len(poisons), 1)
        self.assertEqual(int(poisons[0].params["damage"]), 4)
        self.assertEqual(int(poisons[0].remaining), 3)

    def test_heal_block_prevents_healing(self) -> None:
        state = build_state([("fafnir", 1), ("garm", 1)], [("nidhogg", 1)])
        battle_engine.start_battle(state)

        healer = unit_of(state, "fafnir", GUILD_A)
        target = unit_of(state, "garm", GUILD_A)
        attacker = unit_of(state, "nidhogg", GUILD_B)

        target.current_hp = 20
        battle_engine.perform_attack(state, attacker, target)
        self.assertTrue(effects.is_heal_blocked(state, target))

        before = target.current_hp
        battle_engine.heal_unit(state, healer, target, 20)
        self.assertEqual(target.current_hp, before)

    def test_repeating_the_same_skill_refreshes_instead_of_stacking(self) -> None:
        # 7節：同じ使い魔が同じスキルを同じ対象へ再使用しても重複しない
        state = build_state([("azazel", 1), ("garm", 1)], [("behemoth", 1)])
        battle_engine.start_battle(state)

        caster = unit_of(state, "azazel", GUILD_A)
        ally = unit_of(state, "garm", GUILD_A)
        base = ally.base_atk

        use_skill(state, caster, "azazel_active", {"main": [ally.battle_unit_id]})
        self.assertEqual(effects.compute_atk(state, ally), base + 1)

        applied = [
            effect
            for effect in state.unit_effects(ally.battle_unit_id)
            if effect.source_skill_id == "azazel_active"
        ]
        applied[0].remaining = 1

        use_skill(state, caster, "azazel_active", {"main": [ally.battle_unit_id]})

        applied = [
            effect
            for effect in state.unit_effects(ally.battle_unit_id)
            if effect.source_skill_id == "azazel_active"
        ]
        self.assertEqual(len(applied), 1)
        self.assertEqual(applied[0].remaining, 2, "残りターンは長い方へ揃える")
        self.assertEqual(effects.compute_atk(state, ally), base + 1)


# ==================================================
# 表示（GAME_SPEC 23節・24節）
# ==================================================
class DisplayTests(unittest.TestCase):
    def test_stat_shows_the_change_from_the_base_value(self) -> None:
        from game import battle_embed

        self.assertEqual(battle_embed.stat_with_delta(15, 13), "15（+2）")
        self.assertEqual(battle_embed.stat_with_delta(7, 9), "7（-2）")
        self.assertEqual(battle_embed.stat_with_delta(9, 9), "9")

    def test_status_line_uses_the_inline_delta(self) -> None:
        from game import battle_embed

        state = build_state([("mammon", 1)], [("garm", 1)])
        battle_engine.start_battle(state)

        caster = unit_of(state, "mammon", GUILD_A)
        target = unit_of(state, "garm", GUILD_B)

        use_skill(state, caster, "mammon_active", {"main": [target.battle_unit_id]})

        self.assertIn("（+2）", battle_embed.atk_text(caster))
        self.assertIn("（-2）", battle_embed.atk_text(target))
        self.assertIn("ATK", battle_embed.stat_line(state, caster))
        # 基礎値を末尾へ書く旧表記は使わない
        self.assertNotIn("基礎ATK", battle_embed.stat_line(state, caster))

    def test_speed_shows_the_change_too(self) -> None:
        from game import battle_embed

        state = build_state([("pegasus", 1), ("loki", 1)], [("garm", 1)])
        battle_engine.start_battle(state)

        caster = unit_of(state, "pegasus", GUILD_A)
        loki = unit_of(state, "loki", GUILD_A)

        use_skill(state, caster, "pegasus_active", {"main": [loki.battle_unit_id]})

        self.assertEqual(battle_embed.speed_text(loki), "96（+12）")

    def test_turn_time_is_two_minutes(self) -> None:
        self.assertEqual(MASTER.battle.turn_time_seconds, 120)


# ==================================================
# 現在SPDと行動順（BATTLE_RULES.md 1節・7節）
# ==================================================
class SpeedTests(unittest.TestCase):
    def test_speed_buff_and_debuff_add_up(self) -> None:
        # 7節の例：基礎SPD84 + 12 - 20 = 76
        state = build_state([("loki", 1)], [("behemoth", 1)])
        battle_engine.start_battle(state)

        loki = unit_of(state, "loki", GUILD_A)
        self.assertEqual(loki.base_speed, 84)

        effects.apply_effect(
            state, loki, effect_type=EFFECT_SPEED_MODIFIER, value=12,
            duration_type="turns", duration_turns=2, source_skill_id="test_up",
        )
        effects.apply_effect(
            state, loki, effect_type=EFFECT_SPEED_MODIFIER, value=-20,
            duration_type="turns", duration_turns=2, source_skill_id="test_down",
        )

        self.assertEqual(effects.compute_speed(state, loki), 76)
        self.assertEqual(loki.speed, 76)

    def test_speed_never_goes_below_zero(self) -> None:
        state = build_state([("mandragora", 1)], [("behemoth", 1)])
        battle_engine.start_battle(state)

        unit = unit_of(state, "mandragora", GUILD_A)
        effects.apply_effect(
            state, unit, effect_type=EFFECT_SPEED_MODIFIER, value=-99,
            duration_type="permanent", source_skill_id="test_down",
        )
        self.assertEqual(effects.compute_speed(state, unit), 0)

    def test_no_two_familiars_share_a_base_speed(self) -> None:
        # 1節「基礎SPDは原則として全使い魔で重複させない」
        speeds = [familiar.speed for familiar in MASTER.familiars.values()]
        self.assertEqual(len(speeds), len(set(speeds)))

    def test_same_current_speed_puts_the_higher_base_speed_first(self) -> None:
        state = build_state([("kyubi", 1)], [("pegasus", 1)])
        battle_engine.start_battle(state)

        kyubi = unit_of(state, "kyubi", GUILD_A)  # 基礎97
        pegasus = unit_of(state, "pegasus", GUILD_B)  # 基礎98

        effects.apply_effect(
            state, pegasus, effect_type=EFFECT_SPEED_MODIFIER, value=-1,
            duration_type="permanent", source_skill_id="test_down",
        )
        self.assertEqual(kyubi.speed, pegasus.speed)

        order = battle_engine._order_units(state)
        self.assertEqual(order[0], pegasus.battle_unit_id)

    def test_pegasus_pushes_loki_ahead_of_fenrir(self) -> None:
        state = build_state([("pegasus", 1), ("loki", 1)], [("fenrir", 1)])
        battle_engine.start_battle(state)

        caster = unit_of(state, "pegasus", GUILD_A)
        loki = unit_of(state, "loki", GUILD_A)
        fenrir = unit_of(state, "fenrir", GUILD_B)

        use_skill(state, caster, "pegasus_active", {"main": [loki.battle_unit_id]})

        self.assertEqual(loki.speed, 96)
        self.assertGreater(loki.speed, fenrir.speed)

    def test_finished_turns_are_not_resorted(self) -> None:
        # 1節：行動済みは並び替えず、未行動だけを現在SPDで並び替える
        state = build_state(
            [("kyubi", 1), ("garm", 1)], [("griffin", 1), ("minotaur", 1)]
        )
        battle_engine.start_battle(state)

        kyubi = unit_of(state, "kyubi", GUILD_A)
        griffin = unit_of(state, "griffin", GUILD_B)  # SPD89

        self.assertEqual(state.turn_order[0], kyubi.battle_unit_id)
        self.assertEqual(state.turn_order[1], griffin.battle_unit_id)

        use_skill(state, kyubi, "kyubi_active", {"main": [griffin.battle_unit_id]})
        battle_engine.auto_action(state, elapsed_seconds=0)

        self.assertEqual(griffin.speed, 69)
        self.assertEqual(state.turn_order[0], kyubi.battle_unit_id)
        self.assertEqual(len(state.turn_order), len(set(state.turn_order)))


# ==================================================
# 戦闘不能・蘇生（BATTLE_RULES.md 5節）
# ==================================================
class DefeatTests(unittest.TestCase):
    def test_dullahan_survives_lethal_attack_damage_once(self) -> None:
        state = build_state([("dullahan", 1)], [("cyclops", 1)])
        battle_engine.start_battle(state)

        dullahan = unit_of(state, "dullahan", GUILD_A)
        attacker = unit_of(state, "cyclops", GUILD_B)

        dullahan.current_hp = 3
        battle_engine.perform_attack(state, attacker, dullahan)

        self.assertTrue(dullahan.alive)
        self.assertEqual(dullahan.current_hp, 1)

        battle_engine.perform_attack(state, attacker, dullahan)
        self.assertFalse(dullahan.alive)

    def test_dullahan_does_not_survive_skill_damage(self) -> None:
        state = build_state([("dullahan", 1)], [("ifrit", 1)])
        battle_engine.start_battle(state)

        dullahan = unit_of(state, "dullahan", GUILD_A)
        caster = unit_of(state, "ifrit", GUILD_B)

        dullahan.current_hp = 3
        use_skill(state, caster, "ifrit_active", {"main": [dullahan.battle_unit_id]})

        self.assertFalse(dullahan.alive)

    def test_instant_defeat_ignores_damage_based_survival(self) -> None:
        state = build_state([("dullahan", 1)], [("hel", 1)])
        battle_engine.start_battle(state)

        dullahan = unit_of(state, "dullahan", GUILD_A)
        caster = unit_of(state, "hel", GUILD_B)

        use_skill(state, caster, "hel_active", {"main": [dullahan.battle_unit_id]})

        self.assertFalse(dullahan.alive)

    def test_hel_revives_the_first_fallen_ally(self) -> None:
        state = build_state([("hel", 1), ("garm", 1)], [("cyclops", 1)])
        battle_engine.start_battle(state)

        ally = unit_of(state, "garm", GUILD_A)
        attacker = unit_of(state, "cyclops", GUILD_B)

        ally.current_hp = 1
        battle_engine.perform_attack(state, attacker, ally)

        self.assertTrue(ally.alive)
        self.assertEqual(ally.current_hp, 10)

    def test_banshee_adds_healing_on_revive(self) -> None:
        # 13節：ヘルのHP10蘇生なら最終HP18で復帰する
        state = build_state(
            [("hel", 1), ("banshee", 1), ("garm", 1)], [("cyclops", 1)]
        )
        battle_engine.start_battle(state)

        ally = unit_of(state, "garm", GUILD_A)
        attacker = unit_of(state, "cyclops", GUILD_B)

        ally.current_hp = 1
        battle_engine.perform_attack(state, attacker, ally)

        self.assertTrue(ally.alive)
        self.assertEqual(ally.current_hp, 18)

    def test_phoenix_revives_a_defeated_ally(self) -> None:
        state = build_state([("phoenix", 1), ("garm", 1)], [("cyclops", 1)])
        battle_engine.start_battle(state)

        caster = unit_of(state, "phoenix", GUILD_A)
        ally = unit_of(state, "garm", GUILD_A)

        ally.alive = False
        ally.current_hp = 0

        use_skill(state, caster, "phoenix_active", {"main": [ally.battle_unit_id]})

        self.assertTrue(ally.alive)
        self.assertEqual(ally.current_hp, 16)

    def test_effects_survive_defeat_and_revive(self) -> None:
        state = build_state([("phoenix", 1), ("garm", 1)], [("cyclops", 1)])
        battle_engine.start_battle(state)

        caster = unit_of(state, "phoenix", GUILD_A)
        ally = unit_of(state, "garm", GUILD_A)

        effects.apply_effect(
            state, ally, effect_type=EFFECT_ATK_MODIFIER, value=3,
            duration_type="permanent", source_skill_id="test_buff",
        )
        base = ally.base_atk

        ally.alive = False
        ally.current_hp = 0

        use_skill(state, caster, "phoenix_active", {"main": [ally.battle_unit_id]})

        self.assertEqual(effects.compute_atk(state, ally), base + 3)

    def test_heal_block_prevents_reviving(self) -> None:
        state = build_state([("phoenix", 1), ("garm", 1)], [("nidhogg", 1)])
        battle_engine.start_battle(state)

        caster = unit_of(state, "phoenix", GUILD_A)
        ally = unit_of(state, "garm", GUILD_A)
        attacker = unit_of(state, "nidhogg", GUILD_B)

        ally.current_hp = 1
        battle_engine.perform_attack(state, attacker, ally)
        self.assertFalse(ally.alive)
        self.assertTrue(effects.is_heal_blocked(state, ally))

        use_skill(state, caster, "phoenix_active", {"main": [ally.battle_unit_id]})
        self.assertFalse(ally.alive, "回復阻害中は蘇生できない")

    def test_durations_tick_while_defeated(self) -> None:
        # 5節：戦闘不能中でも本来の行動順で持続ターンが1減る
        state = build_state([("garm", 1)], [("behemoth", 1)])
        battle_engine.start_battle(state)

        unit = unit_of(state, "garm", GUILD_A)
        effects.apply_effect(
            state, unit, effect_type=EFFECT_ATK_MODIFIER, value=-2,
            duration_type="turns", duration_turns=2, source_skill_id="test_debuff",
        )

        unit.alive = False
        unit.current_hp = 0

        # 付与したターンでは減らさないため、次のターン位置へ進めてから確認する
        state.turn_index += 1

        before = state.unit_effects(unit.battle_unit_id)[0].remaining
        battle_engine._tick_defeated_turn(state, unit)
        after = state.unit_effects(unit.battle_unit_id)[0].remaining

        self.assertEqual(before - after, 1)

    def test_a_revived_unit_can_still_act_this_round(self) -> None:
        state = build_state([("phoenix", 1), ("garm", 1)], [("behemoth", 1)])
        battle_engine.start_battle(state)

        ally = unit_of(state, "garm", GUILD_A)
        ally.alive = False
        ally.current_hp = 0

        caster = unit_of(state, "phoenix", GUILD_A)
        use_skill(state, caster, "phoenix_active", {"main": [ally.battle_unit_id]})

        self.assertTrue(ally.alive)
        self.assertNotEqual(ally.state_flags.get("acted_round"), state.current_round)
        self.assertIn(ally.battle_unit_id, state.turn_order)

    def test_a_unit_that_already_acted_does_not_act_again(self) -> None:
        state = build_state([("phoenix", 1), ("garm", 1)], [("behemoth", 1)])
        battle_engine.start_battle(state)

        ally = unit_of(state, "garm", GUILD_A)
        force_turn(state, ally)
        battle_engine.auto_action(state, elapsed_seconds=0)
        self.assertEqual(ally.state_flags.get("acted_round"), 1)

        ally.alive = False
        ally.current_hp = 0

        caster = unit_of(state, "phoenix", GUILD_A)
        use_skill(state, caster, "phoenix_active", {"main": [ally.battle_unit_id]})

        self.assertTrue(ally.alive)
        self.assertEqual(ally.state_flags.get("acted_round"), 1)


# ==================================================
# パッシブスキル（BATTLE_RULES.md 11〜13節）
# ==================================================
class PassiveTests(unittest.TestCase):
    def test_surtr_reduces_attack_damage_only(self) -> None:
        state = build_state([("surtr", 1)], [("cyclops", 1), ("ifrit", 1)])
        battle_engine.start_battle(state)

        surtr = unit_of(state, "surtr", GUILD_A)
        attacker = unit_of(state, "cyclops", GUILD_B)  # ATK10

        before = surtr.current_hp
        battle_engine.perform_attack(state, attacker, surtr)
        self.assertEqual(before - surtr.current_hp, 8)

        caster = unit_of(state, "ifrit", GUILD_B)
        before = surtr.current_hp
        use_skill(state, caster, "ifrit_active", {"main": [surtr.battle_unit_id]})
        self.assertEqual(before - surtr.current_hp, 6, "スキルダメージは軽減しない")

    def test_surtr_gains_atk_for_each_fallen_ally(self) -> None:
        state = build_state(
            [("surtr", 1), ("garm", 1), ("griffin", 1)], [("cyclops", 1)]
        )
        battle_engine.start_battle(state)

        surtr = unit_of(state, "surtr", GUILD_A)
        base = effects.compute_atk(state, surtr)

        for name in ("garm", "griffin"):
            battle_engine.instant_defeat(state, None, unit_of(state, name, GUILD_A))

        self.assertEqual(effects.compute_atk(state, surtr), base + 4)

    def test_fenrir_boosts_the_first_attack_on_an_unacted_enemy(self) -> None:
        state = build_state([("fenrir", 1)], [("behemoth", 1), ("minotaur", 1)])
        battle_engine.start_battle(state)

        fenrir = unit_of(state, "fenrir", GUILD_A)
        first = unit_of(state, "behemoth", GUILD_B)
        second = unit_of(state, "minotaur", GUILD_B)

        before = first.current_hp
        battle_engine.perform_attack(state, fenrir, first)
        self.assertEqual(before - first.current_hp, fenrir.base_atk + 6)

        before = second.current_hp
        battle_engine.perform_attack(state, fenrir, second)
        self.assertEqual(
            before - second.current_hp, fenrir.base_atk, "同じラウンドの2回目は乗らない"
        )

    def test_fenrir_does_not_boost_an_enemy_that_already_acted(self) -> None:
        state = build_state([("fenrir", 1)], [("behemoth", 1)])
        battle_engine.start_battle(state)

        fenrir = unit_of(state, "fenrir", GUILD_A)
        target = unit_of(state, "behemoth", GUILD_B)
        target.state_flags["acted_round"] = state.current_round

        before = target.current_hp
        battle_engine.perform_attack(state, fenrir, target)
        self.assertEqual(before - target.current_hp, fenrir.base_atk)

    def test_jormungandr_lowers_enemy_atk_at_battle_start(self) -> None:
        state = build_state([("jormungandr", 1)], [("garm", 1)])
        battle_engine.start_battle(state)

        enemy = unit_of(state, "garm", GUILD_B)
        self.assertEqual(effects.compute_atk(state, enemy), enemy.base_atk - 1)

    def test_asmodeus_raises_ally_atk_at_battle_start(self) -> None:
        state = build_state([("asmodeus", 1), ("garm", 1)], [("behemoth", 1)])
        battle_engine.start_battle(state)

        ally = unit_of(state, "garm", GUILD_A)
        self.assertEqual(effects.compute_atk(state, ally), ally.base_atk + 1)

    def test_beelzebub_poisons_every_enemy_at_battle_start(self) -> None:
        state = build_state([("beelzebub", 1)], [("garm", 1), ("griffin", 1)])
        battle_engine.start_battle(state)

        for name in ("garm", "griffin"):
            enemy = unit_of(state, name, GUILD_B)
            self.assertTrue(effects.has_status(state, enemy, STATUS_POISON))

    def test_belphegor_stuns_male_enemies_only(self) -> None:
        state = build_state([("belphegor", 1)], [("garm", 1), ("medusa", 1)])
        battle_engine.start_battle(state)

        belphegor = unit_of(state, "belphegor", GUILD_A)
        male = unit_of(state, "garm", GUILD_B)
        female = unit_of(state, "medusa", GUILD_B)

        battle_engine.perform_attack(state, belphegor, male)
        self.assertTrue(effects.has_status(state, male, STATUS_STUN))

        battle_engine.perform_attack(state, belphegor, female)
        self.assertFalse(effects.has_status(state, female, STATUS_STUN))

    def test_lilith_lowers_male_enemy_atk_only(self) -> None:
        state = build_state(
            [("lilith", 1)], [("garm", 1), ("medusa", 1), ("behemoth", 1)]
        )
        battle_engine.start_battle(state)

        male = unit_of(state, "garm", GUILD_B)
        female = unit_of(state, "medusa", GUILD_B)
        genderless = unit_of(state, "behemoth", GUILD_B)

        self.assertEqual(effects.compute_atk(state, male), male.base_atk - 2)
        self.assertEqual(effects.compute_atk(state, female), female.base_atk)
        self.assertEqual(effects.compute_atk(state, genderless), genderless.base_atk)

    def test_siren_only_weakens_the_strongest_male_enemy(self) -> None:
        state = build_state([("siren", 1)], [("cyclops", 1), ("garm", 1)])
        battle_engine.start_battle(state)

        strongest = unit_of(state, "cyclops", GUILD_B)  # ATK10
        other = unit_of(state, "garm", GUILD_B)  # ATK9

        self.assertEqual(effects.compute_atk(state, strongest), strongest.base_atk - 1)
        self.assertEqual(effects.compute_atk(state, other), other.base_atk)

    def test_cerberus_boosts_attacks_on_weakened_enemies(self) -> None:
        state = build_state([("cerberus", 1)], [("behemoth", 1)])
        battle_engine.start_battle(state)

        cerberus = unit_of(state, "cerberus", GUILD_A)
        target = unit_of(state, "behemoth", GUILD_B)

        before = target.current_hp
        battle_engine.perform_attack(state, cerberus, target)
        self.assertEqual(before - target.current_hp, cerberus.base_atk)

        target.current_hp = target.max_hp // 2
        before = target.current_hp
        battle_engine.perform_attack(state, cerberus, target)
        self.assertEqual(before - target.current_hp, cerberus.base_atk + 2)

    def test_chimera_boosts_attacks_on_afflicted_enemies(self) -> None:
        state = build_state([("chimera", 1)], [("behemoth", 1)])
        battle_engine.start_battle(state)

        chimera = unit_of(state, "chimera", GUILD_A)
        target = unit_of(state, "behemoth", GUILD_B)

        before = target.current_hp
        battle_engine.perform_attack(state, chimera, target)
        self.assertEqual(before - target.current_hp, chimera.base_atk)

        effects.apply_effect(
            state, target, effect_type=EFFECT_ATK_MODIFIER, value=-1,
            duration_type="permanent", source_skill_id="test_debuff",
        )
        before = target.current_hp
        battle_engine.perform_attack(state, chimera, target)
        self.assertEqual(before - target.current_hp, chimera.base_atk + 2)

    def test_hraesvelgr_slows_the_enemy_it_hits(self) -> None:
        state = build_state([("hraesvelgr", 1)], [("griffin", 1)])
        battle_engine.start_battle(state)

        attacker = unit_of(state, "hraesvelgr", GUILD_A)
        target = unit_of(state, "griffin", GUILD_B)

        battle_engine.perform_attack(state, attacker, target)
        self.assertEqual(target.speed, target.base_speed - 5)

    def test_kraken_only_reacts_to_normal_attacks(self) -> None:
        state = build_state([("kraken", 1)], [("cyclops", 1), ("ifrit", 1)])
        battle_engine.start_battle(state)

        kraken = unit_of(state, "kraken", GUILD_A)
        attacker = unit_of(state, "cyclops", GUILD_B)

        battle_engine.perform_attack(state, attacker, kraken)
        self.assertEqual(attacker.speed, attacker.base_speed - 4)

        caster = unit_of(state, "ifrit", GUILD_B)
        use_skill(state, caster, "ifrit_active", {"main": [kraken.battle_unit_id]})
        self.assertEqual(caster.speed, caster.base_speed)

    def test_paimon_heals_the_weakest_ally_on_its_turn(self) -> None:
        state = build_state(
            [("paimon", 1), ("garm", 1), ("griffin", 1)], [("behemoth", 1)]
        )
        battle_engine.start_battle(state)

        weakest = unit_of(state, "garm", GUILD_A)
        healthy = unit_of(state, "griffin", GUILD_A)
        weakest.current_hp = 5

        paimon = unit_of(state, "paimon", GUILD_A)

        # ターン開始パッシブは行動順を進めたときに発動する
        for _ in range(len(state.turn_order)):
            if state.current_unit_id == paimon.battle_unit_id:
                break
            battle_engine.auto_action(state, elapsed_seconds=0)

        self.assertEqual(state.current_unit_id, paimon.battle_unit_id)
        self.assertEqual(weakest.current_hp, 9)
        self.assertEqual(healthy.current_hp, healthy.max_hp)

    def test_sphinx_nullifies_only_the_first_status(self) -> None:
        state = build_state([("sphinx", 1)], [("medusa", 1)])
        battle_engine.start_battle(state)

        sphinx = unit_of(state, "sphinx", GUILD_A)

        self.assertFalse(
            skill_engine.apply_status(
                state, None, sphinx, STATUS_STUN, duration_turns=1
            )
        )
        self.assertTrue(
            skill_engine.apply_status(
                state, None, sphinx, STATUS_STUN, duration_turns=1
            )
        )

    def test_belial_nullifies_the_first_stun_on_an_ally(self) -> None:
        state = build_state([("belial", 1), ("garm", 1)], [("medusa", 1)])
        battle_engine.start_battle(state)

        ally = unit_of(state, "garm", GUILD_A)

        self.assertFalse(
            skill_engine.apply_status(
                state, None, ally, STATUS_STUN, duration_turns=1
            )
        )
        self.assertTrue(
            skill_engine.apply_status(
                state, None, ally, STATUS_STUN, duration_turns=1
            )
        )

    def test_belial_does_not_block_poison(self) -> None:
        state = build_state([("belial", 1), ("garm", 1)], [("medusa", 1)])
        battle_engine.start_battle(state)

        ally = unit_of(state, "garm", GUILD_A)
        self.assertTrue(
            skill_engine.apply_status(
                state, None, ally, STATUS_POISON, duration_turns=2, damage=2
            )
        )

    def test_loki_reflects_a_status_back_to_its_source(self) -> None:
        state = build_state([("loki", 1)], [("medusa", 1)])
        battle_engine.start_battle(state)

        loki = unit_of(state, "loki", GUILD_A)
        source = unit_of(state, "medusa", GUILD_B)

        use_skill(state, source, "medusa_active", {"main": [loki.battle_unit_id]})

        self.assertFalse(effects.has_status(state, loki, STATUS_STUN))
        self.assertTrue(effects.has_status(state, source, STATUS_STUN))

    def test_loki_still_takes_the_skill_damage_of_world_poison(self) -> None:
        # 11節の注記：4スキルダメージは受け、毒だけを無効化・反射する
        state = build_state([("loki", 1)], [("jormungandr", 1)])
        battle_engine.start_battle(state)

        loki = unit_of(state, "loki", GUILD_A)
        caster = unit_of(state, "jormungandr", GUILD_B)
        before = loki.current_hp

        use_skill(state, caster, "jormungandr_active")

        self.assertEqual(before - loki.current_hp, 4)
        self.assertFalse(effects.has_status(state, loki, STATUS_POISON))
        self.assertTrue(effects.has_status(state, caster, STATUS_POISON))

    def test_nidhogg_debuff_does_not_stack_on_the_same_target(self) -> None:
        state = build_state([("nidhogg", 1)], [("behemoth", 1)])
        battle_engine.start_battle(state)

        attacker = unit_of(state, "nidhogg", GUILD_A)
        target = unit_of(state, "behemoth", GUILD_B)

        battle_engine.perform_attack(state, attacker, target)
        battle_engine.perform_attack(state, attacker, target)

        blocks = [
            effect
            for effect in state.unit_effects(target.battle_unit_id)
            if effect.effect_type == EFFECT_HEAL_BLOCK
        ]
        self.assertEqual(len(blocks), 1)


# ==================================================
# アクティブスキル（BATTLE_RULES.md 2節・11〜13節）
# ==================================================
class ActiveSkillTests(unittest.TestCase):
    def test_active_skill_can_be_used_once_per_battle(self) -> None:
        state = build_state([("ifrit", 1)], [("behemoth", 1)])
        battle_engine.start_battle(state)

        caster = unit_of(state, "ifrit", GUILD_A)
        target = unit_of(state, "behemoth", GUILD_B)

        use_skill(state, caster, "ifrit_active", {"main": [target.battle_unit_id]})

        with self.assertRaises(Exception):
            use_skill(
                state, caster, "ifrit_active", {"main": [target.battle_unit_id]}
            )

    def test_unlimited_active_skill_can_be_reused(self) -> None:
        state = build_state([("abaddon", 1)], [("behemoth", 1)])
        battle_engine.start_battle(state)

        caster = unit_of(state, "abaddon", GUILD_A)
        target = unit_of(state, "behemoth", GUILD_B)

        before = target.current_hp
        use_skill(state, caster, "abaddon_active")
        use_skill(state, caster, "abaddon_active")

        self.assertEqual(before - target.current_hp, 10)

    def test_skill_damage_never_criticals(self) -> None:
        state = build_state(
            [("ifrit", 1)], [("behemoth", 1)], rng=AlwaysCriticalRandom()
        )
        battle_engine.start_battle(state)

        caster = unit_of(state, "ifrit", GUILD_A)
        target = unit_of(state, "behemoth", GUILD_B)
        before = target.current_hp

        use_skill(state, caster, "ifrit_active", {"main": [target.battle_unit_id]})

        self.assertEqual(before - target.current_hp, 6)

    def test_area_skill_hits_every_living_enemy(self) -> None:
        state = build_state(
            [("abaddon", 1)], [("behemoth", 1), ("minotaur", 1), ("garm", 1)]
        )
        battle_engine.start_battle(state)

        caster = unit_of(state, "abaddon", GUILD_A)
        fallen = unit_of(state, "garm", GUILD_B)
        fallen.alive = False
        fallen.current_hp = 0

        targets = [
            unit_of(state, name, GUILD_B) for name in ("behemoth", "minotaur")
        ]
        before = {unit.battle_unit_id: unit.current_hp for unit in targets}

        use_skill(state, caster, "abaddon_active")

        for unit in targets:
            self.assertEqual(before[unit.battle_unit_id] - unit.current_hp, 5)
        self.assertEqual(fallen.current_hp, 0)

    def test_surtr_attacks_twice_with_its_own_bonus(self) -> None:
        state = build_state([("surtr", 1)], [("behemoth", 1), ("minotaur", 1)])
        battle_engine.start_battle(state)

        caster = unit_of(state, "surtr", GUILD_A)  # ATK10
        first = unit_of(state, "behemoth", GUILD_B)
        second = unit_of(state, "minotaur", GUILD_B)

        before_first = first.current_hp
        before_second = second.current_hp

        use_skill(
            state,
            caster,
            "surtr_active",
            {"main": [first.battle_unit_id, second.battle_unit_id]},
        )

        self.assertEqual(before_first - first.current_hp, 13)
        self.assertEqual(before_second - second.current_hp, 13)

        before = first.current_hp
        battle_engine.perform_attack(state, caster, first)
        self.assertEqual(before - first.current_hp, 10, "ATK+3は2回攻撃だけ")

    def test_taunt_forces_normal_attacks_but_not_attack_actives(self) -> None:
        state = build_state(
            [("surtr", 1), ("garm", 1)], [("leviathan", 1), ("behemoth", 1)]
        )
        battle_engine.start_battle(state)

        leviathan = unit_of(state, "leviathan", GUILD_B)
        other = unit_of(state, "behemoth", GUILD_B)
        surtr = unit_of(state, "surtr", GUILD_A)
        garm = unit_of(state, "garm", GUILD_A)

        use_skill(state, leviathan, "leviathan_active")

        self.assertEqual(
            [
                unit.battle_unit_id
                for unit in battle_engine.attack_target_choices(state, garm)
            ],
            [leviathan.battle_unit_id],
        )

        choices = skill_engine.selectable_targets(
            state, surtr, MASTER.get_skill("surtr_active").targets[0]
        )
        self.assertIn(
            other.battle_unit_id, [unit.battle_unit_id for unit in choices]
        )

        before = other.current_hp
        use_skill(
            state,
            surtr,
            "surtr_active",
            {"main": [other.battle_unit_id, other.battle_unit_id]},
        )
        self.assertLess(other.current_hp, before)

        self.assertEqual(
            [
                unit.battle_unit_id
                for unit in battle_engine.attack_target_choices(state, surtr)
            ],
            [leviathan.battle_unit_id],
            "ACTIVE後の通常攻撃は挑発から解放されない",
        )

    def test_mammon_moves_atk_from_the_enemy_to_itself(self) -> None:
        state = build_state([("mammon", 1)], [("garm", 1)])
        battle_engine.start_battle(state)

        caster = unit_of(state, "mammon", GUILD_A)
        target = unit_of(state, "garm", GUILD_B)

        use_skill(state, caster, "mammon_active", {"main": [target.battle_unit_id]})

        self.assertEqual(effects.compute_atk(state, caster), caster.base_atk + 2)
        self.assertEqual(effects.compute_atk(state, target), target.base_atk - 2)

    def test_kyubi_and_yuki_onna_lower_speed(self) -> None:
        state = build_state(
            [("kyubi", 1), ("yuki_onna", 1)], [("griffin", 1), ("garm", 1)]
        )
        battle_engine.start_battle(state)

        kyubi = unit_of(state, "kyubi", GUILD_A)
        yuki = unit_of(state, "yuki_onna", GUILD_A)
        griffin = unit_of(state, "griffin", GUILD_B)
        garm = unit_of(state, "garm", GUILD_B)

        use_skill(state, kyubi, "kyubi_active", {"main": [griffin.battle_unit_id]})
        self.assertEqual(griffin.speed, griffin.base_speed - 20)

        use_skill(state, yuki, "yuki_onna_active")
        self.assertEqual(griffin.speed, griffin.base_speed - 25)
        self.assertEqual(garm.speed, garm.base_speed - 5)

    def test_atk_swap_exchanges_current_atk(self) -> None:
        state = build_state([("loki", 1), ("pegasus", 1)], [("cyclops", 1)])
        battle_engine.start_battle(state)

        loki = unit_of(state, "loki", GUILD_A)
        ally = unit_of(state, "pegasus", GUILD_A)  # ATK5
        enemy = unit_of(state, "cyclops", GUILD_B)  # ATK10

        use_skill(
            state,
            loki,
            "loki_active",
            {"main": [enemy.battle_unit_id], "ally": [ally.battle_unit_id]},
        )

        self.assertEqual(effects.compute_atk(state, ally), 10)
        self.assertEqual(effects.compute_atk(state, enemy), 5)

    def test_heal_never_exceeds_max_hp_and_skips_defeated_units(self) -> None:
        state = build_state([("fafnir", 1), ("garm", 1)], [("behemoth", 1)])
        battle_engine.start_battle(state)

        caster = unit_of(state, "fafnir", GUILD_A)
        ally = unit_of(state, "garm", GUILD_A)

        ally.current_hp = ally.max_hp - 3
        use_skill(state, caster, "fafnir_active", {"main": [ally.battle_unit_id]})
        self.assertEqual(ally.current_hp, ally.max_hp)

        ally.alive = False
        ally.current_hp = 0
        self.assertEqual(battle_engine.heal_unit(state, caster, ally, 20), 0)

    def test_attack_consuming_actives_are_marked(self) -> None:
        # 2節：攻撃権を消費するACTIVEには必ず記載する
        self.assertTrue(MASTER.get_skill("ifrit_active").consumes_attack)
        self.assertTrue(MASTER.get_skill("surtr_active").consumes_attack)
        self.assertFalse(MASTER.get_skill("fenrir_active").consumes_attack)
        self.assertFalse(MASTER.get_skill("loki_active").consumes_attack)

        for skill in MASTER.skills.values():
            if skill.is_active and skill.consumes_attack:
                self.assertIn(
                    "このターンの攻撃を消費する", skill.description, skill.skill_id
                )


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
