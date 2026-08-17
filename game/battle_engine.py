"""ギルドバトルの進行と戦闘計算（GAME_SPEC 15節～18節、25節、26節）。

Discordのボタンやメッセージからは完全に分離しています。入力は
``BattleState`` と ``BattleAction`` だけで、出力は更新後の ``BattleState`` と
``state.logs`` に積まれた行動ログです。
"""

from __future__ import annotations

import logging
import random

from typing import Any

from . import effects as effects_module
from . import skill_engine
from .master_data import load_master_data
from .models import (
    ACTION_ATTACK,
    ACTION_SKILL,
    BATTLE_STATUS_ABORTED,
    BATTLE_STATUS_FINISHED,
    BATTLE_STATUS_IN_PROGRESS,
    BATTLE_STATUS_PREPARING,
    RESULT_ABORTED,
    RESULT_DRAW,
    RESULT_GUILD_A,
    RESULT_GUILD_B,
    TRIGGER_AFTER_ATTACK,
    TRIGGER_BATTLE_START,
    TRIGGER_BEFORE_ACTION,
    TRIGGER_BEFORE_ATTACK,
    TRIGGER_BEFORE_DEFEAT,
    TRIGGER_ON_DAMAGE,
    TRIGGER_ON_DEFEAT,
    TRIGGER_ON_REVIVE,
    TRIGGER_ROUND_END,
    TRIGGER_ROUND_START,
    TRIGGER_TURN_END,
    TRIGGER_TURN_START,
    BattleAction,
    BattleEvent,
    BattleRuleError,
    BattleState,
    BattleUnit,
    round_half_up,
)


logger = logging.getLogger(__name__)

# 行動可能な使い魔を探すときの安全上限。無限ループを避けるためだけに使う。
_MAX_ADVANCE_STEPS = 500

_rng = random.Random()


def set_random(rng: random.Random) -> None:
    """乱数生成器を差し替える（自動テスト用）。"""

    global _rng
    _rng = rng


def roll_permille(state: BattleState, permille: int) -> bool:
    """千分率の確率判定。"""

    if permille <= 0:
        return False
    if permille >= 1000:
        return True
    return _rng.randrange(1000) < permille


# ==================================================
# 戦闘用使い魔の生成（13節・14節）
# ==================================================
def build_unit_payloads(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """出場者と所有使い魔から、戦闘用使い魔の初期値を作る。

    ``entries`` の各要素は ``guild_id`` ``player_id`` ``instance_id``
    ``familiar_id`` ``level`` ``slot`` を持つ辞書です。
    """

    master = load_master_data()
    payloads = []

    for entry in entries:
        familiar = master.get_familiar(entry["familiar_id"])
        if familiar is None:
            raise BattleRuleError(
                f"使い魔マスターが見つかりません: {entry['familiar_id']}"
            )

        stats = master.level_stats(familiar.familiar_id, int(entry["level"]))
        if stats is None:
            raise BattleRuleError("使い魔の能力値を計算できませんでした。")

        payloads.append(
            {
                "guild_id": int(entry["guild_id"]),
                "player_id": int(entry["player_id"]),
                "familiar_instance_id": int(entry["instance_id"]),
                "familiar_id": familiar.familiar_id,
                "level": int(entry["level"]),
                "max_hp": stats.max_hp,
                "base_atk": stats.atk,
                "speed": stats.speed,
                "base_speed": stats.speed,
                "cost": familiar.cost,
                "gender": familiar.gender,
                "slot": int(entry["slot"]),
                "active_skill_uses": {},
            }
        )

    return payloads


# ==================================================
# バトル開始
# ==================================================
def start_battle(state: BattleState) -> None:
    """バトルを開始し、最初の行動者が決まるところまで進める。"""

    if state.status != BATTLE_STATUS_PREPARING:
        raise BattleRuleError("このバトルは既に開始しています。")

    state.status = BATTLE_STATUS_IN_PROGRESS
    # バトル開始時パッシブは第1ラウンドの効果として扱う（19.3節・20節）。
    state.current_round = 1
    state.turn_index = 0

    state.add_log(BattleEvent.BATTLE_START)

    skill_engine.register_always_passives(state)
    effects_module.refresh_all_atk(state)

    context = skill_engine.EventContext(trigger=TRIGGER_BATTLE_START)
    skill_engine.run_passives(state, TRIGGER_BATTLE_START, context)
    effects_module.refresh_all_atk(state)

    _begin_round(state, first=True)


def _order_key(unit: BattleUnit) -> tuple[int, int, int, int]:
    """行動順の並び替えキー（BATTLE_RULES.md 1節）。

    現在SPDが高い順。同値なら基礎SPDが高い方を先にし、基礎SPDも同じ場合は
    ラウンド開始時に振った乱数で決める。
    """

    return (
        -unit.speed,
        -effects_module.base_speed_of(unit),
        unit.order_seed,
        unit.battle_unit_id,
    )


def _order_units(state: BattleState) -> list[int]:
    """ラウンド開始時の行動順を決める。"""

    units = list(state.units.values())

    for unit in units:
        unit.order_seed = _rng.randrange(1_000_000)

    units.sort(key=_order_key)
    return [unit.battle_unit_id for unit in units]


def resort_pending_turns(state: BattleState) -> None:
    """SPDが変化したとき、まだ行動していない分だけ並び替える（1節）。

    行動済みの並びは変えず、再行動も起こしません。現在の行動者も動かしません。
    """

    if not state.turn_order:
        return

    # 行動中の使い魔は動かさないため、その次から並び替える。
    start = state.turn_index
    if state.current_unit_id is not None:
        start = state.turn_index + 1

    if start >= len(state.turn_order):
        return

    pending = [state.unit(unit_id) for unit_id in state.turn_order[start:]]
    pending_units = [unit for unit in pending if unit is not None]

    if len(pending_units) < 2:
        return

    pending_units.sort(key=_order_key)
    state.turn_order = (
        state.turn_order[:start]
        + [unit.battle_unit_id for unit in pending_units]
    )


def _begin_round(state: BattleState, *, first: bool = False) -> None:
    if not first:
        state.current_round += 1

    state.turn_index = 0
    state.turn_order = _order_units(state)
    state.current_unit_id = None

    # ラウンドをまたいで「行動済み」の記録を持ち越さない（蘇生の判定に使う）
    for unit in state.units.values():
        unit.state_flags.pop("acted_round", None)

    state.add_log(BattleEvent.ROUND_START)

    context = skill_engine.EventContext(trigger=TRIGGER_ROUND_START)
    skill_engine.run_passives(state, TRIGGER_ROUND_START, context)
    effects_module.refresh_all_atk(state)

    _advance(state)


def _end_round(state: BattleState) -> None:
    context = skill_engine.EventContext(trigger=TRIGGER_ROUND_END)
    skill_engine.run_passives(state, TRIGGER_ROUND_END, context)

    expired = effects_module.expire_round_effects(state)
    if expired:
        state.add_log(BattleEvent.ROUND_END, expired=len(expired))
    else:
        state.add_log(BattleEvent.ROUND_END)

    effects_module.refresh_all_atk(state)


# ==================================================
# 行動順の進行
# ==================================================
def _advance(state: BattleState) -> BattleUnit | None:
    """次に操作できる使い魔まで進める。決着した場合は ``None`` を返す。"""

    for _ in range(_MAX_ADVANCE_STEPS):
        if is_finished(state):
            state.current_unit_id = None
            return None

        if state.turn_index >= len(state.turn_order):
            _end_round(state)
            if is_finished(state):
                state.current_unit_id = None
                return None
            _begin_round_without_advance(state)
            continue

        unit = state.unit(state.turn_order[state.turn_index])

        # 戦闘不能はギルド持ち時間を消費せず自動スキップする。
        # ただし本来の行動順が来た時点で、持続ターンは1減らす（5節・7節）。
        if unit is None:
            state.turn_index += 1
            continue

        if not unit.alive:
            _tick_defeated_turn(state, unit)
            state.turn_index += 1
            continue

        blocked, reason = effects_module.is_action_blocked(state, unit)
        if blocked:
            state.add_log(
                BattleEvent.SKIP,
                actor_unit_id=unit.battle_unit_id,
                status=reason,
                text="行動不能のため行動をスキップ",
            )
            _finish_turn(state, unit, advance=False)
            state.turn_index += 1
            continue

        state.current_unit_id = unit.battle_unit_id

        context = skill_engine.EventContext(
            trigger=TRIGGER_TURN_START, turn_owner=unit
        )
        skill_engine.run_passives(state, TRIGGER_TURN_START, context)

        context = skill_engine.EventContext(
            trigger=TRIGGER_BEFORE_ACTION, turn_owner=unit
        )
        skill_engine.run_passives(state, TRIGGER_BEFORE_ACTION, context)

        effects_module.refresh_all_atk(state)

        # ターン開始パッシブで戦闘不能・行動不能になった場合は次へ送る
        if not unit.alive:
            state.turn_index += 1
            continue

        blocked, reason = effects_module.is_action_blocked(state, unit)
        if blocked:
            state.add_log(
                BattleEvent.SKIP,
                actor_unit_id=unit.battle_unit_id,
                status=reason,
                text="行動不能のため行動をスキップ",
            )
            _finish_turn(state, unit, advance=False)
            state.turn_index += 1
            continue

        state.add_log(BattleEvent.TURN_START, actor_unit_id=unit.battle_unit_id)
        return unit

    logger.error("行動順の探索が上限に達しました: battle_id=%s", state.battle_id)
    _finish(state, result=RESULT_DRAW, reason="engine_stalled")
    return None


def _begin_round_without_advance(state: BattleState) -> None:
    """``_advance`` のループ内から呼ぶ、再帰しないラウンド開始処理。"""

    state.current_round += 1
    state.turn_index = 0
    state.turn_order = _order_units(state)
    state.current_unit_id = None

    # ラウンドをまたいで「行動済み」の記録を持ち越さない（蘇生の判定に使う）
    for unit in state.units.values():
        unit.state_flags.pop("acted_round", None)

    state.add_log(BattleEvent.ROUND_START)

    context = skill_engine.EventContext(trigger=TRIGGER_ROUND_START)
    skill_engine.run_passives(state, TRIGGER_ROUND_START, context)
    effects_module.refresh_all_atk(state)


def current_actor(state: BattleState) -> BattleUnit | None:
    return state.current_unit()


# ==================================================
# ターン終了
# ==================================================
def _tick_defeated_turn(state: BattleState, unit: BattleUnit) -> None:
    """戦闘不能中の使い魔の行動順が来たときの処理（5節）。

    行動はしませんが、効果時間だけを経過させます。毒などの継続ダメージは
    戦闘不能中には適用しません。
    """

    if unit.state_flags.get("ticked_round") == state.current_round:
        return

    unit.state_flags["ticked_round"] = state.current_round
    unit.state_flags["acted_round"] = state.current_round

    effects_module.decrement_turn_effects(state, unit)
    effects_module.refresh_all_stats(state)
    resort_pending_turns(state)


def _finish_turn(
    state: BattleState, unit: BattleUnit, *, advance: bool = True
) -> None:
    """ターン終了処理（毒・残りターン・ターン終了パッシブ）。"""

    unit.state_flags["acted_round"] = state.current_round
    unit.state_flags["ticked_round"] = state.current_round
    unit.state_flags.pop("skill_used_this_turn", None)

    context = skill_engine.EventContext(trigger=TRIGGER_TURN_END, turn_owner=unit)
    skill_engine.run_passives(state, TRIGGER_TURN_END, context)

    # 毒は行動終了時にダメージを与えてから残りターンを1減らす（20節）
    for effect, damage in effects_module.pending_poison(state, unit):
        if not unit.alive:
            break

        source = state.unit(effect.source_unit_id)
        deal_damage(
            state,
            source,
            unit,
            damage,
            attack_type="poison",
            skill_id=effect.source_skill_id,
            can_critical=False,
        )

    effects_module.decrement_turn_effects(state, unit)
    effects_module.refresh_all_stats(state)
    resort_pending_turns(state)

    state.add_log(BattleEvent.TURN_END, actor_unit_id=unit.battle_unit_id)

    _check_wipe(state)

    if advance:
        state.turn_index += 1
        state.current_unit_id = None
        _advance(state)


# ==================================================
# ダメージ・回復・戦闘不能
# ==================================================
def deal_damage(
    state: BattleState,
    source: BattleUnit | None,
    target: BattleUnit,
    amount: int,
    *,
    attack_type: str,
    skill_id: str | None = None,
    can_critical: bool = True,
    context: skill_engine.EventContext | None = None,
) -> int:
    """ダメージを与える。クリティカル判定と被ダメージパッシブを処理する。

    ``attack_type`` はダメージの分類です（BATTLE_RULES.md 3節）。

    - ``"normal"`` / ``"skill_attack"``：攻撃ダメージ。クリティカルと
      「攻撃ダメージ軽減」の対象。
    - ``"skill"``：スキルダメージ。固定値で、クリティカルも軽減も受けない。
    - ``"poison"``：継続ダメージ。クリティカルも軽減も受けない。
    """

    if not target.alive:
        return 0

    master = load_master_data()
    base_amount = max(0, int(amount))
    is_attack_damage = attack_type in ATTACK_DAMAGE_TYPES

    critical = False
    if can_critical and is_attack_damage and base_amount > 0:
        critical = roll_permille(state, master.battle.critical_chance_permille)
        if critical:
            base_amount = round_half_up(
                base_amount * master.battle.critical_multiplier
            )

    # 軽減はクリティカル計算の後に適用する（4節）
    if is_attack_damage and base_amount > 0:
        base_amount = max(
            0, base_amount - effects_module.attack_damage_reduction(state, target)
        )

    damage_context = skill_engine.EventContext(
        trigger=TRIGGER_ON_DAMAGE,
        event_unit=target,
        event_source=source,
        attack_type=attack_type,
        damage=base_amount,
        skill_id=skill_id,
    )
    skill_engine.run_passives(
        state, TRIGGER_ON_DAMAGE, damage_context, candidates=[target]
    )

    final_damage = max(0, int(damage_context.damage))

    hp_before = target.current_hp
    target.current_hp = hp_before - final_damage
    hp_after = max(0, target.current_hp)

    state.add_log(
        BattleEvent.DAMAGE,
        actor_unit_id=source.battle_unit_id if source else None,
        target_unit_id=target.battle_unit_id,
        skill_id=skill_id,
        amount=final_damage,
        critical=critical,
        attack_type=attack_type,
        hp_before=hp_before,
        hp_after=hp_after,
        nullified=damage_context.cancelled and final_damage == 0,
    )

    effects_module.refresh_all_atk(state)

    if target.current_hp <= 0:
        defeated = _resolve_defeat(
            state, target, source, by_damage=True, attack_type=attack_type
        )
        if defeated and context is not None:
            context.defeats_caused += 1
    else:
        target.current_hp = max(0, target.current_hp)

    return final_damage


ATTACK_DAMAGE_TYPES = frozenset({"normal", "skill_attack"})


def _resolve_defeat(
    state: BattleState,
    target: BattleUnit,
    source: BattleUnit | None,
    *,
    by_damage: bool,
    attack_type: str | None = None,
) -> bool:
    """戦闘不能の確定処理。耐える・蘇生するパッシブをここで処理する（18.5節）。"""

    before_context = skill_engine.EventContext(
        trigger=TRIGGER_BEFORE_DEFEAT,
        event_unit=target,
        event_source=source,
        defeat_by_damage=by_damage,
        attack_type=attack_type,
    )
    skill_engine.run_passives(
        state, TRIGGER_BEFORE_DEFEAT, before_context, candidates=[target]
    )

    if before_context.survive_hp is not None:
        target.current_hp = min(target.max_hp, max(1, before_context.survive_hp))
        state.add_log(
            BattleEvent.BEFORE_DEFEAT,
            target_unit_id=target.battle_unit_id,
            amount=target.current_hp,
            text=f"HP{target.current_hp}で耐えた",
        )
        return False

    target.current_hp = 0
    target.alive = False

    state.add_log(
        BattleEvent.DEFEAT_CHECK,
        actor_unit_id=source.battle_unit_id if source else None,
        target_unit_id=target.battle_unit_id,
        text="戦闘不能",
    )

    # 蘇生パッシブを処理してからギルド全滅を判定する（18.5節）
    defeat_context = skill_engine.EventContext(
        trigger=TRIGGER_ON_DEFEAT,
        event_unit=target,
        event_source=source,
        defeat_by_damage=by_damage,
    )
    skill_engine.run_passives(state, TRIGGER_ON_DEFEAT, defeat_context)
    effects_module.refresh_all_atk(state)

    return not target.alive


def heal_unit(
    state: BattleState,
    source: BattleUnit | None,
    target: BattleUnit,
    amount: int,
    *,
    skill_id: str | None = None,
) -> int:
    """HPを回復する。戦闘不能の使い魔は通常回復の対象にできない（18.3節）。

    回復阻害デバフを受けている使い魔は回復しません（BATTLE_RULES.md 8節）。
    """

    if not target.alive or amount <= 0:
        return 0

    if effects_module.is_heal_blocked(state, target):
        state.add_log(
            BattleEvent.EFFECT,
            actor_unit_id=source.battle_unit_id if source else None,
            target_unit_id=target.battle_unit_id,
            skill_id=skill_id,
            text="回復阻害により回復できない",
        )
        return 0

    hp_before = target.current_hp
    target.current_hp = min(target.max_hp, hp_before + int(amount))
    healed = target.current_hp - hp_before

    if healed <= 0:
        return 0

    state.add_log(
        BattleEvent.HEAL,
        actor_unit_id=source.battle_unit_id if source else None,
        target_unit_id=target.battle_unit_id,
        skill_id=skill_id,
        amount=healed,
        hp_before=hp_before,
        hp_after=target.current_hp,
    )
    effects_module.refresh_all_atk(state)
    return healed


def revive_unit(
    state: BattleState,
    source: BattleUnit | None,
    target: BattleUnit,
    hp: int,
    *,
    skill_id: str | None = None,
) -> bool:
    """戦闘不能の使い魔を蘇生する。残っているバフ・デバフは引き継ぐ（5節）。

    回復阻害デバフを受けている使い魔は蘇生できません（8節）。蘇生後、そのラウンドで
    まだ行動していなければ行動順へ復帰します。
    """

    if target.alive:
        return False

    if effects_module.is_heal_blocked(state, target):
        state.add_log(
            BattleEvent.EFFECT,
            actor_unit_id=source.battle_unit_id if source else None,
            target_unit_id=target.battle_unit_id,
            skill_id=skill_id,
            text="回復阻害により蘇生できない",
        )
        return False

    target.alive = True
    target.current_hp = max(1, min(target.max_hp, int(hp)))

    state.add_log(
        BattleEvent.REVIVE,
        actor_unit_id=source.battle_unit_id if source else None,
        target_unit_id=target.battle_unit_id,
        skill_id=skill_id,
        amount=target.current_hp,
        text=f"HP{target.current_hp}で復活",
    )
    effects_module.refresh_all_stats(state)

    # 蘇生時パッシブ（バンシー「死者の慟哭」など）
    revive_context = skill_engine.EventContext(
        trigger=TRIGGER_ON_REVIVE,
        event_unit=target,
        event_source=source,
    )
    skill_engine.run_passives(state, TRIGGER_ON_REVIVE, revive_context)
    effects_module.refresh_all_stats(state)

    return True


def instant_defeat(
    state: BattleState,
    source: BattleUnit | None,
    target: BattleUnit,
    *,
    skill_id: str | None = None,
) -> bool:
    """即時戦闘不能。ダメージを伴わないため「耐える」条件は成立しない（18.5節）。"""

    if not target.alive:
        return False

    state.add_log(
        BattleEvent.EFFECT,
        actor_unit_id=source.battle_unit_id if source else None,
        target_unit_id=target.battle_unit_id,
        skill_id=skill_id,
        text="即座に戦闘不能",
    )
    return _resolve_defeat(state, target, source, by_damage=False)


# ==================================================
# 通常攻撃
# ==================================================
def attack_target_choices(
    state: BattleState, unit: BattleUnit
) -> list[BattleUnit]:
    """通常攻撃で選べる対象を返す。攻撃対象が固定されている場合は1体だけ。"""

    forced_id = effects_module.forced_target_id(state, unit)
    if forced_id is not None:
        forced = state.unit(forced_id)
        if forced is not None and forced.alive:
            return [forced]

    return state.living_units(state.enemy_guild_id(unit.guild_id))


def perform_attack(
    state: BattleState,
    attacker: BattleUnit,
    target: BattleUnit,
    *,
    skill_id: str | None = None,
    context: skill_engine.EventContext | None = None,
) -> int:
    """攻撃を1回実行する（BATTLE_RULES.md 2節・3節）。

    ``skill_id`` があるものは攻撃型ACTIVEによる攻撃（``skill_attack``）として
    扱います。どちらも攻撃ダメージなので、クリティカルと攻撃ダメージ軽減の
    対象ですが、「通常攻撃を受けた時」に反応するパッシブ（クラーケン
    「深海の拘束」など）は通常攻撃だけに反応します。
    """

    if not attacker.alive or not target.alive:
        return 0

    attack_type = "normal" if skill_id is None else "skill_attack"

    before_context = skill_engine.EventContext(
        trigger=TRIGGER_BEFORE_ATTACK,
        event_unit=target,
        event_source=attacker,
        attack_type=attack_type,
        skill_id=skill_id,
    )
    skill_engine.run_passives(
        state, TRIGGER_BEFORE_ATTACK, before_context, candidates=[attacker]
    )

    attack_power = effects_module.attack_atk(state, attacker)

    # 「次の攻撃のみ」の補正は、使った時点でここで消費する。
    # ダメージ処理の後に消費すると、被ダメージパッシブがこの攻撃中に付与した
    # 「次の攻撃のみ」の補正（怠惰の権能など）まで一緒に消してしまい、
    # その効果が一度も発動しなくなる。
    effects_module.consume_attack_modifiers(state, attacker)

    state.add_log(
        BattleEvent.ATTACK,
        actor_unit_id=attacker.battle_unit_id,
        target_unit_id=target.battle_unit_id,
        skill_id=skill_id,
        amount=attack_power,
    )

    damage = deal_damage(
        state,
        attacker,
        target,
        attack_power,
        attack_type=attack_type,
        skill_id=skill_id,
        can_critical=True,
        context=context,
    )

    attacker.state_flags["attacks_made"] = (
        int(attacker.state_flags.get("attacks_made", 0)) + 1
    )

    after_context = skill_engine.EventContext(
        trigger=TRIGGER_AFTER_ATTACK,
        event_unit=target,
        event_source=attacker,
        attack_type=attack_type,
        damage=damage,
        skill_id=skill_id,
    )
    skill_engine.run_passives(
        state, TRIGGER_AFTER_ATTACK, after_context, candidates=[attacker]
    )
    effects_module.refresh_all_stats(state)

    return damage


# ==================================================
# プレイヤー操作
# ==================================================
def _consume_time(state: BattleState, guild_id: int, seconds: int) -> None:
    """ギルド持ち時間を減らす（22節）。相手ギルドの時間は減らさない。"""

    if seconds <= 0:
        return

    remaining = int(state.remaining_seconds.get(guild_id, 0))
    state.remaining_seconds[guild_id] = max(0, remaining - int(seconds))


def submit_action(
    state: BattleState, action: BattleAction, *, elapsed_seconds: int = 0
) -> None:
    """プレイヤーの行動を1回処理する。"""

    if state.status != BATTLE_STATUS_IN_PROGRESS:
        raise BattleRuleError("このバトルは進行中ではありません。")

    unit = state.current_unit()
    if unit is None:
        raise BattleRuleError("現在は行動を受け付けていません。")

    if unit.battle_unit_id != action.actor_unit_id:
        raise BattleRuleError("現在の行動順ではありません。")

    _consume_time(state, unit.guild_id, elapsed_seconds)

    if action.action_type == ACTION_SKILL:
        master = load_master_data()
        owned_skill_ids = {
            skill.skill_id for skill in master.active_skills_of(unit.familiar_id)
        }

        if action.skill_id not in owned_skill_ids:
            raise BattleRuleError("この使い魔は指定のスキルを持っていません。")

        skill = master.get_skill(action.skill_id)
        if skill is None:
            raise BattleRuleError("スキル定義が見つかりません。")

        if unit.state_flags.get("skill_used_this_turn"):
            raise BattleRuleError("このターンは既にスキルを使用しています。")

        skill_engine.use_active_skill(state, unit, skill, dict(action.selections))
        unit.state_flags["skill_used_this_turn"] = True
        effects_module.refresh_all_atk(state)

        _check_wipe(state)

        if is_finished(state):
            state.current_unit_id = None
            return

        # 攻撃権を消費するスキルはここでターンを終える（19.2節）
        if skill.consumes_attack or not unit.alive:
            _finish_turn(state, unit)
        return

    if action.action_type == ACTION_ATTACK:
        choices = attack_target_choices(state, unit)
        if not choices:
            _finish_turn(state, unit)
            return

        target = None
        if action.target_unit_id is not None:
            target = next(
                (
                    choice
                    for choice in choices
                    if choice.battle_unit_id == action.target_unit_id
                ),
                None,
            )

        if target is None:
            raise BattleRuleError("その対象は現在攻撃できません。")

        perform_attack(state, unit, target)
        _check_wipe(state)

        if is_finished(state):
            state.current_unit_id = None
            return

        _finish_turn(state, unit)
        return

    raise BattleRuleError("未対応の行動です。")


def auto_target(state: BattleState, unit: BattleUnit) -> BattleUnit | None:
    """自動攻撃の対象を決める（17節）。

    現在HPが最も低い敵を選び、同じHPの相手が複数いる場合だけ乱数で1体選びます。
    """

    choices = attack_target_choices(state, unit)
    if not choices:
        return None

    lowest = min(choice.current_hp for choice in choices)
    candidates = [choice for choice in choices if choice.current_hp == lowest]

    if len(candidates) == 1:
        return candidates[0]

    return candidates[_rng.randrange(len(candidates))]


def auto_action(state: BattleState, *, elapsed_seconds: int) -> None:
    """持ち時間切れ・操作なしのときの自動攻撃（17節）。"""

    unit = state.current_unit()
    if unit is None:
        return

    _consume_time(state, unit.guild_id, elapsed_seconds)

    state.add_log(
        BattleEvent.TIMEOUT,
        actor_unit_id=unit.battle_unit_id,
        text="時間切れのため自動攻撃",
    )

    if state.remaining_seconds.get(unit.guild_id, 0) <= 0:
        _finish_by_timeout(state, unit.guild_id)
        return

    target = auto_target(state, unit)
    if target is not None:
        perform_attack(state, unit, target)

    _check_wipe(state)

    if is_finished(state):
        state.current_unit_id = None
        return

    _finish_turn(state, unit)


# ==================================================
# 決着
# ==================================================
def _finish(state: BattleState, *, result: str | None, reason: str) -> None:
    if state.status in {BATTLE_STATUS_FINISHED, BATTLE_STATUS_ABORTED}:
        return

    state.status = (
        BATTLE_STATUS_ABORTED if result == RESULT_ABORTED else BATTLE_STATUS_FINISHED
    )
    state.result = result
    state.end_reason = reason
    state.current_unit_id = None

    state.add_log(BattleEvent.BATTLE_END, result=result, reason=reason)


def _check_wipe(state: BattleState) -> None:
    """全滅判定。同じイベント内で両ギルドが全滅した場合は引き分け（26.1節）。"""

    if state.status != BATTLE_STATUS_IN_PROGRESS:
        return

    a_wiped = state.is_guild_wiped(state.guild_a_id)
    b_wiped = state.is_guild_wiped(state.guild_b_id)

    if a_wiped and b_wiped:
        _finish(state, result=RESULT_DRAW, reason="double_wipe")
    elif a_wiped:
        _finish(state, result=RESULT_GUILD_B, reason="wipe")
    elif b_wiped:
        _finish(state, result=RESULT_GUILD_A, reason="wipe")


def _finish_by_timeout(state: BattleState, guild_id: int) -> None:
    loser_is_a = guild_id == state.guild_a_id
    _finish(
        state,
        result=RESULT_GUILD_B if loser_is_a else RESULT_GUILD_A,
        reason="time_over",
    )


def check_time_over(state: BattleState) -> bool:
    """持ち時間が0のギルドがあれば敗北として決着させる（22節）。"""

    for guild_id in (state.guild_a_id, state.guild_b_id):
        if state.remaining_seconds.get(guild_id, 0) <= 0:
            _finish_by_timeout(state, guild_id)
            return True

    return False


def surrender(state: BattleState, guild_id: int) -> None:
    """ギルドマスターによる降参（26.1節）。"""

    if state.status != BATTLE_STATUS_IN_PROGRESS:
        raise BattleRuleError("このバトルは進行中ではありません。")

    loser_is_a = guild_id == state.guild_a_id
    state.add_log(BattleEvent.BATTLE_END, guild_id=guild_id, text="降参")
    _finish(
        state,
        result=RESULT_GUILD_B if loser_is_a else RESULT_GUILD_A,
        reason="surrender",
    )


def abort(state: BattleState, reason: str) -> None:
    """運営による強制中止。勝敗なしで保存する（26.1節）。"""

    _finish(state, result=RESULT_ABORTED, reason=reason)


def is_finished(state: BattleState) -> bool:
    return state.status in {BATTLE_STATUS_FINISHED, BATTLE_STATUS_ABORTED}


def resume(state: BattleState) -> BattleUnit | None:
    """再起動後の再開。行動待ちの使い魔がいなければ進行を進める（29節）。"""

    if state.status != BATTLE_STATUS_IN_PROGRESS:
        return None

    unit = state.current_unit()
    if unit is not None and unit.alive:
        blocked, _ = effects_module.is_action_blocked(state, unit)
        if not blocked:
            return unit

    return _advance(state)


# ==================================================
# 表示・操作候補
# ==================================================
def available_skills(state: BattleState, unit: BattleUnit):
    return skill_engine.available_skills(state, unit)


def selectable_targets(state: BattleState, unit: BattleUnit, group):
    return skill_engine.selectable_targets(state, unit, group)
