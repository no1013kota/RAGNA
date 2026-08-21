"""精霊のクラス候補表示とクラス変更フローを管理するCog。"""

import logging

import discord
import config

from discord.ext import commands
from discord import app_commands
from datetime import datetime
from database.trial_member import (
    get_trial_member,
    get_trial_member_thread,
    get_class_change_candidates,
    update_trial_member_class,
    update_trial_member_thread,
    add_class_change,
    clear_trial_member_evaluations
)
from texts import common as common_texts
from texts import member as member_texts


logger = logging.getLogger(__name__)


class ClassChange(commands.Cog):
    def __init__(self,bot):
        self.bot = bot

    # ==================================================
    # 権限確認
    # ==================================================
    def has_permission(self,member: discord.Member):

        return any(
            role.id in config.PERMISSION_CLASS_MANAGEMENT
            for role in member.roles
        )

    # ==================================================
    # クラス候補
    # ==================================================
    @app_commands.guilds(discord.Object(id=config.GUILD_ID))
    @app_commands.command(name="評価状況",description="総合評価からクラス変更候補を表示します")
    @app_commands.describe(trial_member_class="対象クラス")
    @app_commands.choices(
        trial_member_class=[
            app_commands.Choice(name="全体",value="all"),
            app_commands.Choice(name="A",value="A"),
            app_commands.Choice(name="B",value="B"),
            app_commands.Choice(name="C",value="C")
        ]
    )
    async def class_candidates(self,interaction: discord.Interaction,trial_member_class: app_commands.Choice[str]):

        if not self.has_permission(interaction.user):
            await interaction.response.send_message(common_texts.NO_PERMISSION,ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        target_class = (
            None
            if trial_member_class.value == "all"
            else trial_member_class.value
        )

        candidates = get_class_change_candidates(target_class)

        if not candidates:
            await interaction.followup.send(member_texts.CANDIDATE_NOT_ENOUGH,ephemeral=True)
            return

        top_candidates = sorted(
            candidates,
            key=lambda x: (
                -x[2],
                -x[3],
                x[0]
            )
        )[:5]

        worst_candidates = sorted(
            candidates,
            key=lambda x: (
                x[2],
                -x[3],
                x[0]
            )
        )[:3]

        top_lines = []
        worst_lines = []

        for rank, candidate in enumerate(top_candidates,start=1):

            (
                user_id,
                current_class,
                average_score,
                evaluator_count
            ) = candidate

            member = interaction.guild.get_member(int(user_id))

            if member is None:
                try:
                    member = await interaction.guild.fetch_member(int(user_id))

                except discord.NotFound:
                    continue

                except discord.HTTPException as e:
                    logger.warning(f"クラス候補対象者取得失敗：{user_id} / {e}")
                    continue

            top_lines.append(
                member_texts.CANDIDATE_LINE.format(
                    rank=rank,
                    score=f"{average_score:.2f}",
                    count=evaluator_count,
                    user=member.mention
                )
            )

        for rank, candidate in enumerate(worst_candidates,start=1):

            (
                user_id,
                current_class,
                average_score,
                evaluator_count
            ) = candidate

            member = interaction.guild.get_member(int(user_id))

            if member is None:
                try:
                    member = await interaction.guild.fetch_member(int(user_id))

                except discord.NotFound:
                    continue

                except discord.HTTPException as e:
                    logger.warning(f"クラス候補対象者取得失敗：{user_id} / {e}")
                    continue

            worst_lines.append(
                member_texts.CANDIDATE_LINE.format(
                    rank=rank,
                    score=f"{average_score:.2f}",
                    count=evaluator_count,
                    user=member.mention
                )
            )

        title = (
            member_texts.CANDIDATE_TITLE_ALL
            if target_class is None
            else member_texts.CANDIDATE_TITLE_CLASS.format(
                class_name=target_class
            )
        )

        top_text = (
            "\n".join(top_lines)
            if top_lines
            else member_texts.CANDIDATE_EMPTY
        )

        worst_text = (
            "\n".join(worst_lines)
            if worst_lines
            else member_texts.CANDIDATE_EMPTY
        )

        embed = discord.Embed(
            title=title,
            description=member_texts.CANDIDATE_BODY.format(
                top=top_text,
                worst=worst_text
            ),
            color=config.COLOR_BLUE
        )

        await interaction.followup.send(embed=embed,ephemeral=True)

    # ==================================================
    # クラス分布
    # ==================================================
    @app_commands.guilds(discord.Object(id=config.GUILD_ID))
    @app_commands.command(name="クラス分布",description="現在のクラス分布を表示します")
    async def class_distribution(self,interaction: discord.Interaction):

        if not self.has_permission(interaction.user):
            await interaction.response.send_message(common_texts.NO_PERMISSION,ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild

        if guild is None:
            await interaction.followup.send(common_texts.GUILD_NOT_FOUND,ephemeral=True)
            return

        role_map = {
            "S": guild.get_role(config.ROLE_CLASS_S),
            "A+": guild.get_role(config.ROLE_A_PLUS),
            "A": guild.get_role(config.ROLE_CLASS_A),
            "B+": guild.get_role(config.ROLE_B_PLUS),
            "B": guild.get_role(config.ROLE_CLASS_B),
            "C": guild.get_role(config.ROLE_CLASS_C)
        }

        counts = {}

        for name, role in role_map.items():

            if role is None:
                counts[name] = 0
                continue

            counts[name] = sum(
                1
                for member in role.members
                if not member.bot
            )

        total = sum(counts.values())

        if total == 0:
            await interaction.followup.send(member_texts.DISTRIBUTION_EMPTY,ephemeral=True)
            return

        def percent(count):
            return count / total * 100

        a_group = counts["A+"] + counts["A"]
        b_group = counts["B+"] + counts["B"]

        embed = discord.Embed(
            title=member_texts.DISTRIBUTION_TITLE,
            description=member_texts.DISTRIBUTION_BODY.format(
                total=total,
                s=counts["S"],
                s_percent=f"{percent(counts['S']):.1f}",
                a_plus=counts["A+"],
                a_plus_percent=f"{percent(counts['A+']):.1f}",
                a=counts["A"],
                a_percent=f"{percent(counts['A']):.1f}",
                b_plus=counts["B+"],
                b_plus_percent=f"{percent(counts['B+']):.1f}",
                b=counts["B"],
                b_percent=f"{percent(counts['B']):.1f}",
                c=counts["C"],
                c_percent=f"{percent(counts['C']):.1f}",
                a_group=a_group,
                a_group_percent=f"{percent(a_group):.1f}",
                b_group=b_group,
                b_group_percent=f"{percent(b_group):.1f}"
            ),
            color=config.COLOR_BLUE
        )

        await interaction.followup.send(embed=embed,ephemeral=True)

    # ==================================================
    # クラス変更
    # ==================================================
    @app_commands.guilds(discord.Object(id=config.GUILD_ID))
    @app_commands.command(name="クラス変更",description="精霊のクラスを変更します")
    @app_commands.describe(user="クラスを変更する精霊",new_class="変更先クラス",note="変更理由・備考")
    @app_commands.choices(
        new_class=[
            app_commands.Choice(name="A",value="A"),
            app_commands.Choice(name="B",value="B"),
            app_commands.Choice(name="C",value="C")
        ]
    )
    async def change_class(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        new_class: app_commands.Choice[str],
        note: str = ""
    ):

        if not self.has_permission(interaction.user):
            await interaction.response.send_message(common_texts.NO_PERMISSION,ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild

        if guild is None:
            await interaction.followup.send(common_texts.GUILD_NOT_FOUND,ephemeral=True)
            return

        trial_member = get_trial_member(user.id)

        if trial_member is None:
            await interaction.followup.send(member_texts.NOT_REGISTERED,ephemeral=True)
            return

        old_class = trial_member[1]
        target_class = new_class.value

        if old_class == target_class:
            await interaction.followup.send(
                member_texts.CLASS_CHANGE_SAME.format(class_name=target_class),
                ephemeral=True
            )
            return

        class_roles = {
            "A": guild.get_role(config.ROLE_CLASS_A),
            "B": guild.get_role(config.ROLE_CLASS_B),
            "C": guild.get_role(config.ROLE_CLASS_C)
        }

        old_role = class_roles.get(old_class)
        new_role = class_roles.get(target_class)

        if new_role is None:
            await interaction.followup.send(
                member_texts.CLASS_ROLE_NOT_FOUND.format(class_name=target_class),
                ephemeral=True
            )
            return

        # ==================================================
        # 評価スレッド移動
        # ==================================================
        trial_member_cog = self.bot.get_cog("TrialMember")

        if trial_member_cog is None:
            await interaction.followup.send(member_texts.CLASS_CHANGE_COG_NOT_FOUND,ephemeral=True)
            return

        thread_id = get_trial_member_thread(user.id)
        thread = None
        new_thread = None
        new_thread_id = None

        if thread_id is not None:

            thread = guild.get_thread(thread_id)

            if thread is None:
                try:
                    thread = await guild.fetch_channel(thread_id)

                except discord.NotFound:
                    thread = None

                except discord.HTTPException as e:
                    logger.warning(f"クラス変更時評価スレッド取得失敗：{thread_id} / {e}")
                    await interaction.followup.send(member_texts.THREAD_FETCH_FAILED,ephemeral=True)
                    return

            if thread is not None:

                try:
                    new_thread_id = await trial_member_cog.create_evaluation_thread(
                        member=user,
                        trial_member_class=target_class,
                        intro_url=trial_member[4],
                        start_date=datetime.fromisoformat(trial_member[2]),
                        end_date=datetime.fromisoformat(trial_member[3])
                    )

                except discord.HTTPException as e:
                    logger.warning(f"クラス変更時新評価スレッド作成失敗：{user.id} / {e}")
                    await interaction.followup.send(member_texts.CLASS_CHANGE_NEW_THREAD_FAILED,ephemeral=True)
                    return

                if new_thread_id is None:
                    await interaction.followup.send(member_texts.CLASS_CHANGE_NEW_THREAD_FAILED,ephemeral=True)
                    return

                new_thread = guild.get_thread(new_thread_id)

                if new_thread is None:
                    try:
                        new_thread = await guild.fetch_channel(new_thread_id)

                    except discord.NotFound:
                        new_thread = None

                    except discord.HTTPException as e:
                        logger.warning(f"クラス変更時新評価スレッド取得失敗：{new_thread_id} / {e}")
                        new_thread = None

                if new_thread is None:
                    await interaction.followup.send(member_texts.CLASS_CHANGE_NEW_THREAD_NOT_FOUND,ephemeral=True)
                    return

                copied = await trial_member_cog.copy_evaluation_thread(
                    source_thread=thread,
                    target_thread=new_thread,
                    skip_first_message=True
                )

                if not copied:
                    try:
                        await new_thread.delete()

                    except discord.NotFound:
                        pass

                    except discord.HTTPException as e:
                        logger.warning(f"不完全な新評価スレッド削除失敗：{new_thread.id} / {e}")

                    await interaction.followup.send(member_texts.CLASS_CHANGE_THREAD_MOVE_FAILED,ephemeral=True)
                    return

        # ==================================================
        # クラスロール変更
        # ==================================================
        new_role_added = False

        try:
            if new_role not in user.roles:
                await user.add_roles(new_role,reason=f"{interaction.user}によるクラス変更")
                new_role_added = True

            if old_role is not None and old_role in user.roles:
                await user.remove_roles(old_role,reason=f"{interaction.user}によるクラス変更")

        except discord.HTTPException as e:
            logger.warning(f"クラス変更ロール変更失敗：{user.id} / {e}")

            if new_role_added:
                try:
                    await user.remove_roles(new_role,reason="クラス変更失敗によるロール復元")

                except discord.HTTPException as rollback_error:
                    logger.warning(f"クラス変更ロール復元失敗：{user.id} / {rollback_error}")

            if new_thread is not None:
                try:
                    await new_thread.delete()

                except discord.NotFound:
                    pass

                except discord.HTTPException as delete_error:
                    logger.warning(f"クラス変更失敗時新評価スレッド削除失敗：{new_thread.id} / {delete_error}")

            await interaction.followup.send(member_texts.CLASS_CHANGE_ROLE_FAILED,ephemeral=True)
            return

        # ==================================================
        # DB更新
        # ==================================================
        update_trial_member_class(user.id,target_class)

        if new_thread_id is not None:
            update_trial_member_thread(user.id,new_thread_id)

        # ==================================================
        # 旧評価スレッド削除
        # ==================================================
        if thread is not None:
            try:
                await thread.delete()

            except discord.NotFound:
                pass

            except discord.HTTPException as e:
                logger.warning(f"クラス変更時旧評価スレッド削除失敗：{thread_id} / {e}")

        add_class_change(
            executor_id=interaction.user.id,
            target_id=user.id,
            old_class=old_class,
            new_class=target_class
        )

        # ==================================================
        # 評価記録リセット
        # ==================================================
        clear_trial_member_evaluations(user.id)

        # ==================================================
        # ログ
        # ==================================================
        note_text = (
            note.strip()
            if note.strip()
            else member_texts.CLASS_CHANGE_NOTE_DEFAULT
        )

        log_channel = guild.get_channel(config.CHANNEL_CLASS_CHANGE_LOG)

        if log_channel is not None:

            embed = discord.Embed(
                title=member_texts.CLASS_CHANGE_LOG_TITLE,
                description=member_texts.CLASS_CHANGE_LOG_BODY.format(
                    actor=interaction.user.mention,
                    user=user.mention,
                    old_class=old_class,
                    new_class=target_class,
                    note=note_text
                ),
                color=config.COLOR_BLUE
            )

            embed.set_thumbnail(url=user.display_avatar.url)

            try:
                await log_channel.send(embed=embed)

            except discord.HTTPException as e:
                logger.warning(f"クラス変更ログ送信失敗：{user.id} / {e}")

        await interaction.followup.send(
            member_texts.CLASS_CHANGE_DONE.format(
                user=user.mention,
                old_class=old_class,
                new_class=target_class
            ),
            ephemeral=True
        )


async def setup(bot):
    await bot.add_cog(ClassChange(bot),guild=discord.Object(id=config.GUILD_ID))

    logger.info("ClassChange Cog 登録完了")
