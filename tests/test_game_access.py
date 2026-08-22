"""ラグナオンラインの利用資格ゲートのテスト（GAME_SPEC 4節・34.1.1節）。

利用できるのは本メンバー（騎士）・七聖・運営だけです。ロール判定そのものと、
「ゲームの入口が共通のゲートを通っているか」の2点を確認します。
"""

from __future__ import annotations

import os
import re
import unittest

from pathlib import Path


# cogs 経由で config.py を読み込むため、先に必須の環境変数を用意する。
os.environ.setdefault("DISCORD_BOT_TOKEN", "test-token")
os.environ.setdefault("DISCORD_GUILD_ID", "1")

import config  # noqa: E402

from cogs import game_shared  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]

# ゲームのDiscord UI層。ここでは公開スイッチを直接見ない（共通ゲートを使う）。
# ギルドバトルは責務ごとに分かれているため、全モジュールを対象にする。
VIEW_MODULES = (
    "cogs/familiar/views.py",
    "cogs/guild/views.py",
    "cogs/guild_battle/views.py",
    "cogs/guild_battle/battle_common.py",
    "cogs/guild_battle/familiar_options.py",
    "cogs/guild_battle/entry_views.py",
    "cogs/guild_battle/register_views.py",
    "cogs/guild_battle/battle_action_views.py",
    "cogs/guild_battle/matchmaking_views.py",
    "cogs/guild_battle/battle_embeds.py",
)

# そのうち、利用資格を自分で確かめる入口を持つモジュール。
#
# 残りは次のどちらかなので、ゲートを自分では呼びません。
#
# - ``battle_common`` ``battle_action_views``：進行中バトルの行動。``load_actor``
#   が「いま手番か」だけを見ます（下の ``allowed`` を参照）。
# - それ以外：上のモジュールがゲートを通した後に開く一時View・整形・Embedのみ。
GATE_MODULES = (
    "cogs/familiar/views.py",
    "cogs/guild/views.py",
    "cogs/guild_battle/views.py",
    "cogs/guild_battle/matchmaking_views.py",
)


class FakeRole:
    def __init__(self, role_id: int) -> None:
        self.id = role_id


class FakeGuild:
    def __init__(self, role_ids: tuple[int, ...] = ()) -> None:
        self.roles = [FakeRole(role_id) for role_id in role_ids]


class FakeMember:
    """``discord.Member`` の代役。ロール判定だけに使う。"""

    def __init__(self, *role_ids: int, guild: FakeGuild | None = None) -> None:
        self.id = 1
        self.roles = [FakeRole(role_id) for role_id in role_ids]
        # 既定では「サーバーのロールを受信済み」の状態にする
        self.guild = FakeGuild((0,)) if guild is None else guild


class MemberRoleTests(unittest.TestCase):
    """4節：本メンバー（騎士）・七聖・運営だけが利用できる。"""

    def test_full_members_and_above_can_play(self) -> None:
        for role_id in (
            config.ROLE_MEMBER,
            config.ROLE_SUB_MANAGER,
            config.ROLE_MANAGER,
        ):
            self.assertTrue(
                game_shared.is_game_member(FakeMember(role_id)), role_id
            )

    def test_everyone_else_cannot_play(self) -> None:
        for role_id in (
            config.ROLE_TRIAL_MEMBER,
            config.ROLE_ASSOCIATE_MEMBER,
            config.ROLE_DEMOTED,
            config.ROLE_APPLICANT,
            config.ROLE_INTERVIEWING,
            config.ROLE_ENROLLMENT_WAITING,
        ):
            self.assertFalse(
                game_shared.is_game_member(FakeMember(role_id)), role_id
            )

    def test_a_class_role_alone_is_not_enough(self) -> None:
        # クラス（S/A/B/C）とメンバー種別は別の軸。クラスだけでは遊べない
        self.assertFalse(
            game_shared.is_game_member(FakeMember(config.ROLE_CLASS_S))
        )
        self.assertTrue(
            game_shared.is_game_member(
                FakeMember(config.ROLE_CLASS_S, config.ROLE_MEMBER)
            )
        )

    def test_no_roles_at_all_cannot_play(self) -> None:
        self.assertFalse(game_shared.is_game_member(FakeMember()))


class RoleCacheTests(unittest.TestCase):
    """ロールをまだ受信していない状態を「ロール無し」と取り違えない。

    再接続直後のボタン操作では、そのサーバーのロールが届く前に判定が走る
    ことがあります。そこで保存済みのロール情報を上書きしてしまうと、本メンバー
    だったプレイヤーが以後ずっとバトルへ出られなくなります。
    """

    def test_roles_are_readable_only_when_the_guild_has_roles(self) -> None:
        self.assertTrue(
            game_shared._roles_are_readable(FakeMember(config.ROLE_MEMBER))
        )

    def test_an_empty_role_list_on_the_guild_is_treated_as_not_received(self) -> None:
        # サーバーには必ず @everyone があるため、空なら未受信
        member = FakeMember(config.ROLE_MEMBER, guild=FakeGuild(()))
        self.assertFalse(game_shared._roles_are_readable(member))

    def test_a_member_without_a_guild_is_treated_as_not_received(self) -> None:
        member = FakeMember(config.ROLE_MEMBER)
        member.guild = None
        self.assertFalse(game_shared._roles_are_readable(member))


class GateUsageTests(unittest.TestCase):
    """34.1.1節：入口は共通ゲートを通す。"""

    def test_views_do_not_check_the_switch_on_its_own(self) -> None:
        """``is_game_enabled`` の直接呼び出しを入口へ残さない。

        公開スイッチだけを見ると、資格の確認が抜けたボタンができてしまいます。
        例外は2つだけで、どちらも「止めると詰む」ため意図的に資格を見ません。

        - ``load_actor``：進行中バトルの行動。止めると相手ギルドまで進めない。
        - ``_game_block_reason``：脱退・譲渡・解散の入口。止めるとギルドが
          誰にも動かせなくなる。
        """

        allowed = {
            ("cogs/guild_battle/battle_common.py", "load_actor"),
            ("cogs/guild/views.py", "_game_block_reason"),
        }

        for relative_path in VIEW_MODULES:
            source = (ROOT / relative_path).read_text(encoding="utf-8")

            for match in re.finditer(r"is_game_enabled\(\)", source):
                owner = self._enclosing_function(source, match.start())
                self.assertIn(
                    (relative_path, owner),
                    allowed,
                    f"{relative_path} の {owner} は game_block_reason を使ってください",
                )

    def test_every_view_module_uses_the_shared_gate(self) -> None:
        for relative_path in GATE_MODULES:
            source = (ROOT / relative_path).read_text(encoding="utf-8")
            self.assertIn("game_block_reason", source, relative_path)

    def test_every_view_module_is_a_real_file(self) -> None:
        # 分割で名前が変わったモジュールを検査対象から取りこぼさない
        for relative_path in VIEW_MODULES + GATE_MODULES:
            self.assertTrue((ROOT / relative_path).is_file(), relative_path)

    @staticmethod
    def _enclosing_function(source: str, position: int) -> str:
        """``position`` を含む一番近い ``def`` の名前を返す。"""

        found = "<module>"
        for match in re.finditer(r"^\s*(?:async )?def (\w+)", source[:position], re.M):
            found = match.group(1)

        return found


if __name__ == "__main__":
    unittest.main()
