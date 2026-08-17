"""ギルドバトルの進行処理（DB保存とDiscord投稿の橋渡し）。

Discordの操作部品（View）は ``views.py``、Cog本体と定期タスクは ``cog.py`` に置き、
このモジュールは「開始前チェック」「バトル開始」「行動の適用」「終了処理と報酬」
といった、複数の入口から共通で使う処理だけを持ちます。

戦闘計算そのものは ``game.battle_engine``、SQLは ``database.battle`` の責務です。
ここでは次の2点を必ず守ります（docs/GAME_SPEC.md 16節・23節・24節・27節）。

- 行動の保存は ``action_seq`` の楽観ロックで行い、保存できなかった場合は
  Discordへ**何も投稿しない**（ボタン二重押し・再送による二重処理の防止）。
- 行動ログは両ギルドのバトル専用チャンネルへ同じ内容を投稿し、
  戦況Embedはチャンネルごとに最新1件を編集して更新する。
"""

from __future__ import annotations

import asyncio
import logging

from datetime import datetime, timedelta

import discord
import config

from cogs import game_shared
from database.battle import (
    create_battle,
    finish_battle,
    get_active_battle_for_guild,
    get_active_battles,
    get_battle,
    get_battle_entries,
    get_battle_lock,
    get_battle_roster,
    get_battles_to_purge_channels,
    load_battle_state,
    mark_battle_channels_deleted,
    release_battle_lock,
    save_battle_state,
    set_battle_channel,
    set_battle_messages,
    set_battle_status,
    set_battle_turn_timing,
    settle_battle_bet,
    split_bet_evenly,
)
from database.familiar import get_owned_familiar
from database.guild import get_guild, get_guild_members, get_player_guild
from game import battle_embed, battle_engine
from game.master_data import load_master_data
from game.models import (
    BATTLE_STATUS_IN_PROGRESS,
    BATTLE_STATUS_PAUSED,
    BATTLE_STATUS_PREPARING,
    RESULT_ABORTED,
    RESULT_DRAW,
    RESULT_GUILD_A,
    RESULT_GUILD_B,
    BattleAction,
    BattleRuleError,
    BattleState,
)


logger = logging.getLogger(__name__)

# 報酬日付の判定に使う日本時間
JST = game_shared.JST

# ``apply_action`` へ渡す行動の種類
AUTO_ACTION = "auto"
SURRENDER_ACTION = "surrender"

# 同一プロセス内で同じバトルを同時に処理しないための鍵。
# 別プロセスとの競合は ``save_battle_state`` の楽観ロックが防ぎます。
_battle_locks: dict[int, asyncio.Lock] = {}


# ==================================================
# 小さな共通ヘルパ
# ==================================================
# 日時と送受信の共通処理は cogs/game_shared.py が持つ
_now_utc = game_shared.now_utc
_parse_iso = game_shared.parse_iso


def surrender_action(guild_id: int) -> tuple[str, int]:
    """降参を ``apply_action`` へ渡す形にする。"""

    return (SURRENDER_ACTION, int(guild_id))


def battle_lock(battle_id: int) -> asyncio.Lock:
    """バトルごとの排他ロックを返す。"""

    lock = _battle_locks.get(battle_id)
    if lock is None:
        lock = asyncio.Lock()
        _battle_locks[battle_id] = lock

    return lock


def elapsed_seconds_since_turn_start(battle_row: dict | None) -> int:
    """ターン開始からの経過秒数を返す（22節）。

    ``turn_started_at`` はUTCのISO 8601文字列です。1操作あたりの制限時間を
    超える値は返さず、相手ギルドの持ち時間には影響させません
    （持ち時間を減らすのは ``battle_engine`` 側で行動者のギルドだけです）。
    """

    master = load_master_data()
    limit = master.battle.turn_time_seconds

    started_at = _parse_iso((battle_row or {}).get("turn_started_at"))
    if started_at is None:
        return 0

    elapsed = int((_now_utc() - started_at).total_seconds())
    return max(0, min(elapsed, limit))


def turn_remaining_seconds(battle_row: dict | None) -> int:
    """自動攻撃までの残り秒数を返す（17節）。"""

    master = load_master_data()
    limit = master.battle.turn_time_seconds

    deadline = _parse_iso((battle_row or {}).get("turn_deadline_at"))
    if deadline is None:
        return limit

    remaining = int((deadline - _now_utc()).total_seconds())
    return max(0, min(remaining, limit))


def _side_key(state: BattleState, guild_id: int, column: str) -> str:
    """``guild_battles`` のギルド別列名を組み立てる。"""

    side = "a" if guild_id == state.guild_a_id else "b"
    return f"guild_{side}_{column}"


def guild_names(state: BattleState) -> dict[int, str]:
    """戦況Embed用のギルド名辞書を作る。"""

    names: dict[int, str] = {}
    for guild_id in (state.guild_a_id, state.guild_b_id):
        row = get_guild(guild_id)
        names[guild_id] = row["name"] if row else f"ギルド{guild_id}"

    return names


def player_names(bot: discord.Client, state: BattleState) -> dict[int, str]:
    """行動ログ用の表示名辞書を作る。

    サーバー上で表示名を取得できたプレイヤーだけを入れます。取得できない
    プレイヤーは ``game.battle_embed`` 側でメンション表記になります。
    """

    guild = bot.get_guild(config.GUILD_ID)
    if guild is None:
        return {}

    names: dict[int, str] = {}
    for unit in state.units.values():
        member = guild.get_member(unit.player_id)
        if member is not None:
            names[unit.player_id] = member.display_name

    return names


def command_channel_ids(state: BattleState) -> dict[int, int]:
    """両ギルドのバトル専用チャンネルIDを返す（34.14節）。"""

    battle_row = get_battle(state.battle_id) or {}

    channel_ids: dict[int, int] = {}
    for guild_id in (state.guild_a_id, state.guild_b_id):
        channel_id = battle_row.get(_side_key(state, guild_id, "channel_id"))
        if channel_id:
            channel_ids[guild_id] = int(channel_id)

    return channel_ids


def battle_channels(bot: discord.Client, state: BattleState) -> dict[int, discord.TextChannel]:
    """両ギルドのバトル専用チャンネルを返す。取得できないギルドは除外する。"""

    guild = bot.get_guild(config.GUILD_ID)
    if guild is None:
        return {}

    channels: dict[int, discord.TextChannel] = {}
    for guild_id, channel_id in command_channel_ids(state).items():
        channel = guild.get_channel(channel_id)
        if isinstance(channel, discord.TextChannel):
            channels[guild_id] = channel

    return channels


def _command_view() -> discord.ui.View:
    """バトル専用チャンネルの操作Viewを作る（循環import回避のため遅延import）。"""

    from .views import BattleCommandView

    return BattleCommandView()


_safe_send = game_shared.safe_send
_delete_message = game_shared.safe_delete_message


def save_state(state: BattleState, *, expected_action_seq: int) -> bool:
    """戦闘状態を保存する（``action_seq`` の楽観ロック付き）。

    決着した状態は ``status`` を進行中のまま保存し、``finished`` / ``aborted``
    への遷移は ``database.battle.finish_battle`` へ任せます。終了処理は
    「進行中からの遷移」だけを許可することで二重実行を防いでいるため、ここで
    先に終了状態を書き込むと、編成ロック解除・占有ロック解放・戦績反映・報酬付与が
    実行されなくなります（26.2節）。``result`` と ``end_reason`` は保存するので、
    保存後に停止しても復旧時に決着済みだと判断できます。
    """

    final_status = state.status
    if battle_engine.is_finished(state):
        state.status = BATTLE_STATUS_IN_PROGRESS

    try:
        return save_battle_state(state, expected_action_seq=expected_action_seq)
    finally:
        state.status = final_status


def _restore_finished_status(state: BattleState) -> bool:
    """保存済みだが終了処理が済んでいない状態かを判定し、``status`` を戻す。"""

    if state.status != BATTLE_STATUS_IN_PROGRESS or state.result is None:
        return False

    state.status = "aborted" if state.result == RESULT_ABORTED else "finished"
    return True


# ==================================================
# 開始前チェックとバトル開始（13節）
# ==================================================
def _players_in_active_battles(exclude_battle_id: int | None = None) -> set[int]:
    """進行中バトルへ参加しているプレイヤーIDを集める。"""

    players: set[int] = set()
    for row in get_active_battles():
        if exclude_battle_id is not None and row["battle_id"] == exclude_battle_id:
            continue

        state = load_battle_state(row["battle_id"])
        if state is None:
            continue

        players.update(unit.player_id for unit in state.units.values())

    return players


def check_guild_ready(
    bot: discord.Client,
    guild_id: int,
    *,
    busy_players: set[int] | None = None,
) -> tuple[list[str], list[dict]]:
    """1ギルド分の開始前チェックを行う（13節）。

    戻り値は ``(不足内容のリスト, 戦闘用使い魔の元データ)`` です。不足が1つでも
    あれば2つ目は使いません。
    """

    master = load_master_data()
    issues: list[str] = []
    entries: list[dict] = []

    guild_row = get_guild(guild_id)
    if guild_row is None or guild_row["status"] != "active":
        return ["ギルドが見つからない、または活動中ではありません。"], []

    discord_guild = bot.get_guild(config.GUILD_ID)
    if discord_guild is None:
        return ["Discordサーバー情報を取得できませんでした。"], []

    # チャンネルが利用可能か
    channel_labels = (
        ("category_id", "ギルドカテゴリー"),
        ("guild_text_channel_id", "ギルドTC"),
        ("master_text_channel_id", "ギルドマスター専用TC"),
        ("battle_member_channel_id", "使い魔セットチャンネル"),
    )
    for column, label in channel_labels:
        channel_id = guild_row.get(column)
        if not channel_id or discord_guild.get_channel(int(channel_id)) is None:
            issues.append(f"{label}が見つかりません。")

    # ギルドが他の進行中バトルへ参加していないか
    if get_active_battle_for_guild(guild_id) is not None:
        issues.append("このギルドには既に進行中のバトルがあります。")

    roster = get_battle_roster(guild_id)
    member_ids = {int(member["user_id"]) for member in roster}

    if not roster:
        issues.append(
            "出場者がセットされていません。"
            "ギルドマスター専用tcの「メンバーセット」で決めてください。"
        )
    elif len(roster) > master.battle.max_members:
        issues.append(
            f"出場者は最大{master.battle.max_members}人です（現在{len(roster)}人）。"
        )

    total_cost = 0

    battle_entries = get_battle_entries(guild_id)

    if not battle_entries:
        issues.append("使い魔がセットされていません。")
    elif len(battle_entries) > master.battle.max_units:
        issues.append(
            f"使い魔は最大{master.battle.max_units}体です"
            f"（現在{len(battle_entries)}体）。"
        )

    if busy_players is None:
        busy_players = _players_in_active_battles()

    set_counts: dict[int, int] = {}
    for entry in battle_entries:
        owner_id = int(entry["user_id"])
        set_counts[owner_id] = set_counts.get(owner_id, 0) + 1

    # 出場者そのものの確認（使い魔をセットしていない人も対象にする）
    for member in roster:
        user_id = int(member["user_id"])
        mention = f"<@{user_id}>"

        player_guild = get_player_guild(user_id)
        if player_guild is None or int(player_guild["guild_id"]) != guild_id:
            issues.append(f"{mention} は現在このギルドへ所属していません。")
            continue

        if user_id in busy_players:
            issues.append(f"{mention} は他の進行中バトルへ参加しています。")

        # 9節：マスターが割り当てた体数まで埋まっていること
        assigned = int(member["familiar_count"] or 0)
        current = set_counts.get(user_id, 0)
        if current < assigned:
            issues.append(
                f"{mention} の割り当ては{assigned}体ですが、{current}体しか"
                "セットされていません。"
            )

    # セットされた使い魔ごとの確認
    for entry in battle_entries:
        user_id = int(entry["user_id"])
        instance_id = int(entry["instance_id"])
        mention = f"<@{user_id}>"

        if user_id not in member_ids:
            issues.append(f"{mention} は出場者から外れています。")
            continue

        owned = get_owned_familiar(instance_id)
        if (
            owned is None
            or int(owned["user_id"]) != user_id
            or owned["status"] != "owned"
        ):
            issues.append(f"{mention} がセットした使い魔を現在所有していません。")
            continue

        familiar = master.get_familiar(owned["familiar_id"])
        if familiar is None:
            issues.append(f"{mention} がセットした使い魔のマスターデータがありません。")
            continue

        rank_info = game_shared.get_player_rank_info(user_id)
        if rank_info is None:
            # 27節: 推測でランクを補わず操作を止める
            issues.append(f"{mention} のプレイヤーランクを確認できません。")
            continue

        if not master.can_use_rank(
            rank_info["player_rank"],
            familiar.rank,
            is_sub_manager=rank_info["is_sub_manager"],
        ):
            issues.append(
                f"{mention} は{familiar.name}"
                f"（{game_shared.rank_label(familiar.rank)}）を使役できません。"
            )
            continue

        total_cost += familiar.cost

        entries.append(
            {
                "guild_id": guild_id,
                "player_id": user_id,
                "instance_id": instance_id,
                "familiar_id": owned["familiar_id"],
                "level": int(owned["level"]),
                "slot": int(entry["entry_slot"]),
            }
        )

    # COST上限を設けている場合だけ確認する（0以下なら無制限）
    cost_limit = master.battle.max_total_cost
    if cost_limit > 0 and total_cost > cost_limit:
        issues.append(
            f"編成の合計COSTが上限を超えています（{total_cost}／{cost_limit}）。"
            "COSTの低い使い魔へ入れ替えてください。"
        )

    return issues, entries


def lock_issues(guild_id: int) -> list[str]:
    """申請・募集を受け付けられない占有状態を、具体的な理由にして返す（12節）。"""

    issues: list[str] = []

    lock = get_battle_lock(guild_id)
    if lock is None:
        return issues

    lock_type = str(lock["lock_type"])

    if lock_type == "recruitment":
        issues.append("このギルドは現在バトルを募集中です。先に募集を取り消してください。")
    elif lock_type == "request":
        issues.append(
            "このギルドには回答待ちのバトル申請があります。"
            "先に取り消すか、相手の回答を待ってください。"
        )
    elif lock_type == "battle":
        issues.append("このギルドは現在バトル中です。終了までお待ちください。")
    else:
        issues.append("このギルドは現在ほかの対戦手続き中です。")

    return issues


def entry_blockers(bot: discord.Client, guild_id: int) -> list[str]:
    """申請・募集・対戦申請を出せない理由をすべて集めて返す（12節）。

    占有状態（募集中・申請中・バトル中）と、開始前チェックの不足内容を
    まとめます。空なら受け付けられます。
    """

    issues = lock_issues(guild_id)
    ready_issues, _ = check_guild_ready(bot, guild_id)

    for issue in ready_issues:
        if issue not in issues:
            issues.append(issue)

    return issues


def blocker_message(issues: list[str], *, action: str) -> str:
    """不足内容を、そのまま利用者へ出せる文面にする。"""

    lines = [f"**{action}できません。** 次の点を直してください。", ""]
    lines.extend(f"・{issue}" for issue in issues)

    return "\n".join(lines)[:1900]


async def _notify_start_failure(
    bot: discord.Client,
    guild_ids: tuple[int, int],
    problems: dict[int, list[str]],
) -> None:
    """開始前チェックで不足した内容を両ギルドへ具体的に通知する（13節）。"""

    discord_guild = bot.get_guild(config.GUILD_ID)
    if discord_guild is None:
        return

    lines = ["次の条件を満たしてから、改めて申請・募集してください。"]

    for guild_id in guild_ids:
        guild_row = get_guild(guild_id)
        name = guild_row["name"] if guild_row else f"ギルド{guild_id}"
        issues = problems.get(guild_id) or ["条件を満たしています。"]

        lines.append("")
        lines.append(game_shared.item_line(name, ""))
        lines.extend(f"・{issue}" for issue in issues)

    embed = discord.Embed(
        title="⚔ ギルドバトルを開始できませんでした",
        description="\n".join(lines)[:4000],
        color=config.COLOR_RED,
    )

    for guild_id in guild_ids:
        guild_row = get_guild(guild_id)
        if guild_row is None:
            continue

        channel_id = guild_row.get("master_text_channel_id")
        if not channel_id:
            continue

        channel = discord_guild.get_channel(int(channel_id))
        if isinstance(channel, discord.TextChannel):
            await _safe_send(channel, embed=embed)


async def ensure_battle_channels(
    bot: discord.Client, battle_id: int, guild_ids: tuple[int, ...]
) -> dict[int, int]:
    """両ギルドのカテゴリー内にバトル専用チャンネルを用意する（34.14節）。

    既に作成済みの場合はそのIDを返します。所属メンバー全員が観戦でき、
    投稿はBotだけが行います。
    """

    discord_guild = bot.get_guild(config.GUILD_ID)
    if discord_guild is None:
        return {}

    battle_row = get_battle(battle_id) or {}
    guild_a_id = int(battle_row.get("guild_a_id") or guild_ids[0])

    created: dict[int, int] = {}

    for guild_id in guild_ids:
        side = "a" if int(guild_id) == guild_a_id else "b"
        existing = battle_row.get(f"guild_{side}_channel_id")

        if existing and discord_guild.get_channel(int(existing)) is not None:
            created[int(guild_id)] = int(existing)
            continue

        guild_row = get_guild(int(guild_id))
        if guild_row is None:
            continue

        member_ids = [int(row["user_id"]) for row in get_guild_members(int(guild_id))]
        channel_id = await game_shared.create_battle_channel(
            discord_guild, guild_row, battle_id=battle_id, member_ids=member_ids
        )

        if channel_id is None:
            continue

        set_battle_channel(battle_id, guild_id=int(guild_id), channel_id=channel_id)
        created[int(guild_id)] = channel_id

    return created


async def purge_battle_channels(bot: discord.Client) -> int:
    """保存期間を過ぎたバトル専用チャンネルを削除する（34.14節）。"""

    discord_guild = bot.get_guild(config.GUILD_ID)
    if discord_guild is None:
        return 0

    master = load_master_data()
    before = (
        _now_utc() - timedelta(days=master.battle.battle_channel_retention_days)
    ).isoformat()

    deleted = 0

    for battle_row in get_battles_to_purge_channels(before):
        results = [
            await game_shared.delete_channel(
                discord_guild, battle_row.get("guild_a_channel_id")
            ),
            await game_shared.delete_channel(
                discord_guild, battle_row.get("guild_b_channel_id")
            ),
        ]

        if all(results):
            mark_battle_channels_deleted(int(battle_row["battle_id"]))
            deleted += 1

    return deleted


async def try_start_battle(
    bot: discord.Client,
    guild_a_id: int,
    guild_b_id: int,
    *,
    bet_coin: int | None = None,
) -> dict:
    """開始前チェックを行い、通過したらバトルを開始する（13節）。

    1つでも条件が欠けている場合はバトルを作らず、不足内容を両ギルドへ通知し、
    確保していた占有ロックを解放します。``bet_coin`` は申請・募集で決めた
    ギルドごとのベット額です（未指定ならマスターデータの既定値）。
    """

    master = load_master_data()
    guild_ids = (int(guild_a_id), int(guild_b_id))

    busy_players = _players_in_active_battles()
    problems: dict[int, list[str]] = {}
    entries: list[dict] = []

    for guild_id in guild_ids:
        issues, guild_entries = check_guild_ready(
            bot, guild_id, busy_players=busy_players
        )
        if issues:
            problems[guild_id] = issues
        else:
            entries.extend(guild_entries)

    if problems:
        for guild_id in guild_ids:
            release_battle_lock(guild_id)
        await _notify_start_failure(bot, guild_ids, problems)
        return {"ok": False, "error": "precheck_failed", "problems": problems}

    try:
        units = battle_engine.build_unit_payloads(entries)
    except BattleRuleError as exc:
        for guild_id in guild_ids:
            release_battle_lock(guild_id)
        problems = {guild_ids[0]: [str(exc)], guild_ids[1]: [str(exc)]}
        await _notify_start_failure(bot, guild_ids, problems)
        return {"ok": False, "error": "master_data_error", "problems": problems}

    created = create_battle(
        guild_a_id=guild_ids[0],
        guild_b_id=guild_ids[1],
        guild_time_seconds=master.battle.guild_time_seconds,
        units=units,
        bet_coin=default_bet_coin() if bet_coin is None else int(bet_coin),
    )

    if not created["ok"]:
        for guild_id in guild_ids:
            release_battle_lock(guild_id)
        message = game_shared.error_message(created["error"])
        await _notify_start_failure(
            bot, guild_ids, {guild_ids[0]: [message], guild_ids[1]: [message]}
        )
        return created

    battle_id = int(created["battle_id"])

    # 対戦成立時に、各ギルドのカテゴリー内へバトル専用チャンネルを作る（34.14節）
    await ensure_battle_channels(bot, battle_id, guild_ids)

    started = await start_prepared_battle(bot, battle_id)

    if not started:
        return {"ok": False, "error": "start_failed", "battle_id": battle_id}

    await game_shared.game_admin_log(
        bot,
        action="ギルドバトル開始",
        target_guild_id=guild_ids[0],
        target_battle_id=battle_id,
        detail=f"{guild_ids[0]} vs {guild_ids[1]}",
    )

    return {"ok": True, "error": None, "battle_id": battle_id}


async def start_prepared_battle(bot: discord.Client, battle_id: int) -> bool:
    """``preparing`` のバトルを開始し、開始告知までを投稿する。"""

    async with battle_lock(battle_id):
        state = load_battle_state(battle_id)
        if state is None or state.status != BATTLE_STATUS_PREPARING:
            return False

        expected = state.action_seq
        try:
            battle_engine.start_battle(state)
        except BattleRuleError:
            logger.exception(f"バトルを開始できませんでした: battle_id={battle_id}")
            return False

        state.action_seq = expected + 1
        if not save_state(state, expected_action_seq=expected):
            logger.info(f"バトル開始の保存を破棄しました: battle_id={battle_id}")
            return False

        # started_at を記録する（保存済みの status は既に in_progress）
        set_battle_status(battle_id, BATTLE_STATUS_IN_PROGRESS)

        master = load_master_data()
        channels = battle_channels(bot, state)
        names = guild_names(state)
        bet_coin = battle_bet_coin(battle_id)

        guild_time = battle_embed.format_remaining_time(
            master.battle.guild_time_seconds
        )
        opening = discord.Embed(
            title="⚔ GUILD BATTLE 開始",
            description="\n".join(
                [
                    f"**{names.get(state.guild_a_id, '—')}** vs "
                    f"**{names.get(state.guild_b_id, '—')}**",
                    "",
                    game_shared.item_line("ギルドの持ち時間", guild_time),
                    game_shared.item_line(
                        "1操作の制限時間",
                        battle_embed.format_remaining_time(
                            master.battle.turn_time_seconds
                        ),
                    ),
                    game_shared.item_line(
                        "ベット額",
                        f"ギルドごと {game_shared.format_coin(bet_coin)}"
                        "（出場者で均等に分担）",
                    ),
                    game_shared.item_line(
                        "XP", f"勝利 {master.battle.bet.win_xp}／敗北 {master.battle.bet.lose_xp}"
                    ),
                    "",
                    f"-# {bet_notice(bet_coin)}",
                ]
            ),
            color=config.COLOR_PURPLE,
        )

        labels = player_names(bot, state)

        for guild_id, channel in channels.items():
            await _safe_send(channel, embed=opening)

            # 編成表はギルドごとに内容が変わる（自分はスキル効果まで、相手は名前だけ）
            await _safe_send(
                channel,
                embed=battle_embed.build_lineup_embed(
                    state,
                    guild_id=guild_id,
                    guild_names=names,
                    player_names=labels,
                    bet_notice=bet_share_notice(
                        bet_coin, len(state.guild_units(guild_id))
                    ),
                ),
            )

        await publish_progress(bot, state, channels=channels)
        return True


# ==================================================
# バトル進行の中核（16節・23節・24節）
# ==================================================
def _run_engine(state: BattleState, action, elapsed_seconds: int) -> None:
    """行動の種類に応じて ``battle_engine`` を呼び分ける。"""

    if isinstance(action, BattleAction):
        battle_engine.submit_action(state, action, elapsed_seconds=elapsed_seconds)
    elif isinstance(action, tuple) and action and action[0] == SURRENDER_ACTION:
        battle_engine.surrender(state, int(action[1]))
    elif action == AUTO_ACTION:
        battle_engine.auto_action(state, elapsed_seconds=elapsed_seconds)
    else:
        raise BattleRuleError("未対応の行動です。")

    # 持ち時間が0になったギルドを時間切れ敗北にする（22節）
    if not battle_engine.is_finished(state):
        battle_engine.check_time_over(state)


async def apply_action(
    bot: discord.Client,
    battle_id: int,
    action,
    *,
    elapsed_seconds: int,
    expected_seq: int | None,
) -> bool:
    """1回の行動を戦闘へ反映し、保存できた場合だけDiscordへ投稿する。

    保存は ``action_seq`` の楽観ロックで行い、他の操作が先に処理されていた場合は
    ``False`` を返して**何も投稿しません**（16節の二重処理防止）。戦闘ルール上
    実行できない行動は ``BattleRuleError`` を送出します。
    """

    async with battle_lock(battle_id):
        # 1. 現在の状態を読み直す
        state = load_battle_state(battle_id)
        if state is None or state.status != BATTLE_STATUS_IN_PROGRESS:
            return False

        # 決着済みなのに終了処理が済んでいない場合は、先に後片付けを終わらせる
        if _restore_finished_status(state):
            await finish_battle_flow(bot, state)
            return False

        # 2. 期待する action_seq を控える
        expected = state.action_seq
        if expected_seq is not None and expected != int(expected_seq):
            return False

        # 3. 戦闘エンジンで行動を処理する
        _run_engine(state, action, int(elapsed_seconds))

        # 4. action_seq を1つ進める
        state.action_seq = expected + 1

        # 5. 保存できなければ何も投稿しない
        if not save_state(state, expected_action_seq=expected):
            return False

        # 6～9. 行動ログ・戦況Embed・ターン通知・終了処理
        await publish_progress(bot, state)
        return True


async def publish_progress(
    bot: discord.Client,
    state: BattleState,
    *,
    channels: dict[int, discord.TextChannel] | None = None,
) -> None:
    """保存済みの戦闘状態をDiscordへ反映する（23節・24節・26節）。"""

    if channels is None:
        channels = battle_channels(bot, state)

    # 投稿より先に操作の起点を更新する。
    # ここで更新しておかないと、投稿が失敗した場合に turn_deadline_at が
    # 過去のまま残り、見張りタスクが15秒ごとに自動攻撃を繰り返して
    # ギルド持ち時間を不当に消費してしまう（17節・22節）。
    if not battle_engine.is_finished(state):
        refresh_turn_deadline(state)

    try:
        await post_action_logs(bot, state, channels)
        await refresh_status_embeds(state, channels)
    except Exception:
        logger.exception(
            f"戦闘ログ・戦況の投稿に失敗しました: battle_id={state.battle_id}"
        )

    if battle_engine.is_finished(state):
        await finish_battle_flow(bot, state, channels=channels)
    else:
        await announce_turn(bot, state, channels=channels)


def refresh_turn_deadline(state: BattleState) -> datetime:
    """現在のターンの操作開始時刻と期限を更新し、開始時刻を返す（22節）。"""

    master = load_master_data()
    started_at = _now_utc()
    deadline = started_at + timedelta(seconds=master.battle.turn_time_seconds)

    set_battle_turn_timing(
        state.battle_id,
        turn_started_at=started_at.isoformat(),
        turn_deadline_at=deadline.isoformat(),
    )
    return started_at


async def post_action_logs(
    bot: discord.Client,
    state: BattleState,
    channels: dict[int, discord.TextChannel],
) -> None:
    """行動ログを両ギルドのバトル専用チャンネルへ同じ内容で投稿する（23節）。"""

    if not state.logs or not channels:
        return

    messages = battle_embed.build_action_log_messages(
        state,
        state.logs,
        player_names=player_names(bot, state),
        guild_names=guild_names(state),
    )

    for message in messages:
        for channel in channels.values():
            embed = message.embed.copy()
            attachment = (
                battle_embed.thumbnail_file(message.familiar_id)
                if message.familiar_id
                else None
            )
            if attachment is not None:
                embed.set_thumbnail(url=f"attachment://{attachment.filename}")

            # discord.File は1回しか送信できないため、チャンネルごとに作り直す
            await _safe_send(channel, embed=embed, file=attachment)


async def refresh_status_embeds(
    state: BattleState,
    channels: dict[int, discord.TextChannel],
) -> None:
    """戦況Embedをチャンネルごとに最新1件だけ編集して更新する（24節）。"""

    if not channels:
        return

    battle_row = get_battle(state.battle_id) or {}
    names = guild_names(state)

    current_unit = state.current_unit()
    highlight_guild_id = current_unit.guild_id if current_unit else None

    # 自動攻撃までの残り時間を戦況Embedへ表示する（17節）
    turn_remaining = turn_remaining_seconds(battle_row) if current_unit else None

    for guild_id, channel in channels.items():
        embed = battle_embed.build_status_embed(
            state,
            guild_names=names,
            highlight_guild_id=highlight_guild_id,
            turn_remaining_seconds=turn_remaining,
        )

        message_id = battle_row.get(_side_key(state, guild_id, "status_message_id"))
        if message_id:
            try:
                message = await channel.fetch_message(int(message_id))
                await message.edit(embed=embed)
                continue
            except discord.NotFound:
                # 削除されていた場合だけ新規投稿する
                pass
            except Exception:
                # 一時的な失敗では新規投稿せず、次の更新に任せる（二重投稿の防止）
                logger.warning(
                    f"戦況Embedの更新に失敗しました: channel_id={channel.id}"
                )
                continue

        posted = await _safe_send(channel, embed=embed)
        if posted is not None:
            set_battle_messages(
                state.battle_id, guild_id=guild_id, status_message_id=posted.id
            )


async def announce_turn(
    bot: discord.Client,
    state: BattleState,
    *,
    channels: dict[int, discord.TextChannel] | None = None,
) -> None:
    """次の行動者へターン通知を出し、前回のターン通知を削除する（16節・22節）。"""

    if channels is None:
        channels = battle_channels(bot, state)

    unit = state.current_unit()
    if unit is None:
        return

    master = load_master_data()
    battle_row = get_battle(state.battle_id) or {}

    refresh_turn_deadline(state)

    channel = channels.get(unit.guild_id)
    embed = battle_embed.build_turn_embed(
        state, unit, turn_seconds=master.battle.turn_time_seconds
    )
    attachment = battle_embed.thumbnail_file(unit.familiar_id)
    if attachment is not None:
        embed.set_thumbnail(url=f"attachment://{attachment.filename}")

    posted = await _safe_send(
        channel,
        content=f"<@{unit.player_id}>",
        embed=embed,
        view=_command_view(),
        file=attachment,
    )

    # 前回のターン通知は両ギルド分を削除し、常に現在の操作対象だけを残す
    for guild_id, other_channel in channels.items():
        previous_id = battle_row.get(_side_key(state, guild_id, "turn_message_id"))
        if previous_id:
            await _delete_message(other_channel, int(previous_id))
        set_battle_messages(state.battle_id, guild_id=guild_id, turn_message_id=None)

    if posted is not None:
        set_battle_messages(
            state.battle_id, guild_id=unit.guild_id, turn_message_id=posted.id
        )

    # 相手ギルドへは「誰のターンか」を知らせる（17節）
    names = guild_names(state)
    labels = player_names(bot, state)

    for guild_id, other_channel in channels.items():
        if guild_id == unit.guild_id:
            continue

        notice = await _safe_send(
            other_channel,
            embed=battle_embed.build_opponent_turn_embed(
                state, unit, guild_names=names, player_names=labels
            ),
        )
        if notice is not None:
            set_battle_messages(
                state.battle_id, guild_id=guild_id, turn_message_id=notice.id
            )


async def clear_turn_messages(
    state: BattleState,
    channels: dict[int, discord.TextChannel],
) -> None:
    """残っているターン通知をすべて削除する（26.2節）。"""

    battle_row = get_battle(state.battle_id) or {}

    for guild_id, channel in channels.items():
        message_id = battle_row.get(_side_key(state, guild_id, "turn_message_id"))
        if message_id:
            await _delete_message(channel, int(message_id))
        set_battle_messages(state.battle_id, guild_id=guild_id, turn_message_id=None)


# ==================================================
# バトル終了と報酬（26.2節）
# ==================================================
def _reward_key(state: BattleState, guild_id: int) -> str | None:
    """ギルドから報酬区分（win/lose/draw）を求める。"""

    if state.result == RESULT_DRAW:
        return "draw"

    if state.result == RESULT_GUILD_A:
        return "win" if guild_id == state.guild_a_id else "lose"

    if state.result == RESULT_GUILD_B:
        return "win" if guild_id == state.guild_b_id else "lose"

    return None


def _no_reward_reason(state: BattleState) -> str | None:
    """報酬を付与しない理由を返す。付与する場合は ``None``（26.2節）。"""

    master = load_master_data()

    if state.result == RESULT_ABORTED:
        return "運営による強制中止のため報酬はありません。"

    if state.end_reason == "surrender" and (
        state.current_round < master.battle.surrender_reward_from_round
    ):
        return (
            f"{master.battle.surrender_reward_from_round}巡目より前の降参のため、"
            "両ギルドとも報酬はありません。"
        )

    if _reward_key(state, state.guild_a_id) is None:
        return "勝敗が確定しなかったため報酬はありません。"

    return None


OUTCOME_LABELS = {"win": "勝利", "lose": "敗北", "draw": "引き分け"}


DEFAULT_BET_LABEL = "既定"

# 自由入力で受け付けるベット額の範囲
MIN_BET_COIN = 0
MAX_BET_COIN = 10_000_000


def default_bet_coin() -> int:
    """ベット額の初期値（マスターデータの値）を返す。"""

    return load_master_data().battle.bet.coin


def battle_bet_coin(battle_id: int) -> int:
    """そのバトルのベット額を返す。未保存なら既定値を使う。"""

    battle_row = get_battle(battle_id) or {}
    saved = battle_row.get("bet_coin")

    return int(saved) if saved is not None else default_bet_coin()


def parse_bet_coin(text: str) -> tuple[int | None, str | None]:
    """自由入力のベット額を読み取る。``(値, エラー文)`` を返す。"""

    cleaned = (text or "").strip().replace(",", "").replace("，", "")
    if not cleaned:
        return default_bet_coin(), None

    if not cleaned.isdigit():
        return None, "ベット額は半角数字で入力してください（例：20000）。"

    value = int(cleaned)
    if not MIN_BET_COIN <= value <= MAX_BET_COIN:
        return None, (
            f"ベット額は{MIN_BET_COIN:,}〜{MAX_BET_COIN:,} coinの範囲で"
            "入力してください。"
        )

    return value, None


def bet_notice(bet_coin: int | None = None) -> str:
    """ベット額の案内文を返す（バトル開始時と結果に出す）。"""

    bet = load_master_data().battle.bet
    amount = default_bet_coin() if bet_coin is None else bet_coin

    return (
        f"ギルドごとに {game_shared.format_coin(amount)} をベットします"
        "（出場者で均等に分担）。"
        f"負けた側のcoinは勝った側へ移ります"
        f"（勝利 {bet.win_xp} XP／敗北 {bet.lose_xp} XP）。"
    )


def bet_share_notice(bet_coin: int, member_count: int) -> str:
    """1人あたりの分担額を説明する文を返す。"""

    if member_count <= 0:
        return f"ベット額：{game_shared.format_coin(bet_coin)}"

    shares = split_bet_evenly(bet_coin, member_count)
    low, high = min(shares), max(shares)

    if low == high:
        each = game_shared.format_coin(low)
    else:
        each = f"{low:,}〜{high:,} coin"

    return (
        f"ギルド合計 {game_shared.format_coin(bet_coin)}"
        f"／出場者{member_count}人で分担（1人 {each}）"
    )


def build_settlement_embed(
    results: list[dict],
    *,
    reason: str | None = None,
    bet_coin: int | None = None,
) -> discord.Embed:
    """ベットの清算結果Embedを作る。"""

    if not results:
        return discord.Embed(
            title="バトル清算",
            description=reason or "coinの移動はありません。",
            color=config.COLOR_GREY,
        )

    moved = sum(item["coin"] for item in results if item["coin"] > 0)

    lines = [
        game_shared.item_line("移動したcoin", game_shared.format_coin(moved)),
        "",
    ]

    for outcome in ("win", "lose", "draw"):
        members = [item for item in results if item["outcome"] == outcome]
        if not members:
            continue

        lines.append(f"**{OUTCOME_LABELS[outcome]}**")
        for item in members:
            coin = int(item["coin"])
            sign = f"{coin:+,} coin" if coin else "±0 coin"
            xp = f"{item['xp']} XP" if item["xp"] else "XP上限"
            lines.append(f"<@{item['user_id']}>　{sign}／{xp}")

        lines.append("")

    lines.append(f"-# {bet_notice(bet_coin)}")

    return discord.Embed(
        title="バトル清算",
        description="\n".join(lines)[:4000],
        color=config.COLOR_GOLD,
    )


def battle_participants(state: BattleState) -> dict[str, list[dict]]:
    """出場者を勝ち・負け・引き分けへ分けて返す（同じプレイヤーは1回だけ）。

    並び順はメンバー選択順（使い魔の枠番号順）です。ベットの端数はこの順に
    切り上げ → 切り下げで割り振るため、順番を固定しておく必要があります。
    """

    seen: set[int] = set()
    groups: dict[str, list[dict]] = {"win": [], "lose": [], "draw": []}

    ordered = sorted(
        state.units.values(), key=lambda unit: (unit.slot, unit.battle_unit_id)
    )

    for unit in ordered:
        if unit.player_id in seen:
            continue

        key = _reward_key(state, unit.guild_id)
        if key is None:
            continue

        seen.add(unit.player_id)
        groups[key].append(
            {"user_id": unit.player_id, "guild_id": unit.guild_id}
        )

    return groups


def grant_rewards(state: BattleState) -> tuple[list[dict], str | None]:
    """ベットしたcoinを清算し、``(清算結果, 清算しなかった理由)`` を返す。"""

    reason = _no_reward_reason(state)
    if reason is not None:
        return [], reason

    master = load_master_data()
    bet = master.battle.bet
    groups = battle_participants(state)

    outcome = settle_battle_bet(
        state.battle_id,
        winners=groups["win"],
        losers=groups["lose"],
        drawers=groups["draw"],
        bet_coin=battle_bet_coin(state.battle_id),
        win_xp=bet.win_xp,
        lose_xp=bet.lose_xp,
        draw_xp=bet.draw_xp,
        reward_date=datetime.now(JST).strftime("%Y-%m-%d"),
        daily_limit=master.battle.reward_daily_limit_per_player,
    )

    results = outcome["results"]
    if not results:
        return [], "このバトルはすでに清算済みです。"

    return results, None


async def finish_battle_flow(
    bot: discord.Client,
    state: BattleState,
    *,
    channels: dict[int, discord.TextChannel] | None = None,
) -> None:
    """勝敗確定後の後片付けを順番に実行する（26.2節）。

    Discord操作に失敗しても勝敗確定を繰り返さないよう、DB更新は
    ``finish_battle`` の1回だけに任せ、以降は後片付けを続行します。
    """

    if channels is None:
        channels = battle_channels(bot, state)

    names = guild_names(state)

    # 1. 勝敗確定 → 2. 結果Embedを両チャンネルへ投稿
    result_embed = battle_embed.build_result_embed(state, guild_names=names)
    for channel in channels.values():
        await _safe_send(channel, embed=result_embed)

    # 3. DB上の試合状態を終了へ更新
    status = "aborted" if state.result == RESULT_ABORTED else "finished"
    finished = finish_battle(
        state.battle_id,
        result=state.result,
        end_reason=state.end_reason or "unknown",
        status=status,
    )

    if not finished["ok"] and finished["error"] != "already_finished":
        logger.warning(
            f"バトル終了処理に失敗しました: battle_id={state.battle_id} "
            f"error={finished['error']}"
        )

    # 4・5. 操作を停止し、残っているターン通知を削除
    await clear_turn_messages(state, channels)

    # 6～8. 出場者専用TCの権限を整理する（マスターと運営の権限は維持）
    discord_guild = bot.get_guild(config.GUILD_ID)
    battle_row = get_battle(state.battle_id) or {}

    if discord_guild is not None:
        for guild_id in (state.guild_a_id, state.guild_b_id):
            guild_row = get_guild(guild_id)
            if guild_row is None:
                continue

            member_ids = [row["user_id"] for row in get_guild_members(guild_id)]
            await game_shared.apply_guild_permissions(
                discord_guild, guild_row, member_ids=member_ids
            )

            # バトルログをカテゴリー権限へ同期し、後からの加入者も読めるようにする
            await game_shared.open_battle_log_channel(
                discord_guild,
                battle_row.get(_side_key(state, guild_id, "channel_id")),
            )

    # 9・10. 報酬付与と運営ログ
    if finished["ok"]:
        granted, reason = grant_rewards(state)
    else:
        granted, reason = [], None

    reward_embed = build_settlement_embed(
        granted, reason=reason, bet_coin=battle_bet_coin(state.battle_id)
    )
    for channel in channels.values():
        await _safe_send(channel, embed=reward_embed)

    await game_shared.game_admin_log(
        bot,
        action="ギルドバトル終了",
        target_guild_id=state.guild_a_id,
        target_battle_id=state.battle_id,
        success=finished["ok"],
        reason=state.end_reason,
        detail=f"result={state.result} / round={state.current_round}",
    )


# ==================================================
# 再起動後の復旧（29節）
# ==================================================
def recovery_problems(bot: discord.Client, state: BattleState) -> list[str]:
    """再開できるかを点検し、問題があればその内容を返す（29節）。"""

    problems: list[str] = []
    discord_guild = bot.get_guild(config.GUILD_ID)

    if discord_guild is None:
        return ["Discordサーバー情報を取得できませんでした。"]

    battle_row = get_battle(state.battle_id) or {}

    for guild_id in (state.guild_a_id, state.guild_b_id):
        guild_row = get_guild(guild_id)
        if guild_row is None or guild_row["status"] != "active":
            problems.append(f"ギルド{guild_id}が活動中ではありません。")
            continue

        channel_id = battle_row.get(_side_key(state, guild_id, "channel_id"))
        if not channel_id or discord_guild.get_channel(int(channel_id)) is None:
            problems.append(f"ギルド{guild_id}のバトル専用チャンネルがありません。")

    for unit in state.units.values():
        player_guild = get_player_guild(unit.player_id)
        if player_guild is None or int(player_guild["guild_id"]) != unit.guild_id:
            problems.append(f"<@{unit.player_id}> の所属が変わっています。")

    return problems


async def abort_battle(
    bot: discord.Client, battle_id: int, *, executor_id: int, reason: str
) -> dict:
    """運営がバトルを強制中止する（26.1節）。

    勝敗なしの ``aborted`` として保存し、通常の勝敗数・ランキング・報酬へは
    反映しません。中止理由は必須です。
    """

    reason = (reason or "").strip()
    if not reason:
        return {"ok": False, "error": "reason_required"}

    async with battle_lock(battle_id):
        state = load_battle_state(battle_id)
        if state is None:
            return {"ok": False, "error": "battle_not_found"}

        if state.status not in (
            BATTLE_STATUS_PREPARING,
            BATTLE_STATUS_IN_PROGRESS,
            BATTLE_STATUS_PAUSED,
        ):
            return {"ok": False, "error": "already_finished"}

        # 終了処理は「進行中からの遷移」を前提にしているため、状態を戻してから中止する
        set_battle_status(battle_id, BATTLE_STATUS_IN_PROGRESS)
        state.status = BATTLE_STATUS_IN_PROGRESS

        battle_engine.abort(state, reason)
        await finish_battle_flow(bot, state)

        await game_shared.game_admin_log(
            bot,
            action="ギルドバトル強制中止",
            executor_id=executor_id,
            target_battle_id=battle_id,
            target_guild_id=state.guild_a_id,
            success=True,
            reason=reason,
            detail=f"対戦: {state.guild_a_id} vs {state.guild_b_id}",
        )

        return {"ok": True, "error": None, "battle_id": battle_id}


async def resume_battle(
    bot: discord.Client, battle_id: int, *, include_paused: bool = False
) -> bool:
    """再起動後に進行中バトルを再開する（29節）。

    停止中の時間を消費させないため、``turn_started_at`` を現在時刻へ入れ直して
    から監視を再開します。整合性に問題があれば ``paused`` にして運営判断を待ちます。
    """

    async with battle_lock(battle_id):
        state = load_battle_state(battle_id)
        if state is None:
            return False

        allowed = [BATTLE_STATUS_PREPARING, BATTLE_STATUS_IN_PROGRESS]
        if include_paused:
            allowed.append(BATTLE_STATUS_PAUSED)

        if state.status not in allowed:
            return False

        if state.status == BATTLE_STATUS_PAUSED:
            # 運営の再開判断で呼ばれた場合だけ、進行中へ戻して再点検する
            state.status = BATTLE_STATUS_IN_PROGRESS
            set_battle_status(battle_id, BATTLE_STATUS_IN_PROGRESS)

        problems = recovery_problems(bot, state)
        if problems:
            set_battle_status(battle_id, "paused")
            set_battle_turn_timing(
                battle_id, turn_started_at=None, turn_deadline_at=None
            )
            await game_shared.game_admin_log(
                bot,
                action="ギルドバトル一時停止",
                target_battle_id=battle_id,
                target_guild_id=state.guild_a_id,
                success=False,
                reason="再起動後の整合性チェックに失敗",
                detail="\n".join(problems)[:1000],
            )
            return False

        if state.status == BATTLE_STATUS_PREPARING:
            # 開始処理の途中で停止していた場合は開始からやり直す
            return False

        # 決着済みなのに終了処理が済んでいない場合は、後片付けだけを実行する
        if _restore_finished_status(state):
            await finish_battle_flow(bot, state)
            return True

        expected = state.action_seq
        battle_engine.resume(state)
        state.action_seq = expected + 1

        if not save_state(state, expected_action_seq=expected):
            return False

        channels = battle_channels(bot, state)
        await post_action_logs(bot, state, channels)
        await refresh_status_embeds(state, channels)

        if battle_engine.is_finished(state):
            await finish_battle_flow(bot, state, channels=channels)
        else:
            await announce_turn(bot, state, channels=channels)

        return True


# ==================================================
# 見張りタスクの本体（17節・22節）
# ==================================================
async def process_timeouts(bot: discord.Client) -> None:
    """制限時間を過ぎたターンを自動処理する（17節）。"""

    master = load_master_data()
    now = _now_utc()

    for row in get_active_battles():
        if row["status"] != BATTLE_STATUS_IN_PROGRESS:
            continue

        deadline = _parse_iso(row.get("turn_deadline_at"))
        if deadline is None or now < deadline:
            continue

        try:
            await apply_action(
                bot,
                int(row["battle_id"]),
                AUTO_ACTION,
                elapsed_seconds=master.battle.turn_time_seconds,
                expected_seq=int(row["action_seq"]),
            )
        except BattleRuleError:
            logger.warning(
                f"自動行動を処理できませんでした: battle_id={row['battle_id']}"
            )
        except Exception:
            logger.exception(
                f"自動行動の処理で例外が発生しました: battle_id={row['battle_id']}"
            )
