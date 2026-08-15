"""機能分割でDiscordの公開入口が変わっていないことを確認するテスト。"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

FEATURE_PACKAGES = ("coin", "hotel", "member", "ticket", "trial_member")
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
EXPECTED_SLASH_COMMANDS = {
    "ban",
    "xp確認",
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

    def test_bot_keeps_the_same_extension_names(self) -> None:
        tree = ast.parse((ROOT / "bot.py").read_text(encoding="utf-8"))
        extensions = None
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "EXTENSIONS"
                for target in node.targets
            ):
                extensions = ast.literal_eval(node.value)
                break

        self.assertEqual(extensions, EXPECTED_EXTENSIONS)

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
        ):
            self.assertTrue((ROOT / "database" / f"{module_name}.py").is_file())


if __name__ == "__main__":
    unittest.main()
