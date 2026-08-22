"""ギルドバトルの入口（利用資格の確認・出場者セット・常設パネルの設置）。

固定の ``custom_id`` を持つ常設Viewは、Bot再起動後も同じボタンが動くように
します（29節）。操作対象は ``custom_id`` へ埋め込まず、``interaction.message.id`` と
``interaction.channel.id`` からDBを引いて特定します。

一時的（ephemeral）なSelectやボタンには ``custom_id`` を付けません
（discord.pyが自動採番するため）。

画面部品は責務ごとに兄弟モジュールへ分かれています。

- ``battle_common``：定数・戦闘状態の読み出し・一時Viewの基底
- ``familiar_options``：所有使い魔を選択肢へ整える純粋関数
- ``entry_views``：出場する使い魔の追加・入替・解除
- ``register_views``：バトル使い魔の事前登録
- ``battle_action_views``：バトル中の行動（攻撃・スキル・降参）
- ``matchmaking_views``：バトル申請・公開募集・レート
- ``battle_embeds``：編成確認・ランキング・各パネルのEmbed

外部（``cog.py`` ``service.py`` とテスト）が ``views.X`` で引いている名前は、
このモジュールの末尾で明示的に再公開しています。
"""

from __future__ import annotations

import logging

import discord
import config

from cogs import game_shared
from database.battle import (
    create_battle_recruitment,
    get_active_battle_for_guild,
    get_battle_entries,
    get_battle_lock,
    get_battle_roster,
    get_pending_battle_request_for_guild,
    set_battle_recruitment_message,
    set_battle_roster,
)
from database.guild import get_guild, get_guild_by_channel, get_guild_members
from game.master_data import load_master_data
from utils import ensure_panel_message, remove_legacy_panels

from . import service
from .battle_action_views import BattleCommandView, SurrenderConfirmView
from .battle_common import (
    BATTLE_PANEL_TITLE,
    EPHEMERAL_TIMEOUT,
    LEGACY_FAMILIAR_PANEL_TITLES,
    LEGACY_RANKING_PANEL_TITLES,
    LEGACY_ROSTER_PANEL_TITLES,
    RANKING_PANEL_TITLE,
    ROSTER_PANEL_TITLE,
    SELECT_LIMIT,
    SELECT_ROWS_FOR_COUNTS,
)
from .battle_embeds import (
    battle_panel_embed,
    build_ranking_embed,
    build_roster_embed,
    ranking_panel_embed,
    roster_panel_embed,
)
from .entry_views import RosterFamiliarActionView, build_entry_overview
from .matchmaking_views import (
    BattleRecruitmentView,
    BattleRequestView,
    BetRateSelectView,
    OpponentSelectView,
    RecruitmentCancelView,
    RequestCancelView,
    bet_confirmation,
    bet_rate_guide,
    opponent_options,
    recruitment_embed,
)
from .register_views import BattleFamiliarRegisterView, build_register_status
from texts import battle as battle_texts
from texts import common as common_texts


logger = logging.getLogger(__name__)


# ==================================================
# 利用資格と操作チャンネルの確認（29節・34.1節）
# ==================================================
def master_guild_of_channel(interaction: discord.Interaction) -> tuple[dict | None, str | None]:
    """マスター専用TCの操作であることを確認し、ギルド行を返す（29節・34.1節）。"""

    blocked = game_shared.game_block_reason(interaction.user)
    if blocked is not None:
        return None, blocked

    channel = interaction.channel
    if channel is None:
        return None, common_texts.CHANNEL_ERROR

    guild_row = get_guild_by_channel(channel.id)
    if guild_row is None or guild_row.get("master_text_channel_id") != channel.id:
        return None, battle_texts.NOT_MASTER_CHANNEL

    if guild_row["status"] != "active":
        return None, battle_texts.GUILD_NOT_ACTIVE

    if guild_row["master_id"] != interaction.user.id:
        return None, game_shared.error_message("not_master")

    return guild_row, None


def roster_guild_of_channel(interaction: discord.Interaction) -> tuple[dict | None, str | None]:
    """使い魔バトルチャンネルの操作であることを確認し、ギルド行を返す（34.1節）。"""

    blocked = game_shared.game_block_reason(interaction.user)
    if blocked is not None:
        return None, blocked

    channel = interaction.channel
    if channel is None:
        return None, common_texts.CHANNEL_ERROR

    guild_row = get_guild_by_channel(channel.id)
    if guild_row is None or guild_row.get("battle_member_channel_id") != channel.id:
        return None, battle_texts.NOT_BATTLE_MEMBER_CHANNEL

    if guild_row["status"] != "active":
        return None, battle_texts.GUILD_NOT_ACTIVE

    return guild_row, None


# ==================================================
# 出場者セット（9節）
# ==================================================
def default_familiar_counts(member_count: int) -> list[int]:
    """出場者ごとの使い魔体数の初期値を、合計上限まで均等に配る（9節）。"""

    master = load_master_data()

    if member_count <= 0:
        return []

    limit = master.familiar_limit_per_member(member_count)
    total = min(master.battle.max_units, limit * member_count)

    base = total // member_count
    counts = [max(1, base)] * member_count

    # 端数は先頭の出場者から1体ずつ、上限に触れない範囲で足す。
    remainder = total - sum(counts)
    index = 0
    while remainder > 0 and index < member_count:
        if counts[index] < limit:
            counts[index] += 1
            remainder -= 1
        else:
            index += 1

    return counts


async def apply_roster(
    interaction: discord.Interaction,
    guild_row: dict,
    assignments: list[tuple[int, int]],
) -> None:
    """出場者セットを確定し、結果を知らせる。

    使い魔バトルチャンネルは所属メンバー全員へ開放しているため、出場者が
    変わってもチャンネル権限は張り替えません（9.1節）。
    """

    guild_id = int(guild_row["guild_id"])
    master = load_master_data()

    result = set_battle_roster(guild_id, assignments)
    if not result["ok"]:
        await game_shared.respond(
            interaction, game_shared.error_message(result["error"])
        )
        return

    user_ids = [user_id for user_id, _ in assignments]
    discord_guild = interaction.guild

    if discord_guild is not None:
        await ensure_roster_panel(interaction.client, discord_guild, guild_row)

    lines = [
        game_shared.item_line(
            battle_texts.LABEL_MEMBERS,
            battle_texts.ROSTER_MEMBER_COUNT.format(count=len(user_ids)),
        ),
        game_shared.item_line(
            battle_texts.LABEL_FAMILIARS,
            battle_texts.ROSTER_FAMILIAR_COUNT.format(
                count=sum(count for _, count in assignments),
                max_units=master.battle.max_units,
            ),
        ),
        "",
    ]
    lines.extend(
        battle_texts.ROSTER_ASSIGN_LINE.format(user_id=user_id, count=count)
        for user_id, count in assignments
    )

    added = "・".join(f"<@{user_id}>" for user_id in result["added"])
    removed = "・".join(f"<@{user_id}>" for user_id in result["removed"])
    lines.append("")

    if added:
        lines.append(game_shared.item_line(battle_texts.LABEL_ADDED, added))
    if removed:
        lines.append(game_shared.item_line(battle_texts.LABEL_REMOVED, removed))

    if result.get("adopted"):
        lines.append(
            battle_texts.ROSTER_ADOPTED_NOTE.format(count=len(result["adopted"]))
        )

    if result.get("released"):
        lines.append(
            battle_texts.ROSTER_RELEASED_NOTE.format(count=len(result["released"]))
        )

    lines.append(
        battle_texts.ROSTER_SWAP_HINT.format(
            channel=game_shared.CHANNEL_LABELS["battle_member"]
        )
    )

    await game_shared.respond(interaction, "\n".join(lines))


class RosterSelectView(discord.ui.View):
    """所属メンバーから出場者を選ぶ一時View。"""

    def __init__(self, guild_row: dict, members: list[dict]) -> None:
        super().__init__(timeout=EPHEMERAL_TIMEOUT)

        master = load_master_data()
        self.guild_row = guild_row
        self.member_order = [int(row["user_id"]) for row in members]
        self.member_names = {
            int(row["user_id"]): str(row["display_name"]) for row in members
        }

        options = [
            discord.SelectOption(
                label=row["display_name"][:100],
                description=(
                    battle_texts.OPTION_GUILD_MASTER
                    if row["member_role"] == "master"
                    else None
                ),
                value=str(row["user_id"]),
            )
            for row in members[:SELECT_LIMIT]
        ]

        select = discord.ui.Select(
            placeholder=battle_texts.ROSTER_SELECT_PLACEHOLDER.format(
                max_members=master.battle.max_members
            ),
            min_values=master.battle.min_members,
            max_values=min(master.battle.max_members, len(options)),
            options=options,
        )
        select.callback = self._select_callback
        self.add_item(select)
        self._select = select

    async def _select_callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        guild_id = int(self.guild_row["guild_id"])

        # 権限と状態をDBから再確認する（29節）
        guild_row = get_guild(guild_id)
        if guild_row is None or guild_row["master_id"] != interaction.user.id:
            await game_shared.respond(interaction, game_shared.error_message("not_master"))
            return

        chosen = {int(value) for value in self._select.values}
        # Selectの返却順は保証されないため、メンバー一覧の順でスロットを決める
        user_ids = [user_id for user_id in self.member_order if user_id in chosen]

        master = load_master_data()
        limit = master.familiar_limit_per_member(len(user_ids))
        defaults = default_familiar_counts(len(user_ids))

        # 1人1体しか置けない人数では選ぶ余地がないため、そのまま確定する。
        # 選択肢がある場合も、Discordの5行制限に収まらない人数は初期値で確定する。
        if limit <= 1 or len(user_ids) > SELECT_ROWS_FOR_COUNTS:
            await apply_roster(
                interaction, guild_row, list(zip(user_ids, defaults))
            )
            return

        await game_shared.respond(
            interaction,
            battle_texts.ROSTER_COUNT_PROMPT.format(
                limit=limit, max_units=master.battle.max_units
            ),
            view=RosterCountView(
                guild_row=guild_row,
                user_ids=user_ids,
                names=self.member_names,
                limit=limit,
                defaults=defaults,
            ),
        )


class RosterCountView(discord.ui.View):
    """出場者ごとの使い魔体数を決める一時View（9節）。"""

    def __init__(
        self,
        *,
        guild_row: dict,
        user_ids: list[int],
        names: dict[int, str],
        limit: int,
        defaults: list[int],
    ) -> None:
        super().__init__(timeout=EPHEMERAL_TIMEOUT)

        master = load_master_data()

        self.guild_row = guild_row
        self.user_ids = user_ids
        self.counts = {
            user_id: count for user_id, count in zip(user_ids, defaults)
        }
        self.max_units = master.battle.max_units
        self._selects: dict[int, discord.ui.Select] = {}

        for row, user_id in enumerate(user_ids):
            current = self.counts[user_id]
            select = discord.ui.Select(
                placeholder=battle_texts.ROSTER_COUNT_LABEL.format(
                    name=names.get(user_id, user_id), count=current
                ),
                min_values=1,
                max_values=1,
                row=row,
                options=[
                    discord.SelectOption(
                        label=battle_texts.ROSTER_COUNT_LABEL.format(
                            name=names.get(user_id, user_id), count=count
                        )[:100],
                        value=f"{user_id}:{count}",
                        default=count == current,
                    )
                    for count in range(1, limit + 1)
                ],
            )
            select.callback = self._count_callback
            self.add_item(select)
            self._selects[user_id] = select

        confirm = discord.ui.Button(
            label=battle_texts.ROSTER_COUNT_CONFIRM,
            style=discord.ButtonStyle.success,
            row=len(user_ids),
        )
        confirm.callback = self._confirm_callback
        self.add_item(confirm)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != int(self.guild_row["master_id"]):
            await game_shared.respond(
                interaction, game_shared.error_message("not_master")
            )
            return False

        return True

    async def _count_callback(self, interaction: discord.Interaction) -> None:
        for select in self._selects.values():
            if not select.values:
                continue

            user_id, _, count = str(select.values[0]).partition(":")
            self.counts[int(user_id)] = int(count)

        total = sum(self.counts[user_id] for user_id in self.user_ids)

        try:
            await interaction.response.edit_message(
                content=(
                    battle_texts.ROSTER_COUNT_HEADING
                    + "\n"
                    + game_shared.item_line(
                        battle_texts.LABEL_CURRENT_TOTAL,
                        battle_texts.ROSTER_COUNT_TOTAL.format(
                            total=total, max_units=self.max_units
                        ),
                    )
                ),
                view=self,
            )
        except discord.HTTPException:
            logger.warning("体数選択の更新に失敗しました")

    async def _confirm_callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        guild_row = get_guild(int(self.guild_row["guild_id"]))
        if guild_row is None or guild_row["master_id"] != interaction.user.id:
            await game_shared.respond(
                interaction, game_shared.error_message("not_master")
            )
            return

        assignments = [(user_id, self.counts[user_id]) for user_id in self.user_ids]
        await apply_roster(interaction, guild_row, assignments)

        self.stop()


# ==================================================
# 常設View：ギルドマスター専用TCのバトルパネル（8.2節）
# ==================================================
class GuildBattlePanelView(discord.ui.View):
    """ギルドマスターがバトル準備と対戦成立を操作するパネル。"""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    # ==================================================
    # メンバーセット
    # ==================================================
    @discord.ui.button(
        label=battle_texts.BUTTON_SET_MEMBERS,
        style=discord.ButtonStyle.primary,
        custom_id="guild_battle:set_members",
    )
    async def set_members(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        guild_row, error = master_guild_of_channel(interaction)
        if error is not None:
            await game_shared.respond(interaction, error)
            return

        if guild_row["roster_locked"]:
            await game_shared.respond(
                interaction, game_shared.error_message("roster_locked")
            )
            return

        discord_guild = interaction.guild
        members = []
        for row in get_guild_members(int(guild_row["guild_id"])):
            member = (
                discord_guild.get_member(row["user_id"])
                if discord_guild is not None
                else None
            )
            members.append(
                {
                    "user_id": row["user_id"],
                    "member_role": row["member_role"],
                    "display_name": (
                        member.display_name if member else f"ID:{row['user_id']}"
                    ),
                }
            )

        if not members:
            await game_shared.respond(interaction, battle_texts.NO_GUILD_MEMBERS)
            return

        await game_shared.respond(
            interaction,
            battle_texts.ROSTER_SELECT_PROMPT,
            view=RosterSelectView(guild_row, members),
        )

    # ==================================================
    # セット確認（11節）
    # ==================================================
    @discord.ui.button(
        label=battle_texts.BUTTON_CHECK_ROSTER,
        style=discord.ButtonStyle.secondary,
        custom_id="guild_battle:check_roster",
    )
    async def check_roster(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        guild_row, error = master_guild_of_channel(interaction)
        if error is not None:
            await game_shared.respond(interaction, error)
            return

        await game_shared.respond(
            interaction, embed=build_roster_embed(int(guild_row["guild_id"]))
        )

    # ==================================================
    # バトル申請（12.1節）
    # ==================================================
    @discord.ui.button(
        label=battle_texts.BUTTON_REQUEST,
        style=discord.ButtonStyle.success,
        custom_id="guild_battle:request",
    )
    async def request_battle(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        guild_row, error = master_guild_of_channel(interaction)
        if error is not None:
            await game_shared.respond(interaction, error)
            return

        guild_id = int(guild_row["guild_id"])

        pending = get_pending_battle_request_for_guild(guild_id)
        if pending is not None:
            if int(pending["from_guild_id"]) == guild_id:
                opponent = get_guild(int(pending["to_guild_id"]))
                name = (
                    opponent["name"] if opponent else battle_texts.OPPONENT_FALLBACK
                )
                await game_shared.respond(
                    interaction,
                    battle_texts.REQUEST_PENDING_OUT.format(guild_name=name),
                    view=RequestCancelView(int(pending["request_id"])),
                )
            else:
                await game_shared.respond(
                    interaction, battle_texts.REQUEST_PENDING_IN
                )
            return

        # 申し込めない理由をすべて具体的に出す（12節）
        issues = service.entry_blockers(interaction.client, guild_id)
        if issues:
            await game_shared.respond(
                interaction,
                service.blocker_message(
                    issues, action=battle_texts.ACTION_REQUEST
                ),
            )
            return

        if not opponent_options(guild_id):
            await game_shared.respond(interaction, battle_texts.NO_OPPONENT_AVAILABLE)
            return

        async def open_opponent_select(
            rate_interaction: discord.Interaction, rate
        ) -> None:
            options = opponent_options(guild_id)
            if not options:
                await game_shared.respond(
                    rate_interaction, battle_texts.NO_OPPONENT_AVAILABLE
                )
                return

            await game_shared.respond(
                rate_interaction,
                battle_texts.OPPONENT_SELECT_PROMPT.format(
                    rate=rate.name, bet=bet_confirmation(guild_id, rate.coin)
                ),
                view=OpponentSelectView(guild_row, options, bet_coin=rate.coin),
            )

        await game_shared.respond(
            interaction,
            bet_rate_guide(battle_texts.NEXT_STEP_OPPONENT),
            view=BetRateSelectView(
                guild_row=guild_row,
                action=battle_texts.ACTION_REQUEST,
                on_choose=open_opponent_select,
            ),
        )

    # ==================================================
    # バトル募集（12.2節）
    # ==================================================
    @discord.ui.button(
        label=battle_texts.BUTTON_RECRUIT,
        style=discord.ButtonStyle.success,
        custom_id="guild_battle:recruit",
    )
    async def recruit_battle(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        guild_row, error = master_guild_of_channel(interaction)
        if error is not None:
            await game_shared.respond(interaction, error)
            return

        guild_id = int(guild_row["guild_id"])

        lock = get_battle_lock(guild_id)
        if lock is not None:
            if lock["lock_type"] == "recruitment":
                await game_shared.respond(
                    interaction,
                    battle_texts.RECRUIT_PENDING,
                    view=RecruitmentCancelView(int(lock["reference_id"])),
                )
            else:
                await game_shared.respond(
                    interaction, game_shared.error_message("guild_busy")
                )
            return

        if not config.GUILD_BATTLE_RECRUITMENT_CHANNEL_ID:
            await game_shared.respond(
                interaction, battle_texts.RECRUIT_CHANNEL_UNSET
            )
            return

        channel = interaction.client.get_channel(
            config.GUILD_BATTLE_RECRUITMENT_CHANNEL_ID
        )
        if not isinstance(channel, discord.TextChannel):
            await game_shared.respond(
                interaction, battle_texts.RECRUIT_CHANNEL_NOT_FOUND
            )
            return

        # 募集する前に、開始条件を満たしているかを具体的に確認する（12.2節）
        issues = service.entry_blockers(interaction.client, guild_id)
        if issues:
            await game_shared.respond(
                interaction,
                service.blocker_message(issues, action=battle_texts.ACTION_RECRUIT),
            )
            return

        async def post_recruitment(
            rate_interaction: discord.Interaction, rate
        ) -> None:
            result = create_battle_recruitment(guild_id, bet_coin=rate.coin)
            if not result["ok"]:
                await game_shared.respond(
                    rate_interaction, game_shared.error_message(result["error"])
                )
                return

            recruitment_id = int(result["recruitment_id"])
            message = await channel.send(
                embed=recruitment_embed(
                    guild_row,
                    state_text=battle_texts.RECRUIT_STATE_OPEN,
                    bet_coin=rate.coin,
                ),
                view=BattleRecruitmentView(),
            )
            set_battle_recruitment_message(recruitment_id, channel.id, message.id)

            await game_shared.respond(
                rate_interaction,
                battle_texts.RECRUIT_POSTED.format(
                    channel=channel.mention,
                    rate=rate.name,
                    bet=bet_confirmation(guild_id, rate.coin),
                ),
            )

        await game_shared.respond(
            interaction,
            bet_rate_guide(battle_texts.NEXT_STEP_RECRUIT),
            view=BetRateSelectView(
                guild_row=guild_row,
                action=battle_texts.ACTION_RECRUIT,
                on_choose=post_recruitment,
            ),
        )

    # ==================================================
    # 降参（26.1節）
    # ==================================================
    @discord.ui.button(
        label=battle_texts.BUTTON_SURRENDER,
        style=discord.ButtonStyle.danger,
        custom_id="guild_battle:surrender",
    )
    async def surrender(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        guild_row, error = master_guild_of_channel(interaction)
        if error is not None:
            await game_shared.respond(interaction, error)
            return

        guild_id = int(guild_row["guild_id"])
        battle_row = get_active_battle_for_guild(guild_id)
        if battle_row is None or battle_row["status"] != "in_progress":
            await game_shared.respond(interaction, battle_texts.NO_ACTIVE_BATTLE)
            return

        await game_shared.respond(
            interaction,
            battle_texts.SURRENDER_CONFIRM,
            view=SurrenderConfirmView(guild_id, int(battle_row["battle_id"])),
        )


# ==================================================
# 常設View：使い魔バトルチャンネルのパネル
# 事前登録は全メンバー、出場する使い魔の差し替えは出場者だけ
# ==================================================
class BattleMemberPanelView(discord.ui.View):
    """バトル用使い魔の事前登録と、出場者による差し替えをまとめたパネル。"""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label=battle_texts.BUTTON_REGISTER,
        style=discord.ButtonStyle.primary,
        custom_id="guild_battle:register_familiars",
    )
    async def register(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        """バトルで使う使い魔を、順番付きで登録する（9.1節）。

        出場者に選ばれていなくても、バトルの進行中でも操作できます。
        """

        blocked = game_shared.game_block_reason(interaction.user)
        if blocked is not None:
            await game_shared.respond(interaction, blocked)
            return

        await interaction.response.defer(ephemeral=True)

        try:
            status = build_register_status(interaction.user.id)
            view = BattleFamiliarRegisterView(interaction.user.id)
        except Exception:
            logger.exception(
                "バトル使い魔登録の表示に失敗しました: user_id=%s", interaction.user.id
            )
            await game_shared.respond(
                interaction, game_shared.UNEXPECTED_ERROR_MESSAGE
            )
            return

        await game_shared.respond(interaction, status, view=view)

    @discord.ui.button(
        label=battle_texts.BUTTON_SET_FAMILIAR,
        style=discord.ButtonStyle.success,
        custom_id="guild_battle:set_familiar",
    )
    async def set_familiar(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        """出場者が、割り当ての範囲で使い魔を差し替える（9.3節）。"""

        guild_row, error = roster_guild_of_channel(interaction)
        if error is not None:
            await game_shared.respond(interaction, error)
            return

        guild_id = int(guild_row["guild_id"])

        if guild_row["roster_locked"]:
            await game_shared.respond(
                interaction, game_shared.error_message("roster_locked")
            )
            return

        roster = get_battle_roster(guild_id)
        assigned = next(
            (
                int(row["familiar_count"] or 0)
                for row in roster
                if int(row["user_id"]) == interaction.user.id
            ),
            None,
        )

        if assigned is None:
            await game_shared.respond(interaction, battle_texts.NOT_IN_ROSTER)
            return

        rank_info = game_shared.get_player_rank_info(interaction.user.id)
        if rank_info is None:
            # 27節: 推測でランクを補わず、操作を止めて運営ログへ記録する
            await game_shared.game_admin_log(
                interaction.client,
                action="プレイヤーランク未同期",
                executor_id=interaction.user.id,
                target_user_id=interaction.user.id,
                target_guild_id=guild_id,
                success=False,
                reason="player_roles が未同期のため使い魔セットを中止",
            )
            await game_shared.respond(interaction, battle_texts.RANK_UNKNOWN)
            return

        master = load_master_data()
        entries = get_battle_entries(guild_id)
        mine = [entry for entry in entries if int(entry["user_id"]) == interaction.user.id]

        status = build_entry_overview(
            guild_id, viewer_id=interaction.user.id, assigned=assigned
        )

        view = RosterFamiliarActionView(
            guild_id=guild_id,
            user_id=interaction.user.id,
            can_add=len(entries) < master.battle.max_units and len(mine) < assigned,
            can_remove=bool(mine),
        )

        await game_shared.respond(interaction, status, view=view)


# ==================================================
# 常設View：ランキングパネル（26.2節）
# ==================================================
class BattleRankingPanelView(discord.ui.View):
    """ギルドバトルの通算ランキングを表示するパネル。"""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label=battle_texts.BUTTON_RANKING,
        style=discord.ButtonStyle.primary,
        custom_id="guild_battle:ranking",
    )
    async def ranking(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        blocked = game_shared.game_block_reason(interaction.user)
        if blocked is not None:
            await game_shared.respond(interaction, blocked)
            return

        await game_shared.respond(
            interaction, embed=build_ranking_embed(interaction.user.id)
        )


# ==================================================
# 常設パネルの設置
# ==================================================
async def ensure_roster_panel(
    bot: discord.Client, guild: discord.Guild, guild_row: dict
) -> None:
    """使い魔バトルチャンネルへパネルを（無ければ）設置する。

    ギルド情報パネルはギルド情報チャンネルへ移したため、ここには置きません。
    改名前に作られたギルドのチャンネルは、ここで新しい名前へ寄せます。
    """

    channel_id = guild_row.get("battle_member_channel_id")
    if not channel_id:
        return

    await game_shared.ensure_channel_name(
        guild,
        channel_id,
        game_shared.CHANNEL_LABELS["battle_member"],
        legacy_names=game_shared.LEGACY_CHANNEL_NAMES["battle_member"],
    )
    await remove_legacy_panels(
        bot, guild, int(channel_id), titles=LEGACY_ROSTER_PANEL_TITLES
    )

    await ensure_panel_message(
        bot,
        guild,
        int(channel_id),
        panel_title=ROSTER_PANEL_TITLE,
        embed=roster_panel_embed(),
        view=BattleMemberPanelView(),
        panel_name="使い魔セットパネル",
    )


async def ensure_battle_panel(
    bot: discord.Client, guild: discord.Guild, guild_row: dict
) -> None:
    """ギルドマスター専用TCへバトルパネルを（無ければ）設置する（8.2節）。"""

    channel_id = guild_row.get("master_text_channel_id")
    if not channel_id:
        return

    await ensure_panel_message(
        bot,
        guild,
        int(channel_id),
        panel_title=BATTLE_PANEL_TITLE,
        embed=battle_panel_embed(),
        view=GuildBattlePanelView(),
        panel_name="ギルドバトルパネル",
    )


# ==================================================
# 再公開（外部が ``views.X`` で引いている名前）
# ==================================================
# 兄弟モジュールへ移した後も ``cog.py`` ``service.py`` とテストが
# ``views.X`` で引けるようにするため、ここで明示的に並べます。
__all__ = [
    # 常設パネルの表題（``ensure_panel_message`` の重複判定に使う）
    "BATTLE_PANEL_TITLE",
    "ROSTER_PANEL_TITLE",
    "RANKING_PANEL_TITLE",
    "LEGACY_ROSTER_PANEL_TITLES",
    "LEGACY_FAMILIAR_PANEL_TITLES",
    "LEGACY_RANKING_PANEL_TITLES",
    # Embedの組み立て
    "battle_panel_embed",
    "roster_panel_embed",
    "ranking_panel_embed",
    "build_roster_embed",
    "build_ranking_embed",
    # 常設View（``bot.add_view`` で登録する）
    "GuildBattlePanelView",
    "BattleMemberPanelView",
    "BattleRankingPanelView",
    "BattleRequestView",
    "BattleRecruitmentView",
    "BattleCommandView",
    # パネルの設置
    "ensure_battle_panel",
    "ensure_roster_panel",
    # このモジュール自身の入口
    "master_guild_of_channel",
    "roster_guild_of_channel",
    "default_familiar_counts",
    "apply_roster",
    "RosterSelectView",
    "RosterCountView",
]
