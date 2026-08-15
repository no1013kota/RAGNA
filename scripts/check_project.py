"""依存パッケージなしで実行できる、デプロイ前の簡易チェック。"""

from __future__ import annotations

import ast
import json
import re
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_FILES = (
    ".env.example",
    "bot.py",
    "config.py",
    "database.py",
    "discord_settings.py",
    "schema.sql",
    "requirements.txt",
    "railway.json",
)


def check_required_files(errors: list[str]) -> None:
    """Railway起動に必要なファイルがそろっているか確認する。"""

    for relative_path in REQUIRED_FILES:
        if not (ROOT / relative_path).is_file():
            errors.append(f"必須ファイルがありません: {relative_path}")


def check_python_syntax(errors: list[str]) -> None:
    """全Pythonファイルを構文解析し、起動前に文法エラーを見つける。"""

    for path in sorted(ROOT.rglob("*.py")):
        if any(part in {".git", ".venv", "venv"} for part in path.parts):
            continue
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, UnicodeError) as exc:
            errors.append(f"Python構文エラー: {path.relative_to(ROOT)}: {exc}")


def check_configuration(errors: list[str]) -> None:
    """Railway設定と秘密情報の基本的な事故を検査する。"""

    railway_path = ROOT / "railway.json"
    if railway_path.exists():
        try:
            railway = json.loads(railway_path.read_text(encoding="utf-8"))
            start_command = railway.get("deploy", {}).get("startCommand")
            if start_command != "python -u bot.py":
                errors.append("railway.jsonのstartCommandが想定と異なります")
        except (json.JSONDecodeError, UnicodeError) as exc:
            errors.append(f"railway.jsonを読み込めません: {exc}")

    env_example_path = ROOT / ".env.example"
    if env_example_path.exists():
        env_example = env_example_path.read_text(encoding="utf-8")
        for variable in ("DISCORD_BOT_TOKEN", "DISCORD_GUILD_ID"):
            if variable not in env_example:
                errors.append(f".env.exampleに{variable}がありません")

    # PythonコードにToken文字列を直接代入していないかを確認する。
    hardcoded_token = re.compile(r"^\s*TOKEN\s*=\s*['\"][^'\"]+['\"]", re.MULTILINE)
    for path in sorted(ROOT.rglob("*.py")):
        if hardcoded_token.search(path.read_text(encoding="utf-8")):
            errors.append(f"Tokenらしき直接代入があります: {path.relative_to(ROOT)}")


def check_database(errors: list[str]) -> None:
    """同梱されているSQLite DBがある場合だけ整合性を確認する。"""

    database_path = ROOT / "data" / "ragna.db"
    if not database_path.exists():
        return

    try:
        connection = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
        try:
            result = connection.execute("PRAGMA integrity_check").fetchone()
        finally:
            connection.close()
        if result is None or result[0] != "ok":
            errors.append("data/ragna.dbの整合性確認に失敗しました")
    except sqlite3.Error as exc:
        errors.append(f"data/ragna.dbを確認できません: {exc}")


def main() -> int:
    errors: list[str] = []
    check_required_files(errors)
    check_python_syntax(errors)
    check_configuration(errors)
    check_database(errors)

    if errors:
        print("デプロイ前チェック: NG")
        for error in errors:
            print(f"- {error}")
        return 1

    print("デプロイ前チェック: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
