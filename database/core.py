"""SQLiteの接続・初期化・マイグレーションの共通実装。

保存先の決定（Railway Volumeを含む）、取り込み用DBの受け入れ、``get_connection`` の提供、
schema.sqlの適用と後から追加した列の補完までを担当します。機能ごとのSQLはここには置かず、
``database.coin`` や ``database.hotel`` など、担当領域のモジュールに実装してください。

``DB_PATH`` はこのモジュールのグローバルとして参照されます。他モジュールは値をコピーせず、
実行時にここを読む ``get_connection`` を経由してください。
"""

import logging
import os
import shutil
import sqlite3

from pathlib import Path


logger = logging.getLogger(__name__)

# ==================================================
# パス設定
# ==================================================

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = PROJECT_ROOT / "schema.sql"


def _resolve_database_path() -> Path:
    configured_path = os.getenv("DATABASE_PATH", "").strip()
    if configured_path:
        return Path(configured_path).expanduser()

    railway_volume_path = os.getenv("RAILWAY_VOLUME_MOUNT_PATH", "").strip()
    if railway_volume_path:
        return Path(railway_volume_path) / "ragna.db"

    return PROJECT_ROOT / "data" / "ragna.db"


DB_PATH = _resolve_database_path()
DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def _validate_database(path: Path) -> None:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        result = connection.execute("PRAGMA integrity_check").fetchone()
    finally:
        connection.close()

    if result is None or result[0] != "ok":
        raise RuntimeError(f"SQLiteデータベースの整合性確認に失敗しました: {path}")


def _import_database_if_present() -> None:
    import_path_value = os.getenv("DATABASE_IMPORT_PATH", "").strip()
    import_path = (
        Path(import_path_value).expanduser()
        if import_path_value
        else Path(f"{DB_PATH}.import")
    )

    if not import_path.exists():
        return

    if import_path.resolve() == DB_PATH.resolve():
        raise RuntimeError("DATABASE_IMPORT_PATH は DATABASE_PATH と別の場所にしてください。")

    _validate_database(import_path)

    if DB_PATH.exists():
        current_connection = sqlite3.connect(DB_PATH, timeout=30)
        try:
            current_connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            current_connection.close()

        backup_path = Path(f"{DB_PATH}.before-import")
        shutil.copy2(DB_PATH, backup_path)

    for suffix in ("-wal", "-shm"):
        sidecar = Path(f"{DB_PATH}{suffix}")
        if sidecar.exists():
            sidecar.unlink()

    os.replace(import_path, DB_PATH)
    logger.info(f"既存データベースを取り込みました: {DB_PATH}")


def get_connection():
    connection = sqlite3.connect(DB_PATH, timeout=30)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 30000")
    return connection


# ==================================================
# 列の追加
# CREATE TABLE IF NOT EXISTS では既存テーブルへ列を足せないため、
# 後から追加した列だけをここで補う
# ==================================================
ADDED_COLUMNS = (
    # (テーブル名, 列名, 列定義)
    ("guild_battles", "guild_a_channel_id", "INTEGER"),
    ("guild_battles", "guild_b_channel_id", "INTEGER"),
    ("guild_battles", "channels_deleted_at", "TEXT"),
    ("guild_battle_members", "familiar_count", "INTEGER NOT NULL DEFAULT 0"),
    ("guild_battle_units", "base_speed", "INTEGER NOT NULL DEFAULT 0"),
    ("guild_battles", "bet_coin", "INTEGER"),
    ("guild_battle_requests", "bet_coin", "INTEGER"),
    ("guild_battle_recruitments", "bet_coin", "INTEGER"),
    ("guilds", "info_channel_id", "INTEGER"),
)


# 既存データの整合を取るための一度きりの補正。
# (SQL, 説明) の順。何度実行しても結果が変わらない文だけを置くこと。
DATA_FIXES = (
    (
        "UPDATE player_familiars SET level = 1 WHERE level < 1",
        "使い魔の初期レベルを1に統一",
    ),
)


def _apply_data_fixes(conn) -> None:
    """既存データを現在の仕様に合わせて補正する。"""

    for sql, description in DATA_FIXES:
        table = sql.split()[1]
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
        if exists is None:
            continue

        changed = conn.execute(sql).rowcount
        if changed > 0:
            logger.info(f"データを補正しました: {description}（{changed}件）")


def _apply_added_columns(conn) -> None:
    """既存テーブルに不足している列を追加する。"""

    for table, column, definition in ADDED_COLUMNS:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
        if exists is None:
            continue

        columns = {
            row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column in columns:
            continue

        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        logger.info(f"列を追加しました: {table}.{column}")


# ==================================================
# DB初期化
# schema.sqlを読み込んでテーブルを自動生成する
# ==================================================
def init_database():

    _import_database_if_present()

    conn = get_connection()
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")

    logger.info(f"DB初期化開始: {DB_PATH}")
    with open(
        SCHEMA_PATH,
        "r",
        encoding="utf-8"
    ) as file:

        schema = file.read()

    # schema.sqlのインデックスが後から追加した列を参照するため、
    # スキーマ適用より先に不足している列を補う。
    _apply_added_columns(conn)
    conn.commit()

    conn.executescript(schema)
    conn.commit()

    _apply_data_fixes(conn)
    conn.commit()
    conn.close()
    logger.info("DB初期化完了")
