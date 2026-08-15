"""ATMのボタン、選択画面、入力画面をまとめたDiscord UI層。"""

import logging
import discord
import config

from datetime import timezone, timedelta
from database.coin import (
    get_balance,
)

JST = timezone(timedelta(hours=9))
logger = logging.getLogger(__name__)

class ATMView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    # 送金
    @discord.ui.button(label="送金",style=discord.ButtonStyle.primary,custom_id="atm:transfer")
    async def transfer(self,interaction: discord.Interaction,button: discord.ui.Button):

        await interaction.response.send_message(
            "送金先を選択してください。",
            view=TransferUserSelectView(),
            ephemeral=True
        )

    # 残高照会
    @discord.ui.button(label="残高照会",style=discord.ButtonStyle.secondary,custom_id="atm:balance")
    async def balance(self,interaction: discord.Interaction,button: discord.ui.Button):

        balance = get_balance(interaction.user.id)
        embed = discord.Embed(
            title="残高照会",
            description=(f"現在の残高は **{balance:,} coin**"),
            color=config.COLOR_YELLOW
        )
        embed.set_thumbnail(url=interaction.user.display_avatar.url)

        await interaction.response.send_message(embed=embed,ephemeral=True)


# ==========================================
# 送金先選択
# ==========================================
class TransferUserSelectView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)

        self.add_item(TransferUserSelect())


class TransferUserSelect(discord.ui.UserSelect):
    def __init__(self):
        super().__init__(placeholder="送金先を選択してください",min_values=1,max_values=1)

    async def callback(self,interaction: discord.Interaction):

        selected_user = self.values[0]

        # Guild内のMemberとして取得
        target = interaction.guild.get_member(selected_user.id)

        if target is None:
            await interaction.response.send_message("送金先のユーザーを取得できませんでした。",ephemeral=True)
            return

        if target.bot:
            await interaction.response.send_message("Botには送金できません。",ephemeral=True)
            return

        if target.id == interaction.user.id:
            await interaction.response.send_message("自分には送金できません。",ephemeral=True)
            return

        await interaction.response.send_modal(TransferModal(target))

# ==========================================
# 送金内容入力
# ==========================================
class TransferModal(discord.ui.Modal):
    def __init__(self,target: discord.Member):
        super().__init__(title=f"{target.display_name} へ送金")

        self.target = target
        self.amount = discord.ui.TextInput(
            label="送金額",
            placeholder="1000以上の数字を入力",
            required=True,
            max_length=10
        )
        self.note = discord.ui.TextInput(
            label="コメント",
            placeholder="任意",
            required=False,
            style=discord.TextStyle.paragraph,
            max_length=500
        )

        self.add_item(self.amount)
        self.add_item(self.note)

    async def on_submit(self,interaction: discord.Interaction):

        try:
            amount = int(self.amount.value.strip())

        except ValueError:
            await interaction.response.send_message("送金額は数字で入力してください。",ephemeral=True)
            return

        if amount < 1000:
            await interaction.response.send_message("1000coin以上から送金できます。",ephemeral=True)
            return

        note = (
            self.note.value.strip()
            or ""
        )

        coin_cog = interaction.client.get_cog("Coin")

        if coin_cog is None:
            await interaction.response.send_message("coin管理機能を取得できませんでした。",ephemeral=True)
            return

        await coin_cog.execute_transfer(interaction=interaction,user=self.target,amount=amount,note=note)
