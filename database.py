"""RAGNA BotのSQLite永続化レイヤー。

CogからSQLを分離し、Railway Volume上のDBを安全に扱うための関数を提供します。
すべての書き込みは、このモジュールの関数を経由させてください。
"""

import logging
import os
import shutil
import sqlite3

from contextlib import closing
from pathlib import Path
from datetime import datetime, timedelta


logger = logging.getLogger(__name__)

# ==================================================
# パス設定
# ==================================================

BASE_DIR = Path(__file__).parent
SCHEMA_PATH = BASE_DIR / "schema.sql"


def _resolve_database_path() -> Path:
    configured_path = os.getenv("DATABASE_PATH", "").strip()
    if configured_path:
        return Path(configured_path).expanduser()

    railway_volume_path = os.getenv("RAILWAY_VOLUME_MOUNT_PATH", "").strip()
    if railway_volume_path:
        return Path(railway_volume_path) / "ragna.db"

    return BASE_DIR / "data" / "ragna.db"


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
    conn.executescript(schema)
    conn.commit()
    conn.close()
    logger.info("DB初期化完了")

# ==================================================
# 仮メンバー
# ==================================================
def add_trial_member(
    user_id: int,
    trial_member_class: str,
    start_date: str,
    end_date: str,
    intro_url: str | None,
    thread_id: int
):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT OR REPLACE INTO trial_members (
            user_id,
            class,
            start_date,
            end_date,
            intro_url,
            evaluation_thread_id,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
        """,
        (
            user_id,
            trial_member_class,
            start_date,
            end_date,
            intro_url,
            thread_id
        )
    )
    conn.commit()
    conn.close()

def get_trial_member(user_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT *
        FROM trial_members
        WHERE user_id = ?
        """,
        (user_id,)
    )
    result = cur.fetchone()
    conn.close()
    return result

def get_trial_member_thread(user_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT evaluation_thread_id
        FROM trial_members
        WHERE user_id = ?
        """,
        (user_id,)
    )
    result = cur.fetchone()
    conn.close()
    if result:
        return result[0]

    return None

def get_trial_member_class_and_thread(user_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT
            class,
            evaluation_thread_id
        FROM trial_members
        WHERE user_id = ?
    """, (user_id,))
    result = cur.fetchone()
    conn.close()
    return result

def get_trial_member_end_date(user_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT end_date
        FROM trial_members
        WHERE user_id = ?
    """, (user_id,))
    row = cur.fetchone()
    conn.close()
    if row:
        return row[0]

    return None

def extend_trial_member_end_date(user_id,days):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT end_date
        FROM trial_members
        WHERE user_id = ?
    """,(user_id,))

    row = cur.fetchone()
    if row is None:
        conn.close()
        return False

    current = datetime.fromisoformat(row[0])
    new_end = current + timedelta(days=days)
    cur.execute("""
        UPDATE trial_members
        SET end_date = ?
        WHERE user_id = ?
    """,
    (new_end.isoformat(),user_id
    ))
    conn.commit()
    conn.close()

    return True

def update_trial_member_class(user_id: int,new_class: str):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE trial_members
        SET class = ?
        WHERE user_id = ?
        """,
        (
            new_class,
            user_id
        )
    )
    conn.commit()
    conn.close()

def update_trial_member_thread(user_id: int,thread_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE trial_members
        SET evaluation_thread_id = ?
        WHERE user_id = ?
        """,
        (thread_id,user_id)
    )
    conn.commit()
    conn.close()

def delete_trial_member(user_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        DELETE FROM trial_members
        WHERE user_id = ?
    """, (user_id,))
    conn.commit()
    conn.close()

# ==================================================
# 評価・追記
# ==================================================
def add_evaluation(
    trial_member_id: int,
    evaluator_id: int,
    voice_score: int,
    conversation_score: int,
    charm_score: int,
    overall_score: int,
    note: str
):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO evaluations
        (
            trial_member_id,
            evaluator_id,
            voice_score,
            conversation_score,
            charm_score,
            overall_score,
            note,
            created_at
        )
        VALUES
        (?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """,
        (
            trial_member_id,
            evaluator_id,
            voice_score,
            conversation_score,
            charm_score,
            overall_score,
            note
        )
    )
    conn.commit()
    conn.close()

def add_comment(
    trial_member_id: int,
    evaluator_id: int,
    voice_score: int | None,
    conversation_score: int | None,
    charm_score: int | None,
    overall_score: int | None,
    note: str | None
):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO comments
        (
            trial_member_id,
            evaluator_id,
            voice_score,
            conversation_score,
            charm_score,
            overall_score,
            note,
            created_at
        )
        VALUES
        (?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """,
        (
            trial_member_id,
            evaluator_id,
            voice_score,
            conversation_score,
            charm_score,
            overall_score,
            note
        )
    )
    conn.commit()
    conn.close()

def has_extension(trial_member_id: int,evaluator_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT 1
        FROM evaluation_extensions
        WHERE trial_member_id = ?
        AND evaluator_id = ?
        """,
        (trial_member_id,evaluator_id)
    )
    result = cur.fetchone()
    conn.close()
    return result is not None

def add_extension(trial_member_id: int,evaluator_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO evaluation_extensions
        (trial_member_id,evaluator_id,extended_at)
        VALUES
        (?, ?, datetime('now'))
        """,
        (
            trial_member_id,
            evaluator_id
        )
    )
    conn.commit()
    conn.close()

def clear_trial_member_evaluations(trial_member_id: int):
    conn = get_connection()
    cur = conn.cursor()

    try:
        # 評価を削除
        cur.execute(
            """
            DELETE FROM evaluations
            WHERE trial_member_id = ?
            """,
            (trial_member_id,)
        )

        # 追記を削除
        cur.execute(
            """
            DELETE FROM comments
            WHERE trial_member_id = ?
            """,
            (trial_member_id,)
        )

        # 延長記録を削除
        cur.execute(
            """
            DELETE FROM evaluation_extensions
            WHERE trial_member_id = ?
            """,
            (trial_member_id,)
        )

        conn.commit()

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()

# ==================================================
# 評価ログチャンネル
# ==================================================
def get_evaluation_log_channel(evaluator_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT channel_id
        FROM evaluation_log_channels
        WHERE evaluator_id = ?
        """,
        (evaluator_id,)
    )
    row = cur.fetchone()
    conn.close()
    return row[0] if row else None

def set_evaluation_log_channel(evaluator_id: int,channel_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT OR REPLACE INTO evaluation_log_channels
        (evaluator_id,channel_id)
        VALUES (?,?)
        """,
        (evaluator_id,channel_id)
    )
    conn.commit()
    conn.close()

def get_all_evaluation_log_channels():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT evaluator_id, channel_id
        FROM evaluation_log_channels
        """
    )
    rows = cur.fetchall()
    conn.close()
    return rows

def delete_evaluation_log_channel(evaluator_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        DELETE FROM evaluation_log_channels
        WHERE evaluator_id = ?
        """,
        (evaluator_id,)
    )
    conn.commit()
    conn.close()

def add_evaluator_review(ended_trial_member_id: int,evaluator_id: int,comment: str = ""):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO evaluator_reviews
        (ended_trial_member_id,evaluator_id,comment,created_at)
        VALUES
        (?, ?, ?, datetime('now'))
        """,
        (ended_trial_member_id,evaluator_id,comment)
    )
    conn.commit()
    conn.close()

def add_trial_member_end_survey(ended_trial_member_id: int,channel_id: int,message_id: int,expires_at: str):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT OR REPLACE INTO trial_member_end_surveys
        (ended_trial_member_id,channel_id,message_id,expires_at)
        VALUES
        (?, ?, ?, ?)
        """,
        (ended_trial_member_id,channel_id,message_id,expires_at)
    )
    conn.commit()
    conn.close()

# 仮メンバー終了アンケートDM取得
def get_trial_member_end_survey(ended_trial_member_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            channel_id,
            message_id,
            expires_at
        FROM trial_member_end_surveys
        WHERE ended_trial_member_id = ?
        """,
        (ended_trial_member_id,)
    )
    row = cur.fetchone()
    conn.close()
    return row

# 期限切れ仮メンバー終了アンケート取得
def get_expired_trial_member_end_surveys():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            ended_trial_member_id,
            channel_id,
            message_id
        FROM trial_member_end_surveys
        WHERE expires_at <= ?
        """,
        (datetime.now().isoformat(),)
    )
    rows = cur.fetchall()
    conn.close()
    return rows

# 仮メンバー終了アンケートDM削除
def delete_trial_member_end_survey(ended_trial_member_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        DELETE
        FROM trial_member_end_surveys
        WHERE ended_trial_member_id = ?
        """,
        (ended_trial_member_id,)
    )
    conn.commit()
    conn.close()

def get_all_trial_member_end_dates():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT
            user_id,
            end_date
        FROM trial_members
    """)
    data = cur.fetchall()
    conn.close()
    return data

# ==================================================
# クラス変更
# ==================================================
def add_class_change(executor_id: int,target_id: int,old_class: str,new_class: str):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO class_changes
        (executor_id,target_id,old_class,new_class,created_at)
        VALUES
        (?, ?, ?, ?, datetime('now'))
        """,
        (executor_id,target_id,old_class,new_class)
    )
    conn.commit()
    conn.close()


def get_class_change_candidates(trial_member_class: str | None = None):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        WITH all_scores AS (

            SELECT
                id,
                trial_member_id,
                evaluator_id,
                overall_score,
                created_at,
                0 AS source_order
            FROM evaluations
            WHERE overall_score IS NOT NULL

            UNION ALL

            SELECT
                id,
                trial_member_id,
                evaluator_id,
                overall_score,
                created_at,
                1 AS source_order
            FROM comments
            WHERE overall_score IS NOT NULL
        ),

        latest_scores AS (

            SELECT
                trial_member_id,
                evaluator_id,
                overall_score,

                ROW_NUMBER() OVER (
                    PARTITION BY
                        trial_member_id,
                        evaluator_id

                    ORDER BY
                        created_at DESC,
                        source_order DESC,
                        id DESC
                ) AS rn

            FROM all_scores
        )

        SELECT
            s.user_id,
            s.class,
            AVG(ls.overall_score) AS average_score,
            COUNT(*) AS evaluator_count

        FROM trial_members AS s

        JOIN latest_scores AS ls
            ON ls.trial_member_id = s.user_id
            AND ls.rn = 1

        WHERE
            (? IS NULL OR s.class = ?)

        GROUP BY
            s.user_id,
            s.class

        HAVING COUNT(*) >= 2
        """,
        (trial_member_class,trial_member_class)
    )
    rows = cur.fetchall()
    conn.close()

    return rows


# ==================================================
# 評価員の評価済み仮メンバー取得
# ==================================================
def get_evaluated_trial_member_ids(evaluator_id: int):

    # sqlite3.Connectionのwith文はcommit/rollbackだけでcloseしないため、明示的に閉じる。
    with closing(get_connection()) as conn:

        rows = conn.execute(
            """
            SELECT DISTINCT trial_member_id
            FROM evaluations
            WHERE evaluator_id = ?
            """,
            (evaluator_id,)
        ).fetchall()

        return {
            row[0]
            for row in rows
        }


# ==================================================
# Coin
# ==================================================

# 残高
def get_balance(user_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT balance
        FROM balances
        WHERE user_id = ?
        """,
        (user_id,)
    )
    result = cur.fetchone()
    conn.close()
    if result:
        return result[0]
    return 0

def add_balance(user_id: int,amount: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO balances
        (user_id,balance)
        VALUES
        (?, ?)

        ON CONFLICT(user_id)
        DO UPDATE SET
        balance = balance + excluded.balance
        """,
        (user_id,amount)
    )
    conn.commit()
    conn.close()

# 取引履歴
def add_transaction(transaction_type: str,executor_id: int | None,target_id: int,amount: int,note: str = ""):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO transactions
        (type,executor_id,target_id,amount,note,created_at)
        VALUES
        (?, ?, ?, ?, ?, datetime('now'))
        """,
        (transaction_type,executor_id,target_id,amount,note)
    )
    conn.commit()
    conn.close()


def transfer_balance(sender_id: int, target_id: int, amount: int, note: str = "") -> bool:
    """残高確認・送金・履歴保存を1つのトランザクションで実行する。

    同時に複数の送金操作が来ても残高を二重に使用できないよう、書き込みロックを
    先に取得します。残高不足の場合は何も変更せず ``False`` を返します。
    """

    if amount <= 0 or sender_id == target_id:
        return False

    # 送金完了・残高不足・例外のどの経路でも接続を確実に閉じる。
    with closing(get_connection()) as conn:
        with conn:
            conn.execute("BEGIN IMMEDIATE")

            debit = conn.execute(
                """
                UPDATE balances
                SET balance = balance - ?
                WHERE user_id = ?
                  AND balance >= ?
                """,
                (amount, sender_id, amount),
            )

            if debit.rowcount == 0:
                conn.rollback()
                return False

            conn.execute(
                """
                INSERT INTO balances (user_id, balance)
                VALUES (?, ?)
                ON CONFLICT(user_id)
                DO UPDATE SET balance = balance + excluded.balance
                """,
                (target_id, amount),
            )
            conn.execute(
                """
                INSERT INTO transactions
                    (type, executor_id, target_id, amount, note, created_at)
                VALUES
                    ('送金', ?, ?, ?, ?, datetime('now'))
                """,
                (sender_id, target_id, amount, note),
            )

    return True

# 月次給料
def get_monthly_reward_state():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT year_month
        FROM monthly_rewards
        WHERE id = 1
    """)
    row = cur.fetchone()
    conn.close()
    return row[0] if row else None

def set_monthly_reward_state(year_month: str):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO monthly_rewards(id,year_month)
        VALUES(1,?)

        ON CONFLICT(id)
        DO UPDATE SET
            year_month = excluded.year_month
    """,
    (year_month,)
    )
    conn.commit()
    conn.close()


def grant_monthly_reward(
    year_month: str,
    user_id: int,
    role_id: int,
    amount: int,
    role_name: str,
) -> bool:
    """月次報酬の記録・残高・取引履歴を原子的に更新する。

    同じ年月・ユーザー・ロールの組み合わせは一度しか反映しません。
    Railwayの再起動や一時的な通信失敗で処理が再実行されても二重支給を防ぎます。
    """

    with closing(get_connection()) as conn:
        with conn:
            inserted = conn.execute(
                """
                INSERT OR IGNORE INTO monthly_reward_grants
                    (year_month, user_id, role_id, amount, created_at)
                VALUES
                    (?, ?, ?, ?, datetime('now'))
                """,
                (year_month, user_id, role_id, amount),
            )

            if inserted.rowcount == 0:
                return False

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
                    ('月次支給', NULL, ?, ?, ?, datetime('now'))
                """,
                (user_id, amount, role_name),
            )

    return True

# ==================================================
# 残高不足を確認して減算
# ==================================================
def subtract_balance_if_enough(user_id: int,amount: int) -> bool:

    if amount <= 0:
        return False

    conn = get_connection()

    try:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE balances
            SET balance = balance - ?
            WHERE user_id = ?
            AND balance >= ?
            """,
            (amount,user_id,amount)
        )
        success = cur.rowcount > 0
        conn.commit()

        return success

    except sqlite3.Error:
        conn.rollback()
        raise

    finally:
        conn.close()

# ==================================================
# VC時間・XP
# ==================================================
def get_vc_time(user_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            total_minutes,
            monthly_minutes,
            total_xp,
            monthly_xp
        FROM vc_time
        WHERE user_id = ?
        """,
        (user_id,)
    )
    result = cur.fetchone()
    conn.close()
    if result:
        return result
    return (0, 0, 0, 0)

def add_vc_time(user_id: int,minutes: int,xp: int = 0):
    if minutes <= 0 and xp <= 0:
        return
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO vc_time
        (user_id,total_minutes,monthly_minutes,total_xp,monthly_xp)
        VALUES
        (?, ?, ?, ?, ?)

        ON CONFLICT(user_id)
        DO UPDATE SET
            total_minutes =
                total_minutes + excluded.total_minutes,

            monthly_minutes =
                monthly_minutes + excluded.monthly_minutes,

            total_xp =
                total_xp + excluded.total_xp,

            monthly_xp =
                monthly_xp + excluded.monthly_xp
        """,
        (user_id,minutes,minutes,xp,xp)
    )
    conn.commit()
    conn.close()

def add_vc_time_batch(records):
    if not records:
        return

    conn = get_connection()
    cur = conn.cursor()

    values = []
    for user_id, minutes, xp in records:
        if minutes <= 0 and xp <= 0:
            continue

        values.append((user_id,minutes,minutes,xp,xp))

    if not values:
        conn.close()
        return

    cur.executemany(
        """
        INSERT INTO vc_time
        (user_id,total_minutes,monthly_minutes,total_xp,monthly_xp)
        VALUES
        (?, ?, ?, ?, ?)

        ON CONFLICT(user_id)
        DO UPDATE SET
            total_minutes =
                total_minutes + excluded.total_minutes,

            monthly_minutes =
                monthly_minutes + excluded.monthly_minutes,

            total_xp =
                total_xp + excluded.total_xp,

            monthly_xp =
                monthly_xp + excluded.monthly_xp
        """,
        values
    )
    conn.commit()
    conn.close()

def get_vc_month_state():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT year_month
        FROM vc_month_state
        WHERE id = 1
        """
    )
    result = cur.fetchone()
    conn.close()

    if result:
        return result[0]

    return None

def set_vc_month_state(year_month: str):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO vc_month_state
        (id,year_month)
        VALUES
        (1,?)

        ON CONFLICT(id)
        DO UPDATE SET
            year_month = excluded.year_month
        """,
        (year_month,)
    )
    conn.commit()
    conn.close()

def reset_monthly_vc_data(year_month: str):
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            UPDATE vc_time
            SET
                monthly_minutes = 0,
                monthly_xp = 0
            """
        )
        cur.execute(
            """
            INSERT INTO vc_month_state
            (id,year_month)
            VALUES
            (1,?)

            ON CONFLICT(id)
            DO UPDATE SET
                year_month = excluded.year_month
            """,
            (year_month,)
        )
        conn.commit()

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()

# ==================================================
# 招待ポイント
# ==================================================
def get_invite_points(user_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT invite_points
        FROM members
        WHERE user_id = ?
        """,
        (user_id,)
    )
    result = cur.fetchone()
    conn.close()
    if result:
        return result[0]

    return 0

def add_invite_points(user_id: int,points: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT OR IGNORE INTO members
        (user_id,class,join_date,invite_points)
        VALUES
        (?, '', '', 0)
        """,
        (user_id,)
    )
    cur.execute(
        """
        UPDATE members
        SET invite_points = invite_points + ?
        WHERE user_id = ?
        """,
        (points,user_id)
    )
    conn.commit()
    conn.close()

def spend_invite_points(user_id: int,points: int) -> bool:

    if points <= 0:
        return False

    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE members
        SET invite_points = invite_points - ?
        WHERE user_id = ?
        AND invite_points >= ?
        """,
        (points,user_id,points)
    )
    success = cur.rowcount > 0
    conn.commit()
    conn.close()

    return success

def add_invite_reward(executor_id: int,target_id: int,points: int,reason: str):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO invite_rewards
        (executor_id,target_id,points,reason,created_at)
        VALUES
        (?, ?, ?, ?, datetime('now'))
        """,
        (executor_id,target_id,points,reason)
    )
    conn.commit()
    conn.close()

# ==================================================
# 招待ポイント特典
# ==================================================

def get_hotel_free_rate(user_id: int) -> int:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT hotel_free_rate
        FROM invite_benefits
        WHERE user_id = ?
        """,
        (user_id,)
    )
    row = cur.fetchone()
    conn.close()

    if row is None:
        return 0

    return row[0]


def add_hotel_free_rate(user_id: int, amount: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO invite_benefits
        (user_id,hotel_free_rate)
        VALUES (?, ?)

        ON CONFLICT(user_id)
        DO UPDATE SET
        hotel_free_rate =
        MIN(99,invite_benefits.hotel_free_rate + excluded.hotel_free_rate)
        """,
        (user_id,amount)
    )
    conn.commit()
    conn.close()

def has_start_ticket(user_id: int) -> bool:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT has_start_ticket
        FROM invite_benefits
        WHERE user_id = ?
        """,
        (user_id,)
    )
    row = cur.fetchone()
    conn.close()

    if row is None:
        return False

    return bool(row[0])

def set_start_ticket(user_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO invite_benefits
        (user_id,has_start_ticket)
        VALUES (?, 1)

        ON CONFLICT(user_id)
        DO UPDATE SET
        has_start_ticket = 1
        """,
        (user_id,)
    )
    conn.commit()
    conn.close()


def remove_start_ticket(user_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE invite_benefits
        SET has_start_ticket = 0
        WHERE user_id = ?
        """,
        (user_id,)
    )
    conn.commit()
    conn.close()

# ==================================================
# ランキング
# ==================================================
def get_evaluator_review_ranking():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            evaluator_id,
            COUNT(*) as cnt
        FROM evaluator_reviews
        GROUP BY evaluator_id
        ORDER BY cnt DESC, evaluator_id ASC
        LIMIT 20
        """
    )
    result = cur.fetchall()
    conn.close()

    return result

def get_balance_ranking():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            user_id,
            balance
        FROM balances
        ORDER BY balance DESC
        """
    )
    result = cur.fetchall()
    conn.close()

    return result

def get_vc_ranking(monthly: bool = False):
    column = (
        "monthly_minutes"
        if monthly
        else "total_minutes"
    )
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        f"""
        SELECT
            user_id,
            {column}
        FROM vc_time
        ORDER BY {column} DESC
        """
    )
    result = cur.fetchall()
    conn.close()

    return result

def get_xp_ranking(monthly: bool = False):
    column = (
        "monthly_xp"
        if monthly
        else "total_xp"
    )
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        f"""
        SELECT
            user_id,
            {column}
        FROM vc_time
        ORDER BY {column} DESC
        """
    )
    result = cur.fetchall()
    conn.close()

    return result

def get_invite_ranking():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            user_id,
            invite_points
        FROM members
        ORDER BY invite_points DESC
        """
    )
    result = cur.fetchall()
    conn.close()

    return result

# ==================================================
# ホテル
# ==================================================
def create_hotel_room(
    channel_id: int,
    text_channel_id: int | None,
    owner_id: int,
    plan: str,
    expires_at: str,
    is_private: bool,
    max_users: int
):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO hotel_rooms(
            channel_id,
            text_channel_id,
            owner_id,
            plan,
            created_at,
            expires_at,
            is_private,
            max_users
        )
        VALUES
        (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            channel_id,
            text_channel_id,
            owner_id,
            plan,
            datetime.now().isoformat(),
            expires_at,
            1 if is_private else 0,
            max_users
        )
    )
    conn.commit()
    conn.close()

def get_hotel_by_channel(channel_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT *
        FROM hotel_rooms
        WHERE channel_id = ?
        """,
        (channel_id,)
    )
    row = cur.fetchone()
    conn.close()
    return row

def get_hotel_by_text_channel(text_channel_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT *
        FROM hotel_rooms
        WHERE text_channel_id = ?
    """, (text_channel_id,)
    )
    row = cur.fetchone()
    conn.close()
    return row

def get_all_hotels():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
        channel_id,
        text_channel_id,
        expires_at
        FROM hotel_rooms
        """
    )
    rows = cur.fetchall()
    conn.close()
    return rows

def update_hotel_limit(channel_id: int,max_users: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE hotel_rooms
        SET max_users = ?
        WHERE channel_id = ?
        """,
        (max_users,channel_id)
    )
    conn.commit()
    conn.close()

def update_hotel_private(channel_id: int,is_private: bool):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE hotel_rooms
        SET is_private = ?
        WHERE channel_id = ?
        """,
        (1 if is_private else 0,channel_id)
    )
    conn.commit()
    conn.close()

def add_hotel_manager(channel_id: int,user_id: int):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT OR IGNORE INTO hotel_managers
        (channel_id, user_id)
        VALUES (?, ?)
        """,
        (channel_id,user_id)
    )
    conn.commit()
    conn.close()

def is_hotel_manager(channel_id: int,user_id: int):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT 1
        FROM hotel_managers
        WHERE channel_id=?
        AND user_id=?
        """,
        (channel_id,user_id)
    )
    result = cursor.fetchone()
    conn.close()
    return result is not None

def delete_hotel_room(channel_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        DELETE
        FROM hotel_rooms
        WHERE channel_id = ?
        """,
        (channel_id,)
    )
    cur.execute("""
        DELETE
        FROM hotel_managers
        WHERE channel_id = ?
        """,
        (channel_id,)
    )
    conn.commit()
    conn.close()

# ==================================================
# お問い合わせ
# ==================================================
def create_ticket(
    channel_id: int,
    owner_id: int,
    ticket_type: str
):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO tickets (
            channel_id,
            owner_id,
            ticket_type,
            status,
            created_at
        )
        VALUES (?, ?, ?, 'open', datetime('now'))
        """,
        (
            channel_id,
            owner_id,
            ticket_type
        )
    )
    conn.commit()
    conn.close()

def get_ticket(channel_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT *
        FROM tickets
        WHERE channel_id = ?
        """,
        (channel_id,)
    )
    row = cur.fetchone()
    conn.close()

    return row

def get_ticket_by_owner(owner_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT *
        FROM tickets
        WHERE owner_id = ?
        LIMIT 1
        """,
        (owner_id,)
    )
    row = cur.fetchone()
    conn.close()

    return row

def close_ticket(channel_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE tickets
        SET status = 'closed'
        WHERE channel_id = ?
        """,
        (channel_id,)
    )
    conn.commit()
    conn.close()

def reopen_ticket(channel_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE tickets
        SET status = 'open'
        WHERE channel_id = ?
        """,
        (channel_id,)
    )
    conn.commit()
    conn.close()

def review_ticket(channel_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE tickets
        SET status = 'review'
        WHERE channel_id = ?
        """,
        (channel_id,)
    )
    conn.commit()
    conn.close()

def delete_ticket(channel_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        DELETE FROM tickets
        WHERE channel_id = ?
        """,
        (channel_id,)
    )
    conn.commit()
    conn.close()
