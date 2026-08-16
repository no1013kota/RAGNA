"""行動ログEmbedと戦況Embedの組み立て（GAME_SPEC 23節・24節）。

``game/`` の中で唯一Discordへ依存するモジュールです。戦闘の計算は行わず、
``BattleState`` と行動ログを受け取って表示だけを作ります。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import discord

from .master_data import familiar_image_path, load_master_data
from .models import (
    RESULT_ABORTED,
    RESULT_DRAW,
    RESULT_GUILD_A,
    RESULT_GUILD_B,
    STATUS_LABELS,
    BattleEvent,
    BattleLogEntry,
    BattleState,
    BattleUnit,
    round_half_up,
)
from . import effects as effects_module


# config.py はBot Tokenを必須にするため、テストから使えるよう色はここで定義する。
COLOR_ATTACK = 0xBEDBFF
COLOR_CRITICAL = 0xFEE75C
COLOR_SKILL = 0x5C21FF
COLOR_PASSIVE = 0xBEFFD7
COLOR_DAMAGE = 0xFFB7B7
COLOR_INFO = 0x2B2D31
COLOR_RESULT = 0xFFFFF0

HP_BAR_LENGTH = 10
HP_BAR_FILLED = "█"
HP_BAR_EMPTY = "░"

SLOT_MARKS = ("①", "②", "③", "④", "⑤", "⑥", "⑦", "⑧", "⑨", "⑩")

# 1つの行動ログEmbedとしてまとめ始めるイベント
_GROUP_START_EVENTS = {
    BattleEvent.ATTACK.value,
    BattleEvent.SKILL.value,
    BattleEvent.PASSIVE.value,
    BattleEvent.SKIP.value,
    BattleEvent.TIMEOUT.value,
    BattleEvent.ROUND_START.value,
    BattleEvent.BATTLE_END.value,
}

# 表示しないイベント
_HIDDEN_EVENTS = {
    BattleEvent.BATTLE_START.value,
    BattleEvent.TURN_START.value,
    BattleEvent.TURN_END.value,
    BattleEvent.ROUND_END.value,
    BattleEvent.NEXT_TURN.value,
    BattleEvent.BEFORE_ACTION.value,
}


# ==================================================
# 共通表示
# ==================================================
def familiar_name(familiar_id: str) -> str:
    familiar = load_master_data().get_familiar(familiar_id)
    return familiar.name if familiar else familiar_id


def unit_name(state: BattleState, unit_id: int | None) -> str:
    unit = state.unit(unit_id)
    if unit is None:
        return "不明"
    return familiar_name(unit.familiar_id)


def hp_bar(current_hp: int, max_hp: int, length: int = HP_BAR_LENGTH) -> str:
    """HPバーを作る。HPが1以上なら最低1文字は残量を表示する（24節）。"""

    if max_hp <= 0 or current_hp <= 0:
        return HP_BAR_EMPTY * length

    filled = round_half_up(current_hp / max_hp * length)
    filled = max(1, min(length, filled))
    return HP_BAR_FILLED * filled + HP_BAR_EMPTY * (length - filled)


def format_remaining_time(seconds: int) -> str:
    seconds = max(0, int(seconds))
    minutes, remainder = divmod(seconds, 60)
    return f"{minutes}分{remainder:02d}秒"


def thumbnail_file(familiar_id: str) -> discord.File | None:
    """使い魔画像を添付用に開く。画像が無ければ ``None`` を返す。"""

    path: Path | None = familiar_image_path(familiar_id)
    if path is None:
        return None

    return discord.File(path, filename=f"familiar_{familiar_id}{path.suffix}")


# ==================================================
# 表示の共通処理
# Embedはfieldを使わず、本文へ「【項目】結果」の形で並べる
# ==================================================
def item_line(label: str, value: object) -> str:
    """「【項目】結果」の1行を作る。"""

    return f"【{label}】{value}"


# ==================================================
# 行動ログ
# ==================================================
@dataclass
class LogMessage:
    """1件分の行動ログEmbedと、サムネイルに使う使い魔ID。"""

    embed: discord.Embed
    familiar_id: str | None = None


@dataclass
class _Group:
    title: str
    color: int
    familiar_id: str | None = None
    lines: list[str] = field(default_factory=list)
    # 「【項目】結果」で本文の末尾へ並べる項目
    items: list[tuple[str, str]] = field(default_factory=list)


def _player_label(player_names: dict[int, str] | None, player_id: int | None) -> str:
    if player_id is None:
        return "—"
    if player_names and player_id in player_names:
        return player_names[player_id]
    return f"<@{player_id}>"


def _describe_log(
    state: BattleState, log: BattleLogEntry, player_names: dict[int, str] | None
) -> str | None:
    event = log.event_type
    detail = log.detail or {}

    if event == BattleEvent.DAMAGE.value:
        target = unit_name(state, log.target_unit_id)
        amount = log.amount or 0

        if detail.get("nullified"):
            return f"{target} へのダメージを無効化"

        lines = []
        if detail.get("critical"):
            lines.append("⚡ **CRITICAL**")
        lines.append(f"**{amount}** DAMAGE → {target}")
        lines.append(f"HP {detail.get('hp_before', '?')} → {detail.get('hp_after', '?')}")
        return "\n".join(lines)

    if event == BattleEvent.HEAL.value:
        target = unit_name(state, log.target_unit_id)
        return (
            f"💚 {target} のHPを **{log.amount or 0}** 回復\n"
            f"HP {detail.get('hp_before', '?')} → {detail.get('hp_after', '?')}"
        )

    if event == BattleEvent.STATUS_APPLY.value:
        target = unit_name(state, log.target_unit_id)
        status = STATUS_LABELS.get(detail.get("status", ""), detail.get("status", ""))
        if not detail.get("applied"):
            return f"{target}：{status}は無効化された"
        return f"{target}：{status}（残り{detail.get('remaining', 1)}ターン）"

    if event == BattleEvent.EFFECT.value:
        target = unit_name(state, log.target_unit_id)
        text = detail.get("text") or ""
        if log.target_unit_id is None:
            return text
        return f"{target}：{text}"

    if event == BattleEvent.DEFEAT_CHECK.value:
        return f"💀 {unit_name(state, log.target_unit_id)} は戦闘不能"

    if event == BattleEvent.BEFORE_DEFEAT.value:
        return f"🛡 {unit_name(state, log.target_unit_id)} は{detail.get('text', '耐えた')}"

    if event == BattleEvent.REVIVE.value:
        return f"✨ {unit_name(state, log.target_unit_id)} が{detail.get('text', '復活')}"

    if event == BattleEvent.POISON.value:
        return f"☠ {unit_name(state, log.target_unit_id)} は毒を受けている"

    return None


def build_action_log_messages(
    state: BattleState,
    logs: list[BattleLogEntry],
    *,
    player_names: dict[int, str] | None = None,
) -> list[LogMessage]:
    """行動ログを、投稿単位のEmbedへまとめる（23節）。"""

    messages: list[LogMessage] = []
    group: _Group | None = None

    def flush() -> None:
        nonlocal group
        if group is None:
            return

        body = list(group.lines)
        if group.items:
            if body:
                body.append("")
            body.extend(item_line(name, value or "—") for name, value in group.items)

        embed = discord.Embed(
            title=group.title,
            description="\n".join(body) if body else None,
            color=group.color,
        )

        messages.append(LogMessage(embed=embed, familiar_id=group.familiar_id))
        group = None

    for log in logs:
        event = log.event_type
        detail = log.detail or {}

        if event in _HIDDEN_EVENTS:
            continue

        # 毒ダメージは単独のログとして扱う
        is_poison_damage = (
            event == BattleEvent.DAMAGE.value and detail.get("attack_type") == "poison"
        )

        if event in _GROUP_START_EVENTS or is_poison_damage:
            flush()

        actor = state.unit(log.actor_unit_id)

        if event == BattleEvent.ROUND_START.value:
            group = _Group(
                title=f"── ラウンド {log.round} ──", color=COLOR_INFO
            )
            flush()
            continue

        if event == BattleEvent.ATTACK.value:
            group = _Group(
                title=f"⚔ {unit_name(state, log.actor_unit_id)}の攻撃",
                color=COLOR_ATTACK,
                familiar_id=actor.familiar_id if actor else None,
            )
            group.items.append(
                ("使用者", _player_label(player_names, actor.player_id if actor else None))
            )
            group.items.append(("対象", unit_name(state, log.target_unit_id)))
            continue

        if event == BattleEvent.SKILL.value:
            group = _Group(
                title=f"✦ SKILL「{detail.get('skill_name', '')}」",
                color=COLOR_SKILL,
                familiar_id=actor.familiar_id if actor else None,
            )
            group.lines.append(detail.get("description", ""))
            group.items.append(
                ("使用者", _player_label(player_names, actor.player_id if actor else None))
            )
            targets = [
                unit_name(state, unit_id)
                for unit_id in detail.get("target_unit_ids") or ()
            ]
            if targets:
                group.items.append(("対象", "・".join(targets)))
            continue

        if event == BattleEvent.PASSIVE.value:
            group = _Group(
                title="✦ PASSIVE SKILL",
                color=COLOR_PASSIVE,
                familiar_id=actor.familiar_id if actor else None,
            )
            group.lines.append(
                f"{unit_name(state, log.actor_unit_id)}「{detail.get('skill_name', '')}」発動"
            )
            continue

        if event == BattleEvent.SKIP.value:
            status = STATUS_LABELS.get(detail.get("status", ""), "行動不能")
            group = _Group(
                title=f"⏭ {unit_name(state, log.actor_unit_id)}は行動できない",
                color=COLOR_INFO,
                familiar_id=actor.familiar_id if actor else None,
            )
            group.lines.append(f"{status}のため行動をスキップしました。")
            flush()
            continue

        if event == BattleEvent.TIMEOUT.value:
            group = _Group(
                title="⏱ 時間切れ",
                color=COLOR_INFO,
                familiar_id=actor.familiar_id if actor else None,
            )
            group.lines.append(detail.get("text", "自動攻撃を実行しました。"))
            continue

        if event == BattleEvent.BATTLE_END.value:
            continue

        if is_poison_damage:
            group = _Group(title="☠ 毒", color=COLOR_DAMAGE)

        text = _describe_log(state, log, player_names)
        if text is None:
            continue

        if group is None:
            group = _Group(title="戦況の変化", color=COLOR_INFO)

        if log.event_type == BattleEvent.DAMAGE.value and detail.get("critical"):
            group.color = COLOR_CRITICAL

        group.lines.append(text)

    flush()
    return messages


# ==================================================
# 戦況Embed
# ==================================================
def _unit_line(state: BattleState, unit: BattleUnit, index: int) -> str:
    mark = SLOT_MARKS[index] if index < len(SLOT_MARKS) else f"{index + 1}."
    name = familiar_name(unit.familiar_id)

    if not unit.alive:
        head = f"{mark} ~~{name}~~ 💀 戦闘不能"
        bar = HP_BAR_EMPTY * HP_BAR_LENGTH
        return f"{head}\n`{bar}` 0/{unit.max_hp}"

    bar = hp_bar(unit.current_hp, unit.max_hp)
    head = f"{mark} **{name}** Lv.{unit.level}"
    stats = f"`{bar}` {unit.current_hp}/{unit.max_hp} ATK{unit.current_atk} SPD{unit.speed}"

    summary = effects_module.buff_summary(state, unit)
    marks: list[str] = []
    marks.extend(f"🔺{text}" for text in summary["buffs"])
    marks.extend(f"🔻{text}" for text in summary["debuffs"])
    marks.extend(f"☠{text}" for text in summary["statuses"])
    marks.extend(f"◆{text}" for text in summary["others"])

    lines = [head, stats]
    if marks:
        lines.append("　" + " ".join(marks))

    skill_marks = _skill_usage(unit)
    if skill_marks:
        lines.append("　" + skill_marks)

    return "\n".join(lines)


def _skill_usage(unit: BattleUnit) -> str:
    master = load_master_data()
    parts = []

    for skill in master.active_skills_of(unit.familiar_id):
        used = int(unit.active_skill_uses.get(skill.skill_id, 0))
        limit = skill.max_uses_per_battle
        if limit is None:
            parts.append(f"ACTIVE {skill.name}：{used}回使用")
        else:
            parts.append(
                f"ACTIVE {skill.name}：{'使用済' if used >= limit else f'残{limit - used}'}"
            )

    return " / ".join(parts)


def build_status_embed(
    state: BattleState,
    *,
    guild_names: dict[int, str],
    highlight_guild_id: int | None = None,
    turn_remaining_seconds: int | None = None,
) -> discord.Embed:
    """その時点の戦況をまとめたEmbedを作る（17節・24節）。"""

    header = [item_line("ラウンド", state.current_round)]

    if state.current_unit_id:
        header.append(item_line("行動中", unit_name(state, state.current_unit_id)))

        if turn_remaining_seconds is not None:
            header.append(
                item_line(
                    "自動攻撃まで", format_remaining_time(turn_remaining_seconds)
                )
            )

    sections: list[str] = []

    for guild_id in (state.guild_a_id, state.guild_b_id):
        name = guild_names.get(guild_id, f"ギルド{guild_id}")
        remaining = format_remaining_time(state.remaining_seconds.get(guild_id, 0))
        prefix = "▶ " if guild_id == highlight_guild_id else ""

        blocks = [
            _unit_line(state, unit, index)
            for index, unit in enumerate(state.guild_units(guild_id))
        ]

        body = "\n".join(blocks) if blocks else "—"
        if len(body) > 1500:
            body = body[:1490] + "\n…"

        sections.append(
            item_line(f"{prefix}{name}", f"残り持ち時間 {remaining}") + "\n" + body
        )

    return discord.Embed(
        title="【戦況】",
        description="\n\n".join(["\n".join(header), *sections])[:4000],
        color=COLOR_INFO,
    )


# ==================================================
# ターン通知・結果
# ==================================================
def build_turn_embed(
    state: BattleState,
    unit: BattleUnit,
    *,
    turn_seconds: int,
) -> discord.Embed:
    """バトル専用チャンネルへ出すターン通知（16節・17節）。

    ``turn_seconds`` は自動攻撃までの残り時間です（17節）。
    """

    return discord.Embed(
        title=f"{familiar_name(unit.familiar_id)}の行動順です",
        description="\n".join(
            [
                item_line("ラウンド", state.current_round),
                item_line("自動攻撃まで", format_remaining_time(turn_seconds)),
                item_line(
                    "残り持ち時間",
                    format_remaining_time(
                        state.remaining_seconds.get(unit.guild_id, 0)
                    ),
                ),
                item_line("現在HP", f"{unit.current_hp}/{unit.max_hp}"),
                item_line("現在ATK", unit.current_atk),
                item_line("SPD", unit.speed),
            ]
        ),
        color=COLOR_ATTACK,
    )


def build_result_embed(
    state: BattleState,
    *,
    guild_names: dict[int, str],
    reward_text: str | None = None,
) -> discord.Embed:
    """勝敗の結果Embed（26節）。"""

    guild_a = guild_names.get(state.guild_a_id, f"ギルド{state.guild_a_id}")
    guild_b = guild_names.get(state.guild_b_id, f"ギルド{state.guild_b_id}")

    if state.result == RESULT_GUILD_A:
        title = "🏆 GUILD BATTLE 終了"
        description = f"**{guild_a}** の勝利"
    elif state.result == RESULT_GUILD_B:
        title = "🏆 GUILD BATTLE 終了"
        description = f"**{guild_b}** の勝利"
    elif state.result == RESULT_DRAW:
        title = "🤝 GUILD BATTLE 終了"
        description = "引き分け"
    elif state.result == RESULT_ABORTED:
        title = "⛔ GUILD BATTLE 中止"
        description = "運営により中止されました。勝敗は記録されません。"
    else:
        title = "GUILD BATTLE 終了"
        description = "—"

    reasons = {
        "wipe": "相手ギルドの全滅",
        "double_wipe": "同時全滅",
        "time_over": "持ち時間切れ",
        "surrender": "降参",
        "engine_stalled": "進行不能のため引き分け",
    }

    lines = [
        description,
        "",
        item_line("対戦", f"{guild_a} vs {guild_b}"),
        item_line(
            "決着理由", reasons.get(state.end_reason or "", state.end_reason or "—")
        ),
        item_line("ラウンド数", state.current_round),
    ]

    if reward_text:
        lines.append(item_line("報酬", reward_text))

    return discord.Embed(
        title=title,
        description="\n".join(lines)[:4000],
        color=COLOR_RESULT,
    )
