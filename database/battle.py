"""RAGNA Onlineのギルドバトル関連データアクセスの公開入口。

出場者セット、バトル申請・募集、進行中バトルの状態保存、終了処理、報酬付与、
運営操作ログまでを担当します。SQLはこのモジュール群へ閉じ込め、Cogやgame
パッケージからは公開関数だけを呼び出してください。

実装は責務ごとに兄弟モジュールへ分けてあります。このファイルは、それらを
まとめて再公開するだけの入口です（``from database.battle import ...`` は
これまでどおり動きます）。

- ``database.battle_common``: 共通ヘルパと定数
- ``database.battle_roster``: 出場者セットと事前登録の同期（9節）
- ``database.battle_match``: 占有ロック・申請・募集・バトル作成（12〜14節）
- ``database.battle_state``: 戦闘状態の読み書き・バトル参照更新・行動ログ（14・16・23節）
- ``database.battle_result``: バトル終了・報酬・運営操作ログ・保守（26・29節）

設計上の要点（docs/GAME_SPEC.md 9・12・13・14・26・27・29節）:

- ``guild_battle_locks`` はギルドIDが主キーです。申請・募集・進行中バトルを
  合わせて1ギルド1件に制限し、二重参加を防ぎます（12.1節・27節）。
  → ``database.battle_match``
- 募集への同時申込みは ``claim_battle_recruitment`` の ``BEGIN IMMEDIATE`` +
  ``status='open'`` 条件付きUPDATEで、最初の1件だけを成立させます（12.2節）。
  → ``database.battle_match``
- ボタンの二重押しや再送で同じ行動を2回処理しないよう、``save_battle_state`` は
  ``action_seq`` による楽観ロックで更新します（16節・27節）。
  → ``database.battle_state``
- バトル終了処理は二重実行を防ぐため、進行中の状態からの遷移だけを許可します（26.2節）。
  → ``database.battle_result``

再公開だけを行うファイルなので、未使用importの指摘は意図どおりです。名前を
1つでも落とすと呼び出し側がImportErrorになるため、星importは使わず明示的に
列挙しています。
"""

# ruff: noqa: F401

from __future__ import annotations

import logging

from .battle_common import ACTIVE_BATTLE_STATUSES
from .battle_match import (
    acquire_battle_lock,
    claim_battle_recruitment,
    create_battle,
    create_battle_recruitment,
    create_battle_request,
    get_battle_lock,
    get_battle_recruitment,
    get_battle_recruitment_by_message,
    get_battle_request,
    get_battle_request_by_message,
    get_pending_battle_request_for_guild,
    release_battle_lock,
    resolve_battle_recruitment,
    resolve_battle_request,
    set_battle_recruitment_message,
    set_battle_request_message,
)
from .battle_result import (
    add_admin_log,
    finish_battle,
    purge_old_battle_logs,
    settle_battle_bet,
    split_bet_evenly,
)
from .battle_roster import (
    add_battle_entry,
    clear_roster_familiars,
    count_member_entries,
    entry_cost_total,
    get_battle_entries,
    get_battle_roster,
    get_locked_instance_ids,
    get_player_battle_familiars,
    remove_battle_entry,
    renumber_battle_entries,
    set_battle_roster,
    set_player_battle_familiars,
    swap_battle_entry,
)
from .battle_state import (
    build_log_entries,
    get_active_battle_for_guild,
    get_active_battles,
    get_battle,
    get_battle_for_channel,
    get_battle_logs,
    get_battles_to_purge_channels,
    load_battle_state,
    mark_battle_channels_deleted,
    save_battle_state,
    set_battle_channel,
    set_battle_messages,
    set_battle_status,
    set_battle_turn_timing,
)


logger = logging.getLogger(__name__)

__all__ = [
    # 共通の定数
    "ACTIVE_BATTLE_STATUSES",
    # 出場者セット（9節）
    "add_battle_entry",
    "clear_roster_familiars",
    "count_member_entries",
    "entry_cost_total",
    "get_battle_entries",
    "get_battle_roster",
    "get_locked_instance_ids",
    "get_player_battle_familiars",
    "remove_battle_entry",
    "renumber_battle_entries",
    "set_battle_roster",
    "set_player_battle_familiars",
    "swap_battle_entry",
    # 占有ロック・申請・募集・バトル作成（12節・13節・14節）
    "acquire_battle_lock",
    "claim_battle_recruitment",
    "create_battle",
    "create_battle_recruitment",
    "create_battle_request",
    "get_battle_lock",
    "get_battle_recruitment",
    "get_battle_recruitment_by_message",
    "get_battle_request",
    "get_battle_request_by_message",
    "get_pending_battle_request_for_guild",
    "release_battle_lock",
    "resolve_battle_recruitment",
    "resolve_battle_request",
    "set_battle_recruitment_message",
    "set_battle_request_message",
    # 戦闘状態の読み書き・バトル参照更新・行動ログ（14節・16節・23節）
    "build_log_entries",
    "get_active_battle_for_guild",
    "get_active_battles",
    "get_battle",
    "get_battle_for_channel",
    "get_battle_logs",
    "get_battles_to_purge_channels",
    "load_battle_state",
    "mark_battle_channels_deleted",
    "save_battle_state",
    "set_battle_channel",
    "set_battle_messages",
    "set_battle_status",
    "set_battle_turn_timing",
    # バトル終了・報酬・運営操作ログ・保守（26節・29節）
    "add_admin_log",
    "finish_battle",
    "purge_old_battle_logs",
    "settle_battle_bet",
    "split_bet_evenly",
]
