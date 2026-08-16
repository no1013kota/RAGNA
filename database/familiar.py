"""RAGNA Onlineの使い魔データの公開入口。

使い魔マスターの同期（``data/master/`` → DB）と、所有使い魔のガチャ・合成・
売却を扱います（docs/GAME_SPEC.md 10節）。coinの増減を伴う操作は、残高・
coin履歴・使い魔履歴をすべて1つのトランザクションで確定させ、途中で失敗した
場合はcoinを消費しません（10.2節）。

抽選そのものや売却額の計算はサービス層の責務です。このモジュールは
渡された結果と金額をそのまま保存します。
"""

from __future__ import annotations

import json
import logging
import sqlite3

from contextlib import closing
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from .core import get_connection


logger = logging.getLogger(__name__)


# ==================================================
# 共通処理
# ==================================================
def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _owned_familiar_columns() -> str:
    """所有使い魔の一覧で返す列。契約で定めたキーだけを返す。"""

    return "instance_id, user_id, familiar_id, level, obtained_at"


def _disable_missing(
    conn: sqlite3.Connection,
    table: str,
    key_column: str,
    keys: list[str],
    now: str,
) -> int:
    """JSONから消えた行を ``enabled = 0`` にする（物理削除しない）。"""

    # テーブル名・列名はこのモジュール内の固定値のみで、外部入力は含めない。
    if keys:
        placeholders = ",".join("?" for _ in keys)
        cursor = conn.execute(
            f"""
            UPDATE {table}
            SET enabled = 0,
                updated_at = ?
            WHERE enabled = 1
              AND {key_column} NOT IN ({placeholders})
            """,
            (now, *keys),
        )
    else:
        cursor = conn.execute(
            f"""
            UPDATE {table}
            SET enabled = 0,
                updated_at = ?
            WHERE enabled = 1
            """,
            (now,),
        )

    return cursor.rowcount


# ==================================================
# マスターデータ同期
# ==================================================
def sync_master_data() -> None:
    """マスターデータをDBへ同期する（Bot起動時に1回だけ呼ぶ）。

    ``game.master_data.load_master_data()`` の内容を使い魔・スキル・
    スキル対応・ガチャ設定へUPSERTします。JSONから消えた使い魔とスキルは
    ``enabled = 0`` にするだけで削除しません。所有使い魔から参照される
    ためです。
    """

    from game.master_data import load_master_data

    master = load_master_data()
    now = _now()

    with closing(get_connection()) as conn:
        with conn:
            conn.execute("BEGIN IMMEDIATE")

            # ----- スキル定義 -----
            # 使い魔とスキル対応より先に入れる（外部キーの参照先になるため）。
            for skill in master.skills.values():
                conn.execute(
                    """
                    INSERT INTO familiar_skills (
                        skill_id,
                        name,
                        description,
                        skill_type,
                        "trigger",
                        target_type,
                        priority,
                        max_uses_per_battle,
                        consumes_attack,
                        targets,
                        conditions,
                        effects,
                        enabled,
                        version,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)

                    ON CONFLICT(skill_id)
                    DO UPDATE SET
                        name = excluded.name,
                        description = excluded.description,
                        skill_type = excluded.skill_type,
                        "trigger" = excluded."trigger",
                        target_type = excluded.target_type,
                        priority = excluded.priority,
                        max_uses_per_battle = excluded.max_uses_per_battle,
                        consumes_attack = excluded.consumes_attack,
                        targets = excluded.targets,
                        conditions = excluded.conditions,
                        effects = excluded.effects,
                        enabled = excluded.enabled,
                        version = excluded.version,
                        updated_at = excluded.updated_at
                    """,
                    (
                        skill.skill_id,
                        skill.name,
                        skill.description,
                        skill.skill_type,
                        skill.trigger,
                        skill.target_type,
                        skill.priority,
                        skill.max_uses_per_battle,
                        1 if skill.consumes_attack else 0,
                        json.dumps(
                            [asdict(group) for group in skill.targets],
                            ensure_ascii=False,
                        ),
                        json.dumps(
                            [dict(condition) for condition in skill.conditions],
                            ensure_ascii=False,
                        ),
                        json.dumps(
                            [effect.to_dict() for effect in skill.effects],
                            ensure_ascii=False,
                        ),
                        1 if skill.enabled else 0,
                        skill.version,
                        now,
                    ),
                )

            disabled_skills = _disable_missing(
                conn,
                "familiar_skills",
                "skill_id",
                list(master.skills.keys()),
                now,
            )

            # ----- 使い魔マスター -----
            for familiar in master.familiars.values():
                conn.execute(
                    """
                    INSERT INTO familiars (
                        familiar_id,
                        name,
                        "rank",
                        base_hp,
                        base_atk,
                        speed,
                        cost,
                        gender,
                        image_file,
                        description,
                        enabled,
                        version,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)

                    ON CONFLICT(familiar_id)
                    DO UPDATE SET
                        name = excluded.name,
                        "rank" = excluded."rank",
                        base_hp = excluded.base_hp,
                        base_atk = excluded.base_atk,
                        speed = excluded.speed,
                        cost = excluded.cost,
                        gender = excluded.gender,
                        image_file = excluded.image_file,
                        description = excluded.description,
                        enabled = excluded.enabled,
                        version = excluded.version,
                        updated_at = excluded.updated_at
                    """,
                    (
                        familiar.familiar_id,
                        familiar.name,
                        familiar.rank,
                        familiar.base_hp,
                        familiar.base_atk,
                        familiar.speed,
                        familiar.cost,
                        familiar.gender,
                        familiar.image_file,
                        familiar.description,
                        1 if familiar.enabled else 0,
                        familiar.version,
                        now,
                    ),
                )

                # スキル対応は差分更新が難しいため、対象の使い魔だけ入れ直す。
                conn.execute(
                    """
                    DELETE FROM familiar_skill_links
                    WHERE familiar_id = ?
                    """,
                    (familiar.familiar_id,),
                )

                for slot, skill_id in enumerate(familiar.skill_ids, start=1):
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO familiar_skill_links
                        (familiar_id, skill_id, slot)
                        VALUES (?, ?, ?)
                        """,
                        (familiar.familiar_id, skill_id, slot),
                    )

            disabled_familiars = _disable_missing(
                conn,
                "familiars",
                "familiar_id",
                list(master.familiars.keys()),
                now,
            )

            # ----- ガチャ設定 -----
            for pool in master.gacha_pools.values():
                conn.execute(
                    """
                    INSERT INTO familiar_gacha_pools (
                        pool_id,
                        name,
                        single_cost,
                        multi_cost,
                        multi_count,
                        is_public,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)

                    ON CONFLICT(pool_id)
                    DO UPDATE SET
                        name = excluded.name,
                        single_cost = excluded.single_cost,
                        multi_cost = excluded.multi_cost,
                        multi_count = excluded.multi_count,
                        is_public = excluded.is_public,
                        updated_at = excluded.updated_at
                    """,
                    (
                        pool.pool_id,
                        pool.name,
                        pool.single_cost,
                        pool.multi_cost,
                        pool.multi_count,
                        1 if pool.is_public else 0,
                        now,
                    ),
                )

                # 排出率は削除されたランクが残らないよう、プールごとに入れ直す。
                conn.execute(
                    """
                    DELETE FROM familiar_gacha_entries
                    WHERE pool_id = ?
                    """,
                    (pool.pool_id,),
                )

                for slot_type, weights in pool.rates.items():
                    for rank, weight_permille in weights.items():
                        conn.execute(
                            """
                            INSERT INTO familiar_gacha_entries
                            (pool_id, slot_type, "rank", weight_permille)
                            VALUES (?, ?, ?, ?)
                            """,
                            (pool.pool_id, slot_type, rank, int(weight_permille)),
                        )

    logger.info(
        "使い魔マスター同期完了: 使い魔%d体 / スキル%d件 / ガチャ%d件"
        "（無効化 使い魔%d体・スキル%d件）",
        len(master.familiars),
        len(master.skills),
        len(master.gacha_pools),
        disabled_familiars,
        disabled_skills,
    )


# ==================================================
# 所有使い魔の参照
# ==================================================
def get_owned_familiars(user_id: int) -> list[dict[str, Any]]:
    """所有中（``status = 'owned'``）の使い魔をすべて返す。"""

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row

        rows = conn.execute(
            f"""
            SELECT {_owned_familiar_columns()}
            FROM player_familiars
            WHERE user_id = ?
              AND status = 'owned'
            ORDER BY
                familiar_id ASC,
                level DESC,
                instance_id ASC
            """,
            (user_id,),
        ).fetchall()

        return [dict(row) for row in rows]


def get_owned_familiar(instance_id: int) -> dict[str, Any] | None:
    """個体を1件返す。合成・売却済みでも取得できる。"""

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row

        row = conn.execute(
            """
            SELECT *
            FROM player_familiars
            WHERE instance_id = ?
            """,
            (instance_id,),
        ).fetchone()

        return dict(row) if row is not None else None


def count_owned_familiars(user_id: int) -> int:
    """所有中の使い魔の体数を返す。"""

    with closing(get_connection()) as conn:
        row = conn.execute(
            """
            SELECT COUNT(*)
            FROM player_familiars
            WHERE user_id = ?
              AND status = 'owned'
            """,
            (user_id,),
        ).fetchone()

        return int(row[0]) if row is not None else 0


def get_same_familiars(
    user_id: int,
    familiar_id: str,
    *,
    exclude_instance_id: int | None = None,
) -> list[dict[str, Any]]:
    """同じ種類の所有使い魔を返す。合成の素材候補に使う。"""

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row

        rows = conn.execute(
            f"""
            SELECT {_owned_familiar_columns()}
            FROM player_familiars
            WHERE user_id = ?
              AND familiar_id = ?
              AND status = 'owned'
              AND (? IS NULL OR instance_id != ?)
            ORDER BY
                level DESC,
                instance_id ASC
            """,
            (user_id, familiar_id, exclude_instance_id, exclude_instance_id),
        ).fetchall()

        return [dict(row) for row in rows]


# ==================================================
# ガチャ
# ==================================================
def draw_gacha(
    user_id: int,
    *,
    pool_id: str,
    count: int,
    cost: int,
    results: list[tuple[str, str]],
) -> dict[str, Any]:
    """coin減算と抽選結果の保存を1つのトランザクションで確定する（10.2節）。

    ``results`` は抽選済みの ``[(rank, familiar_id), ...]`` です。抽選は
    サービス層が行い、ここでは並び順を保ったまま保存します。残高不足など
    途中で失敗した場合はcoinを消費しません。
    """

    if len(results) != count:
        logger.warning(
            "ガチャ結果の件数が回数と一致しません: user_id=%s pool_id=%s count=%s results=%s",
            user_id,
            pool_id,
            count,
            len(results),
        )

    now = _now()

    with closing(get_connection()) as conn:
        with conn:
            conn.execute("BEGIN IMMEDIATE")

            # coin減算。残高行が無いユーザーもrowcountで弾ける。
            if cost > 0:
                debit = conn.execute(
                    """
                    UPDATE balances
                    SET balance = balance - ?
                    WHERE user_id = ?
                      AND balance >= ?
                    """,
                    (cost, user_id, cost),
                )

                if debit.rowcount == 0:
                    conn.rollback()
                    return {
                        "ok": False,
                        "error": "insufficient_balance",
                        "instances": [],
                    }

                conn.execute(
                    """
                    INSERT INTO transactions
                        (type, executor_id, target_id, amount, note, created_at)
                    VALUES
                        ('ガチャ', ?, ?, ?, ?, ?)
                    """,
                    (user_id, user_id, -cost, f"{pool_id} {count}回", now),
                )

            instances: list[dict[str, Any]] = []

            for rank, familiar_id in results:
                cursor = conn.execute(
                    """
                    INSERT INTO player_familiars
                        (user_id, familiar_id, level, status, obtained_at, updated_at)
                    VALUES
                        (?, ?, 0, 'owned', ?, ?)
                    """,
                    (user_id, familiar_id, now, now),
                )
                instance_id = int(cursor.lastrowid)

                conn.execute(
                    """
                    INSERT INTO familiar_transactions
                        (user_id, type, instance_id, familiar_id, level,
                         coin_amount, material_instance_id, note, created_at)
                    VALUES
                        (?, 'gacha', ?, ?, 0, 0, NULL, ?, ?)
                    """,
                    (user_id, instance_id, familiar_id, pool_id, now),
                )

                instances.append(
                    {
                        "instance_id": instance_id,
                        "familiar_id": familiar_id,
                        "rank": rank,
                    }
                )

    return {"ok": True, "error": None, "instances": instances}


# ==================================================
# 合成
# ==================================================
def fuse_familiar(
    user_id: int,
    *,
    base_instance_id: int,
    material_instance_id: int,
    max_level: int,
    locked_instance_ids: set[int] | frozenset[int],
) -> dict[str, Any]:
    """同じ種類の使い魔を素材にしてレベルを1つ上げる（10.2節）。

    素材は ``status = 'fused'`` にして残し、所有一覧から外します。編成
    ロック中・進行中バトルで使用中の個体は合成できません。
    """

    if base_instance_id == material_instance_id:
        return {"ok": False, "error": "same_instance"}

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row

        with conn:
            conn.execute("BEGIN IMMEDIATE")

            rows = conn.execute(
                """
                SELECT instance_id, user_id, familiar_id, level, status
                FROM player_familiars
                WHERE instance_id IN (?, ?)
                """,
                (base_instance_id, material_instance_id),
            ).fetchall()

            found = {int(row["instance_id"]): dict(row) for row in rows}
            base = found.get(base_instance_id)
            material = found.get(material_instance_id)

            for record in (base, material):
                if (
                    record is None
                    or int(record["user_id"]) != user_id
                    or record["status"] != "owned"
                ):
                    conn.rollback()
                    return {"ok": False, "error": "not_owned"}

            if base["familiar_id"] != material["familiar_id"]:
                conn.rollback()
                return {"ok": False, "error": "different_familiar"}

            if (
                base_instance_id in locked_instance_ids
                or material_instance_id in locked_instance_ids
            ):
                conn.rollback()
                return {"ok": False, "error": "in_use"}

            if int(base["level"]) >= max_level:
                conn.rollback()
                return {"ok": False, "error": "max_level"}

            now = _now()
            new_level = int(base["level"]) + 1

            conn.execute(
                """
                UPDATE player_familiars
                SET level = ?,
                    updated_at = ?
                WHERE instance_id = ?
                """,
                (new_level, now, base_instance_id),
            )
            conn.execute(
                """
                UPDATE player_familiars
                SET status = 'fused',
                    updated_at = ?
                WHERE instance_id = ?
                """,
                (now, material_instance_id),
            )
            conn.execute(
                """
                INSERT INTO familiar_transactions
                    (user_id, type, instance_id, familiar_id, level,
                     coin_amount, material_instance_id, note, created_at)
                VALUES
                    (?, 'fusion', ?, ?, ?, 0, ?, NULL, ?)
                """,
                (
                    user_id,
                    base_instance_id,
                    base["familiar_id"],
                    new_level,
                    material_instance_id,
                    now,
                ),
            )

    return {
        "ok": True,
        "error": None,
        "familiar_id": base["familiar_id"],
        "level": new_level,
    }


# ==================================================
# 売却
# ==================================================
def sell_familiar(
    user_id: int,
    *,
    instance_id: int,
    price: int,
    locked_instance_ids: set[int] | frozenset[int],
) -> dict[str, Any]:
    """所有使い魔を売却し、coinを受け取る（10.2節）。

    売却額 ``price`` はサービス層が ``master_data.sell_price`` で計算した
    値をそのまま使い、ここでは検算しません。状態変更・coin加算・履歴を
    1つのトランザクションで確定します。
    """

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row

        with conn:
            conn.execute("BEGIN IMMEDIATE")

            row = conn.execute(
                """
                SELECT instance_id, user_id, familiar_id, level, status
                FROM player_familiars
                WHERE instance_id = ?
                """,
                (instance_id,),
            ).fetchone()

            if (
                row is None
                or int(row["user_id"]) != user_id
                or row["status"] != "owned"
            ):
                conn.rollback()
                return {"ok": False, "error": "not_owned"}

            if instance_id in locked_instance_ids:
                conn.rollback()
                return {"ok": False, "error": "in_use"}

            familiar_id = row["familiar_id"]
            level = int(row["level"])
            now = _now()

            conn.execute(
                """
                UPDATE player_familiars
                SET status = 'sold',
                    updated_at = ?
                WHERE instance_id = ?
                """,
                (now, instance_id),
            )
            conn.execute(
                """
                INSERT INTO balances (user_id, balance)
                VALUES (?, ?)
                ON CONFLICT(user_id)
                DO UPDATE SET balance = balance + excluded.balance
                """,
                (user_id, price),
            )
            conn.execute(
                """
                INSERT INTO transactions
                    (type, executor_id, target_id, amount, note, created_at)
                VALUES
                    ('使い魔売却', ?, ?, ?, ?, ?)
                """,
                (user_id, user_id, price, f"{familiar_id} Lv.{level}", now),
            )
            conn.execute(
                """
                INSERT INTO familiar_transactions
                    (user_id, type, instance_id, familiar_id, level,
                     coin_amount, material_instance_id, note, created_at)
                VALUES
                    (?, 'sell', ?, ?, ?, ?, NULL, NULL, ?)
                """,
                (user_id, instance_id, familiar_id, level, price, now),
            )

    return {
        "ok": True,
        "error": None,
        "familiar_id": familiar_id,
        "level": level,
        "price": price,
    }


__all__ = [
    "count_owned_familiars",
    "draw_gacha",
    "fuse_familiar",
    "get_owned_familiar",
    "get_owned_familiars",
    "get_same_familiars",
    "sell_familiar",
    "sync_master_data",
]
