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
        return rank not in missing and bool(master.familiars_by_rank(rank))

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

    if fallback and master.familiars_by_rank(fallback):
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

        candidates = master.familiars_by_rank(rank)
        if not candidates:
            raise GachaUnavailableError(f"ランク{rank}の使い魔が登録されていません")

        familiar = _rng.choice(candidates)
        results.append((rank, familiar.familiar_id))

    return results


# ==================================================
# 価格・候補の計算
# ==================================================
def sell_price(familiar_id: str, level: int) -> int:
    """売却額を返す（10.2節：``ランク別基準価格 × (レベル + 1)``）。"""

    master = load_master_data()

    familiar = master.get_familiar(familiar_id)
    if familiar is None:
        return 0

    return master.sell_price(familiar.rank, int(level))


def fusable_bases(
    owned: list[dict[str, Any]],
    locked_instance_ids: Iterable[int],
) -> list[dict[str, Any]]:
    """合成のベースに選べる個体だけを絞り込む。

    素材にできる同種の個体が別に存在し、最大レベル未満で、編成ロック中・
    進行中バトルで使用中でない個体だけを返します。
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


def exclude_locked(
    rows: list[dict[str, Any]],
    locked_instance_ids: Iterable[int],
) -> list[dict[str, Any]]:
    """編成ロック中・進行中バトルで使用中の個体を除外する。"""

    locked = set(locked_instance_ids)
    return [row for row in rows if int(row["instance_id"]) not in locked]


# ==================================================
# 表示の共通処理
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


def build_instance_options(rows: list[dict[str, Any]]) -> list[discord.SelectOption]:
    """所有使い魔をセレクトの選択肢へ変換する（同じ種類も個体ごとに区別する）。"""

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


# ==================================================
# Embedの組み立て
# ==================================================
def build_gacha_confirm_embed(pool: GachaPool, *, count: int, cost: int) -> discord.Embed:
    """実行前の確認画面（料金・回数・排出率）を作る。"""

    embed = discord.Embed(
        title="ガチャ確認",
        description=(
            f"**{pool.name}** を **{count}回** 実行します。\n"
            f"料金：**{game_shared.format_coin(cost)}**"
        ),
        color=game_shared.RANK_COLORS.get("S", 0xFEE75C),
    )

    normal_table = build_rank_table(pool, SLOT_NORMAL)
    if normal_table:
        embed.add_field(
            name="排出率",
            value="\n".join(
                f"{game_shared.rank_label(rank)}：{format_rate(permille)}"
                for rank, permille in reversed(normal_table)
            ),
            inline=True,
        )

    if count == pool.multi_count and pool.guaranteed_slot:
        guaranteed_table = build_rank_table(pool, SLOT_GUARANTEED)
        if guaranteed_table:
            embed.add_field(
                name=f"{pool.guaranteed_slot}枠目（保証枠）",
                value="\n".join(
                    f"{game_shared.rank_label(rank)}：{format_rate(permille)}"
                    for rank, permille in reversed(guaranteed_table)
                ),
                inline=True,
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
    """ガチャ結果をまとめて表示するEmbedを作る（演出はランク色と絵文字のみ）。"""

    master = load_master_data()

    lines: list[str] = []
    best_rank = None
    rank_order = list(master.familiar.rank_order)

    for index, instance in enumerate(instances, start=1):
        rank = str(instance["rank"])
        name = familiar_name(str(instance["familiar_id"]))

        lines.append(f"`{index:>2}` {game_shared.rank_label(rank)} **{name}**")

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

    embed = discord.Embed(
        title="ガチャ結果",
        description="\n".join(lines) if lines else "結果がありません。",
        color=rank_color(best_rank or "C"),
    )
    embed.add_field(name="内訳", value=summary or "—", inline=False)
    embed.add_field(
        name="消費coin",
        value=f"{game_shared.format_coin(cost)}（{count}回）",
        inline=False,
    )

    notice = rank_table_notice(pool)
    if notice:
        embed.set_footer(text=notice)

    return embed


def build_familiar_detail_embed(
    row: dict[str, Any],
) -> tuple[discord.Embed, discord.File | None]:
    """所有使い魔1体の詳細Embedと、サムネイル用の画像ファイルを作る。

    ``discord.File`` は使い回せないため、表示のたびに開き直します。
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

    stats = master.level_stats(familiar_id, level)

    embed = discord.Embed(
        title=f"{familiar.name} Lv.{level}",
        description=familiar.description or "—",
        color=rank_color(familiar.rank),
    )
    embed.add_field(name="ランク", value=game_shared.rank_label(familiar.rank), inline=True)
    embed.add_field(name="レベル", value=f"Lv.{level}／Lv.{master.familiar.max_level}", inline=True)
    embed.add_field(name="性別", value=gender_label(familiar.gender), inline=True)

    if stats is not None:
        embed.add_field(name="HP", value=str(stats.max_hp), inline=True)
        embed.add_field(name="ATK", value=str(stats.atk), inline=True)
        embed.add_field(name="SPD", value=str(stats.speed), inline=True)

    embed.add_field(name="COST", value=str(familiar.cost), inline=True)
    embed.add_field(name="個体ID", value=str(row["instance_id"]), inline=True)

    skills = master.skills_of(familiar_id)
    if skills:
        embed.add_field(
            name="スキル",
            value="\n".join(
                f"**{skill.name}**（{'アクティブ' if skill.is_active else 'パッシブ'}）\n"
                f"-# {skill.description}"
                for skill in skills
            )[:1024],
            inline=False,
        )
    else:
        embed.add_field(name="スキル", value="なし", inline=False)

    thumbnail = thumbnail_file(familiar_id)
    if thumbnail is not None:
        embed.set_thumbnail(url=f"attachment://{thumbnail.filename}")

    return embed, thumbnail


def build_fusion_result_embed(familiar_id: str, *, level: int) -> discord.Embed:
    """合成成功時に、変化後の能力値を表示するEmbedを作る。"""

    master = load_master_data()

    before = master.level_stats(familiar_id, max(level - 1, 0))
    after = master.level_stats(familiar_id, level)
    rank = familiar_rank(familiar_id)

    embed = discord.Embed(
        title="合成成功",
        description=f"**{familiar_name(familiar_id)}** が **Lv.{level}** になりました。",
        color=rank_color(rank),
    )

    if before is not None and after is not None:
        embed.add_field(name="HP", value=f"{before.max_hp} → **{after.max_hp}**", inline=True)
        embed.add_field(name="ATK", value=f"{before.atk} → **{after.atk}**", inline=True)
        embed.add_field(name="SPD", value=f"{before.speed} → **{after.speed}**", inline=True)

    if level >= master.familiar.max_level:
        embed.set_footer(text="最大レベルに到達しました")

    return embed


def build_sell_confirm_embed(row: dict[str, Any], *, price: int) -> discord.Embed:
    """売却確認のEmbedを作る（誤操作防止のため必ず挟む）。"""

    rank = familiar_rank(str(row["familiar_id"]))

    embed = discord.Embed(
        title="売却確認",
        description=(
            f"**{instance_title(row)}**（{game_shared.rank_label(rank)}）を売却します。\n"
            f"受取額：**{game_shared.format_coin(price)}**"
        ),
        color=rank_color(rank),
    )
    embed.set_footer(text="売却した使い魔は取り消しできません")

    return embed


__all__ = [
    "DEFAULT_POOL_ID",
    "GachaUnavailableError",
    "PERMILLE_TOTAL",
    "SLOT_GUARANTEED",
    "SLOT_NORMAL",
    "build_familiar_detail_embed",
    "build_fusion_result_embed",
    "build_gacha_confirm_embed",
    "build_gacha_result_embed",
    "build_instance_options",
    "build_rank_table",
    "build_sell_confirm_embed",
    "draw_results",
    "exclude_locked",
    "fusable_bases",
    "gacha_plan",
    "gender_label",
    "get_pool",
    "get_random",
    "instance_title",
    "rank_table_notice",
    "sell_price",
    "set_random",
]
