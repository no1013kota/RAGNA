"""RAGNA Onlineのプレイヤーランク（Discordロール同期情報）データアクセス層。

プレイヤーランクの正式な参照元は ``player_roles`` テーブルです（GAME_SPEC 26節）。
Discord上の表示をゲーム処理の判定材料にせず、Bot起動時・メンバー参加時・ロール更新時に
Discordの状態をこのテーブルへ同期し、ゲーム処理は常にここから読み取ります。

- 騎士（``ROLE_MEMBER``）・七星（``ROLE_SUB_MANAGER``）・運営（``ROLE_MANAGER``）の
  いずれかを持つプレイヤーがギルドを設立できます（GAME_SPEC 4節）。
- 七星は特権としてプレイヤーランクに関係なくC～Sの使い魔を使用できます（GAME_SPEC 10.4節）。

CogやViewからSQLを直接書かず、このモジュールの公開APIだけを利用してください。
"""

from __future__ import annotations

import logging
import sqlite3

from contextlib import closing
from datetime import datetime, timezone
from typing import Any

from .core import get_connection


logger = logging.getLogger(__name__)

# ==================================================
# 共通定義
# ==================================================

# schema.sql の CHECK 制約と同じ値。ここに無い値はランク不明として NULL で保存する。
VALID_PLAYER_RANKS = ("S", "A", "B", "C")

# player_roles は user_id 主キー。同期はすべてこのUPSERTで行う。
_UPSERT_PLAYER_ROLE_SQL = """
    INSERT INTO player_roles
        (user_id, player_rank, is_manager, is_sub_manager, is_member, synced_at)
    VALUES
        (?, ?, ?, ?, ?, ?)
    ON CONFLICT(user_id)
    DO UPDATE SET
        player_rank = excluded.player_rank,
        is_manager = excluded.is_manager,
        is_sub_manager = excluded.is_sub_manager,
        is_member = excluded.is_member,
        synced_at = excluded.synced_at
"""


def _now() -> str:
    """UTCのISO 8601文字列を返す。"""

    return datetime.now(timezone.utc).isoformat()


def _normalize_rank(player_rank: str | None) -> str | None:
    """プレイヤーランクを 'S'/'A'/'B'/'C' もしくは None へ正規化する。

    想定外の値をそのまま保存するとCHECK制約違反で同期全体が失敗するため、
    推測でランクを補わずランク不明（NULL）として保存し、警告ログを残します。
    """

    if player_rank is None:
        return None

    normalized = str(player_rank).strip().upper()
    if normalized in VALID_PLAYER_RANKS:
        return normalized

    if normalized:
        logger.warning(f"未知のプレイヤーランクを無視しました: {player_rank!r}")
    return None


def _to_flag(value: Any) -> int:
    """真偽値を SQLite 用の 0 / 1 へ変換する。"""

    return 1 if value else 0


# ==================================================
# 同期（更新系）
# ==================================================
def sync_player_rank(
    user_id: int,
    *,
    player_rank: str | None,
    is_manager: bool,
    is_sub_manager: bool,
    is_member: bool,
) -> None:
    """1人分のDiscordロール情報を player_roles へ反映する。

    メンバー参加時やロール更新時に呼び出します。既存行があれば上書きします。
    """

    values = (
        int(user_id),
        _normalize_rank(player_rank),
        _to_flag(is_manager),
        _to_flag(is_sub_manager),
        _to_flag(is_member),
        _now(),
    )

    with closing(get_connection()) as conn:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(_UPSERT_PLAYER_ROLE_SQL, values)


def sync_player_ranks(records: list[dict]) -> int:
    """複数人分のDiscordロール情報を1トランザクションで一括同期する。

    ``records`` の各要素は ``sync_player_rank`` と同じキー＋ ``"user_id"`` です。
    Bot起動時の全メンバー同期で使い、戻り値は更新件数です。
    """

    if not records:
        return 0

    synced_at = _now()
    values = [
        (
            int(record["user_id"]),
            _normalize_rank(record.get("player_rank")),
            _to_flag(record.get("is_manager")),
            _to_flag(record.get("is_sub_manager")),
            _to_flag(record.get("is_member")),
            synced_at,
        )
        for record in records
    ]

    with closing(get_connection()) as conn:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.executemany(_UPSERT_PLAYER_ROLE_SQL, values)
            updated = cursor.rowcount

    # rowcount が取得できない環境では入力件数を返す。
    if updated < 0:
        updated = len(values)
    return updated


def delete_player_rank(user_id: int) -> None:
    """退出したメンバーのロール同期情報を削除する。"""

    with closing(get_connection()) as conn:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                DELETE FROM player_roles
                WHERE user_id = ?
                """,
                (int(user_id),),
            )


# ==================================================
# 参照
# ==================================================
def get_player_rank_record(user_id: int) -> dict | None:
    """player_roles の1行を返す。行が無ければ None。

    戻り値のキーは
    ``{"user_id", "player_rank", "is_manager", "is_sub_manager", "is_member", "synced_at"}``
    で、フラグ列は 0 / 1 の整数です。
    """

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT
                user_id,
                player_rank,
                is_manager,
                is_sub_manager,
                is_member,
                synced_at
            FROM player_roles
            WHERE user_id = ?
            """,
            (int(user_id),),
        ).fetchone()

    if row is None:
        return None
    return dict(row)


def get_player_rank(user_id: int) -> str | None:
    """プレイヤーランク（'S'/'A'/'B'/'C'）を返す。

    行が無い場合とランク未設定の場合はどちらも None を返します。呼び出し側は
    ランク不明として扱い、推測で補わずに操作を停止してください（GAME_SPEC 26節）。
    """

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT player_rank
            FROM player_roles
            WHERE user_id = ?
            """,
            (int(user_id),),
        ).fetchone()

    if row is None:
        return None
    return row["player_rank"]


def is_sub_manager(user_id: int) -> bool:
    """七星（準運営）かどうかを返す。使い魔の使役可能ランク特権の判定に使う。"""

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT is_sub_manager
            FROM player_roles
            WHERE user_id = ?
            """,
            (int(user_id),),
        ).fetchone()

    if row is None:
        return False
    return bool(row["is_sub_manager"])


def can_found_guild(user_id: int) -> bool:
    """ギルドを設立できるかどうかを返す。

    騎士・七星・運営のいずれかのDiscordロールを持つプレイヤーが設立できます
    （GAME_SPEC 4節）。同期情報が無いユーザーは False です。
    """

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT
                is_manager,
                is_sub_manager,
                is_member
            FROM player_roles
            WHERE user_id = ?
            """,
            (int(user_id),),
        ).fetchone()

    if row is None:
        return False
    return bool(row["is_member"] or row["is_sub_manager"] or row["is_manager"])
