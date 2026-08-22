"""宿屋チャンネルのSQL実装。

部屋の作成と削除、人数制限や公開設定の更新、管理者の登録と判定を担当します。
"""

from datetime import datetime

from .core import get_connection


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


__all__ = [
    "add_hotel_manager",
    "create_hotel_room",
    "delete_hotel_room",
    "get_all_hotels",
    "get_hotel_by_channel",
    "get_hotel_by_text_channel",
    "is_hotel_manager",
    "update_hotel_limit",
    "update_hotel_private",
]
