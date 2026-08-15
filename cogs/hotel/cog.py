"""coinで作成する一時的な宿屋チャンネルを管理するCog。"""

import logging
import discord
import config
import asyncio

from discord.ext import commands, tasks
from datetime import datetime, timedelta,timezone
from database.hotel import (
    get_all_hotels,
    delete_hotel_room
)
from utils import ensure_panel_message
from .views import HotelPremiumView, HotelSecretView, HotelView

JST = timezone(timedelta(hours=9))
logger = logging.getLogger(__name__)

# ==========================================
# 宿屋パネル
# ==========================================
class HotelCog(commands.Cog):
    def __init__(self,bot):
        self.bot = bot

        self.hotel_panel.start()
        self.hotel_delete_task.start()

    # ==========================================
    # 宿屋パネル自動設置
    # ==========================================
    @tasks.loop(minutes=5)
    async def hotel_panel(self):

        guild = self.bot.get_guild(config.GUILD_ID)

        if guild is None:
            return

        embed = discord.Embed(
            title="入室プラン",
            description=(
                "\u200b\n"
                f"**ツーショ：{config.HOTEL_STANDARD_PRICE:,} coin**\n"
                "-# ※七聖・騎士は無料\n\n"
                f"**シークレット：{config.HOTEL_SECRET_PRICE:,} coin**\n"
                "ー ツーショ＋指定した人だけが見える\n\n"
                f"**プレミアム：{config.HOTEL_PREMIUM_PRICE:,} coin**\n"
                "ー すべて自由にできる＋人数も無制限\n\n"
                "-# ※宿は15時間後に自動削除します"
            ),
            color=config.COLOR_WHITE
        )

        await ensure_panel_message(
            self.bot,
            guild,
            config.CHANNEL_HOTEL_PANEL,
            panel_title="入室プラン",
            embed=embed,
            view=HotelView(),
            panel_name="宿屋パネル",
        )

    @hotel_panel.before_loop
    async def before_hotel_panel(self):

        await self.bot.wait_until_ready()
        await asyncio.sleep(5)

    # 宿の自動削除機能
    @tasks.loop(minutes=1)
    async def hotel_delete_task(self):

        now = datetime.now(timezone.utc)

        try:
            hotels = get_all_hotels()
        except Exception:
            logger.exception("期限切れ宿屋の一覧取得に失敗しました")
            return

        for hotel in hotels:

            channel_id = hotel[0]
            text_channel_id = hotel[1]
            expires_at = hotel[2]

            if datetime.fromisoformat(expires_at) > now:
                continue

            vc = self.bot.get_channel(channel_id)
            text_channel = (
                self.bot.get_channel(text_channel_id)
                if text_channel_id is not None
                else None
            )

            if text_channel:
                try:
                    await text_channel.delete()
                except discord.NotFound:
                    pass
                except discord.HTTPException:
                    logger.exception(
                        "期限切れ宿屋のテキストチャンネル削除に失敗しました: %s",
                        text_channel_id,
                    )
                    continue

            if vc:
                try:
                    await vc.delete()
                except discord.NotFound:
                    pass
                except discord.HTTPException:
                    logger.exception(
                        "期限切れ宿屋のVC削除に失敗しました: %s",
                        channel_id,
                    )
                    continue

            try:
                delete_hotel_room(channel_id)
            except Exception:
                logger.exception("期限切れ宿屋のDB削除に失敗しました: %s", channel_id)

    @hotel_delete_task.before_loop
    async def before_hotel_delete(self):
        await self.bot.wait_until_ready()

    def cog_unload(self):

        self.hotel_panel.cancel()
        self.hotel_delete_task.cancel()
async def setup(bot):
    await bot.add_cog(HotelCog(bot),guild=discord.Object(id=config.GUILD_ID))

    bot.add_view(HotelView())
    bot.add_view(HotelPremiumView())
    bot.add_view(HotelSecretView())

    logger.info("Hotel Cog 登録完了")
