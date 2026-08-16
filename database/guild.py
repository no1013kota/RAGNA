"""RAGNA Onlineのギルドデータアクセス層。

ギルド本体（guilds）、所属（guild_members）、参加申請（guild_join_requests）を扱う
公開窓口です。CogやViewからSQLを分離し、仕様書 `docs/GAME_SPEC.md` の
5節（ギルド基本仕様）、6節（募集・参加申請）、7節（脱退・追放・譲渡・解散）、
26節（戦績・ランキング）の業務ルールをこの層で保証します。

複数テーブルを更新する処理は必ず1トランザクション（`BEGIN IMMEDIATE`）で実行し、
失敗時は `{"ok": False, "error": "..."}` を返します。coin残高と取引履歴は
`database.core` の関数を呼ばず、同一トランザクション内で直接更新します。
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
# 共通ヘルパー
# ==================================================
def _now() -> str:
    """現在時刻をUTCのISO 8601文字列で返す。"""

    return datetime.now(timezone.utc).isoformat()


# 参加申請のうち、まだ回答されていない状態
_PENDING = "pending"

# 戦績の種別とguildsの列名の対応
_BATTLE_RESULT_COLUMNS = {
    "win": "wins",
    "lose": "losses",
    "draw": "draws",
}

# ランキングの並び順（得点降順 → 勝利数降順 → 敗北数昇順 → guild_id昇順）
_RANKING_ORDER = "points DESC, wins DESC, losses ASC, guild_id ASC"


# ==================================================
# 参照：ギルド
# ==================================================
def get_guild(guild_id: int) -> dict[str, Any] | None:
    """guild_idでギルド1行を取得する（status問わず）。"""

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT *
            FROM guilds
            WHERE guild_id = ?
            """,
            (guild_id,),
        ).fetchone()

    return dict(row) if row else None


def get_guild_by_master(user_id: int) -> dict[str, Any] | None:
    """指定ユーザーがマスターを務める活動中ギルドを取得する。"""

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT *
            FROM guilds
            WHERE master_id = ?
              AND status = 'active'
            ORDER BY guild_id ASC
            LIMIT 1
            """,
            (user_id,),
        ).fetchone()

    return dict(row) if row else None


def get_player_guild(user_id: int) -> dict[str, Any] | None:
    """指定ユーザーが所属している活動中ギルドを取得する。

    1プレイヤー1ギルドはDBの一意インデックスで保証されているため、
    該当は最大1件です。
    """

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT g.*
            FROM guild_members AS m
            JOIN guilds AS g
              ON g.guild_id = m.guild_id
            WHERE m.user_id = ?
              AND g.status = 'active'
            LIMIT 1
            """,
            (user_id,),
        ).fetchone()

    return dict(row) if row else None


def get_active_guilds() -> list[dict[str, Any]]:
    """活動中ギルドの一覧を取得する。"""

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT *
            FROM guilds
            WHERE status = 'active'
            ORDER BY guild_id ASC
            """
        ).fetchall()

    return [dict(row) for row in rows]


def get_guild_by_recruitment_message(message_id: int) -> dict[str, Any] | None:
    """募集EmbedのメッセージIDからギルドを引く。"""

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT *
            FROM guilds
            WHERE recruitment_message_id = ?
            LIMIT 1
            """,
            (message_id,),
        ).fetchone()

    return dict(row) if row else None


def get_guild_by_channel(channel_id: int) -> dict[str, Any] | None:
    """カテゴリーIDまたは専用チャンネルIDからギルドを引く。

    アーカイブ済みギルドのチャンネルも対象にします（解散後の後片付け用）。
    ギルド紹介チャンネルは全ギルド共通のため対象外です。
    """

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT *
            FROM guilds
            WHERE status IN ('active', 'archived')
              AND ? IN (
                  category_id,
                  guild_text_channel_id,
                  guild_voice_channel_id,
                  master_text_channel_id,
                  battle_member_channel_id
              )
            LIMIT 1
            """,
            (channel_id,),
        ).fetchone()

    return dict(row) if row else None


def get_archived_guilds_to_purge(before_utc_iso: str) -> list[dict[str, Any]]:
    """保存期間を過ぎ、まだチャンネルを削除していないアーカイブギルドを返す。"""

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT *
            FROM guilds
            WHERE status = 'archived'
              AND archived_at IS NOT NULL
              AND archived_at <= ?
              AND channels_purged_at IS NULL
            ORDER BY archived_at ASC, guild_id ASC
            """,
            (before_utc_iso,),
        ).fetchall()

    return [dict(row) for row in rows]


# ==================================================
# 参照：所属メンバー
# ==================================================
def get_guild_member(guild_id: int, user_id: int) -> dict[str, Any] | None:
    """ギルド所属行を1件取得する。"""

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT *
            FROM guild_members
            WHERE guild_id = ?
              AND user_id = ?
            """,
            (guild_id, user_id),
        ).fetchone()

    return dict(row) if row else None


def get_guild_members(guild_id: int) -> list[dict[str, Any]]:
    """ギルドの所属メンバーを取得する（マスターが先頭、以降は加入順）。"""

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT *
            FROM guild_members
            WHERE guild_id = ?
            ORDER BY
                CASE WHEN member_role = 'master' THEN 0 ELSE 1 END,
                joined_at ASC,
                user_id ASC
            """,
            (guild_id,),
        ).fetchall()

    return [dict(row) for row in rows]


def count_guild_members(guild_id: int) -> int:
    """ギルドの現在人数を返す（マスターを含む）。"""

    with closing(get_connection()) as conn:
        row = conn.execute(
            """
            SELECT COUNT(*)
            FROM guild_members
            WHERE guild_id = ?
            """,
            (guild_id,),
        ).fetchone()

    return int(row[0]) if row else 0


# ==================================================
# 内部ヘルパー（トランザクション内で使う）
# ==================================================
def _count_members(conn: sqlite3.Connection, guild_id: int) -> int:
    """トランザクション内でギルド人数を数える。"""

    row = conn.execute(
        """
        SELECT COUNT(*)
        FROM guild_members
        WHERE guild_id = ?
        """,
        (guild_id,),
    ).fetchone()

    return int(row[0]) if row else 0


def _current_guild_id(conn: sqlite3.Connection, user_id: int) -> int | None:
    """トランザクション内で所属ギルドIDを引く（未所属はNone）。"""

    row = conn.execute(
        """
        SELECT guild_id
        FROM guild_members
        WHERE user_id = ?
        """,
        (user_id,),
    ).fetchone()

    return int(row[0]) if row else None


def _debit_balance(conn: sqlite3.Connection, user_id: int, amount: int) -> bool:
    """残高から減算する。残高不足または残高行が無い場合はFalse。"""

    if amount <= 0:
        return True

    debit = conn.execute(
        """
        UPDATE balances
        SET balance = balance - ?
        WHERE user_id = ?
          AND balance >= ?
        """,
        (amount, user_id, amount),
    )

    return debit.rowcount > 0


def _credit_balance(conn: sqlite3.Connection, user_id: int, amount: int) -> None:
    """残高へ加算する。残高行が無いユーザーは新規作成する。"""

    if amount <= 0:
        return

    conn.execute(
        """
        INSERT INTO balances (user_id, balance)
        VALUES (?, ?)
        ON CONFLICT(user_id)
        DO UPDATE SET balance = balance + excluded.balance
        """,
        (user_id, amount),
    )


def _add_transaction(
    conn: sqlite3.Connection,
    *,
    transaction_type: str,
    executor_id: int | None,
    target_id: int,
    amount: int,
    note: str,
    created_at: str,
) -> None:
    """coin取引履歴を同一トランザクション内で追加する。"""

    conn.execute(
        """
        INSERT INTO transactions
            (type, executor_id, target_id, amount, note, created_at)
        VALUES
            (?, ?, ?, ?, ?, ?)
        """,
        (transaction_type, executor_id, target_id, amount, note, created_at),
    )


# ==================================================
# ギルド設立・取消
# ==================================================
def _cancel_pending_requests(conn, user_id: int, now: str) -> list[dict[str, Any]]:
    """指定プレイヤーの未処理の参加申請をすべて取り消す（6.2節）。

    参加申請は「申請者が別ギルドへ加入するまで」有効なため、加入が確定した
    時点で同一トランザクション内から呼びます。戻り値には、対応する申請Embedを
    削除するために必要なチャンネルID・メッセージIDを含めます。
    """

    rows = conn.execute(
        """
        SELECT request_id, guild_id, channel_id, message_id
        FROM guild_join_requests
        WHERE user_id = ?
          AND status = 'pending'
        ORDER BY request_id ASC
        """,
        (user_id,),
    ).fetchall()

    if not rows:
        return []

    conn.execute(
        """
        UPDATE guild_join_requests
        SET status = 'auto_cancelled',
            updated_at = ?
        WHERE user_id = ?
          AND status = 'pending'
        """,
        (now, user_id),
    )

    return [
        {
            "request_id": int(row["request_id"]),
            "guild_id": int(row["guild_id"]),
            "channel_id": row["channel_id"],
            "message_id": row["message_id"],
        }
        for row in rows
    ]


def create_guild(*, name: str, master_id: int, capacity: int, cost: int) -> dict[str, Any]:
    """ギルドを設立する（残高確認・減算・履歴・登録を1トランザクション）。

    設立者は自動的にそのギルドへ加入するため、他ギルドへ出している未処理の
    参加申請も同じトランザクションで取り消します（6.2節）。取り消した申請は
    ``cancelled`` として返すので、呼び出し側で申請Embedを削除してください。

    error: "already_in_guild" / "insufficient_balance"
    """

    now = _now()

    with closing(get_connection()) as conn:
        # 取り消した参加申請を列名で読むため、行を辞書として扱う
        conn.row_factory = sqlite3.Row

        with conn:
            conn.execute("BEGIN IMMEDIATE")

            # 1. 二重所属の確認（guild_membersのuser_id一意インデックスと同じ条件）
            if _current_guild_id(conn, master_id) is not None:
                conn.rollback()
                return {
                    "ok": False,
                    "error": "already_in_guild",
                    "guild_id": None,
                    "cancelled": [],
                }

            # 2. 設立費用の減算
            if not _debit_balance(conn, master_id, cost):
                conn.rollback()
                return {
                    "ok": False,
                    "error": "insufficient_balance",
                    "guild_id": None,
                    "cancelled": [],
                }

            # 3. coin取引履歴
            if cost > 0:
                _add_transaction(
                    conn,
                    transaction_type="ギルド設立",
                    executor_id=master_id,
                    target_id=master_id,
                    amount=-cost,
                    note=name,
                    created_at=now,
                )

            # 4. ギルド本体の登録
            inserted = conn.execute(
                """
                INSERT INTO guilds
                    (name, description, master_id, capacity, status,
                     recruitment_status, created_at, updated_at)
                VALUES
                    (?, NULL, ?, ?, 'active', 'closed', ?, ?)
                """,
                (name, master_id, capacity, now, now),
            )
            guild_id = int(inserted.lastrowid)

            # 5. 設立者をマスターとして登録
            try:
                conn.execute(
                    """
                    INSERT INTO guild_members
                        (guild_id, user_id, member_role, joined_at)
                    VALUES
                        (?, ?, 'master', ?)
                    """,
                    (guild_id, master_id, now),
                )
            except sqlite3.IntegrityError:
                # 直前の確認をすり抜けた同時実行（user_id一意インデックス違反）
                conn.rollback()
                logger.warning("ギルド設立の重複所属を検出しました: user_id=%s", master_id)
                return {
                    "ok": False,
                    "error": "already_in_guild",
                    "guild_id": None,
                    "cancelled": [],
                }

            # 6. 設立者が他ギルドへ出している未処理の参加申請を取り消す。
            #    設立＝自分のギルドへの加入なので、6.2節の「申請者が別ギルドへ
            #    加入するまで有効」に従い、同一トランザクションで無効化する。
            cancelled = _cancel_pending_requests(conn, master_id, now)

    return {
        "ok": True,
        "error": None,
        "guild_id": guild_id,
        "cancelled": cancelled,
    }


def refund_guild_creation(guild_id: int, master_id: int, cost: int) -> bool:
    """Discordチャンネル作成に失敗した設立を取り消し、費用を返金する。"""

    now = _now()

    with closing(get_connection()) as conn:
        with conn:
            conn.execute("BEGIN IMMEDIATE")

            updated = conn.execute(
                """
                UPDATE guilds
                SET status = 'deleted',
                    recruitment_status = 'closed',
                    updated_at = ?
                WHERE guild_id = ?
                  AND status = 'active'
                """,
                (now, guild_id),
            )

            if updated.rowcount == 0:
                # 既に取り消し済み。二重返金を防ぐため何もしない。
                conn.rollback()
                return False

            conn.execute(
                """
                DELETE FROM guild_members
                WHERE guild_id = ?
                """,
                (guild_id,),
            )

            _credit_balance(conn, master_id, cost)

            if cost > 0:
                _add_transaction(
                    conn,
                    transaction_type="ギルド設立取消",
                    executor_id=master_id,
                    target_id=master_id,
                    amount=cost,
                    note=f"guild_id={guild_id}",
                    created_at=now,
                )

    return True


# ==================================================
# ギルド情報の更新
# ==================================================
def set_guild_channels(
    guild_id: int,
    *,
    category_id: int,
    guild_text_channel_id: int,
    guild_voice_channel_id: int,
    master_text_channel_id: int,
    battle_member_channel_id: int,
) -> None:
    """作成済みDiscordカテゴリー・チャンネルのIDを保存する。"""

    with closing(get_connection()) as conn:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                UPDATE guilds
                SET category_id = ?,
                    guild_text_channel_id = ?,
                    guild_voice_channel_id = ?,
                    master_text_channel_id = ?,
                    battle_member_channel_id = ?,
                    updated_at = ?
                WHERE guild_id = ?
                """,
                (
                    category_id,
                    guild_text_channel_id,
                    guild_voice_channel_id,
                    master_text_channel_id,
                    battle_member_channel_id,
                    _now(),
                    guild_id,
                ),
            )


def rename_guild(guild_id: int, *, new_name: str, payer_id: int, cost: int) -> dict[str, Any]:
    """ギルド名を変更する（coin減算と名称更新を1トランザクション）。

    error: "guild_not_found" / "insufficient_balance"
    """

    now = _now()

    with closing(get_connection()) as conn:
        with conn:
            conn.execute("BEGIN IMMEDIATE")

            row = conn.execute(
                """
                SELECT name
                FROM guilds
                WHERE guild_id = ?
                  AND status = 'active'
                """,
                (guild_id,),
            ).fetchone()

            if row is None:
                conn.rollback()
                return {"ok": False, "error": "guild_not_found", "old_name": None}

            old_name = str(row[0])

            if not _debit_balance(conn, payer_id, cost):
                conn.rollback()
                return {"ok": False, "error": "insufficient_balance", "old_name": None}

            if cost > 0:
                _add_transaction(
                    conn,
                    transaction_type="ギルド名変更",
                    executor_id=payer_id,
                    target_id=payer_id,
                    amount=-cost,
                    note=f"{old_name} → {new_name}",
                    created_at=now,
                )

            conn.execute(
                """
                UPDATE guilds
                SET name = ?,
                    updated_at = ?
                WHERE guild_id = ?
                """,
                (new_name, now, guild_id),
            )

    return {"ok": True, "error": None, "old_name": old_name}


def update_guild_description(guild_id: int, description: str) -> bool:
    """ギルド説明文を更新する。"""

    with closing(get_connection()) as conn:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            updated = conn.execute(
                """
                UPDATE guilds
                SET description = ?,
                    updated_at = ?
                WHERE guild_id = ?
                  AND status = 'active'
                """,
                (description, _now(), guild_id),
            )
            success = updated.rowcount > 0

    return success


def set_recruitment_status(guild_id: int, status: str) -> bool:
    """募集状態を 'open' / 'closed' に切り替える。"""

    if status not in ("open", "closed"):
        logger.warning("不正な募集状態が指定されました: %s", status)
        return False

    with closing(get_connection()) as conn:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            updated = conn.execute(
                """
                UPDATE guilds
                SET recruitment_status = ?,
                    updated_at = ?
                WHERE guild_id = ?
                  AND status = 'active'
                """,
                (status, _now(), guild_id),
            )
            success = updated.rowcount > 0

    return success


def set_recruitment_message(
    guild_id: int,
    channel_id: int | None,
    message_id: int | None,
) -> None:
    """募集Embedの投稿先チャンネルとメッセージIDを保存する。"""

    with closing(get_connection()) as conn:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                UPDATE guilds
                SET recruitment_channel_id = ?,
                    recruitment_message_id = ?,
                    updated_at = ?
                WHERE guild_id = ?
                """,
                (channel_id, message_id, _now(), guild_id),
            )


def expand_guild_capacity(
    guild_id: int,
    *,
    payer_id: int,
    cost: int,
    max_capacity: int,
) -> dict[str, Any]:
    """メンバー枠を1つ拡張する（coin減算と定員更新を1トランザクション）。

    error: "guild_not_found" / "capacity_max" / "insufficient_balance"
    """

    now = _now()

    with closing(get_connection()) as conn:
        with conn:
            conn.execute("BEGIN IMMEDIATE")

            row = conn.execute(
                """
                SELECT name, capacity
                FROM guilds
                WHERE guild_id = ?
                  AND status = 'active'
                """,
                (guild_id,),
            ).fetchone()

            if row is None:
                conn.rollback()
                return {"ok": False, "error": "guild_not_found", "capacity": None}

            name = str(row[0])
            capacity = int(row[1])

            if capacity >= max_capacity:
                conn.rollback()
                return {"ok": False, "error": "capacity_max", "capacity": capacity}

            if not _debit_balance(conn, payer_id, cost):
                conn.rollback()
                return {"ok": False, "error": "insufficient_balance", "capacity": None}

            new_capacity = capacity + 1

            if cost > 0:
                _add_transaction(
                    conn,
                    transaction_type="ギルド枠拡張",
                    executor_id=payer_id,
                    target_id=payer_id,
                    amount=-cost,
                    note=f"{name}（定員{new_capacity}）",
                    created_at=now,
                )

            conn.execute(
                """
                UPDATE guilds
                SET capacity = ?,
                    updated_at = ?
                WHERE guild_id = ?
                """,
                (new_capacity, now, guild_id),
            )

    return {"ok": True, "error": None, "capacity": new_capacity}


def transfer_guild_master(
    guild_id: int,
    *,
    current_master_id: int,
    new_master_id: int,
) -> dict[str, Any]:
    """マスター権限を所属メンバーへ譲渡する。

    error: "not_master" / "not_member"
    """

    now = _now()

    with closing(get_connection()) as conn:
        with conn:
            conn.execute("BEGIN IMMEDIATE")

            row = conn.execute(
                """
                SELECT master_id
                FROM guilds
                WHERE guild_id = ?
                  AND status = 'active'
                """,
                (guild_id,),
            ).fetchone()

            if row is None or int(row[0]) != current_master_id:
                conn.rollback()
                return {"ok": False, "error": "not_master"}

            member = conn.execute(
                """
                SELECT 1
                FROM guild_members
                WHERE guild_id = ?
                  AND user_id = ?
                """,
                (guild_id, new_master_id),
            ).fetchone()

            if member is None:
                conn.rollback()
                return {"ok": False, "error": "not_member"}

            # 旧マスターを一般メンバーへ戻してから、新マスターを設定する
            conn.execute(
                """
                UPDATE guild_members
                SET member_role = 'member'
                WHERE guild_id = ?
                  AND user_id = ?
                """,
                (guild_id, current_master_id),
            )
            conn.execute(
                """
                UPDATE guild_members
                SET member_role = 'master'
                WHERE guild_id = ?
                  AND user_id = ?
                """,
                (guild_id, new_master_id),
            )
            conn.execute(
                """
                UPDATE guilds
                SET master_id = ?,
                    updated_at = ?
                WHERE guild_id = ?
                """,
                (new_master_id, now, guild_id),
            )

    return {"ok": True, "error": None}


def set_roster_locked(guild_id: int, locked: bool) -> None:
    """編成ロックの状態を切り替える。"""

    with closing(get_connection()) as conn:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                UPDATE guilds
                SET roster_locked = ?,
                    updated_at = ?
                WHERE guild_id = ?
                """,
                (1 if locked else 0, _now(), guild_id),
            )


def archive_guild(guild_id: int) -> dict[str, Any]:
    """ギルドを解散し、アーカイブ状態へ移す。

    メンバー全削除・出場者セット削除・未処理申請の取消・状態変更を
    1トランザクションで行います。戻り値にはDiscord側の後片付けに必要な
    メンバーIDと、削除すべき申請Embedのメッセージ情報を含めます。

    error: "guild_not_found"
    """

    now = _now()

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        with conn:
            conn.execute("BEGIN IMMEDIATE")

            guild = conn.execute(
                """
                SELECT guild_id
                FROM guilds
                WHERE guild_id = ?
                  AND status = 'active'
                """,
                (guild_id,),
            ).fetchone()

            if guild is None:
                conn.rollback()
                return {
                    "ok": False,
                    "error": "guild_not_found",
                    "member_ids": [],
                    "request_message_ids": [],
                }

            member_rows = conn.execute(
                """
                SELECT user_id
                FROM guild_members
                WHERE guild_id = ?
                ORDER BY
                    CASE WHEN member_role = 'master' THEN 0 ELSE 1 END,
                    joined_at ASC,
                    user_id ASC
                """,
                (guild_id,),
            ).fetchall()
            member_ids = [int(row["user_id"]) for row in member_rows]

            request_rows = conn.execute(
                """
                SELECT channel_id, message_id
                FROM guild_join_requests
                WHERE guild_id = ?
                  AND status = 'pending'
                  AND channel_id IS NOT NULL
                  AND message_id IS NOT NULL
                ORDER BY request_id ASC
                """,
                (guild_id,),
            ).fetchall()
            request_message_ids = [
                (int(row["channel_id"]), int(row["message_id"])) for row in request_rows
            ]

            # 未処理の参加申請を強制取消
            conn.execute(
                """
                UPDATE guild_join_requests
                SET status = 'auto_cancelled',
                    updated_at = ?
                WHERE guild_id = ?
                  AND status = 'pending'
                """,
                (now, guild_id),
            )

            # 出場者セットと所属を削除
            conn.execute(
                """
                DELETE FROM guild_battle_members
                WHERE guild_id = ?
                """,
                (guild_id,),
            )
            conn.execute(
                """
                DELETE FROM guild_members
                WHERE guild_id = ?
                """,
                (guild_id,),
            )

            conn.execute(
                """
                UPDATE guilds
                SET status = 'archived',
                    recruitment_status = 'closed',
                    roster_locked = 0,
                    archived_at = ?,
                    updated_at = ?
                WHERE guild_id = ?
                """,
                (now, now, guild_id),
            )

    return {
        "ok": True,
        "error": None,
        "member_ids": member_ids,
        "request_message_ids": request_message_ids,
    }


def mark_guild_channels_purged(guild_id: int) -> None:
    """アーカイブしたDiscordチャンネルを削除済みとして記録する。"""

    now = _now()

    with closing(get_connection()) as conn:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                UPDATE guilds
                SET channels_purged_at = ?,
                    updated_at = ?
                WHERE guild_id = ?
                """,
                (now, now, guild_id),
            )


# ==================================================
# 戦績・ランキング
# ==================================================
def add_guild_battle_record(guild_id: int, result: str) -> None:
    """通算戦績（勝ち・負け・引き分け）を1加算する。"""

    column = _BATTLE_RESULT_COLUMNS.get(result)

    if column is None:
        logger.warning("不正な戦績種別が指定されました: %s", result)
        return

    with closing(get_connection()) as conn:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                f"""
                UPDATE guilds
                SET {column} = {column} + 1,
                    updated_at = ?
                WHERE guild_id = ?
                """,
                (_now(), guild_id),
            )


def get_guild_ranking(
    limit: int,
    *,
    win_points: int,
    draw_points: int,
    lose_points: int,
) -> list[dict[str, Any]]:
    """活動中ギルドのランキングを上位から取得する。

    得点は 勝利数×win_points + 引き分け数×draw_points + 敗北数×lose_points で、
    得点降順 → 勝利数降順 → 敗北数昇順 → guild_id昇順に並べます。
    """

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"""
            SELECT
                guild_id,
                name,
                wins,
                losses,
                draws,
                (wins * ? + draws * ? + losses * ?) AS points
            FROM guilds
            WHERE status = 'active'
            ORDER BY {_RANKING_ORDER}
            LIMIT ?
            """,
            (win_points, draw_points, lose_points, limit),
        ).fetchall()

    return [dict(row) | {"rank": index} for index, row in enumerate(rows, start=1)]


def get_guild_ranking_position(
    guild_id: int,
    *,
    win_points: int,
    draw_points: int,
    lose_points: int,
) -> int | None:
    """指定ギルドのランキング順位（1始まり）を返す。対象外はNone。"""

    with closing(get_connection()) as conn:
        row = conn.execute(
            f"""
            SELECT rank
            FROM (
                SELECT
                    guild_id,
                    ROW_NUMBER() OVER (ORDER BY {_RANKING_ORDER}) AS rank
                FROM (
                    SELECT
                        guild_id,
                        wins,
                        losses,
                        draws,
                        (wins * ? + draws * ? + losses * ?) AS points
                    FROM guilds
                    WHERE status = 'active'
                )
            )
            WHERE guild_id = ?
            """,
            (win_points, draw_points, lose_points, guild_id),
        ).fetchone()

    return int(row[0]) if row else None


# ==================================================
# メンバーの追加・削除
# ==================================================
def add_guild_member(guild_id: int, user_id: int) -> dict[str, Any]:
    """定員と重複所属を再確認してメンバーを追加する。

    error: "guild_not_found" / "guild_full" / "already_in_guild"
    """

    now = _now()

    with closing(get_connection()) as conn:
        with conn:
            conn.execute("BEGIN IMMEDIATE")

            row = conn.execute(
                """
                SELECT capacity
                FROM guilds
                WHERE guild_id = ?
                  AND status = 'active'
                """,
                (guild_id,),
            ).fetchone()

            if row is None:
                conn.rollback()
                return {"ok": False, "error": "guild_not_found"}

            if _count_members(conn, guild_id) >= int(row[0]):
                conn.rollback()
                return {"ok": False, "error": "guild_full"}

            if _current_guild_id(conn, user_id) is not None:
                conn.rollback()
                return {"ok": False, "error": "already_in_guild"}

            try:
                conn.execute(
                    """
                    INSERT INTO guild_members
                        (guild_id, user_id, member_role, joined_at)
                    VALUES
                        (?, ?, 'member', ?)
                    """,
                    (guild_id, user_id, now),
                )
            except sqlite3.IntegrityError:
                conn.rollback()
                logger.warning("メンバー追加の重複所属を検出しました: user_id=%s", user_id)
                return {"ok": False, "error": "already_in_guild"}

    return {"ok": True, "error": None}


def remove_guild_member(guild_id: int, user_id: int) -> dict[str, Any]:
    """メンバーを脱退・追放させ、出場者セットからも解除する。

    error: "not_member" / "master_cannot_leave"
    """

    with closing(get_connection()) as conn:
        with conn:
            conn.execute("BEGIN IMMEDIATE")

            row = conn.execute(
                """
                SELECT member_role
                FROM guild_members
                WHERE guild_id = ?
                  AND user_id = ?
                """,
                (guild_id, user_id),
            ).fetchone()

            if row is None:
                conn.rollback()
                return {"ok": False, "error": "not_member"}

            if str(row[0]) == "master":
                conn.rollback()
                return {"ok": False, "error": "master_cannot_leave"}

            conn.execute(
                """
                DELETE FROM guild_members
                WHERE guild_id = ?
                  AND user_id = ?
                """,
                (guild_id, user_id),
            )
            conn.execute(
                """
                DELETE FROM guild_battle_members
                WHERE guild_id = ?
                  AND user_id = ?
                """,
                (guild_id, user_id),
            )

    return {"ok": True, "error": None}


# ==================================================
# 参加申請
# ==================================================
def create_join_request(guild_id: int, user_id: int) -> dict[str, Any]:
    """ギルドへの参加申請を作成する。

    error: "guild_not_found" / "recruitment_closed" / "guild_full" /
           "already_in_guild" / "already_requested"
    """

    now = _now()

    with closing(get_connection()) as conn:
        with conn:
            conn.execute("BEGIN IMMEDIATE")

            row = conn.execute(
                """
                SELECT capacity, recruitment_status
                FROM guilds
                WHERE guild_id = ?
                  AND status = 'active'
                """,
                (guild_id,),
            ).fetchone()

            if row is None:
                conn.rollback()
                return {"ok": False, "error": "guild_not_found", "request_id": None}

            if str(row[1]) != "open":
                conn.rollback()
                return {"ok": False, "error": "recruitment_closed", "request_id": None}

            if _count_members(conn, guild_id) >= int(row[0]):
                conn.rollback()
                return {"ok": False, "error": "guild_full", "request_id": None}

            if _current_guild_id(conn, user_id) is not None:
                conn.rollback()
                return {"ok": False, "error": "already_in_guild", "request_id": None}

            existing = conn.execute(
                """
                SELECT 1
                FROM guild_join_requests
                WHERE guild_id = ?
                  AND user_id = ?
                  AND status = 'pending'
                """,
                (guild_id, user_id),
            ).fetchone()

            if existing is not None:
                conn.rollback()
                return {"ok": False, "error": "already_requested", "request_id": None}

            try:
                inserted = conn.execute(
                    """
                    INSERT INTO guild_join_requests
                        (guild_id, user_id, status, created_at, updated_at)
                    VALUES
                        (?, ?, 'pending', ?, ?)
                    """,
                    (guild_id, user_id, now, now),
                )
            except sqlite3.IntegrityError:
                # 未処理申請の部分一意インデックス違反（同時押しなど）
                conn.rollback()
                return {"ok": False, "error": "already_requested", "request_id": None}

            request_id = int(inserted.lastrowid)

    return {"ok": True, "error": None, "request_id": request_id}


def set_join_request_message(request_id: int, channel_id: int, message_id: int) -> None:
    """申請Embedの投稿先チャンネルとメッセージIDを保存する。"""

    with closing(get_connection()) as conn:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                UPDATE guild_join_requests
                SET channel_id = ?,
                    message_id = ?,
                    updated_at = ?
                WHERE request_id = ?
                """,
                (channel_id, message_id, _now(), request_id),
            )


def get_join_request(request_id: int) -> dict[str, Any] | None:
    """参加申請を1件取得する（status問わず）。"""

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT *
            FROM guild_join_requests
            WHERE request_id = ?
            """,
            (request_id,),
        ).fetchone()

    return dict(row) if row else None


def get_join_request_by_message(message_id: int) -> dict[str, Any] | None:
    """申請EmbedのメッセージIDから参加申請を引く。"""

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT *
            FROM guild_join_requests
            WHERE message_id = ?
            ORDER BY request_id DESC
            LIMIT 1
            """,
            (message_id,),
        ).fetchone()

    return dict(row) if row else None


def get_pending_join_requests(guild_id: int) -> list[dict[str, Any]]:
    """ギルドの未処理参加申請を古い順に取得する。"""

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT *
            FROM guild_join_requests
            WHERE guild_id = ?
              AND status = 'pending'
            ORDER BY created_at ASC, request_id ASC
            """,
            (guild_id,),
        ).fetchall()

    return [dict(row) for row in rows]


def get_pending_join_requests_by_user(user_id: int) -> list[dict[str, Any]]:
    """申請者本人の未処理参加申請を古い順に取得する。"""

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT *
            FROM guild_join_requests
            WHERE user_id = ?
              AND status = 'pending'
            ORDER BY created_at ASC, request_id ASC
            """,
            (user_id,),
        ).fetchall()

    return [dict(row) for row in rows]


def approve_join_request(request_id: int, *, approver_id: int) -> dict[str, Any]:
    """参加申請を承認し、他ギルドへの未処理申請を同一トランザクションで取り消す。

    GAME_SPEC 27節の必須制約「参加承認と他ギルドへの申請取消を同一トランザクションで
    行う」を満たします。取り消した申請のEmbed情報はCogが削除するために返します。

    error: "not_pending" / "guild_not_found" / "guild_full" / "already_in_guild"
    """

    now = _now()
    empty: list[dict[str, Any]] = []

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        with conn:
            conn.execute("BEGIN IMMEDIATE")

            # 1. 申請・ギルド・定員・所属を再確認
            request = conn.execute(
                """
                SELECT guild_id, user_id, status
                FROM guild_join_requests
                WHERE request_id = ?
                """,
                (request_id,),
            ).fetchone()

            if request is None or str(request["status"]) != _PENDING:
                conn.rollback()
                return {
                    "ok": False,
                    "error": "not_pending",
                    "guild_id": None,
                    "user_id": None,
                    "cancelled": empty,
                }

            guild_id = int(request["guild_id"])
            user_id = int(request["user_id"])

            guild = conn.execute(
                """
                SELECT capacity
                FROM guilds
                WHERE guild_id = ?
                  AND status = 'active'
                """,
                (guild_id,),
            ).fetchone()

            if guild is None:
                conn.rollback()
                return {
                    "ok": False,
                    "error": "guild_not_found",
                    "guild_id": guild_id,
                    "user_id": user_id,
                    "cancelled": empty,
                }

            if _count_members(conn, guild_id) >= int(guild["capacity"]):
                conn.rollback()
                return {
                    "ok": False,
                    "error": "guild_full",
                    "guild_id": guild_id,
                    "user_id": user_id,
                    "cancelled": empty,
                }

            if _current_guild_id(conn, user_id) is not None:
                conn.rollback()
                return {
                    "ok": False,
                    "error": "already_in_guild",
                    "guild_id": guild_id,
                    "user_id": user_id,
                    "cancelled": empty,
                }

            # 2. メンバーとして登録
            try:
                conn.execute(
                    """
                    INSERT INTO guild_members
                        (guild_id, user_id, member_role, joined_at)
                    VALUES
                        (?, ?, 'member', ?)
                    """,
                    (guild_id, user_id, now),
                )
            except sqlite3.IntegrityError:
                conn.rollback()
                logger.warning("参加承認の重複所属を検出しました: user_id=%s", user_id)
                return {
                    "ok": False,
                    "error": "already_in_guild",
                    "guild_id": guild_id,
                    "user_id": user_id,
                    "cancelled": empty,
                }

            # 3. 当該申請を承認済みへ
            conn.execute(
                """
                UPDATE guild_join_requests
                SET status = 'approved',
                    resolved_by = ?,
                    updated_at = ?
                WHERE request_id = ?
                """,
                (approver_id, now, request_id),
            )

            # 4. 同じ申請者の他の未処理申請を強制取消
            cancelled_rows = conn.execute(
                """
                SELECT request_id, guild_id, channel_id, message_id
                FROM guild_join_requests
                WHERE user_id = ?
                  AND status = 'pending'
                  AND request_id != ?
                ORDER BY request_id ASC
                """,
                (user_id, request_id),
            ).fetchall()

            conn.execute(
                """
                UPDATE guild_join_requests
                SET status = 'auto_cancelled',
                    updated_at = ?
                WHERE user_id = ?
                  AND status = 'pending'
                  AND request_id != ?
                """,
                (now, user_id, request_id),
            )

            cancelled = [
                {
                    "request_id": int(row["request_id"]),
                    "guild_id": int(row["guild_id"]),
                    "channel_id": row["channel_id"],
                    "message_id": row["message_id"],
                }
                for row in cancelled_rows
            ]

    return {
        "ok": True,
        "error": None,
        "guild_id": guild_id,
        "user_id": user_id,
        "cancelled": cancelled,
    }


def reject_join_request(request_id: int, *, approver_id: int) -> dict[str, Any]:
    """参加申請を拒否する。

    error: "not_pending"
    """

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        with conn:
            conn.execute("BEGIN IMMEDIATE")

            request = conn.execute(
                """
                SELECT guild_id, user_id, status
                FROM guild_join_requests
                WHERE request_id = ?
                """,
                (request_id,),
            ).fetchone()

            if request is None or str(request["status"]) != _PENDING:
                conn.rollback()
                return {
                    "ok": False,
                    "error": "not_pending",
                    "guild_id": None,
                    "user_id": None,
                }

            guild_id = int(request["guild_id"])
            user_id = int(request["user_id"])

            conn.execute(
                """
                UPDATE guild_join_requests
                SET status = 'rejected',
                    resolved_by = ?,
                    updated_at = ?
                WHERE request_id = ?
                """,
                (approver_id, _now(), request_id),
            )

    return {"ok": True, "error": None, "guild_id": guild_id, "user_id": user_id}


def cancel_join_request(request_id: int, *, user_id: int) -> dict[str, Any]:
    """申請者本人が未処理の参加申請を取り消す。

    error: "not_pending" / "not_owner"
    """

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        with conn:
            conn.execute("BEGIN IMMEDIATE")

            request = conn.execute(
                """
                SELECT guild_id, user_id, status, channel_id, message_id
                FROM guild_join_requests
                WHERE request_id = ?
                """,
                (request_id,),
            ).fetchone()

            if request is None:
                conn.rollback()
                return {
                    "ok": False,
                    "error": "not_pending",
                    "guild_id": None,
                    "channel_id": None,
                    "message_id": None,
                }

            if int(request["user_id"]) != user_id:
                conn.rollback()
                return {
                    "ok": False,
                    "error": "not_owner",
                    "guild_id": None,
                    "channel_id": None,
                    "message_id": None,
                }

            if str(request["status"]) != _PENDING:
                conn.rollback()
                return {
                    "ok": False,
                    "error": "not_pending",
                    "guild_id": None,
                    "channel_id": None,
                    "message_id": None,
                }

            guild_id = int(request["guild_id"])
            channel_id = request["channel_id"]
            message_id = request["message_id"]

            conn.execute(
                """
                UPDATE guild_join_requests
                SET status = 'cancelled',
                    updated_at = ?
                WHERE request_id = ?
                """,
                (_now(), request_id),
            )

    return {
        "ok": True,
        "error": None,
        "guild_id": guild_id,
        "channel_id": channel_id,
        "message_id": message_id,
    }
