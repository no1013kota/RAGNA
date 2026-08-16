"""RAGNA Onlineの使い魔・スキル・戦闘状態を表すデータモデル。

このモジュールはDiscordへ依存しません。戦闘ルールの計算は
``game.battle_engine`` と ``game.skill_engine`` が、このモデルだけを使って
処理します。DBの読み書きは ``database/*.py`` の責務です。
"""

from __future__ import annotations

import enum

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from typing import Any


# ==================================================
# 性別（docs/GAME_SPEC.md 21節）
# ==================================================
GENDER_MALE = "male"
GENDER_FEMALE = "female"
GENDER_NONE = "none"

GENDER_VALUES = frozenset({GENDER_MALE, GENDER_FEMALE, GENDER_NONE})

# Noneは「性別未登録」。運営が登録するまで異性条件の効果は成立させません。
GENDER_UNSET = None


def is_opposite_gender(left: str | None, right: str | None) -> bool:
    """異性条件を満たすかどうかを返す。

    性別なし（``none``）と未登録（``None``）は、どちらも異性になりません。
    """

    if left not in {GENDER_MALE, GENDER_FEMALE}:
        return False

    if right not in {GENDER_MALE, GENDER_FEMALE}:
        return False

    return left != right


# ==================================================
# 状態異常（20節・21節）
# ==================================================
STATUS_POISON = "poison"
STATUS_CHARM = "charm"
STATUS_PETRIFY = "petrify"

STATUS_TYPES = frozenset({STATUS_POISON, STATUS_CHARM, STATUS_PETRIFY})

# 次の自身の行動を行えなくする状態異常
ACTION_BLOCKING_STATUSES = frozenset({STATUS_CHARM, STATUS_PETRIFY})

STATUS_LABELS = {
    STATUS_POISON: "毒",
    STATUS_CHARM: "魅了",
    STATUS_PETRIFY: "石化",
}


# ==================================================
# スキル区分と発動タイミング（19節）
# ==================================================
SKILL_TYPE_ACTIVE = "active"
SKILL_TYPE_PASSIVE = "passive"

SKILL_TYPES = frozenset({SKILL_TYPE_ACTIVE, SKILL_TYPE_PASSIVE})

TRIGGER_BATTLE_START = "battle_start"
TRIGGER_ROUND_START = "round_start"
TRIGGER_TURN_START = "turn_start"
TRIGGER_BEFORE_ACTION = "before_action"
TRIGGER_BEFORE_STATUS_APPLY = "before_status_apply"
TRIGGER_BEFORE_ATTACK = "before_attack"
TRIGGER_AFTER_ATTACK = "after_attack"
TRIGGER_ON_DAMAGE = "on_damage"
TRIGGER_HP_THRESHOLD = "hp_threshold"
TRIGGER_TURN_END = "turn_end"
TRIGGER_BEFORE_DEFEAT = "before_defeat"
TRIGGER_ON_DEFEAT = "on_defeat"
TRIGGER_ROUND_END = "round_end"
TRIGGER_ALWAYS = "always"

TRIGGERS = frozenset(
    {
        TRIGGER_BATTLE_START,
        TRIGGER_ROUND_START,
        TRIGGER_TURN_START,
        TRIGGER_BEFORE_ACTION,
        TRIGGER_BEFORE_STATUS_APPLY,
        TRIGGER_BEFORE_ATTACK,
        TRIGGER_AFTER_ATTACK,
        TRIGGER_ON_DAMAGE,
        TRIGGER_HP_THRESHOLD,
        TRIGGER_TURN_END,
        TRIGGER_BEFORE_DEFEAT,
        TRIGGER_ON_DEFEAT,
        TRIGGER_ROUND_END,
        TRIGGER_ALWAYS,
    }
)


class BattleEvent(str, enum.Enum):
    """25節の基本イベント順。行動ログの ``event_type`` にも使用する。"""

    BATTLE_START = "BATTLE_START"
    ROUND_START = "ROUND_START"
    TURN_START = "TURN_START"
    BEFORE_ACTION = "BEFORE_ACTION"
    SKILL = "SKILL"
    BEFORE_STATUS_APPLY = "BEFORE_STATUS_APPLY"
    STATUS_APPLY = "STATUS_APPLY"
    BEFORE_ATTACK = "BEFORE_ATTACK"
    ATTACK = "ATTACK"
    DAMAGE = "DAMAGE"
    AFTER_ATTACK = "AFTER_ATTACK"
    BEFORE_DEFEAT = "BEFORE_DEFEAT"
    DEFEAT_CHECK = "DEFEAT_CHECK"
    TURN_END = "TURN_END"
    NEXT_TURN = "NEXT_TURN"
    ROUND_END = "ROUND_END"
    # 表示用の補助イベント
    PASSIVE = "PASSIVE"
    HEAL = "HEAL"
    EFFECT = "EFFECT"
    SKIP = "SKIP"
    REVIVE = "REVIVE"
    POISON = "POISON"
    TIMEOUT = "TIMEOUT"
    BATTLE_END = "BATTLE_END"


# ==================================================
# 効果種別と継続方式
# ==================================================
EFFECT_DAMAGE = "damage"
EFFECT_ATTACK = "attack"
EFFECT_HEAL = "heal"
EFFECT_HEAL_PER_DEFEAT = "heal_per_defeat"
EFFECT_ATK_MODIFIER = "atk_modifier"
EFFECT_CONDITIONAL_ATK_MODIFIER = "conditional_atk_modifier"
EFFECT_NEXT_ATTACK_ATK_MODIFIER = "next_attack_atk_modifier"
EFFECT_STATUS = "status"
EFFECT_INSTANT_DEFEAT = "instant_defeat"
EFFECT_REVIVE = "revive"
EFFECT_SURVIVE_WITH_HP = "survive_with_hp"
EFFECT_NULLIFY_DAMAGE = "nullify_damage"
EFFECT_NULLIFY_STATUS = "nullify_status"
EFFECT_STATUS_REFLECT = "status_reflect"
EFFECT_STATUS_IMMUNE = "status_immune"
EFFECT_CLEANSE_STATUS = "cleanse_status"
EFFECT_ATK_SWAP = "atk_swap"
EFFECT_TAUNT = "taunt"
EFFECT_ACTIVE_LOCK = "active_lock"

EFFECT_TYPES = frozenset(
    {
        EFFECT_DAMAGE,
        EFFECT_ATTACK,
        EFFECT_HEAL,
        EFFECT_HEAL_PER_DEFEAT,
        EFFECT_ATK_MODIFIER,
        EFFECT_CONDITIONAL_ATK_MODIFIER,
        EFFECT_NEXT_ATTACK_ATK_MODIFIER,
        EFFECT_STATUS,
        EFFECT_INSTANT_DEFEAT,
        EFFECT_REVIVE,
        EFFECT_SURVIVE_WITH_HP,
        EFFECT_NULLIFY_DAMAGE,
        EFFECT_NULLIFY_STATUS,
        EFFECT_STATUS_REFLECT,
        EFFECT_STATUS_IMMUNE,
        EFFECT_CLEANSE_STATUS,
        EFFECT_ATK_SWAP,
        EFFECT_TAUNT,
        EFFECT_ACTIVE_LOCK,
    }
)

# 戦闘用使い魔へ継続的に保存する効果（instant以外）
DURATION_INSTANT = "instant"
DURATION_TURNS = "turns"
DURATION_ROUND_END = "round_end"
DURATION_ATTACKS = "attacks"
DURATION_PERMANENT = "permanent"

DURATION_TYPES = frozenset(
    {
        DURATION_INSTANT,
        DURATION_TURNS,
        DURATION_ROUND_END,
        DURATION_ATTACKS,
        DURATION_PERMANENT,
    }
)

# 効果の対象区分
TARGET_SELF = "self"
TARGET_ALLY_ALL = "ally_all"
TARGET_ENEMY_ALL = "enemy_all"
TARGET_EVENT_UNIT = "event_unit"
TARGET_EVENT_SOURCE = "event_source"
TARGET_SELECTION_PREFIX = "selection:"


# ==================================================
# 共通の計算処理
# ==================================================
def round_half_up(value: float | Decimal) -> int:
    """四捨五入して整数を返す。

    Pythonの組み込み ``round`` は偶数丸めのため、仕様の「四捨五入」とは
    異なる結果になります（例：``round(12.5)`` は12）。仕様に合わせるため、
    Decimalで明示的にHALF_UPへ丸めます。
    """

    return int(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


@dataclass(frozen=True)
class LevelStats:
    """レベル反映後の能力値。"""

    max_hp: int
    atk: int
    speed: int


def compute_level_stats(
    base_hp: int,
    base_atk: int,
    base_speed: int,
    level: int,
    *,
    hp_rate: float,
    atk_rate: float,
    speed_levels: tuple[int, ...] | list[int],
    speed_value: int,
    speed_max: int,
) -> LevelStats:
    """Lv.0の基本能力からレベル反映後の能力値を計算する（10.2節）。

    HPとATKは ``基本値 × (1 + 成長率 × レベル)`` を四捨五入します。
    SPDは指定レベル到達ごとに加算し、上限を超えません。
    """

    max_hp = round_half_up(base_hp * (1 + hp_rate * level))
    atk = round_half_up(base_atk * (1 + atk_rate * level))

    speed_bonus = sum(
        speed_value for threshold in speed_levels if level >= threshold
    )
    speed = min(base_speed + speed_bonus, speed_max)

    return LevelStats(max_hp=max_hp, atk=atk, speed=speed)


# ==================================================
# マスターデータ
# ==================================================
@dataclass(frozen=True)
class SkillEffect:
    """スキル1つが持つ効果。19.1節の必須項目に合わせる。"""

    effect_type: str
    value: int | None = None
    duration_turns: int | None = None
    chance: int | None = None
    target_type: str = TARGET_SELF
    duration_type: str = DURATION_INSTANT
    params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "effect_type": self.effect_type,
            "value": self.value,
            "duration_turns": self.duration_turns,
            "chance": self.chance,
            "target_type": self.target_type,
            "duration_type": self.duration_type,
            "params": dict(self.params),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SkillEffect":
        return cls(
            effect_type=data["effect_type"],
            value=data.get("value"),
            duration_turns=data.get("duration_turns"),
            chance=data.get("chance"),
            target_type=data.get("target_type") or TARGET_SELF,
            duration_type=data.get("duration_type") or DURATION_INSTANT,
            params=dict(data.get("params") or {}),
        )


@dataclass(frozen=True)
class TargetGroup:
    """アクティブスキルが要求する対象選択の1グループ。"""

    key: str
    side: str  # "enemy" または "ally"
    count: int = 1
    filter: tuple[dict[str, Any], ...] = ()
    allow_duplicate: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TargetGroup":
        return cls(
            key=data["key"],
            side=data["side"],
            count=int(data.get("count", 1)),
            filter=tuple(data.get("filter") or ()),
            allow_duplicate=bool(data.get("allow_duplicate", False)),
        )


@dataclass(frozen=True)
class Skill:
    """アクティブ・パッシブ共通のスキル定義。"""

    skill_id: str
    name: str
    description: str
    skill_type: str
    trigger: str | None = None
    target_type: str | None = None
    priority: int = 100
    max_uses_per_battle: int | None = None
    consumes_attack: bool = False
    targets: tuple[TargetGroup, ...] = ()
    conditions: tuple[dict[str, Any], ...] = ()
    effects: tuple[SkillEffect, ...] = ()
    enabled: bool = True
    version: int = 1

    @property
    def is_active(self) -> bool:
        return self.skill_type == SKILL_TYPE_ACTIVE

    @property
    def is_passive(self) -> bool:
        return self.skill_type == SKILL_TYPE_PASSIVE

    def requires_selection(self) -> bool:
        return bool(self.targets)


@dataclass(frozen=True)
class FamiliarMaster:
    """使い魔マスター（10.1節）。"""

    familiar_id: str
    name: str
    rank: str
    base_hp: int
    base_atk: int
    speed: int
    cost: int
    gender: str | None = None
    description: str = ""
    skill_ids: tuple[str, ...] = ()
    image_file: str | None = None
    enabled: bool = True
    version: int = 1


@dataclass(frozen=True)
class OwnedFamiliar:
    """プレイヤーが所有する使い魔の1個体。"""

    instance_id: int
    user_id: int
    familiar_id: str
    level: int = 0
    status: str = "owned"


# ==================================================
# 戦闘データ
# ==================================================
@dataclass
class BattleEffectState:
    """戦闘用使い魔が受けている継続効果。"""

    effect_type: str
    duration_type: str
    battle_unit_id: int
    value: int | None = None
    remaining: int | None = None
    applied_round: int | None = None
    source_unit_id: int | None = None
    source_skill_id: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    effect_id: int | None = None

    @property
    def status_name(self) -> str | None:
        if self.effect_type != EFFECT_STATUS:
            return None
        return self.params.get("status")

    @property
    def is_status(self) -> bool:
        return self.effect_type == EFFECT_STATUS


@dataclass
class BattleUnit:
    """戦闘用使い魔（14節）。所有使い魔の永続データとは分離する。"""

    battle_unit_id: int
    battle_id: int
    guild_id: int
    player_id: int
    familiar_instance_id: int
    familiar_id: str
    level: int
    max_hp: int
    current_hp: int
    base_atk: int
    current_atk: int
    speed: int
    cost: int
    slot: int
    gender: str | None = None
    alive: bool = True
    order_seed: int = 0
    active_skill_uses: dict[str, int] = field(default_factory=dict)
    passive_uses: dict[str, int] = field(default_factory=dict)
    state_flags: dict[str, Any] = field(default_factory=dict)

    @property
    def hp_ratio(self) -> float:
        if self.max_hp <= 0:
            return 0.0
        return self.current_hp / self.max_hp


@dataclass
class BattleLogEntry:
    """行動ログ1件。Discord表示は ``game.battle_embed`` が行う。"""

    event_type: str
    round: int
    sequence: int = 0
    actor_unit_id: int | None = None
    target_unit_id: int | None = None
    skill_id: str | None = None
    amount: int | None = None
    detail: dict[str, Any] = field(default_factory=dict)


BATTLE_STATUS_PREPARING = "preparing"
BATTLE_STATUS_IN_PROGRESS = "in_progress"
BATTLE_STATUS_PAUSED = "paused"
BATTLE_STATUS_FINISHED = "finished"
BATTLE_STATUS_ABORTED = "aborted"

RESULT_GUILD_A = "guild_a"
RESULT_GUILD_B = "guild_b"
RESULT_DRAW = "draw"
RESULT_ABORTED = "aborted"


@dataclass
class BattleState:
    """1つのギルドバトルの全状態。"""

    battle_id: int
    guild_a_id: int
    guild_b_id: int
    status: str = BATTLE_STATUS_PREPARING
    result: str | None = None
    end_reason: str | None = None
    current_round: int = 0
    turn_index: int = 0
    turn_order: list[int] = field(default_factory=list)
    current_unit_id: int | None = None
    action_seq: int = 0
    log_seq: int = 0
    remaining_seconds: dict[int, int] = field(default_factory=dict)
    units: dict[int, BattleUnit] = field(default_factory=dict)
    effects: list[BattleEffectState] = field(default_factory=list)
    logs: list[BattleLogEntry] = field(default_factory=list)

    # ==================================================
    # 参照
    # ==================================================
    def unit(self, battle_unit_id: int | None) -> BattleUnit | None:
        if battle_unit_id is None:
            return None
        return self.units.get(battle_unit_id)

    def guild_units(self, guild_id: int) -> list[BattleUnit]:
        return sorted(
            (unit for unit in self.units.values() if unit.guild_id == guild_id),
            key=lambda unit: unit.slot,
        )

    def living_units(self, guild_id: int) -> list[BattleUnit]:
        return [unit for unit in self.guild_units(guild_id) if unit.alive]

    def enemy_guild_id(self, guild_id: int) -> int:
        return self.guild_b_id if guild_id == self.guild_a_id else self.guild_a_id

    def allies_of(self, unit: BattleUnit) -> list[BattleUnit]:
        return self.living_units(unit.guild_id)

    def enemies_of(self, unit: BattleUnit) -> list[BattleUnit]:
        return self.living_units(self.enemy_guild_id(unit.guild_id))

    def unit_effects(self, battle_unit_id: int) -> list[BattleEffectState]:
        return [
            effect
            for effect in self.effects
            if effect.battle_unit_id == battle_unit_id
        ]

    def is_guild_wiped(self, guild_id: int) -> bool:
        units = self.guild_units(guild_id)
        return bool(units) and all(not unit.alive for unit in units)

    def current_unit(self) -> BattleUnit | None:
        return self.unit(self.current_unit_id)

    # ==================================================
    # ログ
    # ==================================================
    def add_log(
        self,
        event_type: str,
        *,
        actor_unit_id: int | None = None,
        target_unit_id: int | None = None,
        skill_id: str | None = None,
        amount: int | None = None,
        **detail: Any,
    ) -> BattleLogEntry:
        self.log_seq += 1
        # BattleEventはEnumのため、str()ではなく値そのものを保存する。
        entry = BattleLogEntry(
            event_type=getattr(event_type, "value", event_type),
            round=self.current_round,
            sequence=self.log_seq,
            actor_unit_id=actor_unit_id,
            target_unit_id=target_unit_id,
            skill_id=skill_id,
            amount=amount,
            detail=detail,
        )
        self.logs.append(entry)
        return entry


# ==================================================
# プレイヤーの行動
# ==================================================
ACTION_ATTACK = "attack"
ACTION_SKILL = "skill"


@dataclass(frozen=True)
class BattleAction:
    """バトル専用チャンネルから受け取る1回の行動。"""

    action_type: str
    actor_unit_id: int
    target_unit_id: int | None = None
    skill_id: str | None = None
    selections: dict[str, tuple[int, ...]] = field(default_factory=dict)
    automatic: bool = False


class BattleRuleError(Exception):
    """戦闘ルール上、その操作を実行できない場合に送出する。"""
