"""脱退・解散の制限が仕様どおりかを確認するテスト（GAME_SPEC 7.1節・7.5節）。

``cogs/guild/service.py`` の判定関数を、実際のDBの状態に対して呼び出します。
"""

from __future__ import annotations

import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path


# config.py がBot Tokenを必須にするため、import前に用意する。
os.environ.setdefault("DISCORD_BOT_TOKEN", "test-token")
os.environ.setdefault("DISCORD_GUILD_ID", "1")


def _reloaded_module_names() -> list[str]:
    """一時DB用に読み込み直す対象のモジュール名を返す。"""

    return [
        name
        for name in list(sys.modules)
        if name == "database"
        or name.startswith("database.")
        or name.startswith("cogs.guild")
        or name == "cogs.game_shared"
    ]


def reload_with_database(database_path: Path) -> tuple[dict, dict]:
    """一時DBを参照した状態で、DB層とギルドCogを読み込み直す。

    ``database.core`` はimport時に保存先を決めるため、他のテストの影響を受けない
    よう関連モジュールをすべて読み込み直します。差し替え前のモジュールも返すので、
    テストの後で元へ戻せます（他のテストが持っている参照が古くならないように）。
    """

    os.environ["DATABASE_PATH"] = str(database_path)

    saved = {name: sys.modules.pop(name) for name in _reloaded_module_names()}

    connection = importlib.import_module("database.connection")
    connection.init_database()

    modules = {
        "connection": connection,
        "guild_db": importlib.import_module("database.guild"),
        "battle_db": importlib.import_module("database.battle"),
        "service": importlib.import_module("cogs.guild.service"),
    }

    return modules, saved


def restore_modules(saved: dict) -> None:
    """``reload_with_database`` で差し替えたモジュールを元へ戻す。"""

    for name in _reloaded_module_names():
        sys.modules.pop(name, None)

    sys.modules.update(saved)


class LeaveAndDisbandRuleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        modules, saved = reload_with_database(
            Path(self.temporary_directory.name) / "test.db"
        )
        self.addCleanup(restore_modules, saved)

        self.connection_module = modules["connection"]
        self.guild_db = modules["guild_db"]
        self.battle_db = modules["battle_db"]
        self.service = modules["service"]

        self.guild_id = self.create_guild("ALPHA", 100)
        self.other_guild_id = self.create_guild("BETA", 200)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()
        os.environ.pop("DATABASE_PATH", None)

    def create_guild(self, name: str, master_id: int) -> int:
        from contextlib import closing

        with closing(self.connection_module.get_connection()) as connection:
            with connection:
                connection.execute(
                    "INSERT INTO balances (user_id, balance) VALUES (?, ?)",
                    (master_id, 1_000_000),
                )

        result = self.guild_db.create_guild(
            name=name, master_id=master_id, capacity=5, cost=1_000_000
        )
        self.assertTrue(result["ok"], result)
        return result["guild_id"]

    def guild_row(self, guild_id: int | None = None) -> dict:
        return self.guild_db.get_guild(guild_id or self.guild_id)

    # ==================================================
    # 通常時
    # ==================================================
    def test_leaving_is_allowed_when_nothing_is_in_progress(self) -> None:
        self.assertIsNone(
            self.service.can_leave_guild(self.guild_id, self.guild_row())
        )
        self.assertIsNone(
            self.service.can_disband_or_leave(self.guild_id, self.guild_row())
        )

    # ==================================================
    # 7.1節：脱退できない状態
    # ==================================================
    def test_a_pending_battle_request_blocks_leaving(self) -> None:
        self.battle_db.create_battle_request(self.guild_id, self.other_guild_id)

        reason = self.service.can_leave_guild(self.guild_id, self.guild_row())
        self.assertIsNotNone(reason)
        self.assertIn("申請", reason)

        # 申請を受けた側も脱退できない
        self.assertIsNotNone(
            self.service.can_leave_guild(
                self.other_guild_id, self.guild_row(self.other_guild_id)
            )
        )

    def test_an_open_recruitment_blocks_leaving(self) -> None:
        self.battle_db.create_battle_recruitment(self.guild_id)

        reason = self.service.can_leave_guild(self.guild_id, self.guild_row())
        self.assertIsNotNone(reason)
        self.assertIn("募集", reason)

    def test_a_locked_roster_blocks_leaving(self) -> None:
        self.guild_db.set_roster_locked(self.guild_id, True)

        self.assertIsNotNone(
            self.service.can_leave_guild(self.guild_id, self.guild_row())
        )

    def test_cancelling_the_request_allows_leaving_again(self) -> None:
        created = self.battle_db.create_battle_request(
            self.guild_id, self.other_guild_id
        )
        self.battle_db.resolve_battle_request(created["request_id"], "cancelled")

        self.assertIsNone(
            self.service.can_leave_guild(self.guild_id, self.guild_row())
        )

    # ==================================================
    # 7.5節：解散はバトル進行中だけを拒否する
    # ==================================================
    def test_a_pending_request_does_not_block_disbanding(self) -> None:
        # 7.5節では未処理のバトル申請・募集は解散時に終了させるため、
        # 解散そのものは拒否しない。
        self.battle_db.create_battle_request(self.guild_id, self.other_guild_id)

        self.assertIsNone(
            self.service.can_disband_or_leave(self.guild_id, self.guild_row())
        )

    def test_a_locked_roster_blocks_disbanding(self) -> None:
        self.guild_db.set_roster_locked(self.guild_id, True)

        self.assertIsNotNone(
            self.service.can_disband_or_leave(self.guild_id, self.guild_row())
        )

    # ==================================================
    # 9節：編成ロック中は出場者セットを変更できない
    # ==================================================
    def test_a_locked_roster_blocks_kicking(self) -> None:
        # 追放も出場者セットの変更になるため、脱退と同じく拒否する
        self.assertIsNone(
            self.service.can_kick_member(self.guild_id, self.guild_row())
        )

        self.guild_db.set_roster_locked(self.guild_id, True)

        self.assertIsNotNone(
            self.service.can_kick_member(self.guild_id, self.guild_row())
        )


if __name__ == "__main__":
    unittest.main()
