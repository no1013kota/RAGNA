"""出場者セットと、事前登録・出場する使い魔の同期のSQL実装（GAME_SPEC 9節）。

プレイヤーごとの事前登録（優先順の本体）と、ギルドの出場する使い魔（その上位を
実際に出す枠）の読み書き、および両者の双方向同期を担当します。

設計上の要点（docs/GAME_SPEC.md 9節・34.18節）:

- 「事前登録＝優先順の本体、出場する使い魔＝その上位を実際に出す枠」という関係を、
  どちらから変更しても保てるようにします。片方だけ変えるともう片方が古いままになり、
  どちらが本当の編成なのか分からなくなるためです。同期する2方向は互いを前提に
  するので、同じファイルに置いています。
- 同期するのは「編成ロックされていないギルドの出場者」だけです。事前登録そのものは、
  無所属でも編成ロック中でもいつでも変更できます（34.18節）。
- 編成を書き換える処理は ``BEGIN IMMEDIATE`` で始め、体数・COST上限の判定と
  書き込みを同じトランザクションに収めます（27節）。
"""

from __future__ import annotations

import logging
import sqlite3

from contextlib import closing
from typing import Any

from .battle_common import (
    ACTIVE_BATTLE_STATUSES,
    error_result,
    now_iso,
    ok_result,
)
from .core import get_connection


logger = logging.getLogger(__name__)


# ==================================================
# 出場者セット（9節）
# ==================================================
def _familiar_cost(conn: sqlite3.Connection, instance_id: int) -> int:
    """所有使い魔1体の編成COSTを返す。使い魔マスターの同期結果から引く。"""

    row = conn.execute(
        """
        SELECT master.cost
        FROM player_familiars AS owned
        JOIN familiars AS master
          ON master.familiar_id = owned.familiar_id
        WHERE owned.instance_id = ?
        """,
        (instance_id,),
    ).fetchone()

    return int(row["cost"]) if row is not None else 0


def _entry_cost_total(conn: sqlite3.Connection, guild_id: int) -> int:
    """セット済みの使い魔の合計COSTを返す。"""

    row = conn.execute(
        """
        SELECT COALESCE(SUM(master.cost), 0) AS total
        FROM guild_battle_entries AS entry
        JOIN player_familiars AS owned
          ON owned.instance_id = entry.instance_id
        JOIN familiars AS master
          ON master.familiar_id = owned.familiar_id
        WHERE entry.guild_id = ?
        """,
        (guild_id,),
    ).fetchone()

    return int(row["total"]) if row is not None else 0


def entry_cost_total(guild_id: int) -> int:
    """セット済みの使い魔の合計COSTを返す（表示用の公開窓口）。"""

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        return _entry_cost_total(conn, guild_id)


def get_player_battle_familiars(user_id: int) -> list[dict[str, Any]]:
    """プレイヤーがバトル用に事前登録した使い魔を、優先順で返す（9節）。

    ギルドに所属していなくても、バトル中でも登録できます。所有していない
    個体（売却・合成済み）は結果から除きます。
    """

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT
                entry.priority,
                entry.instance_id,
                owned.familiar_id,
                owned.level
            FROM player_battle_familiars AS entry
            JOIN player_familiars AS owned
              ON owned.instance_id = entry.instance_id
            WHERE entry.user_id = ?
              AND owned.user_id = ?
              AND owned.status = 'owned'
            ORDER BY entry.priority
            """,
            (user_id, user_id),
        ).fetchall()

    return [dict(row) for row in rows]


# ==================================================
# 事前登録と出場する使い魔の同期（9節）
# ==================================================
# 「事前登録＝優先順の本体、出場する使い魔＝その上位を実際に出す枠」という
# 関係を、どちらから変更しても保てるようにします。片方だけ変えても
# もう片方が古いままだと、どちらが本当の編成なのか分からなくなるためです。
#
# 同期するのは「編成ロックされていないギルドの出場者」だけです。事前登録
# そのものは、無所属でも編成ロック中でもいつでも変更できます（34.18節）。
def _write_registration(
    conn: sqlite3.Connection,
    user_id: int,
    ordered: list[int],
    timestamp: str,
) -> None:
    """事前登録を、渡された順番のまま1番から書き直す。

    ``PRIMARY KEY (user_id, priority)`` と個体の一意制約があるため、
    途中を差し替えるのではなく、いったん消してから並べ直します。
    """

    conn.execute(
        "DELETE FROM player_battle_familiars WHERE user_id = ?", (user_id,)
    )

    for priority, instance_id in enumerate(ordered, start=1):
        conn.execute(
            """
            INSERT INTO player_battle_familiars
                (user_id, priority, instance_id, updated_at)
            VALUES
                (?, ?, ?, ?)
            """,
            (user_id, priority, instance_id, timestamp),
        )


def _registered_instance_ids(conn: sqlite3.Connection, user_id: int) -> list[int]:
    """事前登録している所有使い魔IDを優先順で返す。"""

    rows = conn.execute(
        """
        SELECT instance_id
        FROM player_battle_familiars
        WHERE user_id = ?
        ORDER BY priority
        """,
        (user_id,),
    ).fetchall()

    return [int(row["instance_id"]) for row in rows]


def _entry_instance_ids(
    conn: sqlite3.Connection, guild_id: int, user_id: int
) -> list[int]:
    """その人が出場させる使い魔IDを枠順で返す。"""

    rows = conn.execute(
        """
        SELECT instance_id
        FROM guild_battle_entries
        WHERE guild_id = ?
          AND user_id = ?
        ORDER BY entry_slot
        """,
        (guild_id, user_id),
    ).fetchall()

    return [int(row["instance_id"]) for row in rows]


def _syncable_assignment(
    conn: sqlite3.Connection, user_id: int
) -> tuple[int, int] | None:
    """同期対象のギルドと、その人の割り当て体数を返す。

    次のいずれかに当てはまる場合は ``None`` を返し、事前登録だけを更新します。

    - どのギルドにも所属していない
    - そのギルドの出場者に選ばれていない
    - そのギルドが編成ロック中（バトル成立後は編成を凍結する）
    """

    row = conn.execute(
        """
        SELECT member.guild_id, roster.familiar_count
        FROM guild_members AS member
        JOIN guilds AS guild
          ON guild.guild_id = member.guild_id
        JOIN guild_battle_members AS roster
          ON roster.guild_id = member.guild_id
         AND roster.user_id = member.user_id
        WHERE member.user_id = ?
          AND guild.roster_locked = 0
        """,
        (user_id,),
    ).fetchone()

    if row is None:
        return None

    return int(row["guild_id"]), int(row["familiar_count"] or 0)


def _sync_entries_from_registration(
    conn: sqlite3.Connection,
    guild_id: int,
    user_id: int,
    limit: int,
    timestamp: str,
    *,
    max_total_cost: int,
) -> dict[str, list[int]]:
    """事前登録の上位から、その人の出場する使い魔を作り直す（9.1節）。

    いったん本人の枠をすべて空けてから、事前登録の優先順で埋め直します。
    ギルドの合計COST上限を超える候補は飛ばすため、採用できた分だけが枠へ
    入ります。飛ばした個体は ``cost_skipped`` で返し、呼び出し側が
    「上限のためセットできなかった」と伝えられるようにします。
    """

    released = _entry_instance_ids(conn, guild_id, user_id)

    conn.execute(
        """
        DELETE FROM guild_battle_entries
        WHERE guild_id = ?
          AND user_id = ?
        """,
        (guild_id, user_id),
    )
    _renumber_entries(conn, guild_id, timestamp)

    cost_skipped: list[int] = []
    adopted = _adopt_registered_familiars(
        conn,
        guild_id,
        [(user_id, limit)],
        timestamp,
        max_total_cost=max_total_cost,
        cost_skipped=cost_skipped,
    )

    return {
        "adopted": adopted,
        "released": [
            instance_id for instance_id in released if instance_id not in adopted
        ],
        "cost_skipped": cost_skipped,
    }


def _sync_registration_from_entries(
    conn: sqlite3.Connection,
    guild_id: int,
    user_id: int,
    timestamp: str,
    *,
    max_units: int,
) -> bool:
    """出場する使い魔の並びを、事前登録の先頭へ写す（9.3節）。

    出場している使い魔をそのまま優先順の先頭に置き、出場していない登録は
    その後ろへ残します。次にメンバーセットをやり直したときも、いま出して
    いる編成がそのまま選ばれます。変更した場合だけ ``True`` を返します。
    """

    entries = _entry_instance_ids(conn, guild_id, user_id)
    registered = _registered_instance_ids(conn, user_id)

    chosen = set(entries)
    ordered = entries + [
        instance_id for instance_id in registered if instance_id not in chosen
    ]

    if max_units > 0:
        ordered = ordered[:max_units]

    if ordered == registered:
        return False

    _write_registration(conn, user_id, ordered, timestamp)
    return True


def set_player_battle_familiars(
    user_id: int, instance_ids: list[int]
) -> dict[str, Any]:
    """バトル用の事前登録を、渡された順番で登録し直す（9節）。

    所有していない個体が混ざっていた場合は ``not_owned`` を返し、登録内容は
    変えません。編成ロックやバトルの進行状況とは無関係にいつでも変更できます。

    出場者に選ばれていて、そのギルドが編成ロック中でない場合は、出場する
    使い魔も新しい優先順から作り直します。戻り値の ``adopted`` と
    ``released`` が入れ替わった使い魔、``cost_skipped`` がギルドの合計COST
    上限のためにセットできなかった使い魔です。
    """

    ordered = [int(instance_id) for instance_id in instance_ids]
    if len(set(ordered)) != len(ordered):
        return error_result("duplicate_familiar")

    from game.master_data import load_master_data

    master = load_master_data()
    timestamp = now_iso()
    synced: dict[str, list[int]] = {
        "adopted": [],
        "released": [],
        "cost_skipped": [],
    }

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        with conn:
            conn.execute("BEGIN IMMEDIATE")

            if ordered:
                placeholders = ", ".join("?" for _ in ordered)
                owned = conn.execute(
                    f"""
                    SELECT instance_id
                    FROM player_familiars
                    WHERE instance_id IN ({placeholders})
                      AND user_id = ?
                      AND status = 'owned'
                    """,
                    [*ordered, user_id],
                ).fetchall()

                if len(owned) != len(ordered):
                    conn.rollback()
                    return error_result("not_owned")

            _write_registration(conn, user_id, ordered, timestamp)

            assignment = _syncable_assignment(conn, user_id)
            if assignment is not None:
                guild_id, limit = assignment
                synced = _sync_entries_from_registration(
                    conn,
                    guild_id,
                    user_id,
                    limit,
                    timestamp,
                    max_total_cost=master.battle.max_total_cost,
                )

    return ok_result(
        count=len(ordered),
        adopted=synced["adopted"],
        released=synced["released"],
        cost_skipped=synced["cost_skipped"],
    )


def get_battle_roster(guild_id: int) -> list[dict[str, Any]]:
    """ギルドの出場者セット（1～5人）をスロット順で返す。

    ``familiar_count`` は、マスターがその出場者へ割り当てた使い魔の体数です。
    """

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT guild_id, user_id, slot, familiar_count
            FROM guild_battle_members
            WHERE guild_id = ?
            ORDER BY slot
            """,
            (guild_id,),
        ).fetchall()

    return [dict(row) for row in rows]


def get_battle_entries(guild_id: int) -> list[dict[str, Any]]:
    """ギルドがセットした使い魔（最大5体）を枠順で返す（9節）。"""

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT guild_id, entry_slot, user_id, instance_id
            FROM guild_battle_entries
            WHERE guild_id = ?
            ORDER BY entry_slot
            """,
            (guild_id,),
        ).fetchall()

    return [dict(row) for row in rows]


def count_member_entries(guild_id: int, user_id: int) -> int:
    """その出場者がセット済みの使い魔数を返す。"""

    with closing(get_connection()) as conn:
        row = conn.execute(
            """
            SELECT COUNT(*)
            FROM guild_battle_entries
            WHERE guild_id = ?
              AND user_id = ?
            """,
            (guild_id, user_id),
        ).fetchone()

    return int(row[0]) if row else 0


def set_battle_roster(
    guild_id: int, assignments: list[tuple[int, int]]
) -> dict[str, Any]:
    """出場者セットと、1人あたりの使い魔体数を登録し直す（9節）。

    ``assignments`` は ``[(出場者ID, 使い魔の体数), ...]`` で、渡した順に
    ``slot=1`` から並べます。登録後は各出場者の事前登録（優先順）から自動で
    使い魔を採用します。既に本人が選び直していた分は、割り当て体数に収まる
    範囲でそのまま残します。編成ロック中は変更できません。

    error: "duplicate_member" / "not_member" / "roster_locked" /
           "invalid_count" / "member_limit" / "entries_full"
    """

    ordered = [(int(user_id), int(count)) for user_id, count in assignments]
    ordered_ids = [user_id for user_id, _ in ordered]

    if len(set(ordered_ids)) != len(ordered_ids):
        return error_result("duplicate_member", added=[], removed=[], released=[])

    if any(count < 1 for _, count in ordered):
        return error_result("invalid_count", added=[], removed=[], released=[])

    from game.master_data import load_master_data

    master = load_master_data()
    limit = master.familiar_limit_per_member(len(ordered))

    if any(count > limit for _, count in ordered):
        return error_result(
            "member_limit", added=[], removed=[], released=[], limit=limit
        )

    if sum(count for _, count in ordered) > master.battle.max_units:
        return error_result(
            "entries_full",
            added=[],
            removed=[],
            released=[],
            max_units=master.battle.max_units,
        )

    timestamp = now_iso()

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        with conn:
            conn.execute("BEGIN IMMEDIATE")

            guild_row = conn.execute(
                """
                SELECT roster_locked
                FROM guilds
                WHERE guild_id = ?
                """,
                (guild_id,),
            ).fetchone()

            if guild_row is not None and guild_row["roster_locked"]:
                conn.rollback()
                return error_result("roster_locked", added=[], removed=[], released=[])

            member_rows = conn.execute(
                """
                SELECT user_id
                FROM guild_members
                WHERE guild_id = ?
                """,
                (guild_id,),
            ).fetchall()
            member_ids = {row["user_id"] for row in member_rows}

            if any(user_id not in member_ids for user_id in ordered_ids):
                conn.rollback()
                return error_result("not_member", added=[], removed=[], released=[])

            current_rows = conn.execute(
                """
                SELECT user_id
                FROM guild_battle_members
                WHERE guild_id = ?
                ORDER BY slot
                """,
                (guild_id,),
            ).fetchall()
            current = [row["user_id"] for row in current_rows]

            conn.execute(
                """
                DELETE FROM guild_battle_members
                WHERE guild_id = ?
                """,
                (guild_id,),
            )

            for slot, (user_id, count) in enumerate(ordered, start=1):
                conn.execute(
                    """
                    INSERT INTO guild_battle_members
                        (guild_id, user_id, slot, familiar_count,
                         instance_id, updated_at)
                    VALUES
                        (?, ?, ?, ?, NULL, ?)
                    """,
                    (guild_id, user_id, slot, count, timestamp),
                )

            released = _release_invalid_entries(conn, guild_id, ordered, timestamp)
            adopted = _adopt_registered_familiars(
                conn,
                guild_id,
                ordered,
                timestamp,
                max_total_cost=master.battle.max_total_cost,
            )

    added = [user_id for user_id in ordered_ids if user_id not in current]
    removed = [user_id for user_id in current if user_id not in set(ordered_ids)]

    return ok_result(added=added, removed=removed, released=released, adopted=adopted)


def _release_invalid_entries(
    conn: sqlite3.Connection,
    guild_id: int,
    assignments: list[tuple[int, int]],
    timestamp: str,
) -> list[int]:
    """出場者から外れた人、および割り当て体数を超えた分のセットを解除する。

    戻り値は解除した所有使い魔ID。トランザクション内から呼びます。
    """

    limits = {user_id: count for user_id, count in assignments}

    rows = conn.execute(
        """
        SELECT entry_slot, user_id, instance_id
        FROM guild_battle_entries
        WHERE guild_id = ?
        ORDER BY entry_slot
        """,
        (guild_id,),
    ).fetchall()

    kept_per_user: dict[int, int] = {}
    released: list[int] = []

    for row in rows:
        user_id = int(row["user_id"])
        used = kept_per_user.get(user_id, 0)

        if used < limits.get(user_id, 0):
            kept_per_user[user_id] = used + 1
            continue

        released.append(int(row["instance_id"]))
        conn.execute(
            """
            DELETE FROM guild_battle_entries
            WHERE guild_id = ?
              AND entry_slot = ?
            """,
            (guild_id, row["entry_slot"]),
        )

    if released:
        _renumber_entries(conn, guild_id, timestamp)

    return released


def _adopt_registered_familiars(
    conn: sqlite3.Connection,
    guild_id: int,
    assignments: list[tuple[int, int]],
    timestamp: str,
    max_total_cost: int = 0,
    cost_skipped: list[int] | None = None,
) -> list[int]:
    """割り当て体数に足りない分を、事前登録の優先順から自動で埋める（9節）。

    ``max_total_cost`` が0より大きい場合は、合計COSTがその値を超える使い魔を
    飛ばして次の候補へ進みます。``cost_skipped`` を渡すと、そのとき飛ばした
    所有使い魔IDを追記します。戻り値は自動採用した所有使い魔IDです。
    トランザクション内から呼びます。
    """

    used_rows = conn.execute(
        """
        SELECT entry_slot, user_id, instance_id
        FROM guild_battle_entries
        WHERE guild_id = ?
        """,
        (guild_id,),
    ).fetchall()

    used_counts: dict[int, int] = {}
    used_instances = set()

    for row in used_rows:
        user_id = int(row["user_id"])
        used_counts[user_id] = used_counts.get(user_id, 0) + 1
        used_instances.add(int(row["instance_id"]))

    next_slot = max(
        (int(row["entry_slot"]) for row in used_rows), default=0
    ) + 1
    adopted: list[int] = []
    cost_total = _entry_cost_total(conn, guild_id) if max_total_cost > 0 else 0

    for user_id, count in assignments:
        missing = count - used_counts.get(user_id, 0)
        if missing <= 0:
            continue

        candidates = conn.execute(
            """
            SELECT entry.instance_id
            FROM player_battle_familiars AS entry
            JOIN player_familiars AS owned
              ON owned.instance_id = entry.instance_id
            WHERE entry.user_id = ?
              AND owned.user_id = ?
              AND owned.status = 'owned'
            ORDER BY entry.priority
            """,
            (user_id, user_id),
        ).fetchall()

        for row in candidates:
            if missing <= 0:
                break

            instance_id = int(row["instance_id"])
            if instance_id in used_instances:
                continue

            if max_total_cost > 0:
                adding = _familiar_cost(conn, instance_id)
                if cost_total + adding > max_total_cost:
                    # COST上限を超える候補は飛ばし、次の登録へ進む
                    if cost_skipped is not None:
                        cost_skipped.append(instance_id)
                    continue
                cost_total += adding

            conn.execute(
                """
                INSERT INTO guild_battle_entries
                    (guild_id, entry_slot, user_id, instance_id, updated_at)
                VALUES
                    (?, ?, ?, ?, ?)
                """,
                (guild_id, next_slot, user_id, instance_id, timestamp),
            )

            used_instances.add(instance_id)
            adopted.append(instance_id)
            next_slot += 1
            missing -= 1

    return adopted


def _renumber_entries(
    conn: sqlite3.Connection, guild_id: int, timestamp: str
) -> None:
    """使い魔の枠番号を1から詰め直す。"""

    rows = conn.execute(
        """
        SELECT entry_slot, user_id, instance_id
        FROM guild_battle_entries
        WHERE guild_id = ?
        ORDER BY entry_slot
        """,
        (guild_id,),
    ).fetchall()

    conn.execute(
        "DELETE FROM guild_battle_entries WHERE guild_id = ?", (guild_id,)
    )

    for slot, row in enumerate(rows, start=1):
        conn.execute(
            """
            INSERT INTO guild_battle_entries
                (guild_id, entry_slot, user_id, instance_id, updated_at)
            VALUES
                (?, ?, ?, ?, ?)
            """,
            (guild_id, slot, row["user_id"], row["instance_id"], timestamp),
        )


def renumber_battle_entries(
    conn: sqlite3.Connection, guild_id: int, timestamp: str | None = None
) -> None:
    """使い魔の枠番号を1から詰め直す。

    追放・脱退で行を削除した側から、同じトランザクション内で呼びます。
    呼び出し元の ``row_factory`` 設定に依存しないよう、一時的に切り替えます。
    """

    previous = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        _renumber_entries(conn, guild_id, timestamp or now_iso())
    finally:
        conn.row_factory = previous


def add_battle_entry(
    guild_id: int,
    user_id: int,
    instance_id: int,
    *,
    max_units: int,
    max_total_cost: int = 0,
) -> dict[str, Any]:
    """出場者が自分の使い魔を1体セットする（9節・10.3節）。

    体数の上限は、マスターがその出場者へ割り当てた ``familiar_count`` です。
    自動採用された使い魔を差し替えたい場合は、先に外してから追加します。
    ``max_total_cost`` が0より大きい場合は、ギルドの合計COSTがその値を超える
    セットを拒否します（10.6節）。セットした使い魔は本人の事前登録の先頭へも
    反映し、両方の並びを一致させます（9.3節）。
    error: "roster_locked" / "not_selected" / "entries_full" /
           "member_limit" / "already_set" / "not_owned" / "cost_over"
    """

    timestamp = now_iso()

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        with conn:
            conn.execute("BEGIN IMMEDIATE")

            guild_row = conn.execute(
                "SELECT roster_locked FROM guilds WHERE guild_id = ?", (guild_id,)
            ).fetchone()

            if guild_row is not None and guild_row["roster_locked"]:
                conn.rollback()
                return error_result("roster_locked")

            selected = conn.execute(
                """
                SELECT familiar_count
                FROM guild_battle_members
                WHERE guild_id = ?
                  AND user_id = ?
                """,
                (guild_id, user_id),
            ).fetchone()

            if selected is None:
                conn.rollback()
                return error_result("not_selected")

            per_member_limit = int(selected["familiar_count"] or 0)

            owned = conn.execute(
                """
                SELECT user_id, status
                FROM player_familiars
                WHERE instance_id = ?
                """,
                (instance_id,),
            ).fetchone()

            if (
                owned is None
                or int(owned["user_id"]) != int(user_id)
                or owned["status"] != "owned"
            ):
                conn.rollback()
                return error_result("not_owned")

            counts = conn.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    COALESCE(MAX(entry_slot), 0) AS last_slot,
                    SUM(CASE WHEN user_id = ? THEN 1 ELSE 0 END) AS mine,
                    SUM(CASE WHEN instance_id = ? THEN 1 ELSE 0 END) AS duplicated
                FROM guild_battle_entries
                WHERE guild_id = ?
                """,
                (user_id, instance_id, guild_id),
            ).fetchone()

            if int(counts["duplicated"] or 0):
                conn.rollback()
                return error_result("already_set")

            # 両方に該当する場合は、本人の上限を先に案内する（より具体的なため）
            if int(counts["mine"] or 0) >= per_member_limit:
                conn.rollback()
                return error_result("member_limit", limit=per_member_limit)

            if int(counts["total"] or 0) >= max_units:
                conn.rollback()
                return error_result("entries_full")

            if max_total_cost > 0:
                current_cost = _entry_cost_total(conn, guild_id)
                adding = _familiar_cost(conn, instance_id)

                if current_cost + adding > max_total_cost:
                    conn.rollback()
                    return error_result(
                        "cost_over",
                        current_cost=current_cost,
                        adding_cost=adding,
                        max_total_cost=max_total_cost,
                    )

            # 枠番号は詰め直しているが、途中に抜けがあっても衝突しないようにする
            next_slot = int(counts["last_slot"] or 0) + 1
            conn.execute(
                """
                INSERT INTO guild_battle_entries
                    (guild_id, entry_slot, user_id, instance_id, updated_at)
                VALUES
                    (?, ?, ?, ?, ?)
                """,
                (guild_id, next_slot, user_id, instance_id, timestamp),
            )

            _sync_registration_from_entries(
                conn, guild_id, user_id, timestamp, max_units=max_units
            )

    return ok_result(entry_slot=next_slot)


def remove_battle_entry(
    guild_id: int, user_id: int, instance_id: int
) -> dict[str, Any]:
    """自分がセットした使い魔を1体解除する。

    解除した使い魔は本人の事前登録からも外します。片方だけ残ると、次に
    メンバーセットをやり直したときに解除したはずの使い魔が戻るためです（9.3節）。

    error: "roster_locked" / "not_set"
    """

    from game.master_data import load_master_data

    max_units = load_master_data().battle.max_units
    timestamp = now_iso()

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        with conn:
            conn.execute("BEGIN IMMEDIATE")

            guild_row = conn.execute(
                "SELECT roster_locked FROM guilds WHERE guild_id = ?", (guild_id,)
            ).fetchone()

            if guild_row is not None and guild_row["roster_locked"]:
                conn.rollback()
                return error_result("roster_locked")

            deleted = conn.execute(
                """
                DELETE FROM guild_battle_entries
                WHERE guild_id = ?
                  AND user_id = ?
                  AND instance_id = ?
                """,
                (guild_id, user_id, instance_id),
            )

            if deleted.rowcount == 0:
                conn.rollback()
                return error_result("not_set")

            _renumber_entries(conn, guild_id, timestamp)

            registered = _registered_instance_ids(conn, user_id)
            if instance_id in registered:
                registered.remove(instance_id)
                _write_registration(conn, user_id, registered, timestamp)

            _sync_registration_from_entries(
                conn, guild_id, user_id, timestamp, max_units=max_units
            )

    return ok_result()


def swap_battle_entry(
    guild_id: int,
    user_id: int,
    removed_instance_id: int,
    new_instance_id: int,
    *,
    max_units: int,
    max_total_cost: int = 0,
) -> dict[str, Any]:
    """セット済みの1体を、同じ枠のまま別の使い魔へ入れ替える（9.3節）。

    外してから入れ直すと枠順と事前登録の優先順が末尾へ動いてしまううえ、
    途中で失敗すると元へ戻す処理が必要になります。1つの処理としてまとめ、
    同じ枠・同じ優先順のまま差し替えます。

    error: "roster_locked" / "not_set" / "already_set" / "not_owned" / "cost_over"
    """

    removed_instance_id = int(removed_instance_id)
    new_instance_id = int(new_instance_id)

    if removed_instance_id == new_instance_id:
        return error_result("already_set")

    timestamp = now_iso()

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        with conn:
            conn.execute("BEGIN IMMEDIATE")

            guild_row = conn.execute(
                "SELECT roster_locked FROM guilds WHERE guild_id = ?", (guild_id,)
            ).fetchone()

            if guild_row is not None and guild_row["roster_locked"]:
                conn.rollback()
                return error_result("roster_locked")

            current = conn.execute(
                """
                SELECT entry_slot
                FROM guild_battle_entries
                WHERE guild_id = ?
                  AND user_id = ?
                  AND instance_id = ?
                """,
                (guild_id, user_id, removed_instance_id),
            ).fetchone()

            if current is None:
                conn.rollback()
                return error_result("not_set")

            entry_slot = int(current["entry_slot"])

            duplicated = conn.execute(
                """
                SELECT 1
                FROM guild_battle_entries
                WHERE guild_id = ?
                  AND instance_id = ?
                """,
                (guild_id, new_instance_id),
            ).fetchone()

            if duplicated is not None:
                conn.rollback()
                return error_result("already_set")

            owned = conn.execute(
                """
                SELECT user_id, status
                FROM player_familiars
                WHERE instance_id = ?
                """,
                (new_instance_id,),
            ).fetchone()

            if (
                owned is None
                or int(owned["user_id"]) != int(user_id)
                or owned["status"] != "owned"
            ):
                conn.rollback()
                return error_result("not_owned")

            if max_total_cost > 0:
                current_cost = _entry_cost_total(conn, guild_id)
                leaving = _familiar_cost(conn, removed_instance_id)
                adding = _familiar_cost(conn, new_instance_id)

                if current_cost - leaving + adding > max_total_cost:
                    conn.rollback()
                    return error_result(
                        "cost_over",
                        current_cost=current_cost - leaving,
                        adding_cost=adding,
                        max_total_cost=max_total_cost,
                    )

            conn.execute(
                """
                UPDATE guild_battle_entries
                SET instance_id = ?,
                    updated_at = ?
                WHERE guild_id = ?
                  AND entry_slot = ?
                """,
                (new_instance_id, timestamp, guild_id, entry_slot),
            )

            # 事前登録も同じ位置で差し替える。外した方は1つ下げて控えに残し、
            # 登録から消えてしまわないようにする。
            registered = _registered_instance_ids(conn, user_id)
            if new_instance_id in registered:
                registered.remove(new_instance_id)

            if removed_instance_id in registered:
                position = registered.index(removed_instance_id)
                registered[position] = new_instance_id

                # 上限に余裕があるときだけ、外した方を1つ下の控えへ残す。
                # 余裕が無いのに押し込むと、末尾の登録が黙って消えてしまう。
                if max_units <= 0 or len(registered) < max_units:
                    registered.insert(position + 1, removed_instance_id)
            else:
                registered.append(new_instance_id)
                if max_units > 0:
                    del registered[max_units:]

            _write_registration(conn, user_id, registered, timestamp)
            _sync_registration_from_entries(
                conn, guild_id, user_id, timestamp, max_units=max_units
            )

    return ok_result(entry_slot=entry_slot)


def clear_roster_familiars(guild_id: int) -> None:
    """ギルドの使い魔セットだけを解除する（26.2節）。"""

    with closing(get_connection()) as conn:
        with conn:
            conn.execute(
                "DELETE FROM guild_battle_entries WHERE guild_id = ?", (guild_id,)
            )


def get_locked_instance_ids() -> set[int]:
    """合成・売却を禁止する所有使い魔のIDを返す（10.2節）。

    編成ロック中ギルドのセット済み使い魔と、進行中バトルの戦闘用使い魔が
    参照している個体を合わせた集合です。
    """

    placeholders = ", ".join("?" for _ in ACTIVE_BATTLE_STATUSES)

    with closing(get_connection()) as conn:
        roster_rows = conn.execute(
            """
            SELECT entry.instance_id
            FROM guild_battle_entries AS entry
            JOIN guilds AS guild
              ON guild.guild_id = entry.guild_id
            WHERE guild.roster_locked = 1
            """
        ).fetchall()

        unit_rows = conn.execute(
            f"""
            SELECT unit.familiar_instance_id
            FROM guild_battle_units AS unit
            JOIN guild_battles AS battle
              ON battle.battle_id = unit.battle_id
            WHERE battle.status IN ({placeholders})
            """,
            ACTIVE_BATTLE_STATUSES,
        ).fetchall()

    locked = {row[0] for row in roster_rows if row[0] is not None}
    locked.update(row[0] for row in unit_rows if row[0] is not None)

    return locked
