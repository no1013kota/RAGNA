"""ガチャの排出率と抽選が仕様どおりかを確認するテスト（GAME_SPEC 10.2節・34.2節）。"""

from __future__ import annotations

import os
import random
import unittest
from collections import Counter


# cogs.game_shared 経由で config.py を読み込むため、先に必須の環境変数を用意する。
os.environ.setdefault("DISCORD_BOT_TOKEN", "test-token")
os.environ.setdefault("DISCORD_GUILD_ID", "1")

from cogs import game_shared  # noqa: E402
from cogs.familiar import service  # noqa: E402
from game.master_data import load_master_data  # noqa: E402


MASTER = load_master_data()
POOL = MASTER.gacha_pools["standard"]


class RankTableTests(unittest.TestCase):
    def test_tables_always_total_one_thousand(self) -> None:
        for slot_type in ("normal", "guaranteed"):
            table = service.build_rank_table(POOL, slot_type)
            self.assertEqual(sum(weight for _, weight in table), 1000, slot_type)

    def test_every_rank_is_drawable(self) -> None:
        # 全ランクが登録済みなので、そのまま4ランクとも抽選対象になる
        table = dict(service.build_rank_table(POOL, "normal"))
        self.assertEqual(set(table), {"C", "B", "A", "S"})

    def test_rates_match_the_spec_without_adjustment(self) -> None:
        # 10.2節：C45% B45% A9% S1%
        self.assertEqual(
            dict(service.build_rank_table(POOL, "normal")), POOL.rates["normal"]
        )

    def test_no_notice_is_shown_when_nothing_was_adjusted(self) -> None:
        self.assertIsNone(service.rank_table_notice(POOL))

    def test_the_fallback_rank_is_still_configured(self) -> None:
        # ランクを増やしたときに備えて寄せ先の設定は残しておく（34.2節）
        self.assertEqual(POOL.missing_rank_fallback, "B")

    def test_guaranteed_slot_is_unaffected_by_redistribution(self) -> None:
        # 保証枠にはCランクが含まれないため、本文どおりの確率がそのまま使われる
        self.assertEqual(
            dict(service.build_rank_table(POOL, "guaranteed")),
            {"A": 900, "S": 100},
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

    def test_the_guaranteed_slot_is_never_below_a(self) -> None:
        # 10.2節「10回実行の10枠目はAランク以上を保証」
        allowed = {"A", "S"}

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


class RateListEmbedTests(unittest.TestCase):
    """ガチャパネルの「排出使い魔の確認」が出す一覧（10.2節・10.6節）。"""

    def setUp(self) -> None:
        self.embed, self.has_familiars = service.build_rate_list_embed(POOL)
        self.body = self.embed.description or ""

    def test_every_drawable_familiar_is_listed(self) -> None:
        self.assertTrue(self.has_familiars)

        for rank, _ in service.build_rank_table(POOL, "normal"):
            for familiar in MASTER.gacha_familiars_by_rank(rank):
                self.assertIn(familiar.name, self.body, familiar.familiar_id)

    def test_complete_reward_familiars_are_not_listed(self) -> None:
        # ヘルはコンプリート報酬なのでガチャ一覧へ出さない
        for familiar in MASTER.complete_reward_familiars():
            self.assertNotIn(familiar.name, self.body, familiar.familiar_id)

    def test_every_rank_is_listed(self) -> None:
        for rank in ("S", "A", "B", "C"):
            self.assertIn(f"【{rank}】", self.body)

    def test_rank_rate_and_per_familiar_rate_are_shown(self) -> None:
        # 例：「【B】45.0%（20体・各2.25%）」
        self.assertIn("【B】45.0%（20体・各2.25%）", self.body)

    def test_rank_labels_have_no_emoji(self) -> None:
        # 排出率の表示は絵文字を使わない
        for rank in ("S", "A", "B"):
            self.assertIn(f"【{rank}】", self.body)

        for emoji in game_shared.RANK_EMOJIS.values():
            self.assertNotIn(emoji, self.body)

    def test_the_guaranteed_slot_is_explained(self) -> None:
        self.assertIn(f"【{POOL.multi_count}連{POOL.guaranteed_slot}枠目】", self.body)

    def test_the_embed_uses_no_fields(self) -> None:
        # すべての項目は「【項目】結果」で本文へ並べる
        self.assertEqual(self.embed.fields, [])

    def test_embed_stays_within_discord_limits(self) -> None:
        self.assertLessEqual(len(self.body), 4096)
        self.assertLessEqual(
            len(self.embed.title or "") + len(self.body), 6000
        )

    def test_per_familiar_rate_formatting(self) -> None:
        self.assertEqual(service.format_each_rate(900, 20), "4.50%")
        self.assertEqual(service.format_each_rate(10, 5), "0.20%")
        self.assertEqual(service.format_each_rate(10, 0), "—")


class CostTests(unittest.TestCase):
    def test_ten_draws_cost_nine_single_draws(self) -> None:
        # 10.2節：単発30,000 coin、10連270,000 coin（1回分だけ得になる）
        self.assertEqual(POOL.single_cost, 30_000)
        self.assertEqual(POOL.multi_cost, 270_000)
        self.assertEqual(POOL.single_cost * (POOL.multi_count - 1), POOL.multi_cost)


if __name__ == "__main__":
    unittest.main()
