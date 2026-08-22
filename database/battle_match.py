"""バトル占有ロック・バトル申請・公開バトル募集・バトル作成のSQL実装。

対戦相手が決まるまで（GAME_SPEC 12.1節・12.2節・13節）と、成立した対戦から
バトル行を作るところ（14節）までを担当します。

設計上の要点（docs/GAME_SPEC.md 12・13・14・27節）:

- ``guild_battle_locks`` はギルドIDが主キーです。申請・募集・進行中バトルを
  合わせて1ギルド1件に制限し、二重参加を防ぎます（12.1節・27節）。
- 募集への同時申込みは ``claim_battle_recruitment`` の ``BEGIN IMMEDIATE`` +
  ``status='open'`` 条件付きUPDATEで、最初の1件だけを成立させます（12.2節）。
- バトル作成は、ロック取得・バトル行の作成・出場ユニットの複製を同じ
  トランザクションで行い、途中状態を残しません（13節・14節）。
"""

from __future__ import annotations

import logging
import sqlite3

from contextlib import closing
from typing import Any

from .battle_common import (
    ACTIVE_BATTLE_STATUSES,
    dump_json,
    error_result,
    now_iso,
    ok_result,
    row_to_dict,
)
from .core import get_connection


logger = logging.getLogger(__name__)


# ==================================================
# バトル占有ロック（12.1節・27節）
# ==================================================
def acquire_battle_lock(
    conn: sqlite3.Connection,
    guild_id: int,
    lock_type: str,
    reference_id: int,
) -> bool:
    """同一トランザクション内でギルドの占有ロックを取得する。

    既にロックがある場合は何もせず ``False`` を返します。呼び出し側は
    ``BEGIN IMMEDIATE`` を開始済みの接続を渡してください。
    """

    inserted = conn.execute(
        """
        INSERT OR IGNORE INTO guild_battle_locks
            (guild_id, lock_type, reference_id, created_at)
        VALUES
            (?, ?, ?, ?)
        """,
        (guild_id, lock_type, reference_id, now_iso()),
    )

    return inserted.rowcount > 0


def get_battle_lock(guild_id: int) -> dict[str, Any] | None:
    """ギルドが保持している占有ロックを返す。"""

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT *
            FROM guild_battle_locks
            WHERE guild_id = ?
            """,
            (guild_id,),
        ).fetchone()

    return row_to_dict(row)


def release_battle_lock(guild_id: int) -> None:
    """ギルドの占有ロックを解放する。"""

    with closing(get_connection()) as conn:
        with conn:
            conn.execute(
                """
                DELETE FROM guild_battle_locks
                WHERE guild_id = ?
                """,
                (guild_id,),
            )


# ==================================================
# バトル申請（12.1節）
# ==================================================
def create_battle_request(
    from_guild_id: int, to_guild_id: int, *, bet_coin: int | None = None
) -> dict[str, Any]:
    """特定ギルドへのバトル申請を作成し、双方のロックを取得する。

    ``bet_coin`` は申請したギルドマスターが決めたベット額（ギルド合計）です。
    """

    if from_guild_id == to_guild_id:
        return error_result("same_guild", request_id=None)

    timestamp = now_iso()

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        with conn:
            conn.execute("BEGIN IMMEDIATE")

            guild_rows = conn.execute(
                """
                SELECT guild_id
                FROM guilds
                WHERE guild_id IN (?, ?)
                  AND status = 'active'
                """,
                (from_guild_id, to_guild_id),
            ).fetchall()

            if len(guild_rows) != 2:
                conn.rollback()
                return error_result("guild_not_found", request_id=None)

            lock_rows = conn.execute(
                """
                SELECT guild_id
                FROM guild_battle_locks
                WHERE guild_id IN (?, ?)
                """,
                (from_guild_id, to_guild_id),
            ).fetchall()
            locked_ids = {row["guild_id"] for row in lock_rows}

            if from_guild_id in locked_ids:
                conn.rollback()
                return error_result("guild_busy", request_id=None)

            if to_guild_id in locked_ids:
                conn.rollback()
                return error_result("opponent_busy", request_id=None)

            inserted = conn.execute(
                """
                INSERT INTO guild_battle_requests
                    (from_guild_id, to_guild_id, status, bet_coin,
                     created_at, updated_at)
                VALUES
                    (?, ?, 'pending', ?, ?, ?)
                """,
                (from_guild_id, to_guild_id, bet_coin, timestamp, timestamp),
            )
            request_id = int(inserted.lastrowid)

            if not acquire_battle_lock(conn, from_guild_id, "request", request_id):
                conn.rollback()
                return error_result("guild_busy", request_id=None)

            if not acquire_battle_lock(conn, to_guild_id, "request", request_id):
                conn.rollback()
                return error_result("opponent_busy", request_id=None)

    return ok_result(request_id=request_id)


def get_battle_request(request_id: int) -> dict[str, Any] | None:
    """バトル申請を1件返す。"""

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT *
            FROM guild_battle_requests
            WHERE request_id = ?
            """,
            (request_id,),
        ).fetchone()

    return row_to_dict(row)


def get_battle_request_by_message(message_id: int) -> dict[str, Any] | None:
    """申請パネルのメッセージIDからバトル申請を引く。"""

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT *
            FROM guild_battle_requests
            WHERE message_id = ?
            ORDER BY request_id DESC
            LIMIT 1
            """,
            (message_id,),
        ).fetchone()

    return row_to_dict(row)


def get_pending_battle_request_for_guild(guild_id: int) -> dict[str, Any] | None:
    """ギルドが関係する未処理のバトル申請を返す（送信・受信を問わない）。"""

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT *
            FROM guild_battle_requests
            WHERE status = 'pending'
              AND (from_guild_id = ? OR to_guild_id = ?)
            ORDER BY request_id DESC
            LIMIT 1
            """,
            (guild_id, guild_id),
        ).fetchone()

    return row_to_dict(row)


def set_battle_request_message(
    request_id: int,
    channel_id: int,
    message_id: int,
) -> None:
    """申請パネルのチャンネル・メッセージIDを保存する。"""

    with closing(get_connection()) as conn:
        with conn:
            conn.execute(
                """
                UPDATE guild_battle_requests
                SET channel_id = ?,
                    message_id = ?,
                    updated_at = ?
                WHERE request_id = ?
                """,
                (channel_id, message_id, now_iso(), request_id),
            )


def resolve_battle_request(request_id: int, status: str) -> dict[str, Any]:
    """バトル申請へ回答する。

    ``approved`` 以外はロックを解放します。``approved`` の場合は開始前チェックと
    バトル作成まで占有を維持します（13節）。
    """

    if status not in ("approved", "rejected", "cancelled"):
        raise ValueError(f"バトル申請の状態が不正です: {status}")

    timestamp = now_iso()

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        with conn:
            conn.execute("BEGIN IMMEDIATE")

            request_row = conn.execute(
                """
                SELECT *
                FROM guild_battle_requests
                WHERE request_id = ?
                """,
                (request_id,),
            ).fetchone()

            if request_row is None:
                conn.rollback()
                return error_result("not_pending")

            updated = conn.execute(
                """
                UPDATE guild_battle_requests
                SET status = ?,
                    updated_at = ?
                WHERE request_id = ?
                  AND status = 'pending'
                """,
                (status, timestamp, request_id),
            )

            if updated.rowcount == 0:
                conn.rollback()
                return error_result("not_pending")

            if status != "approved":
                conn.execute(
                    """
                    DELETE FROM guild_battle_locks
                    WHERE guild_id IN (?, ?)
                      AND lock_type = 'request'
                      AND reference_id = ?
                    """,
                    (
                        request_row["from_guild_id"],
                        request_row["to_guild_id"],
                        request_id,
                    ),
                )

    return ok_result(
        request_id=request_id,
        status=status,
        from_guild_id=request_row["from_guild_id"],
        to_guild_id=request_row["to_guild_id"],
        channel_id=request_row["channel_id"],
        message_id=request_row["message_id"],
        bet_coin=request_row["bet_coin"],
    )


# ==================================================
# 公開バトル募集（12.2節）
# ==================================================
def create_battle_recruitment(
    guild_id: int, *, bet_coin: int | None = None
) -> dict[str, Any]:
    """公開バトル募集を作成し、募集側のロックを取得する。"""

    timestamp = now_iso()

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        with conn:
            conn.execute("BEGIN IMMEDIATE")

            inserted = conn.execute(
                """
                INSERT INTO guild_battle_recruitments
                    (guild_id, status, bet_coin, created_at, updated_at)
                VALUES
                    (?, 'open', ?, ?, ?)
                """,
                (guild_id, bet_coin, timestamp, timestamp),
            )
            recruitment_id = int(inserted.lastrowid)

            if not acquire_battle_lock(conn, guild_id, "recruitment", recruitment_id):
                conn.rollback()
                return error_result("guild_busy", recruitment_id=None)

    return ok_result(recruitment_id=recruitment_id)


def get_battle_recruitment(recruitment_id: int) -> dict[str, Any] | None:
    """公開バトル募集を1件返す。"""

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT *
            FROM guild_battle_recruitments
            WHERE recruitment_id = ?
            """,
            (recruitment_id,),
        ).fetchone()

    return row_to_dict(row)


def get_battle_recruitment_by_message(message_id: int) -> dict[str, Any] | None:
    """募集EmbedのメッセージIDから募集を引く。"""

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT *
            FROM guild_battle_recruitments
            WHERE message_id = ?
            ORDER BY recruitment_id DESC
            LIMIT 1
            """,
            (message_id,),
        ).fetchone()

    return row_to_dict(row)


def set_battle_recruitment_message(
    recruitment_id: int,
    channel_id: int,
    message_id: int,
) -> None:
    """募集Embedのチャンネル・メッセージIDを保存する。"""

    with closing(get_connection()) as conn:
        with conn:
            conn.execute(
                """
                UPDATE guild_battle_recruitments
                SET channel_id = ?,
                    message_id = ?,
                    updated_at = ?
                WHERE recruitment_id = ?
                """,
                (channel_id, message_id, now_iso(), recruitment_id),
            )


def resolve_battle_recruitment(recruitment_id: int, status: str) -> dict[str, Any]:
    """公開バトル募集を終了する。``cancelled`` は募集側のロックも解放する。"""

    if status not in ("matched", "cancelled"):
        raise ValueError(f"バトル募集の状態が不正です: {status}")

    timestamp = now_iso()

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        with conn:
            conn.execute("BEGIN IMMEDIATE")

            recruitment_row = conn.execute(
                """
                SELECT *
                FROM guild_battle_recruitments
                WHERE recruitment_id = ?
                """,
                (recruitment_id,),
            ).fetchone()

            if recruitment_row is None:
                conn.rollback()
                return error_result("not_open")

            updated = conn.execute(
                """
                UPDATE guild_battle_recruitments
                SET status = ?,
                    updated_at = ?
                WHERE recruitment_id = ?
                  AND status = 'open'
                """,
                (status, timestamp, recruitment_id),
            )

            if updated.rowcount == 0:
                conn.rollback()
                return error_result("not_open")

            if status == "cancelled":
                conn.execute(
                    """
                    DELETE FROM guild_battle_locks
                    WHERE guild_id = ?
                      AND lock_type = 'recruitment'
                      AND reference_id = ?
                    """,
                    (recruitment_row["guild_id"], recruitment_id),
                )

    return ok_result(
        recruitment_id=recruitment_id,
        status=status,
        guild_id=recruitment_row["guild_id"],
        channel_id=recruitment_row["channel_id"],
        message_id=recruitment_row["message_id"],
    )


def claim_battle_recruitment(
    recruitment_id: int,
    challenger_guild_id: int,
) -> dict[str, Any]:
    """公開バトル募集へ申し込み、対戦相手を確定する。

    同時に複数の申込みが届いても、``status='open'`` の行を更新できた1件だけを
    成立させます。更新できなかった申込みは ``already_matched`` になります（12.2節）。
    """

    timestamp = now_iso()

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        with conn:
            conn.execute("BEGIN IMMEDIATE")

            recruitment_row = conn.execute(
                """
                SELECT *
                FROM guild_battle_recruitments
                WHERE recruitment_id = ?
                """,
                (recruitment_id,),
            ).fetchone()

            if recruitment_row is None:
                conn.rollback()
                return error_result("already_matched", recruitment_id=recruitment_id)

            host_guild_id = recruitment_row["guild_id"]

            if host_guild_id == challenger_guild_id:
                conn.rollback()
                return error_result("same_guild", recruitment_id=recruitment_id)

            updated = conn.execute(
                """
                UPDATE guild_battle_recruitments
                SET status = 'matched',
                    updated_at = ?
                WHERE recruitment_id = ?
                  AND status = 'open'
                """,
                (timestamp, recruitment_id),
            )

            if updated.rowcount == 0:
                conn.rollback()
                return error_result("already_matched", recruitment_id=recruitment_id)

            if not acquire_battle_lock(
                conn,
                challenger_guild_id,
                "recruitment",
                recruitment_id,
            ):
                conn.rollback()
                return error_result("guild_busy", recruitment_id=recruitment_id)

    return ok_result(
        recruitment_id=recruitment_id,
        guild_id=host_guild_id,
        challenger_guild_id=challenger_guild_id,
        channel_id=recruitment_row["channel_id"],
        message_id=recruitment_row["message_id"],
        bet_coin=recruitment_row["bet_coin"],
    )


# ==================================================
# バトル作成（13節・14節）
# ==================================================
def create_battle(
    *,
    guild_a_id: int,
    guild_b_id: int,
    guild_time_seconds: int,
    units: list[dict[str, Any]],
    bet_coin: int | None = None,
) -> dict[str, Any]:
    """開始前チェックを通過した対戦のバトルデータと戦闘用使い魔を作成する。

    バトル本体、戦闘用使い魔、両ギルドのロック張り替え、編成ロックまでを
    1トランザクションで確定します。
    """

    if guild_a_id == guild_b_id:
        raise ValueError("同じギルド同士のバトルは作成できません。")

    timestamp = now_iso()
    placeholders = ", ".join("?" for _ in ACTIVE_BATTLE_STATUSES)

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        with conn:
            conn.execute("BEGIN IMMEDIATE")

            active_row = conn.execute(
                f"""
                SELECT battle_id
                FROM guild_battles
                WHERE status IN ({placeholders})
                  AND (
                        guild_a_id IN (?, ?)
                     OR guild_b_id IN (?, ?)
                  )
                LIMIT 1
                """,
                (
                    *ACTIVE_BATTLE_STATUSES,
                    guild_a_id,
                    guild_b_id,
                    guild_a_id,
                    guild_b_id,
                ),
            ).fetchone()

            if active_row is not None:
                conn.rollback()
                return error_result("guild_busy", battle_id=None)

            battle_lock_row = conn.execute(
                """
                SELECT guild_id
                FROM guild_battle_locks
                WHERE guild_id IN (?, ?)
                  AND lock_type = 'battle'
                LIMIT 1
                """,
                (guild_a_id, guild_b_id),
            ).fetchone()

            if battle_lock_row is not None:
                conn.rollback()
                return error_result("guild_busy", battle_id=None)

            inserted = conn.execute(
                """
                INSERT INTO guild_battles
                    (guild_a_id, guild_b_id, status, current_round, turn_index,
                     turn_order, action_seq, log_seq,
                     guild_a_remaining_seconds, guild_b_remaining_seconds,
                     bet_coin, created_at, updated_at)
                VALUES
                    (?, ?, 'preparing', 0, 0, '[]', 0, 0, ?, ?, ?, ?, ?)
                """,
                (
                    guild_a_id,
                    guild_b_id,
                    guild_time_seconds,
                    guild_time_seconds,
                    bet_coin,
                    timestamp,
                    timestamp,
                ),
            )
            battle_id = int(inserted.lastrowid)

            for unit in units:
                max_hp = int(unit["max_hp"])
                base_atk = int(unit["base_atk"])
                conn.execute(
                    """
                    INSERT INTO guild_battle_units
                        (battle_id, guild_id, player_id, familiar_instance_id,
                         familiar_id, level, max_hp, current_hp, base_atk,
                         current_atk, speed, base_speed, cost, gender, slot, alive,
                         order_seed, active_skill_uses, passive_uses,
                         state_flags, updated_at)
                    VALUES
                        (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        battle_id,
                        int(unit["guild_id"]),
                        int(unit["player_id"]),
                        int(unit["familiar_instance_id"]),
                        str(unit["familiar_id"]),
                        int(unit.get("level", 0)),
                        max_hp,
                        int(unit.get("current_hp", max_hp)),
                        base_atk,
                        int(unit.get("current_atk", base_atk)),
                        int(unit.get("speed", 0)),
                        int(unit.get("base_speed", unit.get("speed", 0))),
                        int(unit.get("cost", 0)),
                        unit.get("gender"),
                        int(unit["slot"]),
                        1 if unit.get("alive", True) else 0,
                        int(unit.get("order_seed", 0)),
                        dump_json(unit.get("active_skill_uses") or {}),
                        dump_json(unit.get("passive_uses") or {}),
                        dump_json(unit.get("state_flags") or {}),
                        timestamp,
                    ),
                )

            for guild_id in (guild_a_id, guild_b_id):
                conn.execute(
                    """
                    INSERT INTO guild_battle_locks
                        (guild_id, lock_type, reference_id, created_at)
                    VALUES
                        (?, 'battle', ?, ?)
                    ON CONFLICT(guild_id)
                    DO UPDATE SET
                        lock_type = 'battle',
                        reference_id = excluded.reference_id,
                        created_at = excluded.created_at
                    """,
                    (guild_id, battle_id, timestamp),
                )

            conn.execute(
                """
                UPDATE guilds
                SET roster_locked = 1,
                    updated_at = ?
                WHERE guild_id IN (?, ?)
                """,
                (timestamp, guild_a_id, guild_b_id),
            )

    return ok_result(battle_id=battle_id)
