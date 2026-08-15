"""Intentの設定変更で既存機能が停止しないことを確認するテスト。"""

import unittest

from discord_settings import REQUIRED_INTENT_NAMES, create_intents


class DiscordIntentTests(unittest.TestCase):
    def test_only_required_intents_are_enabled(self) -> None:
        intents = create_intents()
        enabled = {name for name, value in intents if value}

        self.assertEqual(enabled, set(REQUIRED_INTENT_NAMES))

    def test_privileged_intents_required_by_features_are_enabled(self) -> None:
        intents = create_intents()

        self.assertTrue(intents.members)
        self.assertTrue(intents.message_content)
        self.assertFalse(intents.presences)


if __name__ == "__main__":
    unittest.main()
