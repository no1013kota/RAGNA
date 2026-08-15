"""精霊（仮メンバー）の登録、評価、転生フローを管理するCog。"""

import logging
import discord
import config
import asyncio

from discord.ext import commands, tasks
from discord import app_commands
from datetime import datetime, timedelta
from .trial_member_views import (
    EvaluationPanelView,
    TrialMemberEndSurveyView
)
from database import (
    add_balance,
    add_trial_member,
    add_evaluation,
    add_comment,
    get_trial_member,
    get_trial_member_thread,
    has_extension,
    add_extension,
    delete_trial_member,
    extend_trial_member_end_date,
    get_trial_member_class_and_thread,
    get_all_trial_member_end_dates,
    get_trial_member_end_date,
    get_evaluation_log_channel,
    set_evaluation_log_channel,
    add_trial_member_end_survey,
    get_expired_trial_member_end_surveys,
    delete_trial_member_end_survey,
    remove_start_ticket,
    clear_trial_member_evaluations
)
from utils import ensure_panel_message


logger = logging.getLogger(__name__)


class TrialMember(commands.Cog):
    def __init__(self,bot: commands.Bot):
        self.bot = bot

        self.check_trial_member_end.start()
        self.check_trial_member_end_surveys.start()
        self.evaluation_panel.start()

    # 評価権限
    def is_evaluator(self,member: discord.Member):

        evaluator_role = member.guild.get_role(config.ROLE_EVALUATOR)

        return (evaluator_role is not None and evaluator_role in member.roles)

    def is_comment_editor(self,member: discord.Member):

        evaluator_role = member.guild.get_role(config.ROLE_EVALUATOR)

        return (evaluator_role is not None and evaluator_role in member.roles)

    def can_evaluate_trial_member(self,evaluator: discord.Member,trial_member_class: str):

        # 天使以外は評価不可
        evaluator_role = evaluator.guild.get_role(config.ROLE_EVALUATOR)

        if (evaluator_role is None or evaluator_role not in evaluator.roles):
            return False

        # S・A+はすべて評価可能
        if (evaluator.get_role(config.ROLE_CLASS_S) or evaluator.get_role(config.ROLE_A_PLUS)):
            return True

        # B+はB・Cのみ評価可能
        if evaluator.get_role(config.ROLE_B_PLUS):
            return trial_member_class in ["B", "C"]

        return False

    # 延長処理
    def get_extension_days(self,overall_score: int | None) -> int:

        if overall_score == 5:
            return 3

        if overall_score == 4:
            return 2

        if overall_score == 3:
            return 1

        return 0

    # ==================================================
    # 召喚
    # ==================================================
    @app_commands.guilds(discord.Object(id=config.GUILD_ID))
    @app_commands.default_permissions(administrator=True)
    @app_commands.command(name="召喚",description="対象者を精霊にします")
    @app_commands.describe(user="精霊にする対象",class_name="クラス（A / B / C）")
    async def start_trial_member(self,interaction: discord.Interaction,user: discord.Member,class_name: str):

        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        if guild is None:
            await interaction.followup.send("サーバー情報を取得できませんでした。",ephemeral=True)
            return

        # Botは対象外
        if user.bot:
            await interaction.followup.send("Botは選択できません。",ephemeral=True)
            return

        # ロール取得
        applicant_role = guild.get_role(config.ROLE_APPLICANT)
        demoted_role = guild.get_role(config.ROLE_DEMOTED)
        trial_member_role = guild.get_role(config.ROLE_TRIAL_MEMBER)
        associate_member_role = guild.get_role(config.ROLE_ASSOCIATE_MEMBER)
        member_role = guild.get_role(config.ROLE_MEMBER)
        enrollment_waiting_role = guild.get_role(config.ROLE_ENROLLMENT_WAITING)
        interviewing_role = guild.get_role(config.ROLE_INTERVIEWING)

        class_s_role = guild.get_role(config.ROLE_CLASS_S)
        class_a_role = guild.get_role(config.ROLE_CLASS_A)
        class_b_role = guild.get_role(config.ROLE_CLASS_B)
        class_c_role = guild.get_role(config.ROLE_CLASS_C)

        # 面接中かどうか
        is_interviewing = (interviewing_role is not None and interviewing_role in user.roles)

        # 評価落ちは召喚不可
        if (demoted_role is not None and demoted_role in user.roles):
            await interaction.followup.send("魔物は返済するまで精霊になれません。",ephemeral=True)
            return

        # すでに仮メンバーとしてDB登録されている場合
        if get_trial_member(user.id) is not None:
            await interaction.followup.send("対象者はすでに精霊として登録されています。",ephemeral=True)
            return

        # クラス確認
        class_name = class_name.upper().strip()

        if class_name not in ["A", "B", "C"]:
            await interaction.followup.send(
                "クラスは A・B・C のいずれかで入力してください。",
                ephemeral=True
            )
            return

        # 指定されたクラスロール
        class_roles = {
            "A": class_a_role,
            "B": class_b_role,
            "C": class_c_role
        }

        selected_class_role = class_roles[class_name]

        if trial_member_role is None:
            await interaction.followup.send("精霊ロールが見つかりません。",ephemeral=True)
            return

        if selected_class_role is None:
            await interaction.followup.send(f"{class_name}クラスロールが見つかりません。",ephemeral=True)
            return

        # 召喚日・転生日
        start_date = datetime.now()
        end_date = (start_date + timedelta(days=config.TRIAL_MEMBER_PERIOD_DAYS))

        # 自己紹介URL取得
        intro_url = await self.get_intro_url(user)

        # 評価スレッドを先に作成
        try:
            thread_id = await self.create_evaluation_thread(
                member=user,
                trial_member_class=class_name,
                intro_url=intro_url,
                start_date=start_date,
                end_date=end_date
            )

        except discord.HTTPException as e:
            logger.warning(f"召喚時の評価スレッド作成失敗：{user.id} / {e}")
            await interaction.followup.send("評価スレッドの作成に失敗しました。",ephemeral=True)
            return

        if thread_id is None:
            await interaction.followup.send("評価フォーラムが見つかりません。",ephemeral=True)
            return

        # 召喚前に削除するロール
        roles_to_remove = []

        for role in (
            enrollment_waiting_role,
            applicant_role,
            interviewing_role,
            associate_member_role,
            member_role,
            class_s_role,
            class_a_role,
            class_b_role,
            class_c_role
        ):
            if role and role in user.roles:
                roles_to_remove.append(role)

        # 召喚時に追加するロール
        #
        # 指定クラスロールは削除対象に含まれているため、
        # 現在持っているかに関係なく必ず再追加する
        roles_to_add = [selected_class_role]

        if trial_member_role not in user.roles:
            roles_to_add.insert(0,trial_member_role)

        # ロール変更
        try:
            if roles_to_remove:
                await user.remove_roles(*roles_to_remove,reason=f"{interaction.user}による召喚処理")

            if roles_to_add:
                await user.add_roles(*roles_to_add,reason=f"{interaction.user}による召喚処理")

        except discord.HTTPException as e:
            logger.warning(f"召喚時のロール変更失敗：{user.id} / {e}")

            # ロール変更に失敗した場合は、
            # 作成した評価スレッドを削除
            thread = guild.get_thread(thread_id)

            if thread is None:
                try:
                    thread = await guild.fetch_channel(thread_id)

                except discord.HTTPException:
                    thread = None

            if thread:
                try:
                    await thread.delete()

                except discord.HTTPException:
                    pass

            await interaction.followup.send("ロールの変更に失敗しました。",ephemeral=True)
            return

        # DB登録
        add_trial_member(
            user_id=user.id,
            trial_member_class=class_name,
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
            intro_url=intro_url,
            thread_id=thread_id
        )

        # 召喚チケットを使用していた場合は消費済みにする
        if enrollment_waiting_role and enrollment_waiting_role in roles_to_remove:
            remove_start_ticket(user.id)

        # 初回の召喚だけ初期coinを付与
        if is_interviewing:
            add_balance(user.id,config.START_COIN)

            start_type = "召喚"
            coin_text = f"\n初期所持金：{config.START_COIN:,} coin"

        else:
            start_type = "再召喚"
            coin_text = "\n初期所持金の再付与はありません。"

        await interaction.followup.send(
            (
                f"{user.mention} を"
                f" **{class_name}クラス**へ"
                f"{start_type}させました。\n"
                f"転生予定："
                f"{end_date.strftime('%Y/%m/%d')}"
                f"{coin_text}"
            ),
            ephemeral=True
        )

    # ==================================================
    # 自己紹介URL取得
    # ==================================================
    async def get_intro_url(self,member: discord.Member):

        channel_ids = [
            config.CHANNEL_TRIAL_MEMBER_MALE,
            config.CHANNEL_TRIAL_MEMBER_FEMALE
        ]

        for channel_id in channel_ids:

            channel = self.bot.get_channel(channel_id)
            if channel is None:
                try:
                    channel = await self.bot.fetch_channel(channel_id)

                except discord.NotFound:
                    continue

                except discord.HTTPException as e:
                    logger.warning(f"自己紹介チャンネル取得失敗：{channel_id} / {e}")
                    continue

            try:
                async for message in channel.history(limit=None):

                    if message.author.id == member.id:
                        return message.jump_url

            except discord.HTTPException as e:
                logger.warning(f"自己紹介履歴取得失敗：{channel_id} / {e}")

        return None

    # ==================================================
    # 評価スレッド作成
    # ==================================================
    async def create_evaluation_thread(
        self,
        member: discord.Member,
        trial_member_class: str,
        intro_url: str | None,
        start_date: datetime,
        end_date: datetime
    ):

        if trial_member_class == "A":
            forum_id = config.FORUM_EVAL_A

        elif trial_member_class == "B":
            forum_id = config.FORUM_EVAL_B

        else:
            forum_id = config.FORUM_EVAL_C

        forum = self.bot.get_channel(forum_id)

        if forum is None:
            try:
                forum = await self.bot.fetch_channel(forum_id)

            except discord.NotFound:
                logger.warning(f"評価フォーラムが見つかりません：{forum_id}")
                return None

            except discord.HTTPException as e:
                logger.warning(f"評価フォーラム取得失敗：{forum_id} / {e}")
                return None

        intro_text = (
            intro_url
            if intro_url
            else "未登録"
        )

        embed = discord.Embed(
            description=(
                f"{member.mention}\n\n"
                f"【召喚日】{start_date.strftime('%Y/%m/%d')}\n"
                f"【転生予定】{end_date.strftime('%Y/%m/%d')}\n"
                f"{intro_text}"
            ),
            color=config.COLOR_WHITE
        )
        embed.set_thumbnail(url=member.display_avatar.url)

        thread = await forum.create_thread(name=member.display_name,embed=embed)

        return thread.thread.id

    # ==================================================
    # 評価スレッド内容コピー
    # ==================================================
    async def copy_evaluation_thread(
        self,
        source_thread: discord.Thread,
        target_thread: discord.Thread,
        skip_first_message: bool = False
    ) -> bool:

        try:
            first_message = True

            async for message in source_thread.history(oldest_first=True,limit=None):

                if skip_first_message and first_message:
                    first_message = False
                    continue

                first_message = False

                if message.embeds:
                    for embed in message.embeds:
                        await target_thread.send(embed=embed)

                if message.content or message.attachments:

                    text = (
                        f"【{message.author.display_name}】\n"
                        f"{message.content}"
                    )

                    if len(text) > 1900:
                        text = text[:1900] + "\n...(省略)"

                    files = []

                    if message.attachments:
                        files = [
                            await attachment.to_file()
                            for attachment in message.attachments
                        ]

                    await target_thread.send(content=text,files=files)

        except discord.HTTPException as e:
            logger.warning(f"評価スレッドコピー失敗：{source_thread.id} / {e}")
            return False

        return True

    # ==================================================
    # 評価員個人の評価シート取得・作成
    # ==================================================
    async def get_evaluator_log_channel(self,evaluator: discord.Member):

        channel_id = get_evaluation_log_channel(evaluator.id)

        if channel_id:
            channel = evaluator.guild.get_channel(channel_id)

            if channel:
                return channel

        category = evaluator.guild.get_channel(config.CATEGORY_EVALUATION_LOG)

        if category is None:
            return None

        overwrites = {
            evaluator.guild.default_role:
                discord.PermissionOverwrite(view_channel=False),
            evaluator:
                discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True
                )
        }

        channel = await evaluator.guild.create_text_channel(
            name=f"評価シート-{evaluator.display_name}",
            category=category,
            overwrites=overwrites
        )

        set_evaluation_log_channel(evaluator.id,channel.id)

        return channel

    async def send_evaluator_log(
        self,
        evaluator: discord.Member,
        trial_member: discord.Member,
        title: str,
        voice_score: int | None,
        communication_score: int | None,
        charm_score: int | None,
        overall_score: int | None,
        note: str | None
    ):

        channel = await self.get_evaluator_log_channel(evaluator)

        if channel is None:
            return

        embed = discord.Embed(
            title=title,
            color=config.COLOR_BLUE
        )
        embed.set_footer(
            text=f"{trial_member.display_name} / @{trial_member.name}"
        )

        lines = [f"{trial_member.mention}\n"]

        if voice_score is not None:
            lines.append(f"【声/音質】{voice_score}")

        if communication_score is not None:
            lines.append(f"【トーク力】{communication_score}")

        if charm_score is not None:
            lines.append(f"【個性/魅力】{charm_score}")

        if overall_score is not None:
            lines.append(f"【総合評価】{overall_score}")

        if note:
            lines.append(f"【コメント】{note}")

        embed.description = "\n".join(lines)
        embed.set_thumbnail(url=trial_member.display_avatar.url)

        await channel.send(embed=embed)

        return channel

    # ==================================================
    # 精霊スレッド転生予定更新
    # ==================================================
    async def update_trial_member_end_embed(self,trial_member_id: int):

        thread_id = get_trial_member_thread(trial_member_id)

        if thread_id is None:
            return

        thread = self.bot.get_channel(thread_id)

        if thread is None:
            try:
                thread = await self.bot.fetch_channel(thread_id)

            except discord.NotFound:
                logger.warning(f"評価スレッドが見つかりません：{thread_id}")
                return

            except discord.HTTPException as e:
                logger.warning(f"評価スレッド取得失敗：{thread_id} / {e}")
                return

        end_date = get_trial_member_end_date(trial_member_id)

        if end_date is None:
            return

        end_text = datetime.fromisoformat(end_date).strftime("%Y/%m/%d")

        starter = await thread.fetch_message(thread.id)

        if not starter.embeds:
            return

        embed = starter.embeds[0].copy()
        lines = embed.description.split("\n")

        for i, line in enumerate(lines):
            if line.startswith("【転生予定】"):
                lines[i] = f"【転生予定】{end_text}"

        embed.description = "\n".join(lines)

        await starter.edit(embed=embed)

    # ==================================================
    # 評価共通処理
    # ==================================================
    async def execute_evaluation(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        voice_score: int,
        conversation_score: int,
        charm_score: int,
        overall_score: int,
        note: str | None
    ):
        # 応答を保留
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        # 評価権限確認
        if not self.is_evaluator(interaction.user):
            await interaction.followup.send("評価権限がありません。",ephemeral=True)
            return

        # 点数確認
        if (
            not 1 <= voice_score <= 10
            or not 1 <= conversation_score <= 10
            or not 1 <= charm_score <= 10
        ):
            await interaction.followup.send("条件に沿って入力してください。",ephemeral=True)
            return

        if not 1 <= overall_score <= 5:
            await interaction.followup.send("総合評価は1～5で入力してください。",ephemeral=True)
            return

        # 評価対象確認
        trial_member = get_trial_member(member.id)

        if trial_member is None:
            await interaction.followup.send("対象者は精霊として登録されていません。",ephemeral=True)
            return

        trial_member_class = trial_member[1]

        if not self.can_evaluate_trial_member(interaction.user,trial_member_class):
            await interaction.followup.send("この精霊は評価できません。",ephemeral=True)
            return

        # スレッド取得
        thread_id = get_trial_member_thread(member.id)

        if thread_id is None:
            await interaction.followup.send("評価スレッドが見つかりません。",ephemeral=True)
            return

        thread = self.bot.get_channel(thread_id)

        if thread is None:
            try:
                thread = await self.bot.fetch_channel(thread_id)

            except discord.NotFound:
                await interaction.followup.send("評価スレッドが削除されています。",ephemeral=True)
                return

            except discord.HTTPException:
                await interaction.followup.send("評価スレッドの取得に失敗しました。",ephemeral=True)
                return

        # 評価Embed
        embed = discord.Embed(color=config.COLOR_GREEN)
        embed.description = (
            f"{interaction.user.mention}\n\n"
            f"【声/音質】{voice_score}\n"
            f"【トーク力】{conversation_score}\n"
            f"【個性/魅力】{charm_score}\n"
            f"【総合評価】{overall_score}\n"
            f"【コメント】{note if note else 'なし'}"
        )
        embed.set_thumbnail(url=interaction.user.display_avatar.url)

        try:
            await thread.send(embed=embed)

        except discord.HTTPException:
            await interaction.followup.send("評価スレッドへの送信に失敗しました。",ephemeral=True)
            return

        # DB保存
        add_evaluation(
            trial_member_id=member.id,
            evaluator_id=interaction.user.id,
            voice_score=voice_score,
            conversation_score=conversation_score,
            charm_score=charm_score,
            overall_score=overall_score,
            note=note
        )

        # 延長処理
        extension_days = self.get_extension_days(overall_score)

        if extension_days > 0:
            already_extended = has_extension(member.id,interaction.user.id)

            if not already_extended:
                extend_trial_member_end_date(member.id,extension_days)
                add_extension(member.id,interaction.user.id)

                await self.update_trial_member_end_embed(member.id)

        # 評価員個人の評価シートへ記録
        log_channel = await self.send_evaluator_log(
            evaluator=interaction.user,
            trial_member=member,
            title="評価",
            voice_score=voice_score,
            communication_score=conversation_score,
            charm_score=charm_score,
            overall_score=overall_score,
            note=note
        )

        message = "評価を登録しました。"

        if log_channel:
            message += f"\n{log_channel.mention} で内容を確認できます。"

        await interaction.followup.send(message,ephemeral=True)

    # ==================================================
    # 追記共通処理
    # ==================================================
    async def execute_comment(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        voice_score: int | None,
        conversation_score: int | None,
        charm_score: int | None,
        overall_score: int | None,
        note: str | None
    ):
        # 応答を保留
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        # 追記権限確認
        if not self.is_comment_editor(interaction.user):
            await interaction.followup.send("追記権限がありません。",ephemeral=True)
            return

        # 入力内容確認
        if (
            voice_score is None
            and conversation_score is None
            and charm_score is None
            and overall_score is None
            and not note
        ):
            await interaction.followup.send("追記内容を1つ以上入力してください。",ephemeral=True)
            return

        # 点数確認
        if (
            (voice_score is not None and not 1 <= voice_score <= 10)
            or (conversation_score is not None and not 1 <= conversation_score <= 10)
            or (charm_score is not None and not 1 <= charm_score <= 10)
        ):
            await interaction.followup.send("条件に沿って入力してください。",ephemeral=True)
            return

        if (
            overall_score is not None
            and not 1 <= overall_score <= 5
        ):
            await interaction.followup.send("総合評価は1～5で入力してください。",ephemeral=True)
            return

        # 対象仮メンバー確認
        trial_member = get_trial_member(member.id)

        if trial_member is None:
            await interaction.followup.send("対象者は精霊として登録されていません。",ephemeral=True)
            return

        trial_member_class = trial_member[1]

        if not self.can_evaluate_trial_member(interaction.user,trial_member_class):
            await interaction.followup.send("この精霊には追記できません。",ephemeral=True)
            return

        # スレッド取得
        thread_id = get_trial_member_thread(member.id)

        if thread_id is None:
            await interaction.followup.send("評価スレッドが見つかりません。",ephemeral=True)
            return

        thread = self.bot.get_channel(thread_id)

        if thread is None:
            try:
                thread = await self.bot.fetch_channel(thread_id)

            except discord.NotFound:
                await interaction.followup.send("評価スレッドが削除されています。",ephemeral=True)
                return

            except discord.HTTPException:
                await interaction.followup.send("評価スレッドの取得に失敗しました。",ephemeral=True)
                return

        # 追記Embed
        embed = discord.Embed(color=config.COLOR_BLUE)

        lines = [f"{interaction.user.mention}\n"]

        if voice_score is not None:
            lines.append(f"【声/音質】{voice_score}")

        if conversation_score is not None:
            lines.append(f"【トーク力】{conversation_score}")

        if charm_score is not None:
            lines.append(f"【個性/魅力】{charm_score}")

        if overall_score is not None:
            lines.append(f"【総合評価】{overall_score}")

        if note:
            lines.append(f"【コメント】{note}")

        embed.description = "\n".join(lines)
        embed.set_thumbnail(url=interaction.user.display_avatar.url)

        try:
            await thread.send(embed=embed)

        except discord.HTTPException:
            await interaction.followup.send("評価スレッドへの送信に失敗しました。",ephemeral=True)
            return

        # DB保存
        add_comment(
            trial_member_id=member.id,
            evaluator_id=interaction.user.id,
            voice_score=voice_score,
            conversation_score=conversation_score,
            charm_score=charm_score,
            overall_score=overall_score,
            note=note
        )

        # 延長処理
        extension_days = self.get_extension_days(overall_score)

        if extension_days > 0:
            already_extended = has_extension(member.id,interaction.user.id)

            if not already_extended:
                extend_trial_member_end_date(member.id,extension_days)
                add_extension(member.id,interaction.user.id)

                await self.update_trial_member_end_embed(member.id)

        # 評価員個人の評価シートへ記録
        log_channel = await self.send_evaluator_log(
            evaluator=interaction.user,
            trial_member=member,
            title="追記",
            voice_score=voice_score,
            communication_score=conversation_score,
            charm_score=charm_score,
            overall_score=overall_score,
            note=note
        )

        message = "追記を登録しました。"

        if log_channel:
            message += f"\n{log_channel.mention} で内容を確認できます。"

        await interaction.followup.send(message,ephemeral=True)

    # ==================================================
    # 評価スレッド保存
    # ==================================================
    async def archive_evaluation_thread(
        self,
        member: discord.Member,
        archive_forum_id: int,
        header_embed: discord.Embed | None = None
    ) -> bool:

        guild = member.guild
        trial_member_data = get_trial_member_class_and_thread(member.id)

        if trial_member_data is None:
            return True

        thread_id = trial_member_data[1]

        if thread_id is None:
            logger.warning(f"評価スレッドIDがありません：{member.id}")
            return False

        # 元の評価スレッド取得
        evaluation_thread = guild.get_thread(thread_id)

        if evaluation_thread is None:
            try:
                evaluation_thread = await guild.fetch_channel(thread_id)

            except discord.NotFound:
                logger.warning(f"評価スレッドが見つかりません：{thread_id}")
                return True

            except discord.HTTPException as e:
                logger.warning(f"評価スレッド取得失敗：{thread_id} / {e}")
                return False

        # 保存フォーラム取得
        archive_forum = guild.get_channel(archive_forum_id)

        if archive_forum is None:
            try:
                archive_forum = await guild.fetch_channel(archive_forum_id)

            except discord.NotFound:
                logger.warning(f"評価保存フォーラムが見つかりません：{archive_forum_id}")
                return False

            except discord.HTTPException as e:
                logger.warning(f"評価保存フォーラム取得失敗：{archive_forum_id} / {e}")
                return False

        # 保存スレッド作成
        try:
            archive_thread, _ = await archive_forum.create_thread(
                name=f"{member.display_name}/{member.name}",
                content="\u200b"
            )

        except discord.HTTPException as e:
            logger.warning(f"評価保存スレッド作成失敗：{member.id} / {e}")
            return False

        # 評価落ち移行などの説明Embed
        if header_embed is not None:
            try:
                await archive_thread.send(embed=header_embed)

            except discord.HTTPException as e:
                logger.warning(f"評価保存ヘッダー送信失敗：{member.id} / {e}")

                try:
                    await archive_thread.delete()

                except discord.HTTPException:
                    pass

                return False

        # 元スレッドの内容をコピー
        copied = await self.copy_evaluation_thread(
            source_thread=evaluation_thread,
            target_thread=archive_thread
        )

        if not copied:
            try:
                await archive_thread.delete()

            except discord.NotFound:
                pass

            except discord.HTTPException as e:
                logger.warning(f"不完全な評価保存スレッド削除失敗：{archive_thread.id} / {e}")

            return False

        # コピー成功後に元スレッド削除
        try:
            await evaluation_thread.delete()

        except discord.NotFound:
            pass

        except discord.HTTPException as e:
            logger.warning(f"元評価スレッド削除失敗：{thread_id} / {e}")
            return False

        return True

    # ==================================================
    # 仮メンバーの評価落ち移行
    # ==================================================
    async def archive_demoted_trial_member(self,member: discord.Member) -> bool:

        trial_member_data = get_trial_member_class_and_thread(member.id)

        if trial_member_data is None:
            return True

        trial_member_class = trial_member_data[0]

        archive_embed = discord.Embed(
            title="評価落ち移行",
            description=(
                f"対象者：{member.mention}\n"
                f"クラス：**{trial_member_class}クラス**"
            ),
            color=config.COLOR_RED
        )
        archive_embed.set_thumbnail(url=member.display_avatar.url)

        archived = await self.archive_evaluation_thread(
            member=member,
            archive_forum_id=config.FORUM_DEMOTED_ARCHIVE,
            header_embed=archive_embed
        )

        if not archived:
            return False

        # 評価記録リセット
        clear_trial_member_evaluations(member.id)

        # 仮メンバー情報削除
        delete_trial_member(member.id)

        return True

    # ==================================================
    # 共通転生処理
    # ==================================================
    async def execute_trial_member_end(
        self,
        member: discord.Member,
        executor: discord.Member | None = None,
        reason: str = "自動転生",
        is_leave: bool = False
    ):
        guild = member.guild
        trial_member_class = None
        trial_member_data = get_trial_member_class_and_thread(member.id)
        archive_forum_id = None

        if trial_member_data:
            trial_member_class = trial_member_data[0]

            if is_leave:
                archive_forum_id = config.FORUM_LEAVE_ARCHIVE

            elif trial_member_class == "A":
                archive_forum_id = config.FORUM_ARCHIVE_A

            elif trial_member_class == "B":
                archive_forum_id = config.FORUM_ARCHIVE_B

            elif trial_member_class == "C":
                archive_forum_id = config.FORUM_ARCHIVE_C

        # 評価スレッドを保存
        if archive_forum_id is not None:
            archived = await self.archive_evaluation_thread(
                member=member,
                archive_forum_id=archive_forum_id
            )

            if not archived:
                logger.warning(f"転生時の評価スレッド保存に失敗しました：{member.id}")
                return

        if not is_leave:
            # ロール変更
            trial_member_role = guild.get_role(config.ROLE_TRIAL_MEMBER)
            associate_member_role = guild.get_role(config.ROLE_ASSOCIATE_MEMBER)
            member_role = guild.get_role(config.ROLE_MEMBER)
            class_c_role = guild.get_role(config.ROLE_CLASS_C)

            # 仮メンバー期間後ロール決定
            end_role = None

            if trial_member_data:
                trial_member_class = trial_member_data[0]

                if trial_member_class == "C":
                    end_role = associate_member_role

                else:
                    end_role = member_role

            try:
                if (
                    trial_member_role
                    and trial_member_role in member.roles
                ):
                    await member.remove_roles(trial_member_role)

                # Cクラスで転生した者はCロールも外す
                if (
                    trial_member_class == "C"
                    and class_c_role
                    and class_c_role in member.roles
                ):
                    await member.remove_roles(class_c_role)

                if end_role:
                    await member.add_roles(end_role)

            except Exception as e:
                logger.warning(f"ロール変更失敗：{e}")

        if not is_leave:
            # アンケートDM
            try:
                expires_at = datetime.now() + timedelta(days=3)

                embed = discord.Embed(
                    title="アンケート",
                    description=(
                        "今後の運営改善のため、\n"
                        "簡単なアンケートにご協力ください。\n\n"
                        "※回答期限：3日\n"
                        "※回答結果は運営が保管しています"
                    ),
                    color=config.COLOR_WHITE
                )

                survey_message = await member.send(
                    embed=embed,
                    view=TrialMemberEndSurveyView()
                )

                add_trial_member_end_survey(
                    ended_trial_member_id=member.id,
                    channel_id=survey_message.channel.id,
                    message_id=survey_message.id,
                    expires_at=expires_at.isoformat()
                )

            except discord.Forbidden:
                logger.warning(f"アンケートDM送信失敗：{member.mention} はDMを受信できません。")

            except discord.HTTPException as e:
                logger.warning(f"アンケートDM送信失敗：{member.mention} / {e}")

            # ログ
            log_channel = guild.get_channel(config.CHANNEL_TRIAL_MEMBER_END_LOG)

            if log_channel:
                description = (
                    f"対象者：{member.mention}\n"
                    f"理由：{reason}"
                )

                embed = discord.Embed(
                    title="転生",
                    description=description,
                    color=config.COLOR_BLUE
                )
                embed.set_thumbnail(url=member.display_avatar.url)

                await log_channel.send(embed=embed)

        # 評価記録リセット
        clear_trial_member_evaluations(member.id)

        # 仮メンバー情報削除
        delete_trial_member(member.id)

    # ==================================================
    # 表示名変更
    # ==================================================
    @commands.Cog.listener()
    async def on_member_update(self,before: discord.Member,after: discord.Member):

        if before.display_name == after.display_name:
            return

        channel_id = get_evaluation_log_channel(after.id)

        if channel_id is None:
            return

        channel = after.guild.get_channel(channel_id)

        if channel is None:
            return

        new_name = f"評価シート-{after.display_name}"

        if channel.name != new_name:
            try:
                await channel.edit(name=new_name)

            except discord.HTTPException:
                pass

    # ==================================================
    # メンバー脱退
    # ==================================================
    @commands.Cog.listener()
    async def on_member_remove(self,member: discord.Member):

        trial_member = get_trial_member(member.id)

        if trial_member is None:
            return

        await self.execute_trial_member_end(
            member=member,
            reason="サーバー脱退",
            is_leave=True
        )

    # ==================================================
    # 転生自動確認
    # ==================================================
    @tasks.loop(hours=1)
    async def check_trial_member_end(self):

        now = datetime.now()
        try:
            trial_members = get_all_trial_member_end_dates()
        except Exception:
            logger.exception("自動転生の対象者一覧を取得できませんでした")
            return

        guild = self.bot.get_guild(config.GUILD_ID)

        if guild is None:
            return

        for user_id, end_date in trial_members:
            try:
                end_datetime = datetime.fromisoformat(end_date)

            except ValueError:
                continue

            if end_datetime > now:
                continue

            member = guild.get_member(user_id)

            if member is None:
                continue

            try:
                await self.execute_trial_member_end(
                    member=member,
                    reason="自動転生"
                )
            except Exception:
                # 1人の失敗で他の対象者や次回の定期確認まで停止させない。
                logger.exception("自動転生に失敗しました: user_id=%s", user_id)

    @check_trial_member_end.before_loop
    async def before_check_trial_member_end(self):
        await self.bot.wait_until_ready()

    # ==================================================
    # アンケート期限確認
    # ==================================================
    @tasks.loop(hours=1)
    async def check_trial_member_end_surveys(self):

        try:
            surveys = get_expired_trial_member_end_surveys()
        except Exception:
            logger.exception("期限切れアンケートの一覧を取得できませんでした")
            return

        for ended_trial_member_id, channel_id, message_id in surveys:
            try:
                channel = self.bot.get_channel(channel_id)

                if channel is None:
                    channel = await self.bot.fetch_channel(channel_id)

                message = await channel.fetch_message(message_id)
                await message.delete()

            except discord.NotFound:
                pass

            except discord.HTTPException as e:
                logger.warning(f"アンケート削除失敗：{ended_trial_member_id} / {e}")
                # 一時的な失敗ならDB記録を残し、次回ループで再試行する。
                continue

            try:
                delete_trial_member_end_survey(ended_trial_member_id)
            except Exception:
                logger.exception(
                    "期限切れアンケートのDB記録を削除できませんでした: user_id=%s",
                    ended_trial_member_id,
                )

    @check_trial_member_end_surveys.before_loop
    async def before_check_trial_member_end_surveys(self):
        await self.bot.wait_until_ready()

    # ==================================================
    # 評価パネル自動設置
    # ==================================================
    @tasks.loop(minutes=5)
    async def evaluation_panel(self):

        guild = self.bot.get_guild(config.GUILD_ID)

        if guild is None:
            return

        embed = discord.Embed(
            title="評価シート",
            description=(
                "\u200b\n"
                "**評価**\n"
                "-# 指定した精霊の評価を登録します\n\n"
                "**追記**\n"
                "-# 登録済みのスレッドに評価を追記します\n\n"
                "**評価チェック**\n"
                "-# 評価可能な精霊をXP順に表示します"
            ),
            color=config.COLOR_WHITE
        )

        await ensure_panel_message(
            self.bot,
            guild,
            config.CHANNEL_EVALUATION_PANEL,
            panel_title="評価シート",
            embed=embed,
            view=EvaluationPanelView(),
            panel_name="評価パネル",
        )

    @evaluation_panel.before_loop
    async def before_evaluation_panel(self):
        await self.bot.wait_until_ready()
        await asyncio.sleep(5)

    # ==================================================
    # 転生
    # ==================================================
    @app_commands.guilds(discord.Object(id=config.GUILD_ID))
    @app_commands.default_permissions(administrator=True)
    @app_commands.command(name="転生",description="対象の精霊を手動で転生させます")
    @app_commands.describe(user="対象者",reason="理由")
    async def force_trial_member_end(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        reason: str = "転生"
    ):

        await interaction.response.defer(ephemeral=True)

        if get_trial_member(user.id) is None:
            await interaction.followup.send("対象者は精霊として登録されていません。",ephemeral=True)
            return

        await self.execute_trial_member_end(
            member=user,
            executor=interaction.user,
            reason=reason
        )

        await interaction.followup.send(f"{user.mention} をロール変更させました。",ephemeral=True)

    # ==================================================
    # Cog終了
    # ==================================================
    def cog_unload(self):
        self.check_trial_member_end.cancel()
        self.check_trial_member_end_surveys.cancel()
        self.evaluation_panel.cancel()


# ==========================================
# Cog読込
# ==========================================
async def setup(bot: commands.Bot):
    await bot.add_cog(TrialMember(bot))
    bot.add_view(EvaluationPanelView())
    bot.add_view(TrialMemberEndSurveyView())

    logger.info("TrialMember Cog 登録完了")
