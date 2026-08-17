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

        self.assertEqual(familiars, 50)
        self.assertEqual(skills, 40)

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

    def test_gacha_starts_familiars_at_level_one(self) -> None:
        self.add_coin(506, 30_000)
        drawn = self.familiar_db.draw_gacha(
            506, pool_id="standard", count=1, cost=30_000,
            results=[("S", "loki")], initial_level=1,
        )

        self.assertEqual(drawn["instances"][0]["level"], 1)
        self.assertEqual(
            self.familiar_db.get_owned_familiars(506)[0]["level"], 1
        )

    def test_fusion_consumes_the_materials_and_raises_the_level(self) -> None:
        self.add_coin(502, 100_000)
        drawn = self.familiar_db.draw_gacha(
            502, pool_id="standard", count=3, cost=20_000,
            results=[("S", "loki")] * 3, initial_level=1,
        )
        base, *materials = (item["instance_id"] for item in drawn["instances"])

        result = self.familiar_db.fuse_familiar(
            502, base_instance_id=base, material_count=2,
            max_level=10, locked_instance_ids=set(),
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["before_level"], 1)
        self.assertEqual(result["level"], 3)
        self.assertEqual(sorted(result["material_instance_ids"]), sorted(materials))
        self.assertEqual(self.familiar_db.get_owned_familiar(base)["level"], 3)

        for material in materials:
            self.assertEqual(
                self.familiar_db.get_owned_familiar(material)["status"], "fused"
            )

        self.assertEqual(len(self.familiar_db.get_owned_familiars(502)), 1)

    def test_fusion_never_passes_the_maximum_level(self) -> None:
        self.add_coin(503, 100_000)
        drawn = self.familiar_db.draw_gacha(
            503, pool_id="standard", count=12, cost=20_000,
            results=[("S", "loki")] * 12, initial_level=1,
        )
        base = drawn["instances"][0]["instance_id"]

        over = self.familiar_db.fuse_familiar(
            503, base_instance_id=base, material_count=10,
            max_level=10, locked_instance_ids=set(),
        )
        self.assertEqual(over["error"], "over_max_level")
        self.assertEqual(over["available_levels"], 9)
        self.assertEqual(self.familiar_db.get_owned_familiar(base)["level"], 1)

        to_max = self.familiar_db.fuse_familiar(
            503, base_instance_id=base, material_count=9,
            max_level=10, locked_instance_ids=set(),
        )
        self.assertTrue(to_max["ok"])
        self.assertEqual(to_max["level"], 10)

    def test_fusion_needs_enough_materials(self) -> None:
        self.add_coin(507, 100_000)
        drawn = self.familiar_db.draw_gacha(
            507, pool_id="standard", count=2, cost=20_000,
            results=[("S", "loki"), ("B", "garm")], initial_level=1,
        )
        base = drawn["instances"][0]["instance_id"]

        # 別の種類は素材にできないため、素材が足りない
        result = self.familiar_db.fuse_familiar(
            507, base_instance_id=base, material_count=1,
            max_level=10, locked_instance_ids=set(),
        )
        self.assertEqual(result["error"], "not_enough_materials")
        self.assertEqual(result["available_materials"], 0)

    def test_selling_pays_coin_and_removes_the_familiars(self) -> None:
        self.add_coin(504, 10_000)
        drawn = self.familiar_db.draw_gacha(
            504, pool_id="standard", count=2, cost=10_000,
            results=[("S", "loki")] * 2, initial_level=1,
        )
        instance_ids = [item["instance_id"] for item in drawn["instances"]]

        result = self.familiar_db.sell_familiars(
            504,
            prices={instance_id: 50_000 for instance_id in instance_ids},
            locked_instance_ids=set(),
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["total"], 100_000)
        self.assertEqual(len(result["sold"]), 2)
        self.assertEqual(self.balance(504), 100_000)
        self.assertEqual(self.familiar_db.get_owned_familiars(504), [])

    def test_familiars_in_use_cannot_be_sold_or_fused(self) -> None:
        self.add_coin(505, 30_000)
        drawn = self.familiar_db.draw_gacha(
            505, pool_id="standard", count=2, cost=20_000,
            results=[("S", "loki"), ("S", "loki")], initial_level=1,
        )
        base, material = (item["instance_id"] for item in drawn["instances"])

        sold = self.familiar_db.sell_familiars(
            505, prices={base: 50_000}, locked_instance_ids={base}
        )
        fused = self.familiar_db.fuse_familiar(
            505, base_instance_id=base, material_count=1,
            max_level=10, locked_instance_ids={material},
        )

        self.assertEqual(sold["error"], "in_use")
        self.assertEqual(fused["error"], "not_enough_materials")
        self.assertEqual(self.balance(505), 10_000)

    def test_fusion_charges_coin(self) -> None:
        self.add_coin(509, 100_000)
        drawn = self.familiar_db.draw_gacha(
            509, pool_id="standard", count=3, cost=0,
            results=[("S", "loki")] * 3, initial_level=1,
        )
        base = drawn["instances"][0]["instance_id"]

        result = self.familiar_db.fuse_familiar(
            509, base_instance_id=base, material_count=2,
            max_level=10, locked_instance_ids=set(), cost=50_000,
        )

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["cost"], 50_000)
        self.assertEqual(self.balance(509), 50_000)

    def test_fusion_without_enough_coin_changes_nothing(self) -> None:
        self.add_coin(510, 1_000)
        drawn = self.familiar_db.draw_gacha(
            510, pool_id="standard", count=2, cost=0,
            results=[("S", "loki")] * 2, initial_level=1,
        )
        base, material = (item["instance_id"] for item in drawn["instances"])

        result = self.familiar_db.fuse_familiar(
            510, base_instance_id=base, material_count=1,
            max_level=10, locked_instance_ids=set(), cost=25_000,
        )

        self.assertEqual(result["error"], "insufficient_balance")
        self.assertEqual(self.balance(510), 1_000)
        self.assertEqual(self.familiar_db.get_owned_familiar(base)["level"], 1)
        self.assertEqual(
            self.familiar_db.get_owned_familiar(material)["status"], "owned"
        )

    def test_selling_is_all_or_nothing(self) -> None:
        self.add_coin(508, 30_000)
        drawn = self.familiar_db.draw_gacha(
            508, pool_id="standard", count=2, cost=20_000,
            results=[("S", "loki")] * 2, initial_level=1,
        )
        first, second = (item["instance_id"] for item in drawn["instances"])

        result = self.familiar_db.sell_familiars(
            508,
            prices={first: 50_000, second: 50_000},
            locked_instance_ids={second},
        )

        self.assertEqual(result["error"], "in_use")
        self.assertEqual(self.balance(508), 10_000)
        self.assertEqual(len(self.familiar_db.get_owned_familiars(508)), 2)


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

    # ==================================================
    # 出場者と使い魔のセット（9節）
    # ==================================================
    def familiar_limit(self, member_count: int) -> int:
        from game.master_data import load_master_data

        return load_master_data().familiar_limit_per_member(member_count)

    def give_familiars(self, user_id: int, count: int) -> list[int]:
        """テスト用に使い魔を配り、instance_idを返す。"""

        self.add_coin(user_id, 30_000 * count)
        drawn = self.familiar_db.draw_gacha(
            user_id,
            pool_id="standard",
            count=count,
            cost=30_000 * count,
            results=[("B", "garm")] * count,
            initial_level=1,
        )
        self.assertTrue(drawn["ok"], drawn)
        return [item["instance_id"] for item in drawn["instances"]]

    def set_roster(self, guild_id: int, assignments):
        return self.battle_db.set_battle_roster(guild_id, list(assignments))

    def even_roster(self, guild_id: int, user_ids: list[int]):
        """全員へ1体ずつ割り当てて出場者セットする。"""

        return self.set_roster(guild_id, [(user_id, 1) for user_id in user_ids])

    def add_entry(self, guild_id: int, user_id: int, instance_id: int):
        from game.master_data import load_master_data

        return self.battle_db.add_battle_entry(
            guild_id,
            user_id,
            instance_id,
            max_units=load_master_data().battle.max_units,
        )

    def test_per_member_limit_follows_the_roster_size(self) -> None:
        # 5人→1体、4人→2体、3人→2体、2人→3体、1人→5体
        self.assertEqual(self.familiar_limit(5), 1)
        self.assertEqual(self.familiar_limit(4), 2)
        self.assertEqual(self.familiar_limit(3), 2)
        self.assertEqual(self.familiar_limit(2), 3)
        self.assertEqual(self.familiar_limit(1), 5)

    def test_the_master_decides_how_many_familiars_each_member_brings(self) -> None:
        # 9節：出場者ごとの体数はギルドマスターが割り当てる
        members = self.members_a[:3]
        result = self.set_roster(
            self.guild_a, [(members[0], 2), (members[1], 2), (members[2], 1)]
        )

        self.assertTrue(result["ok"], result)
        self.assertEqual(
            [row["familiar_count"] for row in self.battle_db.get_battle_roster(self.guild_a)],
            [2, 2, 1],
        )

    def test_assignments_respect_the_per_member_and_guild_limits(self) -> None:
        members = self.members_a[:3]

        # 3人のときは1人2体まで
        over_member = self.set_roster(
            self.guild_a, [(members[0], 3), (members[1], 1), (members[2], 1)]
        )
        self.assertEqual(over_member["error"], "member_limit")

        # 合計は5体まで
        over_total = self.set_roster(
            self.guild_a, [(members[0], 2), (members[1], 2), (members[2], 2)]
        )
        self.assertEqual(over_total["error"], "entries_full")

        # 0体の割り当ては認めない
        zero = self.set_roster(self.guild_a, [(members[0], 0)])
        self.assertEqual(zero["error"], "invalid_count")

    def test_registered_familiars_are_adopted_in_order(self) -> None:
        # 9節：事前登録した順番のまま自動でセットする
        user_id = self.members_a[0]
        instances = self.give_familiars(user_id, 5)

        registered = self.battle_db.set_player_battle_familiars(
            user_id, list(reversed(instances[:3]))
        )
        self.assertTrue(registered["ok"], registered)

        result = self.set_roster(self.guild_a, [(user_id, 2)])
        self.assertTrue(result["ok"], result)

        entries = self.battle_db.get_battle_entries(self.guild_a)
        self.assertEqual(
            [int(entry["instance_id"]) for entry in entries],
            [instances[2], instances[1]],
        )
        self.assertEqual(result["adopted"], [instances[2], instances[1]])

    def test_registration_is_independent_of_guilds_and_locks(self) -> None:
        # 出場者でなくても、編成ロック中でも登録できる
        outsider = 9_001
        instances = self.give_familiars(outsider, 2)

        result = self.battle_db.set_player_battle_familiars(outsider, instances)
        self.assertTrue(result["ok"], result)
        self.assertEqual(
            [int(row["instance_id"]) for row in
             self.battle_db.get_player_battle_familiars(outsider)],
            instances,
        )

        self.guild_db.set_roster_locked(self.guild_a, True)
        member = self.members_a[0]
        member_instances = self.give_familiars(member, 1)

        during_lock = self.battle_db.set_player_battle_familiars(
            member, member_instances
        )
        self.assertTrue(during_lock["ok"], during_lock)
        self.guild_db.set_roster_locked(self.guild_a, False)

    def test_registration_rejects_familiars_the_player_does_not_own(self) -> None:
        owner = self.members_a[0]
        other = self.members_a[1]
        instance_id = self.give_familiars(other, 1)[0]

        result = self.battle_db.set_player_battle_familiars(owner, [instance_id])
        self.assertEqual(result["error"], "not_owned")
        self.assertEqual(self.battle_db.get_player_battle_familiars(owner), [])

    def test_sold_familiars_drop_out_of_the_registration(self) -> None:
        user_id = self.members_a[0]
        instances = self.give_familiars(user_id, 2)
        self.battle_db.set_player_battle_familiars(user_id, instances)

        self.familiar_db.sell_familiars(
            user_id, prices={instances[0]: 5_000}, locked_instance_ids=set()
        )

        remaining = self.battle_db.get_player_battle_familiars(user_id)
        self.assertEqual([int(row["instance_id"]) for row in remaining], [instances[1]])

    def test_a_single_member_can_set_five_familiars(self) -> None:
        user_id = self.members_a[0]
        instances = self.give_familiars(user_id, 6)
        self.set_roster(self.guild_a, [(user_id, 5)])

        for instance_id in instances[:5]:
            result = self.add_entry(self.guild_a, user_id, instance_id)
            self.assertTrue(result["ok"], result)

        self.assertEqual(len(self.battle_db.get_battle_entries(self.guild_a)), 5)

        # 6体目は上限で拒否される（1人5体＝ギルド合計5体なので両方に該当する）
        overflow = self.add_entry(self.guild_a, user_id, instances[5])
        self.assertIn(overflow["error"], {"member_limit", "entries_full"})

    def test_five_members_can_set_one_familiar_each(self) -> None:
        self.even_roster(self.guild_a, self.members_a)

        for user_id in self.members_a:
            instances = self.give_familiars(user_id, 2)
            first = self.add_entry(self.guild_a, user_id, instances[0])
            self.assertTrue(first["ok"], first)

            second = self.add_entry(self.guild_a, user_id, instances[1])
            self.assertIn(second["error"], {"member_limit", "entries_full"})

        self.assertEqual(len(self.battle_db.get_battle_entries(self.guild_a)), 5)

    def test_members_can_only_fill_their_assigned_slots(self) -> None:
        members = self.members_a[:4]
        self.set_roster(
            self.guild_a,
            [(members[0], 2), (members[1], 1), (members[2], 1), (members[3], 1)],
        )

        instances = {user_id: self.give_familiars(user_id, 3) for user_id in members}

        for user_id in members:
            self.assertTrue(self.add_entry(self.guild_a, user_id, instances[user_id][0])["ok"])

        # 2体割り当てられた人だけが2体目を置ける
        self.assertTrue(
            self.add_entry(self.guild_a, members[0], instances[members[0]][1])["ok"]
        )
        self.assertEqual(len(self.battle_db.get_battle_entries(self.guild_a)), 5)

        # 1体しか割り当てられていない人は、合計に空きがあっても置けない
        blocked = self.add_entry(self.guild_a, members[1], instances[members[1]][1])
        self.assertEqual(blocked["error"], "member_limit")
        self.assertEqual(blocked["limit"], 1)

        # 割り当て分を使い切った人も置けない
        over = self.add_entry(self.guild_a, members[0], instances[members[0]][2])
        self.assertIn(over["error"], {"member_limit", "entries_full"})

    def test_members_can_swap_their_own_familiars(self) -> None:
        # 9節：本人は割り当ての範囲で自由に差し替えられる
        user_id = self.members_a[0]
        instances = self.give_familiars(user_id, 3)
        self.battle_db.set_player_battle_familiars(user_id, instances[:2])
        self.set_roster(self.guild_a, [(user_id, 2)])

        self.battle_db.remove_battle_entry(self.guild_a, user_id, instances[0])
        self.assertTrue(self.add_entry(self.guild_a, user_id, instances[2])["ok"])

        entries = self.battle_db.get_battle_entries(self.guild_a)
        self.assertEqual(
            sorted(int(entry["instance_id"]) for entry in entries),
            sorted([instances[1], instances[2]]),
        )
        self.assertEqual([int(entry["entry_slot"]) for entry in entries], [1, 2])

    def test_the_same_familiar_cannot_be_set_twice(self) -> None:
        user_id = self.members_a[0]
        instance_id = self.give_familiars(user_id, 1)[0]
        self.set_roster(self.guild_a, [(user_id, 5)])

        self.assertTrue(self.add_entry(self.guild_a, user_id, instance_id)["ok"])
        again = self.add_entry(self.guild_a, user_id, instance_id)

        self.assertEqual(again["error"], "already_set")

    def test_only_selected_members_can_set_familiars(self) -> None:
        outsider = self.members_a[1]
        self.set_roster(self.guild_a, [(self.members_a[0], 1)])
        instance_id = self.give_familiars(outsider, 1)[0]

        result = self.add_entry(self.guild_a, outsider, instance_id)
        self.assertEqual(result["error"], "not_selected")

    def test_only_owned_familiars_can_be_set(self) -> None:
        user_id = self.members_a[0]
        other = self.members_a[1]
        self.set_roster(self.guild_a, [(user_id, 1)])
        instance_id = self.give_familiars(other, 1)[0]

        result = self.add_entry(self.guild_a, user_id, instance_id)
        self.assertEqual(result["error"], "not_owned")

    def test_shrinking_the_assignment_releases_extra_familiars(self) -> None:
        # 1人で5体セットしたあと5人へ広げると、割り当て1体を超えた分が解除される
        user_id = self.members_a[0]
        instances = self.give_familiars(user_id, 5)
        self.set_roster(self.guild_a, [(user_id, 5)])

        for instance_id in instances:
            self.add_entry(self.guild_a, user_id, instance_id)

        self.assertEqual(len(self.battle_db.get_battle_entries(self.guild_a)), 5)

        result = self.even_roster(self.guild_a, self.members_a)

        self.assertTrue(result["ok"], result)
        self.assertEqual(len(result["released"]), 4, result)
        self.assertEqual(len(self.battle_db.get_battle_entries(self.guild_a)), 1)

    def test_removing_a_member_releases_their_familiars(self) -> None:
        members = self.members_a[:2]
        self.even_roster(self.guild_a, members)

        for user_id in members:
            self.add_entry(self.guild_a, user_id, self.give_familiars(user_id, 1)[0])

        self.assertEqual(len(self.battle_db.get_battle_entries(self.guild_a)), 2)

        self.set_roster(self.guild_a, [(members[0], 1)])
        entries = self.battle_db.get_battle_entries(self.guild_a)

        self.assertEqual(len(entries), 1)
        self.assertEqual(int(entries[0]["user_id"]), members[0])
        self.assertEqual(int(entries[0]["entry_slot"]), 1)

    def test_set_familiars_are_locked_from_fusion_and_selling(self) -> None:
        user_id = self.members_a[0]
        instance_id = self.give_familiars(user_id, 1)[0]
        self.set_roster(self.guild_a, [(user_id, 1)])
        self.add_entry(self.guild_a, user_id, instance_id)

        # 編成ロック前は自由に売却できる
        self.assertNotIn(instance_id, self.battle_db.get_locked_instance_ids())

        self.guild_db.set_roster_locked(self.guild_a, True)
        self.assertIn(instance_id, self.battle_db.get_locked_instance_ids())

    def test_kicking_a_member_releases_their_entries(self) -> None:
        members = self.members_a[:3]
        owned = {user_id: self.give_familiars(user_id, 2) for user_id in members}

        for user_id, instance_ids in owned.items():
            self.battle_db.set_player_battle_familiars(user_id, instance_ids)

        self.set_roster(
            self.guild_a, [(members[0], 2), (members[1], 2), (members[2], 1)]
        )
        self.assertEqual(len(self.battle_db.get_battle_entries(self.guild_a)), 5)

        self.guild_db.remove_guild_member(self.guild_a, members[1])
        entries = self.battle_db.get_battle_entries(self.guild_a)

        self.assertTrue(all(int(e["user_id"]) != members[1] for e in entries), entries)
        self.assertEqual(len(entries), 3)
        # 枠番号を詰め直しておかないと、次の追加でIDが衝突する
        self.assertEqual([int(e["entry_slot"]) for e in entries], [1, 2, 3])

        # 事前登録は本人のものなので残る
        self.assertEqual(
            len(self.battle_db.get_player_battle_familiars(members[1])), 2
        )

        self.battle_db.remove_battle_entry(
            self.guild_a, members[0], owned[members[0]][0]
        )
        added = self.add_entry(self.guild_a, members[0], owned[members[0]][0])
        self.assertTrue(added["ok"], added)

        slots = [int(e["entry_slot"]) for e in self.battle_db.get_battle_entries(self.guild_a)]
        self.assertEqual(len(slots), len(set(slots)), slots)

    def test_roster_requires_guild_membership(self) -> None:
        result = self.even_roster(self.guild_a, self.members_a[:4] + [999])
        self.assertEqual(result["error"], "not_member")

        duplicate = self.even_roster(
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
        self.even_roster(self.guild_a, self.members_a)
        self.even_roster(self.guild_b, self.members_b)
        self.assertEqual(len(self.battle_db.get_battle_roster(self.guild_a)), 5)

        battle_id = self.start_battle()
        self.battle_db.finish_battle(
            battle_id, result="guild_a", end_reason="wipe"
        )

        self.assertEqual(self.battle_db.get_battle_roster(self.guild_a), [])
        self.assertEqual(self.battle_db.get_battle_roster(self.guild_b), [])
        self.assertEqual(self.battle_db.get_locked_instance_ids(), set())

    def settle(self, battle_id: int, *, winner: int, loser: int, date="2026-08-15"):
        return self.battle_db.settle_battle_bet(
            battle_id,
            winners=[{"user_id": winner, "guild_id": self.guild_a}],
            losers=[{"user_id": loser, "guild_id": self.guild_b}],
            drawers=[],
            bet_coin=20_000,
            win_xp=40,
            lose_xp=20,
            draw_xp=20,
            reward_date=date,
            daily_limit=3,
        )

    def test_the_bet_moves_coin_from_the_loser_to_the_winner(self) -> None:
        # 26.2節：ベットしたcoinは負けた側から勝った側へ移る
        battle_id = self.start_battle()
        self.battle_db.finish_battle(battle_id, result="guild_a", end_reason="wipe")

        winner, loser = self.members_a[0], self.members_b[0]
        self.add_coin(winner, 50_000)
        self.add_coin(loser, 50_000)

        outcome = self.settle(battle_id, winner=winner, loser=loser)

        self.assertEqual(outcome["pot"], 20_000)
        self.assertEqual(self.balance(winner), 70_000)
        self.assertEqual(self.balance(loser), 30_000)
        # coinは移動するだけで総量は変わらない
        self.assertEqual(self.balance(winner) + self.balance(loser), 100_000)

    def test_the_bet_is_split_evenly_with_rounding_up_first(self) -> None:
        # 26.2節：端数はメンバー選択順に切り上げ → 切り下げ
        self.assertEqual(self.battle_db.split_bet_evenly(20_000, 1), [20_000])
        self.assertEqual(self.battle_db.split_bet_evenly(20_000, 2), [10_000, 10_000])
        self.assertEqual(
            self.battle_db.split_bet_evenly(20_000, 3), [6_667, 6_667, 6_666]
        )

        for count in range(1, 6):
            self.assertEqual(
                sum(self.battle_db.split_bet_evenly(20_000, count)), 20_000, count
            )

    def test_the_guild_bet_is_shared_by_its_members(self) -> None:
        # ベット額はギルド単位。出場者が増えても1ギルドの負担は変わらない
        battle_id = self.start_battle()
        self.battle_db.finish_battle(battle_id, result="guild_a", end_reason="wipe")

        winner = self.members_a[0]
        losers = self.members_b[:3]

        self.add_coin(winner, 100_000)
        for user_id in losers:
            self.add_coin(user_id, 100_000)

        outcome = self.battle_db.settle_battle_bet(
            battle_id,
            winners=[{"user_id": winner, "guild_id": self.guild_a}],
            losers=[{"user_id": u, "guild_id": self.guild_b} for u in losers],
            drawers=[],
            bet_coin=20_000,
            win_xp=40,
            lose_xp=20,
            draw_xp=20,
            reward_date="2026-08-18",
            daily_limit=3,
        )

        self.assertEqual(outcome["pot"], 20_000)
        self.assertEqual(self.balance(winner), 120_000)
        # 先頭2人が切り上げ、最後が切り下げ
        self.assertEqual(
            [self.balance(u) for u in losers], [93_333, 93_333, 93_334]
        )

    def test_the_bet_amount_is_stored_on_the_battle(self) -> None:
        request = self.battle_db.create_battle_request(
            self.guild_a, self.guild_b, bet_coin=55_000
        )
        self.assertTrue(request["ok"], request)

        row = self.battle_db.get_battle_request(int(request["request_id"]))
        self.assertEqual(int(row["bet_coin"]), 55_000)

        resolved = self.battle_db.resolve_battle_request(
            int(request["request_id"]), "approved"
        )
        self.assertEqual(int(resolved["bet_coin"]), 55_000)

    def test_the_bet_is_settled_only_once_per_battle(self) -> None:
        battle_id = self.start_battle()
        self.battle_db.finish_battle(battle_id, result="guild_a", end_reason="wipe")

        winner, loser = self.members_a[0], self.members_b[0]
        self.add_coin(winner, 50_000)
        self.add_coin(loser, 50_000)

        self.settle(battle_id, winner=winner, loser=loser)
        repeated = self.settle(battle_id, winner=winner, loser=loser)

        self.assertEqual(repeated["results"], [])
        self.assertEqual(self.balance(winner), 70_000)

    def test_the_bet_never_makes_a_negative_balance(self) -> None:
        battle_id = self.start_battle()
        self.battle_db.finish_battle(battle_id, result="guild_a", end_reason="wipe")

        winner, loser = self.members_a[0], self.members_b[0]
        self.add_coin(winner, 10_000)
        self.add_coin(loser, 5_000)

        outcome = self.settle(battle_id, winner=winner, loser=loser)

        self.assertEqual(outcome["pot"], 5_000)
        self.assertEqual(self.balance(loser), 0)
        self.assertEqual(self.balance(winner), 15_000)


if __name__ == "__main__":
    unittest.main()
