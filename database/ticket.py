"""お問い合わせチャンネルのSQL実装。

チケットの作成と削除、対応状況（open / review / closed）の更新を担当します。
"""

from .core import get_connection


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


__all__ = [
    "close_ticket",
    "create_ticket",
    "delete_ticket",
    "get_ticket",
    "get_ticket_by_owner",
    "reopen_ticket",
    "review_ticket",
]
