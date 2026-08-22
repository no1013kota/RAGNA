"""VC滞在時間・XPのSQL実装。

通話時間とXPの累計・月間値の加算、月替わりの状態管理とリセットを担当します。
"""

from .core import get_connection


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


__all__ = [
    "add_vc_time",
    "add_vc_time_batch",
    "get_vc_month_state",
    "get_vc_time",
    "reset_monthly_vc_data",
    "set_vc_month_state",
]
