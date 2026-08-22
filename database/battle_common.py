"""ギルドバトルのデータ層で共通に使う小さなヘルパと定数。

``database.battle_roster`` ``database.battle_match`` ``database.battle_state``
``database.battle_result`` から呼ばれる、現在時刻の文字列化、JSON列の読み書き、
戻り値の組み立てをまとめます。このファイル自体はSQLを持ちません。

設計上の要点（docs/GAME_SPEC.md 9・12・13・14・26・27・29節）:

- 戻り値は ``ok_result`` / ``error_result`` で組み立て、``{"ok": ..., "error": ...}``
  の形をデータ層全体でそろえます。エラーコードの文字列は呼び出し側の分岐に
  使われるため、増減も言い換えもしないでください。
- JSON列は壊れていても処理を止めず、空の辞書・空のリストとして読み込みます。
- ``remaining_seconds`` は値を持たない場合に保存済みの値を維持します。持ち時間を
  誤って0へ書き換えると時間切れ敗北になるためです（16節）。

複数のモジュールから呼ぶため、ヘルパは公開名（先頭のアンダースコアなし）に
しています。取り込む側は ``from .battle_common import now_iso`` のように明示して
ください（星importは使わない）。``now`` ``success`` ``failure`` のような、
呼び出し側の引数名・局所変数名とぶつかりやすい名前は避けています。
"""

from __future__ import annotations

import json
import logging
import sqlite3

from datetime import datetime, timezone
from typing import Any

from game.models import BattleState


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
def now_iso() -> str:
    """UTCのISO 8601文字列を返す。"""

    return datetime.now(timezone.utc).isoformat()


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    """1行を辞書へ変換する。行が無い場合は ``None`` を返す。"""

    if row is None:
        return None
    return dict(row)


def load_json_dict(raw: str | None) -> dict[str, Any]:
    """JSON列を辞書として読み込む。壊れていても処理を止めない。"""

    if not raw:
        return {}

    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        logger.warning(f"JSON列の読み込みに失敗しました: {raw!r}")
        return {}

    return value if isinstance(value, dict) else {}


def load_json_list(raw: str | None) -> list[Any]:
    """JSON列をリストとして読み込む。壊れていても処理を止めない。"""

    if not raw:
        return []

    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        logger.warning(f"JSON列の読み込みに失敗しました: {raw!r}")
        return []

    return list(value) if isinstance(value, list) else []


def dump_json(value: Any) -> str:
    """辞書・リストをJSON文字列へ変換する。空のリストは配列のまま保存する。"""

    if value is None:
        return "{}"

    return json.dumps(value, ensure_ascii=False)


def event_type_value(event_type: Any) -> str:
    """ログの種別を文字列へ揃える。

    ``BattleEvent`` のような列挙型をそのまま ``str()`` すると
    ``BattleEvent.ATTACK`` のような値になってしまうため、値の側を取り出します。
    """

    return str(getattr(event_type, "value", event_type))


def error_result(error: str, **payload: Any) -> dict[str, Any]:
    """失敗時の戻り値を組み立てる。"""

    return {"ok": False, "error": error, **payload}


def ok_result(**payload: Any) -> dict[str, Any]:
    """成功時の戻り値を組み立てる。"""

    return {"ok": True, "error": None, **payload}


def remaining_seconds(state: BattleState, guild_id: int, fallback: int) -> int:
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
