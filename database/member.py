"""招待ポイント・招待特典のSQL実装。

招待ポイントの参照と増減、付与履歴、宿屋の割引率や開始チケットなどの特典を担当します。
"""

from .core import get_connection


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


__all__ = [
    "add_hotel_free_rate",
    "add_invite_points",
    "add_invite_reward",
    "get_hotel_free_rate",
    "get_invite_points",
    "has_start_ticket",
    "remove_start_ticket",
    "set_start_ticket",
    "spend_invite_points",
]
