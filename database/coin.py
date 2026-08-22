"""coin残高・送金・月次報酬のSQL実装。

残高の参照と増減、ユーザー間の送金、取引履歴、月次報酬の支給記録を担当します。
"""

import sqlite3

from contextlib import closing

from .core import get_connection


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


__all__ = [
    "add_balance",
    "add_transaction",
    "get_balance",
    "get_monthly_reward_state",
    "grant_monthly_reward",
    "set_monthly_reward_state",
    "subtract_balance_if_enough",
    "transfer_balance",
]
