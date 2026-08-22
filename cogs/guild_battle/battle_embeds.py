"""編成確認・ランキング・各常設パネルのEmbedを組み立てる。

``*_panel_embed`` の文面は ``utils.ensure_panel_message`` が既存の投稿と突き合わせて
貼り直しの要否を決めるため、1文字でも変えると全サーバーのパネルが編集されます。
"""

from __future__ import annotations

import discord
import config

from cogs import game_shared
from database.battle import get_battle_entries, get_battle_roster
from database.familiar import get_owned_familiar
from database.guild import (
    get_guild,
    get_guild_ranking,
    get_guild_ranking_position,
    get_player_guild,
)
from game import battle_embed
from game.master_data import load_master_data

from .battle_common import (
    BATTLE_PANEL_TITLE,
    RANKING_PANEL_TITLE,
    ROSTER_PANEL_TITLE,
    familiar_display_name,
)
from texts import battle as battle_texts


# ==================================================
# Embed・パネルの組み立て
# ==================================================
def build_roster_embed(guild_id: int) -> discord.Embed:
    """11節の編成確認Embedを組み立てる（出場者1～5人・使い魔最大5体）。"""

    master = load_master_data()
    guild_row = get_guild(guild_id)
    roster = get_battle_roster(guild_id)
    entries = get_battle_entries(guild_id)

    # 出場者ごとにセット済みの使い魔をまとめる
    by_user: dict[int, list[str]] = {}
    for entry in entries:
        owned = get_owned_familiar(int(entry["instance_id"]))
        text = (
            battle_texts.FAMILIAR_LABEL.format(
                name=familiar_display_name(owned["familiar_id"]), level=owned["level"]
            )
            if owned is not None and owned["status"] == "owned"
            else battle_texts.ROSTER_NOT_OWNED
        )
        by_user.setdefault(int(entry["user_id"]), []).append(text)

    lines: list[str] = []
    ready_count = 0

    for index, member in enumerate(roster):
        mark = (
            battle_embed.SLOT_MARKS[index]
            if index < len(battle_embed.SLOT_MARKS)
            else f"{index + 1}."
        )

        user_id = int(member["user_id"])
        assigned = int(member["familiar_count"] or 0)
        familiars = by_user.get(user_id, [])

        if familiars and len(familiars) >= assigned:
            ready = battle_texts.ROSTER_READY_OK
            ready_count += 1
            familiar_text = "\n　".join(familiars)
        elif familiars:
            ready = battle_texts.ROSTER_READY_SHORT
            familiar_text = "\n　".join(familiars)
        else:
            ready = battle_texts.ROSTER_READY_NONE
            familiar_text = battle_texts.ROSTER_UNSET

        lines.append(
            battle_texts.ROSTER_EMBED_MEMBER.format(
                mark=mark,
                user_id=user_id,
                count=len(familiars),
                assigned=assigned,
                familiars=familiar_text,
                ready=ready,
            )
        )

    if not lines:
        lines.append(battle_texts.ROSTER_EMPTY)

    # 編成ロック中は操作しても弾かれるだけで理由が分からないため、状態を先に示す
    locked = bool(guild_row and guild_row["roster_locked"])
    state_text = (
        battle_texts.ROSTER_LOCKED if locked else battle_texts.ROSTER_UNLOCKED
    )

    header = [
        game_shared.item_line(battle_texts.LABEL_STATE, state_text),
        game_shared.item_line(
            battle_texts.LABEL_MEMBERS,
            battle_texts.ROSTER_MEMBER_SUMMARY.format(
                count=len(roster), max_members=master.battle.max_members
            ),
        ),
        game_shared.item_line(
            battle_texts.LABEL_FAMILIARS,
            battle_texts.ROSTER_FAMILIAR_SUMMARY.format(
                count=len(entries), max_units=master.battle.max_units
            ),
        ),
        "",
    ]

    if locked:
        header.insert(1, battle_texts.ROSTER_LOCKED_NOTE)

    embed = discord.Embed(
        title=battle_texts.ROSTER_EMBED_TITLE,
        description=("\n".join(header) + "\n\n".join(lines))[:4000],
        color=config.COLOR_BLUE,
    )
    # embed.set_footer(
    #     text=(
    #         f"{guild_row['name'] if guild_row else ''}　"
    #         f"使い魔をセット済み {ready_count}/{len(roster)}人"
    #     )
    # )
    return embed


def build_ranking_embed(user_id: int) -> discord.Embed:
    """ギルドランキングEmbedを組み立てる（26.2節）。"""

    master = load_master_data()
    ranking_balance = master.battle.ranking

    ranking = get_guild_ranking(
        ranking_balance.display_limit,
        win_points=ranking_balance.win_points,
        draw_points=ranking_balance.draw_points,
        lose_points=ranking_balance.lose_points,
    )

    if ranking:
        lines = [
            battle_texts.RANKING_LINE.format(
                rank=row["rank"],
                name=row["name"],
                points=row["points"],
                wins=row["wins"],
                losses=row["losses"],
                draws=row["draws"],
            )
            for row in ranking
        ]
        description = "\n".join(lines)[:4000]
    else:
        description = battle_texts.RANKING_EMPTY

    own_guild = get_player_guild(user_id)
    if own_guild is not None:
        position = get_guild_ranking_position(
            int(own_guild["guild_id"]),
            win_points=ranking_balance.win_points,
            draw_points=ranking_balance.draw_points,
            lose_points=ranking_balance.lose_points,
        )
        position_text = (
            battle_texts.RANKING_POSITION.format(position=position)
            if position
            else battle_texts.RANKING_NO_POSITION
        )
        description = (
            f"{description}\n\n"
            + game_shared.item_line(
                battle_texts.LABEL_YOUR_GUILD,
                battle_texts.RANKING_OWN_GUILD.format(
                    name=own_guild["name"], position=position_text
                ),
            )
        )

    embed = discord.Embed(
        title=battle_texts.RANKING_EMBED_TITLE,
        description=description[:4000],
        color=config.COLOR_GOLD,
    )
    embed.set_footer(
        text=battle_texts.RANKING_FOOTER.format(
            win=ranking_balance.win_points,
            draw=ranking_balance.draw_points,
            lose=ranking_balance.lose_points,
        )
    )

    return embed


def battle_panel_embed() -> discord.Embed:
    """ギルドマスター専用TCへ置くバトルパネル（8.2節）。"""

    master = load_master_data()

    return discord.Embed(
        title=BATTLE_PANEL_TITLE,
        description=battle_texts.BATTLE_PANEL_BODY.format(
            max_units=master.battle.max_units
        ),
        color=config.COLOR_PURPLE,
    )


def roster_panel_embed() -> discord.Embed:
    """使い魔バトルチャンネルへ置くパネル（9節）。"""

    master = load_master_data()

    return discord.Embed(
        title=ROSTER_PANEL_TITLE,
        description=battle_texts.ROSTER_PANEL_BODY.format(
            max_units=master.battle.max_units
        ),
        color=config.COLOR_BLUE,
    )


def ranking_panel_embed() -> discord.Embed:
    """ギルド受付チャンネルへ置くギルドランキングパネル。"""

    return discord.Embed(
        title=RANKING_PANEL_TITLE,
        description=battle_texts.RANKING_PANEL_BODY,
        color=config.COLOR_GOLD,
    )
