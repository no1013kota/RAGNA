"""RAGNA Botの起動処理。

データベース初期化、Cog読込、Slash Command同期、終了時保存を担当します。
個別機能の実装は ``cogs/`` 配下に置いてください。
"""

import logging

import discord
import config

from discord.ext import commands
from database import init_database


logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("ragna")

EXTENSIONS = (
    "cogs.coin",
    "cogs.trial_member",
    "cogs.xp",
    "cogs.member",
    "cogs.hotel",
    "cogs.ranking",
    "cogs.class_change",
    "cogs.ticket",
    "cogs.introduction",
)


class RagnaBot(commands.Bot):
    async def setup_hook(self) -> None:
        init_database()

        for extension in EXTENSIONS:
            await self.load_extension(extension)

        logger.info("登録済みCog: %s", ", ".join(self.cogs))

        guild = discord.Object(id=config.GUILD_ID)
        synced = await self.tree.sync(guild=guild)
        logger.info("Slash Commandを%d個同期しました", len(synced))

    async def close(self) -> None:
        xp_cog = self.get_cog("XP")
        if xp_cog is not None:
            try:
                await xp_cog.flush_sessions()
            except Exception:
                logger.exception("終了前のVC時間保存に失敗しました")

        await super().close()


intents = discord.Intents.default()
intents.members = True
intents.presences = False
intents.message_content = False

bot = RagnaBot(
    command_prefix="!",
    intents=intents,
    max_messages=200,
    allowed_mentions=discord.AllowedMentions(
        everyone=False,
        roles=True,
        users=True,
        replied_user=False,
    ),
)


@bot.event
async def on_ready() -> None:
    logger.info("ログイン完了: %s (%s)", bot.user, bot.user.id if bot.user else "unknown")


def main() -> None:
    bot.run(config.TOKEN, log_handler=None)


if __name__ == "__main__":
    main()
