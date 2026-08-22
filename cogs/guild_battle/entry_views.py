"""出場する使い魔の追加・入替・解除と、その一覧表示（10.3節）。

ここのViewはすべて一時（ephemeral）です。利用資格の確認は、これらを開く
``views.py`` 側の入口で済ませています。
"""

from __future__ import annotations

import discord

from cogs import game_shared
from database.battle import (
    add_battle_entry,
    get_battle_entries,
    get_battle_roster,
    remove_battle_entry,
    swap_battle_entry,
)
from database.familiar import get_owned_familiar, get_owned_familiars
from database.guild import get_guild
from game.master_data import load_master_data

from .battle_common import PagedSelectView, familiar_display_name
from .familiar_options import (
    familiar_option,
    familiar_sort_key,
    grouped_familiar_options,
)
from texts import battle as battle_texts


# ==================================================
# 使い魔セット（10.3節）
# ==================================================
class RosterFamiliarAddView(PagedSelectView):
    """出場者が持ち込む使い魔を1体追加する一時View（9節）。"""

    def __init__(self, guild_id: int, options: list[discord.SelectOption]) -> None:
        super().__init__(options, placeholder=battle_texts.ENTRY_ADD_PLACEHOLDER)

        self.guild_id = guild_id

    async def on_choice(self, interaction: discord.Interaction, value: str) -> None:
        await interaction.response.defer(ephemeral=True)

        guild_row = get_guild(self.guild_id)
        if guild_row is None or guild_row["status"] != "active":
            await game_shared.respond(
                interaction, game_shared.error_message("guild_not_found")
            )
            return

        if guild_row["roster_locked"]:
            await game_shared.respond(
                interaction, game_shared.error_message("roster_locked")
            )
            return

        master = load_master_data()

        result = add_battle_entry(
            self.guild_id,
            interaction.user.id,
            int(value),
            max_units=master.battle.max_units,
            max_total_cost=master.battle.max_total_cost,
        )

        if not result["ok"]:
            await game_shared.respond(
                interaction, entry_error_message(result["error"], detail=result)
            )
            return

        owned = get_owned_familiar(int(value))
        name = (
            battle_texts.FAMILIAR_LABEL_BOLD.format(
                name=familiar_display_name(owned["familiar_id"]), level=owned["level"]
            )
            if owned
            else battle_texts.FAMILIAR_FALLBACK
        )
        total = len(get_battle_entries(self.guild_id))

        await game_shared.respond(
            interaction,
            battle_texts.ENTRY_ADDED.format(
                name=name, count=total, max_units=master.battle.max_units
            ),
        )


class RosterFamiliarRemoveView(PagedSelectView):
    """自分がセットした使い魔を1体解除する一時View。"""

    def __init__(self, guild_id: int, options: list[discord.SelectOption]) -> None:
        super().__init__(options, placeholder=battle_texts.ENTRY_REMOVE_PLACEHOLDER)

        self.guild_id = guild_id

    async def on_choice(self, interaction: discord.Interaction, value: str) -> None:
        await interaction.response.defer(ephemeral=True)

        result = remove_battle_entry(self.guild_id, interaction.user.id, int(value))
        if not result["ok"]:
            await game_shared.respond(
                interaction, entry_error_message(result["error"])
            )
            return

        master = load_master_data()
        total = len(get_battle_entries(self.guild_id))

        await game_shared.respond(
            interaction,
            battle_texts.ENTRY_REMOVED.format(
                count=total, max_units=master.battle.max_units
            ),
        )


class RosterFamiliarSwapView(PagedSelectView):
    """セット済みの1体を、別の使い魔へ入れ替える一時View（9.3節）。"""

    def __init__(
        self,
        options: list[discord.SelectOption],
        *,
        guild_id: int,
        removed_instance_id: int,
    ) -> None:
        super().__init__(options, placeholder=battle_texts.ENTRY_SWAP_PLACEHOLDER)

        self.guild_id = guild_id
        self.removed_instance_id = removed_instance_id

    async def on_choice(self, interaction: discord.Interaction, value: str) -> None:
        await interaction.response.defer(ephemeral=True)

        master = load_master_data()

        result = swap_battle_entry(
            self.guild_id,
            interaction.user.id,
            self.removed_instance_id,
            int(value),
            max_units=master.battle.max_units,
            max_total_cost=master.battle.max_total_cost,
        )

        if not result["ok"]:
            await game_shared.respond(
                interaction, entry_error_message(result["error"], detail=result)
            )
            return

        owned = get_owned_familiar(int(value))
        name = (
            battle_texts.FAMILIAR_LABEL_BOLD.format(
                name=familiar_display_name(owned["familiar_id"]), level=owned["level"]
            )
            if owned
            else battle_texts.FAMILIAR_FALLBACK
        )

        await game_shared.respond(
            interaction, battle_texts.ENTRY_SWAPPED.format(name=name)
        )


class RosterFamiliarSwapTargetView(PagedSelectView):
    """入れ替える枠を選ぶ一時View（9.3節）。"""

    def __init__(
        self, options: list[discord.SelectOption], *, guild_id: int
    ) -> None:
        super().__init__(options, placeholder=battle_texts.ENTRY_SWAP_SLOT_PLACEHOLDER)

        self.guild_id = guild_id

    async def on_choice(self, interaction: discord.Interaction, value: str) -> None:
        await interaction.response.defer(ephemeral=True)

        options = build_settable_familiar_options(self.guild_id, interaction.user.id)
        if not options:
            issues = settable_blockers(self.guild_id, interaction.user.id)
            await game_shared.respond(
                interaction,
                battle_texts.ENTRY_NO_SWAPPABLE_HEADING + "\n" + "\n".join(issues),
            )
            return

        owned = get_owned_familiar(int(value))
        current = (
            battle_texts.FAMILIAR_LABEL.format(
                name=familiar_display_name(owned["familiar_id"]), level=owned["level"]
            )
            if owned
            else battle_texts.ENTRY_SWAP_FALLBACK
        )

        await game_shared.respond(
            interaction,
            battle_texts.ENTRY_SWAP_PROMPT.format(familiar=current),
            view=RosterFamiliarSwapView(
                options, guild_id=self.guild_id, removed_instance_id=int(value)
            ),
        )


class RosterFamiliarActionView(discord.ui.View):
    """使い魔セットの追加・入れ替え・解除を選ぶ一時View（9節）。"""

    def __init__(
        self,
        *,
        guild_id: int,
        user_id: int,
        can_add: bool,
        can_remove: bool,
    ) -> None:
        super().__init__(timeout=300)

        self.guild_id = guild_id
        self.user_id = user_id
        self.add_familiar.disabled = not can_add
        self.swap_familiar.disabled = not can_remove
        self.remove_familiar.disabled = not can_remove

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await game_shared.respond(interaction, battle_texts.OWNER_ONLY)
            return False

        return True

    @discord.ui.button(
        label=battle_texts.ENTRY_BUTTON_ADD, style=discord.ButtonStyle.success
    )
    async def add_familiar(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        options = build_settable_familiar_options(self.guild_id, interaction.user.id)

        if not options:
            # 理由を具体的に伝える（ランク不足・全部セット済みなど）
            issues = settable_blockers(self.guild_id, interaction.user.id)
            await game_shared.respond(
                interaction,
                battle_texts.ENTRY_NO_SETTABLE_HEADING + "\n" + "\n".join(issues),
            )
            return

        await game_shared.respond(
            interaction,
            battle_texts.ENTRY_ADD_PROMPT,
            view=RosterFamiliarAddView(self.guild_id, options),
        )

    @discord.ui.button(
        label=battle_texts.ENTRY_BUTTON_SWAP, style=discord.ButtonStyle.primary
    )
    async def swap_familiar(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        options = build_current_entry_options(self.guild_id, interaction.user.id)

        if not options:
            await game_shared.respond(interaction, battle_texts.ENTRY_NO_SWAPPABLE)
            return

        await game_shared.respond(
            interaction,
            battle_texts.ENTRY_SWAP_SLOT_PROMPT,
            view=RosterFamiliarSwapTargetView(options, guild_id=self.guild_id),
        )

    @discord.ui.button(
        label=battle_texts.ENTRY_BUTTON_REMOVE, style=discord.ButtonStyle.secondary
    )
    async def remove_familiar(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        options = build_current_entry_options(self.guild_id, interaction.user.id)

        if not options:
            await game_shared.respond(interaction, battle_texts.ENTRY_NO_REMOVABLE)
            return

        await game_shared.respond(
            interaction,
            battle_texts.ENTRY_REMOVE_PROMPT,
            view=RosterFamiliarRemoveView(self.guild_id, options),
        )


# ==================================================
# セットできる使い魔の絞り込みと一覧表示
# ==================================================
def settable_familiars(guild_id: int, user_id: int) -> list[dict]:
    """まだセットしていない、使役できる所有使い魔を返す。"""

    master = load_master_data()

    rank_info = game_shared.get_player_rank_info(user_id)
    if rank_info is None:
        return []

    already_set = {
        int(entry["instance_id"]) for entry in get_battle_entries(guild_id)
    }

    found: list[dict] = []

    for owned in get_owned_familiars(user_id):
        if int(owned["instance_id"]) in already_set:
            continue

        familiar = master.get_familiar(owned["familiar_id"])
        if familiar is None:
            continue

        if not master.can_use_rank(
            rank_info["player_rank"],
            familiar.rank,
            is_sub_manager=rank_info["is_sub_manager"],
        ):
            continue

        found.append(dict(owned))

    return found


def settable_blockers(guild_id: int, user_id: int) -> list[str]:
    """セットできる使い魔が無い理由を、具体的に並べて返す（10.3節）。

    「セットできる使い魔がありません」だけでは、ランクが足りないのか、
    すでに全部セットしたのか、そもそも持っていないのかが分かりません。
    """

    master = load_master_data()

    rank_info = game_shared.get_player_rank_info(user_id)
    if rank_info is None:
        return [battle_texts.RANK_UNKNOWN]

    usable = master.usable_ranks(
        rank_info["player_rank"], is_sub_manager=rank_info["is_sub_manager"]
    )
    if not usable:
        return [
            battle_texts.NO_CLASS_ROLE,
            battle_texts.NO_CLASS_ROLE_HINT,
        ]

    owned = get_owned_familiars(user_id)
    if not owned:
        return [battle_texts.NO_OWNED_FAMILIAR]

    already_set = {
        int(entry["instance_id"]) for entry in get_battle_entries(guild_id)
    }
    remaining = [
        row for row in owned if int(row["instance_id"]) not in already_set
    ]

    if not remaining:
        return [
            battle_texts.ALL_ALREADY_SET,
            battle_texts.ALL_ALREADY_SET_HINT,
        ]

    # 残っているのに候補が無い＝使役できるランクが足りない
    ranks = sorted(
        {
            familiar.rank
            for row in remaining
            if (familiar := master.get_familiar(row["familiar_id"])) is not None
        }
    )
    return [
        battle_texts.RANK_TOO_LOW.format(
            player_rank=rank_info["player_rank"], ranks="・".join(ranks)
        ),
        battle_texts.RANK_USABLE_HINT.format(ranks="・".join(usable)),
    ]


def build_settable_familiar_options(
    guild_id: int, user_id: int
) -> list[discord.SelectOption]:
    """まだセットしていない、使役可能な所有使い魔の選択肢を作る。

    同じ使い魔が複数ある場合は1つにまとめ、体数を「×3」で示します。
    """

    return grouped_familiar_options(settable_familiars(guild_id, user_id))


def build_entry_overview(
    guild_id: int, *, viewer_id: int, assigned: int
) -> str:
    """出場する使い魔を、他のメンバー分も含めてまとめて表示する（9節）。"""

    master = load_master_data()

    roster = get_battle_roster(guild_id)
    entries = get_battle_entries(guild_id)
    mine = [entry for entry in entries if int(entry["user_id"]) == viewer_id]

    by_user: dict[int, list[dict]] = {}
    for entry in entries:
        by_user.setdefault(int(entry["user_id"]), []).append(entry)

    lines = [
        battle_texts.ENTRY_OVERVIEW_HEADING,
        game_shared.item_line(
            battle_texts.LABEL_GUILD_TOTAL,
            battle_texts.ENTRY_GUILD_TOTAL.format(
                count=len(entries), max_units=master.battle.max_units
            ),
        ),
        game_shared.item_line(
            battle_texts.LABEL_YOUR_SLOT,
            battle_texts.ENTRY_YOUR_SLOT.format(
                count=len(mine), assigned=assigned
            ),
        ),
        "",
    ]

    for member in roster:
        user_id = int(member["user_id"])
        count = int(member["familiar_count"] or 0)
        owned_entries = by_user.get(user_id, [])

        is_viewer = user_id == viewer_id
        mark = (
            battle_texts.ENTRY_MARK_YOU if is_viewer else battle_texts.ENTRY_MARK_OTHER
        )
        suffix = battle_texts.ENTRY_SUFFIX_YOU if is_viewer else ""
        lines.append(
            battle_texts.ENTRY_MEMBER_LINE.format(
                mark=mark,
                user_id=user_id,
                suffix=suffix,
                count=len(owned_entries),
                assigned=count,
            )
        )

        if not owned_entries:
            lines.append(battle_texts.ENTRY_UNSET)
            continue

        for entry in owned_entries:
            owned = get_owned_familiar(int(entry["instance_id"]))
            if owned is None:
                lines.append(battle_texts.ENTRY_NOT_OWNED)
                continue

            familiar_id = str(owned["familiar_id"])
            level = int(owned["level"])
            familiar = master.get_familiar(familiar_id)
            stats = master.level_stats(familiar_id, level)

            detail = (
                battle_texts.FAMILIAR_STATS.format(
                    hp=stats.max_hp, atk=stats.atk, speed=stats.speed
                )
                if stats
                else battle_texts.DASH
            )
            rank = game_shared.rank_label(familiar.rank) if familiar else "?"
            cost = familiar.cost if familiar else battle_texts.DASH

            lines.append(
                battle_texts.ENTRY_FAMILIAR_LINE.format(
                    rank=rank,
                    name=familiar_display_name(familiar_id),
                    level=level,
                    cost=cost,
                )
            )
            lines.append(battle_texts.ENTRY_DETAIL_LINE.format(detail=detail))

    total_cost = 0
    for entry in entries:
        owned = get_owned_familiar(int(entry["instance_id"]))
        familiar = master.get_familiar(owned["familiar_id"]) if owned else None
        if familiar is not None:
            total_cost += familiar.cost

    cap = master.battle.max_total_cost
    cost_text = (
        f"{total_cost}"
        if cap <= 0
        else battle_texts.ENTRY_COST_WITH_LIMIT.format(cost=total_cost, limit=cap)
    )
    if cap > 0 and total_cost > cap:
        cost_text = battle_texts.ENTRY_COST_OVER.format(cost=cost_text)

    lines.append("")
    lines.append(game_shared.item_line(battle_texts.LABEL_TOTAL_COST, cost_text))
    if cap > 0:
        lines.append(battle_texts.COST_TABLE_NOTE)
    lines.append(battle_texts.ENTRY_OVERVIEW_FOOTER)

    return "\n".join(lines)[:1900]


def build_current_entry_options(
    guild_id: int, user_id: int
) -> list[discord.SelectOption]:
    """自分がセット済みの使い魔の選択肢を、ランク順 → レベル順で作る。"""

    found: list[tuple[tuple[int, int, str], discord.SelectOption]] = []

    for entry in get_battle_entries(guild_id):
        if int(entry["user_id"]) != user_id:
            continue

        owned = get_owned_familiar(int(entry["instance_id"]))
        if owned is None:
            continue

        familiar_id = str(owned["familiar_id"])
        level = int(owned["level"])

        option = familiar_option(
            int(entry["instance_id"]),
            familiar_id,
            level,
            prefix=battle_texts.ENTRY_SLOT_PREFIX.format(slot=entry["entry_slot"]),
        )
        if option is not None:
            found.append((familiar_sort_key(familiar_id, level), option))

    found.sort(key=lambda item: item[0])
    return [option for _, option in found]


def roster_is_set(guild_id: int) -> bool:
    """出場者セットが済んでいるか（12節の前提条件）。"""

    return bool(get_battle_roster(guild_id))


def entry_error_message(
    code: str | None,
    *,
    limit: int | None = None,
    detail: dict | None = None,
) -> str:
    """使い魔セット固有のエラーを日本語へ変換する。"""

    master = load_master_data()
    detail = detail or {}

    if limit is None:
        limit = detail.get("limit")

    if code == "entries_full":
        return battle_texts.ENTRY_ERROR_FULL.format(
            max_units=master.battle.max_units
        )
    if code == "cost_over":
        current = int(detail.get("current_cost", 0))
        adding = int(detail.get("adding_cost", 0))
        cap = int(detail.get("max_total_cost", master.battle.max_total_cost))
        return battle_texts.ENTRY_ERROR_COST_OVER.format(
            current=current, adding=adding, limit=cap
        )
    if code == "member_limit":
        return battle_texts.ENTRY_ERROR_MEMBER_LIMIT.format(
            limit=limit if limit is not None else 0
        )
    if code == "already_set":
        return battle_texts.ENTRY_ERROR_ALREADY_SET
    if code == "not_set":
        return battle_texts.ENTRY_ERROR_NOT_SET

    return game_shared.error_message(code)
