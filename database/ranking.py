"""ランキング集計のSQL実装。

評価数・所持coin・VC時間・XP・招待ポイントの並び替え済み一覧を担当します。
"""

from .core import get_connection


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


__all__ = [
    "get_balance_ranking",
    "get_evaluator_review_ranking",
    "get_invite_ranking",
    "get_vc_ranking",
    "get_xp_ranking",
]
