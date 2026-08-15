"""招待ポイントパネルのボタンと入力画面をまとめたDiscord UI層。"""

import logging
import discord
import config

from database.coin import add_balance
from database.member import (
    get_invite_points,
    add_invite_points,
    spend_invite_points,
    get_hotel_free_rate,
    add_hotel_free_rate,
    has_start_ticket,
    set_start_ticket,
)
from database.trial_member import extend_trial_member_end_date


logger = logging.getLogger(__name__)

async def send_invite_point_use_log(
    interaction: discord.Interaction,
    benefit_name: str,
    use_points: int,
    remaining_points: int,
    result_text: str
):

    channel = interaction.guild.get_channel(config.CHANNEL_INVITE_POINT_USE_LOG)

    if channel is None:
        return

    embed = discord.Embed(
        title="招待ポイント使用",
        description=(
            f"対象者：{interaction.user.mention}\n"
            f"特典：**{benefit_name}**\n"
            f"使用：**{use_points} pt**\n"
            f"残り：**{remaining_points} pt**\n"
            f"結果：{result_text}"
        ),
        color=config.COLOR_GREEN
    )
    embed.set_thumbnail(url=interaction.user.display_avatar.url)

    try:
        await channel.send(embed=embed)

    except discord.HTTPException as e:
        logger.warning(f"招待ポイント使用ログ送信失敗：{interaction.user.id} / {e}")


class InvitePointView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    # ==================================================
    # 招待ポイント確認
    # ==================================================
    @discord.ui.button(label="pt確認",style=discord.ButtonStyle.primary,custom_id="invite:check")
    async def check(self,interaction: discord.Interaction,button: discord.ui.Button):

        invite_points = get_invite_points(interaction.user.id)
        embed = discord.Embed(
            title="招待ポイント確認",
            description=(
                f"あなたは **{invite_points:,} pt** です。"
            ),
            color=config.COLOR_GREEN
        )
        embed.set_thumbnail(url=interaction.user.display_avatar.url)

        await interaction.response.send_message(embed=embed,ephemeral=True)

    # ==================================================
    # 招待ポイント使用
    # ==================================================
    @discord.ui.button(label="pt使用",style=discord.ButtonStyle.success,custom_id="invite:use")
    async def use(self,interaction: discord.Interaction,button: discord.ui.Button):

        invite_points = get_invite_points(interaction.user.id)
        if invite_points <= 0:
            await interaction.response.send_message("使用できる招待ポイントがありません。",ephemeral=True)
            return

        demoted_role = interaction.guild.get_role(config.ROLE_DEMOTED)
        trial_member_role = interaction.guild.get_role(config.ROLE_TRIAL_MEMBER)
        has_member_rank = any(role.id in config.ROLE_GROUP_MEMBER for role in interaction.user.roles)
        associate_member_role = interaction.guild.get_role(config.ROLE_ASSOCIATE_MEMBER)
        benefit_type = None

        # を最優先
        if (demoted_role  is not None and demoted_role  in interaction.user.roles):
            benefit_type = "demoted"

        # 七聖・騎士
        elif has_member_rank:
            benefit_type = "member"

        elif (trial_member_role is not None and trial_member_role in interaction.user.roles):
            benefit_type = "trial_member"

        elif (associate_member_role is not None and associate_member_role in interaction.user.roles):
            benefit_type = "associate_member"

        if benefit_type is None:
            await interaction.response.send_message("現在のロールでは招待ポイントを使用できません。",ephemeral=True)
            return

        await interaction.response.send_message(
            (f"現在の招待ポイント：**{invite_points:,} pt**"),
            view=InviteBenefitView(benefit_type),
            ephemeral=True
        )

# ==================================================
# 招待ポイント特典選択View
# ==================================================
class InviteBenefitView(discord.ui.View):
    def __init__(self,benefit_type: str):
        super().__init__(timeout=180)

        self.add_item(InviteBenefitSelect(benefit_type))

# ==================================================
# 招待ポイント特典選択
# ==================================================
class InviteBenefitSelect(discord.ui.Select):
    def __init__(self,benefit_type: str):

        self.benefit_type = benefit_type
        options = []

        # 精霊
        if benefit_type == "trial_member":
            options = [
                discord.SelectOption(
                    label="精霊期間を延長",
                    description=(f"1ptにつき精霊期間を{config.INVITE_TRIAL_MEMBER_EXTENSION_DAYS}日延長"),
                    value="trial_member_extension",
                ),
                discord.SelectOption(
                    label="coinへ交換",
                    description=(f"1ptにつき{config.INVITE_COIN_REWARD:,} coin"),
                    value="trial_member_coin",
                )
            ]

        # 七聖・騎士
        elif benefit_type == "member":
            options = [
                discord.SelectOption(
                    label="宿屋の無料確率を上げる",
                    description=(f"1ptにつき無料確率を{config.INVITE_MEMBER_FREE_RATE}% 増加"),
                    value="member_hotel_rate",
                ),
                discord.SelectOption(
                    label="coinへ交換",
                    description=(f"1ptにつき{config.INVITE_COIN_REWARD:,} coin"),
                    value="member_coin",
                )
            ]

        # 小人
        elif benefit_type == "associate_member":
            options = [
                discord.SelectOption(
                    label="精霊チケット",
                    description=(f"{config.INVITE_TICKET_COST}ptで確認待ちロールを取得"),
                    value="associate_member_ticket",
                ),
                discord.SelectOption(
                    label="coinへ交換",
                    description=(f"1ptにつき{config.INVITE_COIN_REWARD:,} coin"),
                    value="associate_member_coin",
                )
            ]

        # 魔物
        elif benefit_type == "demoted":
            options = [
                discord.SelectOption(
                    label="coinへ交換",
                    description=(f"1ptにつき{config.INVITE_DEMOTED_COIN_REWARD:,} coin"),
                    value="demoted_coin",
                )
            ]

        super().__init__(placeholder="交換する特典を選択してください",min_values=1,max_values=1,options=options)

    async def callback(self,interaction: discord.Interaction):
        selected_benefit = self.values[0]

        # 精霊チケットは固定5pt
        if selected_benefit == "associate_member_ticket":

            associate_member_role = interaction.guild.get_role(config.ROLE_ASSOCIATE_MEMBER)
            enrollment_waiting_role = interaction.guild.get_role(config.ROLE_ENROLLMENT_WAITING)
            # 小人ロール再確認
            if (
                associate_member_role is None
                or associate_member_role not in interaction.user.roles
            ):
                await interaction.response.send_message("小人のみ精霊チケットを交換できます。",ephemeral=True)
                return

            if enrollment_waiting_role is None:
                await interaction.response.send_message("確認待ちロールが見つかりません。",ephemeral=True)
                return

            # すでにチケットを交換済み
            if (
                has_start_ticket(interaction.user.id)
                or enrollment_waiting_role in interaction.user.roles
            ):
                await interaction.response.send_message("すでに精霊チケットを所持しています。",ephemeral=True)
                return

            # ポイント減算
            success = spend_invite_points(interaction.user.id,config.INVITE_TICKET_COST)
            if not success:
                await interaction.response.send_message(
                    (f"精霊チケットの交換には **{config.INVITE_TICKET_COST}pt** 必要です。"),
                    ephemeral=True
                )
                return

            # 確認待ちロール付与
            try:
                await interaction.user.add_roles(enrollment_waiting_role,reason="精霊チケット交換")

            except discord.HTTPException as e:
                # ロール付与に失敗した場合はポイントを返却
                add_invite_points(interaction.user.id,config.INVITE_TICKET_COST)

                logger.warning(f"確認待ちロール付与失敗：{interaction.user.id} / {e}")

                await interaction.response.send_message("確認待ちロールの付与に失敗しました。",ephemeral=True)
                return

            set_start_ticket(interaction.user.id)

            remaining_points = get_invite_points(interaction.user.id)

            await send_invite_point_use_log(
                interaction=interaction,
                benefit_name="精霊チケット",
                use_points=config.INVITE_TICKET_COST,
                remaining_points=remaining_points,
                result_text="確認待ちロールを付与"
            )

            embed = discord.Embed(
                title="精霊チケット交換",
                description=(
                    "精霊チケットを交換しました。\n"
                    f"**{config.INVITE_TICKET_COST}pt** 使用\n"
                ),
                color=config.COLOR_GREEN
            )
            embed.set_thumbnail(url=interaction.user.display_avatar.url)

            await interaction.response.send_message(embed=embed,ephemeral=True)
            return

        # 宿屋無料確率が上限の場合
        if selected_benefit == "member_hotel_rate":

            current_rate = get_hotel_free_rate(interaction.user.id)
            if current_rate >= config.MAX_HOTEL_FREE_RATE:
                await interaction.response.send_message(
                    f"宿屋無料確率はすでに **{config.MAX_HOTEL_FREE_RATE}%** です。",ephemeral=True)
                return

        await interaction.response.send_message("使用するポイント数を選択してください。",
            view=InvitePointAmountView(benefit=selected_benefit,user_id=interaction.user.id),
            ephemeral=True
        )

# ==================================================
# 使用ポイント数選択View
# ==================================================
class InvitePointAmountView(discord.ui.View):
    def __init__(self,benefit: str,user_id: int):

        super().__init__(timeout=180)

        self.add_item(InvitePointAmountSelect(benefit=benefit,user_id=user_id))

# ==================================================
# 使用ポイント数選択
# ==================================================
class InvitePointAmountSelect(discord.ui.Select):
    def __init__(self,benefit: str,user_id: int):

        self.benefit = benefit
        self.user_id = user_id
        current_points = get_invite_points(user_id)

        # 宿屋無料確率は99％まで
        if benefit == "member_hotel_rate":
            current_rate = get_hotel_free_rate(user_id)
            remaining_rate = (config.MAX_HOTEL_FREE_RATE - current_rate)
            usable_points = (remaining_rate // config.INVITE_MEMBER_FREE_RATE)
            current_points = min(current_points,usable_points)

        options = []
        # 25pt以下なら、所持ポイント分だけ表示
        if current_points <= 25:
            options = [
                discord.SelectOption(label=f"{point}pt使用",value=str(point))
                for point in range(1,current_points + 1)
            ]

        # 26pt以上なら、1～24ptと入力選択肢を表示
        else:
            options = [discord.SelectOption(label=f"{point}pt使用",value=str(point)) for point in range(1, 25)]

            options.append(
                discord.SelectOption(
                    label="25pt以上を指定",
                    description=(f"25～{current_points}ptの範囲で入力"),
                    value="custom"
                )
            )

        super().__init__(
            placeholder="使用するポイント数を選択してください",
            min_values=1,
            max_values=1,
            options=options
        )

    async def callback(self,interaction: discord.Interaction):

        # メニューを開いた本人以外は操作不可
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("このメニューは操作できません。",ephemeral=True)
            return

        selected_value = self.values[0]
        # 25pt以上を入力
        if selected_value == "custom":
            await interaction.response.send_modal(
                InvitePointAmountModal(benefit=self.benefit,user_id=self.user_id)
            )
            return

        use_points = int(selected_value)

        await execute_invite_benefit(interaction=interaction,benefit=self.benefit,use_points=use_points)

# ==================================================
# 使用ポイント数入力Modal
# ==================================================
class InvitePointAmountModal(discord.ui.Modal,title="使用ポイント数"):

    amount = discord.ui.TextInput(
        label="使用するポイント数",
        placeholder="25以上の数字を入力してください",
        required=True,
        max_length=10
    )

    def __init__(self,benefit: str,user_id: int):
        super().__init__()

        self.benefit = benefit
        self.user_id = user_id

    async def on_submit(self,interaction: discord.Interaction):

        if interaction.user.id != self.user_id:
            await interaction.response.send_message("この入力画面は操作できません。",ephemeral=True)
            return

        amount_text = self.amount.value.strip()
        if not amount_text.isdigit():
            await interaction.response.send_message("ポイント数は数字で入力してください。",ephemeral=True)
            return

        use_points = int(amount_text)
        current_points = get_invite_points(interaction.user.id)

        if use_points < 25:
            await interaction.response.send_message("25pt以上を入力してください。",ephemeral=True)
            return

        if use_points > current_points:
            await interaction.response.send_message(
                (
                    "所持ポイントを超えています。\n"
                    f"現在：**{current_points}pt**"
                ),
                ephemeral=True
            )
            return

        await execute_invite_benefit(interaction=interaction,benefit=self.benefit,use_points=use_points)

# ==================================================
# 招待ポイント特典適用共通処理
# ==================================================
async def execute_invite_benefit(interaction: discord.Interaction,benefit: str,use_points: int):

    await interaction.response.defer(ephemeral=True)

    guild = interaction.guild
    member = interaction.user

    if guild is None:
        await interaction.followup.send("サーバー情報を取得できませんでした。",ephemeral=True)
        return

    has_member_rank = any(role.id in config.ROLE_GROUP_MEMBER for role in member.roles)
    current_points = get_invite_points(member.id)

    if use_points <= 0:
        await interaction.followup.send("使用ポイントは1以上で指定してください。",ephemeral=True)
        return

    if use_points > current_points:
        await interaction.followup.send(
            (
                "招待ポイントが不足しています。\n"
                f"現在：**{current_points}pt**"
            ),
            ephemeral=True
        )
        return

    trial_member_role = guild.get_role(config.ROLE_TRIAL_MEMBER)
    associate_member_role = guild.get_role(config.ROLE_ASSOCIATE_MEMBER)
    demoted_role = guild.get_role(config.ROLE_DEMOTED)

    benefit_name = ""
    result_text = ""

    # ==================================================
    # 精霊期間延長
    # ==================================================
    if benefit == "trial_member_extension":

        if (trial_member_role is None or trial_member_role not in member.roles):
            await interaction.followup.send("現在、精霊ではありません。",ephemeral=True)
            return

        extension_days = (use_points * config.INVITE_TRIAL_MEMBER_EXTENSION_DAYS)
        success = spend_invite_points(member.id,use_points)

        if not success:
            await interaction.followup.send("招待ポイントが不足しています。",ephemeral=True)
            return

        extended = extend_trial_member_end_date(member.id,extension_days)

        if not extended:
            add_invite_points(member.id,use_points)

            await interaction.followup.send("精霊情報が見つからないため延長できませんでした。",ephemeral=True)
            return

        trial_member_cog = interaction.client.get_cog("TrialMember")

        if trial_member_cog is not None:
            await trial_member_cog.update_trial_member_end_embed(member.id)

        benefit_name = "精霊期間延長"
        result_text = f"精霊期間を{extension_days}日延長"

    # ==================================================
    # 七聖・騎士の宿屋無料確率
    # ==================================================
    elif benefit == "member_hotel_rate":

        if not has_member_rank:
            await interaction.followup.send("現在、騎士または七聖ではありません。",ephemeral=True)
            return

        current_rate = get_hotel_free_rate(member.id)
        if current_rate >= config.MAX_HOTEL_FREE_RATE:
            await interaction.followup.send(
                (f"宿屋無料確率はすでに **{config.MAX_HOTEL_FREE_RATE}%** です。"),
                ephemeral=True
            )
            return

        increase_rate = (use_points * config.INVITE_MEMBER_FREE_RATE)

        if (current_rate + increase_rate > config.MAX_HOTEL_FREE_RATE):
            remaining_rate = (config.MAX_HOTEL_FREE_RATE - current_rate)

            max_points = (remaining_rate // config.INVITE_MEMBER_FREE_RATE)

            await interaction.followup.send(
                (
                    "宿の無料確率が上限を超えます。\n"
                    f"現在：**{current_rate}%**\n"
                    f"上限：**{config.MAX_HOTEL_FREE_RATE}%**\n"
                    f"使用可能：最大 **{max_points}pt**"
                ),
                ephemeral=True
            )
            return

        success = spend_invite_points(member.id,use_points)

        if not success:
            await interaction.followup.send("招待ポイントが不足しています。",ephemeral=True)
            return

        old_rate = current_rate
        add_hotel_free_rate(member.id,increase_rate)

        new_rate = get_hotel_free_rate(member.id)
        benefit_name = "宿屋無料確率"
        result_text = f"{old_rate}% → {new_rate}%"

    # ==================================================
    # Coin交換
    # ==================================================
    elif benefit in ("trial_member_coin","member_coin","associate_member_coin","demoted_coin"):

        # 精霊
        if benefit == "trial_member_coin":

            if (trial_member_role is None or trial_member_role not in member.roles):
                await interaction.followup.send("現在、精霊ではありません。",ephemeral=True)
                return

        # 七聖・騎士
        elif benefit == "member_coin":

            if not has_member_rank:
                await interaction.followup.send("現在、騎士または七聖ではありません。",ephemeral=True)
                return

        # 小人
        elif benefit == "associate_member_coin":

            if (associate_member_role is None or associate_member_role not in member.roles):
                await interaction.followup.send("現在、小人ではありません。",ephemeral=True)
                return

        # 魔物
        elif benefit == "demoted_coin":

            if (demoted_role is None or demoted_role not in member.roles):
                await interaction.followup.send("現在、魔物ではありません。",ephemeral=True)
                return

        coin_per_point = (
            config.INVITE_DEMOTED_COIN_REWARD
            if benefit == "demoted_coin"
            else config.INVITE_COIN_REWARD
        )

        coin_amount = (use_points * coin_per_point)
        success = spend_invite_points(member.id,use_points)
        if not success:
            await interaction.followup.send("招待ポイントが不足しています。",ephemeral=True)
            return

        add_balance(member.id,coin_amount)

        coin_cog = interaction.client.get_cog("Coin")
        if coin_cog is not None:
            await coin_cog.update_debt_status(guild,member)

        benefit_name = "coin交換"
        result_text = f"{coin_amount:,} coin付与"

    else:
        await interaction.followup.send("選択した特典を確認できませんでした。",ephemeral=True)
        return

    remaining_points = get_invite_points(member.id)
    await send_invite_point_use_log(
        interaction=interaction,
        benefit_name=benefit_name,
        use_points=use_points,
        remaining_points=remaining_points,
        result_text=result_text
    )

    embed = discord.Embed(
        title="招待ポイント使用",
        description=(
            f"特典：**{benefit_name}**\n"
            f"使用：**{use_points}pt**\n"
            f"結果：{result_text}\n"
            f"残り：**{remaining_points}pt**"
        ),
        color=config.COLOR_WHITE
    )
    embed.set_thumbnail(url=member.display_avatar.url)

    await interaction.followup.send(embed=embed,ephemeral=True)
