"""ギルドバトルの操作画面（パネル・申請・募集・バトル専用チャンネル）。

固定の ``custom_id`` を持つ常設Viewはこのモジュールにまとめ、Bot再起動後も
同じボタンが動くようにします（29節）。操作対象は ``custom_id`` へ埋め込まず、
``interaction.message.id`` と ``interaction.channel.id`` からDBを引いて特定します。

一時的（ephemeral）なSelectやボタンには ``custom_id`` を付けません
（discord.pyが自動採番するため）。
"""

from __future__ import annotations

import logging

import discord
import config

from cogs import game_shared
from database.battle import (
    claim_battle_recruitment,
    create_battle_recruitment,
    create_battle_request,
    get_active_battle_for_guild,
    get_battle,
    get_battle_for_channel,
    get_battle_lock,
    get_battle_recruitment,
    get_battle_recruitment_by_message,
    get_battle_request,
    get_battle_request_by_message,
    get_battle_roster,
    get_pending_battle_request_for_guild,
    load_battle_state,
    resolve_battle_recruitment,
    resolve_battle_request,
    set_battle_recruitment_message,
    set_battle_request_message,
    set_battle_roster,
    set_roster_familiar,
)
from database.familiar import get_owned_familiar, get_owned_familiars
from database.guild import (
    get_active_guilds,
    get_guild,
    get_guild_by_channel,
    get_guild_members,
    get_guild_ranking,
    get_guild_ranking_position,
    get_player_guild,
)
from game import battle_embed, battle_engine
from game.master_data import load_master_data
from game.models import (
    ACTION_ATTACK,
    ACTION_SKILL,
    BattleAction,
    BattleRuleError,
)
from utils import ensure_panel_message

from . import service


logger = logging.getLogger(__name__)

# 公開スイッチがOFFのときに返す共通メッセージ（34.1節）
DISABLED_MESSAGE = game_shared.DISABLED_MESSAGE

# 常設パネルのタイトル（``ensure_panel_message`` の重複判定に使う）
BATTLE_PANEL_TITLE = "ギルドバトル"
ROSTER_PANEL_TITLE = "バトル出場者"
RANKING_PANEL_TITLE = "ギルドバトルランキング"

# 一時Viewの有効時間（秒）
EPHEMERAL_TIMEOUT = 300

# Discordの選択肢の上限
SELECT_LIMIT = 25


# ==================================================
# 共通ヘルパ
# ==================================================
def _familiar_name(familiar_id: str) -> str:
    """使い魔IDから表示名を取得する。"""

    master = load_master_data()
    familiar = master.get_familiar(familiar_id)
    return familiar.name if familiar else familiar_id


def master_guild_of_channel(interaction: discord.Interaction) -> tuple[dict | None, str | None]:
    """マスター専用TCの操作であることを確認し、ギルド行を返す（29節・34.1節）。"""

    if not game_shared.is_game_enabled():
        return None, DISABLED_MESSAGE

    channel = interaction.channel
    if channel is None:
        return None, "チャンネル情報を取得できませんでした。"

    guild_row = get_guild_by_channel(channel.id)
    if guild_row is None or guild_row.get("master_text_channel_id") != channel.id:
        return None, "このチャンネルはギルドマスター専用TCではありません。"

    if guild_row["status"] != "active":
        return None, "このギルドは現在活動中ではありません。"

    if guild_row["master_id"] != interaction.user.id:
        return None, game_shared.error_message("not_master")

    return guild_row, None


def roster_guild_of_channel(interaction: discord.Interaction) -> tuple[dict | None, str | None]:
    """バトル出場者専用TCの操作であることを確認し、ギルド行を返す（34.1節）。"""

    if not game_shared.is_game_enabled():
        return None, DISABLED_MESSAGE

    channel = interaction.channel
    if channel is None:
        return None, "チャンネル情報を取得できませんでした。"

    guild_row = get_guild_by_channel(channel.id)
    if guild_row is None or guild_row.get("battle_member_channel_id") != channel.id:
        return None, "このチャンネルはバトル出場者専用TCではありません。"

    if guild_row["status"] != "active":
        return None, "このギルドは現在活動中ではありません。"

    return guild_row, None


def load_actor(channel_id: int, user_id: int):
    """バトル専用チャンネルから、現在の行動者と戦闘状態を取り出す（16節・34.1節）。"""

    if not game_shared.is_game_enabled():
        return None, None, None, DISABLED_MESSAGE

    battle_row = get_battle_for_channel(channel_id)
    if battle_row is None:
        return None, None, None, "このチャンネルで進行中のバトルはありません。"

    if battle_row["status"] != "in_progress":
        return None, None, None, "このバトルは現在進行中ではありません。"

    state = load_battle_state(int(battle_row["battle_id"]))
    if state is None:
        return None, None, None, "バトル情報を取得できませんでした。"

    unit = state.current_unit()
    if unit is None:
        return None, None, None, "現在は行動を受け付けていません。"

    if unit.player_id != user_id:
        return None, None, None, "現在の行動順のプレイヤーだけが操作できます。"

    return battle_row, state, unit, None


def _unit_option(unit) -> discord.SelectOption:
    """戦闘用使い魔を選択肢へ変換する。"""

    return discord.SelectOption(
        label=f"{_familiar_name(unit.familiar_id)} Lv.{unit.level}"[:100],
        description=f"HP {unit.current_hp}/{unit.max_hp}　ATK {unit.current_atk}"[:100],
        value=str(unit.battle_unit_id),
    )


async def _apply_and_report(
    interaction: discord.Interaction,
    battle_id: int,
    action,
    *,
    expected_seq: int,
    success_message: str,
) -> bool:
    """行動を適用し、結果を実行者へ ephemeral で返す。"""

    battle_row = get_battle(battle_id)
    elapsed = service.elapsed_seconds_since_turn_start(battle_row)

    try:
        applied = await service.apply_action(
            interaction.client,
            battle_id,
            action,
            elapsed_seconds=elapsed,
            expected_seq=expected_seq,
        )
    except BattleRuleError as exc:
        await game_shared.respond(interaction, str(exc))
        return False
    except Exception:
        logger.exception(f"バトル行動の処理に失敗しました: battle_id={battle_id}")
        await game_shared.respond(interaction, "行動を処理できませんでした。")
        return False

    if not applied:
        await game_shared.respond(interaction, "他の操作が先に処理されました。")
        return False

    await game_shared.respond(interaction, success_message)
    return True


# ==================================================
# 一時View（custom_idを付けない）
# ==================================================
class PagedSelectView(discord.ui.View):
    """25件を超える候補をページ送りで1件選ばせる一時View。"""

    def __init__(
        self,
        options: list[discord.SelectOption],
        *,
        placeholder: str,
    ) -> None:
        super().__init__(timeout=EPHEMERAL_TIMEOUT)

        self._options = options
        self._placeholder = placeholder
        self.page = 0
        self._render()

    @property
    def page_count(self) -> int:
        return max(1, (len(self._options) + SELECT_LIMIT - 1) // SELECT_LIMIT)

    def _render(self) -> None:
        self.clear_items()

        start = self.page * SELECT_LIMIT
        chunk = self._options[start : start + SELECT_LIMIT]

        select = discord.ui.Select(
            placeholder=f"{self._placeholder}（{self.page + 1}/{self.page_count}）"[:150],
            min_values=1,
            max_values=1,
            options=chunk,
        )
        select.callback = self._select_callback
        self.add_item(select)
        self._select = select

        if self.page_count > 1:
            previous = discord.ui.Button(
                label="前の25件",
                style=discord.ButtonStyle.secondary,
                disabled=self.page == 0,
            )
            previous.callback = self._previous_callback
            self.add_item(previous)

            following = discord.ui.Button(
                label="次の25件",
                style=discord.ButtonStyle.secondary,
                disabled=self.page >= self.page_count - 1,
            )
            following.callback = self._next_callback
            self.add_item(following)

    async def _previous_callback(self, interaction: discord.Interaction) -> None:
        self.page = max(0, self.page - 1)
        self._render()
        await interaction.response.edit_message(view=self)

    async def _next_callback(self, interaction: discord.Interaction) -> None:
        self.page = min(self.page_count - 1, self.page + 1)
        self._render()
        await interaction.response.edit_message(view=self)

    async def _select_callback(self, interaction: discord.Interaction) -> None:
        await self.on_choice(interaction, self._select.values[0])

    async def on_choice(self, interaction: discord.Interaction, value: str) -> None:
        """選択されたときの処理。継承先で実装する。"""

        raise NotImplementedError


class ConfirmView(discord.ui.View):
    """2段階確認用の一時View。"""

    def __init__(self, *, confirm_label: str = "実行する") -> None:
        super().__init__(timeout=EPHEMERAL_TIMEOUT)

        confirm = discord.ui.Button(
            label=confirm_label, style=discord.ButtonStyle.danger
        )
        confirm.callback = self._confirm_callback
        self.add_item(confirm)

        cancel = discord.ui.Button(label="やめる", style=discord.ButtonStyle.secondary)
        cancel.callback = self._cancel_callback
        self.add_item(cancel)

        self._confirm = confirm

    async def _confirm_callback(self, interaction: discord.Interaction) -> None:
        # 連打による二重実行を防ぐため、押下直後に操作不能にする
        for item in self.children:
            item.disabled = True

        try:
            await interaction.response.edit_message(view=self)
        except discord.HTTPException:
            pass

        await self.on_confirm(interaction)

    async def _cancel_callback(self, interaction: discord.Interaction) -> None:
        for item in self.children:
            item.disabled = True

        try:
            await interaction.response.edit_message(content="操作を取り消しました。", view=self)
        except discord.HTTPException:
            pass

    async def on_confirm(self, interaction: discord.Interaction) -> None:
        """確定されたときの処理。継承先で実装する。"""

        raise NotImplementedError


# ==================================================
# 出場者セット（9節）
# ==================================================
class RosterSelectView(discord.ui.View):
    """所属メンバーから出場者を選ぶ一時View。"""

    def __init__(self, guild_row: dict, members: list[dict]) -> None:
        super().__init__(timeout=EPHEMERAL_TIMEOUT)

        master = load_master_data()
        self.guild_row = guild_row
        self.member_order = [int(row["user_id"]) for row in members]

        options = [
            discord.SelectOption(
                label=row["display_name"][:100],
                description="ギルドマスター" if row["member_role"] == "master" else None,
                value=str(row["user_id"]),
            )
            for row in members[:SELECT_LIMIT]
        ]

        select = discord.ui.Select(
            placeholder=f"出場者を選択（最大{master.battle.team_size}人）",
            min_values=1,
            max_values=min(master.battle.team_size, len(options)),
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

        result = set_battle_roster(guild_id, user_ids)
        if not result["ok"]:
            await game_shared.respond(
                interaction, game_shared.error_message(result["error"])
            )
            return

        discord_guild = interaction.guild
        if discord_guild is not None:
            member_ids = [row["user_id"] for row in get_guild_members(guild_id)]
            await game_shared.apply_guild_permissions(
                discord_guild,
                guild_row,
                member_ids=member_ids,
                roster_ids=user_ids,
            )
            await ensure_roster_panel(interaction.client, discord_guild, guild_row)

        added = "・".join(f"<@{user_id}>" for user_id in result["added"]) or "なし"
        removed = "・".join(f"<@{user_id}>" for user_id in result["removed"]) or "なし"

        await game_shared.respond(
            interaction,
            (
                f"出場者を{len(user_ids)}人にセットしました。\n"
                f"追加：{added}\n"
                f"除外：{removed}"
            ),
        )


# ==================================================
# 使い魔セット（10.3節）
# ==================================================
class RosterFamiliarSelectView(PagedSelectView):
    """出場者が持ち込む使い魔を選ぶ一時View。"""

    def __init__(self, guild_id: int, options: list[discord.SelectOption]) -> None:
        super().__init__(options, placeholder="使用する使い魔を選択")

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

        result = set_roster_familiar(self.guild_id, interaction.user.id, int(value))
        if not result["ok"]:
            await game_shared.respond(
                interaction, game_shared.error_message(result["error"])
            )
            return

        owned = get_owned_familiar(int(value))
        if owned is None:
            await game_shared.respond(interaction, "使い魔をセットしました。")
            return

        await game_shared.respond(
            interaction,
            (
                f"**{_familiar_name(owned['familiar_id'])} "
                f"Lv.{owned['level']}** をバトルで使用します。"
            ),
        )


# ==================================================
# バトル申請（12.1節）
# ==================================================
class OpponentSelectView(PagedSelectView):
    """バトル申請の相手ギルドを選ぶ一時View。"""

    def __init__(self, guild_row: dict, options: list[discord.SelectOption]) -> None:
        super().__init__(options, placeholder="対戦を申し込むギルドを選択")

        self.guild_row = guild_row

    async def on_choice(self, interaction: discord.Interaction, value: str) -> None:
        await interaction.response.defer(ephemeral=True)

        guild_id = int(self.guild_row["guild_id"])
        guild_row = get_guild(guild_id)
        if guild_row is None or guild_row["master_id"] != interaction.user.id:
            await game_shared.respond(interaction, game_shared.error_message("not_master"))
            return

        opponent_id = int(value)
        opponent_row = get_guild(opponent_id)
        if opponent_row is None or opponent_row["status"] != "active":
            await game_shared.respond(
                interaction, game_shared.error_message("guild_not_found")
            )
            return

        result = create_battle_request(guild_id, opponent_id)
        if not result["ok"]:
            await game_shared.respond(
                interaction, game_shared.error_message(result["error"])
            )
            return

        request_id = int(result["request_id"])
        discord_guild = interaction.guild
        channel = None
        if discord_guild is not None and opponent_row.get("master_text_channel_id"):
            channel = discord_guild.get_channel(
                int(opponent_row["master_text_channel_id"])
            )

        if not isinstance(channel, discord.TextChannel):
            resolve_battle_request(request_id, "cancelled")
            await game_shared.respond(
                interaction, "相手ギルドのマスター専用TCが見つかりませんでした。"
            )
            return

        embed = discord.Embed(
            title="⚔ ギルドバトル申請",
            description=(
                f"**{guild_row['name']}** から対戦の申し込みが届きました。\n"
                "承認すると開始前チェックを行い、条件を満たしていればバトルが始まります。"
            ),
            color=config.COLOR_PURPLE,
        )
        embed.add_field(name="申請元", value=guild_row["name"], inline=True)
        embed.add_field(name="申請先", value=opponent_row["name"], inline=True)

        message = await channel.send(embed=embed, view=BattleRequestView())
        set_battle_request_message(request_id, channel.id, message.id)

        await game_shared.respond(
            interaction,
            (
                f"**{opponent_row['name']}** へバトル申請を送信しました。\n"
                "相手が回答する前なら、もう一度「バトル申請」から取り消せます。"
            ),
        )


class RequestCancelView(ConfirmView):
    """送信済みバトル申請を取り消す一時View。"""

    def __init__(self, request_id: int) -> None:
        super().__init__(confirm_label="申請を取り消す")

        self.request_id = request_id

    async def on_confirm(self, interaction: discord.Interaction) -> None:
        request_row = get_battle_request(self.request_id)
        if request_row is None:
            await game_shared.respond(interaction, game_shared.error_message("not_pending"))
            return

        guild_row = get_guild(int(request_row["from_guild_id"]))
        if guild_row is None or guild_row["master_id"] != interaction.user.id:
            await game_shared.respond(interaction, game_shared.error_message("not_master"))
            return

        result = resolve_battle_request(self.request_id, "cancelled")
        if not result["ok"]:
            await game_shared.respond(
                interaction, game_shared.error_message(result["error"])
            )
            return

        await _delete_request_message(interaction.client, result)

        opponent_row = get_guild(int(request_row["to_guild_id"]))
        if opponent_row is not None:
            await _notify_guild_master_channel(
                interaction.client,
                opponent_row,
                title="⚔ ギルドバトル申請の取消",
                description=f"**{guild_row['name']}** がバトル申請を取り消しました。",
                color=config.COLOR_GREY,
            )

        await game_shared.respond(interaction, "バトル申請を取り消しました。")


async def _delete_request_message(bot: discord.Client, payload: dict) -> None:
    """回答済み・取消済みの申請Embedを削除する。"""

    channel_id = payload.get("channel_id")
    message_id = payload.get("message_id")
    if not channel_id or not message_id:
        return

    channel = bot.get_channel(int(channel_id))
    if not isinstance(channel, discord.TextChannel):
        return

    try:
        message = await channel.fetch_message(int(message_id))
        await message.delete()
    except discord.NotFound:
        return
    except (discord.HTTPException, discord.Forbidden):
        logger.warning(f"バトル申請Embedの削除に失敗しました: message_id={message_id}")


async def _notify_guild_master_channel(
    bot: discord.Client,
    guild_row: dict,
    *,
    title: str,
    description: str,
    color: int,
) -> None:
    """ギルドマスター専用TCへ通知を送る。"""

    channel_id = guild_row.get("master_text_channel_id")
    if not channel_id:
        return

    channel = bot.get_channel(int(channel_id))
    if not isinstance(channel, discord.TextChannel):
        return

    embed = discord.Embed(title=title, description=description, color=color)

    try:
        await channel.send(embed=embed)
    except (discord.HTTPException, discord.Forbidden):
        logger.warning(f"ギルドへの通知に失敗しました: channel_id={channel_id}")


# ==================================================
# 公開バトル募集（12.2節）
# ==================================================
class RecruitmentCancelView(ConfirmView):
    """公開バトル募集を取り消す一時View。"""

    def __init__(self, recruitment_id: int) -> None:
        super().__init__(confirm_label="募集を取り消す")

        self.recruitment_id = recruitment_id

    async def on_confirm(self, interaction: discord.Interaction) -> None:
        recruitment = get_battle_recruitment(self.recruitment_id)
        if recruitment is None:
            await game_shared.respond(interaction, game_shared.error_message("not_open"))
            return

        guild_row = get_guild(int(recruitment["guild_id"]))
        if guild_row is None or guild_row["master_id"] != interaction.user.id:
            await game_shared.respond(interaction, game_shared.error_message("not_master"))
            return

        result = resolve_battle_recruitment(self.recruitment_id, "cancelled")
        if not result["ok"]:
            await game_shared.respond(
                interaction, game_shared.error_message(result["error"])
            )
            return

        await close_recruitment_message(
            interaction.client, result, state_text="募集取消"
        )
        await game_shared.respond(interaction, "バトル募集を取り消しました。")


def recruitment_embed(guild_row: dict, *, state_text: str) -> discord.Embed:
    """12.2節の募集Embedを組み立てる。"""

    master = load_master_data()

    embed = discord.Embed(
        title="⚔ GUILD BATTLE",
        description=f"「**{guild_row['name']}**」が対戦ギルドを募集しています。",
        color=config.COLOR_PURPLE,
    )
    embed.add_field(name="出場人数", value=f"{master.battle.team_size}人", inline=True)
    embed.add_field(name="状態", value=state_text, inline=True)
    return embed


async def close_recruitment_message(
    bot: discord.Client, payload: dict, *, state_text: str
) -> None:
    """募集Embedを残したまま状態を更新し、ボタンを無効化する（12.2節）。"""

    channel_id = payload.get("channel_id")
    message_id = payload.get("message_id")
    if not channel_id or not message_id:
        return

    channel = bot.get_channel(int(channel_id))
    if not isinstance(channel, discord.TextChannel):
        return

    guild_row = get_guild(int(payload["guild_id"]))
    if guild_row is None:
        return

    view = BattleRecruitmentView()
    for item in view.children:
        item.disabled = True

    try:
        message = await channel.fetch_message(int(message_id))
        await message.edit(
            embed=recruitment_embed(guild_row, state_text=state_text), view=view
        )
    except discord.NotFound:
        return
    except (discord.HTTPException, discord.Forbidden):
        logger.warning(f"募集Embedの更新に失敗しました: message_id={message_id}")


# ==================================================
# バトル専用チャンネルの操作（16節・17節・19節）
# ==================================================
class ActionChoiceView(discord.ui.View):
    """スキルをキャンセルした後に行動を選び直す一時View（19.2節）。"""

    def __init__(self, battle_id: int, unit_id: int) -> None:
        super().__init__(timeout=EPHEMERAL_TIMEOUT)

        self.battle_id = battle_id
        self.unit_id = unit_id

        skill = discord.ui.Button(label="特殊スキル", style=discord.ButtonStyle.primary)
        skill.callback = self._skill_callback
        self.add_item(skill)

        attack = discord.ui.Button(label="攻撃", style=discord.ButtonStyle.danger)
        attack.callback = self._attack_callback
        self.add_item(attack)

    async def _skill_callback(self, interaction: discord.Interaction) -> None:
        await open_skill_selection(interaction)

    async def _attack_callback(self, interaction: discord.Interaction) -> None:
        await open_attack_selection(interaction)


class AttackTargetView(PagedSelectView):
    """通常攻撃の対象を選ぶ一時View。"""

    def __init__(
        self,
        battle_id: int,
        unit_id: int,
        expected_seq: int,
        options: list[discord.SelectOption],
    ) -> None:
        super().__init__(options, placeholder="攻撃対象を選択")

        self.battle_id = battle_id
        self.unit_id = unit_id
        self.expected_seq = expected_seq

    async def on_choice(self, interaction: discord.Interaction, value: str) -> None:
        await interaction.response.defer(ephemeral=True)

        action = BattleAction(
            action_type=ACTION_ATTACK,
            actor_unit_id=self.unit_id,
            target_unit_id=int(value),
        )
        await _apply_and_report(
            interaction,
            self.battle_id,
            action,
            expected_seq=self.expected_seq,
            success_message="攻撃を実行しました。",
        )


class SkillSelectView(PagedSelectView):
    """使用するアクティブスキルを選ぶ一時View。"""

    def __init__(
        self,
        battle_id: int,
        unit_id: int,
        expected_seq: int,
        options: list[discord.SelectOption],
    ) -> None:
        super().__init__(options, placeholder="使用するスキルを選択")

        self.battle_id = battle_id
        self.unit_id = unit_id
        self.expected_seq = expected_seq

    async def on_choice(self, interaction: discord.Interaction, value: str) -> None:
        master = load_master_data()
        skill = master.get_skill(value)
        if skill is None:
            await game_shared.respond(interaction, "スキル定義が見つかりません。")
            return

        flow = SkillTargetFlow(
            battle_id=self.battle_id,
            unit_id=self.unit_id,
            expected_seq=self.expected_seq,
            skill=skill,
        )
        await flow.advance(interaction)


class SkillTargetFlow:
    """スキルの対象選択を、グループごとに順番へ分解して進める。

    ロキ「虚実反転」のように対象グループが2つあるスキルや、スルト「終末の進軍」の
    ように同じ対象を複数回選べるグループ（``allow_duplicate``）にも対応します。
    """

    def __init__(self, *, battle_id: int, unit_id: int, expected_seq: int, skill) -> None:
        self.battle_id = battle_id
        self.unit_id = unit_id
        self.expected_seq = expected_seq
        self.skill = skill
        self.groups = list(skill.targets)
        self.group_index = 0
        self.pick_index = 0
        self.selections: dict[str, list[int]] = {}

    # ==================================================
    # 進行
    # ==================================================
    def _current_group(self):
        if self.group_index >= len(self.groups):
            return None
        return self.groups[self.group_index]

    def _record(self, group, unit_ids: list[int]) -> None:
        """選んだ対象を記録する。

        前段階のSelectは利用者の画面に残るため、同じグループを選び直せます。
        追加ではなく現在位置への上書きにして、選択数が ``group.count`` を
        超えないようにします（超えると使用確定時に検証エラーになり、
        スキルを使えなくなる）。
        """

        if group.allow_duplicate:
            # 1体ずつ複数回選ぶスキル（終末の進軍）は、今の順番から入れ直す
            chosen = list(self.selections.get(group.key, []))[: self.pick_index]
            chosen.extend(unit_ids)
            self.selections[group.key] = chosen
            return

        self.selections[group.key] = list(unit_ids)

    async def advance(self, interaction: discord.Interaction) -> None:
        """次の対象選択、または最終確認へ進む。"""

        group = self._current_group()
        if group is None:
            await self._show_confirm(interaction)
            return

        state = load_battle_state(self.battle_id)
        unit = state.unit(self.unit_id) if state else None
        if state is None or unit is None:
            await game_shared.respond(interaction, "バトル情報を取得できませんでした。")
            return

        candidates = battle_engine.selectable_targets(state, unit, group)
        if not candidates:
            await game_shared.respond(interaction, "選択できる対象がいません。")
            return

        if group.allow_duplicate:
            need = 1
            label = f"{self.skill.name}：対象{self.pick_index + 1}体目"
        else:
            need = min(group.count, len(candidates))
            label = f"{self.skill.name}：対象を{need}体選択"

        options = [_unit_option(candidate) for candidate in candidates]
        view = SkillTargetSelectView(self, group, options, need=need, label=label)

        await game_shared.respond(interaction, label, view=view)

    async def submit_group(
        self, interaction: discord.Interaction, group, unit_ids: list[int]
    ) -> None:
        """1回分の選択を記録し、次の段階へ進む。"""

        self._record(group, unit_ids)

        if group.allow_duplicate:
            self.pick_index += 1
            if self.pick_index < group.count:
                await self.advance(interaction)
                return

        self.group_index += 1
        self.pick_index = 0
        await self.advance(interaction)

    async def _show_confirm(self, interaction: discord.Interaction) -> None:
        state = load_battle_state(self.battle_id)
        if state is None:
            await game_shared.respond(interaction, "バトル情報を取得できませんでした。")
            return

        lines = [self.skill.description]
        for group in self.groups:
            names = [
                battle_embed.unit_name(state, unit_id)
                for unit_id in self.selections.get(group.key, [])
            ]
            if names:
                lines.append(f"{group.key}：{'・'.join(names)}")

        embed = discord.Embed(
            title=f"✦ SKILL「{self.skill.name}」を使用しますか？",
            description="\n".join(lines)[:2000],
            color=battle_embed.COLOR_SKILL,
        )

        await game_shared.respond(
            interaction, embed=embed, view=SkillConfirmView(self)
        )

    async def execute(self, interaction: discord.Interaction) -> None:
        """最終確認後にスキルを実行する。"""

        action = BattleAction(
            action_type=ACTION_SKILL,
            actor_unit_id=self.unit_id,
            skill_id=self.skill.skill_id,
            selections={
                key: tuple(values) for key, values in self.selections.items()
            },
        )

        applied = await _apply_and_report(
            interaction,
            self.battle_id,
            action,
            expected_seq=self.expected_seq,
            success_message=f"スキル「{self.skill.name}」を使用しました。",
        )

        if not applied or self.skill.consumes_attack:
            return

        # 攻撃権を消費しないスキルの後は、続けて攻撃を選ばせる（19.2節）
        await open_attack_selection(interaction, notice="続けて攻撃対象を選んでください。")


class SkillTargetSelectView(discord.ui.View):
    """スキルの対象を選ぶ一時View。"""

    def __init__(
        self,
        flow: SkillTargetFlow,
        group,
        options: list[discord.SelectOption],
        *,
        need: int,
        label: str,
    ) -> None:
        super().__init__(timeout=EPHEMERAL_TIMEOUT)

        self.flow = flow
        self.group = group

        select = discord.ui.Select(
            placeholder=label[:150],
            min_values=need,
            max_values=need,
            options=options[:SELECT_LIMIT],
        )
        select.callback = self._select_callback
        self.add_item(select)
        self._select = select

        cancel = discord.ui.Button(label="やめる", style=discord.ButtonStyle.secondary)
        cancel.callback = self._cancel_callback
        self.add_item(cancel)

    async def _select_callback(self, interaction: discord.Interaction) -> None:
        unit_ids = [int(value) for value in self._select.values]
        await self.flow.submit_group(interaction, self.group, unit_ids)

    async def _cancel_callback(self, interaction: discord.Interaction) -> None:
        await game_shared.respond(
            interaction,
            "スキルの使用をやめました。行動を選び直してください。",
            view=ActionChoiceView(self.flow.battle_id, self.flow.unit_id),
        )


class SkillConfirmView(discord.ui.View):
    """スキル使用の最終確認（19.2節）。キャンセルで行動選択へ戻る。"""

    def __init__(self, flow: SkillTargetFlow) -> None:
        super().__init__(timeout=EPHEMERAL_TIMEOUT)

        self.flow = flow

        confirm = discord.ui.Button(label="使用する", style=discord.ButtonStyle.primary)
        confirm.callback = self._confirm_callback
        self.add_item(confirm)

        cancel = discord.ui.Button(label="やめる", style=discord.ButtonStyle.secondary)
        cancel.callback = self._cancel_callback
        self.add_item(cancel)

    async def _confirm_callback(self, interaction: discord.Interaction) -> None:
        for item in self.children:
            item.disabled = True

        await interaction.response.defer(ephemeral=True)
        await self.flow.execute(interaction)

    async def _cancel_callback(self, interaction: discord.Interaction) -> None:
        await game_shared.respond(
            interaction,
            "スキルの使用をやめました。行動を選び直してください。",
            view=ActionChoiceView(self.flow.battle_id, self.flow.unit_id),
        )


async def open_attack_selection(
    interaction: discord.Interaction, *, notice: str | None = None
) -> None:
    """通常攻撃の対象選択を開く。"""

    channel = interaction.channel
    if channel is None:
        await game_shared.respond(interaction, "チャンネル情報を取得できませんでした。")
        return

    battle_row, state, unit, error = load_actor(channel.id, interaction.user.id)
    if error is not None:
        await game_shared.respond(interaction, error)
        return

    choices = battle_engine.attack_target_choices(state, unit)
    if not choices:
        await game_shared.respond(interaction, "攻撃できる対象がいません。")
        return

    options = [_unit_option(choice) for choice in choices]
    view = AttackTargetView(
        int(battle_row["battle_id"]), unit.battle_unit_id, state.action_seq, options
    )

    await game_shared.respond(
        interaction, notice or "攻撃対象を選んでください。", view=view
    )


async def open_skill_selection(interaction: discord.Interaction) -> None:
    """アクティブスキルの選択を開く。"""

    channel = interaction.channel
    if channel is None:
        await game_shared.respond(interaction, "チャンネル情報を取得できませんでした。")
        return

    battle_row, state, unit, error = load_actor(channel.id, interaction.user.id)
    if error is not None:
        await game_shared.respond(interaction, error)
        return

    if unit.state_flags.get("skill_used_this_turn"):
        await game_shared.respond(interaction, "このターンは既にスキルを使用しています。")
        return

    skills = battle_engine.available_skills(state, unit)
    if not skills:
        await game_shared.respond(interaction, "現在使用できるスキルがありません。")
        return

    options = []
    for skill in skills:
        used = int(unit.active_skill_uses.get(skill.skill_id, 0))
        limit = skill.max_uses_per_battle
        remaining = "回数無制限" if limit is None else f"残り{limit - used}回"
        options.append(
            discord.SelectOption(
                label=skill.name[:100],
                description=f"{remaining}　{skill.description}"[:100],
                value=skill.skill_id,
            )
        )

    view = SkillSelectView(
        int(battle_row["battle_id"]), unit.battle_unit_id, state.action_seq, options
    )
    await game_shared.respond(interaction, "使用するスキルを選んでください。", view=view)


# ==================================================
# 降参（26.1節）
# ==================================================
class SurrenderConfirmView(ConfirmView):
    """降参の最終確認。"""

    def __init__(self, guild_id: int, battle_id: int) -> None:
        super().__init__(confirm_label="降参する")

        self.guild_id = guild_id
        self.battle_id = battle_id

    async def on_confirm(self, interaction: discord.Interaction) -> None:
        guild_row = get_guild(self.guild_id)
        if guild_row is None or guild_row["master_id"] != interaction.user.id:
            await game_shared.respond(interaction, game_shared.error_message("not_master"))
            return

        try:
            applied = await service.apply_action(
                interaction.client,
                self.battle_id,
                service.surrender_action(self.guild_id),
                elapsed_seconds=0,
                expected_seq=None,
            )
        except BattleRuleError as exc:
            await game_shared.respond(interaction, str(exc))
            return

        if not applied:
            await game_shared.respond(interaction, "降参を処理できませんでした。")
            return

        await game_shared.game_admin_log(
            interaction.client,
            action="ギルドバトル降参",
            executor_id=interaction.user.id,
            target_guild_id=self.guild_id,
            target_battle_id=self.battle_id,
        )
        await game_shared.respond(interaction, "降参しました。")


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
        label="メンバーセット",
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
            await game_shared.respond(interaction, "所属メンバーがいません。")
            return

        await game_shared.respond(
            interaction,
            "バトルへ出場するメンバーを選んでください。",
            view=RosterSelectView(guild_row, members),
        )

    # ==================================================
    # セット確認（11節）
    # ==================================================
    @discord.ui.button(
        label="セット確認",
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
        label="バトル申請",
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
                name = opponent["name"] if opponent else "相手ギルド"
                await game_shared.respond(
                    interaction,
                    f"**{name}** へ申請中です。取り消せます。",
                    view=RequestCancelView(int(pending["request_id"])),
                )
            else:
                await game_shared.respond(
                    interaction,
                    "受信中のバトル申請があります。先に承認または拒否してください。",
                )
            return

        lock = get_battle_lock(guild_id)
        if lock is not None:
            await game_shared.respond(
                interaction, game_shared.error_message("guild_busy")
            )
            return

        options = []
        for candidate in get_active_guilds():
            candidate_id = int(candidate["guild_id"])
            if candidate_id == guild_id:
                continue
            if get_battle_lock(candidate_id) is not None:
                continue

            options.append(
                discord.SelectOption(
                    label=candidate["name"][:100],
                    description=(
                        f"{candidate['wins']}勝 {candidate['losses']}敗 "
                        f"{candidate['draws']}分"
                    )[:100],
                    value=str(candidate_id),
                )
            )

        if not options:
            await game_shared.respond(interaction, "現在申し込めるギルドがありません。")
            return

        await game_shared.respond(
            interaction,
            "対戦を申し込むギルドを選んでください。",
            view=OpponentSelectView(guild_row, options),
        )

    # ==================================================
    # バトル募集（12.2節）
    # ==================================================
    @discord.ui.button(
        label="バトル募集",
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
                    "現在バトルを募集中です。取り消せます。",
                    view=RecruitmentCancelView(int(lock["reference_id"])),
                )
            else:
                await game_shared.respond(
                    interaction, game_shared.error_message("guild_busy")
                )
            return

        if not config.GUILD_BATTLE_RECRUITMENT_CHANNEL_ID:
            await game_shared.respond(
                interaction, "ギルドバトル募集チャンネルが設定されていません。"
            )
            return

        channel = interaction.client.get_channel(
            config.GUILD_BATTLE_RECRUITMENT_CHANNEL_ID
        )
        if not isinstance(channel, discord.TextChannel):
            await game_shared.respond(
                interaction, "ギルドバトル募集チャンネルが見つかりません。"
            )
            return

        await interaction.response.defer(ephemeral=True)

        result = create_battle_recruitment(guild_id)
        if not result["ok"]:
            await game_shared.respond(
                interaction, game_shared.error_message(result["error"])
            )
            return

        recruitment_id = int(result["recruitment_id"])
        message = await channel.send(
            embed=recruitment_embed(guild_row, state_text="対戦相手募集中"),
            view=BattleRecruitmentView(),
        )
        set_battle_recruitment_message(recruitment_id, channel.id, message.id)

        await game_shared.respond(
            interaction,
            f"{channel.mention} へバトル募集を投稿しました。",
        )

    # ==================================================
    # 降参（26.1節）
    # ==================================================
    @discord.ui.button(
        label="降参",
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
            await game_shared.respond(interaction, "進行中のバトルがありません。")
            return

        await game_shared.respond(
            interaction,
            (
                "本当に降参しますか？\n"
                "降参したギルドの敗北として戦績へ記録されます。"
            ),
            view=SurrenderConfirmView(guild_id, int(battle_row["battle_id"])),
        )


# ==================================================
# 常設View：バトル出場者専用TCのパネル
# ==================================================
class BattleMemberPanelView(discord.ui.View):
    """出場者が持ち込む使い魔をセットするパネル。"""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="使い魔セット",
        style=discord.ButtonStyle.primary,
        custom_id="guild_battle:set_familiar",
    )
    async def set_familiar(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
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
        if not any(row["user_id"] == interaction.user.id for row in roster):
            await game_shared.respond(
                interaction, game_shared.error_message("not_selected")
            )
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
            await game_shared.respond(
                interaction,
                "プレイヤーランクを確認できませんでした。運営へ連絡してください。",
            )
            return

        master = load_master_data()
        options = []
        for owned in get_owned_familiars(interaction.user.id):
            familiar = master.get_familiar(owned["familiar_id"])
            if familiar is None:
                continue

            if not master.can_use_rank(
                rank_info["player_rank"],
                familiar.rank,
                is_sub_manager=rank_info["is_sub_manager"],
            ):
                continue

            stats = master.level_stats(familiar.familiar_id, int(owned["level"]))
            description = (
                f"HP {stats.max_hp}　ATK {stats.atk}　SPD {stats.speed}"
                if stats
                else familiar.description
            )
            options.append(
                discord.SelectOption(
                    label=(
                        f"{game_shared.rank_label(familiar.rank)} "
                        f"{familiar.name} Lv.{owned['level']}"
                    )[:100],
                    description=description[:100],
                    value=str(owned["instance_id"]),
                )
            )

        if not options:
            await game_shared.respond(
                interaction, "使役できる使い魔を所有していません。"
            )
            return

        await game_shared.respond(
            interaction,
            "バトルで使用する使い魔を選んでください。",
            view=RosterFamiliarSelectView(guild_id, options),
        )


# ==================================================
# 常設View：バトル申請Embed（12.1節）
# ==================================================
class BattleRequestView(discord.ui.View):
    """受け取ったバトル申請へ回答するボタン。"""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    def _resolve(self, interaction: discord.Interaction) -> tuple[dict | None, str | None]:
        """メッセージIDから申請を特定し、押せる相手かを確認する。"""

        if not game_shared.is_game_enabled():
            return None, DISABLED_MESSAGE

        if interaction.message is None:
            return None, "申請情報を取得できませんでした。"

        request_row = get_battle_request_by_message(interaction.message.id)
        if request_row is None:
            return None, "この申請は見つかりませんでした。"

        if request_row["status"] != "pending":
            return None, game_shared.error_message("not_pending")

        guild_row = get_guild(int(request_row["to_guild_id"]))
        if guild_row is None or guild_row["status"] != "active":
            return None, game_shared.error_message("guild_not_found")

        if guild_row["master_id"] != interaction.user.id:
            return None, game_shared.error_message("not_master")

        return request_row, None

    @discord.ui.button(
        label="承認",
        style=discord.ButtonStyle.success,
        custom_id="guild_battle:request_approve",
    )
    async def approve(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        request_row, error = self._resolve(interaction)
        if error is not None:
            await game_shared.respond(interaction, error)
            return

        await interaction.response.defer(ephemeral=True)

        result = resolve_battle_request(int(request_row["request_id"]), "approved")
        if not result["ok"]:
            await game_shared.respond(
                interaction, game_shared.error_message(result["error"])
            )
            return

        await _delete_request_message(interaction.client, result)
        await game_shared.respond(
            interaction, "申請を承認しました。開始前チェックを行います。"
        )

        await service.try_start_battle(
            interaction.client,
            int(request_row["from_guild_id"]),
            int(request_row["to_guild_id"]),
        )

    @discord.ui.button(
        label="拒否",
        style=discord.ButtonStyle.danger,
        custom_id="guild_battle:request_reject",
    )
    async def reject(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        request_row, error = self._resolve(interaction)
        if error is not None:
            await game_shared.respond(interaction, error)
            return

        await interaction.response.defer(ephemeral=True)

        result = resolve_battle_request(int(request_row["request_id"]), "rejected")
        if not result["ok"]:
            await game_shared.respond(
                interaction, game_shared.error_message(result["error"])
            )
            return

        await _delete_request_message(interaction.client, result)

        from_guild = get_guild(int(request_row["from_guild_id"]))
        to_guild = get_guild(int(request_row["to_guild_id"]))
        if from_guild is not None:
            await _notify_guild_master_channel(
                interaction.client,
                from_guild,
                title="⚔ ギルドバトル申請の結果",
                description=(
                    f"**{to_guild['name'] if to_guild else '相手ギルド'}** が"
                    "バトル申請を拒否しました。"
                ),
                color=config.COLOR_RED,
            )

        await game_shared.respond(interaction, "申請を拒否しました。")


# ==================================================
# 常設View：公開バトル募集Embed（12.2節）
# ==================================================
class BattleRecruitmentView(discord.ui.View):
    """公開バトル募集へ申し込むボタン。"""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="対戦申請",
        style=discord.ButtonStyle.success,
        custom_id="guild_battle:recruit_apply",
    )
    async def apply(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if not game_shared.is_game_enabled():
            await game_shared.respond(interaction, DISABLED_MESSAGE)
            return

        if interaction.message is None:
            await game_shared.respond(interaction, "募集情報を取得できませんでした。")
            return

        recruitment = get_battle_recruitment_by_message(interaction.message.id)
        if recruitment is None:
            await game_shared.respond(interaction, "この募集は見つかりませんでした。")
            return

        if recruitment["status"] != "open":
            await game_shared.respond(interaction, game_shared.error_message("already_matched"))
            return

        challenger = get_player_guild(interaction.user.id)
        if challenger is None or challenger["master_id"] != interaction.user.id:
            await game_shared.respond(
                interaction, "ギルドマスターだけが対戦を申し込めます。"
            )
            return

        challenger_id = int(challenger["guild_id"])
        if challenger_id == int(recruitment["guild_id"]):
            await game_shared.respond(interaction, game_shared.error_message("same_guild"))
            return

        await interaction.response.defer(ephemeral=True)

        # 12.2節：開始前チェックを通過できないギルドは対戦相手として確定させず、
        # その理由を申込者へ表示する。募集を消費してしまわないよう先に確認する。
        problems, _ = service.check_guild_ready(interaction.client, challenger_id)
        if problems:
            await game_shared.respond(
                interaction,
                "次の条件を満たしていないため申し込めません。\n"
                + "\n".join(f"・{problem}" for problem in problems),
            )
            return

        result = claim_battle_recruitment(
            int(recruitment["recruitment_id"]), challenger_id
        )
        if not result["ok"]:
            await game_shared.respond(
                interaction, game_shared.error_message(result["error"])
            )
            return

        await close_recruitment_message(interaction.client, result, state_text="募集終了")
        await game_shared.respond(
            interaction, "対戦が成立しました。開始前チェックを行います。"
        )

        await service.try_start_battle(
            interaction.client, int(result["guild_id"]), challenger_id
        )


# ==================================================
# 常設View：バトル専用チャンネル（16節）
# ==================================================
class BattleCommandView(discord.ui.View):
    """ターン通知に付ける行動ボタン。押せるのは現在の行動者本人だけ。"""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="特殊スキル",
        style=discord.ButtonStyle.primary,
        custom_id="guild_battle:skill",
    )
    async def use_skill(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await open_skill_selection(interaction)

    @discord.ui.button(
        label="攻撃",
        style=discord.ButtonStyle.danger,
        custom_id="guild_battle:attack",
    )
    async def attack(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await open_attack_selection(interaction)


# ==================================================
# 常設View：ランキングパネル（26.2節）
# ==================================================
class BattleRankingPanelView(discord.ui.View):
    """ギルドバトルの通算ランキングを表示するパネル。"""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="ランキング",
        style=discord.ButtonStyle.primary,
        custom_id="guild_battle:ranking",
    )
    async def ranking(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if not game_shared.is_game_enabled():
            await game_shared.respond(interaction, DISABLED_MESSAGE)
            return

        await game_shared.respond(
            interaction, embed=build_ranking_embed(interaction.user.id)
        )


# ==================================================
# Embed・パネルの組み立て
# ==================================================
def build_roster_embed(guild_id: int) -> discord.Embed:
    """11節の編成確認Embedを組み立てる。"""

    master = load_master_data()
    guild_row = get_guild(guild_id)
    roster = get_battle_roster(guild_id)

    lines: list[str] = []
    ready_count = 0

    for index in range(master.battle.team_size):
        mark = (
            battle_embed.SLOT_MARKS[index]
            if index < len(battle_embed.SLOT_MARKS)
            else f"{index + 1}."
        )

        if index >= len(roster):
            lines.append(f"{mark} 未選択\n使い魔：未設定\n準備：❌")
            continue

        member = roster[index]
        instance_id = member.get("instance_id")
        owned = get_owned_familiar(int(instance_id)) if instance_id else None

        if owned is None or owned["status"] != "owned":
            familiar_text = "未設定"
            ready = "❌"
        else:
            familiar_text = (
                f"{_familiar_name(owned['familiar_id'])} Lv.{owned['level']}"
            )
            ready = "✅"
            ready_count += 1

        lines.append(
            f"{mark} <@{member['user_id']}>\n使い魔：{familiar_text}\n準備：{ready}"
        )

    embed = discord.Embed(
        title="【ギルドバトル編成】",
        description="\n\n".join(lines)[:4000],
        color=config.COLOR_BLUE,
    )
    embed.set_footer(
        text=(
            f"{guild_row['name'] if guild_row else ''}　"
            f"準備完了 {ready_count}/{master.battle.team_size}人"
        )
    )
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
            (
                f"{row['rank']}. **{row['name']}**　{row['points']}点"
                f"（{row['wins']}勝 {row['losses']}敗 {row['draws']}分）"
            )
            for row in ranking
        ]
        description = "\n".join(lines)[:4000]
    else:
        description = "まだランキング対象のギルドがありません。"

    embed = discord.Embed(
        title="🏆 ギルドバトルランキング",
        description=description,
        color=config.COLOR_GOLD,
    )
    embed.set_footer(
        text=(
            f"勝利{ranking_balance.win_points}点 / "
            f"引き分け{ranking_balance.draw_points}点 / "
            f"敗北{ranking_balance.lose_points}点"
        )
    )

    own_guild = get_player_guild(user_id)
    if own_guild is not None:
        position = get_guild_ranking_position(
            int(own_guild["guild_id"]),
            win_points=ranking_balance.win_points,
            draw_points=ranking_balance.draw_points,
            lose_points=ranking_balance.lose_points,
        )
        position_text = f"{position}位" if position else "順位なし"
        embed.add_field(
            name="あなたのギルド",
            value=f"**{own_guild['name']}**　{position_text}",
            inline=False,
        )

    return embed


def battle_panel_embed() -> discord.Embed:
    """ギルドマスター専用TCへ置くバトルパネル（8.2節）。"""

    master = load_master_data()

    return discord.Embed(
        title=BATTLE_PANEL_TITLE,
        description=(
            "\u200b\n"
            f"**{master.battle.team_size}対{master.battle.team_size}のギルドバトル**\n"
            "-# メンバーセット → 使い魔セット → 申請または募集の順に進めます。\n"
            "-# 進行中バトルは「降参」で終了できます。"
        ),
        color=config.COLOR_PURPLE,
    )


def roster_panel_embed() -> discord.Embed:
    """バトル出場者専用TCへ置くパネル。"""

    return discord.Embed(
        title=ROSTER_PANEL_TITLE,
        description=(
            "\u200b\n"
            "**バトルで使用する使い魔をセットしてください**\n"
            "-# 使役できるランクはプレイヤーランクによって決まります。\n"
            "-# 編成ロック中は変更できません。"
        ),
        color=config.COLOR_BLUE,
    )


def ranking_panel_embed() -> discord.Embed:
    """ギルドバトル募集チャンネルへ置くランキングパネル。"""

    return discord.Embed(
        title=RANKING_PANEL_TITLE,
        description=(
            "\u200b\n"
            "**ギルドバトルの通算成績**\n"
            "-# ボタンを押すと上位ギルドと自分のギルド順位を表示します。"
        ),
        color=config.COLOR_GOLD,
    )


async def ensure_roster_panel(
    bot: discord.Client, guild: discord.Guild, guild_row: dict
) -> None:
    """バトル出場者専用TCへ使い魔セット用パネルを（無ければ）設置する。"""

    channel_id = guild_row.get("battle_member_channel_id")
    if not channel_id:
        return

    await ensure_panel_message(
        bot,
        guild,
        int(channel_id),
        panel_title=ROSTER_PANEL_TITLE,
        embed=roster_panel_embed(),
        view=BattleMemberPanelView(),
        panel_name="バトル出場者パネル",
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
