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
    "database/__init__.py",
    "database/core.py",
    "discord_settings.py",
    "schema.sql",
    "requirements.txt",
    "railway.json",
    "data/master/balance.json",
    "data/master/familiars.json",
    "data/master/skills.json",
    "data/master/gacha.json",
    "game/models.py",
    "game/master_data.py",
    "game/battle_engine.py",
)
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
# service.py まで求める、RAGNA Onlineの機能パッケージ
GAME_FEATURE_PACKAGES = (
    "guild",
    "familiar",
    "guild_battle",
)


def check_required_files(errors: list[str]) -> None:
    """Railway起動に必要なファイルがそろっているか確認する。"""

    for relative_path in REQUIRED_FILES:
        if not (ROOT / relative_path).is_file():
            errors.append(f"必須ファイルがありません: {relative_path}")


def check_feature_packages(errors: list[str]) -> None:
    """大きな機能がCogとDiscord UIに分離されているか確認する。"""

    for package_name in FEATURE_PACKAGES:
        for filename in ("__init__.py", "cog.py", "views.py"):
            relative_path = Path("cogs") / package_name / filename
            if not (ROOT / relative_path).is_file():
                errors.append(f"機能パッケージのファイルがありません: {relative_path}")

    for package_name in GAME_FEATURE_PACKAGES:
        relative_path = Path("cogs") / package_name / "service.py"
        if not (ROOT / relative_path).is_file():
            errors.append(f"機能パッケージのファイルがありません: {relative_path}")


def check_master_data(errors: list[str]) -> None:
    """ゲームのマスターデータが読み込める状態か確認する。"""

    for name in ("balance.json", "familiars.json", "skills.json", "gacha.json"):
        path = ROOT / "data" / "master" / name
        if not path.is_file():
            continue

        try:
            json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeError) as exc:
            errors.append(f"マスターデータを読み込めません: data/master/{name}: {exc}")

    gacha_path = ROOT / "data" / "master" / "gacha.json"
    if not gacha_path.is_file():
        return

    try:
        gacha = json.loads(gacha_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeError):
        return

    for pool in gacha.get("pools", []):
        for slot_type, weights in (pool.get("rates") or {}).items():
            total = sum(weights.values())
            if total != 1000:
                errors.append(
                    f"ガチャ排出率の合計が1000ではありません: "
                    f"{pool.get('pool_id')}/{slot_type} = {total}"
                )


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
    check_feature_packages(errors)
    check_python_syntax(errors)
    check_configuration(errors)
    check_master_data(errors)
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
