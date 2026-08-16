"""機能分割でDiscordの公開入口が変わっていないことを確認するテスト。"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

FEATURE_PACKAGES = (
    "coin",
    "hotel",
    "member",
    "ticket",
    "trial_member",
    "guild",
    "familiar",
    "guild_battle",
)
# RAGNA Onlineの機能パッケージは service.py まで持つ
GAME_FEATURE_PACKAGES = ("guild", "familiar", "guild_battle")
EXPECTED_EXTENSIONS = (
    "cogs.coin",
    "cogs.trial_member",
    "cogs.xp",
    "cogs.member",
    "cogs.hotel",
    "cogs.ranking",
    "cogs.class_change",
    "cogs.ticket",
    "cogs.introduction",
)
EXPECTED_GAME_EXTENSIONS = (
    "cogs.guild",
    "cogs.familiar",
    "cogs.guild_battle",
)
EXPECTED_SLASH_COMMANDS = {
    "ban",
    "xp確認",
    "バトル中止",
    "バトル再開",
    "クラス分布",
    "クラス変更",
    "メンバー確認",
    "ランキング",
    "召喚",
    "招待報酬",
    "残高変更",
    "評価状況",
    "転生",
}
EXPECTED_PERSISTENT_CUSTOM_IDS = {
    "atm:balance",
    "atm:transfer",
    "comment_user_select",
    "evaluation_panel:comment",
    "evaluation_panel:evaluate",
    "evaluation_panel:target_list",
    "evaluation_user_select",
    "familiar:fuse",
    "familiar:gacha_multi",
    "familiar:gacha_single",
    "familiar:list",
    "familiar:sell",
    "guild:create",
    "guild:description",
    "guild:disband",
    "guild:expand",
    "guild:info",
    "guild:join_request",
    "guild:kick",
    "guild:leave",
    "guild:my_requests",
    "guild:recruit",
    "guild:rename",
    "guild:request_approve",
    "guild:request_reject",
    "guild:transfer",
    "guild_battle:attack",
    "guild_battle:check_roster",
    "guild_battle:ranking",
    "guild_battle:recruit",
    "guild_battle:recruit_apply",
    "guild_battle:request",
    "guild_battle:request_approve",
    "guild_battle:request_reject",
    "guild_battle:set_familiar",
    "guild_battle:set_members",
    "guild_battle:skill",
    "guild_battle:surrender",
    "hotel:limit",
    "hotel:plan",
    "hotel:rename",
    "hotel:status",
    "hotel_premium:close",
    "hotel_premium:deny",
    "hotel_premium:invite",
    "hotel_premium:open",
    "hotel_premium:share",
    "hotel_secret:deny",
    "hotel_secret:invite",
    "invite:check",
    "invite:use",
    "ticket:close",
    "ticket:manager_delete",
    "ticket:manager_reopen",
    "ticket:manager_save",
    "ticket:open",
    "ticket:staff_delete",
    "ticket:staff_reopen",
    "trial_member_end_survey:good_evaluator",
    "trial_member_end_survey:skip",
}


def read_all_cog_source() -> str:
    """Cog配下のPythonソースを検査用にまとめる。"""

    return "\n".join(
        path.read_text(encoding="utf-8") for path in sorted((ROOT / "cogs").rglob("*.py"))
    )


class ProjectStructureTests(unittest.TestCase):
    def test_large_features_have_cog_and_view_boundaries(self) -> None:
        for package_name in FEATURE_PACKAGES:
            package = ROOT / "cogs" / package_name
            self.assertTrue((package / "__init__.py").is_file())
            self.assertTrue((package / "cog.py").is_file())
            self.assertTrue((package / "views.py").is_file())
            self.assertIn(
                "from .cog import setup",
                (package / "__init__.py").read_text(encoding="utf-8"),
            )

    def test_game_features_have_a_service_layer(self) -> None:
        for package_name in GAME_FEATURE_PACKAGES:
            service_path = ROOT / "cogs" / package_name / "service.py"
            self.assertTrue(service_path.is_file(), package_name)

    def test_bot_keeps_the_same_extension_names(self) -> None:
        assignments = self.read_bot_assignments()

        self.assertEqual(assignments.get("EXTENSIONS"), EXPECTED_EXTENSIONS)

    def test_game_extensions_are_loaded_separately(self) -> None:
        # マスターデータを読めない場合でも既存機能を止めないため、
        # ゲーム機能は別のタプルに分けて読み込む。
        assignments = self.read_bot_assignments()

        self.assertEqual(assignments.get("GAME_EXTENSIONS"), EXPECTED_GAME_EXTENSIONS)

    def read_bot_assignments(self) -> dict[str, object]:
        tree = ast.parse((ROOT / "bot.py").read_text(encoding="utf-8"))
        found: dict[str, object] = {}

        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue

            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in {
                    "EXTENSIONS",
                    "GAME_EXTENSIONS",
                }:
                    found[target.id] = ast.literal_eval(node.value)

        return found

    def test_slash_command_names_remain_stable(self) -> None:
        source = read_all_cog_source()
        command_names = set(
            re.findall(r'@app_commands\.command\([^)]*?name\s*=\s*"([^"]+)"', source)
        )

        self.assertEqual(command_names, EXPECTED_SLASH_COMMANDS)

    def test_persistent_component_ids_remain_stable(self) -> None:
        source = read_all_cog_source()
        custom_ids = set(re.findall(r'custom_id\s*=\s*"([^"]+)"', source))

        self.assertEqual(custom_ids, EXPECTED_PERSISTENT_CUSTOM_IDS)

    def test_database_has_feature_specific_public_modules(self) -> None:
        for module_name in (
            "connection",
            "coin",
            "member",
            "trial_member",
            "xp",
            "ranking",
            "hotel",
            "ticket",
            "guild",
            "familiar",
            "battle",
            "player_rank",
        ):
            self.assertTrue((ROOT / "database" / f"{module_name}.py").is_file())

    def test_game_layer_does_not_depend_on_discord(self) -> None:
        """戦闘計算をDiscordなしでテストできる状態を保つ（32.8節）。"""

        # battle_embed.py は表示専用のため唯一の例外。
        allowed_to_import_discord = {"battle_embed.py"}

        for path in sorted((ROOT / "game").glob("*.py")):
            if path.name in allowed_to_import_discord:
                continue

            source = path.read_text(encoding="utf-8")
            self.assertNotIn("import discord", source, path.name)
            self.assertNotIn("import config", source, path.name)

    def test_game_cogs_do_not_run_sql_directly(self) -> None:
        """SQLはdatabase層に閉じ込める（設計・開発ガイドの変更時のルール）。"""

        for package_name in GAME_FEATURE_PACKAGES:
            for path in sorted((ROOT / "cogs" / package_name).glob("*.py")):
                source = path.read_text(encoding="utf-8")
                self.assertNotIn("get_connection", source, str(path))
                self.assertNotIn("sqlite3", source, str(path))

    def test_master_data_files_exist(self) -> None:
        for name in ("balance.json", "familiars.json", "skills.json", "gacha.json"):
            self.assertTrue((ROOT / "data" / "master" / name).is_file(), name)


if __name__ == "__main__":
    unittest.main()
