"""精霊評価と転生後アンケートで使う永続Viewをまとめたモジュール。"""

import logging

import discord
import config

from database.trial_member import (
    add_evaluator_review,
    get_trial_member_end_survey,
    delete_trial_member_end_survey,
    get_trial_member,
    get_evaluated_trial_member_ids,
)
from database.xp import get_vc_time


logger = logging.getLogger(__name__)


# ==========================================
# 評価パネル
# ==========================================
class EvaluationPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

        self.add_item(EvaluateButton())
        self.add_item(CommentButton())
        self.add_item(EvaluationTargetListButton())


class EvaluateButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="評価",
            style=discord.ButtonStyle.primary,
            custom_id="evaluation_panel:evaluate")

    async def callback(self,interaction: discord.Interaction):

        trial_member_cog = interaction.client.get_cog("TrialMember")

        if trial_member_cog is None:
            await interaction.response.send_message("管理機能を取得失敗。",ephemeral=True)
            return

        if not trial_member_cog.is_evaluator(interaction.user):
            await interaction.response.send_message("評価権限がありません。",ephemeral=True)
            return

        await interaction.response.send_message(
            "評価する精霊を選択してください。",
            view=EvaluationUserSelectView(),
            ephemeral=True
        )


class CommentButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="追記",
            style=discord.ButtonStyle.success,
            custom_id="evaluation_panel:comment")

    async def callback(self,interaction: discord.Interaction):

        trial_member_cog = interaction.client.get_cog("TrialMember")

        if trial_member_cog is None:
            await interaction.response.send_message("管理機能を取得失敗。",ephemeral=True)
            return

        if not trial_member_cog.is_comment_editor(interaction.user):
            await interaction.response.send_message("追記権限がありません。",ephemeral=True)
            return

        await interaction.response.send_message(
            "追記する精霊を選択してください。",
            view=CommentUserSelectView(),
            ephemeral=True
        )


class EvaluationTargetListButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="評価チェック",
            style=discord.ButtonStyle.secondary,
            custom_id="evaluation_panel:target_list"
        )

    async def callback(self,interaction: discord.Interaction):

        trial_member_cog = interaction.client.get_cog("TrialMember")

        if trial_member_cog is None:
            await interaction.response.send_message("管理機能を取得失敗。",ephemeral=True)
            return

        if not trial_member_cog.is_evaluator(interaction.user):
            await interaction.response.send_message("評価権限がありません。",ephemeral=True)
            return

        guild = interaction.guild

        if guild is None:
            await interaction.response.send_message("サーバー情報を取得失敗。",ephemeral=True)
            return

        trial_member_role = guild.get_role(config.ROLE_TRIAL_MEMBER)

        if trial_member_role is None:
            await interaction.response.send_message("精霊ロールが見つかりません。",ephemeral=True)
            return

        evaluated_trial_member_ids = get_evaluated_trial_member_ids(interaction.user.id)

        targets = []

        for trial_member in trial_member_role.members:

            if trial_member.bot:
                continue

            trial_member_data = get_trial_member(trial_member.id)

            if trial_member_data is None:
                continue

            trial_member_class = trial_member_data[1]

            if not trial_member_cog.can_evaluate_trial_member(interaction.user,trial_member_class):
                continue

            vc_data = get_vc_time(trial_member.id)
            total_xp = vc_data[2]
            evaluated = (trial_member.id in evaluated_trial_member_ids)

            targets.append((trial_member,trial_member_class,total_xp,evaluated))

        # XPが多い順
        targets.sort(key=lambda x: (-x[2],x[0].id))

        if not targets:

            embed = discord.Embed(
                title="評価チェック",
                description="現在評価できる精霊はいません。",
                color=config.COLOR_WHITE
            )

            await interaction.response.send_message(embed=embed,ephemeral=True)
            return

        view = EvaluationCheckView(targets)

        await interaction.response.send_message(
            embed=view.create_embed(),
            view=view,
            ephemeral=True
        )


# ==========================================
# 評価チェックページ
# ==========================================
class EvaluationCheckView(discord.ui.View):
    PER_PAGE = 25

    def __init__(self,targets):
        super().__init__(timeout=300)

        self.targets = targets
        self.page = 0

        self.update_buttons()

    def create_embed(self):

        start = self.page * self.PER_PAGE
        end = start + self.PER_PAGE

        page_targets = self.targets[start:end]

        lines = []

        for trial_member, trial_member_class, total_xp, evaluated in page_targets:

            status = ("評価済 ✅" if evaluated else "未評価")

            lines.append(
                f"{trial_member_class} {trial_member.mention} {total_xp:,}XP {status}"
            )

        total_pages = (len(self.targets) + self.PER_PAGE - 1) // self.PER_PAGE

        embed = discord.Embed(
            title="評価チェック",
            description="\n".join(lines),
            color=config.COLOR_WHITE
        )
        embed.set_footer(text=f"{self.page + 1} / {total_pages}ページ")

        return embed

    def update_buttons(self):

        self.clear_items()

        if self.page > 0:
            self.add_item(EvaluationCheckPrevButton())

        if ((self.page + 1) * self.PER_PAGE < len(self.targets)):
            self.add_item(EvaluationCheckNextButton())


class EvaluationCheckPrevButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="◀ 前",style=discord.ButtonStyle.secondary)

    async def callback(self,interaction: discord.Interaction):

        self.view.page -= 1
        self.view.update_buttons()

        await interaction.response.edit_message(
            embed=self.view.create_embed(),
            view=self.view
        )


class EvaluationCheckNextButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="次 ▶",style=discord.ButtonStyle.secondary)

    async def callback(self,interaction: discord.Interaction):

        self.view.page += 1
        self.view.update_buttons()

        await interaction.response.edit_message(
            embed=self.view.create_embed(),
            view=self.view
        )


# ==========================================
# 評価対象選択
# ==========================================
class EvaluationUserSelect(discord.ui.UserSelect):
    def __init__(self):
        super().__init__(
            placeholder="評価する精霊を選択",
            min_values=1,
            max_values=1,
            custom_id="evaluation_user_select"
        )

    async def callback(self,interaction: discord.Interaction):

        member = self.values[0]
        trial_member_role = interaction.guild.get_role(config.ROLE_TRIAL_MEMBER)

        if (trial_member_role is None or trial_member_role not in member.roles):
            await interaction.response.send_message("精霊を選択してください。",ephemeral=True)
            return

        await interaction.response.send_modal(EvaluationModal(member))


class EvaluationUserSelectView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)

        self.add_item(EvaluationUserSelect())


# ==========================================
# 評価入力
# ==========================================
class EvaluationModal(discord.ui.Modal):
    def __init__(self,member: discord.Member):
        super().__init__(title=f"{member.display_name} の評価")

        self.member = member

        self.voice_score = discord.ui.TextInput(
            label="声/音質",
            placeholder="1～10",
            required=True,
            max_length=2
        )

        self.conversation_score = discord.ui.TextInput(
            label="トーク力",
            placeholder="1～10",
            required=True,
            max_length=2
        )

        self.charm_score = discord.ui.TextInput(
            label="個性/魅力",
            placeholder="1～10",
            required=True,
            max_length=2
        )

        self.overall_score = discord.ui.TextInput(
            label="総合評価",
            placeholder="1～5",
            required=True,
            max_length=1
        )

        self.note = discord.ui.TextInput(
            label="コメント",
            placeholder="任意",
            required=False,
            style=discord.TextStyle.paragraph,
            max_length=500
        )

        self.add_item(self.voice_score)
        self.add_item(self.conversation_score)
        self.add_item(self.charm_score)
        self.add_item(self.overall_score)
        self.add_item(self.note)

    async def on_submit(self,interaction: discord.Interaction):

        try:
            voice_score = int(self.voice_score.value)
            conversation_score = int(self.conversation_score.value)
            charm_score = int(self.charm_score.value)
            overall_score = int(self.overall_score.value)

        except ValueError:
            await interaction.response.send_message("数字で入力してください。",ephemeral=True)
            return

        trial_member_cog = interaction.client.get_cog("TrialMember")

        if trial_member_cog is None:
            await interaction.response.send_message("管理機能を取得失敗。",ephemeral=True)
            return

        note = self.note.value.strip() or None

        await trial_member_cog.execute_evaluation(
            interaction=interaction,
            member=self.member,
            voice_score=voice_score,
            conversation_score=conversation_score,
            charm_score=charm_score,
            overall_score=overall_score,
            note=note
        )


# ==========================================
# 追記対象選択
# ==========================================
class CommentUserSelect(discord.ui.UserSelect):
    def __init__(self):
        super().__init__(
            placeholder="追記する精霊を選択",
            min_values=1,
            max_values=1,
            custom_id="comment_user_select"
        )

    async def callback(self,interaction: discord.Interaction):

        member = self.values[0]
        trial_member_role = interaction.guild.get_role(config.ROLE_TRIAL_MEMBER)

        if (trial_member_role is None or trial_member_role not in member.roles):
            await interaction.response.send_message("精霊を選択してください。",ephemeral=True)
            return

        await interaction.response.send_modal(CommentModal(member))


class CommentUserSelectView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)

        self.add_item(CommentUserSelect())


# ==========================================
# 追記入力
# ==========================================
class CommentModal(discord.ui.Modal):
    def __init__(self,member: discord.Member):
        super().__init__(title=f"{member.display_name} への追記")

        self.member = member

        self.voice_score = discord.ui.TextInput(
            label="声/音質",
            placeholder="変更する場合のみ1～10で入力",
            required=False,
            max_length=2
        )

        self.conversation_score = discord.ui.TextInput(
            label="トーク力",
            placeholder="変更する場合のみ1～10で入力",
            required=False,
            max_length=2
        )

        self.charm_score = discord.ui.TextInput(
            label="個性/魅力",
            placeholder="変更する場合のみ1～10で入力",
            required=False,
            max_length=2
        )

        self.overall_score = discord.ui.TextInput(
            label="総合評価",
            placeholder="変更する場合のみ1～5で入力",
            required=False,
            max_length=1
        )

        self.note = discord.ui.TextInput(
            label="コメント",
            placeholder="任意",
            required=False,
            style=discord.TextStyle.paragraph,
            max_length=500
        )

        self.add_item(self.voice_score)
        self.add_item(self.conversation_score)
        self.add_item(self.charm_score)
        self.add_item(self.overall_score)
        self.add_item(self.note)

    async def on_submit(self,interaction: discord.Interaction):

        voice_score = None
        conversation_score = None
        charm_score = None
        overall_score = None

        try:
            if self.voice_score.value.strip():
                voice_score = int(self.voice_score.value.strip())

            if self.conversation_score.value.strip():
                conversation_score = int(self.conversation_score.value.strip())

            if self.charm_score.value.strip():
                charm_score = int(self.charm_score.value.strip())

            if self.overall_score.value.strip():
                overall_score = int(self.overall_score.value.strip())

        except ValueError:
            await interaction.response.send_message("数字で入力してください。",ephemeral=True)
            return

        note = self.note.value.strip() or None

        trial_member_cog = interaction.client.get_cog("TrialMember")

        if trial_member_cog is None:
            await interaction.response.send_message("管理機能を取得失敗。",ephemeral=True)
            return

        await trial_member_cog.execute_comment(
            interaction=interaction,
            member=self.member,
            voice_score=voice_score,
            conversation_score=conversation_score,
            charm_score=charm_score,
            overall_score=overall_score,
            note=note
        )


# ==========================================
# アンケートDM削除
# ==========================================
async def remove_trial_member_end_survey(interaction: discord.Interaction):

    trial_member_end_survey = get_trial_member_end_survey(interaction.user.id)

    if trial_member_end_survey is None:
        return

    channel_id = trial_member_end_survey[0]
    message_id = trial_member_end_survey[1]

    try:
        channel = interaction.client.get_channel(channel_id)

        if channel is None:
            channel = await interaction.client.fetch_channel(channel_id)

        message = await channel.fetch_message(message_id)
        await message.delete()

    except discord.NotFound:
        pass

    except discord.HTTPException as e:
        logger.warning(f"アンケートDM削除失敗：{interaction.user.id} / {e}")

    finally:
        delete_trial_member_end_survey(interaction.user.id)


# ==========================================
# 転生アンケート
# ==========================================
class TrialMemberEndSurveyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="👍 一番印象の良い天使",
        style=discord.ButtonStyle.primary,
        custom_id="trial_member_end_survey:good_evaluator"
    )
    async def good_evaluator(self,interaction: discord.Interaction,button: discord.ui.Button):

        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild

        if guild is None:
            guild = interaction.client.get_guild(config.GUILD_ID)

        if guild is None:
            await interaction.followup.send("サーバー情報を取得できませんでした。",ephemeral=True)
            return

        evaluator_role = guild.get_role(config.ROLE_EVALUATOR)

        if evaluator_role is None:
            await interaction.followup.send("天使ロールが見つかりません。",ephemeral=True)
            return

        evaluators = [
            member
            for member in evaluator_role.members
            if not member.bot
        ]

        if not evaluators:
            await interaction.followup.send("現在天使が登録されていません。",ephemeral=True)
            return

        await interaction.followup.send(
            "一番印象の良い天使を選択してください。",
            view=EvaluatorSelectView(evaluators),
            ephemeral=True
        )

    @discord.ui.button(
        label="⏭ スキップ",
        style=discord.ButtonStyle.gray,
        custom_id="trial_member_end_survey:skip"
    )
    async def skip(self,interaction: discord.Interaction,button: discord.ui.Button):

        trial_member_end_survey = get_trial_member_end_survey(interaction.user.id)

        if trial_member_end_survey is None:
            await interaction.response.send_message("このアンケートは終了しています。",ephemeral=True)
            return

        await interaction.response.send_message("アンケートをスキップしました。",ephemeral=True)

        await remove_trial_member_end_survey(interaction)


# ==========================================
# 評価員選択
# ==========================================
class EvaluatorSelect(discord.ui.Select):
    def __init__(self,evaluators):

        options = [
            discord.SelectOption(
                label=member.display_name,
                value=str(member.id)
            )
            for member in evaluators
        ]

        super().__init__(
            placeholder="天使を選択",
            options=options,
            min_values=1,
            max_values=1
        )

    async def callback(self,interaction: discord.Interaction):

        trial_member_end_survey = get_trial_member_end_survey(interaction.user.id)

        if trial_member_end_survey is None:
            await interaction.response.send_message("このアンケートは終了しています。",ephemeral=True)
            return

        evaluator_id = int(self.values[0])

        await interaction.response.send_modal(EvaluatorReviewModal(evaluator_id))


class EvaluatorSelectView(discord.ui.View):
    PER_PAGE = 25

    def __init__(self,evaluators):
        super().__init__(timeout=300)

        self.evaluators = evaluators
        self.page = 0

        self.update_select()

    def update_select(self):

        self.clear_items()

        start = self.page * self.PER_PAGE
        end = start + self.PER_PAGE

        self.add_item(EvaluatorSelect(self.evaluators[start:end]))

        if self.page > 0:
            self.add_item(EvaluatorPrevButton())

        if end < len(self.evaluators):
            self.add_item(EvaluatorNextButton())


# ==========================================
# 評価員選択ページ切り替え
# ==========================================
class EvaluatorPrevButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="◀ 前",style=discord.ButtonStyle.secondary)

    async def callback(self,interaction: discord.Interaction):

        self.view.page -= 1
        self.view.update_select()

        await interaction.response.edit_message(view=self.view)


class EvaluatorNextButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="次 ▶",style=discord.ButtonStyle.secondary)

    async def callback(self,interaction: discord.Interaction):

        self.view.page += 1
        self.view.update_select()

        await interaction.response.edit_message(view=self.view)


# ==========================================
# 天使アンケート
# ==========================================
class EvaluatorReviewModal(discord.ui.Modal,title="天使アンケート"):
    def __init__(self,evaluator_id: int):
        super().__init__()

        self.evaluator_id = evaluator_id

    comment = discord.ui.TextInput(
        label="理由（任意）",
        required=False,
        style=discord.TextStyle.paragraph,
        max_length=500
    )

    async def on_submit(self,interaction: discord.Interaction):

        trial_member_end_survey = get_trial_member_end_survey(interaction.user.id)

        if trial_member_end_survey is None:
            await interaction.response.send_message("このアンケートは終了しています。",ephemeral=True)
            return

        guild = interaction.client.get_guild(config.GUILD_ID)

        comment = self.comment.value.strip() or None

        add_evaluator_review(
            ended_trial_member_id=interaction.user.id,
            evaluator_id=self.evaluator_id,
            comment=comment
        )

        if guild:

            evaluator = guild.get_member(self.evaluator_id)
            channel = guild.get_channel(config.CHANNEL_SURVEY_LOG)

            if channel:

                evaluator_text = (
                    evaluator.mention
                    if evaluator
                    else f"ID: {self.evaluator_id}"
                )

                comment_text = comment or "なし"

                embed = discord.Embed(
                    title="アンケート回答",
                    description=(
                        f"回答者：{interaction.user.mention}\n"
                        f"対象：{evaluator_text}\n"
                        f"理由：{comment_text}"
                    ),
                    color=config.COLOR_GOLD
                )

                embed.set_thumbnail(url=interaction.user.display_avatar.url)

                await channel.send(embed=embed)

        await interaction.response.send_message("回答ありがとうございました。",ephemeral=True)

        await remove_trial_member_end_survey(interaction)
