"""所有している使い魔を、セレクトの選択肢へ整える純粋関数。

DBもDiscordの往復も持たないため、並び順と見出しの文面だけを確かめられます。
"""

from __future__ import annotations

import discord

from cogs import game_shared
from game.master_data import load_master_data

from texts import battle as battle_texts


# ==================================================
# 使い魔の選択肢づくり
# ==================================================
def familiar_sort_key(familiar_id: str, level: int) -> tuple[int, int, str]:
    """ランク順 → レベル順に並べるための並び替えキーを返す。

    強いランクが先、同じランクなら高いレベルが先です。
    """

    master = load_master_data()

    familiar = master.get_familiar(familiar_id)
    order = list(master.familiar.rank_order)

    try:
        strength = order.index(familiar.rank) if familiar else -1
    except ValueError:
        strength = -1

    return (-strength, -int(level), familiar.name if familiar else familiar_id)


def familiar_option(
    instance_id: int,
    familiar_id: str,
    level: int,
    *,
    prefix: str = "",
    count: int = 1,
) -> discord.SelectOption | None:
    """所有使い魔をセレクトの選択肢へ変換する。

    ランクは絵文字（アイコン）と先頭文字の両方で示します。先頭文字があると、
    セレクトの見出しに「S〜Aランク」のような範囲を出せます。``count`` が2以上の
    ときは「×3」を付け、同じ使い魔を何度も並べません。
    """

    master = load_master_data()

    familiar = master.get_familiar(familiar_id)
    if familiar is None:
        return None

    stats = master.level_stats(familiar_id, level)
    description = (
        battle_texts.OPTION_STATS.format(
            hp=stats.max_hp,
            atk=stats.atk,
            speed=stats.speed,
            cost=familiar.cost,
        )
        if stats
        else familiar.description
    )

    label = battle_texts.OPTION_LABEL.format(
        prefix=prefix, rank=familiar.rank, name=familiar.name, level=level
    )
    if count > 1:
        label = battle_texts.OPTION_LABEL_COUNT.format(label=label, count=count)

    return discord.SelectOption(
        label=label[:100],
        description=description[:100],
        value=str(instance_id),
        emoji=game_shared.RANK_EMOJIS.get(familiar.rank),
    )


def grouped_familiar_options(
    rows: list[dict], *, prefix: str = ""
) -> list[discord.SelectOption]:
    """所有使い魔を「同じ種類・同じレベル」でまとめ、ランク順 → レベル順で返す。

    同じ使い魔が複数あっても選択肢は1つにまとめ、体数を「×3」で示します。
    どの個体を選んでも結果は同じなので、代表として最小の個体IDを使います。
    """

    groups: dict[tuple[str, int], dict] = {}

    for row in rows:
        familiar_id = str(row["familiar_id"])
        level = int(row["level"])
        instance_id = int(row["instance_id"])
        key = (familiar_id, level)

        group = groups.get(key)
        if group is None:
            groups[key] = {
                "familiar_id": familiar_id,
                "level": level,
                "instance_id": instance_id,
                "count": 1,
            }
            continue

        group["count"] += 1
        group["instance_id"] = min(group["instance_id"], instance_id)

    found: list[tuple[tuple[int, int, str], discord.SelectOption]] = []

    for group in groups.values():
        option = familiar_option(
            group["instance_id"],
            group["familiar_id"],
            group["level"],
            prefix=prefix,
            count=group["count"],
        )
        if option is not None:
            found.append(
                (familiar_sort_key(group["familiar_id"], group["level"]), option)
            )

    found.sort(key=lambda item: item[0])
    return [option for _, option in found]
