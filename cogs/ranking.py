"""coin、VC時間、XP、招待、評価員レビューのランキングを表示するCog。"""

import logging

import discord
import config

from discord.ext import commands
from discord import app_commands
from utils import format_time
from database import (
    get_balance_ranking,
    get_vc_ranking,
    get_xp_ranking,
    get_invite_ranking,
    get_evaluator_review_ranking
)


logger = logging.getLogger(__name__)


class Ranking(commands.Cog):
    def __init__(self,bot):
        self.bot = bot

    # ==================================================
    # coinランキング
    # ==================================================
    async def create_coin_ranking(self,guild,role):

        rankings = get_balance_ranking()

        title = (
            f"{role.name} coinランキング"
            if role
            else "coinランキング"
        )

        return await self.build_ranking_embed(guild,rankings,title,"coin",role)

    # ==================================================
    # 招待ptランキング
    # ==================================================
    async def create_invite_ranking(self,guild,role):

        rankings = get_invite_ranking()

        title = (
            f"{role.name} 招待ptランキング"
            if role
            else "招待ptランキング"
        )

        return await self.build_ranking_embed(guild,rankings,title,"pt",role)

    # ==================================================
    # 通話時間ランキング
    # ==================================================
    async def create_vc_ranking(self,guild,role,monthly):

        rankings = get_vc_ranking(monthly=monthly)
        if monthly:
            title = (
                f"{role.name} 通話時間ランキング（今月）"
                if role
                else "通話時間ランキング（今月）"
            )

        else:
            title = (
                f"{role.name} 通話時間ランキング（累計）"
                if role
                else "通話時間ランキング（累計）"
            )

        return await self.build_ranking_embed(guild,rankings,title,"TIME",role)

    # ==================================================
    # XPランキング
    # ==================================================
    async def create_xp_ranking(self,guild,role,monthly):

        rankings = get_xp_ranking(monthly=monthly)

        if monthly:
            title = (
                f"{role.name} XPランキング（今月）"
                if role
                else "XPランキング（今月）"
            )

        else:
            title = (
                f"{role.name} XPランキング（累計）"
                if role
                else "XPランキング（累計）"
            )

        return await self.build_ranking_embed(guild,rankings,title,"XP",role)

    # ==================================================
    # アンケートランキング
    # ==================================================
    async def create_survey_ranking(self,guild,role):

        rankings = dict(get_evaluator_review_ranking())

        evaluator_role = guild.get_role(config.ROLE_EVALUATOR)
        if evaluator_role is None:

            embed = discord.Embed(
                title="アンケートランキング",
                description="天使が見つかりません。",
                color=config.COLOR_GOLD
            )

            return [embed]

        members = [
            member
            for member in evaluator_role.members
            if not member.bot
        ]

        if role:
            members = [
                member
                for member in members
                if role in member.roles
            ]

        ranking_list = []

        for member in members:

            votes = rankings.get(member.id,0)

            ranking_list.append((member.id,votes))

        ranking_list.sort(
            key=lambda x: (
                -x[1],
                x[0]
            )
        )

        title = (
            f"{role.name} アンケートランキング"
            if role
            else "アンケートランキング"
        )

        return await self.build_ranking_embed(guild,ranking_list,title,"票",role)

    # ==================================================
    # ランキングEmbed作成
    # ==================================================
    async def build_ranking_embed(self,guild,rankings,title,suffix,role=None):

        pages = []
        lines = []
        rank = 1

        formatters = {"TIME": lambda v: format_time(v),"XP": lambda v: f"{v:,} XP"}

        for user_id, value in rankings:

            # アンケート以外は0を表示しない
            if suffix != "票" and value == 0:
                continue

            member = guild.get_member(int(user_id))
            if member is None:
                try:
                    member = await guild.fetch_member(int(user_id))

                except discord.NotFound:
                    continue

                except discord.HTTPException as e:
                    logger.warning(f"ランキング対象者取得失敗：{user_id} / {e}")
                    continue

            if role and role not in member.roles:
                continue

            value_text = formatters.get(suffix,lambda v: f"{v:,} {suffix}")(value)

            lines.append(f"{rank}位 {value_text} {member.mention}")

            rank += 1

            # 50件ごとにEmbedを分ける
            if len(lines) == 50:
                pages.append(lines)
                lines = []

        if lines:
            pages.append(lines)

        if not pages:
            pages.append(["対象者が見つかりません。"])

        embeds = []

        for i, page in enumerate(pages):

            embed = discord.Embed(
                title=(
                    title
                    if i == 0
                    else f"{title}（続き）"
                ),
                description="\n".join(page),
                color=config.COLOR_GOLD
            )
            embeds.append(embed)

        return embeds

    # ==================================================
    # ランキング
    # ==================================================
    @app_commands.guilds(discord.Object(id=config.GUILD_ID))
    @app_commands.command(name="ランキング",description="ランキング表示")
    @app_commands.describe(role="対象ロール",data="参照データ",ranking_type="通話時間（XP）ランキングの種類")
    @app_commands.choices(
        data=[
            app_commands.Choice(name="coin",value="coin"),
            app_commands.Choice(name="通話時間",value="vc"),
            app_commands.Choice(name="XP",value="xp"),
            app_commands.Choice(name="招待pt",value="invite"),
            app_commands.Choice(name="アンケート",value="survey")
        ],
        ranking_type=[
            app_commands.Choice(name="累計",value="total"),
            app_commands.Choice(name="今月",value="monthly")
        ]
    )
    async def ranking(
        self,
        interaction: discord.Interaction,
        data: app_commands.Choice[str],
        role: discord.Role | None = None,
        ranking_type: app_commands.Choice[str] | None = None
    ):

        allowed_roles = config.ROLE_GROUP_MANAGEMENT

        if not any(user_role.id in allowed_roles for user_role in interaction.user.roles):
            await interaction.response.send_message("権限がありません。",ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        monthly = (ranking_type is not None and ranking_type.value == "monthly")

        if data.value == "coin":
            embeds = await self.create_coin_ranking(interaction.guild,role)

        elif data.value == "vc":
            embeds = await self.create_vc_ranking(interaction.guild,role,monthly)

        elif data.value == "xp":
            embeds = await self.create_xp_ranking(interaction.guild,role,monthly)

        elif data.value == "invite":
            embeds = await self.create_invite_ranking(interaction.guild,role)

        else:
            embeds = await self.create_survey_ranking(interaction.guild,role)

        await interaction.followup.send(embed=embeds[0],ephemeral=True)

        for embed in embeds[1:]:

            await interaction.followup.send(embed=embed,ephemeral=True)


async def setup(bot):
    await bot.add_cog(Ranking(bot),guild=discord.Object(id=config.GUILD_ID))

    logger.info("Ranking Cog 登録完了")
