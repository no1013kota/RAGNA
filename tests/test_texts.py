"""表示文言が ``texts/`` に集まっている状態を保つためのテスト。

文言を直接いじれるようにするのがこのパッケージの目的なので、
Cog側へ日本語を書き戻してしまうと意味がなくなります。
新しい文言を足すときは ``texts/`` へ足してください。
"""

from __future__ import annotations

import importlib
import pkgutil
import re
import unittest

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEXTS = ROOT / "texts"

# 文言を置いてよい場所。ここ以外に日本語の表示文を増やさないための目印。
JAPANESE = re.compile(r"[ぁ-んァ-ヶ一-龠]")


def text_modules() -> list[str]:
    return sorted(
        module.name
        for module in pkgutil.iter_modules([str(TEXTS)])
        if not module.name.startswith("_")
    )


class PackageLayoutTests(unittest.TestCase):
    """``texts/`` そのものの体裁を確認する。"""

    def test_every_module_can_be_imported(self) -> None:
        for name in text_modules():
            importlib.import_module(f"texts.{name}")

    def test_every_module_explains_itself(self) -> None:
        """先頭の説明文が無いと、どのファイルを開けばよいか分からなくなる。"""

        for name in text_modules():
            module = importlib.import_module(f"texts.{name}")
            self.assertTrue((module.__doc__ or "").strip(), name)

    def test_the_index_lists_every_module(self) -> None:
        """``texts/__init__.py`` の目次と、実際のファイルをそろえる。"""

        index = importlib.import_module("texts").__doc__ or ""

        for name in text_modules():
            self.assertIn(f"``{name}.py``", index, name)

    def test_the_index_explains_how_to_edit(self) -> None:
        index = importlib.import_module("texts").__doc__ or ""

        self.assertIn("編集するときのルール", index)

    def test_constants_are_upper_case(self) -> None:
        """定数は大文字。小文字だと関数や変数と見分けが付かなくなる。"""

        for name in text_modules():
            module = importlib.import_module(f"texts.{name}")
            for attribute in vars(module):
                if attribute.startswith("_"):
                    continue
                self.assertEqual(attribute, attribute.upper(), f"{name}.{attribute}")


class PanelTitleTests(unittest.TestCase):
    """常設パネルの表題は ``texts/panels.py`` だけに書く。"""

    def test_no_cog_writes_a_panel_title_inline(self) -> None:
        offenders: list[str] = []

        for path in (ROOT / "cogs").rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            for match in re.finditer(r'panel_title="([^"]*)"', source):
                if JAPANESE.search(match.group(1)):
                    offenders.append(f"{path.relative_to(ROOT)}: {match.group(1)}")

        self.assertEqual(
            offenders,
            [],
            "パネルの表題は texts/panels.py に置いて、定数で渡してください",
        )

    def test_config_reuses_the_panel_titles(self) -> None:
        """並べ替えてよいパネルの一覧が、表題を二重に持たないようにする。"""

        source = (ROOT / "config.py").read_text(encoding="utf-8")
        block = source[source.index("ATM_PANEL_CHANNELS = {") :]
        block = block[: block.index("\n}\n")]

        self.assertNotRegex(block, r'"[^"]*[ぁ-んァ-ヶ一-龠]')


if __name__ == "__main__":
    unittest.main()
