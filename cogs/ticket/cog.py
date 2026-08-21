"""問い合わせチケットの作成、担当、完了、再開を管理するCog。"""

import logging
import discord
import config
import asyncio

from discord.ext import commands, tasks
from utils import ensure_panel_message
from .views import TicketCloseView, TicketManagerView, TicketPanelView, TicketStaffView
from texts import panels as panel_texts
from texts import ticket as ticket_texts


logger = logging.getLogger(__name__)


# ==========================================
# お問い合わせパネル
# ==========================================
class Ticket(commands.Cog):
    def __init__(self,bot: commands.Bot):
        self.bot = bot

        self.ticket_panel.start()

    # ==========================================
    # お問い合わせパネル自動設置
    # ==========================================
    @tasks.loop(minutes=5)
    async def ticket_panel(self):

        guild = self.bot.get_guild(config.GUILD_ID)
        if guild is None:
            return

        embed = discord.Embed(
            title=panel_texts.TICKET,
            description=ticket_texts.PANEL_BODY,
            color=config.COLOR_WHITE
        )

        await ensure_panel_message(
            self.bot,
            guild,
            config.CHANNEL_TICKET_PANEL,
            panel_title=panel_texts.TICKET,
            embed=embed,
            view=TicketPanelView(),
            panel_name="お問い合わせパネル",
        )

    @ticket_panel.before_loop
    async def before_ticket_panel(self):

        await self.bot.wait_until_ready()
        await asyncio.sleep(5)

    def cog_unload(self):

        self.ticket_panel.cancel()


# ==========================================
# Cog読込
# ==========================================
async def setup(bot: commands.Bot):

    await bot.add_cog(Ticket(bot),guild=discord.Object(id=config.GUILD_ID))

    # 再起動後も使用できる永続View
    bot.add_view(TicketPanelView())
    bot.add_view(TicketCloseView())
    bot.add_view(TicketStaffView())
    bot.add_view(TicketManagerView())

    logger.info("Ticket Cog 登録完了")
