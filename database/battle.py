"""RAGNA Onlineのギルドバトル関連データアクセス。

出場者セット、バトル申請・募集、進行中バトルの状態保存、終了処理、報酬付与、
運営操作ログまでを担当します。SQLはこのモジュールへ閉じ込め、Cogやgameパッケージ
からは公開関数だけを呼び出してください。

設計上の要点（docs/GAME_SPEC.md 9・12・13・14・26・27・29節）:

- ``guild_battle_locks`` はギルドIDが主キーです。申請・募集・進行中バトルを
  合わせて1ギルド1件に制限し、二重参加を防ぎます（12.1節・27節）。
- 募集への同時申込みは ``claim_battle_recruitment`` の ``BEGIN IMMEDIATE`` +
  ``status='open'`` 条件付きUPDATEで、最初の1件だけを成立させます（12.2節）。
- ボタンの二重押しや再送で同じ行動を2回処理しないよう、``save_battle_state`` は
  ``action_seq`` による楽観ロックで更新します（16節・27節）。
- バトル終了処理は二重実行を防ぐため、進行中の状態からの遷移だけを許可します（26.2節）。
"""

from __future__ import annotations

import json
import logging
import sqlite3

from contextlib import closing
from datetime import datetime, timedelta, timezone
from typing import Any

from game.models import BattleEffectState, BattleLogEntry, BattleState, BattleUnit

from .core import get_connection


logger = logging.getLogger(__name__)

# 進行中とみなすバトル状態。ロック解放や重複参加の判定に使う。
ACTIVE_BATTLE_STATUSES = ("preparing", "in_progress", "paused")

# 勝敗を戦績へ反映する結果値
_RESULT_GUILD_A = "guild_a"
_RESULT_GUILD_B = "guild_b"
_RESULT_DRAW = "draw"


# ==================================================
# 共通ヘルパ
# ==================================================
def _now() -> str:
    """UTCのISO 8601文字列を返す。"""

    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    """1行を辞書へ変換する。行が無い場合は ``None`` を返す。"""

    if row is None:
        return None
    return dict(row)


def _load_json_dict(raw: str | None) -> dict[str, Any]:
    """JSON列を辞書として読み込む。壊れていても処理を止めない。"""

    if not raw:
        return {}

    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        logger.warning(f"JSON列の読み込みに失敗しました: {raw!r}")
        return {}

    return value if isinstance(value, dict) else {}


def _load_json_list(raw: str | None) -> list[Any]:
    """JSON列をリストとして読み込む。壊れていても処理を止めない。"""

    if not raw:
        return []

    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        logger.warning(f"JSON列の読み込みに失敗しました: {raw!r}")
        return []

    return list(value) if isinstance(value, list) else []


def _dump_json(value: Any) -> str:
    """辞書・リストをJSON文字列へ変換する。空のリストは配列のまま保存する。"""

    if value is None:
        return "{}"

    return json.dumps(value, ensure_ascii=False)


def _event_type_value(event_type: Any) -> str:
    """ログの種別を文字列へ揃える。

    ``BattleEvent`` のような列挙型をそのまま ``str()`` すると
    ``BattleEvent.ATTACK`` のような値になってしまうため、値の側を取り出します。
    """

    return str(getattr(event_type, "value", event_type))


def _failure(error: str, **payload: Any) -> dict[str, Any]:
    """失敗時の戻り値を組み立てる。"""

    return {"ok": False, "error": error, **payload}


def _success(**payload: Any) -> dict[str, Any]:
    """成功時の戻り値を組み立てる。"""

    return {"ok": True, "error": None, **payload}


def _remaining_seconds(state: BattleState, guild_id: int, fallback: int) -> int:
    """ギルド持ち時間を取り出す。

    キーが文字列でも読めるようにし、値を持たない場合は保存済みの値
    （``fallback``）をそのまま維持します。持ち時間を誤って0へ書き換えると
    時間切れ敗北になるため、欠けている場合は上書きしません。
    """

    remaining = state.remaining_seconds or {}
    if guild_id in remaining:
        return int(remaining[guild_id])

    text_key = str(guild_id)
    if text_key in remaining:
        return int(remaining[text_key])

    return int(fallback)


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
        return _failure("duplicate_familiar")

    from game.master_data import load_master_data

    master = load_master_data()
    timestamp = _now()
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
                    return _failure("not_owned")

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

    return _success(
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
        return _failure("duplicate_member", added=[], removed=[], released=[])

    if any(count < 1 for _, count in ordered):
        return _failure("invalid_count", added=[], removed=[], released=[])

    from game.master_data import load_master_data

    master = load_master_data()
    limit = master.familiar_limit_per_member(len(ordered))

    if any(count > limit for _, count in ordered):
        return _failure(
            "member_limit", added=[], removed=[], released=[], limit=limit
        )

    if sum(count for _, count in ordered) > master.battle.max_units:
        return _failure(
            "entries_full",
            added=[],
            removed=[],
            released=[],
            max_units=master.battle.max_units,
        )

    timestamp = _now()

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
                return _failure("roster_locked", added=[], removed=[], released=[])

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
                return _failure("not_member", added=[], removed=[], released=[])

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

    return _success(added=added, removed=removed, released=released, adopted=adopted)


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
        _renumber_entries(conn, guild_id, timestamp or _now())
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

    timestamp = _now()

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        with conn:
            conn.execute("BEGIN IMMEDIATE")

            guild_row = conn.execute(
                "SELECT roster_locked FROM guilds WHERE guild_id = ?", (guild_id,)
            ).fetchone()

            if guild_row is not None and guild_row["roster_locked"]:
                conn.rollback()
                return _failure("roster_locked")

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
                return _failure("not_selected")

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
                return _failure("not_owned")

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
                return _failure("already_set")

            # 両方に該当する場合は、本人の上限を先に案内する（より具体的なため）
            if int(counts["mine"] or 0) >= per_member_limit:
                conn.rollback()
                return _failure("member_limit", limit=per_member_limit)

            if int(counts["total"] or 0) >= max_units:
                conn.rollback()
                return _failure("entries_full")

            if max_total_cost > 0:
                current_cost = _entry_cost_total(conn, guild_id)
                adding = _familiar_cost(conn, instance_id)

                if current_cost + adding > max_total_cost:
                    conn.rollback()
                    return _failure(
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

    return _success(entry_slot=next_slot)


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
    timestamp = _now()

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        with conn:
            conn.execute("BEGIN IMMEDIATE")

            guild_row = conn.execute(
                "SELECT roster_locked FROM guilds WHERE guild_id = ?", (guild_id,)
            ).fetchone()

            if guild_row is not None and guild_row["roster_locked"]:
                conn.rollback()
                return _failure("roster_locked")

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
                return _failure("not_set")

            _renumber_entries(conn, guild_id, timestamp)

            registered = _registered_instance_ids(conn, user_id)
            if instance_id in registered:
                registered.remove(instance_id)
                _write_registration(conn, user_id, registered, timestamp)

            _sync_registration_from_entries(
                conn, guild_id, user_id, timestamp, max_units=max_units
            )

    return _success()


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
        return _failure("already_set")

    timestamp = _now()

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        with conn:
            conn.execute("BEGIN IMMEDIATE")

            guild_row = conn.execute(
                "SELECT roster_locked FROM guilds WHERE guild_id = ?", (guild_id,)
            ).fetchone()

            if guild_row is not None and guild_row["roster_locked"]:
                conn.rollback()
                return _failure("roster_locked")

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
                return _failure("not_set")

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
                return _failure("already_set")

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
                return _failure("not_owned")

            if max_total_cost > 0:
                current_cost = _entry_cost_total(conn, guild_id)
                leaving = _familiar_cost(conn, removed_instance_id)
                adding = _familiar_cost(conn, new_instance_id)

                if current_cost - leaving + adding > max_total_cost:
                    conn.rollback()
                    return _failure(
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

    return _success(entry_slot=entry_slot)


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


# ==================================================
# バトル占有ロック（12.1節・27節）
# ==================================================
def acquire_battle_lock(
    conn: sqlite3.Connection,
    guild_id: int,
    lock_type: str,
    reference_id: int,
) -> bool:
    """同一トランザクション内でギルドの占有ロックを取得する。

    既にロックがある場合は何もせず ``False`` を返します。呼び出し側は
    ``BEGIN IMMEDIATE`` を開始済みの接続を渡してください。
    """

    inserted = conn.execute(
        """
        INSERT OR IGNORE INTO guild_battle_locks
            (guild_id, lock_type, reference_id, created_at)
        VALUES
            (?, ?, ?, ?)
        """,
        (guild_id, lock_type, reference_id, _now()),
    )

    return inserted.rowcount > 0


def get_battle_lock(guild_id: int) -> dict[str, Any] | None:
    """ギルドが保持している占有ロックを返す。"""

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT *
            FROM guild_battle_locks
            WHERE guild_id = ?
            """,
            (guild_id,),
        ).fetchone()

    return _row_to_dict(row)


def release_battle_lock(guild_id: int) -> None:
    """ギルドの占有ロックを解放する。"""

    with closing(get_connection()) as conn:
        with conn:
            conn.execute(
                """
                DELETE FROM guild_battle_locks
                WHERE guild_id = ?
                """,
                (guild_id,),
            )


# ==================================================
# バトル申請（12.1節）
# ==================================================
def create_battle_request(
    from_guild_id: int, to_guild_id: int, *, bet_coin: int | None = None
) -> dict[str, Any]:
    """特定ギルドへのバトル申請を作成し、双方のロックを取得する。

    ``bet_coin`` は申請したギルドマスターが決めたベット額（ギルド合計）です。
    """

    if from_guild_id == to_guild_id:
        return _failure("same_guild", request_id=None)

    timestamp = _now()

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        with conn:
            conn.execute("BEGIN IMMEDIATE")

            guild_rows = conn.execute(
                """
                SELECT guild_id
                FROM guilds
                WHERE guild_id IN (?, ?)
                  AND status = 'active'
                """,
                (from_guild_id, to_guild_id),
            ).fetchall()

            if len(guild_rows) != 2:
                conn.rollback()
                return _failure("guild_not_found", request_id=None)

            lock_rows = conn.execute(
                """
                SELECT guild_id
                FROM guild_battle_locks
                WHERE guild_id IN (?, ?)
                """,
                (from_guild_id, to_guild_id),
            ).fetchall()
            locked_ids = {row["guild_id"] for row in lock_rows}

            if from_guild_id in locked_ids:
                conn.rollback()
                return _failure("guild_busy", request_id=None)

            if to_guild_id in locked_ids:
                conn.rollback()
                return _failure("opponent_busy", request_id=None)

            inserted = conn.execute(
                """
                INSERT INTO guild_battle_requests
                    (from_guild_id, to_guild_id, status, bet_coin,
                     created_at, updated_at)
                VALUES
                    (?, ?, 'pending', ?, ?, ?)
                """,
                (from_guild_id, to_guild_id, bet_coin, timestamp, timestamp),
            )
            request_id = int(inserted.lastrowid)

            if not acquire_battle_lock(conn, from_guild_id, "request", request_id):
                conn.rollback()
                return _failure("guild_busy", request_id=None)

            if not acquire_battle_lock(conn, to_guild_id, "request", request_id):
                conn.rollback()
                return _failure("opponent_busy", request_id=None)

    return _success(request_id=request_id)


def get_battle_request(request_id: int) -> dict[str, Any] | None:
    """バトル申請を1件返す。"""

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT *
            FROM guild_battle_requests
            WHERE request_id = ?
            """,
            (request_id,),
        ).fetchone()

    return _row_to_dict(row)


def get_battle_request_by_message(message_id: int) -> dict[str, Any] | None:
    """申請パネルのメッセージIDからバトル申請を引く。"""

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT *
            FROM guild_battle_requests
            WHERE message_id = ?
            ORDER BY request_id DESC
            LIMIT 1
            """,
            (message_id,),
        ).fetchone()

    return _row_to_dict(row)


def get_pending_battle_request_for_guild(guild_id: int) -> dict[str, Any] | None:
    """ギルドが関係する未処理のバトル申請を返す（送信・受信を問わない）。"""

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT *
            FROM guild_battle_requests
            WHERE status = 'pending'
              AND (from_guild_id = ? OR to_guild_id = ?)
            ORDER BY request_id DESC
            LIMIT 1
            """,
            (guild_id, guild_id),
        ).fetchone()

    return _row_to_dict(row)


def set_battle_request_message(
    request_id: int,
    channel_id: int,
    message_id: int,
) -> None:
    """申請パネルのチャンネル・メッセージIDを保存する。"""

    with closing(get_connection()) as conn:
        with conn:
            conn.execute(
                """
                UPDATE guild_battle_requests
                SET channel_id = ?,
                    message_id = ?,
                    updated_at = ?
                WHERE request_id = ?
                """,
                (channel_id, message_id, _now(), request_id),
            )


def resolve_battle_request(request_id: int, status: str) -> dict[str, Any]:
    """バトル申請へ回答する。

    ``approved`` 以外はロックを解放します。``approved`` の場合は開始前チェックと
    バトル作成まで占有を維持します（13節）。
    """

    if status not in ("approved", "rejected", "cancelled"):
        raise ValueError(f"バトル申請の状態が不正です: {status}")

    timestamp = _now()

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        with conn:
            conn.execute("BEGIN IMMEDIATE")

            request_row = conn.execute(
                """
                SELECT *
                FROM guild_battle_requests
                WHERE request_id = ?
                """,
                (request_id,),
            ).fetchone()

            if request_row is None:
                conn.rollback()
                return _failure("not_pending")

            updated = conn.execute(
                """
                UPDATE guild_battle_requests
                SET status = ?,
                    updated_at = ?
                WHERE request_id = ?
                  AND status = 'pending'
                """,
                (status, timestamp, request_id),
            )

            if updated.rowcount == 0:
                conn.rollback()
                return _failure("not_pending")

            if status != "approved":
                conn.execute(
                    """
                    DELETE FROM guild_battle_locks
                    WHERE guild_id IN (?, ?)
                      AND lock_type = 'request'
                      AND reference_id = ?
                    """,
                    (
                        request_row["from_guild_id"],
                        request_row["to_guild_id"],
                        request_id,
                    ),
                )

    return _success(
        request_id=request_id,
        status=status,
        from_guild_id=request_row["from_guild_id"],
        to_guild_id=request_row["to_guild_id"],
        channel_id=request_row["channel_id"],
        message_id=request_row["message_id"],
        bet_coin=request_row["bet_coin"],
    )


# ==================================================
# 公開バトル募集（12.2節）
# ==================================================
def create_battle_recruitment(
    guild_id: int, *, bet_coin: int | None = None
) -> dict[str, Any]:
    """公開バトル募集を作成し、募集側のロックを取得する。"""

    timestamp = _now()

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        with conn:
            conn.execute("BEGIN IMMEDIATE")

            inserted = conn.execute(
                """
                INSERT INTO guild_battle_recruitments
                    (guild_id, status, bet_coin, created_at, updated_at)
                VALUES
                    (?, 'open', ?, ?, ?)
                """,
                (guild_id, bet_coin, timestamp, timestamp),
            )
            recruitment_id = int(inserted.lastrowid)

            if not acquire_battle_lock(conn, guild_id, "recruitment", recruitment_id):
                conn.rollback()
                return _failure("guild_busy", recruitment_id=None)

    return _success(recruitment_id=recruitment_id)


def get_battle_recruitment(recruitment_id: int) -> dict[str, Any] | None:
    """公開バトル募集を1件返す。"""

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT *
            FROM guild_battle_recruitments
            WHERE recruitment_id = ?
            """,
            (recruitment_id,),
        ).fetchone()

    return _row_to_dict(row)


def get_battle_recruitment_by_message(message_id: int) -> dict[str, Any] | None:
    """募集EmbedのメッセージIDから募集を引く。"""

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT *
            FROM guild_battle_recruitments
            WHERE message_id = ?
            ORDER BY recruitment_id DESC
            LIMIT 1
            """,
            (message_id,),
        ).fetchone()

    return _row_to_dict(row)


def set_battle_recruitment_message(
    recruitment_id: int,
    channel_id: int,
    message_id: int,
) -> None:
    """募集Embedのチャンネル・メッセージIDを保存する。"""

    with closing(get_connection()) as conn:
        with conn:
            conn.execute(
                """
                UPDATE guild_battle_recruitments
                SET channel_id = ?,
                    message_id = ?,
                    updated_at = ?
                WHERE recruitment_id = ?
                """,
                (channel_id, message_id, _now(), recruitment_id),
            )


def resolve_battle_recruitment(recruitment_id: int, status: str) -> dict[str, Any]:
    """公開バトル募集を終了する。``cancelled`` は募集側のロックも解放する。"""

    if status not in ("matched", "cancelled"):
        raise ValueError(f"バトル募集の状態が不正です: {status}")

    timestamp = _now()

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        with conn:
            conn.execute("BEGIN IMMEDIATE")

            recruitment_row = conn.execute(
                """
                SELECT *
                FROM guild_battle_recruitments
                WHERE recruitment_id = ?
                """,
                (recruitment_id,),
            ).fetchone()

            if recruitment_row is None:
                conn.rollback()
                return _failure("not_open")

            updated = conn.execute(
                """
                UPDATE guild_battle_recruitments
                SET status = ?,
                    updated_at = ?
                WHERE recruitment_id = ?
                  AND status = 'open'
                """,
                (status, timestamp, recruitment_id),
            )

            if updated.rowcount == 0:
                conn.rollback()
                return _failure("not_open")

            if status == "cancelled":
                conn.execute(
                    """
                    DELETE FROM guild_battle_locks
                    WHERE guild_id = ?
                      AND lock_type = 'recruitment'
                      AND reference_id = ?
                    """,
                    (recruitment_row["guild_id"], recruitment_id),
                )

    return _success(
        recruitment_id=recruitment_id,
        status=status,
        guild_id=recruitment_row["guild_id"],
        channel_id=recruitment_row["channel_id"],
        message_id=recruitment_row["message_id"],
    )


def claim_battle_recruitment(
    recruitment_id: int,
    challenger_guild_id: int,
) -> dict[str, Any]:
    """公開バトル募集へ申し込み、対戦相手を確定する。

    同時に複数の申込みが届いても、``status='open'`` の行を更新できた1件だけを
    成立させます。更新できなかった申込みは ``already_matched`` になります（12.2節）。
    """

    timestamp = _now()

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        with conn:
            conn.execute("BEGIN IMMEDIATE")

            recruitment_row = conn.execute(
                """
                SELECT *
                FROM guild_battle_recruitments
                WHERE recruitment_id = ?
                """,
                (recruitment_id,),
            ).fetchone()

            if recruitment_row is None:
                conn.rollback()
                return _failure("already_matched", recruitment_id=recruitment_id)

            host_guild_id = recruitment_row["guild_id"]

            if host_guild_id == challenger_guild_id:
                conn.rollback()
                return _failure("same_guild", recruitment_id=recruitment_id)

            updated = conn.execute(
                """
                UPDATE guild_battle_recruitments
                SET status = 'matched',
                    updated_at = ?
                WHERE recruitment_id = ?
                  AND status = 'open'
                """,
                (timestamp, recruitment_id),
            )

            if updated.rowcount == 0:
                conn.rollback()
                return _failure("already_matched", recruitment_id=recruitment_id)

            if not acquire_battle_lock(
                conn,
                challenger_guild_id,
                "recruitment",
                recruitment_id,
            ):
                conn.rollback()
                return _failure("guild_busy", recruitment_id=recruitment_id)

    return _success(
        recruitment_id=recruitment_id,
        guild_id=host_guild_id,
        challenger_guild_id=challenger_guild_id,
        channel_id=recruitment_row["channel_id"],
        message_id=recruitment_row["message_id"],
        bet_coin=recruitment_row["bet_coin"],
    )


# ==================================================
# バトル作成（13節・14節）
# ==================================================
def create_battle(
    *,
    guild_a_id: int,
    guild_b_id: int,
    guild_time_seconds: int,
    units: list[dict[str, Any]],
    bet_coin: int | None = None,
) -> dict[str, Any]:
    """開始前チェックを通過した対戦のバトルデータと戦闘用使い魔を作成する。

    バトル本体、戦闘用使い魔、両ギルドのロック張り替え、編成ロックまでを
    1トランザクションで確定します。
    """

    if guild_a_id == guild_b_id:
        raise ValueError("同じギルド同士のバトルは作成できません。")

    timestamp = _now()
    placeholders = ", ".join("?" for _ in ACTIVE_BATTLE_STATUSES)

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        with conn:
            conn.execute("BEGIN IMMEDIATE")

            active_row = conn.execute(
                f"""
                SELECT battle_id
                FROM guild_battles
                WHERE status IN ({placeholders})
                  AND (
                        guild_a_id IN (?, ?)
                     OR guild_b_id IN (?, ?)
                  )
                LIMIT 1
                """,
                (
                    *ACTIVE_BATTLE_STATUSES,
                    guild_a_id,
                    guild_b_id,
                    guild_a_id,
                    guild_b_id,
                ),
            ).fetchone()

            if active_row is not None:
                conn.rollback()
                return _failure("guild_busy", battle_id=None)

            battle_lock_row = conn.execute(
                """
                SELECT guild_id
                FROM guild_battle_locks
                WHERE guild_id IN (?, ?)
                  AND lock_type = 'battle'
                LIMIT 1
                """,
                (guild_a_id, guild_b_id),
            ).fetchone()

            if battle_lock_row is not None:
                conn.rollback()
                return _failure("guild_busy", battle_id=None)

            inserted = conn.execute(
                """
                INSERT INTO guild_battles
                    (guild_a_id, guild_b_id, status, current_round, turn_index,
                     turn_order, action_seq, log_seq,
                     guild_a_remaining_seconds, guild_b_remaining_seconds,
                     bet_coin, created_at, updated_at)
                VALUES
                    (?, ?, 'preparing', 0, 0, '[]', 0, 0, ?, ?, ?, ?, ?)
                """,
                (
                    guild_a_id,
                    guild_b_id,
                    guild_time_seconds,
                    guild_time_seconds,
                    bet_coin,
                    timestamp,
                    timestamp,
                ),
            )
            battle_id = int(inserted.lastrowid)

            for unit in units:
                max_hp = int(unit["max_hp"])
                base_atk = int(unit["base_atk"])
                conn.execute(
                    """
                    INSERT INTO guild_battle_units
                        (battle_id, guild_id, player_id, familiar_instance_id,
                         familiar_id, level, max_hp, current_hp, base_atk,
                         current_atk, speed, base_speed, cost, gender, slot, alive,
                         order_seed, active_skill_uses, passive_uses,
                         state_flags, updated_at)
                    VALUES
                        (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        battle_id,
                        int(unit["guild_id"]),
                        int(unit["player_id"]),
                        int(unit["familiar_instance_id"]),
                        str(unit["familiar_id"]),
                        int(unit.get("level", 0)),
                        max_hp,
                        int(unit.get("current_hp", max_hp)),
                        base_atk,
                        int(unit.get("current_atk", base_atk)),
                        int(unit.get("speed", 0)),
                        int(unit.get("base_speed", unit.get("speed", 0))),
                        int(unit.get("cost", 0)),
                        unit.get("gender"),
                        int(unit["slot"]),
                        1 if unit.get("alive", True) else 0,
                        int(unit.get("order_seed", 0)),
                        _dump_json(unit.get("active_skill_uses") or {}),
                        _dump_json(unit.get("passive_uses") or {}),
                        _dump_json(unit.get("state_flags") or {}),
                        timestamp,
                    ),
                )

            for guild_id in (guild_a_id, guild_b_id):
                conn.execute(
                    """
                    INSERT INTO guild_battle_locks
                        (guild_id, lock_type, reference_id, created_at)
                    VALUES
                        (?, 'battle', ?, ?)
                    ON CONFLICT(guild_id)
                    DO UPDATE SET
                        lock_type = 'battle',
                        reference_id = excluded.reference_id,
                        created_at = excluded.created_at
                    """,
                    (guild_id, battle_id, timestamp),
                )

            conn.execute(
                """
                UPDATE guilds
                SET roster_locked = 1,
                    updated_at = ?
                WHERE guild_id IN (?, ?)
                """,
                (timestamp, guild_a_id, guild_b_id),
            )

    return _success(battle_id=battle_id)


# ==================================================
# 戦闘状態の読み書き（14節・16節）
# ==================================================
def _build_unit(row: sqlite3.Row) -> BattleUnit:
    """``guild_battle_units`` の1行を戦闘用使い魔へ変換する。"""

    return BattleUnit(
        battle_unit_id=row["battle_unit_id"],
        battle_id=row["battle_id"],
        guild_id=row["guild_id"],
        player_id=row["player_id"],
        familiar_instance_id=row["familiar_instance_id"],
        familiar_id=row["familiar_id"],
        level=row["level"],
        max_hp=row["max_hp"],
        current_hp=row["current_hp"],
        base_atk=row["base_atk"],
        current_atk=row["current_atk"],
        speed=row["speed"],
        base_speed=row["base_speed"] or row["speed"],
        cost=row["cost"],
        slot=row["slot"],
        gender=row["gender"],
        alive=bool(row["alive"]),
        order_seed=row["order_seed"],
        active_skill_uses=_load_json_dict(row["active_skill_uses"]),
        passive_uses=_load_json_dict(row["passive_uses"]),
        state_flags=_load_json_dict(row["state_flags"]),
    )


def _build_effect(row: sqlite3.Row) -> BattleEffectState:
    """``guild_battle_effects`` の1行を継続効果へ変換する。"""

    return BattleEffectState(
        effect_type=row["effect_type"],
        duration_type=row["duration_type"],
        battle_unit_id=row["battle_unit_id"],
        value=row["value"],
        remaining=row["remaining"],
        applied_round=row["applied_round"],
        source_unit_id=row["source_unit_id"],
        source_skill_id=row["source_skill_id"],
        params=_load_json_dict(row["params"]),
        effect_id=row["effect_id"],
    )


def load_battle_state(battle_id: int) -> BattleState | None:
    """進行中バトルの全状態を ``BattleState`` として読み込む。

    行動ログは戦闘計算に不要なため空リストで返します。過去ログが必要な場合は
    ``get_battle_logs`` を使ってください。
    """

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row

        battle_row = conn.execute(
            """
            SELECT *
            FROM guild_battles
            WHERE battle_id = ?
            """,
            (battle_id,),
        ).fetchone()

        if battle_row is None:
            return None

        unit_rows = conn.execute(
            """
            SELECT *
            FROM guild_battle_units
            WHERE battle_id = ?
            ORDER BY battle_unit_id
            """,
            (battle_id,),
        ).fetchall()

        effect_rows = conn.execute(
            """
            SELECT *
            FROM guild_battle_effects
            WHERE battle_id = ?
            ORDER BY effect_id
            """,
            (battle_id,),
        ).fetchall()

    units: dict[int, BattleUnit] = {}
    for row in unit_rows:
        unit = _build_unit(row)
        units[unit.battle_unit_id] = unit

    return BattleState(
        battle_id=battle_row["battle_id"],
        guild_a_id=battle_row["guild_a_id"],
        guild_b_id=battle_row["guild_b_id"],
        status=battle_row["status"],
        result=battle_row["result"],
        end_reason=battle_row["end_reason"],
        current_round=battle_row["current_round"],
        turn_index=battle_row["turn_index"],
        turn_order=[int(value) for value in _load_json_list(battle_row["turn_order"])],
        current_unit_id=battle_row["current_unit_id"],
        action_seq=battle_row["action_seq"],
        log_seq=battle_row["log_seq"],
        remaining_seconds={
            battle_row["guild_a_id"]: battle_row["guild_a_remaining_seconds"],
            battle_row["guild_b_id"]: battle_row["guild_b_remaining_seconds"],
        },
        units=units,
        effects=[_build_effect(row) for row in effect_rows],
        logs=[],
    )


def save_battle_state(state: BattleState, *, expected_action_seq: int) -> bool:
    """戦闘状態を保存する。二重処理を防ぐ楽観ロック付き。

    ``guild_battles.action_seq`` が ``expected_action_seq`` と一致する場合だけ
    書き込みます。一致しない場合はボタンの二重押しや再送とみなし、何も変更せず
    ``False`` を返します（16節・27節）。``state.logs`` は呼び出し側が表示に使うため
    保存後もクリアしません。
    """

    timestamp = _now()

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        with conn:
            conn.execute("BEGIN IMMEDIATE")

            current_row = conn.execute(
                """
                SELECT guild_a_remaining_seconds, guild_b_remaining_seconds
                FROM guild_battles
                WHERE battle_id = ?
                """,
                (state.battle_id,),
            ).fetchone()

            if current_row is None:
                conn.rollback()
                logger.warning(
                    f"保存対象のバトルが見つかりません: battle_id={state.battle_id}"
                )
                return False

            updated = conn.execute(
                """
                UPDATE guild_battles
                SET status = ?,
                    result = ?,
                    end_reason = ?,
                    current_round = ?,
                    turn_index = ?,
                    turn_order = ?,
                    current_unit_id = ?,
                    action_seq = ?,
                    log_seq = ?,
                    guild_a_remaining_seconds = ?,
                    guild_b_remaining_seconds = ?,
                    updated_at = ?
                WHERE battle_id = ?
                  AND action_seq = ?
                """,
                (
                    state.status,
                    state.result,
                    state.end_reason,
                    state.current_round,
                    state.turn_index,
                    _dump_json(list(state.turn_order)),
                    state.current_unit_id,
                    state.action_seq,
                    state.log_seq,
                    _remaining_seconds(
                        state,
                        state.guild_a_id,
                        current_row["guild_a_remaining_seconds"],
                    ),
                    _remaining_seconds(
                        state,
                        state.guild_b_id,
                        current_row["guild_b_remaining_seconds"],
                    ),
                    timestamp,
                    state.battle_id,
                    expected_action_seq,
                ),
            )

            if updated.rowcount == 0:
                conn.rollback()
                logger.info(
                    f"バトル状態の保存を破棄しました（action_seq不一致）: "
                    f"battle_id={state.battle_id} expected={expected_action_seq}"
                )
                return False

            for unit in state.units.values():
                conn.execute(
                    """
                    UPDATE guild_battle_units
                    SET max_hp = ?,
                        current_hp = ?,
                        base_atk = ?,
                        current_atk = ?,
                        speed = ?,
                        base_speed = ?,
                        alive = ?,
                        order_seed = ?,
                        active_skill_uses = ?,
                        passive_uses = ?,
                        state_flags = ?,
                        updated_at = ?
                    WHERE battle_unit_id = ?
                      AND battle_id = ?
                    """,
                    (
                        unit.max_hp,
                        unit.current_hp,
                        unit.base_atk,
                        unit.current_atk,
                        unit.speed,
                        unit.base_speed,
                        1 if unit.alive else 0,
                        unit.order_seed,
                        _dump_json(unit.active_skill_uses),
                        _dump_json(unit.passive_uses),
                        _dump_json(unit.state_flags),
                        timestamp,
                        unit.battle_unit_id,
                        state.battle_id,
                    ),
                )

            conn.execute(
                """
                DELETE FROM guild_battle_effects
                WHERE battle_id = ?
                """,
                (state.battle_id,),
            )

            for effect in state.effects:
                conn.execute(
                    """
                    INSERT INTO guild_battle_effects
                        (battle_id, battle_unit_id, effect_type, value,
                         duration_type, remaining, applied_round, source_unit_id,
                         source_skill_id, params, created_at)
                    VALUES
                        (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        state.battle_id,
                        effect.battle_unit_id,
                        effect.effect_type,
                        effect.value,
                        effect.duration_type,
                        effect.remaining,
                        effect.applied_round,
                        effect.source_unit_id,
                        effect.source_skill_id,
                        _dump_json(effect.params),
                        timestamp,
                    ),
                )

            for entry in state.logs:
                conn.execute(
                    """
                    INSERT INTO guild_battle_logs
                        (battle_id, sequence, round, event_type, actor_unit_id,
                         target_unit_id, skill_id, amount, detail, created_at)
                    SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    WHERE NOT EXISTS (
                        SELECT 1
                        FROM guild_battle_logs
                        WHERE battle_id = ?
                          AND sequence = ?
                    )
                    """,
                    (
                        state.battle_id,
                        entry.sequence,
                        entry.round,
                        _event_type_value(entry.event_type),
                        entry.actor_unit_id,
                        entry.target_unit_id,
                        entry.skill_id,
                        entry.amount,
                        _dump_json(entry.detail),
                        timestamp,
                        state.battle_id,
                        entry.sequence,
                    ),
                )

    return True


# ==================================================
# バトル参照・更新
# ==================================================
def get_battle(battle_id: int) -> dict[str, Any] | None:
    """バトルを1件返す。"""

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT *
            FROM guild_battles
            WHERE battle_id = ?
            """,
            (battle_id,),
        ).fetchone()

    return _row_to_dict(row)


def get_active_battle_for_guild(guild_id: int) -> dict[str, Any] | None:
    """ギルドが参加している進行中バトルを返す。"""

    placeholders = ", ".join("?" for _ in ACTIVE_BATTLE_STATUSES)

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            f"""
            SELECT *
            FROM guild_battles
            WHERE status IN ({placeholders})
              AND (guild_a_id = ? OR guild_b_id = ?)
            ORDER BY battle_id DESC
            LIMIT 1
            """,
            (*ACTIVE_BATTLE_STATUSES, guild_id, guild_id),
        ).fetchone()

    return _row_to_dict(row)


def get_active_battles() -> list[dict[str, Any]]:
    """進行中のバトルをすべて返す（再起動後の点検に使う。29節）。"""

    placeholders = ", ".join("?" for _ in ACTIVE_BATTLE_STATUSES)

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"""
            SELECT *
            FROM guild_battles
            WHERE status IN ({placeholders})
            ORDER BY battle_id
            """,
            ACTIVE_BATTLE_STATUSES,
        ).fetchall()

    return [dict(row) for row in rows]


def get_battle_for_channel(channel_id: int) -> dict[str, Any] | None:
    """バトル専用チャンネルから進行中バトルを引く（16節・34.14節）。"""

    placeholders = ", ".join("?" for _ in ACTIVE_BATTLE_STATUSES)

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            f"""
            SELECT *
            FROM guild_battles
            WHERE ? IN (guild_a_channel_id, guild_b_channel_id)
              AND status IN ({placeholders})
            ORDER BY battle_id DESC
            LIMIT 1
            """,
            (channel_id, *ACTIVE_BATTLE_STATUSES),
        ).fetchone()

    return _row_to_dict(row)


def set_battle_channel(battle_id: int, *, guild_id: int, channel_id: int) -> None:
    """対戦成立時に作ったバトル専用チャンネルのIDを保存する（34.14節）。"""

    with closing(get_connection()) as conn:
        with conn:
            battle = conn.execute(
                """
                SELECT guild_a_id
                FROM guild_battles
                WHERE battle_id = ?
                """,
                (battle_id,),
            ).fetchone()

            if battle is None:
                return

            column = (
                "guild_a_channel_id"
                if int(battle[0]) == int(guild_id)
                else "guild_b_channel_id"
            )

            conn.execute(
                f"""
                UPDATE guild_battles
                SET {column} = ?,
                    updated_at = ?
                WHERE battle_id = ?
                """,
                (channel_id, _now(), battle_id),
            )


def get_battles_to_purge_channels(before_utc_iso: str) -> list[dict[str, Any]]:
    """保存期間を過ぎ、まだチャンネルを削除していない終了済みバトルを返す。"""

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT *
            FROM guild_battles
            WHERE status IN ('finished', 'aborted')
              AND finished_at IS NOT NULL
              AND finished_at <= ?
              AND channels_deleted_at IS NULL
            ORDER BY finished_at ASC, battle_id ASC
            """,
            (before_utc_iso,),
        ).fetchall()

    return [dict(row) for row in rows]


def mark_battle_channels_deleted(battle_id: int) -> None:
    """バトル専用チャンネルを削除済みとして記録する。"""

    with closing(get_connection()) as conn:
        with conn:
            conn.execute(
                """
                UPDATE guild_battles
                SET channels_deleted_at = ?,
                    updated_at = ?
                WHERE battle_id = ?
                """,
                (_now(), _now(), battle_id),
            )


def set_battle_messages(
    battle_id: int,
    *,
    guild_id: int,
    status_message_id: int | None = ...,
    turn_message_id: int | None = ...,
) -> None:
    """戦況Embed・ターンメンションのメッセージIDを保存する。

    ``guild_id`` がバトルのどちら側かを判定して列を選びます。省略した引数
    （``...``）は更新しません。
    """

    if status_message_id is ... and turn_message_id is ...:
        return

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        with conn:
            conn.execute("BEGIN IMMEDIATE")

            battle_row = conn.execute(
                """
                SELECT guild_a_id, guild_b_id
                FROM guild_battles
                WHERE battle_id = ?
                """,
                (battle_id,),
            ).fetchone()

            if battle_row is None:
                conn.rollback()
                logger.warning(f"バトルが見つかりません: battle_id={battle_id}")
                return

            if guild_id == battle_row["guild_a_id"]:
                side = "guild_a"
            elif guild_id == battle_row["guild_b_id"]:
                side = "guild_b"
            else:
                conn.rollback()
                logger.warning(
                    f"バトルに参加していないギルドです: "
                    f"battle_id={battle_id} guild_id={guild_id}"
                )
                return

            assignments = []
            params: list[Any] = []

            if status_message_id is not ...:
                assignments.append(f"{side}_status_message_id = ?")
                params.append(status_message_id)

            if turn_message_id is not ...:
                assignments.append(f"{side}_turn_message_id = ?")
                params.append(turn_message_id)

            assignments.append("updated_at = ?")
            params.append(_now())
            params.append(battle_id)

            conn.execute(
                f"""
                UPDATE guild_battles
                SET {", ".join(assignments)}
                WHERE battle_id = ?
                """,
                params,
            )


def set_battle_turn_timing(
    battle_id: int,
    *,
    turn_started_at: str | None,
    turn_deadline_at: str | None,
) -> None:
    """現在ターンの開始時刻と制限時刻を保存する（22節）。"""

    with closing(get_connection()) as conn:
        with conn:
            conn.execute(
                """
                UPDATE guild_battles
                SET turn_started_at = ?,
                    turn_deadline_at = ?,
                    updated_at = ?
                WHERE battle_id = ?
                """,
                (turn_started_at, turn_deadline_at, _now(), battle_id),
            )


def set_battle_status(battle_id: int, status: str) -> None:
    """バトルの進行状態を更新する。

    ``in_progress`` へ変更したときは、まだ記録が無ければ開始時刻も保存します。
    """

    timestamp = _now()

    with closing(get_connection()) as conn:
        with conn:
            if status == "in_progress":
                conn.execute(
                    """
                    UPDATE guild_battles
                    SET status = ?,
                        started_at = COALESCE(started_at, ?),
                        updated_at = ?
                    WHERE battle_id = ?
                    """,
                    (status, timestamp, timestamp, battle_id),
                )
            else:
                conn.execute(
                    """
                    UPDATE guild_battles
                    SET status = ?,
                        updated_at = ?
                    WHERE battle_id = ?
                    """,
                    (status, timestamp, battle_id),
                )


# ==================================================
# バトル終了（26節）
# ==================================================
def finish_battle(
    battle_id: int,
    *,
    result: str | None,
    end_reason: str,
    status: str = "finished",
) -> dict[str, Any]:
    """バトルを終了し、後片付けと戦績反映を1トランザクションで確定する。

    進行中の状態からの遷移だけを許可するため、Discord操作の失敗で再実行されても
    勝敗を二重に記録しません（26.2節）。運営による強制中止（``aborted``）は
    勝敗数へ反映しません。
    """

    if status not in ("finished", "aborted"):
        raise ValueError(f"バトル終了状態が不正です: {status}")

    timestamp = _now()
    placeholders = ", ".join("?" for _ in ACTIVE_BATTLE_STATUSES)

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        with conn:
            conn.execute("BEGIN IMMEDIATE")

            battle_row = conn.execute(
                """
                SELECT guild_a_id, guild_b_id
                FROM guild_battles
                WHERE battle_id = ?
                """,
                (battle_id,),
            ).fetchone()

            if battle_row is None:
                conn.rollback()
                return _failure("already_finished")

            guild_a_id = battle_row["guild_a_id"]
            guild_b_id = battle_row["guild_b_id"]

            updated = conn.execute(
                f"""
                UPDATE guild_battles
                SET status = ?,
                    result = ?,
                    end_reason = ?,
                    finished_at = ?,
                    turn_deadline_at = NULL,
                    updated_at = ?
                WHERE battle_id = ?
                  AND status IN ({placeholders})
                """,
                (
                    status,
                    result,
                    end_reason,
                    timestamp,
                    timestamp,
                    battle_id,
                    *ACTIVE_BATTLE_STATUSES,
                ),
            )

            if updated.rowcount == 0:
                conn.rollback()
                return _failure("already_finished")

            conn.execute(
                """
                UPDATE guilds
                SET roster_locked = 0,
                    updated_at = ?
                WHERE guild_id IN (?, ?)
                """,
                (timestamp, guild_a_id, guild_b_id),
            )

            # 出場者セットと使い魔セットを解除する（26.2節）。
            # 行を残すと、次にメンバー構成が変わったときに出場者専用TCの
            # 閲覧権限が復活してしまうため、両方を削除して編成と権限を一致させる。
            conn.execute(
                """
                DELETE FROM guild_battle_members
                WHERE guild_id IN (?, ?)
                """,
                (guild_a_id, guild_b_id),
            )
            conn.execute(
                """
                DELETE FROM guild_battle_entries
                WHERE guild_id IN (?, ?)
                """,
                (guild_a_id, guild_b_id),
            )

            conn.execute(
                """
                DELETE FROM guild_battle_locks
                WHERE guild_id IN (?, ?)
                  AND lock_type = 'battle'
                  AND reference_id = ?
                """,
                (guild_a_id, guild_b_id, battle_id),
            )

            if status == "finished":
                if result == _RESULT_GUILD_A:
                    _add_battle_record(conn, guild_a_id, "wins", timestamp)
                    _add_battle_record(conn, guild_b_id, "losses", timestamp)
                elif result == _RESULT_GUILD_B:
                    _add_battle_record(conn, guild_b_id, "wins", timestamp)
                    _add_battle_record(conn, guild_a_id, "losses", timestamp)
                elif result == _RESULT_DRAW:
                    _add_battle_record(conn, guild_a_id, "draws", timestamp)
                    _add_battle_record(conn, guild_b_id, "draws", timestamp)

    return _success(
        guild_a_id=guild_a_id,
        guild_b_id=guild_b_id,
        result=result,
    )


def _add_battle_record(
    conn: sqlite3.Connection,
    guild_id: int,
    column: str,
    timestamp: str,
) -> None:
    """ギルドの通算戦績を1件加算する。列名は呼び出し側の固定値だけを渡す。"""

    if column not in ("wins", "losses", "draws"):
        raise ValueError(f"戦績の列名が不正です: {column}")

    conn.execute(
        f"""
        UPDATE guilds
        SET {column} = {column} + 1,
            updated_at = ?
        WHERE guild_id = ?
        """,
        (timestamp, guild_id),
    )


# ==================================================
# 行動ログ（23節・27節）
# ==================================================
def get_battle_logs(
    battle_id: int,
    *,
    after_sequence: int = 0,
) -> list[dict[str, Any]]:
    """行動ログを順番に返す。``detail`` はJSON列を辞書へ復元して返す。"""

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT *
            FROM guild_battle_logs
            WHERE battle_id = ?
              AND sequence > ?
            ORDER BY sequence
            """,
            (battle_id, after_sequence),
        ).fetchall()

    logs = []
    for row in rows:
        record = dict(row)
        record["detail"] = _load_json_dict(record.get("detail"))
        logs.append(record)

    return logs


def build_log_entries(records: list[dict[str, Any]]) -> list[BattleLogEntry]:
    """``get_battle_logs`` の結果を ``BattleLogEntry`` へ変換する。

    再起動後にEmbedを組み直す場合など、``game.battle_embed`` へ渡すために使います。
    """

    entries = []
    for record in records:
        detail = record.get("detail")
        entries.append(
            BattleLogEntry(
                event_type=_event_type_value(record["event_type"]),
                round=record["round"],
                sequence=record["sequence"],
                actor_unit_id=record.get("actor_unit_id"),
                target_unit_id=record.get("target_unit_id"),
                skill_id=record.get("skill_id"),
                amount=record.get("amount"),
                detail=detail if isinstance(detail, dict) else _load_json_dict(detail),
            )
        )

    return entries


# ==================================================
# バトル報酬（26.2節）
# ==================================================
def split_bet_evenly(total: int, count: int) -> list[int]:
    """``total`` を ``count`` 人で均等に分ける（26.2節）。

    端数はメンバー選択順に切り上げ → 切り下げで割り振ります。渡された順番が
    そのままメンバー選択順です。合計は必ず ``total`` になります。
    """

    if count <= 0 or total <= 0:
        return [0] * max(0, count)

    base, remainder = divmod(total, count)
    return [base + (1 if index < remainder else 0) for index in range(count)]


def settle_battle_bet(
    battle_id: int,
    *,
    winners: list[dict[str, Any]],
    losers: list[dict[str, Any]],
    drawers: list[dict[str, Any]],
    bet_coin: int,
    win_xp: int,
    lose_xp: int,
    draw_xp: int,
    reward_date: str,
    daily_limit: int,
) -> dict[str, Any]:
    """ベットしたcoinを負けた側から勝った側へ移し、XPを付与する（26.2節）。

    - ``bet_coin`` は**ギルド単位**のベット額です。負けた側の出場者で均等に分担し、
      端数はメンバー選択順に切り上げ → 切り下げで割り振ります。
    - 残高が足りない出場者からは持っているぶんだけ回収し、マイナス残高は作りません。
    - 回収できた合計を、勝った側の出場者へ同じ方法（均等・端数は選択順）で分けます。
      coinは移動するだけなので、総量は増えません。
    - XPは新しく付与するため、1プレイヤー1日 ``daily_limit`` 試合までに制限します。
    - 引き分けはcoinを動かさず、XPだけを付与します。

    戻り値は ``{"pot", "collected", "paid", "results"}`` です。``results`` の各要素は
    ``{"user_id", "guild_id", "coin", "xp", "outcome"}`` で、``coin`` は負けた側なら負数です。
    """

    timestamp = _now()

    def _xp_allowed(conn: sqlite3.Connection, user_id: int) -> bool:
        row = conn.execute(
            """
            SELECT COUNT(*) AS granted_count
            FROM guild_battle_rewards
            WHERE user_id = ?
              AND reward_date = ?
            """,
            (user_id, reward_date),
        ).fetchone()
        return int(row["granted_count"]) < daily_limit

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        with conn:
            conn.execute("BEGIN IMMEDIATE")

            already = conn.execute(
                "SELECT COUNT(*) FROM guild_battle_rewards WHERE battle_id = ?",
                (battle_id,),
            ).fetchone()
            if int(already[0]) > 0:
                # 同じバトルで既に清算済み。二重に動かさない。
                conn.rollback()
                return {"pot": 0, "collected": [], "paid": [], "results": []}

            # ---- 1. 負けた側から回収する（ギルド合計を均等に分担） ----
            pot = 0
            collected: list[dict[str, Any]] = []
            shares = split_bet_evenly(bet_coin, len(losers))

            for entry, share in zip(losers, shares):
                user_id = int(entry["user_id"])

                balance_row = conn.execute(
                    "SELECT balance FROM balances WHERE user_id = ?", (user_id,)
                ).fetchone()
                balance = int(balance_row["balance"]) if balance_row else 0

                taken = max(0, min(share, balance))
                if taken:
                    conn.execute(
                        "UPDATE balances SET balance = balance - ? WHERE user_id = ?",
                        (taken, user_id),
                    )
                    conn.execute(
                        """
                        INSERT INTO transactions
                            (type, executor_id, target_id, amount, note, created_at)
                        VALUES
                            ('ギルドバトル敗北', NULL, ?, ?, ?, ?)
                        """,
                        (user_id, -taken, f"battle_id={battle_id}", timestamp),
                    )

                pot += taken
                collected.append(
                    {"user_id": user_id, "coin": taken, "share": share}
                )

            # ---- 2. 勝った側へ均等に配る ----
            paid: list[dict[str, Any]] = []

            if winners and pot:
                for entry, amount in zip(winners, split_bet_evenly(pot, len(winners))):
                    user_id = int(entry["user_id"])
                    if not amount:
                        continue

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
                            ('ギルドバトル勝利', NULL, ?, ?, ?, ?)
                        """,
                        (user_id, amount, f"battle_id={battle_id}", timestamp),
                    )
                    paid.append({"user_id": user_id, "coin": amount})

            paid_by_user = {item["user_id"]: item["coin"] for item in paid}
            taken_by_user = {item["user_id"]: item["coin"] for item in collected}

            # ---- 3. 記録とXP ----
            results: list[dict[str, Any]] = []

            groups = (
                (winners, "win", win_xp),
                (losers, "lose", lose_xp),
                (drawers, "draw", draw_xp),
            )

            for entries, outcome, xp_amount in groups:
                for entry in entries:
                    user_id = int(entry["user_id"])
                    guild_id = int(entry["guild_id"])

                    if outcome == "win":
                        coin = paid_by_user.get(user_id, 0)
                    elif outcome == "lose":
                        coin = -taken_by_user.get(user_id, 0)
                    else:
                        coin = 0

                    xp = xp_amount if _xp_allowed(conn, user_id) else 0

                    conn.execute(
                        """
                        INSERT OR IGNORE INTO guild_battle_rewards
                            (battle_id, user_id, guild_id, coin, xp, reward_date,
                             created_at)
                        VALUES
                            (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (battle_id, user_id, guild_id, coin, xp, reward_date, timestamp),
                    )

                    if xp:
                        conn.execute(
                            """
                            INSERT INTO vc_time (user_id, total_xp, monthly_xp)
                            VALUES (?, ?, ?)
                            ON CONFLICT(user_id)
                            DO UPDATE SET
                                total_xp = total_xp + excluded.total_xp,
                                monthly_xp = monthly_xp + excluded.monthly_xp
                            """,
                            (user_id, xp, xp),
                        )

                    results.append(
                        {
                            "user_id": user_id,
                            "guild_id": guild_id,
                            "coin": coin,
                            "xp": xp,
                            "outcome": outcome,
                        }
                    )

    return {"pot": pot, "collected": collected, "paid": paid, "results": results}


# ==================================================
# 運営操作ログ・保守（29節）
# ==================================================
def add_admin_log(
    *,
    executor_id: int | None,
    action: str,
    target_user_id: int | None = None,
    target_guild_id: int | None = None,
    target_battle_id: int | None = None,
    success: bool = True,
    reason: str | None = None,
    operation_id: str | None = None,
) -> None:
    """運営操作ログを記録する。表示名やメッセージ本文は保存しない。"""

    with closing(get_connection()) as conn:
        with conn:
            conn.execute(
                """
                INSERT INTO game_admin_logs
                    (executor_id, action, target_user_id, target_guild_id,
                     target_battle_id, success, reason, operation_id, created_at)
                VALUES
                    (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    executor_id,
                    action,
                    target_user_id,
                    target_guild_id,
                    target_battle_id,
                    1 if success else 0,
                    reason,
                    operation_id,
                    _now(),
                ),
            )


def purge_old_battle_logs(
    *,
    battle_log_days: int,
    admin_log_days: int,
) -> dict[str, int]:
    """保存期間を過ぎた行動ログと運営操作ログを削除する（27節）。"""

    now = datetime.now(timezone.utc)
    battle_limit = (now - timedelta(days=battle_log_days)).isoformat()
    admin_limit = (now - timedelta(days=admin_log_days)).isoformat()

    with closing(get_connection()) as conn:
        with conn:
            conn.execute("BEGIN IMMEDIATE")

            battle_deleted = conn.execute(
                """
                DELETE FROM guild_battle_logs
                WHERE created_at < ?
                """,
                (battle_limit,),
            ).rowcount

            admin_deleted = conn.execute(
                """
                DELETE FROM game_admin_logs
                WHERE created_at < ?
                """,
                (admin_limit,),
            ).rowcount

    return {
        "battle_logs": max(battle_deleted, 0),
        "admin_logs": max(admin_deleted, 0),
    }
