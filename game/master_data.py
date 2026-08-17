"""``data/master/`` のマスターデータを読み込み、検証して公開するモジュール。

数値バランス、使い魔、スキル、ガチャ設定はコードへ埋め込まず、この
モジュール経由で参照します。Bot起動時に一度読み込み、許可された種類・
必須項目・数値範囲を検証します（19.1節）。
"""

from __future__ import annotations

import json
import logging

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import (
    DURATION_TYPES,
    EFFECT_TYPES,
    GENDER_VALUES,
    SKILL_TYPES,
    TRIGGERS,
    FamiliarMaster,
    LevelStats,
    Skill,
    SkillEffect,
    TargetGroup,
    compute_level_stats,
    round_half_up,
)


logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MASTER_DIRECTORY = PROJECT_ROOT / "data" / "master"


class MasterDataError(Exception):
    """マスターデータの内容に問題がある場合に送出する。"""


# ==================================================
# バランス設定
# ==================================================
@dataclass(frozen=True)
class GuildBalance:
    create_cost: int
    rename_cost: int
    member_slot_cost: int
    initial_capacity: int
    max_capacity: int
    name_min_length: int
    name_max_length: int
    description_min_length: int
    description_max_length: int
    archive_days: int
    archive_name_prefix: str


@dataclass(frozen=True)
class FamiliarBalance:
    rank_order: tuple[str, ...]
    usable_rank_offset: int
    min_level: int
    max_level: int
    hp_growth_rate_per_level: float
    atk_growth_rate_per_level: float
    speed_growth_levels: tuple[int, ...]
    speed_growth_value: int
    speed_max: int
    sell_base_prices: dict[str, int]
    sell_price_multiplier: float
    fusion_cost_rate_per_material: float


@dataclass(frozen=True)
class BetBalance:
    """ギルドバトルのベット設定（26.2節）。

    ``coin`` は1人あたりのベット額です。負けた側から集めたcoinを勝った側へ
    分配するため、coinの総量は増えません。XPは新しく付与します。
    """

    coin: int
    win_xp: int
    lose_xp: int
    draw_xp: int


@dataclass(frozen=True)
class RankingBalance:
    win_points: int
    draw_points: int
    lose_points: int
    display_limit: int


@dataclass(frozen=True)
class BattleBalance:
    max_units: int
    # 編成の合計COST上限。0以下なら制限なし（10.6節）。
    max_total_cost: int
    max_members: int
    min_members: int
    familiars_per_member: dict[int, int]
    critical_chance_permille: int
    critical_multiplier: float
    atk_buff_cap: int
    atk_debuff_cap: int
    same_skill_stack_limit: int
    guild_time_seconds: int
    turn_time_seconds: int
    battle_channel_retention_days: int
    surrender_reward_from_round: int
    bet: BetBalance
    reward_daily_limit_per_player: int
    ranking: RankingBalance
    battle_log_retention_days: int
    admin_log_retention_days: int


@dataclass(frozen=True)
class GachaPool:
    pool_id: str
    name: str
    single_cost: int
    multi_cost: int
    multi_count: int
    guaranteed_slot: int
    is_public: bool
    rates: dict[str, dict[str, int]]
    # 使い魔が未登録のランクの排出率を寄せる先（Noneなら残りランクへ按分）
    missing_rank_fallback: str | None = None


@dataclass(frozen=True)
class MasterData:
    guild: GuildBalance
    familiar: FamiliarBalance
    battle: BattleBalance
    familiars: dict[str, FamiliarMaster]
    skills: dict[str, Skill]
    gacha_pools: dict[str, GachaPool]
    warnings: tuple[str, ...] = ()

    # ==================================================
    # 参照
    # ==================================================
    def get_familiar(self, familiar_id: str) -> FamiliarMaster | None:
        return self.familiars.get(familiar_id)

    def get_skill(self, skill_id: str) -> Skill | None:
        return self.skills.get(skill_id)

    def skills_of(self, familiar_id: str) -> list[Skill]:
        familiar = self.familiars.get(familiar_id)
        if familiar is None:
            return []

        found = []
        for skill_id in familiar.skill_ids:
            skill = self.skills.get(skill_id)
            if skill is not None and skill.enabled:
                found.append(skill)
        return found

    def active_skills_of(self, familiar_id: str) -> list[Skill]:
        return [skill for skill in self.skills_of(familiar_id) if skill.is_active]

    def passive_skills_of(self, familiar_id: str) -> list[Skill]:
        return [skill for skill in self.skills_of(familiar_id) if skill.is_passive]

    def familiars_by_rank(self, rank: str) -> list[FamiliarMaster]:
        return sorted(
            (
                familiar
                for familiar in self.familiars.values()
                if familiar.rank == rank and familiar.enabled
            ),
            key=lambda familiar: familiar.familiar_id,
        )

    def gacha_familiars_by_rank(self, rank: str) -> list[FamiliarMaster]:
        """ガチャで抽選できる使い魔だけを返す。

        コンプリート報酬など、``in_gacha`` が偽の使い魔は除きます。
        """

        return [
            familiar
            for familiar in self.familiars_by_rank(rank)
            if familiar.in_gacha
        ]

    def complete_reward_familiars(self) -> list[FamiliarMaster]:
        """コンプリート報酬として解放される使い魔を返す。"""

        return [
            familiar
            for familiar in self.familiars.values()
            if familiar.enabled and not familiar.in_gacha
        ]

    def registered_ranks(self) -> list[str]:
        """1体以上のマスターデータがあるランクを、弱い順に返す。"""

        return [
            rank
            for rank in self.familiar.rank_order
            if self.familiars_by_rank(rank)
        ]

    def missing_ranks(self, pool_id: str = "standard") -> list[str]:
        """ガチャ設定に確率があるのに使い魔が未登録のランクを返す。"""

        pool = self.gacha_pools.get(pool_id)
        if pool is None:
            return []

        wanted: set[str] = set()
        for weights in pool.rates.values():
            wanted.update(rank for rank, weight in weights.items() if weight > 0)

        return [
            rank
            for rank in self.familiar.rank_order
            if rank in wanted and not self.gacha_familiars_by_rank(rank)
        ]

    # ==================================================
    # 計算
    # ==================================================
    def level_stats(self, familiar_id: str, level: int) -> LevelStats | None:
        familiar = self.familiars.get(familiar_id)
        if familiar is None:
            return None

        # 初期レベル（min_level）が基本値。そこからの上積み分で計算する。
        return compute_level_stats(
            familiar.base_hp,
            familiar.base_atk,
            familiar.speed,
            max(0, level - self.familiar.min_level),
            hp_rate=self.familiar.hp_growth_rate_per_level,
            atk_rate=self.familiar.atk_growth_rate_per_level,
            speed_levels=self.familiar.speed_growth_levels,
            speed_value=self.familiar.speed_growth_value,
            speed_max=self.familiar.speed_max,
        )

    def familiar_limit_per_member(self, member_count: int) -> int:
        """出場者数に応じた「1人あたりの使い魔上限」を返す（9節）。

        設定に無い人数の場合は、合計上限を人数で割った切り上げを使います。
        """

        limit = self.battle.familiars_per_member.get(member_count)
        if limit is not None:
            return limit

        if member_count <= 0:
            return 0

        return -(-self.battle.max_units // member_count)

    def sell_price(self, rank: str, level: int) -> int:
        """売却額を返す（10.2節）。``基準価格 × レベル``。

        初期レベル（Lv.1）では基準価格そのままになり、合成でレベルが1上がるごとに
        基準価格1つ分だけ増えます。
        """

        base = self.familiar.sell_base_prices.get(rank)
        if base is None:
            return 0

        levels = max(1, int(level))
        return round_half_up(base * levels * self.familiar.sell_price_multiplier)

    def fusion_cost(self, rank: str, material_count: int) -> int:
        """合成にかかるcoinを返す（10.2節）。

        ``ランク基準価格 × fusion_cost_rate_per_material × 素材の体数`` です。
        係数を0にすると無料になります。
        """

        base = self.familiar.sell_base_prices.get(rank)
        if base is None or material_count <= 0:
            return 0

        return round_half_up(
            base * self.familiar.fusion_cost_rate_per_material * material_count
        )

    def usable_ranks(
        self, player_rank: str | None, *, is_sub_manager: bool = False
    ) -> list[str]:
        """プレイヤーが使役できる使い魔ランクを返す（10.4節）。

        七星は特権としてすべてのランクを使役できます。それ以外は自分の
        ランクより ``usable_rank_offset`` 段階上までです。
        """

        order = list(self.familiar.rank_order)

        if is_sub_manager:
            return order

        if player_rank not in order:
            return []

        limit = min(
            order.index(player_rank) + self.familiar.usable_rank_offset,
            len(order) - 1,
        )
        return order[: limit + 1]

    def can_use_rank(
        self,
        player_rank: str | None,
        familiar_rank: str,
        *,
        is_sub_manager: bool = False,
    ) -> bool:
        return familiar_rank in self.usable_ranks(
            player_rank, is_sub_manager=is_sub_manager
        )


# ==================================================
# 読み込み
# ==================================================
def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise MasterDataError(f"マスターデータが見つかりません: {path}")

    try:
        with path.open("r", encoding="utf-8") as file:
            return json.load(file)
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise MasterDataError(f"マスターデータを読み込めません: {path}: {exc}") from exc


def _require(data: dict[str, Any], key: str, path: Path) -> Any:
    if key not in data:
        raise MasterDataError(f"{path.name} に必須項目 {key} がありません")
    return data[key]


def _load_balance(warnings: list[str]) -> tuple[GuildBalance, FamiliarBalance, BattleBalance]:
    path = MASTER_DIRECTORY / "balance.json"
    raw = _read_json(path)

    guild_raw = _require(raw, "guild", path)
    familiar_raw = _require(raw, "familiar", path)
    battle_raw = _require(raw, "battle", path)

    guild = GuildBalance(
        create_cost=int(guild_raw["create_cost"]),
        rename_cost=int(guild_raw["rename_cost"]),
        member_slot_cost=int(guild_raw["member_slot_cost"]),
        initial_capacity=int(guild_raw["initial_capacity"]),
        max_capacity=int(guild_raw["max_capacity"]),
        name_min_length=int(guild_raw["name_min_length"]),
        name_max_length=int(guild_raw["name_max_length"]),
        description_min_length=int(guild_raw["description_min_length"]),
        description_max_length=int(guild_raw["description_max_length"]),
        archive_days=int(guild_raw["archive_days"]),
        archive_name_prefix=str(guild_raw["archive_name_prefix"]),
    )

    if guild.initial_capacity > guild.max_capacity:
        raise MasterDataError("ギルドの初期定員が最大定員を超えています")

    familiar = FamiliarBalance(
        rank_order=tuple(familiar_raw["rank_order"]),
        usable_rank_offset=int(familiar_raw["usable_rank_offset"]),
        min_level=int(familiar_raw.get("min_level", 1)),
        max_level=int(familiar_raw["max_level"]),
        hp_growth_rate_per_level=float(familiar_raw["hp_growth_rate_per_level"]),
        atk_growth_rate_per_level=float(familiar_raw["atk_growth_rate_per_level"]),
        speed_growth_levels=tuple(int(x) for x in familiar_raw["speed_growth_levels"]),
        speed_growth_value=int(familiar_raw["speed_growth_value"]),
        speed_max=int(familiar_raw["speed_max"]),
        sell_base_prices={
            str(rank): int(price)
            for rank, price in familiar_raw["sell_base_prices"].items()
        },
        sell_price_multiplier=float(familiar_raw.get("sell_price_multiplier", 1.0)),
        fusion_cost_rate_per_material=float(
            familiar_raw.get("fusion_cost_rate_per_material", 0.0)
        ),
    )

    if not 1 <= familiar.min_level <= familiar.max_level:
        raise MasterDataError("familiar.min_level / max_level の関係が不正です")

    if familiar.fusion_cost_rate_per_material < 0:
        raise MasterDataError(
            "familiar.fusion_cost_rate_per_material は0以上にしてください"
        )

    if not familiar.rank_order:
        raise MasterDataError("familiar.rank_order が空です")

    for rank in familiar.rank_order:
        if rank not in familiar.sell_base_prices:
            warnings.append(f"ランク{rank}の売却基準価格が未設定です")

    bet_raw = battle_raw["bet"]
    for key in ("coin", "win_xp", "lose_xp", "draw_xp"):
        if key not in bet_raw:
            raise MasterDataError(f"battle.bet に {key} がありません")

    bet = BetBalance(
        coin=int(bet_raw["coin"]),
        win_xp=int(bet_raw["win_xp"]),
        lose_xp=int(bet_raw["lose_xp"]),
        draw_xp=int(bet_raw["draw_xp"]),
    )
    if bet.coin < 0:
        raise MasterDataError("battle.bet.coin は0以上にしてください")

    ranking_raw = battle_raw["ranking"]
    battle = BattleBalance(
        max_units=int(battle_raw["max_units"]),
        max_total_cost=int(battle_raw.get("max_total_cost", 0)),
        max_members=int(battle_raw["max_members"]),
        min_members=int(battle_raw["min_members"]),
        familiars_per_member={
            int(members): int(limit)
            for members, limit in battle_raw["familiars_per_member"].items()
        },
        critical_chance_permille=int(battle_raw["critical_chance_permille"]),
        critical_multiplier=float(battle_raw["critical_multiplier"]),
        atk_buff_cap=int(battle_raw["atk_buff_cap"]),
        atk_debuff_cap=int(battle_raw["atk_debuff_cap"]),
        same_skill_stack_limit=int(battle_raw["same_skill_stack_limit"]),
        guild_time_seconds=int(battle_raw["guild_time_seconds"]),
        turn_time_seconds=int(battle_raw["turn_time_seconds"]),
        battle_channel_retention_days=int(
            battle_raw.get("battle_channel_retention_days", 7)
        ),
        surrender_reward_from_round=int(battle_raw["surrender_reward_from_round"]),
        bet=bet,
        reward_daily_limit_per_player=int(battle_raw["reward_daily_limit_per_player"]),
        ranking=RankingBalance(
            win_points=int(ranking_raw["win_points"]),
            draw_points=int(ranking_raw["draw_points"]),
            lose_points=int(ranking_raw["lose_points"]),
            display_limit=int(ranking_raw["display_limit"]),
        ),
        battle_log_retention_days=int(battle_raw["battle_log_retention_days"]),
        admin_log_retention_days=int(battle_raw["admin_log_retention_days"]),
    )

    if not 0 <= battle.critical_chance_permille <= 1000:
        raise MasterDataError("critical_chance_permille は0～1000で設定してください")

    if battle.max_units <= 0:
        raise MasterDataError("max_units は1以上で設定してください")

    if not 1 <= battle.min_members <= battle.max_members:
        raise MasterDataError("min_members / max_members の関係が不正です")

    for members in range(battle.min_members, battle.max_members + 1):
        if members not in battle.familiars_per_member:
            raise MasterDataError(
                f"familiars_per_member に出場者{members}人分の上限がありません"
            )

        limit = battle.familiars_per_member[members]
        if limit <= 0:
            raise MasterDataError("familiars_per_member の上限は1以上にしてください")

        # 人数 × 上限が合計上限に届かないと、5体そろえられない編成が生まれる
        if members * limit < battle.max_units:
            raise MasterDataError(
                f"出場者{members}人では合計{battle.max_units}体をセットできません"
            )

    return guild, familiar, battle


def _load_skills() -> dict[str, Skill]:
    path = MASTER_DIRECTORY / "skills.json"
    raw = _read_json(path)

    skills: dict[str, Skill] = {}

    for entry in _require(raw, "skills", path):
        skill_id = entry["skill_id"]

        if skill_id in skills:
            raise MasterDataError(f"スキルIDが重複しています: {skill_id}")

        skill_type = entry["skill_type"]
        if skill_type not in SKILL_TYPES:
            raise MasterDataError(f"{skill_id}: 未対応のskill_typeです: {skill_type}")

        trigger = entry.get("trigger")
        extra_triggers = tuple(entry.get("extra_triggers") or ())

        if skill_type == "passive":
            if trigger not in TRIGGERS:
                raise MasterDataError(f"{skill_id}: 未対応のtriggerです: {trigger}")
            for extra in extra_triggers:
                if extra not in TRIGGERS:
                    raise MasterDataError(
                        f"{skill_id}: 未対応のextra_triggersです: {extra}"
                    )
        else:
            if trigger is not None:
                raise MasterDataError(
                    f"{skill_id}: アクティブスキルにtriggerは設定できません"
                )
            if extra_triggers:
                raise MasterDataError(
                    f"{skill_id}: アクティブスキルにextra_triggersは設定できません"
                )

        max_uses = entry.get("max_uses_per_battle")
        if max_uses is not None and int(max_uses) <= 0:
            raise MasterDataError(f"{skill_id}: max_uses_per_battle は1以上にしてください")

        targets = tuple(
            TargetGroup.from_dict(group) for group in entry.get("targets") or ()
        )
        for group in targets:
            if group.side not in {"enemy", "ally"}:
                raise MasterDataError(f"{skill_id}: 未対応のtargets.sideです: {group.side}")
            if group.count <= 0:
                raise MasterDataError(f"{skill_id}: targets.count は1以上にしてください")

        target_keys = {group.key for group in targets}

        effects: list[SkillEffect] = []
        for effect_raw in entry.get("effects") or ():
            effect = SkillEffect.from_dict(effect_raw)

            if effect.effect_type not in EFFECT_TYPES:
                raise MasterDataError(
                    f"{skill_id}: 未対応のeffect_typeです: {effect.effect_type}"
                )
            if effect.duration_type not in DURATION_TYPES:
                raise MasterDataError(
                    f"{skill_id}: 未対応のduration_typeです: {effect.duration_type}"
                )
            if effect.duration_turns is not None and effect.duration_turns <= 0:
                raise MasterDataError(f"{skill_id}: duration_turns は1以上にしてください")
            if effect.chance is not None and not 0 <= effect.chance <= 1000:
                raise MasterDataError(f"{skill_id}: chance は0～1000で設定してください")

            if effect.target_type.startswith("selection:"):
                key = effect.target_type.split(":", 1)[1]
                if key not in target_keys:
                    raise MasterDataError(
                        f"{skill_id}: targetsに存在しない選択キーです: {key}"
                    )

            partner = effect.params.get("partner")
            if partner and str(partner).startswith("selection:"):
                key = str(partner).split(":", 1)[1]
                if key not in target_keys:
                    raise MasterDataError(
                        f"{skill_id}: partnerに存在しない選択キーです: {key}"
                    )

            if effect.on_trigger is not None:
                allowed = {trigger, *extra_triggers}
                if effect.on_trigger not in allowed:
                    raise MasterDataError(
                        f"{skill_id}: on_trigger がこのスキルのタイミングに"
                        f"ありません: {effect.on_trigger}"
                    )

            effects.append(effect)

        if not effects:
            raise MasterDataError(f"{skill_id}: effects が空です")

        skills[skill_id] = Skill(
            skill_id=skill_id,
            name=entry["name"],
            description=entry["description"],
            skill_type=skill_type,
            trigger=trigger,
            extra_triggers=extra_triggers,
            target_type=entry.get("target_type"),
            priority=int(entry.get("priority", 100)),
            max_uses_per_battle=None if max_uses is None else int(max_uses),
            consumes_attack=bool(entry.get("consumes_attack", False)),
            targets=targets,
            conditions=tuple(entry.get("conditions") or ()),
            effects=tuple(effects),
            enabled=bool(entry.get("enabled", True)),
            version=int(entry.get("version", 1)),
        )

    return skills


def _load_familiars(
    skills: dict[str, Skill],
    rank_order: tuple[str, ...],
    warnings: list[str],
) -> dict[str, FamiliarMaster]:
    path = MASTER_DIRECTORY / "familiars.json"
    raw = _read_json(path)

    familiars: dict[str, FamiliarMaster] = {}

    for entry in _require(raw, "familiars", path):
        familiar_id = entry["familiar_id"]

        if familiar_id in familiars:
            raise MasterDataError(f"使い魔IDが重複しています: {familiar_id}")

        rank = entry["rank"]
        if rank not in rank_order:
            raise MasterDataError(f"{familiar_id}: 未対応のランクです: {rank}")

        gender = entry.get("gender")
        if gender is not None and gender not in GENDER_VALUES:
            raise MasterDataError(f"{familiar_id}: 未対応の性別です: {gender}")
        if gender is None:
            warnings.append(f"{familiar_id}: 性別が未登録です")

        skill_ids = tuple(entry.get("skills") or ())
        for skill_id in skill_ids:
            if skill_id not in skills:
                raise MasterDataError(
                    f"{familiar_id}: 未定義のスキルを参照しています: {skill_id}"
                )

        active_count = sum(1 for sid in skill_ids if skills[sid].is_active)
        if active_count > 2:
            raise MasterDataError(
                f"{familiar_id}: アクティブスキルは最大2個までです（19.2節）"
            )

        speed = int(entry["speed"])
        if not 0 <= speed <= 100:
            raise MasterDataError(f"{familiar_id}: SPDは0～100で設定してください")

        familiars[familiar_id] = FamiliarMaster(
            familiar_id=familiar_id,
            name=entry["name"],
            rank=rank,
            base_hp=int(entry["base_hp"]),
            base_atk=int(entry["base_atk"]),
            speed=speed,
            cost=int(entry["cost"]),
            gender=gender,
            description=entry.get("description") or "",
            skill_ids=skill_ids,
            image_file=entry.get("image_file"),
            in_gacha=bool(entry.get("in_gacha", True)),
            enabled=bool(entry.get("enabled", True)),
            version=int(entry.get("version", 1)),
        )

    if not familiars:
        raise MasterDataError("使い魔マスターが1体も登録されていません")

    return familiars


def _load_gacha(
    rank_order: tuple[str, ...], warnings: list[str]
) -> dict[str, GachaPool]:
    path = MASTER_DIRECTORY / "gacha.json"
    raw = _read_json(path)

    pools: dict[str, GachaPool] = {}

    for entry in _require(raw, "pools", path):
        pool_id = entry["pool_id"]

        rates: dict[str, dict[str, int]] = {}
        for slot_type, weights in entry["rates"].items():
            if slot_type not in {"normal", "guaranteed"}:
                raise MasterDataError(f"{pool_id}: 未対応のslot_typeです: {slot_type}")

            parsed = {}
            for rank, weight in weights.items():
                if rank not in rank_order:
                    raise MasterDataError(f"{pool_id}: 未対応のランクです: {rank}")
                parsed[rank] = int(weight)

            total = sum(parsed.values())
            if total != 1000:
                raise MasterDataError(
                    f"{pool_id}/{slot_type}: 排出率の合計が1000ではありません（{total}）"
                )

            rates[slot_type] = parsed

        if "normal" not in rates:
            raise MasterDataError(f"{pool_id}: normal の排出率がありません")

        multi_count = int(entry["multi_count"])
        guaranteed_slot = int(entry.get("guaranteed_slot", 0))
        if guaranteed_slot and not 1 <= guaranteed_slot <= multi_count:
            raise MasterDataError(f"{pool_id}: guaranteed_slot の値が範囲外です")
        if guaranteed_slot and "guaranteed" not in rates:
            warnings.append(f"{pool_id}: 保証枠の排出率が未設定です")

        fallback = entry.get("missing_rank_fallback")
        if fallback is not None and fallback not in rank_order:
            raise MasterDataError(
                f"{pool_id}: missing_rank_fallback が未対応のランクです: {fallback}"
            )

        pools[pool_id] = GachaPool(
            pool_id=pool_id,
            name=entry["name"],
            single_cost=int(entry["single_cost"]),
            multi_cost=int(entry["multi_cost"]),
            multi_count=multi_count,
            guaranteed_slot=guaranteed_slot,
            is_public=bool(entry.get("is_public", False)),
            rates=rates,
            missing_rank_fallback=fallback,
        )

    if not pools:
        raise MasterDataError("ガチャ設定が1件もありません")

    return pools


_cache: MasterData | None = None


def load_master_data(*, reload: bool = False) -> MasterData:
    """マスターデータを読み込む。2回目以降はキャッシュを返す。"""

    global _cache

    if _cache is not None and not reload:
        return _cache

    warnings: list[str] = []

    guild, familiar, battle = _load_balance(warnings)
    skills = _load_skills()
    familiars = _load_familiars(skills, familiar.rank_order, warnings)
    gacha_pools = _load_gacha(familiar.rank_order, warnings)

    master = MasterData(
        guild=guild,
        familiar=familiar,
        battle=battle,
        familiars=familiars,
        skills=skills,
        gacha_pools=gacha_pools,
        warnings=tuple(warnings),
    )

    missing = master.missing_ranks()
    if missing:
        fallback = master.gacha_pools.get("standard")
        target = fallback.missing_rank_fallback if fallback else None

        if target:
            logger.info(
                "使い魔マスター未登録のランク: %s。排出率は%sランクへ加算します。",
                "・".join(missing),
                target,
            )
        else:
            logger.warning(
                "使い魔マスター未登録のランクがあります: %s（該当ランクは抽選から除外します）",
                "・".join(missing),
            )

    gender_unset = sum(1 for item in familiars.values() if item.gender is None)
    if gender_unset:
        logger.warning(
            "性別未登録の使い魔が%d体あります。異性条件のスキルは成立しません。",
            gender_unset,
        )

    logger.info(
        "マスターデータ読込完了: 使い魔%d体 / スキル%d件 / ガチャ%d件",
        len(familiars),
        len(skills),
        len(gacha_pools),
    )

    _cache = master
    return master


def familiar_image_path(familiar_id: str) -> Path | None:
    """使い魔画像のファイルパスを返す。正式画像がなければ共通画像を返す。"""

    # config を import すると環境変数必須のためテストが動かせない。既定値を直接使う。
    directory = PROJECT_ROOT / "assets" / "familiars"

    master = load_master_data()
    familiar = master.get_familiar(familiar_id)
    filename = (familiar.image_file if familiar else None) or f"{familiar_id}.png"

    path = directory / filename
    if path.is_file():
        return path

    default_path = directory / "default.png"
    if default_path.is_file():
        return default_path

    return None
