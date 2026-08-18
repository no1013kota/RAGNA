"""常設パネルの設置・並び順・改名追従のテスト（GAME_SPEC 8.3節・29節）。"""

from __future__ import annotations

import asyncio
import os
import unittest


# cogs 経由で config.py を読み込むため、先に必須の環境変数を用意する。
os.environ.setdefault("DISCORD_BOT_TOKEN", "test-token")
os.environ.setdefault("DISCORD_GUILD_ID", "1")

import discord  # noqa: E402

import utils  # noqa: E402
from cogs.familiar import service as familiar_service  # noqa: E402
from cogs.guild import service as guild_service  # noqa: E402
from cogs.guild import views as guild_views  # noqa: E402
from cogs.guild_battle import views as battle_views  # noqa: E402


BOT_USER_ID = 1000


# ==================================================
# Discordの代役（履歴・送信・書き換え・削除だけを持つ）
# ==================================================
class FakeUser:
    def __init__(self, user_id: int = BOT_USER_ID) -> None:
        self.id = user_id


class FakeBot:
    def __init__(self) -> None:
        self.user = FakeUser()
        self.cogs: dict = {}


class FakeRow:
    """discord.py の ActionRow の代役。"""

    def __init__(self, children) -> None:
        self.children = list(children)
        self.custom_id = None


class FakeMessage:
    def __init__(self, channel, author, embed, view) -> None:
        self.channel = channel
        self.author = author
        self.id = channel.next_message_id()
        self.embeds = [embed] if embed is not None else []
        self.components = [FakeRow(view.children)] if view is not None else []
        self.edits = 0

    async def delete(self) -> None:
        self.channel.messages.remove(self)

    async def edit(self, embed=None, view=None) -> None:
        if embed is not None:
            self.embeds = [embed]
        if view is not None:
            self.components = [FakeRow(view.children)]
        self.edits += 1


class FakeChannel:
    def __init__(self, channel_id: int, name: str = "ch") -> None:
        self.id = channel_id
        self.name = name
        self.messages: list[FakeMessage] = []  # 古い順（Discordの見た目と同じ並び）
        self._message_id = 0

    def next_message_id(self) -> int:
        self._message_id += 1
        return self.id * 1000 + self._message_id

    def history(self, limit: int = 30):
        async def generator():
            for message in list(reversed(self.messages))[:limit]:
                yield message

        return generator()

    async def send(self, embed=None, view=None) -> FakeMessage:
        message = FakeMessage(self, FakeUser(), embed, view)
        self.messages.append(message)
        return message

    async def edit(self, *, name=None, reason=None) -> None:
        if name is not None:
            self.name = name

    @property
    def titles(self) -> list[str]:
        return [
            message.embeds[0].title if message.embeds else None
            for message in self.messages
        ]


class FakeGuild:
    def __init__(self, channels: dict[int, FakeChannel]) -> None:
        self._channels = channels

    def get_channel(self, channel_id):
        return self._channels.get(int(channel_id))


def run(coro):
    return asyncio.run(coro)


class MemberPanelTitleTests(unittest.TestCase):
    """メンバー用パネルの表題はギルド名（8.3節）。"""

    def test_the_guild_name_becomes_the_title(self) -> None:
        self.assertEqual(guild_service.member_panel_title({"name": "黒鉄の団"}), "黒鉄の団")

    def test_the_embed_uses_the_guild_name(self) -> None:
        embed = guild_service.build_member_panel_embed({"name": "黒鉄の団"})
        self.assertEqual(embed.title, "黒鉄の団")

    def test_the_embed_uses_no_fields(self) -> None:
        embed = guild_service.build_member_panel_embed({"name": "黒鉄の団"})
        self.assertEqual(embed.fields, [])

    def test_a_missing_name_falls_back_to_the_old_title(self) -> None:
        self.assertEqual(
            guild_service.member_panel_title({"name": ""}),
            guild_service.LEGACY_MEMBER_PANEL_TITLES[0],
        )
        self.assertEqual(
            guild_service.member_panel_title({}),
            guild_service.LEGACY_MEMBER_PANEL_TITLES[0],
        )

    def test_a_long_name_stays_within_the_embed_limit(self) -> None:
        self.assertEqual(len(guild_service.member_panel_title({"name": "あ" * 400})), 256)

    def test_the_matched_custom_ids_belong_to_the_member_panel(self) -> None:
        # 表題ではなくボタンで見分けるため、Viewと突き合わせておく
        view_ids = {item.custom_id for item in guild_views.GuildMemberPanelView().children}
        self.assertTrue(set(guild_service.MEMBER_PANEL_CUSTOM_IDS) <= view_ids)


class FamiliarSetChannelPanelTests(unittest.TestCase):
    """使い魔セットチャンネルの並び順（8.3節）。"""

    def setUp(self) -> None:
        guild_service._legacy_member_panels_checked.clear()
        self.bot = FakeBot()
        self.master_text = FakeChannel(100, "ギルドマスター専用tc")
        self.guild_text = FakeChannel(200, "ギルドtc")
        self.set_channel = FakeChannel(300, "使い魔セット")
        self.discord_guild = FakeGuild(
            {100: self.master_text, 200: self.guild_text, 300: self.set_channel}
        )
        self.guild_row = {
            "guild_id": 1,
            "name": "黒鉄の団",
            "master_text_channel_id": 100,
            "guild_text_channel_id": 200,
            "battle_member_channel_id": 300,
        }

    def install(self) -> None:
        run(
            battle_views.ensure_roster_panel(
                self.bot, self.discord_guild, self.guild_row
            )
        )

    def test_the_member_panel_comes_before_the_roster_panel(self) -> None:
        self.install()

        self.assertEqual(
            self.set_channel.titles,
            ["黒鉄の団", battle_views.ROSTER_PANEL_TITLE],
        )

    def test_nothing_is_posted_to_the_guild_text_channel(self) -> None:
        self.install()
        self.assertEqual(self.guild_text.titles, [])

    def test_calling_twice_does_not_duplicate_panels(self) -> None:
        self.install()
        self.install()

        self.assertEqual(len(self.set_channel.messages), 2)
        self.assertTrue(all(m.edits == 0 for m in self.set_channel.messages))

    def test_a_renamed_guild_updates_the_panel_in_place(self) -> None:
        self.install()
        member_panel = self.set_channel.messages[0]

        self.guild_row["name"] = "白銀の盾"
        status = run(
            guild_service.ensure_member_panel(
                self.bot, self.discord_guild, self.guild_row
            )
        )

        self.assertEqual(status, utils.PANEL_UPDATED)
        self.assertEqual(len(self.set_channel.messages), 2)
        self.assertIs(self.set_channel.messages[0], member_panel)
        self.assertEqual(
            self.set_channel.titles,
            ["白銀の盾", battle_views.ROSTER_PANEL_TITLE],
        )

    def test_a_deleted_member_panel_is_restored(self) -> None:
        self.install()
        run(self.set_channel.messages[0].delete())

        self.install()

        self.assertEqual(
            self.set_channel.titles,
            ["黒鉄の団", battle_views.ROSTER_PANEL_TITLE],
        )

    def test_a_missing_familiar_set_channel_is_reported(self) -> None:
        del self.guild_row["battle_member_channel_id"]

        status = run(
            guild_service.ensure_member_panel(
                self.bot, self.discord_guild, self.guild_row
            )
        )

        self.assertEqual(status, utils.PANEL_MISSING)


class MemberPanelMigrationTests(unittest.TestCase):
    """旧配置（ギルドTCの「ギルドメンバー」）からの移行（8.3節）。"""

    def setUp(self) -> None:
        guild_service._legacy_member_panels_checked.clear()
        self.bot = FakeBot()
        self.guild_text = FakeChannel(200, "ギルドtc")
        self.set_channel = FakeChannel(300, "バトル出場者専用tc")
        self.discord_guild = FakeGuild({200: self.guild_text, 300: self.set_channel})
        self.guild_row = {
            "guild_id": 1,
            "name": "紅蓮の牙",
            "guild_text_channel_id": 200,
            "battle_member_channel_id": 300,
        }

        # 旧状態を再現する
        run(
            self.guild_text.send(
                embed=discord.Embed(
                    title=guild_service.LEGACY_MEMBER_PANEL_TITLES[0]
                ),
                view=guild_views.GuildMemberPanelView(),
            )
        )
        run(self.guild_text.send(embed=discord.Embed(title="ギルド情報"), view=None))
        self.old_roster = run(
            self.set_channel.send(
                embed=battle_views.roster_panel_embed(),
                view=battle_views.BattleMemberPanelView(),
            )
        )

        run(
            battle_views.ensure_roster_panel(
                self.bot, self.discord_guild, self.guild_row
            )
        )

    def test_the_old_member_panel_is_removed(self) -> None:
        self.assertNotIn(
            guild_service.LEGACY_MEMBER_PANEL_TITLES[0], self.guild_text.titles
        )

    def test_other_messages_are_left_alone(self) -> None:
        self.assertEqual(self.guild_text.titles, ["ギルド情報"])

    def test_the_order_is_fixed_by_reposting_the_roster_panel(self) -> None:
        self.assertEqual(
            self.set_channel.titles,
            ["紅蓮の牙", battle_views.ROSTER_PANEL_TITLE],
        )
        self.assertNotIn(self.old_roster, self.set_channel.messages)

    def test_the_cleanup_only_reads_history_once_per_boot(self) -> None:
        # 定期タスクから何度も呼ばれても、旧ギルドTCを読み直さない
        run(self.guild_text.send(embed=discord.Embed(title="ギルドメンバー"),
                                 view=guild_views.GuildMemberPanelView()))
        run(battle_views.ensure_roster_panel(self.bot, self.discord_guild, self.guild_row))

        self.assertIn("ギルドメンバー", self.guild_text.titles)

    def test_the_channel_is_renamed(self) -> None:
        self.assertEqual(
            self.set_channel.name, battle_views.game_shared.CHANNEL_LABELS["battle_member"]
        )


class PanelCustomIdTests(unittest.TestCase):
    def test_custom_ids_are_collected_from_action_rows(self) -> None:
        channel = FakeChannel(1)
        message = run(
            channel.send(
                embed=discord.Embed(title="表題"),
                view=guild_views.GuildMemberPanelView(),
            )
        )

        self.assertEqual(
            utils.panel_custom_ids(message), {"guild:info", "guild:leave"}
        )

    def test_a_message_without_components_has_no_custom_ids(self) -> None:
        channel = FakeChannel(1)
        message = run(channel.send(embed=discord.Embed(title="表題"), view=None))

        self.assertEqual(utils.panel_custom_ids(message), set())


class GachaCelebrationTests(unittest.TestCase):
    """最高ランクを引いたときのお祝い表示（10.2節）。"""

    INSTANCES = (
        {"instance_id": 1, "familiar_id": "loki", "rank": "S", "level": 1},
        {"instance_id": 2, "familiar_id": "loki", "rank": "S", "level": 1},
        {"instance_id": 3, "familiar_id": "garm", "rank": "B", "level": 1},
        {"instance_id": 4, "familiar_id": "fenrir", "rank": "S", "level": 1},
    )

    def setUp(self) -> None:
        self.found = self.celebrate(list(self.INSTANCES))

    def celebrate(self, instances: list[dict]) -> list[tuple]:
        """お祝いEmbedを作り、開いた画像を必ず閉じるようにする。"""

        found = familiar_service.build_celebration_embeds(instances)
        for _, icon in found:
            if icon is not None:
                self.addCleanup(icon.close)

        return found

    def named(self, keyword: str) -> tuple:
        return next(pair for pair in self.found if keyword in pair[0].author.name)

    def test_the_celebrated_rank_is_the_highest_one(self) -> None:
        self.assertEqual(familiar_service.top_rank(), "S")

    def test_only_the_highest_rank_is_celebrated(self) -> None:
        names = {embed.author.name for embed, _ in self.found}

        self.assertEqual(len(self.found), 2)
        self.assertTrue(any("ロキ" in name for name in names))
        self.assertTrue(any("フェンリル" in name for name in names))
        self.assertFalse(any("ガルム" in name for name in names))

    def test_lower_ranks_alone_celebrate_nothing(self) -> None:
        self.assertEqual(
            self.celebrate(
                [{"instance_id": 1, "familiar_id": "garm", "rank": "B", "level": 1}]
            ),
            [],
        )

    def test_no_draws_celebrate_nothing(self) -> None:
        self.assertEqual(self.celebrate([]), [])

    def test_the_familiar_art_is_used_as_the_icon(self) -> None:
        embed, icon = self.named("ロキ")

        self.assertIsNotNone(icon)
        self.assertEqual(embed.author.icon_url, f"attachment://{icon.filename}")

    def test_the_same_familiar_is_grouped_with_a_count(self) -> None:
        embed, _ = self.named("ロキ")

        self.assertIn("【体数】2体", embed.description)

    def test_a_single_familiar_shows_no_count(self) -> None:
        embed, _ = self.named("フェンリル")

        self.assertNotIn("【体数】", embed.description)

    def test_stats_and_skills_are_shown(self) -> None:
        for embed, _ in self.found:
            self.assertIn("【HP】", embed.description)
            self.assertIn("【ATK】", embed.description)
            self.assertIn("【パッシブ】", embed.description)

    def test_the_embeds_use_no_fields(self) -> None:
        for embed, _ in self.found:
            self.assertEqual(embed.fields, [])

    def test_the_embeds_stay_within_discord_limits(self) -> None:
        for embed, _ in self.found:
            self.assertLessEqual(len(embed.description or ""), 4096)
            self.assertLessEqual(len(embed.title or ""), 256)


if __name__ == "__main__":
    unittest.main()
