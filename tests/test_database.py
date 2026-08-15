"""coin送金が途中状態を残さないことを確認するテスト。"""

from __future__ import annotations

import importlib
import os
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_database_module(database_path: Path):
    """本番DBに触れないよう、一時DBを参照するdatabaseパッケージを読み込む。"""

    previous_path = os.environ.get("DATABASE_PATH")
    os.environ["DATABASE_PATH"] = str(database_path)
    try:
        # DB_PATHはimport時に確定するため、テストごとに関連モジュールを読み直す。
        for module_name in list(sys.modules):
            if module_name == "database" or module_name.startswith("database."):
                sys.modules.pop(module_name)
        return importlib.import_module("database")
    finally:
        if previous_path is None:
            os.environ.pop("DATABASE_PATH", None)
        else:
            os.environ["DATABASE_PATH"] = previous_path


class TransferBalanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        database_path = Path(self.temporary_directory.name) / "test.db"
        self.database = load_database_module(database_path)
        self.database.init_database()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_transfer_updates_both_balances_and_history(self) -> None:
        self.database.add_balance(100, 5_000)

        succeeded = self.database.transfer_balance(100, 200, 1_500, "テスト送金")

        self.assertTrue(succeeded)
        self.assertEqual(self.database.get_balance(100), 3_500)
        self.assertEqual(self.database.get_balance(200), 1_500)

        with closing(self.database.get_connection()) as connection:
            row = connection.execute(
                "SELECT type, executor_id, target_id, amount, note FROM transactions"
            ).fetchone()
        self.assertEqual(row, ("送金", 100, 200, 1_500, "テスト送金"))

    def test_insufficient_balance_changes_nothing(self) -> None:
        self.database.add_balance(100, 500)

        succeeded = self.database.transfer_balance(100, 200, 1_000)

        self.assertFalse(succeeded)
        self.assertEqual(self.database.get_balance(100), 500)
        self.assertEqual(self.database.get_balance(200), 0)
        with closing(self.database.get_connection()) as connection:
            transaction_count = connection.execute(
                "SELECT COUNT(*) FROM transactions"
            ).fetchone()[0]
        self.assertEqual(transaction_count, 0)


class MonthlyRewardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        database_path = Path(self.temporary_directory.name) / "test.db"
        self.database = load_database_module(database_path)
        self.database.init_database()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_same_month_user_and_role_is_granted_only_once(self) -> None:
        first = self.database.grant_monthly_reward(
            "2026-09", 100, 200, 50_000, "騎士"
        )
        second = self.database.grant_monthly_reward(
            "2026-09", 100, 200, 50_000, "騎士"
        )

        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(self.database.get_balance(100), 50_000)

        with closing(self.database.get_connection()) as connection:
            transaction_count = connection.execute(
                "SELECT COUNT(*) FROM transactions WHERE type = '月次支給'"
            ).fetchone()[0]
        self.assertEqual(transaction_count, 1)

    def test_different_roles_can_each_grant_a_reward(self) -> None:
        self.database.grant_monthly_reward("2026-09", 100, 200, 50_000, "騎士")
        self.database.grant_monthly_reward("2026-09", 100, 300, 10_000, "大天使")

        self.assertEqual(self.database.get_balance(100), 60_000)


if __name__ == "__main__":
    unittest.main()
