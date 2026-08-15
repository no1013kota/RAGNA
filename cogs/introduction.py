"""自己紹介チャンネルへ入力テンプレートを自動設置するCog。"""

import logging

import discord
import config
import asyncio

from discord.ext import commands


logger = logging.getLogger(__name__)


# ==========================================
# 自己紹介テンプレート
# ==========================================
INTRODUCTION_TEMPLATE = """【名前】
【年齢】
【性格】
【声質】
【好き】
【嫌い】
【アピール】
【紹介者】"""


# ==========================================
# Introduction Cog
# ==========================================
class Introduction(commands.Cog):
    def __init__(self,bot: commands.Bot):
        self.bot = bot
        self.locks = {}

    # ==========================================
    # チャンネルごとのロック取得
    # ==========================================
    def get_lock(self,channel_id: int):

        if channel_id not in self.locks:
            self.locks[channel_id] = asyncio.Lock()

        return self.locks[channel_id]

    # ==========================================
    # 自己紹介テンプレート更新
    # ==========================================
    async def refresh_template(self,channel: discord.TextChannel):

        lock = self.get_lock(channel.id)

        async with lock:

            # ==========================================
            # 以前のテンプレートを削除
            # ==========================================
            try:

                async for old_message in channel.history(limit=50):

                    if old_message.author.id != self.bot.user.id:
                        continue

                    if old_message.content != INTRODUCTION_TEMPLATE:
                        continue

                    try:
                        await old_message.delete()

                    except discord.HTTPException as e:
                        logger.warning(f"自己紹介テンプレート削除失敗：{old_message.id} / {e}")

            except discord.HTTPException as e:

                logger.warning(f"自己紹介チャンネル履歴取得失敗：{channel.id} / {e}")
                return

            # ==========================================
            # テンプレート再投稿
            # ==========================================
            try:

                await channel.send(INTRODUCTION_TEMPLATE)

            except discord.HTTPException as e:

                logger.warning(f"自己紹介テンプレート送信失敗：{channel.id} / {e}")

    # ==========================================
    # Bot起動時
    # ==========================================
    @commands.Cog.listener()
    async def on_ready(self):

        guild = self.bot.get_guild(config.GUILD_ID)

        if guild is None:
            return

        for channel_id in config.INTRODUCTION_CHANNELS:

            channel = guild.get_channel(channel_id)

            if channel is None:

                try:
                    channel = await guild.fetch_channel(channel_id)

                except discord.NotFound:
                    logger.warning(f"自己紹介チャンネルが見つかりません：{channel_id}")
                    continue

                except discord.HTTPException as e:
                    logger.warning(f"自己紹介チャンネル取得失敗：{channel_id} / {e}")
                    continue

            if not isinstance(channel,discord.TextChannel):
                continue

            await self.refresh_template(channel)

    # ==========================================
    # 自己紹介投稿監視
    # ==========================================
    @commands.Cog.listener()
    async def on_message(self,message: discord.Message):

        # Botの投稿は無視
        if message.author.bot:
            return

        # DMは無視
        if message.guild is None:
            return

        # 対象チャンネル以外は無視
        if message.channel.id not in config.INTRODUCTION_CHANNELS:
            return

        if not isinstance(message.channel,discord.TextChannel):
            return

        await self.refresh_template(message.channel)


# ==========================================
# Cog読込
# ==========================================
async def setup(bot: commands.Bot):

    await bot.add_cog(Introduction(bot),guild=discord.Object(id=config.GUILD_ID))

    logger.info("Introduction Cog 登録完了")
