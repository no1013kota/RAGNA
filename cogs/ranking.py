"""coin、VC時間、XP、招待、評価員レビューのランキングを表示するCog。"""

import logging

import discord
import config

from discord.ext import commands
from discord import app_commands
from utils import format_time
from database.ranking import (
    get_balance_ranking,
    get_vc_ranking,
    get_xp_ranking,
    get_invite_ranking,
    get_evaluator_review_ranking
)
from texts import common as common_texts
from texts import member as member_texts


logger = logging.getLogger(__name__)


class Ranking(commands.Cog):
    def __init__(self,bot):
        self.bot = bot

    # ==================================================
    # coinランキング
    # ==================================================
    async def create_coin_ranking(self,guild,role):

        rankings = get_balance_ranking()

        title = self.build_ranking_title(member_texts.RANKING_TITLE_COIN,role)

        return await self.build_ranking_embed(
            guild,
            rankings,
            title,
            member_texts.RANKING_UNIT_COIN,
            role
        )

    # ==================================================
    # 招待ptランキング
    # ==================================================
    async def create_invite_ranking(self,guild,role):

        rankings = get_invite_ranking()

        title = self.build_ranking_title(member_texts.RANKING_TITLE_INVITE,role)

        return await self.build_ranking_embed(
            guild,
            rankings,
            title,
            member_texts.RANKING_UNIT_POINT,
            role
        )

    # ==================================================
    # 通話時間ランキング
    # ==================================================
    async def create_vc_ranking(self,guild,role,monthly):

        rankings = get_vc_ranking(monthly=monthly)
        if monthly:
            title = self.build_ranking_title(
                member_texts.RANKING_TITLE_VC_MONTHLY,
                role
            )

        else:
            title = self.build_ranking_title(
                member_texts.RANKING_TITLE_VC_TOTAL,
                role
            )

        return await self.build_ranking_embed(guild,rankings,title,"TIME",role)

    # ==================================================
    # XPランキング
    # ==================================================
    async def create_xp_ranking(self,guild,role,monthly):

        rankings = get_xp_ranking(monthly=monthly)

        if monthly:
            title = self.build_ranking_title(
                member_texts.RANKING_TITLE_XP_MONTHLY,
                role
            )

        else:
            title = self.build_ranking_title(
                member_texts.RANKING_TITLE_XP_TOTAL,
                role
            )

        return await self.build_ranking_embed(
            guild,
            rankings,
            title,
            member_texts.RANKING_UNIT_XP,
            role
        )

    # ==================================================
    # アンケートランキング
    # ==================================================
    async def create_survey_ranking(self,guild,role):

        rankings = dict(get_evaluator_review_ranking())

        evaluator_role = guild.get_role(config.ROLE_EVALUATOR)
        if evaluator_role is None:

            embed = discord.Embed(
                title=member_texts.RANKING_TITLE_SURVEY,
                description=member_texts.RANKING_NO_EVALUATOR,
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

        title = self.build_ranking_title(member_texts.RANKING_TITLE_SURVEY,role)

        return await self.build_ranking_embed(
            guild,
            ranking_list,
            title,
            member_texts.RANKING_UNIT_VOTE,
            role
        )

    # ==================================================
    # ランキング表題作成
    # ==================================================
    def build_ranking_title(self,title,role=None):
        """ロールを指定したときだけ、表題の前にロール名を付ける。"""

        if role is None:
            return title

        return member_texts.RANKING_TITLE_WITH_ROLE.format(
            role=role.name,
            title=title
        )

    # ==================================================
    # ランキングEmbed作成
    # ==================================================
    async def build_ranking_embed(self,guild,rankings,title,suffix,role=None):

        pages = []
        lines = []
        rank = 1

        formatters = {"TIME": lambda v: format_time(v)}

        for user_id, value in rankings:

            # アンケート以外は0を表示しない
            if suffix != member_texts.RANKING_UNIT_VOTE and value == 0:
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

            value_text = formatters.get(
                suffix,
                lambda v: member_texts.RANKING_VALUE.format(
                    value=f"{v:,}",
                    unit=suffix
                )
            )(value)

            lines.append(
                member_texts.RANKING_LINE.format(
                    rank=rank,
                    value=value_text,
                    user=member.mention
                )
            )

            rank += 1

            # 50件ごとにEmbedを分ける
            if len(lines) == 50:
                pages.append(lines)
                lines = []

        if lines:
            pages.append(lines)

        if not pages:
            pages.append([member_texts.RANKING_EMPTY])

        embeds = []

        for i, page in enumerate(pages):

            embed = discord.Embed(
                title=(
                    title
                    if i == 0
                    else member_texts.RANKING_TITLE_CONTINUED.format(title=title)
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
            await interaction.response.send_message(common_texts.NO_PERMISSION,ephemeral=True)
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
