"""バトル使い魔の事前登録（9節）。

ギルドに所属していなくても、バトル中でも登録できます。常設パネルの入口は
``views.py`` の ``BattleMemberPanelView`` にあり、資格の確認もそちらで行います。
"""

from __future__ import annotations

import discord

from cogs import game_shared
from database.battle import (
    get_player_battle_familiars,
    set_player_battle_familiars,
)
from database.familiar import get_owned_familiar, get_owned_familiars
from game.master_data import load_master_data

from .battle_common import (
    EPHEMERAL_TIMEOUT,
    PagedSelectView,
    familiar_display_name,
)
from .familiar_options import familiar_option, grouped_familiar_options
from texts import battle as battle_texts


# ==================================================
# バトル使い魔の事前登録（9節）
# ギルドに所属していなくても、バトル中でも登録できる
# ==================================================
def registered_familiar_lines(user_id: int) -> list[str]:
    """事前登録した使い魔を、優先順の行にして返す。"""

    master = load_master_data()

    lines: list[str] = []
    for row in get_player_battle_familiars(user_id):
        stats = master.level_stats(str(row["familiar_id"]), int(row["level"]))
        detail = (
            battle_texts.FAMILIAR_STATS.format(
                hp=stats.max_hp, atk=stats.atk, speed=stats.speed
            )
            if stats
            else battle_texts.DASH
        )

        lines.append(
            game_shared.item_line(
                battle_texts.REGISTER_PRIORITY_LABEL.format(
                    priority=row["priority"]
                ),
                battle_texts.REGISTER_LINE.format(
                    name=familiar_display_name(row["familiar_id"]),
                    level=row["level"],
                    detail=detail,
                ),
            )
        )

    return lines


def registration_sync_notice(result: dict | None) -> list[str]:
    """事前登録の変更が出場する使い魔へどう反映されたかを知らせる（9.1節）。

    出場者に選ばれていない、または編成ロック中で反映しなかった場合は
    何も返しません。合計COST上限でセットできなかった使い魔がある場合は、
    その理由も伝えます。黙って出場体数が減ると気づけないためです。
    """

    if not result or not result.get("ok"):
        return []

    master = load_master_data()

    adopted = result.get("adopted") or []
    released = result.get("released") or []
    skipped = result.get("cost_skipped") or []

    if not adopted and not released and not skipped:
        return []

    lines = [
        battle_texts.REGISTER_SYNC_NOTE.format(
            adopted=len(adopted), released=len(released)
        )
    ]

    if skipped:
        names = "・".join(
            battle_texts.FAMILIAR_LABEL.format(
                name=familiar_display_name(owned["familiar_id"]), level=owned["level"]
            )
            for instance_id in skipped
            if (owned := get_owned_familiar(int(instance_id))) is not None
        )
        lines.append(
            battle_texts.REGISTER_COST_SKIPPED.format(
                limit=master.battle.max_total_cost,
                count=len(skipped),
                names=names or battle_texts.DASH,
            )
        )
        lines.append(battle_texts.REGISTER_COST_SKIPPED_HINT)

    return lines


def build_register_status(user_id: int, result: dict | None = None) -> str:
    """事前登録の現在の内容と操作の案内を作る。"""

    master = load_master_data()

    lines = registered_familiar_lines(user_id)
    body = lines or [battle_texts.REGISTER_EMPTY]

    return "\n".join(
        [
            game_shared.item_line(
                battle_texts.LABEL_REGISTERED,
                battle_texts.REGISTER_COUNT.format(
                    count=len(lines), max_units=master.battle.max_units
                ),
            ),
            "",
            *body,
            "",
            *registration_sync_notice(result),
            battle_texts.REGISTER_NOTE_ORDER,
            battle_texts.REGISTER_NOTE_SYNC,
            battle_texts.REGISTER_NOTE_ANYTIME,
        ]
    )


def build_registerable_options(user_id: int) -> list[discord.SelectOption]:
    """事前登録に追加できる使い魔の選択肢を作る。

    同じ使い魔が複数ある場合は1つにまとめ、体数を「×3」で示します。
    """

    registered = {
        int(row["instance_id"]) for row in get_player_battle_familiars(user_id)
    }

    return grouped_familiar_options(
        [
            dict(owned)
            for owned in get_owned_familiars(user_id)
            if int(owned["instance_id"]) not in registered
        ]
    )


def build_registered_options(user_id: int) -> list[discord.SelectOption]:
    """事前登録している使い魔の選択肢を、登録順で作る。"""

    options: list[discord.SelectOption] = []

    for row in get_player_battle_familiars(user_id):
        option = familiar_option(
            int(row["instance_id"]),
            str(row["familiar_id"]),
            int(row["level"]),
            prefix=battle_texts.REGISTER_SLOT_PREFIX.format(
                priority=row["priority"]
            ),
        )
        if option is not None:
            options.append(option)

    return options


class RegisterAddView(PagedSelectView):
    """事前登録の末尾へ使い魔を1体追加する一時View。"""

    def __init__(self, options: list[discord.SelectOption]) -> None:
        super().__init__(options, placeholder=battle_texts.REGISTER_ADD_PLACEHOLDER)

    async def on_choice(self, interaction: discord.Interaction, value: str) -> None:
        await interaction.response.defer(ephemeral=True)

        master = load_master_data()
        user_id = interaction.user.id

        current = [
            int(row["instance_id"]) for row in get_player_battle_familiars(user_id)
        ]

        if len(current) >= master.battle.max_units:
            await game_shared.respond(
                interaction,
                battle_texts.REGISTER_FULL.format(
                    max_units=master.battle.max_units
                ),
            )
            return

        if int(value) in current:
            await game_shared.respond(interaction, battle_texts.REGISTER_ALREADY)
            return

        result = set_player_battle_familiars(user_id, [*current, int(value)])
        if not result["ok"]:
            await game_shared.respond(
                interaction, game_shared.error_message(result["error"])
            )
            return

        await game_shared.respond(
            interaction,
            build_register_status(user_id, result),
            view=BattleFamiliarRegisterView(user_id),
        )


class RegisterRemoveView(PagedSelectView):
    """事前登録から1体だけ取り消す一時View（9.1節）。"""

    async def on_choice(self, interaction: discord.Interaction, value: str) -> None:
        await interaction.response.defer(ephemeral=True)

        user_id = interaction.user.id
        current = [
            int(row["instance_id"])
            for row in get_player_battle_familiars(user_id)
        ]

        target = int(value)
        if target not in current:
            await game_shared.respond(
                interaction, battle_texts.REGISTER_NOT_REGISTERED
            )
            return

        # 取り消した分を詰めて、以降の優先順を1つ繰り上げる
        current.remove(target)
        result = set_player_battle_familiars(user_id, current)

        await game_shared.respond(
            interaction,
            build_register_status(user_id, result),
            view=BattleFamiliarRegisterView(user_id),
        )


class RegisterReplaceTargetView(PagedSelectView):
    """入れ替える枠を選ぶ一時View（9.1節）。"""

    async def on_choice(self, interaction: discord.Interaction, value: str) -> None:
        await interaction.response.defer(ephemeral=True)

        user_id = interaction.user.id
        options = build_registerable_options(user_id)

        if not options:
            await game_shared.respond(
                interaction, battle_texts.REGISTER_NO_REPLACEMENT
            )
            return

        current = get_player_battle_familiars(user_id)
        target = next(
            (row for row in current if int(row["instance_id"]) == int(value)), None
        )
        if target is None:
            await game_shared.respond(
                interaction, battle_texts.REGISTER_NOT_REGISTERED
            )
            return

        await game_shared.respond(
            interaction,
            battle_texts.REGISTER_REPLACE_PROMPT.format(
                priority=target["priority"],
                name=familiar_display_name(target["familiar_id"]),
                level=target["level"],
            ),
            view=RegisterReplaceView(options, replaced_instance_id=int(value)),
        )


class RegisterReplaceView(PagedSelectView):
    """選んだ枠を別の使い魔へ入れ替える一時View（9.1節）。"""

    def __init__(
        self,
        options: list[discord.SelectOption],
        *,
        replaced_instance_id: int,
    ) -> None:
        super().__init__(options, placeholder=battle_texts.REGISTER_REPLACE_PLACEHOLDER)

        self.replaced_instance_id = replaced_instance_id

    async def on_choice(self, interaction: discord.Interaction, value: str) -> None:
        await interaction.response.defer(ephemeral=True)

        user_id = interaction.user.id
        current = [
            int(row["instance_id"])
            for row in get_player_battle_familiars(user_id)
        ]

        if self.replaced_instance_id not in current:
            await game_shared.respond(
                interaction, battle_texts.REGISTER_NOT_REGISTERED
            )
            return

        new_instance_id = int(value)
        if new_instance_id in current:
            await game_shared.respond(interaction, battle_texts.REGISTER_ALREADY)
            return

        # 優先順を変えずに、同じ位置だけ差し替える
        current[current.index(self.replaced_instance_id)] = new_instance_id

        result = set_player_battle_familiars(user_id, current)
        if not result["ok"]:
            await game_shared.respond(
                interaction, game_shared.error_message(result["error"])
            )
            return

        await game_shared.respond(
            interaction,
            build_register_status(user_id, result),
            view=BattleFamiliarRegisterView(user_id),
        )


class BattleFamiliarRegisterView(discord.ui.View):
    """事前登録の追加・取消を操作する一時View。"""

    def __init__(self, user_id: int) -> None:
        super().__init__(timeout=EPHEMERAL_TIMEOUT)

        master = load_master_data()
        registered = get_player_battle_familiars(user_id)

        self.user_id = user_id
        self.add_familiar.disabled = len(registered) >= master.battle.max_units
        self.replace_familiar.disabled = not registered
        self.remove_one.disabled = not registered
        self.undo.disabled = not registered
        self.clear_all.disabled = not registered

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await game_shared.respond(interaction, battle_texts.OWNER_ONLY)
            return False

        return True

    @discord.ui.button(
        label=battle_texts.REGISTER_BUTTON_ADD, style=discord.ButtonStyle.success
    )
    async def add_familiar(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        options = build_registerable_options(interaction.user.id)

        if not options:
            await game_shared.respond(
                interaction, battle_texts.REGISTER_NO_CANDIDATES
            )
            return

        await game_shared.respond(
            interaction,
            battle_texts.REGISTER_ADD_PROMPT,
            view=RegisterAddView(options),
        )

    @discord.ui.button(
        label=battle_texts.REGISTER_BUTTON_REPLACE, style=discord.ButtonStyle.primary
    )
    async def replace_familiar(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        options = build_registered_options(interaction.user.id)

        if not options:
            await game_shared.respond(interaction, battle_texts.REGISTER_NONE)
            return

        await game_shared.respond(
            interaction,
            battle_texts.REGISTER_REPLACE_SLOT_PROMPT,
            view=RegisterReplaceTargetView(
                options, placeholder=battle_texts.REGISTER_REPLACE_SLOT_PLACEHOLDER
            ),
        )

    @discord.ui.button(
        label=battle_texts.REGISTER_BUTTON_REMOVE_ONE,
        style=discord.ButtonStyle.secondary,
    )
    async def remove_one(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        options = build_registered_options(interaction.user.id)

        if not options:
            await game_shared.respond(interaction, battle_texts.REGISTER_NONE)
            return

        await game_shared.respond(
            interaction,
            battle_texts.REGISTER_REMOVE_PROMPT,
            view=RegisterRemoveView(
                options, placeholder=battle_texts.REGISTER_REMOVE_PLACEHOLDER
            ),
        )

    @discord.ui.button(
        label=battle_texts.REGISTER_BUTTON_UNDO, style=discord.ButtonStyle.secondary
    )
    async def undo(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        current = [
            int(row["instance_id"])
            for row in get_player_battle_familiars(interaction.user.id)
        ]

        if not current:
            await game_shared.respond(interaction, battle_texts.REGISTER_NONE)
            return

        result = set_player_battle_familiars(interaction.user.id, current[:-1])

        await game_shared.respond(
            interaction,
            build_register_status(interaction.user.id, result),
            view=BattleFamiliarRegisterView(interaction.user.id),
        )

    @discord.ui.button(
        label=battle_texts.REGISTER_BUTTON_CLEAR, style=discord.ButtonStyle.danger
    )
    async def clear_all(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        result = set_player_battle_familiars(interaction.user.id, [])

        await game_shared.respond(
            interaction,
            build_register_status(interaction.user.id, result),
            view=BattleFamiliarRegisterView(interaction.user.id),
        )
