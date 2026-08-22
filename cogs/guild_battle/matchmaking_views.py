"""対戦相手を決めるまで（バトルレート・バトル申請・公開バトル募集）。

12節。相手ギルドへ届く申請Embedと募集Embedは常設Viewなので、``custom_id`` を
変えると既に貼ってあるボタンが無反応になります。
"""

from __future__ import annotations

import logging

import discord
import config

from cogs import game_shared
from database.battle import (
    claim_battle_recruitment,
    create_battle_request,
    get_battle_lock,
    get_battle_recruitment,
    get_battle_recruitment_by_message,
    get_battle_request,
    get_battle_request_by_message,
    get_battle_roster,
    resolve_battle_recruitment,
    resolve_battle_request,
    set_battle_request_message,
)
from database.guild import get_active_guilds, get_guild, get_player_guild
from game.master_data import load_master_data

from . import service
from .battle_common import EPHEMERAL_TIMEOUT, ConfirmView, PagedSelectView
from texts import battle as battle_texts
from texts import common as common_texts


logger = logging.getLogger(__name__)


# ==================================================
# バトルレートの選択（12節）
# ==================================================
def bet_rate_option(rate) -> discord.SelectOption:
    """レート1件をセレクトの選択肢へ変換する。

    説明欄には「いくら賭けるのか」と「勝った側が受け取ること」を必ず出します。
    選ぶ前に負担と見返りが分かるようにするためです。
    """

    return discord.SelectOption(
        label=rate.name[:100],
        description=battle_texts.BET_RATE_OPTION_DESCRIPTION.format(
            coin=game_shared.format_coin(rate.coin)
        )[:100],
        value=rate.rate_id,
    )


class BetRateSelectView(discord.ui.View):
    """バトルのレート（ベット額）を選ぶ一時View（12節）。

    選んだ時点で確定します。確認ボタンを挟まないのは、レートを選ぶこと自体が
    「この額で対戦相手を探す」という意思表示だからです。
    """

    def __init__(self, *, guild_row: dict, action: str, on_choose) -> None:
        super().__init__(timeout=EPHEMERAL_TIMEOUT)

        self.guild_row = guild_row
        self._on_choose = on_choose
        self._running = False

        select = discord.ui.Select(
            placeholder=battle_texts.BET_RATE_PLACEHOLDER.format(action=action)[:150],
            min_values=1,
            max_values=1,
            options=[bet_rate_option(rate) for rate in service.bet_rates()],
        )
        select.callback = self._select_callback
        self.add_item(select)
        self._select = select

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != int(self.guild_row["master_id"]):
            await game_shared.respond(
                interaction, game_shared.error_message("not_master")
            )
            return False

        return True

    async def _select_callback(self, interaction: discord.Interaction) -> None:
        if self._running:
            await game_shared.respond(interaction, common_texts.ALREADY_RUNNING)
            return

        rate = service.bet_rate(self._select.values[0])
        if rate is None:
            await game_shared.respond(interaction, battle_texts.BET_RATE_UNAVAILABLE)
            return

        self._running = True

        # 連打による二重投稿を防ぐため、選んだ直後に操作不能にする
        for item in self.children:
            item.disabled = True

        try:
            await interaction.response.edit_message(view=self)
        except discord.HTTPException:
            logger.warning("レート選択の更新に失敗しました")

        try:
            await self._on_choose(interaction, rate)
        finally:
            self.stop()


def bet_rate_guide(next_step: str) -> str:
    """レート選択の案内文を作る。``next_step`` は選んだ直後に起きること。"""

    return battle_texts.BET_RATE_GUIDE.format(next_step=next_step)


def opponent_options(guild_id: int) -> list[discord.SelectOption]:
    """バトル申請を送れる相手ギルドの選択肢を作る。"""

    options: list[discord.SelectOption] = []

    for candidate in get_active_guilds():
        candidate_id = int(candidate["guild_id"])
        if candidate_id == guild_id:
            continue
        if get_battle_lock(candidate_id) is not None:
            continue

        options.append(
            discord.SelectOption(
                label=candidate["name"][:100],
                description=battle_texts.OPPONENT_OPTION_RECORD.format(
                    wins=candidate["wins"],
                    losses=candidate["losses"],
                    draws=candidate["draws"],
                )[:100],
                value=str(candidate_id),
            )
        )

    return options


def bet_confirmation(guild_id: int, bet_coin: int) -> str:
    """決めたベット額を、1人あたりの分担額まで含めて説明する。"""

    roster = get_battle_roster(guild_id)

    return "\n".join(
        [
            game_shared.item_line(
                battle_texts.LABEL_BET, game_shared.format_coin(bet_coin)
            ),
            battle_texts.BET_SHARE_LINE.format(
                notice=service.bet_share_notice(bet_coin, len(roster))
            ),
            battle_texts.BET_TRANSFER_NOTE,
        ]
    )


# ==================================================
# バトル申請（12.1節）
# ==================================================
class OpponentSelectView(PagedSelectView):
    """バトル申請の相手ギルドを選ぶ一時View。"""

    def __init__(
        self,
        guild_row: dict,
        options: list[discord.SelectOption],
        *,
        bet_coin: int,
    ) -> None:
        super().__init__(options, placeholder=battle_texts.OPPONENT_SELECT_PLACEHOLDER)

        self.guild_row = guild_row
        self.bet_coin = bet_coin

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

        result = create_battle_request(
            guild_id, opponent_id, bet_coin=self.bet_coin
        )
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
                interaction, battle_texts.REQUEST_NO_OPPONENT_CHANNEL
            )
            return

        embed = discord.Embed(
            title=battle_texts.REQUEST_EMBED_TITLE,
            description="\n".join(
                [
                    battle_texts.REQUEST_EMBED_INTRO.format(
                        guild_name=guild_row["name"]
                    ),
                    battle_texts.REQUEST_EMBED_NOTE,
                    "",
                    game_shared.item_line(
                        battle_texts.LABEL_REQUEST_FROM, guild_row["name"]
                    ),
                    game_shared.item_line(
                        battle_texts.LABEL_REQUEST_TO, opponent_row["name"]
                    ),
                    game_shared.item_line(
                        battle_texts.LABEL_RATE,
                        service.bet_rate_label(self.bet_coin) or battle_texts.DASH,
                    ),
                    game_shared.item_line(
                        battle_texts.LABEL_BET,
                        battle_texts.BET_PER_GUILD.format(
                            coin=game_shared.format_coin(self.bet_coin)
                        ),
                    ),
                    "",
                    battle_texts.REQUEST_EMBED_FOOTER,
                ]
            ),
            color=config.COLOR_PURPLE,
        )

        message = await channel.send(embed=embed, view=BattleRequestView())
        set_battle_request_message(request_id, channel.id, message.id)

        await game_shared.respond(
            interaction,
            battle_texts.REQUEST_SENT.format(
                guild_name=opponent_row["name"],
                bet=bet_confirmation(guild_id, self.bet_coin),
            ),
        )


class RequestCancelView(ConfirmView):
    """送信済みバトル申請を取り消す一時View。"""

    def __init__(self, request_id: int) -> None:
        super().__init__(confirm_label=battle_texts.REQUEST_CANCEL_BUTTON)

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
            # 取消はメンバー全員に関わるためギルドTCへ送る
            await _notify_guild_channel(
                interaction.client,
                opponent_row,
                title=battle_texts.REQUEST_CANCELLED_TITLE,
                description=battle_texts.REQUEST_CANCELLED_BODY.format(
                    guild_name=guild_row["name"]
                ),
                color=config.COLOR_GREY,
                channel_key="guild_text_channel_id",
            )

        await game_shared.respond(interaction, battle_texts.REQUEST_CANCELLED_DONE)


async def _delete_posted_message(
    bot: discord.Client, payload: dict, *, label: str
) -> None:
    """役目を終えた投稿（申請Embed・募集Embed）を削除する。

    ``payload`` は ``channel_id`` と ``message_id`` を持つDBの戻り値です。
    """

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
        logger.warning(f"{label}の削除に失敗しました: message_id={message_id}")


async def _delete_request_message(bot: discord.Client, payload: dict) -> None:
    """回答済み・取消済みの申請Embedを削除する。"""

    await _delete_posted_message(bot, payload, label="バトル申請Embed")


async def _notify_guild_channel(
    bot: discord.Client,
    guild_row: dict,
    *,
    title: str,
    description: str,
    color: int,
    channel_key: str = "master_text_channel_id",
) -> None:
    """ギルドの指定チャンネルへ通知を送る。

    既定はギルドマスター専用TCですが、メンバー全員へ知らせたい内容は
    ``channel_key="guild_text_channel_id"`` でギルドTCへ送ります。
    """

    channel_id = guild_row.get(channel_key)
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
        super().__init__(confirm_label=battle_texts.RECRUIT_CANCEL_BUTTON)

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

        # 取り消した募集は残しても申し込めないため、投稿ごと片づける
        await _delete_recruitment_message(interaction.client, result)
        await game_shared.respond(interaction, battle_texts.RECRUIT_CANCELLED_DONE)


def recruitment_embed(
    guild_row: dict, *, state_text: str, bet_coin: int | None = None
) -> discord.Embed:
    """12.2節の募集Embedを組み立てる。"""

    master = load_master_data()
    amount = service.default_bet_coin() if bet_coin is None else int(bet_coin)
    rate_name = service.bet_rate_label(amount)

    return discord.Embed(
        title=battle_texts.RECRUIT_EMBED_TITLE,
        description="\n".join(
            [
                battle_texts.RECRUIT_EMBED_INTRO.format(
                    guild_name=guild_row["name"]
                ),
                "",
                game_shared.item_line(
                    battle_texts.LABEL_MEMBER_RANGE,
                    battle_texts.RECRUIT_MEMBER_RANGE.format(
                        min_members=master.battle.min_members,
                        max_members=master.battle.max_members,
                    ),
                ),
                game_shared.item_line(
                    battle_texts.LABEL_RATE, rate_name or battle_texts.DASH
                ),
                game_shared.item_line(
                    battle_texts.LABEL_BET,
                    battle_texts.BET_PER_GUILD.format(
                        coin=game_shared.format_coin(amount)
                    ),
                ),
                game_shared.item_line(battle_texts.LABEL_STATE, state_text),
                "",
                battle_texts.RECRUIT_EMBED_FOOTER,
            ]
        ),
        color=config.COLOR_PURPLE,
    )


async def _delete_recruitment_message(bot: discord.Client, payload: dict) -> None:
    """取り消した募集の投稿を削除する（12.2節）。"""

    await _delete_posted_message(bot, payload, label="募集Embed")


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
            embed=recruitment_embed(
                guild_row,
                state_text=state_text,
                bet_coin=payload.get("bet_coin"),
            ),
            view=view,
        )
    except discord.NotFound:
        return
    except (discord.HTTPException, discord.Forbidden):
        logger.warning(f"募集Embedの更新に失敗しました: message_id={message_id}")


# ==================================================
# 常設View：バトル申請Embed（12.1節）
# ==================================================
class BattleRequestView(discord.ui.View):
    """受け取ったバトル申請へ回答するボタン。"""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    def _resolve(self, interaction: discord.Interaction) -> tuple[dict | None, str | None]:
        """メッセージIDから申請を特定し、押せる相手かを確認する。"""

        blocked = game_shared.game_block_reason(interaction.user)
        if blocked is not None:
            return None, blocked

        if interaction.message is None:
            return None, battle_texts.REQUEST_LOAD_ERROR

        request_row = get_battle_request_by_message(interaction.message.id)
        if request_row is None:
            return None, battle_texts.REQUEST_NOT_FOUND

        if request_row["status"] != "pending":
            return None, game_shared.error_message("not_pending")

        guild_row = get_guild(int(request_row["to_guild_id"]))
        if guild_row is None or guild_row["status"] != "active":
            return None, game_shared.error_message("guild_not_found")

        if guild_row["master_id"] != interaction.user.id:
            return None, game_shared.error_message("not_master")

        return request_row, None

    @discord.ui.button(
        label=battle_texts.BUTTON_APPROVE,
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

        bet_coin = request_row.get("bet_coin")

        await _delete_request_message(interaction.client, result)
        await game_shared.respond(
            interaction,
            battle_texts.REQUEST_APPROVED.format(
                bet=bet_confirmation(
                    int(request_row["to_guild_id"]),
                    service.default_bet_coin() if bet_coin is None else int(bet_coin),
                )
            ),
        )

        started = await service.try_start_battle(
            interaction.client,
            int(request_row["from_guild_id"]),
            int(request_row["to_guild_id"]),
            bet_coin=bet_coin,
        )

        # 開始できなかった理由は、直した人にだけ見せれば足りる（13節）
        if not started["ok"] and started.get("message"):
            await game_shared.respond(interaction, started["message"])

    @discord.ui.button(
        label=battle_texts.BUTTON_REJECT,
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
            await _notify_guild_channel(
                interaction.client,
                from_guild,
                title=battle_texts.REQUEST_RESULT_TITLE,
                description=battle_texts.REQUEST_REJECTED_BODY.format(
                    guild_name=(
                        to_guild["name"]
                        if to_guild
                        else battle_texts.OPPONENT_FALLBACK
                    )
                ),
                color=config.COLOR_RED,
            )

        await game_shared.respond(interaction, battle_texts.REQUEST_REJECTED_DONE)


# ==================================================
# 常設View：公開バトル募集Embed（12.2節）
# ==================================================
class BattleRecruitmentView(discord.ui.View):
    """公開バトル募集へ申し込むボタン。"""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label=battle_texts.BUTTON_APPLY,
        style=discord.ButtonStyle.success,
        custom_id="guild_battle:recruit_apply",
    )
    async def apply(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        blocked = game_shared.game_block_reason(interaction.user)
        if blocked is not None:
            await game_shared.respond(interaction, blocked)
            return

        if interaction.message is None:
            await game_shared.respond(interaction, battle_texts.RECRUIT_LOAD_ERROR)
            return

        recruitment = get_battle_recruitment_by_message(interaction.message.id)
        if recruitment is None:
            await game_shared.respond(interaction, battle_texts.RECRUIT_NOT_FOUND)
            return

        if recruitment["status"] != "open":
            await game_shared.respond(interaction, game_shared.error_message("already_matched"))
            return

        challenger = get_player_guild(interaction.user.id)
        if challenger is None or challenger["master_id"] != interaction.user.id:
            await game_shared.respond(interaction, battle_texts.APPLY_MASTER_ONLY)
            return

        challenger_id = int(challenger["guild_id"])
        if challenger_id == int(recruitment["guild_id"]):
            await game_shared.respond(interaction, game_shared.error_message("same_guild"))
            return

        await interaction.response.defer(ephemeral=True)

        # 12.2節：開始前チェックを通過できないギルドは対戦相手として確定させず、
        # その理由を申込者へ表示する。募集を消費してしまわないよう先に確認する。
        issues = service.entry_blockers(interaction.client, challenger_id)
        if issues:
            await game_shared.respond(
                interaction,
                service.blocker_message(issues, action=battle_texts.ACTION_APPLY),
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

        bet_coin = result.get("bet_coin")
        if bet_coin is None:
            bet_coin = recruitment.get("bet_coin")

        await close_recruitment_message(
            interaction.client, result, state_text=battle_texts.RECRUIT_STATE_CLOSED
        )
        await game_shared.respond(
            interaction,
            battle_texts.MATCH_MADE.format(
                bet=bet_confirmation(
                    challenger_id,
                    service.default_bet_coin() if bet_coin is None else int(bet_coin),
                )
            ),
        )

        started = await service.try_start_battle(
            interaction.client,
            int(result["guild_id"]),
            challenger_id,
            bet_coin=bet_coin,
        )

        if not started["ok"] and started.get("message"):
            await game_shared.respond(interaction, started["message"])
