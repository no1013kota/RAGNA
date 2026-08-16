"""ギルド・使い魔・バトルのDB処理が途中状態を残さないことを確認するテスト。

GAME_SPEC 27節の必須制約（1プレイヤー1ギルド、同一トランザクション、
二重反映の防止）を中心に検証します。
"""

from __future__ import annotations

import importlib
import os
import sys
import tempfile
import unittest

from contextlib import closing
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_game_database(database_path: Path):
    """本番DBに触れないよう、一時DBを参照するdatabaseパッケージを読み込む。"""

    previous_path = os.environ.get("DATABASE_PATH")
    os.environ["DATABASE_PATH"] = str(database_path)
    try:
        for module_name in list(sys.modules):
            if module_name == "database" or module_name.startswith("database."):
                sys.modules.pop(module_name)

        return {
            "connection": importlib.import_module("database.connection"),
            "guild": importlib.import_module("database.guild"),
            "familiar": importlib.import_module("database.familiar"),
            "battle": importlib.import_module("database.battle"),
            "player_rank": importlib.import_module("database.player_rank"),
        }
    finally:
        if previous_path is None:
            os.environ.pop("DATABASE_PATH", None)
        else:
            os.environ["DATABASE_PATH"] = previous_path


class GameDatabaseTestCase(unittest.TestCase):
    """一時DBを用意し、マスターデータを同期した状態から始める共通の土台。"""

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        database_path = Path(self.temporary_directory.name) / "test.db"

        modules = load_game_database(database_path)
        self.connection_module = modules["connection"]
        self.guild_db = modules["guild"]
        self.familiar_db = modules["familiar"]
        self.battle_db = modules["battle"]
        self.player_rank_db = modules["player_rank"]

        self.connection_module.init_database()
        self.familiar_db.sync_master_data()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    # ==================================================
    # 補助
    # ==================================================
    def add_coin(self, user_id: int, amount: int) -> None:
        with closing(self.connection_module.get_connection()) as connection:
            with connection:
                connection.execute(
                    """
                    INSERT INTO balances (user_id, balance)
                    VALUES (?, ?)
                    ON CONFLICT(user_id)
                    DO UPDATE SET balance = balance + excluded.balance
                    """,
                    (user_id, amount),
                )

    def balance(self, user_id: int) -> int:
        with closing(self.connection_module.get_connection()) as connection:
            row = connection.execute(
                "SELECT balance FROM balances WHERE user_id = ?", (user_id,)
            ).fetchone()
        return row[0] if row else 0

    def create_guild(self, name: str, master_id: int, cost: int = 1_000_000) -> int:
        self.add_coin(master_id, cost)
        result = self.guild_db.create_guild(
            name=name, master_id=master_id, capacity=5, cost=cost
        )
        self.assertTrue(result["ok"], result)
        return result["guild_id"]


# ==================================================
# ギルド（5節・6節・7節）
# ==================================================
class GuildTests(GameDatabaseTestCase):
    def test_creation_is_atomic(self) -> None:
        guild_id = self.create_guild("VALHALLA", 100)

        self.assertEqual(self.balance(100), 0)
        self.assertEqual(self.guild_db.count_guild_members(guild_id), 1)
        self.assertEqual(
            self.guild_db.get_guild_member(guild_id, 100)["member_role"], "master"
        )

    def test_insufficient_balance_creates_nothing(self) -> None:
        self.add_coin(101, 100)
        result = self.guild_db.create_guild(
            name="POOR", master_id=101, capacity=5, cost=1_000_000
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "insufficient_balance")
        self.assertEqual(self.balance(101), 100)
        self.assertIsNone(self.guild_db.get_player_guild(101))

    def test_a_player_belongs_to_only_one_guild(self) -> None:
        self.create_guild("FIRST", 100)
        self.add_coin(100, 1_000_000)

        result = self.guild_db.create_guild(
            name="SECOND", master_id=100, capacity=5, cost=1_000_000
        )

        self.assertEqual(result["error"], "already_in_guild")
        self.assertEqual(self.balance(100), 1_000_000)

    def test_refund_restores_the_creation_cost(self) -> None:
        guild_id = self.create_guild("VALHALLA", 100)

        refunded = self.guild_db.refund_guild_creation(guild_id, 100, 1_000_000)

        self.assertTrue(refunded)
        self.assertEqual(self.balance(100), 1_000_000)
        self.assertIsNone(self.guild_db.get_player_guild(100))

    def test_join_request_is_rejected_when_the_guild_is_full(self) -> None:
        guild_id = self.create_guild("VALHALLA", 100)
        self.guild_db.update_guild_description(guild_id, "説明")
        self.guild_db.set_recruitment_status(guild_id, "open")

        for user_id in (200, 201, 202, 203):
            request = self.guild_db.create_join_request(guild_id, user_id)
            self.guild_db.approve_join_request(request["request_id"], approver_id=100)

        self.assertEqual(self.guild_db.count_guild_members(guild_id), 5)

        result = self.guild_db.create_join_request(guild_id, 204)
        self.assertEqual(result["error"], "guild_full")
        self.assertEqual(self.guild_db.get_pending_join_requests(guild_id), [])

    def test_approval_cancels_requests_to_other_guilds(self) -> None:
        first = self.create_guild("FIRST", 100)
        second = self.create_guild("SECOND", 101)

        for guild_id in (first, second):
            self.guild_db.update_guild_description(guild_id, "説明")
            self.guild_db.set_recruitment_status(guild_id, "open")

        request_a = self.guild_db.create_join_request(first, 300)
        request_b = self.guild_db.create_join_request(second, 300)
        self.guild_db.set_join_request_message(request_b["request_id"], 900, 901)

        result = self.guild_db.approve_join_request(
            request_a["request_id"], approver_id=100
        )

        self.assertTrue(result["ok"])
        self.assertEqual(len(result["cancelled"]), 1)
        self.assertEqual(result["cancelled"][0]["message_id"], 901)
        self.assertEqual(
            self.guild_db.get_join_request(request_b["request_id"])["status"],
            "auto_cancelled",
        )

    def test_founding_cancels_the_founders_other_requests(self) -> None:
        # 6.2節：参加申請は「申請者が別ギルドへ加入するまで」有効。
        # 設立も加入なので、他ギルドへの未処理申請は取り消す。
        host = self.create_guild("HOST", 100)
        self.guild_db.update_guild_description(host, "説明")
        self.guild_db.set_recruitment_status(host, "open")

        request = self.guild_db.create_join_request(host, 500)
        self.guild_db.set_join_request_message(request["request_id"], 900, 901)

        self.add_coin(500, 1_000_000)
        result = self.guild_db.create_guild(
            name="OWN", master_id=500, capacity=5, cost=1_000_000
        )

        self.assertTrue(result["ok"], result)
        self.assertEqual(len(result["cancelled"]), 1, result["cancelled"])
        self.assertEqual(result["cancelled"][0]["message_id"], 901)
        self.assertEqual(
            self.guild_db.get_join_request(request["request_id"])["status"],
            "auto_cancelled",
        )
        self.assertEqual(self.guild_db.get_pending_join_requests(host), [])

    def test_failed_creation_reports_no_cancelled_requests(self) -> None:
        self.add_coin(501, 100)
        result = self.guild_db.create_guild(
            name="POOR", master_id=501, capacity=5, cost=1_000_000
        )

        # 呼び出し側がKeyErrorにならないよう、失敗時もキーを揃える
        self.assertFalse(result["ok"])
        self.assertEqual(result["cancelled"], [])

    def test_master_cannot_leave_directly(self) -> None:
        guild_id = self.create_guild("VALHALLA", 100)

        result = self.guild_db.remove_guild_member(guild_id, 100)
        self.assertEqual(result["error"], "master_cannot_leave")

    def test_capacity_cannot_exceed_the_maximum(self) -> None:
        guild_id = self.create_guild("VALHALLA", 100)
        self.add_coin(100, 1_000_000)

        for _ in range(5):
            result = self.guild_db.expand_guild_capacity(
                guild_id, payer_id=100, cost=100_000, max_capacity=10
            )
            self.assertTrue(result["ok"], result)

        self.assertEqual(self.guild_db.get_guild(guild_id)["capacity"], 10)

        result = self.guild_db.expand_guild_capacity(
            guild_id, payer_id=100, cost=100_000, max_capacity=10
        )
        self.assertEqual(result["error"], "capacity_max")

    def test_archive_frees_every_member(self) -> None:
        guild_id = self.create_guild("VALHALLA", 100)
        self.guild_db.update_guild_description(guild_id, "説明")
        self.guild_db.set_recruitment_status(guild_id, "open")
        request = self.guild_db.create_join_request(guild_id, 400)
        self.guild_db.approve_join_request(request["request_id"], approver_id=100)

        result = self.guild_db.archive_guild(guild_id)

        self.assertTrue(result["ok"])
        self.assertIn(400, result["member_ids"])
        self.assertEqual(self.guild_db.get_guild(guild_id)["status"], "archived")
        self.assertIsNone(self.guild_db.get_player_guild(400))

    def test_ranking_orders_by_points_then_wins(self) -> None:
        first = self.create_guild("FIRST", 100)
        second = self.create_guild("SECOND", 101)
        third = self.create_guild("THIRD", 102)

        self.guild_db.add_guild_battle_record(first, "win")
        self.guild_db.add_guild_battle_record(first, "win")
        self.guild_db.add_guild_battle_record(second, "win")
        self.guild_db.add_guild_battle_record(third, "draw")

        ranking = self.guild_db.get_guild_ranking(
            20, win_points=3, draw_points=1, lose_points=0
        )
        order = [row["guild_id"] for row in ranking[:3]]

        self.assertEqual(order, [first, second, third])
        self.assertEqual(ranking[0]["points"], 6)
        self.assertEqual(ranking[0]["rank"], 1)


# ==================================================
# 使い魔（10節）
# ==================================================
class FamiliarTests(GameDatabaseTestCase):
    def test_master_data_sync_is_idempotent(self) -> None:
        self.familiar_db.sync_master_data()

        with closing(self.connection_module.get_connection()) as connection:
            familiars = connection.execute("SELECT COUNT(*) FROM familiars").fetchone()[0]
            skills = connection.execute(
                "SELECT COUNT(*) FROM familiar_skills"
            ).fetchone()[0]

        self.assertEqual(familiars, 40)
        self.assertEqual(skills, 34)

    def test_gacha_saves_every_result_in_one_transaction(self) -> None:
        self.add_coin(500, 100_000)
        results = [("B", "garm")] * 9 + [("S", "loki")]

        result = self.familiar_db.draw_gacha(
            500, pool_id="standard", count=10, cost=100_000, results=results
        )

        self.assertTrue(result["ok"])
        self.assertEqual(len(result["instances"]), 10)
        self.assertEqual(self.balance(500), 0)
        self.assertEqual(len(self.familiar_db.get_owned_familiars(500)), 10)

    def test_gacha_spends_no_coin_when_the_balance_is_short(self) -> None:
        self.add_coin(501, 5_000)

        result = self.familiar_db.draw_gacha(
            501, pool_id="standard", count=1, cost=10_000, results=[("B", "garm")]
        )

        self.assertEqual(result["error"], "insufficient_balance")
        self.assertEqual(self.balance(501), 5_000)
        self.assertEqual(self.familiar_db.get_owned_familiars(501), [])

    def test_fusion_consumes_the_material_and_raises_the_level(self) -> None:
        self.add_coin(502, 100_000)
        drawn = self.familiar_db.draw_gacha(
            502, pool_id="standard", count=2, cost=20_000,
            results=[("S", "loki"), ("S", "loki")],
        )
        base, material = (item["instance_id"] for item in drawn["instances"])

        result = self.familiar_db.fuse_familiar(
            502, base_instance_id=base, material_instance_id=material,
            max_level=10, locked_instance_ids=set(),
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["level"], 1)
        self.assertEqual(self.familiar_db.get_owned_familiar(base)["level"], 1)
        self.assertEqual(self.familiar_db.get_owned_familiar(material)["status"], "fused")
        self.assertEqual(len(self.familiar_db.get_owned_familiars(502)), 1)

    def test_fusion_requires_the_same_familiar(self) -> None:
        self.add_coin(503, 100_000)
        drawn = self.familiar_db.draw_gacha(
            503, pool_id="standard", count=2, cost=20_000,
            results=[("S", "loki"), ("B", "garm")],
        )
        base, material = (item["instance_id"] for item in drawn["instances"])

        result = self.familiar_db.fuse_familiar(
            503, base_instance_id=base, material_instance_id=material,
            max_level=10, locked_instance_ids=set(),
        )
        self.assertEqual(result["error"], "different_familiar")

    def test_selling_pays_coin_and_removes_the_familiar(self) -> None:
        self.add_coin(504, 10_000)
        drawn = self.familiar_db.draw_gacha(
            504, pool_id="standard", count=1, cost=10_000, results=[("S", "loki")]
        )
        instance_id = drawn["instances"][0]["instance_id"]

        result = self.familiar_db.sell_familiar(
            504, instance_id=instance_id, price=50_000, locked_instance_ids=set()
        )

        self.assertTrue(result["ok"])
        self.assertEqual(self.balance(504), 50_000)
        self.assertEqual(self.familiar_db.get_owned_familiars(504), [])

    def test_familiars_in_use_cannot_be_sold_or_fused(self) -> None:
        self.add_coin(505, 30_000)
        drawn = self.familiar_db.draw_gacha(
            505, pool_id="standard", count=2, cost=20_000,
            results=[("S", "loki"), ("S", "loki")],
        )
        base, material = (item["instance_id"] for item in drawn["instances"])

        sold = self.familiar_db.sell_familiar(
            505, instance_id=base, price=50_000, locked_instance_ids={base}
        )
        fused = self.familiar_db.fuse_familiar(
            505, base_instance_id=base, material_instance_id=material,
            max_level=10, locked_instance_ids={material},
        )

        self.assertEqual(sold["error"], "in_use")
        self.assertEqual(fused["error"], "in_use")
        self.assertEqual(self.balance(505), 10_000)


# ==================================================
# バトル（12節・13節・16節・26節）
# ==================================================
class BattleTests(GameDatabaseTestCase):
    def setUp(self) -> None:
        super().setUp()

        self.guild_a = self.create_guild("ALPHA", 100)
        self.guild_b = self.create_guild("BETA", 200)

        for guild_id in (self.guild_a, self.guild_b):
            self.guild_db.update_guild_description(guild_id, "説明")
            self.guild_db.set_recruitment_status(guild_id, "open")

        self.members_a = [100]
        self.members_b = [200]
        for index in range(4):
            for guild_id, members, base in (
                (self.guild_a, self.members_a, 110),
                (self.guild_b, self.members_b, 210),
            ):
                user_id = base + index
                request = self.guild_db.create_join_request(guild_id, user_id)
                self.guild_db.approve_join_request(
                    request["request_id"], approver_id=members[0]
                )
                members.append(user_id)

    def build_units(self) -> list[dict]:
        from game.battle_engine import build_unit_payloads

        familiar_ids = [
            "loki", "surtr", "fenrir", "jormungandr", "hel",
            "garm", "hydra", "dullahan", "phoenix", "kraken",
        ]
        entries = []
        for index in range(10):
            in_guild_a = index < 5
            entries.append(
                {
                    "guild_id": self.guild_a if in_guild_a else self.guild_b,
                    "player_id": (self.members_a if in_guild_a else self.members_b)[index % 5],
                    "instance_id": 7000 + index,
                    "familiar_id": familiar_ids[index],
                    "level": 0,
                    "slot": (index % 5) + 1,
                }
            )
        return build_unit_payloads(entries)

    def start_battle(self) -> int:
        result = self.battle_db.create_battle(
            guild_a_id=self.guild_a,
            guild_b_id=self.guild_b,
            guild_time_seconds=3600,
            units=self.build_units(),
        )
        self.assertTrue(result["ok"], result)
        return result["battle_id"]

    # ==================================================
    def test_one_active_request_per_guild(self) -> None:
        first = self.battle_db.create_battle_request(self.guild_a, self.guild_b)
        self.assertTrue(first["ok"])

        second = self.battle_db.create_battle_request(self.guild_a, self.guild_b)
        self.assertIn(second["error"], {"guild_busy", "opponent_busy"})

        self.battle_db.resolve_battle_request(first["request_id"], "cancelled")
        self.assertIsNone(self.battle_db.get_battle_lock(self.guild_a))

        third = self.battle_db.create_battle_request(self.guild_a, self.guild_b)
        self.assertTrue(third["ok"])

    def test_only_one_guild_can_claim_a_recruitment(self) -> None:
        recruitment = self.battle_db.create_battle_recruitment(self.guild_a)

        first = self.battle_db.claim_battle_recruitment(
            recruitment["recruitment_id"], self.guild_b
        )
        second = self.battle_db.claim_battle_recruitment(
            recruitment["recruitment_id"], self.guild_b
        )

        self.assertTrue(first["ok"])
        self.assertEqual(second["error"], "already_matched")

    def test_roster_requires_guild_membership(self) -> None:
        result = self.battle_db.set_battle_roster(
            self.guild_a, self.members_a[:4] + [999]
        )
        self.assertEqual(result["error"], "not_member")

        duplicate = self.battle_db.set_battle_roster(
            self.guild_a, self.members_a[:4] + [self.members_a[0]]
        )
        self.assertEqual(duplicate["error"], "duplicate_member")

    def test_battle_state_round_trips_through_the_database(self) -> None:
        from game import battle_engine

        battle_id = self.start_battle()
        state = self.battle_db.load_battle_state(battle_id)

        self.assertIsNotNone(state)
        self.assertEqual(len(state.units), 10)
        self.assertEqual(state.remaining_seconds[self.guild_a], 3600)

        battle_engine.set_random(__import__("random").Random(3))
        battle_engine.start_battle(state)
        state.action_seq += 1
        self.assertTrue(self.battle_db.save_battle_state(state, expected_action_seq=0))

        reloaded = self.battle_db.load_battle_state(battle_id)
        self.assertEqual(reloaded.current_round, state.current_round)
        self.assertEqual(reloaded.turn_order, state.turn_order)
        self.assertEqual(reloaded.current_unit_id, state.current_unit_id)
        self.assertEqual(len(reloaded.effects), len(state.effects))
        for unit_id, unit in state.units.items():
            self.assertEqual(reloaded.units[unit_id].current_hp, unit.current_hp)
            self.assertEqual(reloaded.units[unit_id].current_atk, unit.current_atk)

    def test_a_stale_action_is_rejected(self) -> None:
        # 16節「同じ行動を2回処理しない」
        from game import battle_engine

        battle_id = self.start_battle()
        state = self.battle_db.load_battle_state(battle_id)
        battle_engine.set_random(__import__("random").Random(3))
        battle_engine.start_battle(state)

        state.action_seq += 1
        self.assertTrue(self.battle_db.save_battle_state(state, expected_action_seq=0))
        self.assertFalse(self.battle_db.save_battle_state(state, expected_action_seq=0))

    def test_creating_a_battle_locks_both_rosters(self) -> None:
        battle_id = self.start_battle()

        self.assertEqual(self.guild_db.get_guild(self.guild_a)["roster_locked"], 1)
        self.assertEqual(self.guild_db.get_guild(self.guild_b)["roster_locked"], 1)
        self.assertEqual(
            self.battle_db.get_active_battle_for_guild(self.guild_a)["battle_id"],
            battle_id,
        )
        self.assertEqual(len(self.battle_db.get_locked_instance_ids()), 10)

    def test_finishing_a_battle_records_the_result_once(self) -> None:
        battle_id = self.start_battle()

        first = self.battle_db.finish_battle(
            battle_id, result="guild_a", end_reason="wipe"
        )
        second = self.battle_db.finish_battle(
            battle_id, result="guild_a", end_reason="wipe"
        )

        self.assertTrue(first["ok"])
        self.assertEqual(second["error"], "already_finished")
        self.assertEqual(self.guild_db.get_guild(self.guild_a)["wins"], 1)
        self.assertEqual(self.guild_db.get_guild(self.guild_b)["losses"], 1)
        self.assertEqual(self.guild_db.get_guild(self.guild_a)["roster_locked"], 0)

    def test_finishing_a_battle_clears_both_rosters(self) -> None:
        # 26.2節「バトル終了後は出場者セットを解除し、一般出場者から
        # 出場者専用TCの閲覧権限を外します」
        self.battle_db.set_battle_roster(self.guild_a, self.members_a)
        self.battle_db.set_battle_roster(self.guild_b, self.members_b)
        self.assertEqual(len(self.battle_db.get_battle_roster(self.guild_a)), 5)

        battle_id = self.start_battle()
        self.battle_db.finish_battle(
            battle_id, result="guild_a", end_reason="wipe"
        )

        self.assertEqual(self.battle_db.get_battle_roster(self.guild_a), [])
        self.assertEqual(self.battle_db.get_battle_roster(self.guild_b), [])
        self.assertEqual(self.battle_db.get_locked_instance_ids(), set())

    def test_rewards_respect_the_daily_limits(self) -> None:
        # 26.2節：1プレイヤー1日3試合まで、同じ2ギルド間は1日最初の1試合だけ
        battle_id = self.start_battle()
        self.battle_db.finish_battle(battle_id, result="guild_a", end_reason="wipe")

        entries = [
            {"user_id": user_id, "guild_id": self.guild_a, "coin": 20_000, "xp": 100}
            for user_id in self.members_a
        ]
        granted = self.battle_db.grant_battle_rewards(
            battle_id,
            entries=entries,
            reward_date="2026-08-15",
            low_guild_id=min(self.guild_a, self.guild_b),
            high_guild_id=max(self.guild_a, self.guild_b),
            daily_limit=3,
        )

        self.assertEqual(len(granted), 5)
        self.assertEqual(self.balance(self.members_a[0]), 20_000)

        repeated = self.battle_db.grant_battle_rewards(
            battle_id + 1,
            entries=entries,
            reward_date="2026-08-15",
            low_guild_id=min(self.guild_a, self.guild_b),
            high_guild_id=max(self.guild_a, self.guild_b),
            daily_limit=3,
        )
        self.assertEqual(repeated, [])
        self.assertEqual(self.balance(self.members_a[0]), 20_000)


if __name__ == "__main__":
    unittest.main()
