"""仮メンバー・評価・クラス変更のSQL実装。

仮メンバーの登録と期限、評価と追記、評価ログチャンネル、終了アンケート、
クラス変更の候補集計までを担当します。
"""

from contextlib import closing
from datetime import datetime, timedelta

from .core import get_connection


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


__all__ = [
    "add_class_change",
    "add_comment",
    "add_evaluation",
    "add_evaluator_review",
    "add_extension",
    "add_trial_member",
    "add_trial_member_end_survey",
    "clear_trial_member_evaluations",
    "delete_evaluation_log_channel",
    "delete_trial_member",
    "delete_trial_member_end_survey",
    "extend_trial_member_end_date",
    "get_all_evaluation_log_channels",
    "get_all_trial_member_end_dates",
    "get_class_change_candidates",
    "get_evaluated_trial_member_ids",
    "get_evaluation_log_channel",
    "get_expired_trial_member_end_surveys",
    "get_trial_member",
    "get_trial_member_class_and_thread",
    "get_trial_member_end_date",
    "get_trial_member_end_survey",
    "get_trial_member_thread",
    "has_extension",
    "set_evaluation_log_channel",
    "update_trial_member_class",
    "update_trial_member_thread",
]
