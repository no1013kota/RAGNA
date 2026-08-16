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
    initial_level: int = 1,
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
                        (?, ?, ?, 'owned', ?, ?)
                    """,
                    (user_id, familiar_id, initial_level, now, now),
                )
                instance_id = int(cursor.lastrowid)

                conn.execute(
                    """
                    INSERT INTO familiar_transactions
                        (user_id, type, instance_id, familiar_id, level,
                         coin_amount, material_instance_id, note, created_at)
                    VALUES
                        (?, 'gacha', ?, ?, ?, 0, NULL, ?, ?)
                    """,
                    (user_id, instance_id, familiar_id, initial_level, pool_id, now),
                )

                instances.append(
                    {
                        "instance_id": instance_id,
                        "familiar_id": familiar_id,
                        "rank": rank,
                        "level": initial_level,
                    }
                )

    return {"ok": True, "error": None, "instances": instances}


# ==================================================
# 合成
# ==================================================
def count_fusable_materials(
    user_id: int,
    *,
    base_instance_id: int,
    locked_instance_ids: set[int] | frozenset[int],
) -> int:
    """素材にできる同種の使い魔の体数を返す。"""

    base = get_owned_familiar(base_instance_id)
    if base is None or int(base["user_id"]) != user_id or base["status"] != "owned":
        return 0

    materials = get_same_familiars(
        user_id, base["familiar_id"], exclude_instance_id=base_instance_id
    )
    return sum(
        1
        for row in materials
        if int(row["instance_id"]) not in locked_instance_ids
    )


def fuse_familiar(
    user_id: int,
    *,
    base_instance_id: int,
    material_count: int,
    max_level: int,
    locked_instance_ids: set[int] | frozenset[int],
) -> dict[str, Any]:
    """同じ種類の使い魔を素材にしてレベルを上げる（10.2節）。

    ``material_count`` 体を一度に合成し、レベルはその体数だけ上がります。
    素材はレベルの低い個体から自動で選び、``status = 'fused'`` にして
    所有一覧から外します。編成ロック中・進行中バトルで使用中の個体は
    素材にも土台にもできません。
    """

    if material_count < 1:
        return {"ok": False, "error": "invalid_count"}

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row

        with conn:
            conn.execute("BEGIN IMMEDIATE")

            base = conn.execute(
                """
                SELECT instance_id, user_id, familiar_id, level, status
                FROM player_familiars
                WHERE instance_id = ?
                """,
                (base_instance_id,),
            ).fetchone()

            if (
                base is None
                or int(base["user_id"]) != user_id
                or base["status"] != "owned"
            ):
                conn.rollback()
                return {"ok": False, "error": "not_owned"}

            if base_instance_id in locked_instance_ids:
                conn.rollback()
                return {"ok": False, "error": "in_use"}

            before_level = int(base["level"])
            if before_level >= max_level:
                conn.rollback()
                return {"ok": False, "error": "max_level"}

            if before_level + material_count > max_level:
                conn.rollback()
                return {
                    "ok": False,
                    "error": "over_max_level",
                    "available_levels": max_level - before_level,
                }

            # 素材はレベルの低い個体から使う（強い個体を残す）。
            candidates = conn.execute(
                """
                SELECT instance_id
                FROM player_familiars
                WHERE user_id = ?
                  AND familiar_id = ?
                  AND status = 'owned'
                  AND instance_id != ?
                ORDER BY
                    level ASC,
                    instance_id ASC
                """,
                (user_id, base["familiar_id"], base_instance_id),
            ).fetchall()

            material_ids = [
                int(row["instance_id"])
                for row in candidates
                if int(row["instance_id"]) not in locked_instance_ids
            ][:material_count]

            if len(material_ids) < material_count:
                conn.rollback()
                return {
                    "ok": False,
                    "error": "not_enough_materials",
                    "available_materials": len(material_ids),
                }

            now = _now()
            new_level = before_level + material_count

            conn.execute(
                """
                UPDATE player_familiars
                SET level = ?,
                    updated_at = ?
                WHERE instance_id = ?
                """,
                (new_level, now, base_instance_id),
            )

            for step, material_id in enumerate(material_ids, start=1):
                conn.execute(
                    """
                    UPDATE player_familiars
                    SET status = 'fused',
                        updated_at = ?
                    WHERE instance_id = ?
                    """,
                    (now, material_id),
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
                        before_level + step,
                        material_id,
                        now,
                    ),
                )

    return {
        "ok": True,
        "error": None,
        "familiar_id": base["familiar_id"],
        "before_level": before_level,
        "level": new_level,
        "material_instance_ids": material_ids,
    }


# ==================================================
# 売却
# ==================================================
def sell_familiars(
    user_id: int,
    *,
    prices: dict[int, int],
    locked_instance_ids: set[int] | frozenset[int],
) -> dict[str, Any]:
    """所有使い魔をまとめて売却し、coinを受け取る（10.2節）。

    ``prices`` は ``{個体ID: 売却額}`` です。売却額はサービス層が
    ``master_data.sell_price`` で計算した値をそのまま使い、ここでは検算
    しません。1体でも売却できない場合は全体を取り消し、coinも増えません。
    """

    if not prices:
        return {"ok": False, "error": "invalid_count", "sold": [], "total": 0}

    instance_ids = sorted(prices)

    with closing(get_connection()) as conn:
        conn.row_factory = sqlite3.Row

        with conn:
            conn.execute("BEGIN IMMEDIATE")

            placeholders = ",".join("?" for _ in instance_ids)
            rows = conn.execute(
                f"""
                SELECT instance_id, user_id, familiar_id, level, status
                FROM player_familiars
                WHERE instance_id IN ({placeholders})
                """,
                instance_ids,
            ).fetchall()

            found = {int(row["instance_id"]): dict(row) for row in rows}

            for instance_id in instance_ids:
                record = found.get(instance_id)
                if (
                    record is None
                    or int(record["user_id"]) != user_id
                    or record["status"] != "owned"
                ):
                    conn.rollback()
                    return {"ok": False, "error": "not_owned", "sold": [], "total": 0}

                if instance_id in locked_instance_ids:
                    conn.rollback()
                    return {"ok": False, "error": "in_use", "sold": [], "total": 0}

            now = _now()
            sold: list[dict[str, Any]] = []
            total = 0

            for instance_id in instance_ids:
                record = found[instance_id]
                familiar_id = record["familiar_id"]
                level = int(record["level"])
                price = int(prices[instance_id])
                total += price

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
                    INSERT INTO familiar_transactions
                        (user_id, type, instance_id, familiar_id, level,
                         coin_amount, material_instance_id, note, created_at)
                    VALUES
                        (?, 'sell', ?, ?, ?, ?, NULL, NULL, ?)
                    """,
                    (user_id, instance_id, familiar_id, level, price, now),
                )

                sold.append(
                    {
                        "instance_id": instance_id,
                        "familiar_id": familiar_id,
                        "level": level,
                        "price": price,
                    }
                )

            note = f"{sold[0]['familiar_id']} Lv.{sold[0]['level']}"
            if len(sold) > 1:
                note = f"{note} 他{len(sold) - 1}体"

            conn.execute(
                """
                INSERT INTO balances (user_id, balance)
                VALUES (?, ?)
                ON CONFLICT(user_id)
                DO UPDATE SET balance = balance + excluded.balance
                """,
                (user_id, total),
            )
            conn.execute(
                """
                INSERT INTO transactions
                    (type, executor_id, target_id, amount, note, created_at)
                VALUES
                    ('使い魔売却', ?, ?, ?, ?, ?)
                """,
                (user_id, user_id, total, note, now),
            )

    return {"ok": True, "error": None, "sold": sold, "total": total}


__all__ = [
    "count_fusable_materials",
    "count_owned_familiars",
    "draw_gacha",
    "fuse_familiar",
    "get_owned_familiar",
    "get_owned_familiars",
    "get_same_familiars",
    "sell_familiars",
    "sync_master_data",
]
