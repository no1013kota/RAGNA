"""メンバー情報、招待ポイント、各種利用状況を扱うCog。"""

import logging
import discord
import config
import asyncio

from discord.ext import commands,tasks
from discord import app_commands
from utils import ensure_panel_message, format_time
from database.coin import get_balance
from database.member import (
    get_invite_points,
    add_invite_points,
    add_invite_reward,
)
from database.trial_member import (
    get_all_evaluation_log_channels,
    delete_evaluation_log_channel,
)
from database.xp import get_vc_time
from .views import InvitePointView
from texts import common as common_texts
from texts import member as member_texts
from texts import panels as panel_texts


logger = logging.getLogger(__name__)

class Member(commands.Cog):
    def __init__(self,bot):
        self.bot = bot
        self.invite_panel.start()

    # ==================================================
    # メンバーログチャンネル取得
    # ==================================================
    async def get_member_log_channel(self,guild: discord.Guild,channel_id: int):

        channel = guild.get_channel(channel_id)

        if channel is not None:
            return channel

        try:
            channel = await guild.fetch_channel(channel_id)

        except discord.NotFound:
            logger.warning(f"メンバーログチャンネルが見つかりません：{channel_id}")
            return None

        except discord.HTTPException as e:
            logger.warning(f"メンバーログチャンネル取得失敗：{channel_id} / {e}")
            return None

        return channel

    # ==================================================
    # 参加ログ
    # ==================================================
    async def send_member_join_log(self,member: discord.Member):

        guild = member.guild
        channel = await self.get_member_log_channel(guild,config.CHANNEL_MEMBER_JOIN_LOG)

        if channel is None:
            return

        embed = discord.Embed(
            description=member_texts.JOIN_LOG_BODY.format(user=member.mention),
            color=config.COLOR_BLUE
        )
        embed.set_footer(
            text=member_texts.FOOTER_NAME.format(
                display_name=member.display_name,
                name=member.name
            )
        )
        embed.set_thumbnail(url=member.display_avatar.url)

        try:
            await channel.send(embed=embed)

        except discord.HTTPException as e:
            logger.warning(f"参加ログ送信失敗：{member.id} / {e}")

    # ==================================================
    # 脱退ログ
    # ==================================================
    async def send_member_leave_log(self,member: discord.Member):

        guild = member.guild
        channel = await self.get_member_log_channel(guild,config.CHANNEL_MEMBER_LEAVE_LOG)

        if channel is None:
            return

        embed = discord.Embed(
            description=member_texts.LEAVE_LOG_BODY.format(
                user=member.mention,
                user_id=member.id
            ),
            color=config.COLOR_RED
        )
        embed.set_footer(
            text=member_texts.FOOTER_NAME.format(
                display_name=member.display_name,
                name=member.name
            )
        )
        embed.set_thumbnail(url=member.display_avatar.url)

        try:
            await channel.send(embed=embed)

        except discord.HTTPException as e:
            logger.warning(f"脱退ログ送信失敗：{member.id} / {e}")

    @app_commands.guilds(discord.Object(id=config.GUILD_ID))
    @app_commands.command(name="メンバー確認",description="メンバー情報確認")
    async def member_check(self,interaction: discord.Interaction,user: discord.Member):

        allowed_roles = config.ROLE_GROUP_MANAGEMENT

        if not any(
            role.id in allowed_roles
            for role in interaction.user.roles
        ):
            await interaction.response.send_message(common_texts.NO_PERMISSION,ephemeral=True)
            return

        balance = get_balance(user.id)

        (
            total_minutes,
            monthly_minutes,
            total_xp,
            monthly_xp
        ) = get_vc_time(user.id)

        invite_points = get_invite_points(user.id)

        embed = discord.Embed(
            title=user.mention,
            description=(
                member_texts.MEMBER_CHECK_HEADER.format(
                    balance=f"{balance:,}",
                    invite_points=f"{invite_points:,}"
                )
                + member_texts.XP_BODY.format(
                    monthly_xp=f"{monthly_xp:,}",
                    total_xp=f"{total_xp:,}",
                    monthly_time=format_time(monthly_minutes),
                    total_time=format_time(total_minutes)
                )
            ),
            color=config.COLOR_BLUE
        )
        embed.set_thumbnail(url=user.display_avatar.url)

        await interaction.response.send_message(embed=embed,ephemeral=True)

    # ==================================================
    # 招待報酬
    # ==================================================
    @app_commands.guilds(discord.Object(id=config.GUILD_ID))
    @app_commands.command(name="招待報酬",description="対象者へ招待ポイントを付与します")
    @app_commands.describe(user="招待ポイントを付与する対象者",points="付与するポイント",reason="付与理由")
    async def invite_reward(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        points: app_commands.Range[int, 1],
        reason: str = ""
    ):

        allowed_roles = config.PERMISSION_INVITE_REWARD

        if not any(
            role.id in allowed_roles
            for role in interaction.user.roles
        ):
            await interaction.response.send_message(common_texts.NO_PERMISSION,ephemeral=True)
            return

        if user.bot:
            await interaction.response.send_message(member_texts.INVITE_REWARD_BOT,ephemeral=True)
            return

        add_invite_points(user.id,points)
        add_invite_reward(executor_id=interaction.user.id,target_id=user.id,points=points,reason=reason)

        new_points = get_invite_points(user.id)
        log_channel = interaction.guild.get_channel(config.CHANNEL_INVITE_REWARD_LOG)

        if log_channel:
            description = member_texts.INVITE_REWARD_LOG_BODY.format(
                actor=interaction.user.mention,
                user=user.mention,
                points=points,
                total=new_points
            )

            if reason:
                description += "\n" + member_texts.INVITE_REWARD_LOG_REASON.format(
                    reason=reason
                )

            embed = discord.Embed(
                title=member_texts.INVITE_REWARD_LOG_TITLE,
                description=description,
                color=config.COLOR_WHITE
            )
            embed.set_thumbnail(url=user.display_avatar.url)

            try:
                await log_channel.send(embed=embed)

            except discord.HTTPException as e:
                logger.warning(f"招待報酬ログ送信失敗：{user.id} / {e}")

        embed = discord.Embed(
            title=member_texts.INVITE_REWARD_TITLE,
            description=member_texts.INVITE_REWARD_BODY.format(
                user=user.mention,
                points=points,
                total=new_points
            ),
            color=config.COLOR_GREEN
        )
        embed.set_thumbnail(url=user.display_avatar.url)

        await interaction.response.send_message(embed=embed,ephemeral=True)

    async def archive_evaluator_log(self,member: discord.Member):

        guild = member.guild
        rows = get_all_evaluation_log_channels()
        channel_id = None

        for evaluator_id, cid in rows:
            if evaluator_id == member.id:
                channel_id = cid
                break

        if channel_id is None:
            return

        log_channel = guild.get_channel(channel_id)

        if log_channel is None:
            try:
                log_channel = await guild.fetch_channel(channel_id)

            except discord.NotFound:
                logger.warning(f"評価シートが見つかりません：{channel_id}")
                delete_evaluation_log_channel(member.id)
                return

            except discord.HTTPException as e:
                logger.warning(f"評価シート取得失敗：{channel_id} / {e}")
                return

        archive_forum = guild.get_channel(config.FORUM_EVALUATION_ARCHIVE)
        if archive_forum is None:
            return

        try:
            archive_thread, _ = await archive_forum.create_thread(
                name=member_texts.EVALUATOR_ARCHIVE_THREAD_NAME.format(
                    display_name=member.display_name,
                    name=member.name
                ),
                content="\u200b"
            )

        except discord.HTTPException as e:
            logger.warning(f"評価シートアーカイブ作成失敗：{member.id} / {e}")
            return

        roles = []

        for role in member.roles:
            if role.id in (
                config.ROLE_A_PLUS,
                config.ROLE_B_PLUS,
                config.ROLE_EVALUATION_MANAGER,
                config.ROLE_EVALUATOR,
                config.ROLE_SUB_MANAGER
            ):
                roles.append(role.mention)

        embed = discord.Embed(
            title=member_texts.EVALUATOR_ARCHIVE_TITLE,
            color=config.COLOR_GREEN
        )
        embed.description = member_texts.EVALUATOR_ARCHIVE_BODY.format(
            user=member.mention,
            roles=(
                " ".join(roles)
                if roles
                else member_texts.EVALUATOR_ARCHIVE_NO_ROLE
            )
        )
        embed.set_thumbnail(url=member.display_avatar.url)

        await archive_thread.send(embed=embed)

        # ここからコピー開始
        async for message in log_channel.history(oldest_first=True,limit=None):

            content = ""
            if message.content:
                content = message.content

            files = []
            if message.attachments:
                files = [
                    await attachment.to_file()
                    for attachment in message.attachments
                ]

            if message.embeds:
                for embed in message.embeds:
                    await archive_thread.send(embed=embed)

            if content or files:
                await archive_thread.send(
                    content = member_texts.EVALUATION_COPY_MESSAGE.format(
                        author=message.author.display_name,
                        content=content
                    ),
                    files = files
                )

        try:
            await log_channel.delete()

        except discord.NotFound:
            pass

        except discord.HTTPException as e:
            logger.warning(f"評価シート削除失敗：{log_channel.id} / {e}")
            return

        delete_evaluation_log_channel(member.id)

    # ==================================================
    # BANログ
    # ==================================================
    async def send_ban_log(self,guild: discord.Guild,executor: discord.Member,user: discord.User,reason: str):

        channel = guild.get_channel(config.CHANNEL_BAN_LOG)
        if channel is None:
            return

        embed = discord.Embed(
            title=member_texts.BAN_LOG_TITLE,
            description=member_texts.BAN_LOG_BODY.format(
                actor=executor.mention,
                user=user.mention,
                reason=reason
            ),
            color=config.COLOR_RED
        )
        embed.set_footer(
            text=member_texts.FOOTER_NAME.format(
                display_name=user.display_name,
                name=user.name
            )
        )
        embed.set_thumbnail(url=user.display_avatar.url)

        try:
            await channel.send(embed=embed)

        except discord.HTTPException as e:
            logger.warning(
                f"BANログ送信失敗：{user.id} / {e}"
            )

    # ==================================================
    # ユーザーBAN
    # ==================================================
    @app_commands.guilds(discord.Object(id=config.GUILD_ID))
    @app_commands.default_permissions(administrator=True)
    @app_commands.command(name="ban",description="ユーザーIDを指定してBANします")
    @app_commands.describe(user_id="BANするユーザーのID",reason="BANする理由")
    async def ban_user(self,interaction: discord.Interaction,user_id: str,reason: str):

        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        if guild is None:
            await interaction.followup.send(common_texts.GUILD_NOT_FOUND,ephemeral=True)
            return

        user_id = user_id.strip()
        if not user_id.isdigit():
            await interaction.followup.send(member_texts.BAN_ID_NOT_NUMBER,ephemeral=True)
            return

        target_id = int(user_id)
        if target_id == interaction.user.id:
            await interaction.followup.send(member_texts.BAN_SELF,ephemeral=True)
            return

        if self.bot.user and target_id == self.bot.user.id:
            await interaction.followup.send(member_texts.BAN_BOT,ephemeral=True)
            return

        reason = reason.strip()

        if not reason:
            await interaction.followup.send(member_texts.BAN_REASON_REQUIRED,ephemeral=True)
            return

        try:
            target_user = await self.bot.fetch_user(target_id)

        except discord.NotFound:
            await interaction.followup.send(member_texts.BAN_USER_NOT_FOUND,ephemeral=True)
            return

        except discord.HTTPException as e:
            logger.warning(f"BAN対象ユーザー取得失敗：{target_id} / {e}")

            await interaction.followup.send(member_texts.BAN_USER_FETCH_FAILED,ephemeral=True)
            return

        # すでにBANされているか確認
        try:
            await guild.fetch_ban(target_user)
        except discord.NotFound:
            # 未BANなので処理を続行
            pass

        except discord.HTTPException as e:
            logger.warning(f"BAN状態確認失敗：{target_id} / {e}")
            await interaction.followup.send(member_texts.BAN_CHECK_FAILED,ephemeral=True)
            return

        else:
            await interaction.followup.send(member_texts.BAN_ALREADY,ephemeral=True)
            return

        try:
            await guild.ban(
                target_user,
                reason=(f"{reason} （実行者：{interaction.user}）"),
                delete_message_seconds=0
            )

        except discord.HTTPException as e:
            logger.warning(f"ユーザーBAN失敗：{target_id} / {e}")
            await interaction.followup.send(member_texts.BAN_FAILED,ephemeral=True)
            return

        await self.send_ban_log(
            guild=guild,
            executor=interaction.user,
            user=target_user,
            reason=reason
        )

        embed = discord.Embed(
            title=member_texts.BAN_DONE_TITLE,
            description=member_texts.BAN_DONE_BODY.format(
                user=target_user.mention,
                user_id=target_user.id,
                reason=reason
            ),
            color=config.COLOR_RED
        )
        embed.set_thumbnail(url=target_user.display_avatar.url)

        await interaction.followup.send(embed=embed,ephemeral=True)

    # ==================================================
    # サーバー参加
    # ==================================================
    @commands.Cog.listener()
    async def on_member_join(self,member: discord.Member):

        await self.send_member_join_log(member)

    # ==================================================
    # サーバー脱退
    # ==================================================
    @commands.Cog.listener()
    async def on_member_remove(self,member: discord.Member):

        # 脱退ログを送信
        await self.send_member_leave_log(member)

        # 評価員だった場合は評価シートを保存
        await self.archive_evaluator_log(member)

    def cog_unload(self):
        self.invite_panel.cancel()

    # ==================================================
    # 招待ポイントパネル自動設置
    # ==================================================
    @tasks.loop(minutes=5)
    async def invite_panel(self):

        guild = self.bot.get_guild(config.GUILD_ID)
        if guild is None:
            return

        embed = discord.Embed(
            title=panel_texts.INVITE_LIST,
            description=member_texts.PANEL_INVITE_BODY,
            color=config.COLOR_WHITE
        )

        await ensure_panel_message(
            self.bot,
            guild,
            config.CHANNEL_INVITE_POINT_PANEL,
            panel_title=panel_texts.INVITE_LIST,
            embed=embed,
            view=InvitePointView(),
            panel_name="招待ポイントパネル",
        )

    @invite_panel.before_loop
    async def before_invite_panel(self):

        await self.bot.wait_until_ready()
        await asyncio.sleep(5)
async def setup(bot):
    await bot.add_cog(Member(bot),guild=discord.Object(id=config.GUILD_ID))
    bot.add_view(InvitePointView())
    logger.info("Member Cog 登録完了")
