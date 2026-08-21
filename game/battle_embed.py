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

# 自ギルドと相手ギルドを、Embed左端の色帯で見分けるための色。
# バトル専用チャンネルはギルドごとに分かれているため、同じ出来事でも
# 見ているギルドに合わせて色を変えられます（17節）。
COLOR_ALLY = 0xBEDBFF
COLOR_ENEMY = 0xFFB7B7

# 行動順や編成で「どちらのギルドか」を示す記号
MARK_ALLY = "🔵"
MARK_ENEMY = "🔴"
MARK_GUILD_A = "🟦"
MARK_GUILD_B = "🟥"

SIDE_LEGEND = f"{MARK_ALLY}自ギルド {MARK_ENEMY}相手ギルド"

HP_BAR_LENGTH = 10
HP_BAR_FILLED = "█"
HP_BAR_EMPTY = "░"

SLOT_MARKS = ("①", "②", "③", "④", "⑤", "⑥", "⑦", "⑧", "⑨", "⑩")

# 1つの行動ログEmbedとしてまとめ始めるイベント。
# パッシブは含めない。攻撃やスキルの途中で発動するため、まとめ始めてしまうと
# そのあとのダメージ表示がパッシブ側のEmbedへ入ってしまう。
_GROUP_START_EVENTS = {
    BattleEvent.ATTACK.value,
    BattleEvent.SKILL.value,
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


def unit_label(state: BattleState, unit: BattleUnit) -> str:
    """「ロキ Lv.3」のような表示名を返す。

    バトル中はランク記号を付けません。強さはATKやHPで分かるため、
    行動順や戦況が記号で埋まって読みにくくなるのを避けます。
    """

    return f"{familiar_name(unit.familiar_id)} Lv.{unit.level}"


def survivor_text(state: BattleState, guild_id: int) -> str:
    """「3/5体」の生存数を返す。"""

    units = state.guild_units(guild_id)
    alive = sum(1 for unit in units if unit.alive)

    return f"{alive}/{len(units)}体"


def side_mark(
    state: BattleState, guild_id: int, viewer_guild_id: int | None = None
) -> str:
    """使い魔がどちらのギルドのものかを示す記号を返す。

    見ているギルドが分かる場合は「自分＝🔵／相手＝🔴」で示します。
    バトル専用チャンネルはギルドごとに分かれているため、同じ出来事でも
    見る側に合わせた表示にできます。分からない場合はA側・B側で示します。
    """

    if viewer_guild_id is None:
        return MARK_GUILD_A if guild_id == state.guild_a_id else MARK_GUILD_B

    return MARK_ALLY if guild_id == viewer_guild_id else MARK_ENEMY


def side_color(
    state: BattleState, guild_id: int | None, viewer_guild_id: int | None
) -> int | None:
    """自ギルド・相手ギルドで色分けしたEmbedの色を返す。

    どちらのギルドの出来事か分からない場合は ``None`` を返し、
    呼び出し側の色をそのまま使わせます。
    """

    if guild_id is None or viewer_guild_id is None:
        return None

    return COLOR_ALLY if guild_id == viewer_guild_id else COLOR_ENEMY


def turn_queue_text(
    state: BattleState,
    *,
    limit: int = 6,
    viewer_guild_id: int | None = None,
) -> str:
    """このラウンドの残り行動順を「▶いま → 次 → …」の形で返す（1節）。

    戦闘不能の使い魔は行動しないため飛ばします。次に誰が動くかが分かると、
    スキルを使うタイミングを判断できます。長くなりすぎないよう、名前は
    所属を示す記号＋ランク記号＋使い魔名だけにします。
    """

    if not state.turn_order:
        return "—"

    parts: list[str] = []

    for index in range(state.turn_index, len(state.turn_order)):
        unit = state.unit(state.turn_order[index])
        if unit is None or not unit.alive:
            continue

        mark = side_mark(state, unit.guild_id, viewer_guild_id)
        label = f"{mark}{familiar_name(unit.familiar_id)}"
        if state.current_unit_id == unit.battle_unit_id:
            label = f"▶**{label}**"

        parts.append(label)

        if len(parts) >= limit:
            parts.append("…")
            break

    if not parts:
        return "このラウンドの行動は終わりです"

    return " → ".join(parts)


def turn_position_text(state: BattleState, unit: BattleUnit) -> str:
    """「2/6番目」のように、このラウンドでの行動順の位置を返す。"""

    order = [
        unit_id
        for unit_id in state.turn_order
        if (found := state.unit(unit_id)) is not None and found.alive
    ]

    if unit.battle_unit_id not in order:
        return "—"

    return f"{order.index(unit.battle_unit_id) + 1}/{len(order)}番目"


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


def stat_with_delta(current: int, base: int) -> str:
    """現在値と、基礎値からの増減を「15（+2）」の形で返す。

    増減が無ければ数値だけを返します。バフ・デバフがどれだけ乗っているかを
    ひと目で分かるようにするためです。
    """

    delta = current - base
    if delta == 0:
        return str(current)

    return f"{current}（{delta:+d}）"


def atk_text(unit: BattleUnit) -> str:
    """現在ATKを「15（+2）」の形で返す。"""

    return stat_with_delta(unit.current_atk, unit.base_atk)


def speed_text(unit: BattleUnit) -> str:
    """現在SPDを「96（+12）」の形で返す。"""

    return stat_with_delta(unit.speed, effects_module.base_speed_of(unit))


def stat_line(state: BattleState, unit: BattleUnit) -> str:
    """HP・ATK・SPDをまとめた1行を作る（バトルログ用）。"""

    if not unit.alive:
        return f"HP 0/{unit.max_hp}（戦闘不能）"

    return (
        f"HP {unit.current_hp}/{unit.max_hp}"
        f"／ATK {atk_text(unit)}／SPD {speed_text(unit)}"
    )


def unit_status_lines(state: BattleState, unit: BattleUnit) -> list[str]:
    """使い魔1体の状態を、名前・ステータス・効果の3行以内でまとめる。"""

    lines = [stat_line(state, unit)]

    summary = effects_module.buff_summary(state, unit)
    marks: list[str] = []
    marks.extend(f"🔺{text}" for text in summary["buffs"])
    marks.extend(f"🔻{text}" for text in summary["debuffs"])
    marks.extend(f"☠{text}" for text in summary["statuses"])
    marks.extend(f"◆{text}" for text in summary["others"])

    if marks:
        lines.append(" ".join(marks))

    return lines


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
    # 行動した使い魔の所属ギルド。自ギルド・相手ギルドの色分けに使う
    guild_id: int | None = None
    lines: list[str] = field(default_factory=list)
    # 「【項目】結果」で本文の末尾へ並べる項目
    items: list[tuple[str, str]] = field(default_factory=list)
    # このグループへ入ったパッシブの件数（表題の出し分けに使う）
    passive_count: int = 0
    # パッシブだけで始まったグループか
    passive_only: bool = False
    # 両ギルドの使い魔がまとまったグループか（色をどちらかへ寄せられない）
    mixed_guilds: bool = False
    # このグループの中で戦闘不能になった使い魔。グループを出したあとに
    # 1体ずつ専用Embedを続けるため、ここへ溜めておく
    defeats: list[int] = field(default_factory=list)


def _revived_defeat_positions(logs: list[BattleLogEntry]) -> set[int]:
    """復活で取り消された戦闘不能ログの位置を返す。

    最後の状態だけを見ると「倒れて復活し、また倒れた」を1回の戦闘不能と
    区別できず、取り消されたはずの表示まで出てしまいます。ログの並びで
    「戦闘不能のあとに同じ使い魔の復活が来たか」を見て判定します。
    """

    pending: dict[int, int] = {}
    revived: set[int] = set()

    for position, log in enumerate(logs):
        if log.target_unit_id is None:
            continue

        if log.event_type == BattleEvent.DEFEAT_CHECK.value:
            pending[log.target_unit_id] = position
        elif log.event_type == BattleEvent.REVIVE.value:
            defeat_position = pending.pop(log.target_unit_id, None)
            if defeat_position is not None:
                revived.add(defeat_position)

    return revived


def _build_defeat_message(
    state: BattleState, unit_id: int | None, viewer_guild_id: int | None
) -> LogMessage | None:
    """戦闘不能になった使い魔の専用Embedを作る（23節）。

    誰が倒れたのかを取り違えないよう、サムネイルにはその使い魔の画像を使います
    （行動ログのサムネイルは行動した側の画像なので、倒れた側は分かりません）。
    """

    unit = state.unit(unit_id)
    if unit is None:
        return None

    lines = [
        f"{side_mark(state, unit.guild_id, viewer_guild_id)}"
        f"**{unit_label(state, unit)}** は倒れた",
        f"`{HP_BAR_EMPTY * HP_BAR_LENGTH}` 0/{unit.max_hp}",
        item_line("残り", survivor_text(state, unit.guild_id)),
    ]

    embed = discord.Embed(
        title=f"💀 {familiar_name(unit.familiar_id)} 戦闘不能",
        description="\n".join(lines),
        color=side_color(state, unit.guild_id, viewer_guild_id) or COLOR_DAMAGE,
    )

    return LogMessage(embed=embed, familiar_id=unit.familiar_id)


def _skill_description(skill_id: str | None) -> str:
    """スキルの説明文を返す。何をするスキルかログだけで分かるようにする。"""

    if not skill_id:
        return ""

    skill = load_master_data().get_skill(skill_id)
    return skill.description if skill is not None else ""


def _player_label(player_names: dict[int, str] | None, player_id: int | None) -> str:
    if player_id is None:
        return "—"
    if player_names and player_id in player_names:
        return player_names[player_id]
    return f"<@{player_id}>"


def _hp_change_text(before: object, after: object) -> str:
    """「HP 51 → **38**」とHPの増減だけを返す。"""

    return (
        f"HP {before if before is not None else '?'}"
        f" → **{after if after is not None else '?'}**"
    )


def _hp_bar_line(unit: BattleUnit | None, after: object) -> str | None:
    """変動後のHPバーの行を返す。使い魔が分からなければ ``None``。"""

    if unit is None:
        return None

    try:
        current = int(after)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        current = unit.current_hp

    return f"`{hp_bar(current, unit.max_hp)}` {current}/{unit.max_hp}"


def _hp_change_line(
    unit: BattleUnit | None, before: object, after: object
) -> str:
    """「HP 51 → 38」とHPバーを1行で返す。"""

    text = _hp_change_text(before, after)

    bar = _hp_bar_line(unit, after)
    return text if bar is None else f"{text}\n{bar}"


def _describe_log(
    state: BattleState, log: BattleLogEntry, player_names: dict[int, str] | None
) -> str | None:
    event = log.event_type
    detail = log.detail or {}

    if event == BattleEvent.DAMAGE.value:
        unit = state.unit(log.target_unit_id)
        target = unit_name(state, log.target_unit_id)
        amount = log.amount or 0

        if detail.get("nullified"):
            return f"🛡 {target} へのダメージを無効化"

        lines = []
        if detail.get("critical"):
            lines.append("⚡ **CRITICAL**")

        # 括弧の中はHPの変動。何によるダメージかは、この行動ログの
        # 表題（⚔攻撃／✦SKILL／☠毒）が示すため繰り返しません。
        hp_after = detail.get("hp_after")
        lines.append(
            f"💥 **{amount}** ダメージ → {target}"
            f"（{_hp_change_text(detail.get('hp_before'), hp_after)}）"
        )

        bar = _hp_bar_line(unit, hp_after)
        if bar is not None:
            lines.append(bar)

        return "\n".join(lines)

    if event == BattleEvent.HEAL.value:
        unit = state.unit(log.target_unit_id)
        target = unit_name(state, log.target_unit_id)
        return "\n".join(
            [
                f"💚 {target} のHPを **{log.amount or 0}** 回復",
                _hp_change_line(
                    unit, detail.get("hp_before"), detail.get("hp_after")
                ),
            ]
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

        lines = [f"{target}：{text}"]

        # ATK・SPDが実際に動いた場合だけ、その前後を並べる
        for label, before_key, after_key in (
            ("ATK", "atk_before", "atk_after"),
            ("SPD", "speed_before", "speed_after"),
        ):
            before = detail.get(before_key)
            after = detail.get(after_key)
            if before is None or after is None or before == after:
                continue

            lines.append(f"　{label} {before} → **{after}**")

        return "\n".join(lines)

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
    guild_names: dict[int, str] | None = None,
    bet_coin: int | None = None,
    viewer_guild_id: int | None = None,
) -> list[LogMessage]:
    """行動ログを、投稿単位のEmbedへまとめる（23節）。

    攻撃やスキルの途中で発動したパッシブは、その流れと同じEmbedへ入れます。
    別のEmbedへ分けると、ダメージ表示がパッシブ側へ移ってしまうためです。

    ``viewer_guild_id`` を渡すと、そのギルドのバトル専用チャンネル向けに
    自ギルド・相手ギルドで色分けした表示になります（17節）。
    """

    messages: list[LogMessage] = []
    group: _Group | None = None

    # 復活で取り消された戦闘不能は、専用Embedを出さない
    revived_positions = _revived_defeat_positions(logs)

    def flush() -> None:
        nonlocal group
        if group is None:
            return

        # 複数のパッシブがまとまった場合は、表題を1つのスキル名に絞らない
        if group.passive_only and group.passive_count > 1:
            group.title = f"✦ PASSIVE SKILL（{group.passive_count}件）"
            group.items.clear()

            # 両ギルドのパッシブが1件のEmbedへ入っている場合、どちらかの色で
            # 塗ると自ギルドのパッシブが相手のものに見えてしまう。中立色に戻す。
            if group.mixed_guilds:
                group.guild_id = None
                group.familiar_id = None

        body = list(group.lines)
        if group.items:
            if body:
                body.append("")
            body.extend(item_line(name, value or "—") for name, value in group.items)

        embed = discord.Embed(
            title=group.title,
            description="\n".join(body) if body else None,
            color=side_color(state, group.guild_id, viewer_guild_id) or group.color,
        )

        messages.append(LogMessage(embed=embed, familiar_id=group.familiar_id))

        # 戦闘不能は、その流れを出したあとに1体ずつ専用Embedで続ける。
        # 途中でグループを切り替えると、同じ攻撃の残りのダメージ表示が
        # そちらへ移ってしまうため、ここまで溜めてから出す。
        for unit_id in group.defeats:
            defeat = _build_defeat_message(state, unit_id, viewer_guild_id)
            if defeat is not None:
                messages.append(defeat)

        group = None

    for position, log in enumerate(logs):
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
            group.items.append(
                (
                    "行動順",
                    turn_queue_text(state, limit=10, viewer_guild_id=viewer_guild_id),
                )
            )
            flush()

            # 2巡目以降は、ラウンドごとに戦況を1件残す。あとから見返したときに、
            # そのラウンドが始まった時点の状況が分かるようにする（24節）。
            # 1巡目はバトル開始の編成表と最初の戦況Embedがあるため出しません。
            if int(log.round or 0) > 1:
                messages.append(
                    LogMessage(
                        embed=build_status_embed(
                            state,
                            guild_names=guild_names or {},
                            bet_coin=bet_coin,
                            viewer_guild_id=viewer_guild_id,
                        )
                    )
                )
            continue

        if event == BattleEvent.ATTACK.value:
            # 使用者と対象のステータスは並べない。誰が誰を攻撃したかは表題と
            # ダメージ行が示し、結果のHPはダメージ行の括弧内で分かるため。
            group = _Group(
                title=f"⚔ {unit_name(state, log.actor_unit_id)}の攻撃",
                color=COLOR_ATTACK,
                familiar_id=actor.familiar_id if actor else None,
                guild_id=actor.guild_id if actor else None,
            )
            continue

        if event == BattleEvent.SKILL.value:
            group = _Group(
                title=f"✦ SKILL「{detail.get('skill_name', '')}」",
                color=COLOR_SKILL,
                familiar_id=actor.familiar_id if actor else None,
                guild_id=actor.guild_id if actor else None,
            )
            group.lines.append(detail.get("description", ""))
            if actor is not None:
                owner = _player_label(player_names, actor.player_id)
                group.items.append(
                    (
                        f"{familiar_name(actor.familiar_id)}（{owner}）",
                        " ／ ".join(unit_status_lines(state, actor)),
                    )
                )

            for unit_id in detail.get("target_unit_ids") or ():
                target = state.unit(unit_id)
                if target is None:
                    continue
                group.items.append(
                    (
                        f"{familiar_name(target.familiar_id)}（対象）",
                        " ／ ".join(unit_status_lines(state, target)),
                    )
                )
            continue

        if event == BattleEvent.PASSIVE.value:
            skill_name = str(detail.get("skill_name", ""))
            owner = unit_name(state, log.actor_unit_id)
            description = _skill_description(log.skill_id)

            if group is None:
                # 単独で発動したパッシブ（バトル開始時など）
                group = _Group(
                    title=f"✦ PASSIVE「{skill_name}」",
                    color=COLOR_PASSIVE,
                    familiar_id=actor.familiar_id if actor else None,
                    guild_id=actor.guild_id if actor else None,
                    passive_only=True,
                )
                group.lines.append(f"**{owner}** の「{skill_name}」が発動")
                if actor is not None:
                    group.items.append(
                        (
                            f"{familiar_name(actor.familiar_id)}"
                            f"（{_player_label(player_names, actor.player_id)}）",
                            " ／ ".join(unit_status_lines(state, actor)),
                        )
                    )
            else:
                # 攻撃やスキルの途中で発動したパッシブは、その流れの中へ差し込む
                group.lines.append(f"◇ **PASSIVE** {owner}「{skill_name}」")

                # バトル開始時パッシブのように、両ギルド分がまとまることがある
                if (
                    group.passive_only
                    and actor is not None
                    and group.guild_id is not None
                    and actor.guild_id != group.guild_id
                ):
                    group.mixed_guilds = True

            if description:
                group.lines.append(f"-# {description}")

            group.passive_count += 1
            continue

        if event == BattleEvent.SKIP.value:
            status = STATUS_LABELS.get(detail.get("status", ""), "行動不能")
            group = _Group(
                title=f"⏭ {unit_name(state, log.actor_unit_id)}は行動できない",
                color=COLOR_INFO,
                familiar_id=actor.familiar_id if actor else None,
                guild_id=actor.guild_id if actor else None,
            )
            group.lines.append(f"{status}のため行動をスキップしました。")
            flush()
            continue

        if event == BattleEvent.TIMEOUT.value:
            group = _Group(
                title="⏱ 時間切れ",
                color=COLOR_INFO,
                familiar_id=actor.familiar_id if actor else None,
                guild_id=actor.guild_id if actor else None,
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

        # 専用Embedはこの流れを出し切ってから続ける（flush の中で作る）
        if (
            event == BattleEvent.DEFEAT_CHECK.value
            and log.target_unit_id is not None
            and position not in revived_positions
            and log.target_unit_id not in group.defeats
        ):
            group.defeats.append(log.target_unit_id)

    flush()
    return messages


# ==================================================
# 戦況Embed
# ==================================================
def _unit_line(state: BattleState, unit: BattleUnit) -> str:
    """戦況Embedの使い魔1体分。いま生き残りとHPを見るための表示に絞る。

    枠番号・COST・スキルの残り回数は編成を決めるための情報なので、
    戦況では出しません（編成確認とターン通知で確認できます）。
    """

    name = familiar_name(unit.familiar_id)

    if not unit.alive:
        bar = HP_BAR_EMPTY * HP_BAR_LENGTH
        return f"~~{name}~~ 💀 戦闘不能\n`{bar}` 0/{unit.max_hp}"

    bar = hp_bar(unit.current_hp, unit.max_hp)
    stats = (
        f"`{bar}` {unit.current_hp}/{unit.max_hp}"
        f"　ATK {atk_text(unit)}　SPD {speed_text(unit)}"
    )

    summary = effects_module.buff_summary(state, unit)
    marks: list[str] = []
    marks.extend(f"🔺{text}" for text in summary["buffs"])
    marks.extend(f"🔻{text}" for text in summary["debuffs"])
    marks.extend(f"☠{text}" for text in summary["statuses"])
    marks.extend(f"◆{text}" for text in summary["others"])

    lines = [f"**{name}** Lv.{unit.level}", stats]
    if marks:
        lines.append("　" + " ".join(marks))

    return "\n".join(lines)


def _skill_usage(unit: BattleUnit) -> str:
    master = load_master_data()
    parts = []

    for skill in master.active_skills_of(unit.familiar_id):
        used = int(unit.active_skill_uses.get(skill.skill_id, 0))
        limit = skill.max_uses_per_battle

        if limit is None:
            parts.append(f"ACTIVE {skill.name}（回数制限なし）")
        elif used >= limit:
            parts.append(f"ACTIVE {skill.name}：使用済")
        else:
            parts.append(f"ACTIVE {skill.name}：あと{limit - used}回")

    return " / ".join(parts)


def _active_uses_text(unit: BattleUnit, skill) -> str:
    """アクティブスキルの残り使用回数を短く返す。"""

    limit = skill.max_uses_per_battle
    if limit is None:
        return "回数制限なし"

    used = int(unit.active_skill_uses.get(skill.skill_id, 0))
    return "使用済" if used >= limit else f"あと{limit - used}回"


def skill_lines(unit: BattleUnit) -> list[str]:
    """行動中の使い魔が持つアクティブ・パッシブスキルを並べる（19節）。

    自分の順番のときに「何を使えるのか」「何が自動で発動するのか」を
    その場で確認できるようにします。
    """

    skills = load_master_data().skills_of(unit.familiar_id)
    if not skills:
        return [item_line("スキル", "なし")]

    lines = ["**スキル**"]

    for skill in skills:
        if skill.is_active:
            lines.append(f"ACTIVE「{skill.name}」（{_active_uses_text(unit, skill)}）")
        else:
            lines.append(f"PASSIVE「{skill.name}」")

        if skill.description:
            lines.append(f"-# {skill.description}")

    return lines


def build_lineup_embed(
    state: BattleState,
    *,
    guild_id: int,
    guild_names: dict[int, str],
    player_names: dict[int, str] | None = None,
    bet_notice: str | None = None,
) -> discord.Embed:
    """バトル開始時の編成表を、そのギルド向けに作る（BATTLE_RULES.md 11〜13節）。

    自分のギルドの使い魔はスキル名と効果内容の両方を、相手ギルドはスキル名だけを
    表示します。相手の手札を全部見せてしまうと読み合いが無くなるためです。
    """

    master = load_master_data()
    enemy_guild_id = state.enemy_guild_id(guild_id)

    lines: list[str] = []

    if bet_notice:
        lines.extend([bet_notice, ""])

    for side, target_guild_id, show_effects in (
        (f"{MARK_ALLY}自ギルド", guild_id, True),
        (f"{MARK_ENEMY}相手ギルド", enemy_guild_id, False),
    ):
        name = guild_names.get(target_guild_id, f"ギルド{target_guild_id}")
        units = sorted(
            state.guild_units(target_guild_id),
            key=lambda unit: (-unit.speed, unit.battle_unit_id),
        )

        total_cost = sum(unit.cost for unit in units)
        cap = master.battle.max_total_cost
        cost_text = f"{total_cost}" if cap <= 0 else f"{total_cost}/{cap}"

        lines.append(f"**{side}：{name}**")
        lines.append(
            f"　{item_line('合計COST', cost_text)}"
            f"　{item_line('出場', f'{len(units)}体')}"
        )

        for index, unit in enumerate(units):
            mark = SLOT_MARKS[index] if index < len(SLOT_MARKS) else f"{index + 1}."
            owner = _player_label(player_names, unit.player_id)

            lines.append(
                f"{mark} **{unit_label(state, unit)}**"
                f"　COST {unit.cost}　{owner}"
            )
            lines.append(
                f"　`{hp_bar(unit.current_hp, unit.max_hp)}` "
                f"{unit.current_hp}/{unit.max_hp}"
                f"　ATK {atk_text(unit)}　SPD {speed_text(unit)}"
            )

            for skill in master.skills_of(unit.familiar_id):
                kind = "ACTIVE" if skill.is_active else "PASSIVE"
                if show_effects:
                    lines.append(f"　{kind}「{skill.name}」")
                    lines.append(f"　-# {skill.description}")
                else:
                    lines.append(f"　{kind}「{skill.name}」")

            if not master.skills_of(unit.familiar_id):
                lines.append("　スキルなし")

        if not units:
            lines.append("出場する使い魔がいません。")

        lines.append("")

    lines.append("-# 相手ギルドはスキル名だけを表示しています。")

    description = "\n".join(lines)
    if len(description) > 4000:
        description = description[:4000] + "\n-# 表示を省略しました。"

    return discord.Embed(
        title="⚔ バトル開始",
        description=description,
        color=COLOR_RESULT,
    )


def build_opponent_turn_embed(
    state: BattleState,
    unit: BattleUnit,
    *,
    guild_names: dict[int, str],
    player_names: dict[int, str] | None = None,
) -> discord.Embed:
    """相手ギルドのターンが始まったことを知らせるEmbed（17節）。"""

    lines = [
        item_line("行動する使い魔", f"**{unit_label(state, unit)}**"),
        item_line("ステータス", stat_line(state, unit)),
    ]

    summary = effects_module.buff_summary(state, unit)
    marks: list[str] = []
    marks.extend(f"🔺{text}" for text in summary["buffs"])
    marks.extend(f"🔻{text}" for text in summary["debuffs"])
    marks.extend(f"☠{text}" for text in summary["statuses"])
    marks.extend(f"◆{text}" for text in summary["others"])

    if marks:
        lines.append(item_line("かかっている効果", " ".join(marks)))

    return discord.Embed(
        title="⏳ 相手のターンです",
        description="\n".join(lines),
        color=COLOR_ENEMY,
    )


def build_status_embed(
    state: BattleState,
    *,
    guild_names: dict[int, str],
    highlight_guild_id: int | None = None,
    bet_coin: int | None = None,
    viewer_guild_id: int | None = None,
) -> discord.Embed:
    """その時点の戦況をまとめたEmbedを作る（17節・24節）。

    戦況は「どちらが何体残っていて、HPがどれだけあるか」だけを見せます。
    ラウンドと行動順はラウンドの見出しEmbed、行動中の使い魔はターン通知が
    受け持つため、ここでは重ねて出しません。

    ``viewer_guild_id`` を渡すと、そのギルドから見た自ギルド・相手ギルドを
    記号で示します。バトル専用チャンネルはギルドごとに分かれているためです。
    """

    sections: list[str] = []

    for guild_id in (state.guild_a_id, state.guild_b_id):
        name = guild_names.get(guild_id, f"ギルド{guild_id}")
        remaining = format_remaining_time(state.remaining_seconds.get(guild_id, 0))
        prefix = "▶ " if guild_id == highlight_guild_id else ""
        mark = side_mark(state, guild_id, viewer_guild_id)

        blocks = [_unit_line(state, unit) for unit in state.guild_units(guild_id)]

        body = "\n".join(blocks) if blocks else "—"
        if len(body) > 1500:
            body = body[:1490] + "\n…"

        sections.append(
            item_line(
                f"{prefix}{mark}{name}",
                f"{survivor_text(state, guild_id)}　残り持ち時間 {remaining}",
            )
            + "\n"
            + body
        )

    master = load_master_data()

    embed = discord.Embed(
        title="【戦況】",
        description="\n\n".join(sections)[:4000],
        color=COLOR_INFO,
    )
    amount = master.battle.bet.coin if bet_coin is None else int(bet_coin)
    legend = SIDE_LEGEND if viewer_guild_id is not None else ""
    embed.set_footer(
        text=(
            f"ベット ギルドごと {amount:,} coin（出場者で均等に分担）　"
            f"{legend}　🔺バフ 🔻デバフ ☠状態異常 ◆その他"
        )
    )
    return embed


# ==================================================
# ターン通知・結果
# ==================================================
def build_turn_embed(
    state: BattleState,
    unit: BattleUnit,
    *,
    turn_seconds: int,
    bet_coin: int | None = None,
) -> discord.Embed:
    """バトル専用チャンネルへ出すターン通知（16節・17節）。

    ``turn_seconds`` は自動攻撃までの残り時間です（17節）。

    行動する使い魔のことだけを載せます。ラウンド番号とそのラウンドの行動順は
    ラウンドの見出しEmbed、生存とHPは戦況Embedが受け持つため、重ねて出しません。
    """

    master = load_master_data()

    lines = [
        f"`{hp_bar(unit.current_hp, unit.max_hp)}` "
        f"{unit.current_hp}/{unit.max_hp}",
        "",
        item_line("現在ATK", atk_text(unit)),
        item_line("現在SPD", speed_text(unit)),
    ]

    summary = effects_module.buff_summary(state, unit)
    marks: list[str] = []
    marks.extend(f"🔺{text}" for text in summary["buffs"])
    marks.extend(f"🔻{text}" for text in summary["debuffs"])
    marks.extend(f"☠{text}" for text in summary["statuses"])
    marks.extend(f"◆{text}" for text in summary["others"])
    lines.append(item_line("かかっている効果", " ".join(marks) if marks else "なし"))

    enemy_guild_id = state.enemy_guild_id(unit.guild_id)

    lines.extend(
        [
            "",
            *skill_lines(unit),
            "",
            item_line("行動順", turn_position_text(state, unit)),
            item_line(
                "生存",
                f"味方 {survivor_text(state, unit.guild_id)}"
                f"／敵 {survivor_text(state, enemy_guild_id)}",
            ),
            item_line("自動攻撃まで", format_remaining_time(turn_seconds)),
            item_line(
                "残り持ち時間",
                format_remaining_time(state.remaining_seconds.get(unit.guild_id, 0)),
            ),
        ]
    )

    embed = discord.Embed(
        title=f"▶ {unit_label(state, unit)} の行動順です",
        description="\n".join(lines)[:4000],
        color=COLOR_ALLY,
    )
    amount = master.battle.bet.coin if bet_coin is None else int(bet_coin)
    embed.set_footer(
        text=(
            f"ベット ギルドごと {amount:,} coin（出場者で均等に分担）／"
            f"勝利 {master.battle.bet.win_xp} XP・敗北 {master.battle.bet.lose_xp} XP"
        )
    )
    return embed


def build_result_embed(
    state: BattleState,
    *,
    guild_names: dict[int, str],
    reward_text: str | None = None,
    bet_coin: int | None = None,
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

    master = load_master_data()

    lines = [
        description,
        "",
        item_line("対戦", f"{guild_a} vs {guild_b}"),
        item_line(
            "決着理由", reasons.get(state.end_reason or "", state.end_reason or "—")
        ),
        item_line("ラウンド数", state.current_round),
        item_line(
            "ベット額",
            f"ギルドごと {master.battle.bet.coin:,} coin"
            if bet_coin is None
            else f"ギルドごと {int(bet_coin):,} coin",
        ),
    ]

    if reward_text:
        lines.append(item_line("清算", reward_text))

    return discord.Embed(
        title=title,
        description="\n".join(lines)[:4000],
        color=COLOR_RESULT,
    )
