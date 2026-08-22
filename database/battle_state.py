"""進行中バトルの状態の読み書き・バトル行の参照更新・行動ログのSQL実装。

``BattleState`` とDBの相互変換（GAME_SPEC 14節・16節）、バトル行やチャンネル・
メッセージIDの参照更新、行動ログの保存と読み出し（23節・27節）を担当します。

設計上の要点（docs/GAME_SPEC.md 14・16・23・27節）:

- ボタンの二重押しや再送で同じ行動を2回処理しないよう、``save_battle_state`` は
  ``action_seq`` による楽観ロックで更新します（16節・27節）。期待した
  ``action_seq`` と食い違う場合は何も書かずに ``False`` を返します。
- 持ち時間は値を持たない場合に保存済みの値を維持します。誤って0へ書き換えると
  時間切れ敗北になるためです。
- 進行中とみなす状態の判定には ``ACTIVE_BATTLE_STATUSES`` を使います。
"""

from __future__ import annotations

import logging
import sqlite3

from contextlib import closing
from typing import Any

from game.models import BattleEffectState, BattleLogEntry, BattleState, BattleUnit

from .battle_common import (
    ACTIVE_BATTLE_STATUSES,
    dump_json,
    event_type_value,
    load_json_dict,
    load_json_list,
    now_iso,
    remaining_seconds,
    row_to_dict,
)
from .core import get_connection


logger = logging.getLogger(__name__)


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
        active_skill_uses=load_json_dict(row["active_skill_uses"]),
        passive_uses=load_json_dict(row["passive_uses"]),
        state_flags=load_json_dict(row["state_flags"]),
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
        params=load_json_dict(row["params"]),
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
        turn_order=[int(value) for value in load_json_list(battle_row["turn_order"])],
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

    timestamp = now_iso()

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
                    dump_json(list(state.turn_order)),
                    state.current_unit_id,
                    state.action_seq,
                    state.log_seq,
                    remaining_seconds(
                        state,
                        state.guild_a_id,
                        current_row["guild_a_remaining_seconds"],
                    ),
                    remaining_seconds(
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
                        dump_json(unit.active_skill_uses),
                        dump_json(unit.passive_uses),
                        dump_json(unit.state_flags),
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
                        dump_json(effect.params),
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
                        event_type_value(entry.event_type),
                        entry.actor_unit_id,
                        entry.target_unit_id,
                        entry.skill_id,
                        entry.amount,
                        dump_json(entry.detail),
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

    return row_to_dict(row)


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

    return row_to_dict(row)


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

    return row_to_dict(row)


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
                (channel_id, now_iso(), battle_id),
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
                (now_iso(), now_iso(), battle_id),
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
            params.append(now_iso())
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
                (turn_started_at, turn_deadline_at, now_iso(), battle_id),
            )


def set_battle_status(battle_id: int, status: str) -> None:
    """バトルの進行状態を更新する。

    ``in_progress`` へ変更したときは、まだ記録が無ければ開始時刻も保存します。
    """

    timestamp = now_iso()

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
        record["detail"] = load_json_dict(record.get("detail"))
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
                event_type=event_type_value(record["event_type"]),
                round=record["round"],
                sequence=record["sequence"],
                actor_unit_id=record.get("actor_unit_id"),
                target_unit_id=record.get("target_unit_id"),
                skill_id=record.get("skill_id"),
                amount=record.get("amount"),
                detail=detail if isinstance(detail, dict) else load_json_dict(detail),
            )
        )

    return entries
