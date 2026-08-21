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
from texts import common as common_texts
from texts import member as member_texts


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
        title=member_texts.INVITE_USE_TITLE,
        description=member_texts.INVITE_USE_LOG_BODY.format(
            user=interaction.user.mention,
            benefit=benefit_name,
            points=use_points,
            remaining=remaining_points,
            result=result_text
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
    @discord.ui.button(label=member_texts.PANEL_BUTTON_POINT_CHECK,style=discord.ButtonStyle.primary,custom_id="invite:check")
    async def check(self,interaction: discord.Interaction,button: discord.ui.Button):

        invite_points = get_invite_points(interaction.user.id)
        embed = discord.Embed(
            title=member_texts.INVITE_CHECK_TITLE,
            description=member_texts.INVITE_CHECK_BODY.format(
                points=f"{invite_points:,}"
            ),
            color=config.COLOR_GREEN
        )
        embed.set_thumbnail(url=interaction.user.display_avatar.url)

        await interaction.response.send_message(embed=embed,ephemeral=True)

    # ==================================================
    # 招待ポイント使用
    # ==================================================
    @discord.ui.button(label=member_texts.PANEL_BUTTON_POINT_USE,style=discord.ButtonStyle.success,custom_id="invite:use")
    async def use(self,interaction: discord.Interaction,button: discord.ui.Button):

        invite_points = get_invite_points(interaction.user.id)
        if invite_points <= 0:
            await interaction.response.send_message(member_texts.INVITE_NO_POINTS,ephemeral=True)
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
            await interaction.response.send_message(member_texts.INVITE_ROLE_NOT_ELIGIBLE,ephemeral=True)
            return

        await interaction.response.send_message(
            member_texts.INVITE_USE_PROMPT.format(points=f"{invite_points:,}"),
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
                    label=member_texts.BENEFIT_TRIAL_EXTENSION,
                    description=member_texts.BENEFIT_TRIAL_EXTENSION_DESCRIPTION.format(
                        days=config.INVITE_TRIAL_MEMBER_EXTENSION_DAYS
                    ),
                    value="trial_member_extension",
                ),
                discord.SelectOption(
                    label=member_texts.BENEFIT_COIN,
                    description=member_texts.BENEFIT_COIN_DESCRIPTION.format(
                        coin=f"{config.INVITE_COIN_REWARD:,}"
                    ),
                    value="trial_member_coin",
                )
            ]

        # 七聖・騎士
        elif benefit_type == "member":
            options = [
                discord.SelectOption(
                    label=member_texts.BENEFIT_HOTEL_RATE,
                    description=member_texts.BENEFIT_HOTEL_RATE_DESCRIPTION.format(
                        rate=config.INVITE_MEMBER_FREE_RATE
                    ),
                    value="member_hotel_rate",
                ),
                discord.SelectOption(
                    label=member_texts.BENEFIT_COIN,
                    description=member_texts.BENEFIT_COIN_DESCRIPTION.format(
                        coin=f"{config.INVITE_COIN_REWARD:,}"
                    ),
                    value="member_coin",
                )
            ]

        # 小人
        elif benefit_type == "associate_member":
            options = [
                discord.SelectOption(
                    label=member_texts.BENEFIT_TICKET,
                    description=member_texts.BENEFIT_TICKET_DESCRIPTION.format(
                        cost=config.INVITE_TICKET_COST
                    ),
                    value="associate_member_ticket",
                ),
                discord.SelectOption(
                    label=member_texts.BENEFIT_COIN,
                    description=member_texts.BENEFIT_COIN_DESCRIPTION.format(
                        coin=f"{config.INVITE_COIN_REWARD:,}"
                    ),
                    value="associate_member_coin",
                )
            ]

        # 魔物
        elif benefit_type == "demoted":
            options = [
                discord.SelectOption(
                    label=member_texts.BENEFIT_COIN,
                    description=member_texts.BENEFIT_COIN_DESCRIPTION.format(
                        coin=f"{config.INVITE_DEMOTED_COIN_REWARD:,}"
                    ),
                    value="demoted_coin",
                )
            ]

        super().__init__(
            placeholder=member_texts.BENEFIT_SELECT_PLACEHOLDER,
            min_values=1,
            max_values=1,
            options=options
        )

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
                await interaction.response.send_message(member_texts.TICKET_ROLE_REQUIRED,ephemeral=True)
                return

            if enrollment_waiting_role is None:
                await interaction.response.send_message(member_texts.TICKET_WAITING_ROLE_NOT_FOUND,ephemeral=True)
                return

            # すでにチケットを交換済み
            if (
                has_start_ticket(interaction.user.id)
                or enrollment_waiting_role in interaction.user.roles
            ):
                await interaction.response.send_message(member_texts.TICKET_ALREADY_OWNED,ephemeral=True)
                return

            # ポイント減算
            success = spend_invite_points(interaction.user.id,config.INVITE_TICKET_COST)
            if not success:
                await interaction.response.send_message(
                    member_texts.TICKET_NOT_ENOUGH_POINTS.format(
                        cost=config.INVITE_TICKET_COST
                    ),
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

                await interaction.response.send_message(member_texts.TICKET_ROLE_FAILED,ephemeral=True)
                return

            set_start_ticket(interaction.user.id)

            remaining_points = get_invite_points(interaction.user.id)

            await send_invite_point_use_log(
                interaction=interaction,
                benefit_name=member_texts.BENEFIT_NAME_TICKET,
                use_points=config.INVITE_TICKET_COST,
                remaining_points=remaining_points,
                result_text=member_texts.RESULT_TICKET
            )

            embed = discord.Embed(
                title=member_texts.TICKET_DONE_TITLE,
                description=member_texts.TICKET_DONE_BODY.format(
                    cost=config.INVITE_TICKET_COST
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
                    member_texts.HOTEL_RATE_MAX.format(
                        rate=config.MAX_HOTEL_FREE_RATE
                    ),
                    ephemeral=True
                )
                return

        await interaction.response.send_message(member_texts.AMOUNT_SELECT_PROMPT,
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
                discord.SelectOption(
                    label=member_texts.AMOUNT_OPTION.format(points=point),
                    value=str(point)
                )
                for point in range(1,current_points + 1)
            ]

        # 26pt以上なら、1～24ptと入力選択肢を表示
        else:
            options = [
                discord.SelectOption(
                    label=member_texts.AMOUNT_OPTION.format(points=point),
                    value=str(point)
                )
                for point in range(1, 25)
            ]

            options.append(
                discord.SelectOption(
                    label=member_texts.AMOUNT_OPTION_CUSTOM,
                    description=member_texts.AMOUNT_OPTION_CUSTOM_DESCRIPTION.format(
                        max_points=current_points
                    ),
                    value="custom"
                )
            )

        super().__init__(
            placeholder=member_texts.AMOUNT_SELECT_PLACEHOLDER,
            min_values=1,
            max_values=1,
            options=options
        )

    async def callback(self,interaction: discord.Interaction):

        # メニューを開いた本人以外は操作不可
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(member_texts.NOT_YOUR_MENU,ephemeral=True)
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
class InvitePointAmountModal(discord.ui.Modal,title=member_texts.AMOUNT_MODAL_TITLE):

    amount = discord.ui.TextInput(
        label=member_texts.AMOUNT_MODAL_LABEL,
        placeholder=member_texts.AMOUNT_MODAL_PLACEHOLDER,
        required=True,
        max_length=10
    )

    def __init__(self,benefit: str,user_id: int):
        super().__init__()

        self.benefit = benefit
        self.user_id = user_id

    async def on_submit(self,interaction: discord.Interaction):

        if interaction.user.id != self.user_id:
            await interaction.response.send_message(member_texts.NOT_YOUR_MODAL,ephemeral=True)
            return

        amount_text = self.amount.value.strip()
        if not amount_text.isdigit():
            await interaction.response.send_message(member_texts.AMOUNT_NOT_NUMBER,ephemeral=True)
            return

        use_points = int(amount_text)
        current_points = get_invite_points(interaction.user.id)

        if use_points < 25:
            await interaction.response.send_message(member_texts.AMOUNT_TOO_SMALL,ephemeral=True)
            return

        if use_points > current_points:
            await interaction.response.send_message(
                member_texts.AMOUNT_OVER_OWNED.format(points=current_points),
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
        await interaction.followup.send(common_texts.GUILD_NOT_FOUND,ephemeral=True)
        return

    has_member_rank = any(role.id in config.ROLE_GROUP_MEMBER for role in member.roles)
    current_points = get_invite_points(member.id)

    if use_points <= 0:
        await interaction.followup.send(member_texts.AMOUNT_MUST_BE_POSITIVE,ephemeral=True)
        return

    if use_points > current_points:
        await interaction.followup.send(
            member_texts.INVITE_NOT_ENOUGH_POINTS_DETAIL.format(
                points=current_points
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
            await interaction.followup.send(member_texts.NOT_TRIAL_MEMBER,ephemeral=True)
            return

        extension_days = (use_points * config.INVITE_TRIAL_MEMBER_EXTENSION_DAYS)
        success = spend_invite_points(member.id,use_points)

        if not success:
            await interaction.followup.send(member_texts.INVITE_NOT_ENOUGH_POINTS,ephemeral=True)
            return

        extended = extend_trial_member_end_date(member.id,extension_days)

        if not extended:
            add_invite_points(member.id,use_points)

            await interaction.followup.send(member_texts.TRIAL_MEMBER_NOT_FOUND_FOR_EXTENSION,ephemeral=True)
            return

        trial_member_cog = interaction.client.get_cog("TrialMember")

        if trial_member_cog is not None:
            await trial_member_cog.update_trial_member_end_embed(member.id)

        benefit_name = member_texts.BENEFIT_NAME_TRIAL_EXTENSION
        result_text = member_texts.RESULT_TRIAL_EXTENSION.format(
            days=extension_days
        )

    # ==================================================
    # 七聖・騎士の宿屋無料確率
    # ==================================================
    elif benefit == "member_hotel_rate":

        if not has_member_rank:
            await interaction.followup.send(member_texts.NOT_MEMBER,ephemeral=True)
            return

        current_rate = get_hotel_free_rate(member.id)
        if current_rate >= config.MAX_HOTEL_FREE_RATE:
            await interaction.followup.send(
                member_texts.HOTEL_RATE_MAX.format(rate=config.MAX_HOTEL_FREE_RATE),
                ephemeral=True
            )
            return

        increase_rate = (use_points * config.INVITE_MEMBER_FREE_RATE)

        if (current_rate + increase_rate > config.MAX_HOTEL_FREE_RATE):
            remaining_rate = (config.MAX_HOTEL_FREE_RATE - current_rate)

            max_points = (remaining_rate // config.INVITE_MEMBER_FREE_RATE)

            await interaction.followup.send(
                member_texts.HOTEL_RATE_OVER.format(
                    rate=current_rate,
                    max_rate=config.MAX_HOTEL_FREE_RATE,
                    max_points=max_points
                ),
                ephemeral=True
            )
            return

        success = spend_invite_points(member.id,use_points)

        if not success:
            await interaction.followup.send(member_texts.INVITE_NOT_ENOUGH_POINTS,ephemeral=True)
            return

        old_rate = current_rate
        add_hotel_free_rate(member.id,increase_rate)

        new_rate = get_hotel_free_rate(member.id)
        benefit_name = member_texts.BENEFIT_NAME_HOTEL_RATE
        result_text = member_texts.RESULT_HOTEL_RATE.format(
            old_rate=old_rate,
            new_rate=new_rate
        )

    # ==================================================
    # Coin交換
    # ==================================================
    elif benefit in ("trial_member_coin","member_coin","associate_member_coin","demoted_coin"):

        # 精霊
        if benefit == "trial_member_coin":

            if (trial_member_role is None or trial_member_role not in member.roles):
                await interaction.followup.send(member_texts.NOT_TRIAL_MEMBER,ephemeral=True)
                return

        # 七聖・騎士
        elif benefit == "member_coin":

            if not has_member_rank:
                await interaction.followup.send(member_texts.NOT_MEMBER,ephemeral=True)
                return

        # 小人
        elif benefit == "associate_member_coin":

            if (associate_member_role is None or associate_member_role not in member.roles):
                await interaction.followup.send(member_texts.NOT_ASSOCIATE_MEMBER,ephemeral=True)
                return

        # 魔物
        elif benefit == "demoted_coin":

            if (demoted_role is None or demoted_role not in member.roles):
                await interaction.followup.send(member_texts.NOT_DEMOTED,ephemeral=True)
                return

        coin_per_point = (
            config.INVITE_DEMOTED_COIN_REWARD
            if benefit == "demoted_coin"
            else config.INVITE_COIN_REWARD
        )

        coin_amount = (use_points * coin_per_point)
        success = spend_invite_points(member.id,use_points)
        if not success:
            await interaction.followup.send(member_texts.INVITE_NOT_ENOUGH_POINTS,ephemeral=True)
            return

        add_balance(member.id,coin_amount)

        coin_cog = interaction.client.get_cog("Coin")
        if coin_cog is not None:
            await coin_cog.update_debt_status(guild,member)

        benefit_name = member_texts.BENEFIT_NAME_COIN
        result_text = member_texts.RESULT_COIN.format(coin=f"{coin_amount:,}")

    else:
        await interaction.followup.send(member_texts.BENEFIT_UNKNOWN,ephemeral=True)
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
        title=member_texts.INVITE_USE_TITLE,
        description=member_texts.INVITE_USE_BODY.format(
            benefit=benefit_name,
            points=use_points,
            result=result_text,
            remaining=remaining_points
        ),
        color=config.COLOR_WHITE
    )
    embed.set_thumbnail(url=member.display_avatar.url)

    await interaction.followup.send(embed=embed,ephemeral=True)
