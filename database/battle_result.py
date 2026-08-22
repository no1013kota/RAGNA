"""バトル終了・報酬付与・運営操作ログ・保守のSQL実装。

勝敗の確定と戦績への反映（GAME_SPEC 26節）、賭けcoinの精算（26.2節）、
運営操作ログの記録と古いログの削除（29節・27節）を担当します。

設計上の要点（docs/GAME_SPEC.md 26・27・29節）:

- バトル終了処理は二重実行を防ぐため、進行中の状態（``ACTIVE_BATTLE_STATUSES``）
  からの遷移だけを許可します（26.2節）。既に終わっているバトルには何もしません。
- 賭けcoinの精算は ``BEGIN IMMEDIATE`` で始め、勝敗の確定と残高の増減を同じ
  トランザクションに収めます。配分は ``split_bet_evenly`` で端数まで割り切ります。
- 運営操作ログには表示名やメッセージ本文を保存しません（29節）。
"""

from __future__ import annotations

import logging
import sqlite3

from contextlib import closing
from datetime import datetime, timedelta, timezone
from typing import Any

from .battle_common import (
    ACTIVE_BATTLE_STATUSES,
    _RESULT_DRAW,
    _RESULT_GUILD_A,
    _RESULT_GUILD_B,
    error_result,
    now_iso,
    ok_result,
)
from .core import get_connection


logger = logging.getLogger(__name__)


# ==================================================
# バトル終了（26節）
# ==================================================
def finish_battle(
    battle_id: int,
    *,
    result: str | None,
    end_reason: str,
    status: str = "finished",
) -> dict[str, Any]:
    """バトルを終了し、後片付けと戦績反映を1トランザクションで確定する。

    進行中の状態からの遷移だけを許可するため、Discord操作の失敗で再実行されても
    勝敗を二重に記録しません（26.2節）。運営による強制中止（``aborted``）は
    勝敗数へ反映しません。
    """

    if status not in ("finished", "aborted"):
        raise ValueError(f"バトル終了状態が不正です: {status}")

    timestamp = now_iso()
    placeholders = ", ".join("?" for _ in ACTIVE_BATTLE_STATUSES)

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        with conn:
            conn.execute("BEGIN IMMEDIATE")

            battle_row = conn.execute(
                """
                SELECT guild_a_id, guild_b_id
                FROM guild_battles
                WHERE battle_id = ?
                """,
                (battle_id,),
            ).fetchone()

            if battle_row is None:
                conn.rollback()
                return error_result("already_finished")

            guild_a_id = battle_row["guild_a_id"]
            guild_b_id = battle_row["guild_b_id"]

            updated = conn.execute(
                f"""
                UPDATE guild_battles
                SET status = ?,
                    result = ?,
                    end_reason = ?,
                    finished_at = ?,
                    turn_deadline_at = NULL,
                    updated_at = ?
                WHERE battle_id = ?
                  AND status IN ({placeholders})
                """,
                (
                    status,
                    result,
                    end_reason,
                    timestamp,
                    timestamp,
                    battle_id,
                    *ACTIVE_BATTLE_STATUSES,
                ),
            )

            if updated.rowcount == 0:
                conn.rollback()
                return error_result("already_finished")

            conn.execute(
                """
                UPDATE guilds
                SET roster_locked = 0,
                    updated_at = ?
                WHERE guild_id IN (?, ?)
                """,
                (timestamp, guild_a_id, guild_b_id),
            )

            # 出場者セットと使い魔セットを解除する（26.2節）。
            # 行を残すと、次にメンバー構成が変わったときに出場者専用TCの
            # 閲覧権限が復活してしまうため、両方を削除して編成と権限を一致させる。
            conn.execute(
                """
                DELETE FROM guild_battle_members
                WHERE guild_id IN (?, ?)
                """,
                (guild_a_id, guild_b_id),
            )
            conn.execute(
                """
                DELETE FROM guild_battle_entries
                WHERE guild_id IN (?, ?)
                """,
                (guild_a_id, guild_b_id),
            )

            conn.execute(
                """
                DELETE FROM guild_battle_locks
                WHERE guild_id IN (?, ?)
                  AND lock_type = 'battle'
                  AND reference_id = ?
                """,
                (guild_a_id, guild_b_id, battle_id),
            )

            if status == "finished":
                if result == _RESULT_GUILD_A:
                    _add_battle_record(conn, guild_a_id, "wins", timestamp)
                    _add_battle_record(conn, guild_b_id, "losses", timestamp)
                elif result == _RESULT_GUILD_B:
                    _add_battle_record(conn, guild_b_id, "wins", timestamp)
                    _add_battle_record(conn, guild_a_id, "losses", timestamp)
                elif result == _RESULT_DRAW:
                    _add_battle_record(conn, guild_a_id, "draws", timestamp)
                    _add_battle_record(conn, guild_b_id, "draws", timestamp)

    return ok_result(
        guild_a_id=guild_a_id,
        guild_b_id=guild_b_id,
        result=result,
    )


def _add_battle_record(
    conn: sqlite3.Connection,
    guild_id: int,
    column: str,
    timestamp: str,
) -> None:
    """ギルドの通算戦績を1件加算する。列名は呼び出し側の固定値だけを渡す。"""

    if column not in ("wins", "losses", "draws"):
        raise ValueError(f"戦績の列名が不正です: {column}")

    conn.execute(
        f"""
        UPDATE guilds
        SET {column} = {column} + 1,
            updated_at = ?
        WHERE guild_id = ?
        """,
        (timestamp, guild_id),
    )


# ==================================================
# バトル報酬（26.2節）
# ==================================================
def split_bet_evenly(total: int, count: int) -> list[int]:
    """``total`` を ``count`` 人で均等に分ける（26.2節）。

    端数はメンバー選択順に切り上げ → 切り下げで割り振ります。渡された順番が
    そのままメンバー選択順です。合計は必ず ``total`` になります。
    """

    if count <= 0 or total <= 0:
        return [0] * max(0, count)

    base, remainder = divmod(total, count)
    return [base + (1 if index < remainder else 0) for index in range(count)]


def settle_battle_bet(
    battle_id: int,
    *,
    winners: list[dict[str, Any]],
    losers: list[dict[str, Any]],
    drawers: list[dict[str, Any]],
    bet_coin: int,
    win_xp: int,
    lose_xp: int,
    draw_xp: int,
    reward_date: str,
    daily_limit: int,
) -> dict[str, Any]:
    """ベットしたcoinを負けた側から勝った側へ移し、XPを付与する（26.2節）。

    - ``bet_coin`` は**ギルド単位**のベット額です。負けた側の出場者で均等に分担し、
      端数はメンバー選択順に切り上げ → 切り下げで割り振ります。
    - 残高が足りない出場者からは持っているぶんだけ回収し、マイナス残高は作りません。
    - 回収できた合計を、勝った側の出場者へ同じ方法（均等・端数は選択順）で分けます。
      coinは移動するだけなので、総量は増えません。
    - XPは新しく付与するため、1プレイヤー1日 ``daily_limit`` 試合までに制限します。
    - 引き分けはcoinを動かさず、XPだけを付与します。

    戻り値は ``{"pot", "collected", "paid", "results"}`` です。``results`` の各要素は
    ``{"user_id", "guild_id", "coin", "xp", "outcome"}`` で、``coin`` は負けた側なら負数です。
    """

    timestamp = now_iso()

    def _xp_allowed(conn: sqlite3.Connection, user_id: int) -> bool:
        row = conn.execute(
            """
            SELECT COUNT(*) AS granted_count
            FROM guild_battle_rewards
            WHERE user_id = ?
              AND reward_date = ?
            """,
            (user_id, reward_date),
        ).fetchone()
        return int(row["granted_count"]) < daily_limit

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        with conn:
            conn.execute("BEGIN IMMEDIATE")

            already = conn.execute(
                "SELECT COUNT(*) FROM guild_battle_rewards WHERE battle_id = ?",
                (battle_id,),
            ).fetchone()
            if int(already[0]) > 0:
                # 同じバトルで既に清算済み。二重に動かさない。
                conn.rollback()
                return {"pot": 0, "collected": [], "paid": [], "results": []}

            # ---- 1. 負けた側から回収する（ギルド合計を均等に分担） ----
            pot = 0
            collected: list[dict[str, Any]] = []
            shares = split_bet_evenly(bet_coin, len(losers))

            for entry, share in zip(losers, shares):
                user_id = int(entry["user_id"])

                balance_row = conn.execute(
                    "SELECT balance FROM balances WHERE user_id = ?", (user_id,)
                ).fetchone()
                balance = int(balance_row["balance"]) if balance_row else 0

                taken = max(0, min(share, balance))
                if taken:
                    conn.execute(
                        "UPDATE balances SET balance = balance - ? WHERE user_id = ?",
                        (taken, user_id),
                    )
                    conn.execute(
                        """
                        INSERT INTO transactions
                            (type, executor_id, target_id, amount, note, created_at)
                        VALUES
                            ('ギルドバトル敗北', NULL, ?, ?, ?, ?)
                        """,
                        (user_id, -taken, f"battle_id={battle_id}", timestamp),
                    )

                pot += taken
                collected.append(
                    {"user_id": user_id, "coin": taken, "share": share}
                )

            # ---- 2. 勝った側へ均等に配る ----
            paid: list[dict[str, Any]] = []

            if winners and pot:
                for entry, amount in zip(winners, split_bet_evenly(pot, len(winners))):
                    user_id = int(entry["user_id"])
                    if not amount:
                        continue

                    conn.execute(
                        """
                        INSERT INTO balances (user_id, balance)
                        VALUES (?, ?)
                        ON CONFLICT(user_id)
                        DO UPDATE SET balance = balance + excluded.balance
                        """,
                        (user_id, amount),
                    )
                    conn.execute(
                        """
                        INSERT INTO transactions
                            (type, executor_id, target_id, amount, note, created_at)
                        VALUES
                            ('ギルドバトル勝利', NULL, ?, ?, ?, ?)
                        """,
                        (user_id, amount, f"battle_id={battle_id}", timestamp),
                    )
                    paid.append({"user_id": user_id, "coin": amount})

            paid_by_user = {item["user_id"]: item["coin"] for item in paid}
            taken_by_user = {item["user_id"]: item["coin"] for item in collected}

            # ---- 3. 記録とXP ----
            results: list[dict[str, Any]] = []

            groups = (
                (winners, "win", win_xp),
                (losers, "lose", lose_xp),
                (drawers, "draw", draw_xp),
            )

            for entries, outcome, xp_amount in groups:
                for entry in entries:
                    user_id = int(entry["user_id"])
                    guild_id = int(entry["guild_id"])

                    if outcome == "win":
                        coin = paid_by_user.get(user_id, 0)
                    elif outcome == "lose":
                        coin = -taken_by_user.get(user_id, 0)
                    else:
                        coin = 0

                    xp = xp_amount if _xp_allowed(conn, user_id) else 0

                    conn.execute(
                        """
                        INSERT OR IGNORE INTO guild_battle_rewards
                            (battle_id, user_id, guild_id, coin, xp, reward_date,
                             created_at)
                        VALUES
                            (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (battle_id, user_id, guild_id, coin, xp, reward_date, timestamp),
                    )

                    if xp:
                        conn.execute(
                            """
                            INSERT INTO vc_time (user_id, total_xp, monthly_xp)
                            VALUES (?, ?, ?)
                            ON CONFLICT(user_id)
                            DO UPDATE SET
                                total_xp = total_xp + excluded.total_xp,
                                monthly_xp = monthly_xp + excluded.monthly_xp
                            """,
                            (user_id, xp, xp),
                        )

                    results.append(
                        {
                            "user_id": user_id,
                            "guild_id": guild_id,
                            "coin": coin,
                            "xp": xp,
                            "outcome": outcome,
                        }
                    )

    return {"pot": pot, "collected": collected, "paid": paid, "results": results}


# ==================================================
# 運営操作ログ・保守（29節）
# ==================================================
def add_admin_log(
    *,
    executor_id: int | None,
    action: str,
    target_user_id: int | None = None,
    target_guild_id: int | None = None,
    target_battle_id: int | None = None,
    success: bool = True,
    reason: str | None = None,
    operation_id: str | None = None,
) -> None:
    """運営操作ログを記録する。表示名やメッセージ本文は保存しない。"""

    with closing(get_connection()) as conn:
        with conn:
            conn.execute(
                """
                INSERT INTO game_admin_logs
                    (executor_id, action, target_user_id, target_guild_id,
                     target_battle_id, success, reason, operation_id, created_at)
                VALUES
                    (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    executor_id,
                    action,
                    target_user_id,
                    target_guild_id,
                    target_battle_id,
                    1 if success else 0,
                    reason,
                    operation_id,
                    now_iso(),
                ),
            )


def purge_old_battle_logs(
    *,
    battle_log_days: int,
    admin_log_days: int,
) -> dict[str, int]:
    """保存期間を過ぎた行動ログと運営操作ログを削除する（27節）。"""

    now = datetime.now(timezone.utc)
    battle_limit = (now - timedelta(days=battle_log_days)).isoformat()
    admin_limit = (now - timedelta(days=admin_log_days)).isoformat()

    with closing(get_connection()) as conn:
        with conn:
            conn.execute("BEGIN IMMEDIATE")

            battle_deleted = conn.execute(
                """
                DELETE FROM guild_battle_logs
                WHERE created_at < ?
                """,
                (battle_limit,),
            ).rowcount

            admin_deleted = conn.execute(
                """
                DELETE FROM game_admin_logs
                WHERE created_at < ?
                """,
                (admin_limit,),
            ).rowcount

    return {
        "battle_logs": max(battle_deleted, 0),
        "admin_logs": max(admin_deleted, 0),
    }
