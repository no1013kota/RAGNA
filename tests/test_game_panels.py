"""常設パネルの設置・並び順・改名追従のテスト（GAME_SPEC 8.3節・29節）。"""

from __future__ import annotations

import asyncio
import os
import re
import unittest

from pathlib import Path


# cogs 経由で config.py を読み込むため、先に必須の環境変数を用意する。
os.environ.setdefault("DISCORD_BOT_TOKEN", "test-token")
os.environ.setdefault("DISCORD_GUILD_ID", "1")

import discord  # noqa: E402

import config  # noqa: E402
import utils  # noqa: E402
from cogs.coin import views as coin_views  # noqa: E402
from cogs.familiar import service as familiar_service  # noqa: E402
from cogs.guild import service as guild_service  # noqa: E402
from cogs.guild import views as guild_views  # noqa: E402
from cogs.guild_battle import views as battle_views  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
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


class TrimmingChannel(FakeChannel):
    """Discordと同じく、説明文の前後の空白を落として保存するチャンネル。"""

    @staticmethod
    def _trim(embed):
        if embed is not None and embed.description:
            embed.description = embed.description.strip()
        return embed

    async def send(self, embed=None, view=None):
        return await super().send(embed=self._trim(embed), view=view)


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


class FamiliarBattleChannelPanelTests(unittest.TestCase):
    """使い魔バトルチャンネルには使い魔セットパネルだけを置く（8.3節）。"""

    def setUp(self) -> None:
        guild_service._legacy_member_panels_checked.clear()
        self.bot = FakeBot()
        self.master_text = FakeChannel(100, "ギルドマスター専用tc")
        self.guild_text = FakeChannel(200, "ギルドtc")
        self.battle_channel = FakeChannel(300, "使い魔セット")
        self.info_channel = FakeChannel(400, "ギルド情報")
        self.discord_guild = FakeGuild(
            {
                100: self.master_text,
                200: self.guild_text,
                300: self.battle_channel,
                400: self.info_channel,
            }
        )
        self.guild_row = {
            "guild_id": 1,
            "name": "黒鉄の団",
            "master_text_channel_id": 100,
            "guild_text_channel_id": 200,
            "battle_member_channel_id": 300,
            "info_channel_id": 400,
        }

    def install(self) -> None:
        run(
            battle_views.ensure_roster_panel(
                self.bot, self.discord_guild, self.guild_row
            )
        )

    def test_only_the_roster_panel_is_placed_here(self) -> None:
        self.install()

        self.assertEqual(self.battle_channel.titles, [battle_views.ROSTER_PANEL_TITLE])

    def test_the_guild_info_panel_is_not_placed_here(self) -> None:
        self.install()

        self.assertNotIn("黒鉄の団", self.battle_channel.titles)
        self.assertEqual(self.info_channel.titles, [])

    def test_the_channel_is_renamed_from_the_old_name(self) -> None:
        self.install()

        self.assertEqual(
            self.battle_channel.name,
            battle_views.game_shared.CHANNEL_LABELS["battle_member"],
        )
        self.assertEqual(self.battle_channel.name, "使い魔バトル")

    def test_nothing_is_posted_to_the_guild_text_channel(self) -> None:
        self.install()
        self.assertEqual(self.guild_text.titles, [])

    def test_calling_twice_does_not_duplicate_panels(self) -> None:
        self.install()
        self.install()

        self.assertEqual(len(self.battle_channel.messages), 1)
        self.assertTrue(all(m.edits == 0 for m in self.battle_channel.messages))


class GuildInfoChannelPanelTests(unittest.TestCase):
    """ギルド情報チャンネルへメンバー用パネルを置く（8.3節）。"""

    def setUp(self) -> None:
        guild_service._legacy_member_panels_checked.clear()
        self.bot = FakeBot()
        self.guild_text = FakeChannel(200, "ギルドtc")
        self.battle_channel = FakeChannel(300, "使い魔バトル")
        self.info_channel = FakeChannel(400, "ギルド情報")
        self.discord_guild = FakeGuild(
            {200: self.guild_text, 300: self.battle_channel, 400: self.info_channel}
        )
        self.guild_row = {
            "guild_id": 1,
            "name": "黒鉄の団",
            "guild_text_channel_id": 200,
            "battle_member_channel_id": 300,
            "info_channel_id": 400,
        }

    def install(self) -> str:
        return run(
            guild_service.ensure_member_panel(
                self.bot, self.discord_guild, self.guild_row
            )
        )

    def test_the_panel_goes_to_the_info_channel(self) -> None:
        self.install()

        self.assertEqual(self.info_channel.titles, ["黒鉄の団"])
        self.assertEqual(self.battle_channel.titles, [])

    def test_calling_twice_does_not_duplicate_panels(self) -> None:
        self.install()
        self.install()

        self.assertEqual(len(self.info_channel.messages), 1)

    def test_a_renamed_guild_updates_the_panel_in_place(self) -> None:
        self.install()
        panel = self.info_channel.messages[0]

        self.guild_row["name"] = "白銀の盾"
        status = self.install()

        self.assertEqual(status, utils.PANEL_UPDATED)
        self.assertEqual(len(self.info_channel.messages), 1)
        self.assertIs(self.info_channel.messages[0], panel)
        self.assertEqual(self.info_channel.titles, ["白銀の盾"])

    def test_a_deleted_panel_is_restored(self) -> None:
        self.install()
        run(self.info_channel.messages[0].delete())

        self.install()

        self.assertEqual(self.info_channel.titles, ["黒鉄の団"])

    def test_a_missing_info_channel_is_reported(self) -> None:
        del self.guild_row["info_channel_id"]

        self.assertEqual(self.install(), utils.PANEL_MISSING)


class MemberPanelMigrationTests(unittest.TestCase):
    """旧配置（ギルドTC・使い魔バトル）からギルド情報チャンネルへの移行（8.3節）。"""

    def setUp(self) -> None:
        guild_service._legacy_member_panels_checked.clear()
        self.bot = FakeBot()
        self.guild_text = FakeChannel(200, "ギルドtc")
        self.battle_channel = FakeChannel(300, "バトル出場者専用tc")
        self.info_channel = FakeChannel(400, "ギルド情報")
        self.discord_guild = FakeGuild(
            {200: self.guild_text, 300: self.battle_channel, 400: self.info_channel}
        )
        self.guild_row = {
            "guild_id": 1,
            "name": "紅蓮の牙",
            "guild_text_channel_id": 200,
            "battle_member_channel_id": 300,
            "info_channel_id": 400,
        }

        # 旧状態を再現する：ギルドTCと使い魔バトルの両方にメンバー用パネルが残っている
        run(
            self.guild_text.send(
                embed=discord.Embed(
                    title=guild_service.LEGACY_MEMBER_PANEL_TITLES[0]
                ),
                view=guild_views.GuildMemberPanelView(),
            )
        )
        run(self.guild_text.send(embed=discord.Embed(title="お知らせ"), view=None))

        # 直前の版は「表題＝ギルド名」で使い魔バトルchへ置いていた
        self.old_member_panel = run(
            self.battle_channel.send(
                embed=guild_service.build_member_panel_embed(self.guild_row),
                view=guild_views.GuildMemberPanelView(),
            )
        )

        run(
            battle_views.ensure_roster_panel(
                self.bot, self.discord_guild, self.guild_row
            )
        )
        run(
            guild_service.ensure_member_panel(
                self.bot, self.discord_guild, self.guild_row
            )
        )

    def test_the_old_member_panel_is_removed_from_the_guild_text_channel(self) -> None:
        self.assertNotIn(
            guild_service.LEGACY_MEMBER_PANEL_TITLES[0], self.guild_text.titles
        )

    def test_the_old_member_panel_is_removed_from_the_battle_channel(self) -> None:
        self.assertNotIn(self.old_member_panel, self.battle_channel.messages)
        self.assertEqual(
            self.battle_channel.titles, [battle_views.ROSTER_PANEL_TITLE]
        )

    def test_the_panel_now_lives_in_the_info_channel(self) -> None:
        self.assertEqual(self.info_channel.titles, ["紅蓮の牙"])

    def test_other_messages_are_left_alone(self) -> None:
        self.assertEqual(self.guild_text.titles, ["お知らせ"])

    def test_the_cleanup_only_reads_history_once_per_boot(self) -> None:
        # 定期タスクから何度も呼ばれても、旧置き場を読み直さない
        run(self.guild_text.send(embed=discord.Embed(title="ギルドメンバー"),
                                 view=guild_views.GuildMemberPanelView()))
        run(
            guild_service.ensure_member_panel(
                self.bot, self.discord_guild, self.guild_row
            )
        )

        self.assertIn("ギルドメンバー", self.guild_text.titles)

    def test_the_channel_is_renamed(self) -> None:
        self.assertEqual(
            self.battle_channel.name,
            battle_views.game_shared.CHANNEL_LABELS["battle_member"],
        )


class TopPanelTests(unittest.TestCase):
    """ATMパネルはcoinを使うチャンネルの一番上へ置く（34.33.1節）。"""

    def setUp(self) -> None:
        self.bot = FakeBot()
        self.channel = FakeChannel(500, "ガチャ")
        self.discord_guild = FakeGuild({500: self.channel})

    def install(self, movable_titles: tuple[str, ...] = ("ガチャ",)) -> str:
        return run(
            coin_views.ensure_atm_panel(
                self.bot, self.discord_guild, 500, movable_titles=movable_titles
            )
        )

    def other_panel(self, title: str):
        return run(
            self.channel.send(
                embed=discord.Embed(title=title),
                view=guild_views.GuildMemberPanelView(),
            )
        )

    def test_the_panel_is_placed_in_an_empty_channel(self) -> None:
        self.install()

        self.assertEqual(self.channel.titles, [coin_views.ATM_PANEL_TITLE])

    def test_panels_above_are_removed_so_the_atm_comes_first(self) -> None:
        # 先に別のパネルが置かれている状態
        above = self.other_panel("ガチャ")

        self.install()

        self.assertNotIn(above, self.channel.messages)
        self.assertEqual(self.channel.titles, [coin_views.ATM_PANEL_TITLE])

    def test_panels_below_are_left_alone(self) -> None:
        self.install()
        below = self.other_panel("ガチャ")

        self.install()

        self.assertIn(below, self.channel.messages)
        self.assertEqual(
            self.channel.titles, [coin_views.ATM_PANEL_TITLE, "ガチャ"]
        )

    def test_calling_twice_does_not_duplicate_or_churn(self) -> None:
        self.install()
        self.install()

        self.assertEqual(len(self.channel.messages), 1)
        self.assertEqual(self.channel.messages[0].edits, 0)

    def test_member_posts_are_left_alone(self) -> None:
        # 操作部品を持たない投稿（利用者の発言）は消さない
        chatter = run(
            self.channel.send(embed=discord.Embed(title="雑談"), view=None)
        )

        self.install()

        self.assertIn(chatter, self.channel.messages)

    def test_panels_that_cannot_be_reposted_are_never_deleted(self) -> None:
        # 参加申請Embedのように貼り直せない投稿は、上にあっても消さない
        request = self.other_panel("ギルド参加申請")

        self.install(movable_titles=("ギルド管理",))

        self.assertIn(request, self.channel.messages)
        self.assertEqual(
            self.channel.titles, ["ギルド参加申請", coin_views.ATM_PANEL_TITLE]
        )

    def test_without_movable_titles_nothing_is_deleted(self) -> None:
        above = self.other_panel("ガチャ")

        self.install(movable_titles=())

        self.assertIn(above, self.channel.messages)

    def test_nothing_is_deleted_when_the_atm_is_out_of_the_search_window(self) -> None:
        # 履歴の外に自分のパネルがあるかもしれない状況では並べ替えない。
        # 消して貼り直すと、それを毎回繰り返してしまうため。
        self.install()
        for index in range(120):
            run(
                self.channel.send(
                    embed=discord.Embed(title=f"雑談{index}"), view=None
                )
            )
        gacha = self.other_panel("ガチャ")

        self.install()

        self.assertIn(gacha, self.channel.messages)
        self.assertEqual(gacha.edits, 0)


class MasterChannelPanelOrderTests(unittest.TestCase):
    """マスター専用TCで動かしてよいパネルの表題（34.33.1節）。"""

    def test_the_movable_titles_match_the_real_panels(self) -> None:
        from cogs.guild_battle import views as battle_panel_views

        self.assertEqual(guild_service.MANAGE_PANEL_TITLE, "ギルド管理")
        self.assertEqual(battle_panel_views.BATTLE_PANEL_TITLE, "ギルドバトル")


class PanelBodyComparisonTests(unittest.TestCase):
    """説明文の比較は前後の空白を無視する（Discordが落とすため）。"""

    def setUp(self) -> None:
        self.bot = FakeBot()
        self.channel = TrimmingChannel(600, "マスター専用tc")
        self.discord_guild = FakeGuild({600: self.channel})

    def install(self) -> str:
        return run(
            utils.ensure_panel_message(
                self.bot,
                self.discord_guild,
                600,
                panel_title=battle_views.ROSTER_PANEL_TITLE,
                embed=battle_views.roster_panel_embed(),
                view=battle_views.BattleMemberPanelView(),
                panel_name="使い魔セットパネル",
            )
        )

    def test_a_trailing_newline_does_not_cause_endless_edits(self) -> None:
        # 実際に末尾が改行のパネルであることを確かめてから比較する
        description = battle_views.roster_panel_embed().description
        self.assertNotEqual(description, description.rstrip())

        self.assertEqual(self.install(), utils.PANEL_CREATED)
        for _ in range(3):
            self.assertEqual(self.install(), utils.PANEL_FOUND)

        self.assertEqual(self.channel.messages[0].edits, 0)


class AtmPanelChannelTests(unittest.TestCase):
    """ATMパネルを置くチャンネルと、そこで動かしてよいパネル（34.33.1節）。"""

    def test_every_channel_lists_the_panels_it_may_move(self) -> None:
        # 空だと並べ替えができず、広すぎると貼り直せない投稿を消してしまう
        for channel_id, titles in config.ATM_PANEL_CHANNELS.items():
            self.assertTrue(channel_id, channel_id)
            self.assertTrue(titles, channel_id)

    def test_the_movable_titles_match_the_real_panels(self) -> None:
        installed = self.panel_titles_in_cogs()

        for titles in config.ATM_PANEL_CHANNELS.values():
            for title in titles:
                self.assertIn(title, installed, title)

    @staticmethod
    def panel_titles_in_cogs() -> set[str]:
        """各Cogが ``panel_title=`` で設置している表題を集める。"""

        found: set[str] = set()
        for path in (ROOT / "cogs").rglob("*.py"):
            for match in re.finditer(
                r'panel_title="([^"]+)"', path.read_text(encoding="utf-8")
            ):
                found.add(match.group(1))

        return found


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
