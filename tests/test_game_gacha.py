"""ガチャの排出率と抽選が仕様どおりかを確認するテスト（GAME_SPEC 10.2節・34.2節）。"""

from __future__ import annotations

import os
import random
import unittest
from collections import Counter


# cogs.game_shared 経由で config.py を読み込むため、先に必須の環境変数を用意する。
os.environ.setdefault("DISCORD_BOT_TOKEN", "test-token")
os.environ.setdefault("DISCORD_GUILD_ID", "1")

from cogs.familiar import service  # noqa: E402
from game.master_data import load_master_data  # noqa: E402


MASTER = load_master_data()
POOL = MASTER.gacha_pools["standard"]


class RankTableTests(unittest.TestCase):
    def test_tables_always_total_one_thousand(self) -> None:
        for slot_type in ("normal", "guaranteed"):
            table = service.build_rank_table(POOL, slot_type)
            self.assertEqual(sum(weight for _, weight in table), 1000, slot_type)

    def test_unregistered_ranks_are_excluded(self) -> None:
        # Cランクは未登録のため抽選対象に出さない（34.2節）
        table = service.build_rank_table(POOL, "normal")
        self.assertNotIn("C", [rank for rank, _ in table])

    def test_missing_rank_share_goes_to_the_fallback_rank(self) -> None:
        # 34.2節：未登録ランクの排出率は missing_rank_fallback へまとめて寄せる。
        # Cランク45%はBランクへ加算され、A・Sの確率は本文どおり据え置き。
        self.assertEqual(POOL.missing_rank_fallback, "B")

        table = dict(service.build_rank_table(POOL, "normal"))
        original = POOL.rates["normal"]

        self.assertEqual(table["B"], original["B"] + original["C"])
        self.assertEqual(table["A"], original["A"])
        self.assertEqual(table["S"], original["S"])

    def test_notice_explains_where_the_share_moved(self) -> None:
        notice = service.rank_table_notice(POOL)

        self.assertIsNotNone(notice)
        self.assertIn("C", notice)
        self.assertIn("B", notice)

    def test_guaranteed_slot_is_unaffected_by_redistribution(self) -> None:
        # 保証枠にはCランクが含まれないため、本文どおりの確率がそのまま使われる
        self.assertEqual(
            dict(service.build_rank_table(POOL, "guaranteed")),
            {"B": 900, "A": 90, "S": 10},
        )

    def test_every_registered_rank_has_familiars(self) -> None:
        for rank, _ in service.build_rank_table(POOL, "normal"):
            self.assertTrue(MASTER.familiars_by_rank(rank), rank)


class DrawTests(unittest.TestCase):
    def setUp(self) -> None:
        service.set_random(random.Random(20260815))

    def test_single_draw_returns_one_result(self) -> None:
        results = service.draw_results(POOL, 1)

        self.assertEqual(len(results), 1)
        rank, familiar_id = results[0]
        self.assertEqual(MASTER.get_familiar(familiar_id).rank, rank)

    def test_multi_draw_returns_the_configured_count(self) -> None:
        results = service.draw_results(POOL, POOL.multi_count)
        self.assertEqual(len(results), POOL.multi_count)

    def test_the_guaranteed_slot_is_never_below_b(self) -> None:
        # 10.2節「10回実行の10枠目はBランク以上を保証」
        allowed = {"B", "A", "S"}

        for _ in range(200):
            results = service.draw_results(POOL, POOL.multi_count)
            rank, _ = results[POOL.guaranteed_slot - 1]
            self.assertIn(rank, allowed)

    def test_results_only_contain_registered_familiars(self) -> None:
        for _ in range(50):
            for rank, familiar_id in service.draw_results(POOL, POOL.multi_count):
                familiar = MASTER.get_familiar(familiar_id)
                self.assertIsNotNone(familiar, familiar_id)
                self.assertEqual(familiar.rank, rank)

    def test_familiars_of_the_same_rank_appear_evenly(self) -> None:
        # 10.6節「同じランク内の使い魔は、ガチャで同じ確率になるよう均等に配分」
        service.set_random(random.Random(7))
        counts: Counter[str] = Counter()

        for _ in range(3000):
            rank, familiar_id = service.draw_results(POOL, 1)[0]
            if rank == "B":
                counts[familiar_id] += 1

        b_rank = MASTER.familiars_by_rank("B")
        self.assertEqual(len(counts), len(b_rank))

        expected = sum(counts.values()) / len(b_rank)
        for familiar_id, count in counts.items():
            # 均等配分なので、期待値から大きく外れないこと
            self.assertLess(abs(count - expected), expected * 0.6, familiar_id)


class CostTests(unittest.TestCase):
    def test_ten_draws_cost_ten_times_a_single_draw(self) -> None:
        # 10.2節「10回実行に割引はありません」
        self.assertEqual(POOL.single_cost * POOL.multi_count, POOL.multi_cost)


if __name__ == "__main__":
    unittest.main()
