"""RAGNA Onlineのギルド機能を担当するCog。

担当するのは次の4つです。

- ギルド紹介チャンネルへの設立パネル自動設置（``@tasks.loop(minutes=5)``）
- 解散から保存期間を過ぎたギルドカテゴリーの削除（``@tasks.loop(hours=1)``）
- Discordロールと ``player_roles`` テーブルの同期（起動時の一括同期と参加・更新時の個別同期）
- 永続Viewの登録（``setup``）

ボタン・Select・Modalの実装は ``views.py``、Discord側の一連の手順は ``service.py`` にあります。
"""

from __future__ import annotations

import asyncio
import logging

from datetime import datetime, timedelta, timezone

import discord
import config

from discord.ext import commands, tasks
from utils import ensure_panel_message

from cogs import game_shared
from database.guild import (
    get_active_guilds,
    get_archived_guilds_to_purge,
    mark_guild_channels_purged,
)
from game.master_data import load_master_data

from . import service
from .views import (
    GuildManageView,
    GuildMemberPanelView,
    GuildPanelView,
    GuildRecruitmentView,
    JoinRequestView,
)


logger = logging.getLogger(__name__)


# ==================================================
# ギルドCog
# ==================================================
class Guild(commands.Cog):
    """ギルドの設立・募集・参加申請・管理・解散をまとめたCog。"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

        # チャンネル未設定の警告ログは1度だけ出す
        self._warned_intro_channel = False

        self.guild_panel.start()
        self.guild_member_panels.start()
        self.purge_archived_guilds.start()
        self.initial_rank_sync.start()

    # ==================================================
    # 設立パネル自動設置
    # ==================================================
    @tasks.loop(minutes=5)
    async def guild_panel(self) -> None:
        if not game_shared.is_game_enabled():
            return

        guild = self.bot.get_guild(config.GUILD_ID)
        if guild is None:
            return

        if not config.GUILD_INTRO_CHANNEL_ID:
            if not self._warned_intro_channel:
                logger.warning(
                    "GUILD_INTRO_CHANNEL_ID が未設定のため設立パネルを設置しません"
                )
                self._warned_intro_channel = True
            return

        try:
            embed = service.build_intro_panel_embed()
        except Exception:
            logger.exception("設立パネルのEmbed作成に失敗しました")
            return

        await ensure_panel_message(
            self.bot,
            guild,
            config.GUILD_INTRO_CHANNEL_ID,
            panel_title=service.PANEL_TITLE,
            embed=embed,
            view=GuildPanelView(),
            panel_name="RAGNA Onlineギルドパネル",
            # 同じチャンネルへ募集Embedも並ぶため、既存パネルを見落とさないよう広めに探す
            history_limit=100,
        )

    @guild_panel.before_loop
    async def before_guild_panel(self) -> None:
        await self.bot.wait_until_ready()
        await asyncio.sleep(5)

    # ==================================================
    # ギルド専用チャンネルの常設パネル復旧（29節・30.1節）
    # ==================================================
    @tasks.loop(minutes=5)
    async def guild_member_panels(self) -> None:
        """全活動中ギルドの管理パネル・メンバー用パネルを設置し直す。

        設立時の送信失敗や、パネルメッセージが削除された場合でも、
        ギルド管理と脱退の操作手段が失われないようにします。
        """

        if not game_shared.is_game_enabled():
            return

        guild = self.bot.get_guild(config.GUILD_ID)
        if guild is None:
            return

        for guild_row in get_active_guilds():
            try:
                await service.post_guild_panels(self.bot, guild, guild_row)
            except Exception:
                logger.exception(
                    "ギルド常設パネルの確認に失敗しました: guild_id=%s",
                    guild_row.get("guild_id"),
                )

    @guild_member_panels.before_loop
    async def before_guild_member_panels(self) -> None:
        await self.bot.wait_until_ready()
        await asyncio.sleep(20)

    # ==================================================
    # アーカイブ済みギルドのチャンネル削除（5.3節）
    # ==================================================
    @tasks.loop(hours=1)
    async def purge_archived_guilds(self) -> None:
        if not game_shared.is_game_enabled():
            return

        guild = self.bot.get_guild(config.GUILD_ID)
        if guild is None:
            return

        try:
            archive_days = load_master_data().guild.archive_days
        except Exception:
            logger.exception("マスターデータの読み込みに失敗しました")
            return

        before = (
            datetime.now(timezone.utc) - timedelta(days=archive_days)
        ).isoformat()

        for guild_row in get_archived_guilds_to_purge(before):
            deleted = await game_shared.delete_guild_channels(guild, guild_row)
            if not deleted:
                continue

            mark_guild_channels_purged(int(guild_row["guild_id"]))
            logger.info(
                "保存期間を過ぎたギルドカテゴリーを削除しました: guild_id=%s",
                guild_row["guild_id"],
            )

    @purge_archived_guilds.before_loop
    async def before_purge_archived_guilds(self) -> None:
        await self.bot.wait_until_ready()
        await asyncio.sleep(30)

    # ==================================================
    # プレイヤーランクの一括同期（27節）
    # ==================================================
    @tasks.loop(count=1)
    async def initial_rank_sync(self) -> None:
        guild = self.bot.get_guild(config.GUILD_ID)
        if guild is None:
            logger.warning("サーバーを取得できないためプレイヤーランクを同期できません")
            return

        try:
            synced = game_shared.sync_all_member_ranks(guild)
        except Exception:
            logger.exception("プレイヤーランクの一括同期に失敗しました")
            return

        logger.info("プレイヤーランクを同期しました: %d件", synced)

    @initial_rank_sync.before_loop
    async def before_initial_rank_sync(self) -> None:
        await self.bot.wait_until_ready()
        await asyncio.sleep(10)

    # ==================================================
    # ロール同期（27節）
    # ==================================================
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.bot or member.guild.id != config.GUILD_ID:
            return

        try:
            game_shared.sync_member_rank(member)
        except Exception:
            logger.exception("参加メンバーのロール同期に失敗しました: %s", member.id)

    @commands.Cog.listener()
    async def on_member_update(
        self, before: discord.Member, after: discord.Member
    ) -> None:
        if after.bot or after.guild.id != config.GUILD_ID:
            return

        # ロールが変わったときだけ同期する
        if before.roles == after.roles:
            return

        try:
            game_shared.sync_member_rank(after)
        except Exception:
            logger.exception("ロール更新時の同期に失敗しました: %s", after.id)

    # ==================================================
    # 後始末
    # ==================================================
    def cog_unload(self) -> None:
        self.guild_panel.cancel()
        self.guild_member_panels.cancel()
        self.purge_archived_guilds.cancel()
        self.initial_rank_sync.cancel()


# ==================================================
# Cog読込
# ==================================================
async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Guild(bot), guild=discord.Object(id=config.GUILD_ID))

    # 再起動後も使用できる永続View
    bot.add_view(GuildPanelView())
    bot.add_view(GuildRecruitmentView())
    bot.add_view(JoinRequestView())
    bot.add_view(GuildManageView())
    bot.add_view(GuildMemberPanelView())

    logger.info("Guild Cog 登録完了")
