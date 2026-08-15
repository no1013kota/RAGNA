"""共通表示処理のテスト。"""

import unittest

from utils import format_time


class FormatTimeTests(unittest.TestCase):
    def test_minutes_only(self) -> None:
        self.assertEqual(format_time(45), "45分")

    def test_hours_only(self) -> None:
        self.assertEqual(format_time(120), "2時間")

    def test_hours_and_minutes(self) -> None:
        self.assertEqual(format_time(125), "2時間5分")


if __name__ == "__main__":
    unittest.main()
