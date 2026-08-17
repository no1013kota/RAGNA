"""使い魔・スキル・ガチャのマスターデータが仕様どおりかを確認するテスト。

docs/BATTLE_RULES.md と docs/GAME_SPEC.md の数値を変更した場合は、
このテストの期待値も同じ変更として更新してください。
"""

from __future__ import annotations

import re
import unittest

from pathlib import Path

from game.master_data import load_master_data
from game.models import (
    GENDER_VALUES,
    SKILL_TYPE_ACTIVE,
    SKILL_TYPE_PASSIVE,
    TRIGGERS,
    compute_level_stats,
    is_opposite_gender,
    round_half_up,
)


MASTER = load_master_data()

ROOT = Path(__file__).resolve().parents[1]
BATTLE_RULES_DOC = ROOT / "docs" / "BATTLE_RULES.md"

GENDER_LABELS = {"male": "男性", "female": "女性", "none": "なし"}


class DocumentConsistencyTests(unittest.TestCase):
    """docs/BATTLE_RULES.md と data/master/*.json のずれを防ぐ。"""

    def setUp(self) -> None:
        self.document = BATTLE_RULES_DOC.read_text(encoding="utf-8")

    def parse_document(self) -> dict[str, tuple[int, int, int, str]]:
        """文書の表から ``ID → (HP, ATK, SPD, 性別)`` を読み取る。

        Sランクは1体ずつ、A・B・Cランクは一覧表で書いているため、どちらの形も
        読めるように ``| `id` |`` を含む行だけを対象にします。
        """

        entries: dict[str, tuple[int, int, int, str]] = {}

        for line in self.document.split("\n"):
            if "`" not in line or not line.startswith("|"):
                continue

            cells = [cell.strip() for cell in line.strip("|").split("|")]
            ids = [
                cell.strip("`")
                for cell in cells
                if cell.startswith("`") and cell.endswith("`")
            ]
            if len(ids) != 1:
                continue

            # B・Cランクは先頭に連番の列があるため、ID列より後ろだけを見る
            after_id = cells[cells.index(f"`{ids[0]}`") + 1 :]
            numbers = [cell for cell in after_id if cell.isdigit()]
            genders = [cell for cell in after_id if cell in GENDER_LABELS.values()]

            if len(numbers) < 3 or not genders:
                continue

            entries[ids[0]] = (
                int(numbers[0]),
                int(numbers[1]),
                int(numbers[2]),
                genders[0],
            )

        return entries

    def test_every_familiar_appears_in_the_document(self) -> None:
        entries = self.parse_document()

        self.assertEqual(set(entries), set(MASTER.familiars))

    def test_document_stats_match_the_master_data(self) -> None:
        entries = self.parse_document()

        for familiar_id, (hp, atk, speed, gender) in entries.items():
            familiar = MASTER.get_familiar(familiar_id)

            self.assertEqual(hp, familiar.base_hp, familiar_id)
            self.assertEqual(atk, familiar.base_atk, familiar_id)
            self.assertEqual(speed, familiar.speed, familiar_id)
            self.assertEqual(
                gender, GENDER_LABELS.get(familiar.gender), familiar_id
            )


class RoundingTests(unittest.TestCase):
    def test_round_half_up_is_not_bankers_rounding(self) -> None:
        # 組み込みの round(12.5) は12になるため、仕様の四捨五入とは異なる。
        self.assertEqual(round_half_up(12.5), 13)
        self.assertEqual(round_half_up(13.5), 14)
        self.assertEqual(round_half_up(48.3), 48)

    def test_critical_example_from_spec(self) -> None:
        # 18.2節「9ダメージのクリティカルは14ダメージ」
        self.assertEqual(round_half_up(9 * 1.5), 14)


class FamiliarMasterTests(unittest.TestCase):
    def test_registered_counts(self) -> None:
        # BATTLE_RULES.md 10節：S5 + A15 + B20 + C10 = 50体
        self.assertEqual(len(MASTER.familiars), 50)
        self.assertEqual(len(MASTER.familiars_by_rank("S")), 5)
        self.assertEqual(len(MASTER.familiars_by_rank("A")), 15)
        self.assertEqual(len(MASTER.familiars_by_rank("B")), 20)
        self.assertEqual(len(MASTER.familiars_by_rank("C")), 10)

    def test_every_rank_is_registered(self) -> None:
        # 全ランクが登録済みなので、排出率の寄せ替えは発生しない
        self.assertEqual(MASTER.missing_ranks(), [])

    def test_hel_is_a_complete_reward_and_not_in_the_gacha(self) -> None:
        # 11節：ヘルはコンプリート報酬の特別使い魔
        self.assertEqual(
            [familiar.familiar_id for familiar in MASTER.complete_reward_familiars()],
            ["hel"],
        )
        self.assertNotIn(
            "hel",
            [familiar.familiar_id for familiar in MASTER.gacha_familiars_by_rank("S")],
        )
        self.assertEqual(len(MASTER.gacha_familiars_by_rank("S")), 4)

    def test_base_speeds_never_collide(self) -> None:
        # 1節「基礎SPDは原則として全使い魔で重複させない」
        speeds = [familiar.speed for familiar in MASTER.familiars.values()]
        self.assertEqual(len(speeds), len(set(speeds)))

    def test_every_familiar_has_a_registered_gender(self) -> None:
        # 性別は性別条件の効果に影響するため、全個体に登録しておく（BATTLE_RULES.md 10節）。
        for familiar in MASTER.familiars.values():
            self.assertIn(familiar.gender, GENDER_VALUES, familiar.familiar_id)

    def test_both_sexes_exist_so_charm_skills_can_work(self) -> None:
        # 異性条件のスキル（色欲の魔眼・誘惑の歌・夜の誘惑）が成立する前提を守る。
        genders = {familiar.gender for familiar in MASTER.familiars.values()}

        self.assertIn("male", genders)
        self.assertIn("female", genders)

    def test_unset_gender_never_counts_as_opposite(self) -> None:
        self.assertFalse(is_opposite_gender(None, "male"))
        self.assertFalse(is_opposite_gender("none", "female"))
        self.assertTrue(is_opposite_gender("male", "female"))

    def test_known_stats_match_the_document(self) -> None:
        expected = {
            "loki": (46, 8, 84, 5, "S"),
            "hel": (58, 6, 5, 5, "S"),
            "fenrir": (34, 11, 95, 5, "S"),
            "satan": (36, 9, 46, 4, "A"),
            "leviathan": (49, 6, 27, 4, "A"),
            "cyclops": (25, 10, 26, 3, "B"),
            "pegasus": (34, 5, 98, 3, "B"),
            "dark_elf": (14, 8, 83, 2, "C"),
        }

        for familiar_id, (hp, atk, speed, cost, rank) in expected.items():
            familiar = MASTER.get_familiar(familiar_id)
            self.assertIsNotNone(familiar, familiar_id)
            self.assertEqual(
                (familiar.base_hp, familiar.base_atk, familiar.speed, familiar.cost, familiar.rank),
                (hp, atk, speed, cost, rank),
                familiar_id,
            )

    def test_speed_is_within_range(self) -> None:
        for familiar in MASTER.familiars.values():
            self.assertGreaterEqual(familiar.speed, 0, familiar.familiar_id)
            self.assertLessEqual(familiar.speed, 100, familiar.familiar_id)

    def test_gender_values_are_valid_when_registered(self) -> None:
        for familiar in MASTER.familiars.values():
            if familiar.gender is not None:
                self.assertIn(familiar.gender, GENDER_VALUES)


class SkillMasterTests(unittest.TestCase):
    def test_all_referenced_skills_exist(self) -> None:
        for familiar in MASTER.familiars.values():
            for skill_id in familiar.skill_ids:
                self.assertIsNotNone(MASTER.get_skill(skill_id), skill_id)

    def test_active_skill_limit_is_two(self) -> None:
        # 19.2節「使い魔1体は最大2個のアクティブスキルを持てます」
        for familiar in MASTER.familiars.values():
            self.assertLessEqual(
                len(MASTER.active_skills_of(familiar.familiar_id)), 2, familiar.familiar_id
            )

    def test_passive_triggers_are_supported(self) -> None:
        for skill in MASTER.skills.values():
            if skill.skill_type == SKILL_TYPE_PASSIVE:
                self.assertIn(skill.trigger, TRIGGERS, skill.skill_id)
            else:
                self.assertEqual(skill.skill_type, SKILL_TYPE_ACTIVE)
                self.assertIsNone(skill.trigger, skill.skill_id)

    def test_unlimited_actives_are_declared_without_a_limit(self) -> None:
        # BATTLE_RULES.md：「回数制限なし」と書かれたACTIVEだけ上限を持たない
        unlimited = {
            skill.skill_id
            for skill in MASTER.skills.values()
            if skill.is_active and skill.max_uses_per_battle is None
        }
        self.assertEqual(
            unlimited,
            {
                "loki_active",
                "mammon_active",
                "abaddon_active",
                "kyubi_active",
                "yuki_onna_active",
                "azazel_active",
            },
        )

        for skill in MASTER.skills.values():
            if skill.is_active and skill.max_uses_per_battle is not None:
                self.assertEqual(skill.max_uses_per_battle, 1, skill.skill_id)

    def test_passive_extra_triggers_are_supported(self) -> None:
        for skill in MASTER.skills.values():
            for trigger in skill.triggers:
                self.assertIn(trigger, TRIGGERS, skill.skill_id)

    def test_selection_targets_are_declared(self) -> None:
        for skill in MASTER.skills.values():
            keys = {group.key for group in skill.targets}
            for effect in skill.effects:
                if effect.target_type.startswith("selection:"):
                    self.assertIn(effect.target_type.split(":", 1)[1], keys, skill.skill_id)


class GachaMasterTests(unittest.TestCase):
    def test_standard_pool_matches_the_spec(self) -> None:
        pool = MASTER.gacha_pools["standard"]

        # 10.2節：単発30,000 coin、10回270,000 coin（1回分の割引あり）
        self.assertEqual(pool.single_cost, 30_000)
        self.assertEqual(pool.multi_cost, 270_000)
        self.assertEqual(pool.multi_count, 10)
        self.assertEqual(pool.single_cost * (pool.multi_count - 1), pool.multi_cost)

        # 通常枠 C45% B45% A9% S1%
        self.assertEqual(pool.rates["normal"], {"C": 450, "B": 450, "A": 90, "S": 10})
        # 10枠目の保証 A90% S10%
        self.assertEqual(pool.rates["guaranteed"], {"A": 900, "S": 100})
        self.assertEqual(pool.guaranteed_slot, 10)

    def test_rates_sum_to_one_thousand(self) -> None:
        for pool in MASTER.gacha_pools.values():
            for slot_type, weights in pool.rates.items():
                self.assertEqual(sum(weights.values()), 1000, (pool.pool_id, slot_type))


class GrowthTests(unittest.TestCase):
    def test_hp_and_atk_growth(self) -> None:
        # 10.2節：基本値 × (1 + 0.05 × (レベル - 1))、小数は四捨五入
        stats = MASTER.level_stats("loki", 10)
        self.assertEqual(stats.max_hp, round_half_up(46 * 1.45))
        self.assertEqual(stats.atk, round_half_up(8 * 1.45))

    def test_speed_grows_on_even_levels_only(self) -> None:
        speeds = [MASTER.level_stats("loki", level).speed for level in range(1, 11)]
        self.assertEqual(speeds, [84, 85, 85, 86, 86, 87, 87, 88, 88, 89])

    def test_speed_never_exceeds_the_maximum(self) -> None:
        # 素のSPDが95のフェンリルは、Lv.10でも100を超えない。
        self.assertEqual(MASTER.level_stats("fenrir", 10).speed, 100)

    def test_initial_level_keeps_base_values(self) -> None:
        # 初期レベルはLv.1で、そこが基本値になる（10.2節）。
        self.assertEqual(MASTER.familiar.min_level, 1)
        self.assertEqual(MASTER.familiar.max_level, 10)

        for familiar in MASTER.familiars.values():
            stats = MASTER.level_stats(familiar.familiar_id, MASTER.familiar.min_level)
            self.assertEqual(stats.max_hp, familiar.base_hp)
            self.assertEqual(stats.atk, familiar.base_atk)
            self.assertEqual(stats.speed, familiar.speed)

    def test_compute_level_stats_is_pure(self) -> None:
        stats = compute_level_stats(
            32, 8, 64, 4,
            hp_rate=0.05, atk_rate=0.05,
            speed_levels=(2, 4, 6, 8, 10), speed_value=1, speed_max=100,
        )
        self.assertEqual((stats.max_hp, stats.atk, stats.speed), (38, 10, 66))


class SellPriceTests(unittest.TestCase):
    def test_base_prices(self) -> None:
        # 10.2節：Cランク2,000、Bランク5,000、Aランク15,000、Sランク50,000
        # 初期レベル（Lv.1）では基準価格そのまま。
        self.assertEqual(MASTER.sell_price("C", 1), 2_000)
        self.assertEqual(MASTER.sell_price("B", 1), 5_000)
        self.assertEqual(MASTER.sell_price("A", 1), 15_000)
        self.assertEqual(MASTER.sell_price("S", 1), 50_000)

    def test_level_multiplies_the_price(self) -> None:
        # 基準価格 × レベル
        self.assertEqual(MASTER.sell_price("S", 3), 150_000)
        self.assertEqual(MASTER.sell_price("B", 10), 50_000)


class UsableRankTests(unittest.TestCase):
    def test_players_can_use_one_rank_above(self) -> None:
        # 10.4節の表
        self.assertEqual(MASTER.usable_ranks("C"), ["C", "B"])
        self.assertEqual(MASTER.usable_ranks("B"), ["C", "B", "A"])
        self.assertEqual(MASTER.usable_ranks("A"), ["C", "B", "A", "S"])
        self.assertEqual(MASTER.usable_ranks("S"), ["C", "B", "A", "S"])

    def test_sub_manager_can_use_every_rank(self) -> None:
        # 七星はプレイヤーランクに関係なくC～Sすべてを使用できる
        self.assertEqual(
            MASTER.usable_ranks("B", is_sub_manager=True), ["C", "B", "A", "S"]
        )
        self.assertTrue(MASTER.can_use_rank("B", "S", is_sub_manager=True))
        self.assertFalse(MASTER.can_use_rank("B", "S"))

    def test_unknown_rank_can_use_nothing(self) -> None:
        # ランクを確認できない場合は推測で補わない（27節）。
        self.assertEqual(MASTER.usable_ranks(None), [])
        self.assertFalse(MASTER.can_use_rank(None, "C"))


if __name__ == "__main__":
    unittest.main()
