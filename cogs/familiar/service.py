"""使い魔パネルの計算と表示の組み立て（GAME_SPEC 10節）。

Discordの操作部品は ``views.py``、SQLは ``database/familiar.py`` の責務です。
このモジュールはガチャの抽選、Embedの組み立て、売却額の計算だけを行います。

料金・確率・レベル上限・売却基準価格などの数値はコードへ書かず、
``game.master_data.load_master_data()`` から取得します。
"""

from __future__ import annotations

import logging
import random

from collections import Counter
from typing import Any, Iterable

import discord

from cogs import game_shared
from cogs.game_shared import item_line
from database.familiar import grant_complete_reward
from game.battle_embed import thumbnail_file
from game.master_data import GachaPool, load_master_data
from game.models import GENDER_FEMALE, GENDER_MALE, GENDER_NONE


logger = logging.getLogger(__name__)


# ==================================================
# 定数
# ==================================================
# 排出率は千分率の整数で管理する（data/master/gacha.json と同じ単位）。
PERMILLE_TOTAL = 1000

# 公開ガチャのプールID（10.2節では通常ガチャ1種類のみ）
DEFAULT_POOL_ID = "standard"

# セレクトの選択肢上限（Discordの仕様）
PAGE_SIZE = 25

# 排出率テーブルの種類
SLOT_NORMAL = "normal"
SLOT_GUARANTEED = "guaranteed"

# 性別の表示（未登録は推測せず「未登録」と表示する）
GENDER_LABELS = {
    GENDER_MALE: "男性",
    GENDER_FEMALE: "女性",
    GENDER_NONE: "なし",
}
GENDER_UNSET_LABEL = "未登録"


class GachaUnavailableError(RuntimeError):
    """抽選できる使い魔が1体も登録されていない場合に送出する。"""


# ==================================================
# 乱数（テストから差し替えられるようにモジュール変数で保持する）
# ==================================================
_rng = random.Random()


def set_random(rng: random.Random) -> None:
    """抽選に使う乱数生成器を差し替える。自動テスト用。"""

    global _rng
    _rng = rng


def get_random() -> random.Random:
    """現在の乱数生成器を返す。"""

    return _rng


# ==================================================
# ガチャ設定の参照
# ==================================================
def get_pool(pool_id: str = DEFAULT_POOL_ID) -> GachaPool | None:
    """ガチャプールを返す。設定が無ければ ``None``。"""

    master = load_master_data()
    return master.gacha_pools.get(pool_id)


def gacha_plan(pool: GachaPool, *, multi: bool) -> tuple[int, int]:
    """実行回数と料金を返す。10回実行に割引は無い（10.2節）。"""

    if multi:
        return pool.multi_count, pool.multi_cost

    return 1, pool.single_cost


# ==================================================
# 抽選（10.2節・10.6節）
# ==================================================
def build_rank_table(pool: GachaPool, slot_type: str = SLOT_NORMAL) -> list[tuple[str, int]]:
    """抽選に使うランク別の千分率テーブルを作る。

    使い魔マスターが未登録のランク（現時点ではCランク）は抽選対象から外します。
    外した分の確率は、``missing_rank_fallback`` が設定されていればそのランクへ
    まとめて寄せ、未設定なら残りランクへ元の比率のまま按分します。
    戻り値 ``[(ランク, 千分率), ...]`` の合計は必ず ``PERMILLE_TOTAL`` です。
    登録済みランクが1つも無い場合は空リストを返します。
    """

    master = load_master_data()

    weights = pool.rates.get(slot_type) or pool.rates.get(SLOT_NORMAL) or {}
    missing = set(master.missing_ranks(pool.pool_id))

    # 表示と抽選の順序を安定させるため、マスターのランク順（弱い順）に並べる。
    rank_order = {rank: index for index, rank in enumerate(master.familiar.rank_order)}
    ordered = sorted(
        weights.items(),
        key=lambda item: rank_order.get(item[0], len(rank_order)),
    )

    def is_available(rank: str) -> bool:
        return rank not in missing and bool(master.gacha_familiars_by_rank(rank))

    available = [
        (rank, int(weight))
        for rank, weight in ordered
        if int(weight) > 0 and is_available(rank)
    ]

    if not available:
        return []

    total = sum(weight for _, weight in available)
    if total == PERMILLE_TOTAL:
        return list(available)

    # 寄せ先が指定されていれば、未登録ランクの確率をそこへまとめて加算する。
    fallback = pool.missing_rank_fallback
    if fallback and is_available(fallback):
        shortfall = PERMILLE_TOTAL - total
        return [
            (rank, weight + shortfall if rank == fallback else weight)
            for rank, weight in available
        ]

    # 寄せ先が使えない場合は、元の比率を保ったまま合計を PERMILLE_TOTAL へ揃える。
    scaled: list[list[Any]] = []
    remainders: list[tuple[float, int]] = []
    assigned = 0

    for index, (rank, weight) in enumerate(available):
        exact = weight * PERMILLE_TOTAL / total
        floor_value = int(exact)

        scaled.append([rank, floor_value])
        remainders.append((exact - floor_value, index))
        assigned += floor_value

    # 端数が大きい順（同数なら元の並び順）に1ずつ配る。
    remainders.sort(key=lambda item: (-item[0], item[1]))
    for _, index in remainders[: PERMILLE_TOTAL - assigned]:
        scaled[index][1] += 1

    return [(rank, int(weight)) for rank, weight in scaled]


def rank_table_notice(pool: GachaPool) -> str | None:
    """排出率を調整した場合に結果Embedのfooterへ出す注記を返す。"""

    master = load_master_data()

    missing = master.missing_ranks(pool.pool_id)
    if not missing:
        return None

    missing_text = "・".join(missing)
    fallback = pool.missing_rank_fallback

    if fallback and master.gacha_familiars_by_rank(fallback):
        return f"{missing_text}ランク未登録のため、その排出率を{fallback}ランクへ加算しています"

    return f"{missing_text}ランク未登録のため排出率を調整しています"


def _pick_rank(table: list[tuple[str, int]]) -> str:
    """千分率テーブルからランクを1つ選ぶ。"""

    roll = _rng.randint(1, PERMILLE_TOTAL)

    upto = 0
    for rank, weight in table:
        upto += weight
        if roll <= upto:
            return rank

    # 合計は必ず PERMILLE_TOTAL だが、丸め誤差の保険として最後のランクを返す。
    return table[-1][0]


def draw_results(pool: GachaPool, count: int) -> list[tuple[str, str]]:
    """``count`` 回分の抽選結果 ``[(ランク, 使い魔ID), ...]`` を返す。

    同じランク内の使い魔は均等な確率で選びます。10回実行の
    ``guaranteed_slot`` 枠目だけ保証枠の確率表を使います。
    """

    master = load_master_data()

    normal_table = build_rank_table(pool, SLOT_NORMAL)
    if not normal_table:
        raise GachaUnavailableError("抽選できる使い魔が1体も登録されていません")

    guaranteed_table: list[tuple[str, int]] = []
    if pool.guaranteed_slot and SLOT_GUARANTEED in pool.rates:
        guaranteed_table = build_rank_table(pool, SLOT_GUARANTEED)

    results: list[tuple[str, str]] = []

    for slot in range(1, count + 1):
        table = normal_table

        # 保証枠は10回実行のときだけ適用する。
        if (
            guaranteed_table
            and count == pool.multi_count
            and slot == pool.guaranteed_slot
        ):
            table = guaranteed_table

        rank = _pick_rank(table)

        candidates = master.gacha_familiars_by_rank(rank)
        if not candidates:
            raise GachaUnavailableError(f"ランク{rank}の使い魔が登録されていません")

        familiar = _rng.choice(candidates)
        results.append((rank, familiar.familiar_id))

    return results


# ==================================================
# 価格・候補の計算
# ==================================================
def sell_price(familiar_id: str, level: int) -> int:
    """売却額を返す（10.2節：``ランク別基準価格 × レベル``）。"""

    master = load_master_data()

    familiar = master.get_familiar(familiar_id)
    if familiar is None:
        return 0

    return master.sell_price(familiar.rank, int(level))


def fusion_cost(familiar_id: str, material_count: int) -> int:
    """合成にかかるcoinを返す（10.2節）。"""

    master = load_master_data()

    familiar = master.get_familiar(familiar_id)
    if familiar is None:
        return 0

    return master.fusion_cost(familiar.rank, material_count)


def fusion_cost_per_material(familiar_id: str) -> int:
    """素材1体あたりの合成費用を返す。パネルの案内に使う。"""

    return fusion_cost(familiar_id, 1)


def fusable_bases(
    owned: list[dict[str, Any]],
    locked_instance_ids: Iterable[int],
) -> list[dict[str, Any]]:
    """合成のベースに選べる個体だけを絞り込む。

    素材にできる同種の個体が別に存在し、最大レベル未満で、編成ロック中・
    進行中バトルで使用中でない個体だけを返します。レベルの高い個体から
    並べるので、そのまま選択肢にすると育成済みの個体が上に来ます。
    """

    master = load_master_data()
    locked = set(locked_instance_ids)

    usable = [row for row in owned if int(row["instance_id"]) not in locked]
    counts = Counter(row["familiar_id"] for row in usable)

    return [
        row
        for row in usable
        if counts[row["familiar_id"]] >= 2
        and int(row["level"]) < master.familiar.max_level
    ]


def fusable_count(
    owned: list[dict[str, Any]],
    locked_instance_ids: Iterable[int],
    *,
    base: dict[str, Any],
) -> int:
    """``base`` を土台にしたときに素材にできる体数を返す。"""

    locked = set(locked_instance_ids)
    base_id = int(base["instance_id"])

    return sum(
        1
        for row in owned
        if row["familiar_id"] == base["familiar_id"]
        and int(row["instance_id"]) != base_id
        and int(row["instance_id"]) not in locked
    )


def max_fusion_count(
    owned: list[dict[str, Any]],
    locked_instance_ids: Iterable[int],
    *,
    base: dict[str, Any],
) -> int:
    """一度に合成できる最大体数（素材数とレベル上限の小さい方）を返す。"""

    master = load_master_data()

    room = master.familiar.max_level - int(base["level"])
    return max(0, min(room, fusable_count(owned, locked_instance_ids, base=base)))


def exclude_locked(
    rows: list[dict[str, Any]],
    locked_instance_ids: Iterable[int],
) -> list[dict[str, Any]]:
    """編成ロック中・進行中バトルで使用中の個体を除外する。"""

    locked = set(locked_instance_ids)
    return [row for row in rows if int(row["instance_id"]) not in locked]


# ==================================================
# 表示の共通処理
# Embedはfieldを使わず、本文へ「【項目】結果」の形で並べる
# ==================================================
def gender_label(gender: str | None) -> str:
    """性別を表示用の日本語へ変換する。未登録は推測せず「未登録」と返す。"""

    if not gender:
        return GENDER_UNSET_LABEL

    return GENDER_LABELS.get(gender, GENDER_UNSET_LABEL)


def familiar_name(familiar_id: str) -> str:
    """使い魔名を返す。マスターに無い場合はIDをそのまま返す。"""

    master = load_master_data()

    familiar = master.get_familiar(familiar_id)
    return familiar.name if familiar is not None else familiar_id


def familiar_rank(familiar_id: str) -> str:
    """使い魔のランクを返す。マスターに無い場合は ``"?"``。"""

    master = load_master_data()

    familiar = master.get_familiar(familiar_id)
    return familiar.rank if familiar is not None else "?"


def instance_title(row: dict[str, Any]) -> str:
    """「使い魔名 Lv.n」の表記を作る。"""

    return f"{familiar_name(row['familiar_id'])} Lv.{int(row['level'])}"


def stat_lines(familiar_id: str, level: int) -> list[str]:
    """HP・ATK・SPDを縦に並べた行を返す。"""

    master = load_master_data()

    stats = master.level_stats(familiar_id, level)
    if stats is None:
        return []

    return [
        item_line("HP", stats.max_hp),
        item_line("ATK", stats.atk),
        item_line("SPD", stats.speed),
    ]


def skill_lines(familiar_id: str, *, with_description: bool = True) -> list[str]:
    """パッシブ・アクティブスキルを種類ごとに並べた行を返す。"""

    master = load_master_data()

    lines: list[str] = []

    for label, skills in (
        ("パッシブ", master.passive_skills_of(familiar_id)),
        ("アクティブ", master.active_skills_of(familiar_id)),
    ):
        if not skills:
            lines.append(item_line(label, "なし"))
            continue

        lines.append(item_line(label, "／".join(skill.name for skill in skills)))

        if with_description:
            lines.extend(f"-# {skill.description}" for skill in skills)

    return lines


# ==================================================
# 所有使い魔のまとめ表示
# 同じ使い魔を何度も並べず「×n」でまとめる
# ==================================================
def group_instances(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """所有使い魔を「同じ種類・同じレベル」でまとめる。

    戻り値は ``{"familiar_id", "level", "count", "instance_id", "instance_ids"}``
    の一覧で、強いランク・高いレベルの順に並びます。``instance_id`` は代表
    個体（最も小さいID）です。
    """

    master = load_master_data()
    rank_order = list(master.familiar.rank_order)

    groups: dict[tuple[str, int], dict[str, Any]] = {}

    for row in rows:
        familiar_id = str(row["familiar_id"])
        level = int(row["level"])
        key = (familiar_id, level)

        group = groups.get(key)
        if group is None:
            groups[key] = {
                "familiar_id": familiar_id,
                "level": level,
                "count": 1,
                "instance_ids": [int(row["instance_id"])],
            }
            continue

        group["count"] += 1
        group["instance_ids"].append(int(row["instance_id"]))

    ordered = sorted(
        groups.values(),
        key=lambda group: (
            -_rank_strength(rank_order, familiar_rank(group["familiar_id"])),
            group["familiar_id"],
            -group["level"],
        ),
    )

    for group in ordered:
        group["instance_ids"].sort()
        group["instance_id"] = group["instance_ids"][0]

    return ordered


def group_title(group: dict[str, Any]) -> str:
    """「使い魔名 Lv.n ×3」の表記を作る。1体のときは「×n」を付けない。"""

    text = f"{familiar_name(group['familiar_id'])} Lv.{group['level']}"
    if int(group["count"]) > 1:
        text = f"{text} ×{group['count']}"

    return text


def build_group_options(
    groups: list[dict[str, Any]],
) -> list[discord.SelectOption]:
    """まとめた所有使い魔をセレクトの選択肢へ変換する。"""

    options: list[discord.SelectOption] = []

    for group in groups:
        rank = familiar_rank(group["familiar_id"])

        options.append(
            discord.SelectOption(
                label=group_title(group)[:100],
                description=f"{game_shared.rank_label(rank)}／{group['count']}体所有"[:100],
                value=str(group["instance_id"]),
            )
        )

    return options


def build_instance_options(rows: list[dict[str, Any]]) -> list[discord.SelectOption]:
    """所有使い魔をセレクトの選択肢へ変換する（個体ごとに区別する）。"""

    options: list[discord.SelectOption] = []

    for row in rows:
        rank = familiar_rank(row["familiar_id"])

        options.append(
            discord.SelectOption(
                label=instance_title(row)[:100],
                description=(
                    f"{game_shared.rank_label(rank)}／個体ID {row['instance_id']}"
                )[:100],
                value=str(row["instance_id"]),
            )
        )

    return options


def build_fusion_count_options(
    base: dict[str, Any],
    maximum: int,
) -> list[discord.SelectOption]:
    """合成する体数の選択肢を作る。

    選ぶ前に「かかるcoin」と「変化後の能力値」が分かるようにします。
    """

    master = load_master_data()

    familiar_id = str(base["familiar_id"])
    before_level = int(base["level"])
    before = master.level_stats(familiar_id, before_level)

    options: list[discord.SelectOption] = []

    for count in range(1, min(maximum, PAGE_SIZE) + 1):
        after_level = before_level + count
        after = master.level_stats(familiar_id, after_level)
        cost = fusion_cost(familiar_id, count)

        label = f"{count}体　{game_shared.format_coin(cost)}"
        description = f"Lv.{before_level}→Lv.{after_level}"
        if before is not None and after is not None:
            description = (
                f"{description}　HP {before.max_hp}→{after.max_hp}"
                f"　ATK {before.atk}→{after.atk}"
            )

        options.append(
            discord.SelectOption(
                label=label[:100],
                description=description[:100],
                value=str(count),
            )
        )

    return options


def guaranteed_floor_rank(pool: GachaPool) -> str:
    """保証枠で確定する最低ランクを返す。"""

    master = load_master_data()

    table = build_rank_table(pool, SLOT_GUARANTEED)
    if not table:
        return ""

    rank_order = list(master.familiar.rank_order)
    return min(
        (rank for rank, _ in table),
        key=lambda rank: _rank_strength(rank_order, rank),
    )


def build_count_options(
    maximum: int,
    *,
    unit_price: int | None = None,
) -> list[discord.SelectOption]:
    """「何体にするか」を選ぶ選択肢を1〜``maximum`` で作る。"""

    options: list[discord.SelectOption] = []

    for count in range(1, min(maximum, PAGE_SIZE) + 1):
        description = None
        if unit_price is not None:
            description = f"受取額 {game_shared.format_coin(unit_price * count)}"

        options.append(
            discord.SelectOption(
                label=f"{count}体",
                description=description,
                value=str(count),
            )
        )

    return options


def rank_color(rank: str) -> int:
    """ランク色を返す。未知のランクは白。"""

    return game_shared.RANK_COLORS.get(rank, 0xFFFFF0)


def _rank_strength(rank_order: list[str], rank: str) -> int:
    """ランクの強さ順を返す。マスターに無いランクは最弱扱いにする。"""

    try:
        return rank_order.index(rank)
    except ValueError:
        return -1


def format_rate(permille: int) -> str:
    """千分率を百分率の表示へ変換する。"""

    return f"{permille / 10:.1f}%"


def format_each_rate(permille: int, count: int) -> str:
    """ランク内で均等配分したときの、1体あたりの排出率を表示用に整える。"""

    if count <= 0:
        return "—"

    return f"{permille / 10 / count:.2f}%"


def rate_lines(pool: GachaPool, slot_type: str = SLOT_NORMAL) -> list[str]:
    """排出率を「【ランク】確率」の行で返す（絵文字は付けない）。"""

    return [
        item_line(rank, format_rate(permille))
        for rank, permille in reversed(build_rank_table(pool, slot_type))
    ]


# ==================================================
# Embedの組み立て
# ==================================================
def build_rate_list_embed(pool: GachaPool) -> discord.Embed:
    """排出される使い魔の一覧をランクごとにまとめる。

    同じランク内の使い魔は均等に排出されるため（10.6節）、ランクの排出率と
    1体あたりの排出率の両方を表示します。
    """

    master = load_master_data()

    lines = [
        f"**{pool.name}** で入手できる使い魔です。",
        "-# 同じランク内の使い魔は均等に排出されます。",
        "",
    ]

    # 表示順は使い魔マスターの登録順（docs/BATTLE_RULES.md の掲載順）に合わせる
    document_order = {
        familiar_id: index for index, familiar_id in enumerate(master.familiars)
    }

    table = build_rank_table(pool, SLOT_NORMAL)
    listed = 0

    for rank, permille in reversed(table):
        familiars = sorted(
            master.gacha_familiars_by_rank(rank),
            key=lambda familiar: document_order.get(familiar.familiar_id, 0),
        )
        if not familiars:
            continue

        listed += 1
        names = "、".join(familiar.name for familiar in familiars)
        if len(names) > 500:
            names = names[:500] + "…"

        lines.append(
            item_line(
                rank,
                f"{format_rate(permille)}"
                f"（{len(familiars)}体・各{format_each_rate(permille, len(familiars))}）",
            )
        )
        lines.append(f"-# {names}")

    if pool.guaranteed_slot:
        guaranteed_table = build_rank_table(pool, SLOT_GUARANTEED)
        if guaranteed_table:
            lines.append("")
            lines.append(
                item_line(
                    f"{pool.multi_count}連{pool.guaranteed_slot}枠目",
                    " ／ ".join(
                        f"{rank} {format_rate(permille)}"
                        for rank, permille in reversed(guaranteed_table)
                    ),
                )
            )

    embed = discord.Embed(
        title="排出使い魔一覧",
        description="\n".join(lines) if listed else "排出できる使い魔が登録されていません。",
        color=game_shared.RANK_COLORS.get("S", 0xFEE75C),
    )

    notice = rank_table_notice(pool)
    if notice:
        embed.set_footer(text=notice)

    return embed, listed > 0


def build_gacha_confirm_embed(pool: GachaPool, *, count: int, cost: int) -> discord.Embed:
    """実行前の確認画面（料金・回数・排出率）を作る。"""

    lines = [
        item_line("ガチャ", pool.name),
        item_line("回数", f"{count}回"),
        item_line("料金", game_shared.format_coin(cost)),
        "",
        *rate_lines(pool, SLOT_NORMAL),
    ]

    if count == pool.multi_count and pool.guaranteed_slot:
        guaranteed = rate_lines(pool, SLOT_GUARANTEED)
        if guaranteed:
            lines.append("")
            lines.append(f"**{pool.guaranteed_slot}枠目（保証枠）**")
            lines.extend(guaranteed)

    embed = discord.Embed(
        title="ガチャ確認",
        description="\n".join(lines),
        color=game_shared.RANK_COLORS.get("S", 0xFEE75C),
    )

    notice = rank_table_notice(pool)
    if notice:
        embed.set_footer(text=notice)

    return embed


def build_gacha_result_embed(
    pool: GachaPool,
    instances: list[dict[str, Any]],
    *,
    count: int,
    cost: int,
) -> discord.Embed:
    """ガチャ結果を1枚のEmbedへまとめる。

    使い魔名だけでなく、その時点の能力値とスキル（パッシブ・アクティブ）も
    表示します。同じ使い魔が複数出た場合は「×n」でまとめ、1種類だけの
    ときはスキルの説明文まで載せます。
    """

    master = load_master_data()

    rank_order = list(master.familiar.rank_order)
    ranks = {
        (str(instance["familiar_id"]), int(instance.get("level", master.familiar.min_level))): str(
            instance["rank"]
        )
        for instance in instances
    }

    groups = group_instances(
        [
            {
                "instance_id": instance.get("instance_id", index),
                "familiar_id": instance["familiar_id"],
                "level": instance.get("level", master.familiar.min_level),
            }
            for index, instance in enumerate(instances)
        ]
    )
    single = len(groups) == 1

    lines: list[str] = []
    best_rank: str | None = None

    for index, group in enumerate(groups):
        familiar_id = str(group["familiar_id"])
        level = int(group["level"])
        rank = ranks.get((familiar_id, level), familiar_rank(familiar_id))

        if index > 0:
            lines.append("")

        heading = f"{game_shared.rank_label(rank)} {familiar_name(familiar_id)} Lv.{level}"
        if int(group["count"]) > 1:
            heading = f"{heading} ×{group['count']}"

        lines.append(f"**{heading}**")
        lines.extend(stat_lines(familiar_id, level))
        lines.extend(skill_lines(familiar_id, with_description=single))

        if best_rank is None or _rank_strength(rank_order, rank) > _rank_strength(
            rank_order, best_rank
        ):
            best_rank = rank

    counts = Counter(str(instance["rank"]) for instance in instances)
    summary = "／".join(
        f"{game_shared.rank_label(rank)} {counts[rank]}体"
        for rank in reversed(rank_order)
        if counts.get(rank)
    )

    lines.append("")
    lines.append(item_line("内訳", summary or "—"))
    lines.append(
        item_line("消費coin", f"{game_shared.format_coin(cost)}（{count}回）")
    )

    description = "\n".join(lines) if instances else "結果がありません。"
    if len(description) > 4000:
        description = description[:4000] + "\n-# 表示を省略しました。"

    embed = discord.Embed(
        title="ガチャ結果",
        description=description,
        color=rank_color(best_rank or "C"),
    )

    notice = rank_table_notice(pool)
    if notice:
        embed.set_footer(text=notice)

    return embed


def check_complete_rewards(user_id: int) -> list[dict[str, Any]]:
    """コンプリート報酬の条件を満たしていれば解放する（BATTLE_RULES.md 11節）。

    解放した使い魔の一覧を返します。ガチャの直後に呼びます。
    """

    master = load_master_data()

    rewards = master.complete_reward_familiars()
    if not rewards:
        return []

    reward_ids = {familiar.familiar_id for familiar in rewards}
    required = [
        familiar_id
        for familiar_id, familiar in master.familiars.items()
        if familiar.enabled and familiar_id not in reward_ids
    ]

    granted: list[dict[str, Any]] = []

    for familiar in rewards:
        outcome = grant_complete_reward(
            user_id,
            reward_familiar_id=familiar.familiar_id,
            required_familiar_ids=required,
            initial_level=master.familiar.min_level,
        )
        if outcome.get("granted"):
            granted.append(outcome)

    return granted


def build_complete_reward_embed(granted: list[dict[str, Any]]) -> discord.Embed:
    """コンプリート報酬の解放を知らせるEmbedを作る。"""

    lines: list[str] = []

    for item in granted:
        familiar_id = str(item["familiar_id"])
        level = int(item["level"])
        rank = familiar_rank(familiar_id)

        lines.append(
            item_line(
                "解放",
                f"{game_shared.rank_label(rank)} {familiar_name(familiar_id)}"
                f" Lv.{level}",
            )
        )
        lines.extend(stat_lines(familiar_id, level))
        lines.extend(skill_lines(familiar_id))

    return discord.Embed(
        title="コンプリート報酬",
        description="\n".join(
            ["**すべての使い魔を集めました。**", "", *lines]
        ),
        color=game_shared.RANK_COLORS.get("S", 0xFEE75C),
    )


def top_rank(pool: GachaPool | None = None) -> str:
    """お祝い表示の対象にする最上位ランクを返す。"""

    master = load_master_data()
    order = list(master.familiar.rank_order)

    return order[-1] if order else "S"


def celebrated_instances(instances: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """お祝い表示の対象になった使い魔（最上位ランク）を返す。"""

    target = top_rank()

    return [
        instance
        for instance in instances
        if str(instance.get("rank")) == target
    ]


def build_celebration_embeds(
    instances: list[dict[str, Any]],
) -> list[tuple[discord.Embed, discord.File | None]]:
    """最上位ランクを引いたときのお祝いEmbedを作る（10.2節）。

    使い魔の画像をアイコンとして添え、能力値とスキルも見せます。同じ使い魔が
    複数出た場合はまとめて1枚にします。
    """

    master = load_master_data()

    found: list[tuple[discord.Embed, discord.File | None]] = []
    counts = Counter(
        (str(instance["familiar_id"]), int(instance.get("level", master.familiar.min_level)))
        for instance in celebrated_instances(instances)
    )

    for (familiar_id, level), count in counts.items():
        rank = familiar_rank(familiar_id)

        lines = [
            f"**{familiar_name(familiar_id)}** を手に入れました！",
            "",
            item_line("ランク", game_shared.rank_label(rank)),
            item_line("レベル", f"Lv.{level}"),
        ]

        if count > 1:
            lines.append(item_line("体数", f"{count}体"))

        lines.extend(stat_lines(familiar_id, level))
        lines.append("")
        lines.extend(skill_lines(familiar_id))

        embed = discord.Embed(
            title="🎉 おめでとうございます！",
            description="\n".join(lines),
            color=rank_color(rank),
        )

        icon = thumbnail_file(familiar_id)
        title = f"{game_shared.rank_label(rank)} {familiar_name(familiar_id)} Lv.{level}"

        if icon is not None:
            embed.set_author(name=title, icon_url=f"attachment://{icon.filename}")
        else:
            embed.set_author(name=title)

        embed.set_footer(text=f"最高ランク（{rank}）の使い魔です")
        found.append((embed, icon))

    return found


def build_owned_list_embed(rows: list[dict[str, Any]]) -> discord.Embed:
    """所有使い魔の一覧を、同じ使い魔をまとめた形で表示する。"""

    groups = group_instances(rows)

    lines = [item_line("所有数", f"{len(rows)}体（{len(groups)}種類）"), ""]
    shown = 0
    hidden = 0

    for group in groups:
        rank = familiar_rank(group["familiar_id"])
        line = item_line(
            familiar_name(group["familiar_id"]),
            f"{game_shared.rank_label(rank)} Lv.{group['level']}"
            + (f" ×{group['count']}" if int(group["count"]) > 1 else ""),
        )

        # 本文の上限に収まる範囲だけ並べ、残りは体数だけ知らせる。
        if shown >= 40 or sum(len(item) + 1 for item in lines) + len(line) > 3600:
            hidden += int(group["count"])
            continue

        lines.append(line)
        shown += 1

    if hidden:
        lines.append(f"-# ほか +{hidden}体")

    return discord.Embed(
        title="使い魔一覧",
        description="\n".join(lines),
        color=game_shared.RANK_COLORS.get("B", 0xBEDBFF),
    )


def build_familiar_detail_embed(
    row: dict[str, Any],
    *,
    count: int = 1,
) -> tuple[discord.Embed, discord.File | None]:
    """所有使い魔1体の詳細Embedと、添付する画像ファイルを作る。

    能力値だけでなく使い魔の姿も確認できるよう、画像は小さなアイコンではなく
    Embedの大きな画像として添えます。``discord.File`` は使い回せないため、
    表示のたびに開き直します。
    """

    master = load_master_data()

    familiar_id = str(row["familiar_id"])
    level = int(row["level"])
    familiar = master.get_familiar(familiar_id)

    if familiar is None:
        embed = discord.Embed(
            title=f"{familiar_id} Lv.{level}",
            description="この使い魔のマスターデータが見つかりません。運営へお問い合わせください。",
            color=rank_color("?"),
        )
        return embed, None

    lines = [
        item_line("ランク", game_shared.rank_label(familiar.rank)),
        item_line("レベル", f"Lv.{level}／Lv.{master.familiar.max_level}"),
        item_line("性別", gender_label(familiar.gender)),
        *stat_lines(familiar_id, level),
        item_line("COST", familiar.cost),
        item_line("所有数", f"{count}体"),
        "",
        *skill_lines(familiar_id),
    ]

    if familiar.description:
        lines.extend(["", f"-# {familiar.description}"])

    embed = discord.Embed(
        title=f"{familiar.name} Lv.{level}",
        description="\n".join(lines),
        color=rank_color(familiar.rank),
    )

    image = thumbnail_file(familiar_id)
    if image is not None:
        embed.set_image(url=f"attachment://{image.filename}")

    return embed, image


def _diff_lines(familiar_id: str, before_level: int, after_level: int) -> list[str]:
    """``HP 27→29`` の形で能力値の変化を並べる。"""

    master = load_master_data()

    before = master.level_stats(familiar_id, before_level)
    after = master.level_stats(familiar_id, after_level)

    if before is None or after is None:
        return []

    return [
        item_line("HP", f"{before.max_hp}→**{after.max_hp}**"),
        item_line("ATK", f"{before.atk}→**{after.atk}**"),
        item_line("SPD", f"{before.speed}→**{after.speed}**"),
    ]


def build_fusion_result_embed(
    familiar_id: str,
    *,
    before_level: int,
    level: int,
    material_count: int,
    cost: int = 0,
) -> tuple[discord.Embed, discord.File | None]:
    """合成成功時に、変化後の能力値を表示するEmbedを作る。"""

    master = load_master_data()

    rank = familiar_rank(familiar_id)

    lines = [
        item_line("レベル", f"Lv.{before_level}→**Lv.{level}**"),
        *_diff_lines(familiar_id, before_level, level),
        item_line("消費した素材", f"{material_count}体"),
        item_line("かかったcoin", game_shared.format_coin(cost)),
    ]

    embed = discord.Embed(
        title="合成成功",
        description="\n".join(lines),
        color=rank_color(rank),
    )

    if level >= master.familiar.max_level:
        embed.set_footer(text="最大レベルに到達しました")

    icon = thumbnail_file(familiar_id)
    title = f"{familiar_name(familiar_id)} Lv.{level}"

    if icon is not None:
        embed.set_author(name=title, icon_url=f"attachment://{icon.filename}")
    else:
        embed.set_author(name=title)

    return embed, icon


def build_sell_confirm_embed(
    row: dict[str, Any],
    *,
    count: int,
    unit_price: int,
    available: int,
) -> discord.Embed:
    """売却確認のEmbedを作る（誤操作防止のため必ず挟む）。"""

    familiar_id = str(row["familiar_id"])
    rank = familiar_rank(familiar_id)

    lines = [
        item_line("使い魔", f"{game_shared.rank_label(rank)} {instance_title(row)}"),
        item_line("売却する体数", f"{count}体（所有 {available}体）"),
        item_line("1体あたり", game_shared.format_coin(unit_price)),
        item_line("受取額", f"**{game_shared.format_coin(unit_price * count)}**"),
    ]

    embed = discord.Embed(
        title="売却確認",
        description="\n".join(lines),
        color=rank_color(rank),
    )
    embed.set_footer(text="売却した使い魔は取り消しできません")

    return embed


def build_sell_result_embed(outcome: dict[str, Any]) -> discord.Embed:
    """売却結果のEmbedを作る。"""

    sold = list(outcome.get("sold") or [])
    total = int(outcome.get("total", 0))

    first = sold[0] if sold else None
    familiar_id = str(first["familiar_id"]) if first else ""
    rank = familiar_rank(familiar_id) if first else "?"

    lines = [
        item_line(
            "売却した使い魔",
            f"{game_shared.rank_label(rank)} {familiar_name(familiar_id)} Lv.{first['level']}"
            if first
            else "—",
        ),
        item_line("体数", f"{len(sold)}体"),
        item_line("受取額", f"**{game_shared.format_coin(total)}**"),
    ]

    return discord.Embed(
        title="売却完了",
        description="\n".join(lines),
        color=rank_color(rank),
    )


__all__ = [
    "DEFAULT_POOL_ID",
    "GachaUnavailableError",
    "PERMILLE_TOTAL",
    "SLOT_GUARANTEED",
    "SLOT_NORMAL",
    "build_celebration_embeds",
    "celebrated_instances",
    "build_complete_reward_embed",
    "check_complete_rewards",
    "build_count_options",
    "build_familiar_detail_embed",
    "build_fusion_count_options",
    "build_fusion_result_embed",
    "build_gacha_confirm_embed",
    "build_gacha_result_embed",
    "build_group_options",
    "build_instance_options",
    "build_owned_list_embed",
    "build_rank_table",
    "build_rate_list_embed",
    "build_sell_confirm_embed",
    "build_sell_result_embed",
    "draw_results",
    "exclude_locked",
    "fusable_bases",
    "fusable_count",
    "fusion_cost",
    "fusion_cost_per_material",
    "gacha_plan",
    "guaranteed_floor_rank",
    "gender_label",
    "get_pool",
    "get_random",
    "group_instances",
    "group_title",
    "instance_title",
    "item_line",
    "max_fusion_count",
    "rank_table_notice",
    "rate_lines",
    "sell_price",
    "set_random",
    "skill_lines",
    "stat_lines",
]
