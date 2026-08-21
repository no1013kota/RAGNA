"""アクティブ・パッシブスキルの発動条件判定と効果適用（GAME_SPEC 19節）。

パッシブは25節のイベント順に沿って ``run_passives`` から呼ばれ、アクティブは
プレイヤー操作から ``use_active_skill`` で呼ばれます。ダメージ・回復・戦闘不能
そのものの処理は ``game.battle_engine`` が持つため、必要な箇所だけ遅延importで
参照しています（相互参照を避けるため）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import effects as effects_module
from .master_data import load_master_data
from .models import (
    DURATION_INSTANT,
    DURATION_PERMANENT,
    EFFECT_ACTIVE_LOCK,
    EFFECT_ATK_MODIFIER,
    EFFECT_ATK_SWAP,
    EFFECT_ATTACK,
    EFFECT_ATTACK_DAMAGE_REDUCTION,
    EFFECT_CLEANSE_DEBUFF,
    EFFECT_CLEANSE_STATUS,
    EFFECT_CONDITIONAL_ATK_MODIFIER,
    EFFECT_DAMAGE,
    EFFECT_HEAL,
    EFFECT_HEAL_BLOCK,
    EFFECT_HEAL_PER_DEFEAT,
    EFFECT_INSTANT_DEFEAT,
    EFFECT_NEXT_ATTACK_ATK_MODIFIER,
    EFFECT_NULLIFY_DAMAGE,
    EFFECT_NULLIFY_STATUS,
    EFFECT_POISON_AMPLIFY,
    EFFECT_REVIVE,
    EFFECT_SPEED_MODIFIER,
    EFFECT_STATUS,
    EFFECT_STATUS_IMMUNE,
    EFFECT_STATUS_REFLECT,
    EFFECT_SURVIVE_WITH_HP,
    EFFECT_TAUNT,
    PERCENT_OF_ACTOR_ATK,
    PERCENT_OF_ACTOR_MAX_HP,
    PERCENT_OF_SPEED_CAP,
    PERCENT_OF_TARGET_ATK,
    PERCENT_OF_TARGET_MAX_HP,
    STATUS_LABELS,
    STATUS_POISON,
    TARGET_ALLY_ALL,
    TARGET_ALLY_DEFEATED,
    TARGET_ENEMY_ALL,
    TARGET_EVENT_SOURCE,
    TARGET_EVENT_UNIT,
    TARGET_SELECTION_PREFIX,
    TARGET_SELF,
    TRIGGER_ALWAYS,
    BattleEvent,
    BattleRuleError,
    BattleState,
    BattleUnit,
    Skill,
    SkillEffect,
    TargetGroup,
    is_opposite_gender,
)


def _engine():
    """``game.battle_engine`` を遅延importする（相互参照を避けるため）。"""

    from . import battle_engine

    return battle_engine


# ==================================================
# 効果量（割合指定）
# ==================================================
def percent_basis(
    owner: BattleUnit | None,
    target: BattleUnit | None,
    percent_of: str | None,
) -> int:
    """割合指定の基準値を返す（19.1節）。

    ``owner`` はスキルを使った側、``target`` は効果を受ける側です。
    片方しか分からない場面では、もう片方に同じ使い魔を渡してかまいません。
    """

    if percent_of == PERCENT_OF_ACTOR_ATK:
        return owner.current_atk if owner else 0

    if percent_of == PERCENT_OF_ACTOR_MAX_HP:
        return owner.max_hp if owner else 0

    if percent_of == PERCENT_OF_TARGET_ATK:
        unit = target or owner
        return unit.current_atk if unit else 0

    if percent_of == PERCENT_OF_TARGET_MAX_HP:
        unit = target or owner
        return unit.max_hp if unit else 0

    if percent_of == PERCENT_OF_SPEED_CAP:
        return int(load_master_data().familiar.speed_max)

    return 0


def effect_amount(
    effect: SkillEffect,
    owner: BattleUnit | None = None,
    target: BattleUnit | None = None,
) -> int:
    """効果1つの実際の数値を返す（19.1節）。

    ``percent`` が設定されていれば基準値からの割合で計算し、なければ
    ``value`` の固定値をそのまま使います。四捨五入したうえで、割合が0でない
    限り最低でも1は動くようにします（低レベルで効果が消えないようにするため）。
    符号は ``percent`` 側が持ちます。
    """

    if effect.percent is None:
        return int(effect.value or 0)

    percent = int(effect.percent)
    if percent == 0:
        return 0

    basis = percent_basis(owner, target, effect.percent_of)
    amount = max(1, int(abs(basis) * abs(percent) / 100 + 0.5))

    return amount if percent > 0 else -amount


def status_damage(
    effect: SkillEffect,
    owner: BattleUnit | None = None,
    target: BattleUnit | None = None,
) -> int:
    """毒などの継続ダメージ量を返す。

    ``params.damage_percent`` があれば ``params.damage_percent_of`` を基準に
    割合で計算し、なければ ``params.damage`` の固定値を使います。
    """

    percent = effect.params.get("damage_percent")
    if percent is None:
        return int(effect.params.get("damage", 0))

    basis = percent_basis(
        owner, target, str(effect.params.get("damage_percent_of", ""))
    )

    return max(1, int(abs(basis) * abs(int(percent)) / 100 + 0.5))


# ==================================================
# イベント情報
# ==================================================
@dataclass
class EventContext:
    """パッシブ判定に渡す、そのイベントの情報と結果。"""

    trigger: str
    event_unit: BattleUnit | None = None
    event_source: BattleUnit | None = None
    attack_type: str | None = None
    damage: int = 0
    status: str | None = None
    defeat_by_damage: bool = False
    turn_owner: BattleUnit | None = None
    skill_id: str | None = None

    # パッシブが書き換える結果
    cancelled: bool = False
    reflect_targets: list[BattleUnit] = field(default_factory=list)
    survive_hp: int | None = None
    defeats_caused: int = 0

    # 同一イベント内での二重発動を防ぐ
    fired: set[tuple[int, str]] = field(default_factory=set)


# ==================================================
# 条件判定
# ==================================================
def _check_condition(
    state: BattleState,
    owner: BattleUnit,
    condition: dict[str, Any],
    context: EventContext,
) -> bool:
    condition_type = condition.get("type")

    if condition_type == "first_attack_of_battle":
        return int(owner.state_flags.get("attacks_made", 0)) == 0

    if condition_type == "attack_is_normal":
        return context.attack_type == "normal"

    if condition_type == "attack_is_skill":
        return context.attack_type == "skill"

    if condition_type == "damage_dealt_at_least":
        return context.damage >= int(condition.get("value", 1))

    if condition_type == "source_is_opposite_gender":
        source = context.event_source
        if source is None:
            return False
        return is_opposite_gender(owner.gender, source.gender)

    if condition_type == "defeated_is_ally":
        unit = context.event_unit
        return unit is not None and unit.guild_id == owner.guild_id

    if condition_type == "defeated_is_not_self":
        unit = context.event_unit
        return unit is not None and unit.battle_unit_id != owner.battle_unit_id

    if condition_type == "defeated_is_self":
        unit = context.event_unit
        return unit is not None and unit.battle_unit_id == owner.battle_unit_id

    if condition_type == "defeat_by_damage":
        return context.defeat_by_damage

    if condition_type == "status_is":
        return context.status == condition.get("value")

    if condition_type == "event_unit_is_self":
        unit = context.event_unit
        return unit is not None and unit.battle_unit_id == owner.battle_unit_id

    if condition_type == "event_unit_is_ally":
        unit = context.event_unit
        return (
            unit is not None
            and unit.guild_id == owner.guild_id
            and unit.battle_unit_id != owner.battle_unit_id
        )

    if condition_type == "target_hp_ratio_at_most":
        target = context.event_unit
        if target is None:
            return False
        return target.hp_ratio <= float(condition.get("value", 0))

    if condition_type == "target_has_status_or_debuff":
        target = context.event_unit
        if target is None:
            return False
        return effects_module.has_status_or_debuff(state, target)

    if condition_type == "target_not_acted_this_round":
        target = context.event_unit
        if target is None:
            return False
        return target.state_flags.get("acted_round") != state.current_round

    if condition_type == "target_is_gender":
        target = context.event_unit
        if target is None:
            return False
        return target.gender == condition.get("value")

    if condition_type == "damage_is_attack":
        return context.attack_type in _engine().ATTACK_DAMAGE_TYPES

    if condition_type == "once_per_round":
        key = f"once_per_round:{condition.get('key', 'default')}"
        if owner.state_flags.get(key) == state.current_round:
            return False
        return True

    if condition_type == "turn_owner_is_self":
        turn_owner = context.turn_owner
        return (
            turn_owner is not None
            and turn_owner.battle_unit_id == owner.battle_unit_id
        )

    # HP割合・ラウンド条件は効果側と共通の判定を使う
    return effects_module.evaluate_state_condition(state, owner, condition)


def _conditions_met(
    state: BattleState, owner: BattleUnit, skill: Skill, context: EventContext
) -> bool:
    """スキルの発動条件をすべて満たすか。

    条件に ``on_trigger`` があるものは、そのタイミングのときだけ判定します
    （複数のタイミングを持つパッシブで、片方だけに条件を付けるため）。
    """

    for condition in skill.conditions:
        scope = condition.get("on_trigger")
        if scope is not None and scope != context.trigger:
            continue

        if not _check_condition(state, owner, condition, context):
            return False

    return True


# ==================================================
# 対象の解決
# ==================================================
def _selection_units(
    state: BattleState, selections: dict[str, Any], key: str
) -> list[BattleUnit]:
    found = []
    for unit_id in selections.get(key, ()):  # type: ignore[union-attr]
        unit = state.unit(int(unit_id))
        if unit is not None:
            found.append(unit)
    return found


def _narrow(
    state: BattleState,
    owner: BattleUnit,
    candidates: list[BattleUnit],
    effect: SkillEffect,
) -> list[BattleUnit]:
    """全体対象を、``params`` の絞り込み条件で狭める。

    - ``gender``：その性別の使い魔だけ（ベルフェゴール・リリス・セイレーン）
    - ``pick``：``lowest_hp`` / ``highest_atk`` で1体だけ選ぶ
    - ``exclude_self``：自分を除く
    - ``limit``：先頭から指定体数だけ
    """

    params = effect.params

    gender = params.get("gender")
    if gender:
        candidates = [
            target for target in candidates if target.gender == gender
        ]

    if params.get("exclude_self"):
        candidates = [
            target
            for target in candidates
            if target.battle_unit_id != owner.battle_unit_id
        ]

    pick = params.get("pick")
    if pick == "lowest_hp":
        candidates = sorted(
            candidates, key=lambda unit: (unit.current_hp, unit.battle_unit_id)
        )
    elif pick == "highest_atk":
        candidates = sorted(
            candidates,
            key=lambda unit: (-unit.current_atk, unit.battle_unit_id),
        )

    limit = params.get("limit")
    if pick and limit is None:
        limit = 1
    if limit is not None:
        candidates = candidates[: int(limit)]

    return candidates


def resolve_targets(
    state: BattleState,
    owner: BattleUnit,
    effect: SkillEffect,
    context: EventContext,
    selections: dict[str, Any],
) -> list[BattleUnit]:
    """効果の ``target_type`` から実際の対象を求める。"""

    target_type = effect.target_type

    if target_type == TARGET_SELF:
        return [owner]

    if target_type == TARGET_ALLY_ALL:
        return _narrow(state, owner, state.living_units(owner.guild_id), effect)

    if target_type == TARGET_ENEMY_ALL:
        return _narrow(
            state,
            owner,
            state.living_units(state.enemy_guild_id(owner.guild_id)),
            effect,
        )

    if target_type == TARGET_ALLY_DEFEATED:
        return _narrow(
            state, owner, state.defeated_units(owner.guild_id), effect
        )

    if target_type == TARGET_EVENT_UNIT:
        return [context.event_unit] if context.event_unit is not None else []

    if target_type == TARGET_EVENT_SOURCE:
        return [context.event_source] if context.event_source is not None else []

    if target_type.startswith(TARGET_SELECTION_PREFIX):
        key = target_type.split(":", 1)[1]
        return _selection_units(state, selections, key)

    return []


def selectable_targets(
    state: BattleState, unit: BattleUnit, group: TargetGroup
) -> list[BattleUnit]:
    """アクティブスキルの対象選択に出す候補を返す。"""

    if group.side == "enemy":
        candidates = state.living_units(state.enemy_guild_id(unit.guild_id))
    else:
        candidates = state.living_units(unit.guild_id)

    for condition in group.filter:
        condition_type = condition.get("type")

        if condition_type == "opposite_gender":
            candidates = [
                target
                for target in candidates
                if is_opposite_gender(unit.gender, target.gender)
            ]

        elif condition_type == "not_acted_this_round":
            candidates = [
                target
                for target in candidates
                if target.state_flags.get("acted_round") != state.current_round
            ]

        elif condition_type == "is_gender":
            candidates = [
                target
                for target in candidates
                if target.gender == condition.get("value")
            ]

        elif condition_type == "defeated":
            candidates = (
                state.defeated_units(state.enemy_guild_id(unit.guild_id))
                if group.side == "enemy"
                else state.defeated_units(unit.guild_id)
            )

        elif condition_type == "exclude_self":
            candidates = [
                target
                for target in candidates
                if target.battle_unit_id != unit.battle_unit_id
            ]

    return candidates


def available_skills(state: BattleState, unit: BattleUnit) -> list[Skill]:
    """今このターンに使用できるアクティブスキルを返す。"""

    if effects_module.is_active_locked(state, unit):
        return []

    master = load_master_data()
    usable = []

    for skill in master.active_skills_of(unit.familiar_id):
        if not is_skill_usable(state, unit, skill):
            continue
        usable.append(skill)

    return usable


def is_skill_usable(state: BattleState, unit: BattleUnit, skill: Skill) -> bool:
    """使用回数・対象の有無からアクティブスキルを使えるか判定する。"""

    if not skill.enabled or not skill.is_active:
        return False

    if effects_module.is_active_locked(state, unit):
        return False

    if skill.max_uses_per_battle is not None:
        used = int(unit.active_skill_uses.get(skill.skill_id, 0))
        if used >= skill.max_uses_per_battle:
            return False

    for group in skill.targets:
        if not selectable_targets(state, unit, group):
            return False

    # 対象選択が不要な全体スキルでも、生存している敵がいなければ意味がない
    needs_enemy = any(
        effect.target_type == TARGET_ENEMY_ALL for effect in skill.effects
    )
    if needs_enemy and not state.living_units(
        state.enemy_guild_id(unit.guild_id)
    ):
        return False

    return True


def validate_selections(
    state: BattleState,
    unit: BattleUnit,
    skill: Skill,
    selections: dict[str, Any],
) -> dict[str, tuple[int, ...]]:
    """プレイヤーが選んだ対象が現在も有効か再確認する（29節）。"""

    validated: dict[str, tuple[int, ...]] = {}

    for group in skill.targets:
        chosen = tuple(int(value) for value in selections.get(group.key, ()))

        if len(chosen) != group.count:
            raise BattleRuleError(
                f"「{skill.name}」の対象を{group.count}体選択してください。"
            )

        if not group.allow_duplicate and len(set(chosen)) != len(chosen):
            raise BattleRuleError("同じ対象を重複して選択できません。")

        allowed = {
            target.battle_unit_id
            for target in selectable_targets(state, unit, group)
        }
        for unit_id in chosen:
            if unit_id not in allowed:
                raise BattleRuleError("選択した対象は現在指定できません。")

        validated[group.key] = chosen

    return validated


# ==================================================
# 状態異常の付与
# ==================================================
def apply_status(
    state: BattleState,
    source: BattleUnit | None,
    target: BattleUnit,
    status: str,
    *,
    duration_turns: int,
    damage: int = 0,
    skill_id: str | None = None,
    allow_reflect: bool = True,
) -> bool:
    """状態異常を付与する。無効化・反射のパッシブをここで処理する。"""

    label = STATUS_LABELS.get(status, status)

    if not target.alive:
        return False

    if effects_module.is_status_immune(state, target):
        state.add_log(
            BattleEvent.STATUS_APPLY,
            actor_unit_id=source.battle_unit_id if source else None,
            target_unit_id=target.battle_unit_id,
            skill_id=skill_id,
            status=status,
            applied=False,
            text=f"{label}は無効化された",
        )
        return False

    context = EventContext(
        trigger="before_status_apply",
        event_unit=target,
        event_source=source,
        status=status,
        skill_id=skill_id,
    )
    # 味方を守るパッシブ（ベリアル「魔王の先見」）も判定できるよう、
    # 対象本人だけでなく同じギルドの生存者を候補にする。
    guards = [target, *(
        unit for unit in state.living_units(target.guild_id)
        if unit.battle_unit_id != target.battle_unit_id
    )]
    run_passives(state, "before_status_apply", context, candidates=guards)

    if context.cancelled:
        state.add_log(
            BattleEvent.STATUS_APPLY,
            actor_unit_id=source.battle_unit_id if source else None,
            target_unit_id=target.battle_unit_id,
            skill_id=skill_id,
            status=status,
            applied=False,
            text=f"{label}は無効化された",
        )

        if allow_reflect:
            for reflect_target in context.reflect_targets:
                if reflect_target is None or not reflect_target.alive:
                    continue
                apply_status(
                    state,
                    target,
                    reflect_target,
                    status,
                    duration_turns=duration_turns,
                    damage=damage,
                    skill_id=skill_id,
                    allow_reflect=False,
                )
        return False

    amplified = 0

    # ヒュドラ「猛毒増幅」：毒が付与される時だけ、ダメージと継続ターンを増やす。
    # 同じ毒に対して1つの強化効果は1回だけ適用する（BATTLE_RULES.md 7節）。
    if status == STATUS_POISON:
        damage, duration_turns, amplified = effects_module.amplify_poison(
            state, target, damage=damage, turns=duration_turns
        )

    params: dict[str, Any] = {"status": status}
    if damage:
        params["damage"] = damage

    applied = effects_module.apply_effect(
        state,
        target,
        effect_type=EFFECT_STATUS,
        duration_type="turns",
        duration_turns=duration_turns,
        source_unit=source,
        source_skill_id=skill_id,
        params=params,
    )

    if applied is None:
        return False

    detail = f"{label}（残{duration_turns}ターン）"
    if status == STATUS_POISON and damage:
        detail = f"{label} {damage}ダメージ×{duration_turns}ターン"
    if amplified:
        detail = f"{detail}／猛毒増幅"

    state.add_log(
        BattleEvent.STATUS_APPLY,
        actor_unit_id=source.battle_unit_id if source else None,
        target_unit_id=target.battle_unit_id,
        skill_id=skill_id,
        status=status,
        applied=True,
        remaining=duration_turns,
        text=detail,
    )
    return True


# ==================================================
# 効果の適用
# ==================================================
def _apply_single_effect(
    state: BattleState,
    owner: BattleUnit,
    skill: Skill,
    effect: SkillEffect,
    context: EventContext,
    selections: dict[str, Any],
) -> None:
    engine = _engine()
    targets = resolve_targets(state, owner, effect, context, selections)
    effect_type = effect.effect_type
    # 割合指定の効果は対象ごとに数値が変わるため、対象が決まってから計算する。
    value = effect_amount(effect, owner, owner)

    if effect_type == EFFECT_DAMAGE:
        # スキル本文の固定ダメージは「スキルダメージ」。クリティカルも
        # 攻撃ダメージ軽減も受けない（BATTLE_RULES.md 3節）。
        damage_kind = str(effect.params.get("damage_kind", "skill"))
        for target in targets:
            if not target.alive:
                continue
            engine.deal_damage(
                state,
                owner,
                target,
                effect_amount(effect, owner, target),
                attack_type=damage_kind,
                skill_id=skill.skill_id,
                can_critical=damage_kind in engine.ATTACK_DAMAGE_TYPES,
                context=context,
            )
        return

    if effect_type == EFFECT_ATTACK:
        count = int(effect.params.get("count", 1))
        individual = bool(effect.params.get("individual_targets", False))

        if individual:
            # 各攻撃の対象を実行前に選ぶスキル。生存している対象だけ攻撃する（18.4節）。
            for target in targets[:count]:
                if not target.alive:
                    continue
                engine.perform_attack(
                    state, owner, target, skill_id=skill.skill_id, context=context
                )
        else:
            target = targets[0] if targets else None
            for _ in range(count):
                if target is None or not target.alive:
                    break
                engine.perform_attack(
                    state, owner, target, skill_id=skill.skill_id, context=context
                )
        return

    if effect_type == EFFECT_HEAL:
        for target in targets:
            engine.heal_unit(
                state,
                owner,
                target,
                effect_amount(effect, owner, target),
                skill_id=skill.skill_id,
            )
        return

    if effect_type == EFFECT_HEAL_PER_DEFEAT:
        if context.defeats_caused <= 0:
            return
        for target in targets:
            amount = effect_amount(effect, owner, target) * context.defeats_caused
            engine.heal_unit(state, owner, target, amount, skill_id=skill.skill_id)
        return

    if effect_type == EFFECT_INSTANT_DEFEAT:
        for target in targets:
            engine.instant_defeat(state, owner, target, skill_id=skill.skill_id)
        return

    if effect_type == EFFECT_REVIVE:
        for target in targets:
            engine.revive_unit(
                state,
                owner,
                target,
                effect_amount(effect, owner, target),
                skill_id=skill.skill_id,
            )
        return

    if effect_type == EFFECT_SURVIVE_WITH_HP:
        context.survive_hp = value
        return

    if effect_type == EFFECT_NULLIFY_DAMAGE:
        context.damage = 0
        context.cancelled = True
        return

    if effect_type == EFFECT_NULLIFY_STATUS:
        context.cancelled = True
        return

    if effect_type == EFFECT_STATUS_REFLECT:
        context.cancelled = True
        if context.event_source is not None:
            context.reflect_targets.append(context.event_source)
        return

    if effect_type == EFFECT_CLEANSE_STATUS:
        for target in targets:
            removed = effects_module.remove_statuses(state, target)
            if removed:
                names = "・".join(STATUS_LABELS.get(name, name) for name in removed)
                state.add_log(
                    BattleEvent.EFFECT,
                    actor_unit_id=owner.battle_unit_id,
                    target_unit_id=target.battle_unit_id,
                    skill_id=skill.skill_id,
                    text=f"{names}を解除",
                )
        return

    if effect_type == EFFECT_CLEANSE_DEBUFF:
        for target in targets:
            removed = effects_module.remove_debuffs(state, target)
            if removed:
                state.add_log(
                    BattleEvent.EFFECT,
                    actor_unit_id=owner.battle_unit_id,
                    target_unit_id=target.battle_unit_id,
                    skill_id=skill.skill_id,
                    text=f"デバフ{removed}件を解除",
                )
        return

    if effect_type == EFFECT_STATUS:
        status = str(effect.params.get("status", ""))
        for target in targets:
            apply_status(
                state,
                owner,
                target,
                status,
                duration_turns=int(effect.duration_turns or 1),
                damage=status_damage(effect, owner, target),
                skill_id=skill.skill_id,
            )
        return

    if effect_type == EFFECT_ATK_SWAP:
        partner_key = str(effect.params.get("partner", ""))
        partners: list[BattleUnit] = []
        if partner_key.startswith(TARGET_SELECTION_PREFIX):
            partners = _selection_units(
                state, selections, partner_key.split(":", 1)[1]
            )

        if not targets or not partners:
            return

        first, second = targets[0], partners[0]
        # 交換時点の現在ATKを互いの基準値として保存する。
        # 以後のバフ・デバフはこの基準値へ加算される（34.4節）。
        # 交換前から掛かっていた補正は基準値に含まれているため、
        # 二重加算を避けるよう absorbed として記録しておく。
        first_atk = effects_module.compute_atk(state, first)
        second_atk = effects_module.compute_atk(state, second)
        first_absorbed = effects_module.modifier_total(state, first)
        second_absorbed = effects_module.modifier_total(state, second)

        for unit, swapped, absorbed in (
            (first, second_atk, first_absorbed),
            (second, first_atk, second_absorbed),
        ):
            effects_module.apply_effect(
                state,
                unit,
                effect_type=EFFECT_ATK_SWAP,
                duration_type=effect.duration_type,
                duration_turns=effect.duration_turns,
                source_unit=owner,
                source_skill_id=skill.skill_id,
                params={
                    **effect.params,
                    "atk_value": swapped,
                    "absorbed": absorbed,
                },
            )

        state.add_log(
            BattleEvent.EFFECT,
            actor_unit_id=owner.battle_unit_id,
            target_unit_id=first.battle_unit_id,
            skill_id=skill.skill_id,
            text=f"現在ATKを交換（{first_atk} ⇄ {second_atk}）",
        )
        return

    if effect_type in {
        EFFECT_ATK_MODIFIER,
        EFFECT_NEXT_ATTACK_ATK_MODIFIER,
        EFFECT_CONDITIONAL_ATK_MODIFIER,
        EFFECT_STATUS_IMMUNE,
        EFFECT_ACTIVE_LOCK,
        EFFECT_TAUNT,
        EFFECT_SPEED_MODIFIER,
        EFFECT_ATTACK_DAMAGE_REDUCTION,
        EFFECT_HEAL_BLOCK,
        EFFECT_POISON_AMPLIFY,
    }:
        for target in targets:
            params = dict(effect.params)

            if effect_type == EFFECT_TAUNT:
                if params.get("forced_target") == "caster":
                    params["forced_target_unit_id"] = owner.battle_unit_id

            # 能力値の変化を「前 → 後」で見せるため、付与前の値を控える
            atk_before = target.current_atk
            speed_before = target.speed
            # 割合指定はここで実数へ直す。以降は固定値と同じ扱いになる。
            amount = effect_amount(effect, owner, target)

            applied = effects_module.apply_effect(
                state,
                target,
                effect_type=effect_type,
                value=amount,
                duration_type=effect.duration_type,
                duration_turns=effect.duration_turns,
                source_unit=owner,
                source_skill_id=skill.skill_id,
                params=params,
            )

            if applied is None:
                continue

            state.add_log(
                BattleEvent.EFFECT,
                actor_unit_id=owner.battle_unit_id,
                target_unit_id=target.battle_unit_id,
                skill_id=skill.skill_id,
                amount=amount,
                effect_type=effect_type,
                text=_effect_log_text(effect_type, effect, amount),
                atk_before=atk_before,
                atk_after=target.current_atk,
                speed_before=speed_before,
                speed_after=target.speed,
                hp=target.current_hp,
                max_hp=target.max_hp,
            )
        return


def _effect_log_text(effect_type: str, effect: SkillEffect, amount: int) -> str:
    """効果ログの本文。``amount`` は割合指定を実数へ直したあとの数値。"""

    value = int(amount)

    if effect_type in {
        EFFECT_ATK_MODIFIER,
        EFFECT_NEXT_ATTACK_ATK_MODIFIER,
        EFFECT_CONDITIONAL_ATK_MODIFIER,
    }:
        text = f"ATK{value:+d}"
        if effect.duration_turns:
            text = f"{text}（残{effect.duration_turns}ターン）"
        elif effect.duration_type == "attacks":
            text = f"{text}（次の攻撃のみ）"
        return text

    if effect_type == EFFECT_STATUS_IMMUNE:
        return "状態異常無効"

    if effect_type == EFFECT_ACTIVE_LOCK:
        return "ACTIVE使用禁止"

    if effect_type == EFFECT_TAUNT:
        return "攻撃対象を固定"

    if effect_type == EFFECT_SPEED_MODIFIER:
        text = f"SPD{value:+d}"
        if effect.duration_turns:
            text = f"{text}（残{effect.duration_turns}ターン）"
        return text

    if effect_type == EFFECT_ATTACK_DAMAGE_REDUCTION:
        return f"被ダメージ-{abs(value)}"

    if effect_type == EFFECT_HEAL_BLOCK:
        text = "回復阻害"
        if effect.duration_turns:
            text = f"{text}（残{effect.duration_turns}ターン）"
        return text

    if effect_type == EFFECT_POISON_AMPLIFY:
        return "猛毒増幅"

    return effect_type


def apply_skill_effects(
    state: BattleState,
    owner: BattleUnit,
    skill: Skill,
    context: EventContext,
    selections: dict[str, Any] | None = None,
    *,
    trigger: str | None = None,
) -> None:
    """スキルの効果を定義順に適用する。

    ``trigger`` を渡した場合、そのタイミング向けの効果だけを適用します
    （複数のタイミングを持つパッシブ用）。
    """

    selections = selections or {}
    targets = skill.effects if trigger is None else skill.effects_for(trigger)

    for effect in targets:
        if effect.chance is not None:
            if not _engine().roll_permille(state, effect.chance):
                continue

        _apply_single_effect(state, owner, skill, effect, context, selections)


# ==================================================
# パッシブ
# ==================================================
def _passive_candidates(
    state: BattleState, candidates: list[BattleUnit] | None
) -> list[BattleUnit]:
    if candidates is not None:
        return [unit for unit in candidates if unit is not None]
    return list(state.units.values())


def run_passives(
    state: BattleState,
    trigger: str,
    context: EventContext,
    *,
    candidates: list[BattleUnit] | None = None,
) -> None:
    """指定タイミングのパッシブを、決まった順序で発動する（19.3節）。

    優先度が小さい順、発動者のSPDが大きい順、戦闘用使い魔ID、スキルIDの順に
    解決するため、同じ状態からは常に同じ順序で処理されます。
    """

    master = load_master_data()
    entries: list[tuple[int, int, int, str, BattleUnit, Skill]] = []

    for unit in _passive_candidates(state, candidates):
        # 戦闘不能の使い魔のパッシブは発動しない（19.3節）。
        # 自分が倒れたことをきっかけにするパッシブも同じで、倒れた瞬間には
        # もう発動できません。倒れても効果が続くと、先に倒す意味が無くなるためです。
        # 「戦闘不能になる直前」に耐えるパッシブ（首無し騎士）は、まだ生きている
        # 状態で判定するため影響を受けません。
        if not unit.alive:
            continue

        for skill in master.passive_skills_of(unit.familiar_id):
            if trigger not in skill.triggers:
                continue

            entries.append(
                (
                    skill.priority,
                    -unit.speed,
                    unit.battle_unit_id,
                    skill.skill_id,
                    unit,
                    skill,
                )
            )

    entries.sort(key=lambda item: (item[0], item[1], item[2], item[3]))

    for _, _, _, _, unit, skill in entries:
        key = (unit.battle_unit_id, skill.skill_id)
        if key in context.fired:
            continue

        if skill.max_uses_per_battle is not None:
            used = int(unit.passive_uses.get(skill.skill_id, 0))
            if used >= skill.max_uses_per_battle:
                continue

        if not _conditions_met(state, unit, skill, context):
            continue

        context.fired.add(key)
        unit.passive_uses[skill.skill_id] = (
            int(unit.passive_uses.get(skill.skill_id, 0)) + 1
        )
        _mark_once_per_round(state, unit, skill)

        state.add_log(
            BattleEvent.PASSIVE,
            actor_unit_id=unit.battle_unit_id,
            skill_id=skill.skill_id,
            skill_name=skill.name,
        )

        apply_skill_effects(state, unit, skill, context, trigger=trigger)


def _mark_once_per_round(
    state: BattleState, unit: BattleUnit, skill: Skill
) -> None:
    """``once_per_round`` 条件を持つスキルの発動をラウンド単位で記録する。"""

    for condition in skill.conditions:
        if condition.get("type") != "once_per_round":
            continue

        key = f"once_per_round:{condition.get('key', 'default')}"
        unit.state_flags[key] = state.current_round


def register_always_passives(state: BattleState) -> None:
    """常時発動パッシブを、バトル開始時に継続効果として登録する。"""

    master = load_master_data()

    for unit in state.units.values():
        for skill in master.passive_skills_of(unit.familiar_id):
            if skill.trigger != TRIGGER_ALWAYS:
                continue

            registered = False

            for effect in skill.effects:
                if effect.duration_type not in {DURATION_PERMANENT, DURATION_INSTANT}:
                    continue

                applied = effects_module.apply_effect(
                    state,
                    unit,
                    effect_type=effect.effect_type,
                    value=effect_amount(effect, unit, unit),
                    duration_type=DURATION_PERMANENT,
                    source_unit=unit,
                    source_skill_id=skill.skill_id,
                    params=dict(effect.params),
                )
                registered = registered or applied is not None

            if registered:
                state.add_log(
                    BattleEvent.PASSIVE,
                    actor_unit_id=unit.battle_unit_id,
                    skill_id=skill.skill_id,
                    skill_name=skill.name,
                    text="常時発動",
                )


# ==================================================
# アクティブスキル
# ==================================================
def use_active_skill(
    state: BattleState,
    unit: BattleUnit,
    skill: Skill,
    selections: dict[str, Any] | None = None,
) -> None:
    """アクティブスキルを使用する。使用回数と対象は事前に再確認する。"""

    if not is_skill_usable(state, unit, skill):
        raise BattleRuleError("このスキルは現在使用できません。")

    validated = validate_selections(state, unit, skill, selections or {})

    unit.active_skill_uses[skill.skill_id] = (
        int(unit.active_skill_uses.get(skill.skill_id, 0)) + 1
    )

    target_names = []
    for chosen in validated.values():
        for unit_id in chosen:
            target = state.unit(unit_id)
            if target is not None:
                target_names.append(target.familiar_id)

    state.add_log(
        BattleEvent.SKILL,
        actor_unit_id=unit.battle_unit_id,
        skill_id=skill.skill_id,
        skill_name=skill.name,
        description=skill.description,
        target_unit_ids=[
            unit_id for chosen in validated.values() for unit_id in chosen
        ],
    )

    context = EventContext(
        trigger="skill", event_source=unit, skill_id=skill.skill_id
    )
    apply_skill_effects(state, unit, skill, context, validated)
