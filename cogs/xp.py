"""ボイスチャンネル滞在時間とXPを計測・保存するCog。"""

import asyncio
import logging
import time
import discord
import config

from datetime import datetime, time as datetime_time
from zoneinfo import ZoneInfo
from discord.ext import commands, tasks
from discord import app_commands
from utils import format_time
from database.xp import (
    get_vc_time,
    add_vc_time,
    add_vc_time_batch,
    get_vc_month_state,
    set_vc_month_state,
    reset_monthly_vc_data
)
from texts import member as member_texts

JST = ZoneInfo("Asia/Tokyo")
logger = logging.getLogger(__name__)

class XP(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.voice_sessions = {}

        # 月替わり処理と通常保存処理の競合防止
        self.month_change_lock = asyncio.Lock()
        self.flush_lock = asyncio.Lock()

        self.save_loop.start()
        self.monthly_reset_loop.start()

    # ==================================================
    # 世界樹カテゴリ判定
    # ==================================================
    def is_world_tree_channel(self,channel: discord.VoiceChannel|discord.StageChannel|None) -> bool:

        if channel is None:
            return False

        if channel.category is None:
            return False

        return (channel.category.id == config.CATEGORY_WORLD_TREE)

    # ==================================================
    # セッション開始
    # ==================================================
    def start_session(self,member: discord.Member,channel: discord.VoiceChannel | discord.StageChannel):

        self.voice_sessions[member.id] = {
            "channel_id": channel.id,
            "last_check": time.monotonic(),

            # 1分未満の端数
            "vc_remainder": 0,
            "xp_remainder": 0,

            # 次回DB保存分
            "pending_minutes": 0,
            "pending_xp": 0
        }

    # ==================================================
    # 経過時間をメモリ上へ加算
    # ==================================================
    def accumulate_session(self,user_id: int,now: float | None = None):

        session = self.voice_sessions.get(user_id)

        if session is None:
            return

        if now is None:
            now = time.monotonic()

        elapsed_seconds = int(now - session["last_check"])

        if elapsed_seconds <= 0:
            return

        session["last_check"] = now

        # ------------------------------------------
        # 全VCの通話時間
        # ------------------------------------------
        vc_seconds = (session["vc_remainder"] + elapsed_seconds)

        vc_minutes, vc_remainder = divmod(vc_seconds,60)

        session["vc_remainder"] = vc_remainder
        session["pending_minutes"] += vc_minutes

        # ------------------------------------------
        # 世界樹カテゴリ内のみXP加算
        # ------------------------------------------
        channel = self.bot.get_channel(session["channel_id"])

        if self.is_world_tree_channel(channel):

            xp_seconds = (session["xp_remainder"] + elapsed_seconds)
            xp_minutes, xp_remainder = divmod(xp_seconds,60)

            session["xp_remainder"] = xp_remainder
            session["pending_xp"] += xp_minutes

    # ==================================================
    # 1人分をDBへ保存
    # ==================================================
    def save_user_session(self,user_id: int):

        session = self.voice_sessions.get(user_id)

        if session is None:
            return

        minutes = session["pending_minutes"]
        xp = session["pending_xp"]

        if minutes <= 0 and xp <= 0:
            return

        add_vc_time(
            user_id=user_id,
            minutes=minutes,
            xp=xp
        )

        session["pending_minutes"] = 0
        session["pending_xp"] = 0

    # ==================================================
    # 月替わり確認・処理
    # ==================================================
    async def ensure_current_month(self):

        async with self.month_change_lock:

            now_datetime = datetime.now(JST)
            current_year_month = now_datetime.strftime("%Y-%m")

            saved_year_month = get_vc_month_state()

            # 初回起動
            if saved_year_month is None:
                set_vc_month_state(current_year_month)
                return

            # 同じ月なら何もしない
            if saved_year_month == current_year_month:
                return

            now_monotonic = time.monotonic()

            # 今月1日の0時
            month_boundary = now_datetime.replace(
                day=1,
                hour=0,
                minute=0,
                second=0,
                microsecond=0
            )

            seconds_after_month_start = (
                now_datetime - month_boundary
            ).total_seconds()

            # 今月1日0時に相当するmonotonic値
            boundary_monotonic = (
                now_monotonic
                - seconds_after_month_start
            )

            records = []

            # 前月分の未保存時間を0時まで確定
            for user_id in list(self.voice_sessions.keys()):

                session = self.voice_sessions.get(user_id)

                if session is None:
                    continue

                # 今月に入ってから開始されたセッション
                if session["last_check"] >= boundary_monotonic:
                    continue

                self.accumulate_session(
                    user_id,
                    boundary_monotonic
                )

                minutes = session["pending_minutes"]
                xp = session["pending_xp"]

                if minutes <= 0 and xp <= 0:
                    continue

                records.append(
                    (
                        user_id,
                        minutes,
                        xp
                    )
                )

            # 前月分をDBへ保存
            if records:

                add_vc_time_batch(records)

                for user_id, _, _ in records:

                    session = self.voice_sessions.get(user_id)

                    if session is None:
                        continue

                    session["pending_minutes"] = 0
                    session["pending_xp"] = 0

            # 月間データをリセットし、管理年月を更新
            reset_monthly_vc_data(
                current_year_month
            )

    # ==================================================
    # XP確認
    # ==================================================
    @app_commands.guilds(discord.Object(id=config.GUILD_ID))
    @app_commands.command(name="xp確認",description="XPを確認します")
    async def xp_check(self,interaction: discord.Interaction):

        await self.ensure_current_month()

        # 通話中なら未保存分をメモリへ反映
        if interaction.user.id in self.voice_sessions:
            self.accumulate_session(interaction.user.id)

        (
            total_minutes,
            monthly_minutes,
            total_xp,
            monthly_xp
        ) = get_vc_time(interaction.user.id)

        # DB未保存分も表示へ加算
        session = self.voice_sessions.get(interaction.user.id)

        if session:
            total_minutes += session["pending_minutes"]
            monthly_minutes += session["pending_minutes"]
            total_xp += session["pending_xp"]
            monthly_xp += session["pending_xp"]

        embed = discord.Embed(
            description=member_texts.XP_BODY.format(
                monthly_xp=f"{monthly_xp:,}",
                total_xp=f"{total_xp:,}",
                monthly_time=format_time(monthly_minutes),
                total_time=format_time(total_minutes)
            ),
            color=config.COLOR_BLUE
        )
        embed.set_thumbnail(url=interaction.user.display_avatar.url)

        await interaction.response.send_message(embed=embed,ephemeral=True)

    # ==================================================
    # VC入退室・移動監視
    # ==================================================
    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState
    ):

        if member.bot:
            return

        # VC自体が同じなら処理しない
        if before.channel == after.channel:
            return

        # 入室・退出・VC移動の場合だけ月替わりを確認
        await self.ensure_current_month()

        # ------------------------------------------
        # VCへ入室
        # ------------------------------------------
        if (before.channel is None and after.channel is not None):

            self.start_session(member,after.channel)

            return

        # ------------------------------------------
        # VCから退出
        # ------------------------------------------
        if (before.channel is not None and after.channel is None):

            if member.id not in self.voice_sessions:
                return

            self.accumulate_session(member.id)
            self.save_user_session(member.id)

            self.voice_sessions.pop(member.id,None)

            return

        # ------------------------------------------
        # VCから別VCへ直接移動
        # ------------------------------------------
        if (before.channel is not None and after.channel is not None):

            session = self.voice_sessions.get(member.id)
            if session is None:

                self.start_session(member,after.channel)

                return

            # 移動前VCの経過分を確定
            self.accumulate_session(member.id)

            # DB保存はせず、計測対象だけ変更
            session["channel_id"] = after.channel.id
            session["last_check"] = time.monotonic()

    # ==================================================
    # 未保存セッションの一括保存
    # ==================================================
    async def flush_sessions(self):
        async with self.flush_lock:
            await self._flush_sessions()

    async def _flush_sessions(self):
        await self.ensure_current_month()

        now_monotonic = time.monotonic()
        records = []

        for user_id in list(self.voice_sessions.keys()):

            self.accumulate_session(user_id,now_monotonic)

            session = self.voice_sessions.get(user_id)
            if session is None:
                continue

            minutes = session["pending_minutes"]
            xp = session["pending_xp"]

            if minutes <= 0 and xp <= 0:
                continue

            records.append((user_id,minutes,xp))

        # 全員分を一括保存
        if records:

            add_vc_time_batch(records)

            for user_id, _, _ in records:

                session = self.voice_sessions.get(user_id)

                if session is None:
                    continue

                session["pending_minutes"] = 0
                session["pending_xp"] = 0

    # ==================================================
    # 5分ごとの一括保存
    # ==================================================
    @tasks.loop(minutes=5)
    async def save_loop(self):

        try:
            await self.flush_sessions()
        except Exception:
            # pending値は保存成功まで消さないため、次回ループで再試行できる。
            logger.exception("VC時間・XPの定期保存に失敗しました。5分後に再試行します")

    # ==================================================
    # 毎日0時の月替わり確認
    # ==================================================
    @tasks.loop(time=datetime_time(hour=0,minute=0,second=0,tzinfo=JST))
    async def monthly_reset_loop(self):

        try:
            await self.ensure_current_month()
        except Exception:
            logger.exception("VC月次リセットの確認に失敗しました")

    # ==================================================
    # Bot起動時の月間データ確認
    # ==================================================
    @monthly_reset_loop.before_loop
    async def before_monthly_reset_loop(self):

        await self.bot.wait_until_ready()

        # Bot停止中に月をまたいだ場合の補正
        await self.ensure_current_month()

    # ==================================================
    # Bot起動時のVC参加者登録
    # ==================================================
    @save_loop.before_loop
    async def before_save_loop(self):

        await self.bot.wait_until_ready()

        guild = self.bot.get_guild(config.GUILD_ID)

        if guild is None:
            return

        channels = [
            *guild.voice_channels,
            *guild.stage_channels
        ]

        for channel in channels:

            for member in channel.members:

                if member.bot:
                    continue

                if member.id in self.voice_sessions:
                    continue

                self.start_session(member,channel)

    # ==================================================
    # Cog解除時
    # ==================================================
    def cog_unload(self):
        self.save_loop.cancel()
        self.monthly_reset_loop.cancel()

async def setup(bot: commands.Bot):
    await bot.add_cog(
        XP(bot),guild=discord.Object(id=config.GUILD_ID))
    logger.info("XP Cog 登録完了")
