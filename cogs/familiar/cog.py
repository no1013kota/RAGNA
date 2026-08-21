"""使い魔パネル（ガチャ／管理）の自動設置を担当するCog。

ガチャパネルは「ガチャ」チャンネル（``config.GACHA_PANEL_CHANNEL_ID``）、
使い魔管理パネルは「使い魔」チャンネル（``config.FAMILIAR_PANEL_CHANNEL_ID``）
へ設置します。一覧・合成・売却は「使い魔管理」パネルの3ボタンにまとめています
（GAME_SPEC 10.2節）。ガチャチャンネルが未設定の場合は、従来どおり使い魔
チャンネルへ両方を置きます。
"""

from __future__ import annotations

import asyncio
import logging

import discord
import config

from discord.ext import commands, tasks

from cogs import game_shared
from cogs.coin.views import ensure_atm_panel
from utils import ensure_panel_message, remove_legacy_panels

from . import views


logger = logging.getLogger(__name__)


# ==================================================
# 使い魔パネル
# ==================================================
class Familiar(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

        # 未設定チャンネルの警告は1度だけ出す。
        self._warned_missing_channel = False

        self.familiar_panels.start()

    # ==================================================
    # 使い魔パネル自動設置
    # ==================================================
    @tasks.loop(minutes=5)
    async def familiar_panels(self):

        if not game_shared.is_game_enabled():
            return

        guild = self.bot.get_guild(config.GUILD_ID)
        if guild is None:
            return

        manage_channel_id = config.FAMILIAR_PANEL_CHANNEL_ID
        if not manage_channel_id:
            if not self._warned_missing_channel:
                logger.warning(
                    "FAMILIAR_PANEL_CHANNEL_ID が未設定のため使い魔パネルを設置しません"
                )
                self._warned_missing_channel = True
            return

        # ガチャチャンネルが未設定なら、従来どおり使い魔チャンネルへ置く
        gacha_channel_id = config.GACHA_PANEL_CHANNEL_ID or manage_channel_id

        try:
            gacha_embed = views.build_gacha_panel_embed()
            manage_embed = views.build_manage_panel_embed()
        except Exception:
            logger.exception("使い魔パネルのEmbed作成に失敗しました")
            return

        # 改名前のパネルが残っていると新旧が並ぶため、先に片づける
        for channel_id in {manage_channel_id, gacha_channel_id}:
            await remove_legacy_panels(
                self.bot, guild, channel_id, titles=views.LEGACY_PANEL_TITLES
            )

        # ガチャを別チャンネルへ移したあと、使い魔チャンネルに残る旧パネルを消す
        if gacha_channel_id != manage_channel_id:
            await remove_legacy_panels(
                self.bot,
                guild,
                manage_channel_id,
                titles=(views.GACHA_PANEL_TITLE,),
            )

        # coinを使うパネルなので、どちらのチャンネルもATMを一番上へ置く
        panels = (
            (
                gacha_channel_id,
                views.GACHA_PANEL_TITLE,
                gacha_embed,
                views.GachaPanelView(),
                "使い魔ガチャパネル",
            ),
            (
                manage_channel_id,
                views.MANAGE_PANEL_TITLE,
                manage_embed,
                views.FamiliarManagePanelView(),
                "使い魔管理パネル",
            ),
        )

        for channel_id, panel_title, embed, view, panel_name in panels:
            await ensure_atm_panel(
                self.bot,
                guild,
                channel_id,
                movable_titles=(views.GACHA_PANEL_TITLE, views.MANAGE_PANEL_TITLE),
            )
            await ensure_panel_message(
                self.bot,
                guild,
                channel_id,
                panel_title=panel_title,
                embed=embed,
                view=view,
                panel_name=panel_name,
            )

    @familiar_panels.before_loop
    async def before_familiar_panels(self):

        await self.bot.wait_until_ready()
        await asyncio.sleep(5)

    def cog_unload(self):

        self.familiar_panels.cancel()


# ==================================================
# Cog読込
# ==================================================
async def setup(bot: commands.Bot):

    await bot.add_cog(Familiar(bot), guild=discord.Object(id=config.GUILD_ID))

    # 再起動後も使用できる永続View
    bot.add_view(views.GachaPanelView())
    bot.add_view(views.FamiliarManagePanelView())

    logger.info("Familiar Cog 登録完了")
