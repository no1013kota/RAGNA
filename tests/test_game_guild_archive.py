"""解散ギルドの保存カテゴリーのテスト（GAME_SPEC 7.5節）。"""

from __future__ import annotations

import asyncio
import os
import unittest


os.environ.setdefault("DISCORD_BOT_TOKEN", "test-token")
os.environ.setdefault("DISCORD_GUILD_ID", "1")

from cogs import game_shared  # noqa: E402


def run(coro):
    return asyncio.run(coro)


class FakeChannel:
    def __init__(self, channel_id: int, name: str, category=None) -> None:
        self.id = channel_id
        self.name = name
        self.category = category
        self.deleted = False
        if category is not None:
            category.channels.append(self)

    async def edit(self, *, name=None, category=None, overwrites=None, reason=None):
        if name is not None:
            self.name = name
        if category is not None and category is not self.category:
            if self.category is not None:
                self.category.channels.remove(self)
            self.category = category
            category.channels.append(self)

    async def delete(self, reason=None):
        self.deleted = True
        if self.category is not None:
            self.category.channels.remove(self)


class FakeCategory:
    def __init__(self, guild, category_id: int, name: str) -> None:
        self.id = category_id
        self.name = name
        self.channels: list[FakeChannel] = []
        self.deleted = False
        guild.register(self)

    async def delete(self, reason=None):
        self.deleted = True


class FakeGuild:
    """カテゴリー操作だけを持つ ``discord.Guild`` の代役。"""

    def __init__(self) -> None:
        self._channels: dict[int, object] = {}
        self.categories: list[FakeCategory] = []
        self.default_role = object()
        self.me = None
        self._next_id = 9000

    def register(self, category: FakeCategory) -> None:
        self._channels[category.id] = category
        self.categories.append(category)

    def get_channel(self, channel_id):
        return self._channels.get(int(channel_id))

    def get_role(self, role_id):
        return None

    async def create_category(self, *, name, overwrites=None, reason=None):
        self._next_id += 1
        return FakeCategory(self, self._next_id, name)


# discord.CategoryChannel の isinstance 判定を代役へ通す
class _Patched(unittest.TestCase):
    def setUp(self) -> None:
        import discord

        self._original = discord.CategoryChannel
        discord.CategoryChannel = FakeCategory  # type: ignore[misc]
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        import discord

        discord.CategoryChannel = self._original  # type: ignore[misc]


class ArchiveCategoryTests(_Patched):
    """保存カテゴリーは空きのあるものを使い、埋まったら次を作る。"""

    def test_the_category_is_created_when_missing(self) -> None:
        guild = FakeGuild()

        category = run(game_shared.ensure_guild_archive_category(guild))

        self.assertEqual(category.name, game_shared.GUILD_ARCHIVE_CATEGORY_NAME)
        self.assertEqual(len(guild.categories), 1)

    def test_an_existing_category_with_room_is_reused(self) -> None:
        guild = FakeGuild()
        existing = FakeCategory(guild, 1, game_shared.GUILD_ARCHIVE_CATEGORY_NAME)

        category = run(game_shared.ensure_guild_archive_category(guild, needed=5))

        self.assertIs(category, existing)
        self.assertEqual(len(guild.categories), 1)

    def test_a_full_category_overflows_into_the_next_one(self) -> None:
        guild = FakeGuild()
        full = FakeCategory(guild, 1, game_shared.GUILD_ARCHIVE_CATEGORY_NAME)
        for index in range(game_shared.CATEGORY_CHANNEL_LIMIT):
            FakeChannel(100 + index, f"ch{index}", category=full)

        category = run(game_shared.ensure_guild_archive_category(guild, needed=5))

        self.assertIsNot(category, full)
        self.assertEqual(
            category.name, f"{game_shared.GUILD_ARCHIVE_CATEGORY_NAME}2"
        )

    def test_the_numbered_categories_are_reused_in_order(self) -> None:
        guild = FakeGuild()
        first = FakeCategory(guild, 1, game_shared.GUILD_ARCHIVE_CATEGORY_NAME)
        for index in range(game_shared.CATEGORY_CHANNEL_LIMIT):
            FakeChannel(200 + index, f"ch{index}", category=first)
        second = FakeCategory(guild, 2, f"{game_shared.GUILD_ARCHIVE_CATEGORY_NAME}2")

        category = run(game_shared.ensure_guild_archive_category(guild, needed=5))

        self.assertIs(category, second)


class ArchiveGuildChannelsTests(_Patched):
    """解散したギルドのチャンネルを保存カテゴリーへ移す。"""

    def setUp(self) -> None:
        super().setUp()
        self.guild = FakeGuild()
        self.category = FakeCategory(self.guild, 500, "黒鉄の団")
        self.channels = [
            FakeChannel(501, "ギルドtc", category=self.category),
            FakeChannel(502, "ギルドvc", category=self.category),
            FakeChannel(503, "ギルド情報", category=self.category),
        ]
        self.guild_row = {"guild_id": 1, "name": "黒鉄の団", "category_id": 500}

    def archive(self) -> bool:
        return run(
            game_shared.archive_guild_channels(
                self.guild, self.guild_row, prefix="【解散済】"
            )
        )

    def test_the_channels_move_into_the_archive_category(self) -> None:
        self.assertTrue(self.archive())

        archive = next(
            c
            for c in self.guild.categories
            if c.name == game_shared.GUILD_ARCHIVE_CATEGORY_NAME
        )
        self.assertEqual(len(archive.channels), 3)
        for channel in self.channels:
            self.assertIs(channel.category, archive)

    def test_the_channel_names_say_which_guild_they_came_from(self) -> None:
        self.archive()

        self.assertEqual(
            [channel.name for channel in self.channels],
            [
                "【解散済】黒鉄の団-ギルドtc",
                "【解散済】黒鉄の団-ギルドvc",
                "【解散済】黒鉄の団-ギルド情報",
            ],
        )

    def test_the_empty_guild_category_is_deleted(self) -> None:
        self.archive()

        self.assertTrue(self.category.deleted)


class PurgeArchivedChannelsTests(_Patched):
    """保存期間後は、記録したチャンネルIDで消す（移動済みでも消える）。"""

    def test_moved_channels_are_deleted_by_their_recorded_ids(self) -> None:
        guild = FakeGuild()
        archive = FakeCategory(guild, 900, game_shared.GUILD_ARCHIVE_CATEGORY_NAME)
        moved = [
            FakeChannel(601, "【解散済】黒鉄の団-ギルドtc", category=archive),
            FakeChannel(602, "【解散済】黒鉄の団-ギルド情報", category=archive),
        ]
        for channel in moved:
            guild._channels[channel.id] = channel

        guild_row = {
            "guild_id": 1,
            # 解散時にカテゴリーは消えているため、辿れないIDが残る
            "category_id": 500,
            "guild_text_channel_id": 601,
            "info_channel_id": 602,
        }

        self.assertTrue(run(game_shared.delete_guild_channels(guild, guild_row)))
        for channel in moved:
            self.assertTrue(channel.deleted, channel.name)


if __name__ == "__main__":
    unittest.main()
