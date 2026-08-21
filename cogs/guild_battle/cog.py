"""ギルドバトルCog本体。

常設パネルの設置、進行中バトルの見張り（15秒ループ）、Bot再起動後の復旧を
担当します。ボタン操作は ``views.py``、バトルの保存と投稿は ``service.py`` です。
"""

from __future__ import annotations

import asyncio
import logging

import discord
import config

from discord import app_commands
from discord.ext import commands, tasks

from cogs import game_shared
from database.battle import get_active_battles
from database.guild import get_active_guilds
from utils import ensure_panel_message, remove_legacy_panels

from . import service, views


logger = logging.getLogger(__name__)

# 見張りタスクの間隔（秒）
WATCHDOG_INTERVAL_SECONDS = 15


# ==================================================
# ギルドバトルCog
# ==================================================
class GuildBattle(commands.Cog):
    """ギルドバトルのパネル設置・進行監視・復旧を行うCog。"""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

        # 未設定チャンネルの警告を毎回出さないためのフラグ
        self._ranking_channel_warned = False

        # 復旧が終わるまで見張りタスクを動かさないための合図（22節・29節）
        self._recovered = asyncio.Event()

        self.battle_recovery.start()
        self.battle_panels.start()
        self.battle_watchdog.start()
        self.purge_battle_channels.start()

    def cog_unload(self) -> None:
        self.battle_recovery.cancel()
        self.battle_panels.cancel()
        self.battle_watchdog.cancel()
        self.purge_battle_channels.cancel()

    # ==================================================
    # ギルド設立時のバトルパネル設置（ギルドCogから呼ばれる）
    # ==================================================
    async def ensure_battle_panel(self, guild_row: dict) -> None:
        """指定ギルドのマスター専用TCへバトルパネルを設置する（8.2節）。"""

        guild = self.bot.get_guild(config.GUILD_ID)
        if guild is None:
            return

        await views.ensure_battle_panel(self.bot, guild, guild_row)
        await views.ensure_roster_panel(self.bot, guild, guild_row)

    # ==================================================
    # 常設パネルの自動設置
    # ==================================================
    @tasks.loop(minutes=5)
    async def battle_panels(self) -> None:
        """ランキングパネルと各ギルドのバトル関連パネルを設置し直す。"""

        if not game_shared.is_game_enabled():
            return

        guild = self.bot.get_guild(config.GUILD_ID)
        if guild is None:
            return

        await self._ensure_ranking_panel(guild)
        await self._remove_legacy_panels(guild)

        for guild_row in get_active_guilds():
            try:
                await views.ensure_battle_panel(self.bot, guild, guild_row)
                await views.ensure_roster_panel(self.bot, guild, guild_row)
            except Exception:
                logger.exception(
                    "ギルドバトルパネルの設置に失敗しました: guild_id=%s",
                    guild_row["guild_id"],
                )

    async def _ensure_ranking_panel(self, guild: discord.Guild) -> None:
        """ギルド受付チャンネルへギルドランキングパネルを設置する（26.2節）。"""

        channel_id = config.GUILD_RECEPTION_CHANNEL_ID
        if not channel_id:
            if not self._ranking_channel_warned:
                logger.warning(
                    "GUILD_RECEPTION_CHANNEL_ID が未設定のため、"
                    "ギルドランキングパネルを設置しません"
                )
                self._ranking_channel_warned = True
            return

        await ensure_panel_message(
            self.bot,
            guild,
            channel_id,
            panel_title=views.RANKING_PANEL_TITLE,
            embed=views.ranking_panel_embed(),
            view=views.BattleRankingPanelView(),
            panel_name="ギルドランキングパネル",
        )

    async def _remove_legacy_panels(self, guild: discord.Guild) -> None:
        """設置先や表題が変わって不要になった旧パネルを片づける。

        - 使い魔チャンネルの旧「バトル使い魔登録」（各ギルドの使い魔バトルへ移設）
        - バトル募集チャンネルの旧「ギルドバトルランキング」（ギルド受付へ移設）
        """

        targets = (
            (config.FAMILIAR_PANEL_CHANNEL_ID, views.LEGACY_FAMILIAR_PANEL_TITLES),
            (
                config.GUILD_BATTLE_RECRUITMENT_CHANNEL_ID,
                views.LEGACY_RANKING_PANEL_TITLES,
            ),
        )

        for channel_id, titles in targets:
            if not channel_id:
                continue

            await remove_legacy_panels(
                self.bot, guild, channel_id, titles=titles
            )

    @battle_panels.before_loop
    async def before_battle_panels(self) -> None:
        await self.bot.wait_until_ready()
        await asyncio.sleep(10)

    # ==================================================
    # 再起動後の復旧（29節）
    # ==================================================
    @tasks.loop(hours=24, count=1)
    async def battle_recovery(self) -> None:
        """進行中バトルを点検し、停止中の時間を消費せずに再開する。"""

        try:
            if not game_shared.is_game_enabled():
                return

            for row in get_active_battles():
                battle_id = int(row["battle_id"])

                if row["status"] == "paused":
                    # 運営の再開・中止判断を待つ
                    continue

                try:
                    if row["status"] == "preparing":
                        await service.start_prepared_battle(self.bot, battle_id)
                    else:
                        await service.resume_battle(self.bot, battle_id)
                except Exception:
                    logger.exception(
                        "進行中バトルの復旧に失敗しました: battle_id=%s", battle_id
                    )

        finally:
            # 例外が起きても見張りタスクが止まったままにならないようにする
            self._recovered.set()

    @battle_recovery.before_loop
    async def before_battle_recovery(self) -> None:
        await self.bot.wait_until_ready()
        await asyncio.sleep(5)

    # ==================================================
    # 見張りタスク（17節・22節）
    # ==================================================
    @tasks.loop(seconds=WATCHDOG_INTERVAL_SECONDS)
    async def battle_watchdog(self) -> None:
        """制限時間を過ぎたターンを自動処理する。"""

        if not game_shared.is_game_enabled():
            return

        try:
            await service.process_timeouts(self.bot)
        except Exception:
            logger.exception("バトル見張りタスクで例外が発生しました")

    @battle_watchdog.before_loop
    async def before_battle_watchdog(self) -> None:
        await self.bot.wait_until_ready()
        # 復旧で turn_started_at を現在時刻へ入れ直してから監視を始める（22節）
        await self._recovered.wait()

    # ==================================================
    # バトル専用チャンネルの後片付け（34.14節）
    # ==================================================
    @tasks.loop(hours=1)
    async def purge_battle_channels(self) -> None:
        """保存期間を過ぎた終了済みバトルのチャンネルを削除する。"""

        if not game_shared.is_game_enabled():
            return

        try:
            deleted = await service.purge_battle_channels(self.bot)
        except Exception:
            logger.exception("バトル専用チャンネルの削除に失敗しました")
            return

        if deleted:
            logger.info("バトル専用チャンネルを削除しました: %d件", deleted)

    @purge_battle_channels.before_loop
    async def before_purge_battle_channels(self) -> None:
        await self.bot.wait_until_ready()
        await asyncio.sleep(60)

    # ==================================================
    # 運営用コマンド（26.1節・29節）
    # ==================================================
    @app_commands.command(
        name="バトル中止", description="運営がギルドバトルを強制中止します"
    )
    @app_commands.describe(
        battle_id="中止するバトルのID", reason="中止する理由（運営ログに保存します）"
    )
    async def abort_battle_command(
        self, interaction: discord.Interaction, battle_id: int, reason: str
    ) -> None:
        if not game_shared.is_manager(interaction.user):
            await game_shared.respond(interaction, "運営だけが実行できます。")
            return

        await interaction.response.defer(ephemeral=True)

        result = await service.abort_battle(
            self.bot, int(battle_id), executor_id=interaction.user.id, reason=reason
        )

        if not result["ok"]:
            messages = {
                "reason_required": "中止理由を入力してください。",
                "battle_not_found": "指定のバトルが見つかりません。",
                "already_finished": "このバトルは既に終了しています。",
            }
            await game_shared.respond(
                interaction, messages.get(result["error"], "中止できませんでした。")
            )
            return

        await game_shared.respond(
            interaction,
            f"バトル {battle_id} を強制中止しました。勝敗と報酬は記録されません。",
        )

    @app_commands.command(
        name="バトル再開", description="運営が一時停止中のギルドバトルを再開します"
    )
    @app_commands.describe(battle_id="再開するバトルのID")
    async def resume_battle_command(
        self, interaction: discord.Interaction, battle_id: int
    ) -> None:
        if not game_shared.is_manager(interaction.user):
            await game_shared.respond(interaction, "運営だけが実行できます。")
            return

        await interaction.response.defer(ephemeral=True)

        resumed = await service.resume_battle(
            self.bot, int(battle_id), include_paused=True
        )

        await game_shared.game_admin_log(
            self.bot,
            action="ギルドバトル再開",
            executor_id=interaction.user.id,
            target_battle_id=int(battle_id),
            success=resumed,
        )

        if not resumed:
            await game_shared.respond(
                interaction,
                "再開できませんでした。ギルド・チャンネル・出場者の状態を確認し、"
                "復旧できない場合は `/バトル中止` で終了させてください。",
            )
            return

        await game_shared.respond(interaction, f"バトル {battle_id} を再開しました。")


# ==================================================
# Cog読込
# ==================================================
async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(GuildBattle(bot), guild=discord.Object(id=config.GUILD_ID))

    # 再起動後も使用できる永続View
    bot.add_view(views.GuildBattlePanelView())
    bot.add_view(views.BattleMemberPanelView())
    bot.add_view(views.BattleRequestView())
    bot.add_view(views.BattleRecruitmentView())
    bot.add_view(views.BattleCommandView())
    bot.add_view(views.BattleRankingPanelView())

    logger.info("GuildBattle Cog 登録完了")
