"""RAGNA Botの起動処理。

データベース初期化、Cog読込、Slash Command同期、終了時保存を担当します。
個別機能の実装は ``cogs/`` 配下に置いてください。
"""

import logging
import sys

import discord
import config

from discord.ext import commands
from database.connection import init_database
from database.familiar import sync_master_data
from game.master_data import load_master_data
from discord_settings import (
    REQUIRED_INTENT_NAMES,
    create_allowed_mentions,
    create_intents,
)


logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    # Railwayではstderrが赤く表示されるため、通常ログはstdoutへ出す。
    stream=sys.stdout,
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

# RAGNA Online（ギルド・使い魔・ギルドバトル）。
# マスターデータの読み込みに失敗した場合は、既存機能を止めずにこの3つだけ読み込みません。
GAME_EXTENSIONS = (
    "cogs.guild",
    "cogs.familiar",
    "cogs.guild_battle",
)


def load_game_master_data() -> bool:
    """使い魔・スキル・ガチャのマスターデータを検証してDBへ同期する。"""

    try:
        master = load_master_data()
        sync_master_data()
    except Exception:
        logger.exception("RAGNA Onlineのマスターデータを読み込めませんでした")
        return False

    missing_ranks = master.missing_ranks()
    if missing_ranks:
        logger.info(
            "使い魔マスター未登録のランク: %s。"
            "追加するまで、その排出率は設定した寄せ先ランクへ加算します。",
            "・".join(missing_ranks),
        )

    return True


class RagnaBot(commands.Bot):
    async def setup_hook(self) -> None:
        init_database()

        for extension in EXTENSIONS:
            await self.load_extension(extension)

        if load_game_master_data():
            for extension in GAME_EXTENSIONS:
                await self.load_extension(extension)

            if not config.GAME_ENABLED:
                logger.info(
                    "GAME_ENABLED が未設定のため、RAGNA Onlineのパネルは設置しません"
                )

        logger.info("登録済みCog: %s", ", ".join(self.cogs))
        logger.info("有効なDiscord Intent: %s", ", ".join(REQUIRED_INTENT_NAMES))

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


bot = RagnaBot(
    # 現在はSlash Commandのみ。誤解を招く「!コマンド」と標準helpは公開しない。
    command_prefix=commands.when_mentioned,
    help_command=None,
    intents=create_intents(),
    max_messages=200,
    allowed_mentions=create_allowed_mentions(),
)


@bot.event
async def on_ready() -> None:
    logger.info(
        "ログイン完了: %s (%s)", bot.user, bot.user.id if bot.user else "unknown"
    )


def main() -> None:
    bot.run(config.TOKEN, log_handler=None)


if __name__ == "__main__":
    main()
