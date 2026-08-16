"""使い魔パネル（ガチャ／管理）の自動設置を担当するCog。

2つのパネルを同じ「使い魔」専用チャンネル
（``config.FAMILIAR_PANEL_CHANNEL_ID``）へ、ガチャ・管理の順で設置します。
一覧・合成・売却は「使い魔管理」パネルの3ボタンにまとめています（GAME_SPEC 10.2節）。
"""

from __future__ import annotations

import asyncio
import logging

import discord
import config

from discord.ext import commands, tasks

from cogs import game_shared
from utils import ensure_panel_message

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

        channel_id = config.FAMILIAR_PANEL_CHANNEL_ID
        if not channel_id:
            if not self._warned_missing_channel:
                logger.warning(
                    "FAMILIAR_PANEL_CHANNEL_ID が未設定のため使い魔パネルを設置しません"
                )
                self._warned_missing_channel = True
            return

        try:
            # ガチャ → 管理（一覧・合成・売却）の順で設置する。
            panels = (
                (
                    views.GACHA_PANEL_TITLE,
                    views.build_gacha_panel_embed(),
                    views.GachaPanelView(),
                    "使い魔ガチャパネル",
                ),
                (
                    views.MANAGE_PANEL_TITLE,
                    views.build_manage_panel_embed(),
                    views.FamiliarManagePanelView(),
                    "使い魔管理パネル",
                ),
            )
        except Exception:
            logger.exception("使い魔パネルのEmbed作成に失敗しました")
            return

        for panel_title, embed, view, panel_name in panels:
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
