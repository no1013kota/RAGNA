"""バフ・デバフ・状態異常の保持と、現在ATKの計算（GAME_SPEC 20節）。

このモジュールは状態の記録と集計だけを行います。ダメージ処理やパッシブの
発動は ``game.battle_engine`` と ``game.skill_engine`` の責務です。
"""

from __future__ import annotations

from typing import Any

from .master_data import load_master_data
from .models import (
    ACTION_BLOCKING_STATUSES,
    CATEGORY_DEBUFF,
    CATEGORY_STATUS,
    DURATION_ATTACKS,
    DURATION_PERMANENT,
    DURATION_ROUND_END,
    DURATION_TURNS,
    EFFECT_ACTIVE_LOCK,
    EFFECT_ATK_MODIFIER,
    EFFECT_ATK_SWAP,
    EFFECT_ATTACK_DAMAGE_REDUCTION,
    EFFECT_CONDITIONAL_ATK_MODIFIER,
    EFFECT_HEAL_BLOCK,
    EFFECT_NEXT_ATTACK_ATK_MODIFIER,
    EFFECT_POISON_AMPLIFY,
    EFFECT_SPEED_MODIFIER,
    EFFECT_STATUS,
    EFFECT_STATUS_IMMUNE,
    EFFECT_TAUNT,
    STATUS_ACTIVE_LOCK,
    STATUS_LABELS,
    STATUS_POISON,
    BattleEffectState,
    BattleState,
    BattleUnit,
    effect_category,
)


# 継続効果として保存する効果種別
PERSISTENT_EFFECT_TYPES = frozenset(
    {
        EFFECT_ATK_MODIFIER,
        EFFECT_CONDITIONAL_ATK_MODIFIER,
        EFFECT_NEXT_ATTACK_ATK_MODIFIER,
        EFFECT_STATUS,
        EFFECT_STATUS_IMMUNE,
        EFFECT_ACTIVE_LOCK,
        EFFECT_TAUNT,
        EFFECT_ATK_SWAP,
        EFFECT_SPEED_MODIFIER,
        EFFECT_ATTACK_DAMAGE_REDUCTION,
        EFFECT_HEAL_BLOCK,
        EFFECT_POISON_AMPLIFY,
    }
)


# ==================================================
# 効果の付与
# ==================================================
def evaluate_state_condition(
    state: BattleState, unit: BattleUnit, condition: dict[str, Any]
) -> bool:
    """常時発動パッシブなどが使う、現在状態に対する条件判定。"""

    condition_type = condition.get("type")

    if condition_type == "hp_ratio_at_most":
        return unit.hp_ratio <= float(condition.get("value", 0))

    if condition_type == "hp_ratio_at_least":
        return unit.hp_ratio >= float(condition.get("value", 0))

    if condition_type == "round_at_most":
        return state.current_round <= int(condition.get("value", 0))

    if condition_type == "round_at_least":
        return state.current_round >= int(condition.get("value", 0))

    if condition_type == "has_status_or_debuff":
        return has_status_or_debuff(state, unit)

    if condition_type == "not_acted_this_round":
        return unit.state_flags.get("acted_round") != state.current_round

    if condition_type == "is_gender":
        return unit.gender == condition.get("value")

    # 未知の条件は満たさないものとして扱う（誤発動より不発動を選ぶ）。
    return False


def _same_source_effect(
    state: BattleState,
    unit: BattleUnit,
    effect_type: str,
    source_skill_id: str | None,
    source_unit: BattleUnit | None,
) -> BattleEffectState | None:
    """同じ使い魔・同じスキル・同じ対象で既に付いている効果を返す（7節）。

    発生元の使い魔が違えば別効果として重複します（毒の重複など）。
    """

    if source_skill_id is None or source_unit is None:
        return None

    for effect in state.unit_effects(unit.battle_unit_id):
        if (
            effect.effect_type == effect_type
            and effect.source_skill_id == source_skill_id
            and effect.source_unit_id == source_unit.battle_unit_id
        ):
            return effect

    return None


def _same_source_count(
    state: BattleState,
    unit: BattleUnit,
    effect_type: str,
    source_skill_id: str | None,
) -> int:
    return sum(
        1
        for effect in state.effects
        if effect.battle_unit_id == unit.battle_unit_id
        and effect.effect_type == effect_type
        and effect.source_skill_id == source_skill_id
    )


def apply_effect(
    state: BattleState,
    unit: BattleUnit,
    *,
    effect_type: str,
    value: int | None = None,
    duration_type: str = DURATION_TURNS,
    duration_turns: int | None = None,
    source_unit: BattleUnit | None = None,
    source_skill_id: str | None = None,
    params: dict[str, Any] | None = None,
) -> BattleEffectState | None:
    """継続効果を1つ付与する。重複制限に掛かった場合は ``None`` を返す。"""

    if effect_type not in PERSISTENT_EFFECT_TYPES:
        return None

    params = dict(params or {})
    master = load_master_data()

    if duration_type == DURATION_TURNS:
        wanted = duration_turns if duration_turns is not None else 1
    elif duration_type == DURATION_ATTACKS:
        wanted = int(params.get("attacks", 1))
    else:
        wanted = None

    # 同じ使い魔が同じスキルを同じ対象へ再使用しても重複させない。
    # 付け直しで短くならないよう、残りターンは長い方へ揃える（7節）。
    # 「味方が倒れるたびATK+2」のように積み上がる効果は params.stack で除外する。
    existing = (
        None
        if params.get("stack")
        else _same_source_effect(
            state, unit, effect_type, source_skill_id, source_unit
        )
    )
    if existing is not None:
        if wanted is not None and (existing.remaining or 0) < wanted:
            existing.remaining = wanted
        refresh_stats(state, unit)
        return existing

    # 「同一対象には重複しない」パッシブ用の制限
    if params.get("no_stack_per_target"):
        if _same_source_count(state, unit, effect_type, source_skill_id):
            return None

    # 同じスキルから同じ対象へ付与できる同種効果の上限（20節）
    if source_skill_id is not None:
        limit = master.battle.same_skill_stack_limit
        if _same_source_count(state, unit, effect_type, source_skill_id) >= limit:
            return None

    remaining = wanted

    # 自分のターン中に自分へ付けた効果を、その場で1減らさないための記録
    params["applied_turn_index"] = state.turn_index

    effect = BattleEffectState(
        effect_type=effect_type,
        duration_type=duration_type,
        battle_unit_id=unit.battle_unit_id,
        value=value,
        remaining=remaining,
        applied_round=state.current_round,
        source_unit_id=(
            source_unit.battle_unit_id if source_unit is not None else None
        ),
        source_skill_id=source_skill_id,
        params=params,
    )
    state.effects.append(effect)

    refresh_stats(state, unit)
    return effect


def remove_effect(state: BattleState, effect: BattleEffectState) -> None:
    if effect in state.effects:
        state.effects.remove(effect)


# ==================================================
# 現在ATK
# ==================================================
def _atk_base(state: BattleState, unit: BattleUnit) -> tuple[int, int]:
    """基準ATKと、そこへ既に含まれている補正量を返す。

    ATK交換（虚実反転）中は、交換時点の相手の現在ATKが基準値になります。
    その値には交換時点の補正が既に含まれているため、二重に加算しないよう、
    交換時に取り込んだ補正量（``absorbed``）も一緒に返します（34.4節）。
    """

    for effect in state.unit_effects(unit.battle_unit_id):
        if effect.effect_type == EFFECT_ATK_SWAP:
            swapped = effect.params.get("atk_value")
            if swapped is not None:
                return int(swapped), int(effect.params.get("absorbed", 0))

    return unit.base_atk, 0


def compute_atk(
    state: BattleState, unit: BattleUnit, *, include_attack_modifiers: bool = False
) -> int:
    """現在ATKを計算する。

    バフ合計とデバフ合計にそれぞれ上限を適用し、結果は0未満になりません（20節）。
    ``include_attack_modifiers`` が真の場合、「次の攻撃のみ」の補正も含めます。
    """

    master = load_master_data()

    positive = 0
    negative = 0

    for effect in state.unit_effects(unit.battle_unit_id):
        if effect.effect_type == EFFECT_ATK_MODIFIER:
            amount = int(effect.value or 0)

        elif effect.effect_type == EFFECT_CONDITIONAL_ATK_MODIFIER:
            condition = effect.params.get("condition") or {}
            if not evaluate_state_condition(state, unit, condition):
                continue
            amount = int(effect.value or 0)

        elif effect.effect_type == EFFECT_NEXT_ATTACK_ATK_MODIFIER:
            if not include_attack_modifiers:
                continue
            amount = int(effect.value or 0)

        else:
            continue

        if amount >= 0:
            positive += amount
        else:
            negative += amount

    positive = min(positive, master.battle.atk_buff_cap)
    negative = max(negative, -abs(master.battle.atk_debuff_cap))

    base, absorbed = _atk_base(state, unit)

    # 交換で取り込んだ分を差し引き、同じ補正を二重に足さない（34.4節）
    return max(0, base + positive + negative - absorbed)


def modifier_total(state: BattleState, unit: BattleUnit) -> int:
    """現在かかっている継続的なATK補正の合計（上限適用後）を返す。"""

    base, absorbed = _atk_base(state, unit)
    return compute_atk(state, unit) - base + absorbed


def refresh_atk(state: BattleState, unit: BattleUnit) -> None:
    unit.current_atk = compute_atk(state, unit)


def refresh_all_atk(state: BattleState) -> None:
    for unit in state.units.values():
        refresh_stats(state, unit)


def refresh_stats(state: BattleState, unit: BattleUnit) -> None:
    """現在ATKと現在SPDを再計算する。"""

    refresh_atk(state, unit)
    refresh_speed(state, unit)


def refresh_all_stats(state: BattleState) -> None:
    for unit in state.units.values():
        refresh_stats(state, unit)


# ==================================================
# 現在SPD（BATTLE_RULES.md 1節・8節）
# ==================================================
def compute_speed(state: BattleState, unit: BattleUnit) -> int:
    """現在SPDを計算する。基礎SPDへバフ・デバフを加算し、0未満にはしない。"""

    total = 0
    for effect in state.unit_effects(unit.battle_unit_id):
        if effect.effect_type == EFFECT_SPEED_MODIFIER:
            total += int(effect.value or 0)

    return max(0, base_speed_of(unit) + total)


def base_speed_of(unit: BattleUnit) -> int:
    """基礎SPDを返す。未設定の古いデータは現在SPDを基礎SPDとして扱う。"""

    return unit.base_speed if unit.base_speed else unit.speed


def refresh_speed(state: BattleState, unit: BattleUnit) -> None:
    unit.speed = compute_speed(state, unit)


# ==================================================
# 攻撃ダメージ軽減（保護効果）
# ==================================================
def attack_damage_reduction(state: BattleState, unit: BattleUnit) -> int:
    """その使い魔が受ける攻撃ダメージの軽減量を返す。

    スキルダメージ・継続ダメージには適用しません（3節）。
    """

    total = 0
    for effect in state.unit_effects(unit.battle_unit_id):
        if effect.effect_type == EFFECT_ATTACK_DAMAGE_REDUCTION:
            total += abs(int(effect.value or 0))

    return total


# ==================================================
# 回復阻害（デバフ）
# ==================================================
def is_heal_blocked(state: BattleState, unit: BattleUnit) -> bool:
    """HP回復と蘇生が阻害されているか。"""

    return any(
        effect.effect_type == EFFECT_HEAL_BLOCK
        for effect in state.unit_effects(unit.battle_unit_id)
    )


# ==================================================
# 毒強化（ヒュドラ「猛毒増幅」）
# ==================================================
def poison_amplifiers(state: BattleState, guild_id: int) -> list[BattleEffectState]:
    """``guild_id`` の敵へ付く毒を強化する効果を返す。"""

    return [
        effect
        for effect in state.effects
        if effect.effect_type == EFFECT_POISON_AMPLIFY
        and (unit := state.unit(effect.battle_unit_id)) is not None
        and unit.alive
        and unit.guild_id != guild_id
    ]


def amplify_poison(
    state: BattleState, target: BattleUnit, *, damage: int, turns: int
) -> tuple[int, int, int]:
    """毒のダメージと継続ターンへ強化を適用する。

    同じ毒に対して、1つの強化効果は1回だけ適用します。戻り値は
    ``(強化後ダメージ, 強化後ターン, 適用した強化の数)`` です。
    """

    applied = 0

    for effect in poison_amplifiers(state, target.guild_id):
        damage += int(effect.params.get("damage_bonus", 0))
        turns += int(effect.params.get("turn_bonus", 0))
        applied += 1

    return damage, turns, applied


# ==================================================
# 分類別の解除
# ==================================================
def categorized_effects(
    state: BattleState, unit: BattleUnit, category: str
) -> list[BattleEffectState]:
    """指定した分類の効果だけを返す（8節）。"""

    return [
        effect
        for effect in state.unit_effects(unit.battle_unit_id)
        if effect_category(effect.effect_type, effect.value) == category
    ]


def has_status_or_debuff(state: BattleState, unit: BattleUnit) -> bool:
    """状態異常またはデバフを受けているか（キマイラ「異形の狩り」）。"""

    for effect in state.unit_effects(unit.battle_unit_id):
        if effect_category(effect.effect_type, effect.value) in (
            CATEGORY_STATUS,
            CATEGORY_DEBUFF,
        ):
            return True

    return False


def remove_debuffs(state: BattleState, unit: BattleUnit) -> int:
    """デバフだけをすべて解除する。状態異常は解除しない（8節）。"""

    targets = categorized_effects(state, unit, CATEGORY_DEBUFF)
    for effect in targets:
        remove_effect(state, effect)

    if targets:
        refresh_stats(state, unit)

    return len(targets)


def attack_atk(state: BattleState, unit: BattleUnit) -> int:
    """今回の攻撃に使うATK（「次の攻撃のみ」の補正を含む）。"""

    return compute_atk(state, unit, include_attack_modifiers=True)


def attack_modifier_effects(
    state: BattleState, unit: BattleUnit
) -> list[BattleEffectState]:
    """「次の攻撃のみ」の補正を返す。攻撃開始時の控えを取るために使う。"""

    return [
        effect
        for effect in state.unit_effects(unit.battle_unit_id)
        if effect.effect_type == EFFECT_NEXT_ATTACK_ATK_MODIFIER
        and effect.duration_type == DURATION_ATTACKS
    ]


def consume_attack_modifiers(
    state: BattleState,
    unit: BattleUnit,
    *,
    targets: list[BattleEffectState] | None = None,
) -> None:
    """攻撃を1回行ったときに「次の攻撃のみ」の補正を消費する。

    ``targets`` には攻撃開始時点で存在していた効果だけを渡します。攻撃処理の
    途中で付与された効果（ベルフェゴール「怠惰の権能」など）は「次の攻撃」へ
    残す必要があり、今回の攻撃で消費してはいけません。
    """

    candidates = (
        attack_modifier_effects(state, unit) if targets is None else list(targets)
    )

    expired = []

    for effect in candidates:
        if effect not in state.effects:
            continue

        effect.remaining = (effect.remaining or 1) - 1
        if effect.remaining <= 0:
            expired.append(effect)

    for effect in expired:
        remove_effect(state, effect)

    refresh_stats(state, unit)


# ==================================================
# 状態異常
# ==================================================
def status_effects(state: BattleState, unit: BattleUnit) -> list[BattleEffectState]:
    return [
        effect
        for effect in state.unit_effects(unit.battle_unit_id)
        if effect.is_status
    ]


def has_status(state: BattleState, unit: BattleUnit, status: str) -> bool:
    return any(
        effect.status_name == status for effect in status_effects(state, unit)
    )


def active_statuses(state: BattleState, unit: BattleUnit) -> list[str]:
    found = []
    for effect in status_effects(state, unit):
        name = effect.status_name
        if name and name not in found:
            found.append(name)
    return found


def is_status_immune(state: BattleState, unit: BattleUnit) -> bool:
    return any(
        effect.effect_type == EFFECT_STATUS_IMMUNE
        for effect in state.unit_effects(unit.battle_unit_id)
    )


def remove_statuses(state: BattleState, unit: BattleUnit) -> list[str]:
    """状態異常だけをすべて解除する。デバフ・バフは解除しない（8節）。"""

    removed = []
    for effect in categorized_effects(state, unit, CATEGORY_STATUS):
        name = effect.status_name or STATUS_LABELS.get(
            effect.effect_type, effect.effect_type
        )
        removed.append(name)
        remove_effect(state, effect)

    if removed:
        refresh_stats(state, unit)

    return removed


def is_action_blocked(
    state: BattleState, unit: BattleUnit
) -> tuple[bool, str | None]:
    """行動不能かどうかと、その理由になった状態異常名を返す。"""

    for effect in status_effects(state, unit):
        name = effect.status_name
        if name in ACTION_BLOCKING_STATUSES:
            return True, name

    return False, None


def is_active_locked(state: BattleState, unit: BattleUnit) -> bool:
    """ACTIVEスキルの使用が禁止されているか（状態異常「ACTIVE使用不能」）。"""

    for effect in state.unit_effects(unit.battle_unit_id):
        if effect.effect_type == EFFECT_ACTIVE_LOCK:
            return True
        if effect.status_name == STATUS_ACTIVE_LOCK:
            return True

    return False


def forced_target_id(state: BattleState, unit: BattleUnit) -> int | None:
    """通常攻撃の対象が固定されている場合、その戦闘用使い魔IDを返す。"""

    for effect in state.unit_effects(unit.battle_unit_id):
        if effect.effect_type != EFFECT_TAUNT:
            continue

        target_id = effect.params.get("forced_target_unit_id")
        if target_id is None:
            continue

        target = state.unit(int(target_id))
        if target is not None and target.alive:
            return target.battle_unit_id

    return None


# ==================================================
# 継続ターンの更新
# ==================================================
def _applied_this_turn(state: BattleState, effect: BattleEffectState) -> bool:
    """その効果が、いま処理中のターンで付与されたものかを返す（34.5節）。"""

    return (
        effect.applied_round == state.current_round
        and effect.params.get("applied_turn_index") == state.turn_index
    )


def pending_poison(
    state: BattleState, unit: BattleUnit
) -> list[tuple[BattleEffectState, int]]:
    """行動終了時に適用する毒ダメージの一覧を返す。

    自分のターン中に自分へ付いた毒は、残りターンも同じターンでは減らないため
    （34.5節）、ここでダメージを与えると規定回数より1回多く発生します。
    そのため、このターンで付与された毒は次の行動終了から数えます。
    """

    pending = []
    for effect in status_effects(state, unit):
        if effect.status_name != STATUS_POISON:
            continue
        if (effect.remaining or 0) <= 0:
            continue
        if _applied_this_turn(state, effect):
            continue

        damage = int(effect.params.get("damage", 0))
        if damage > 0:
            pending.append((effect, damage))

    return pending


def decrement_turn_effects(
    state: BattleState, unit: BattleUnit
) -> list[BattleEffectState]:
    """行動終了時に残りターンを1減らし、切れた効果を解除する（20節）。

    その使い魔自身のターン中に付与された効果は、同じターンでは減らしません。
    """

    expired = []

    for effect in list(state.unit_effects(unit.battle_unit_id)):
        if effect.duration_type != DURATION_TURNS:
            continue

        if _applied_this_turn(state, effect):
            continue

        effect.remaining = (effect.remaining or 1) - 1
        if effect.remaining <= 0:
            expired.append(effect)

    for effect in expired:
        remove_effect(state, effect)

    refresh_atk(state, unit)
    return expired


def expire_round_effects(state: BattleState) -> list[BattleEffectState]:
    """ラウンド終了時に、ラウンド単位の効果を解除する。"""

    expired = []

    for effect in list(state.effects):
        if effect.duration_type != DURATION_ROUND_END:
            continue

        rounds = int(effect.params.get("rounds", 1))
        applied_round = effect.applied_round or state.current_round
        if state.current_round >= applied_round + rounds - 1:
            expired.append(effect)

    for effect in expired:
        remove_effect(state, effect)

    refresh_all_atk(state)
    return expired


def clear_unit_effects(state: BattleState, unit: BattleUnit) -> None:
    """戦闘用使い魔に付いた効果をすべて取り除く。"""

    for effect in list(state.unit_effects(unit.battle_unit_id)):
        remove_effect(state, effect)


# ==================================================
# 表示用のまとめ
# ==================================================
def _duration_label(effect: BattleEffectState) -> str:
    if effect.duration_type == DURATION_TURNS:
        return f"残{effect.remaining}"
    if effect.duration_type == DURATION_ATTACKS:
        return f"次{effect.remaining}回"
    if effect.duration_type == DURATION_ROUND_END:
        return "ラウンド終了まで"
    if effect.duration_type == DURATION_PERMANENT:
        return "常時"
    return ""


def buff_summary(state: BattleState, unit: BattleUnit) -> dict[str, list[str]]:
    """戦況Embedへ表示するバフ・デバフ・状態異常の一覧を作る（24節）。"""

    buffs: list[str] = []
    debuffs: list[str] = []
    statuses: list[str] = []
    others: list[str] = []

    for effect in state.unit_effects(unit.battle_unit_id):
        label = _duration_label(effect)

        if effect.effect_type in {
            EFFECT_ATK_MODIFIER,
            EFFECT_NEXT_ATTACK_ATK_MODIFIER,
        }:
            amount = int(effect.value or 0)
            text = f"ATK{amount:+d}"
            if label:
                text = f"{text}（{label}）"
            (buffs if amount >= 0 else debuffs).append(text)

        elif effect.effect_type == EFFECT_CONDITIONAL_ATK_MODIFIER:
            condition = effect.params.get("condition") or {}
            if not evaluate_state_condition(state, unit, condition):
                continue
            amount = int(effect.value or 0)
            (buffs if amount >= 0 else debuffs).append(f"ATK{amount:+d}")

        elif effect.effect_type == EFFECT_STATUS:
            name = effect.status_name or ""
            text = STATUS_LABELS.get(name, name)
            if label:
                text = f"{text}（{label}）"
            statuses.append(text)

        elif effect.effect_type == EFFECT_STATUS_IMMUNE:
            others.append(f"状態異常無効（{label}）")

        elif effect.effect_type == EFFECT_ACTIVE_LOCK:
            others.append(f"ACTIVE使用禁止（{label}）")

        elif effect.effect_type == EFFECT_TAUNT:
            others.append(f"攻撃対象固定（{label}）")

        elif effect.effect_type == EFFECT_ATK_SWAP:
            others.append("ATK交換中")

    return {
        "buffs": buffs,
        "debuffs": debuffs,
        "statuses": statuses,
        "others": others,
    }
