"""行動ログEmbedと戦況Embedの組み立て（GAME_SPEC 23節・24節）。

``game/`` の中で唯一Discordへ依存するモジュールです。戦闘の計算は行わず、
``BattleState`` と行動ログを受け取って表示だけを作ります。

表示される日本語の文言と記号は ``texts/battle_display.py`` にまとめてあります。
ここには「どの順番で組み立てるか」だけを書き、文章そのものは置きません。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import discord

from texts import battle_display as display_texts
from texts import common as common_texts

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

# 行動順や編成で「どちらのギルドか」を示す記号（文言は texts/battle_display.py）
MARK_ALLY = display_texts.MARK_ALLY
MARK_ENEMY = display_texts.MARK_ENEMY
MARK_GUILD_A = display_texts.MARK_GUILD_A
MARK_GUILD_B = display_texts.MARK_GUILD_B

SIDE_LEGEND = display_texts.SIDE_LEGEND

HP_BAR_LENGTH = display_texts.HP_BAR_LENGTH
HP_BAR_FILLED = display_texts.HP_BAR_FILLED
HP_BAR_EMPTY = display_texts.HP_BAR_EMPTY

SLOT_MARKS = display_texts.SLOT_MARKS

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
        return display_texts.UNKNOWN_UNIT
    return familiar_name(unit.familiar_id)


def unit_label(state: BattleState, unit: BattleUnit) -> str:
    """「ロキ Lv.3」のような表示名を返す。

    バトル中はランク記号を付けません。強さはATKやHPで分かるため、
    行動順や戦況が記号で埋まって読みにくくなるのを避けます。
    """

    return display_texts.UNIT_LABEL.format(
        name=familiar_name(unit.familiar_id), level=unit.level
    )


def survivor_text(state: BattleState, guild_id: int) -> str:
    """「3/5体」の生存数を返す。"""

    units = state.guild_units(guild_id)
    alive = sum(1 for unit in units if unit.alive)

    return display_texts.SURVIVOR.format(alive=alive, total=len(units))


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
        return display_texts.EMPTY

    parts: list[str] = []

    for index in range(state.turn_index, len(state.turn_order)):
        unit = state.unit(state.turn_order[index])
        if unit is None or not unit.alive:
            continue

        mark = side_mark(state, unit.guild_id, viewer_guild_id)
        label = display_texts.TURN_QUEUE_ENTRY.format(
            mark=mark, name=familiar_name(unit.familiar_id)
        )
        if state.current_unit_id == unit.battle_unit_id:
            label = display_texts.TURN_QUEUE_CURRENT.format(label=label)

        parts.append(label)

        if len(parts) >= limit:
            parts.append(display_texts.TURN_QUEUE_MORE)
            break

    if not parts:
        return display_texts.TURN_QUEUE_FINISHED

    return display_texts.TURN_QUEUE_SEPARATOR.join(parts)


def turn_position_text(state: BattleState, unit: BattleUnit) -> str:
    """「2/6番目」のように、このラウンドでの行動順の位置を返す。"""

    order = [
        unit_id
        for unit_id in state.turn_order
        if (found := state.unit(unit_id)) is not None and found.alive
    ]

    if unit.battle_unit_id not in order:
        return display_texts.EMPTY

    return display_texts.TURN_POSITION.format(
        position=order.index(unit.battle_unit_id) + 1, total=len(order)
    )


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
    return display_texts.REMAINING_TIME.format(minutes=minutes, seconds=remainder)


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

    return common_texts.ITEM_LINE.format(label=label, value=value)


def stat_with_delta(current: int, base: int) -> str:
    """現在値と、基礎値からの増減を「15（+2）」の形で返す。

    増減が無ければ数値だけを返します。バフ・デバフがどれだけ乗っているかを
    ひと目で分かるようにするためです。
    """

    delta = current - base
    if delta == 0:
        return str(current)

    return display_texts.STAT_WITH_DELTA.format(value=current, delta=delta)


def atk_text(unit: BattleUnit) -> str:
    """現在ATKを「15（+2）」の形で返す。"""

    return stat_with_delta(unit.current_atk, unit.base_atk)


def speed_text(unit: BattleUnit) -> str:
    """現在SPDを「96（+12）」の形で返す。"""

    return stat_with_delta(unit.speed, effects_module.base_speed_of(unit))


def stat_line(state: BattleState, unit: BattleUnit) -> str:
    """HP・ATK・SPDをまとめた1行を作る（バトルログ用）。"""

    if not unit.alive:
        return display_texts.STAT_LINE_DEFEATED.format(max_hp=unit.max_hp)

    return display_texts.STAT_LINE.format(
        hp=unit.current_hp,
        max_hp=unit.max_hp,
        atk=atk_text(unit),
        speed=speed_text(unit),
    )


def effect_marks(state: BattleState, unit: BattleUnit) -> list[str]:
    """かかっている効果の記号を返す。バフ・デバフは含めない。

    ATKとSPDの増減は「9（+2）」の形で数値そのものに出ているため、記号で
    重ねて出すと行末が埋まって読みにくくなります。数値では分からない
    状態異常とその他の効果だけを残します。
    """

    summary = effects_module.buff_summary(state, unit)

    marks: list[str] = []
    marks.extend(
        display_texts.MARK_STATUS.format(text=text) for text in summary["statuses"]
    )
    marks.extend(
        display_texts.MARK_OTHER.format(text=text) for text in summary["others"]
    )

    return marks


def unit_status_lines(state: BattleState, unit: BattleUnit) -> list[str]:
    """使い魔1体の状態を、名前・ステータス・効果の3行以内でまとめる。"""

    lines = [stat_line(state, unit)]

    marks = effect_marks(state, unit)

    if marks:
        lines.append(display_texts.MARK_SEPARATOR.join(marks))

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
        display_texts.DEFEAT_LINE.format(
            mark=side_mark(state, unit.guild_id, viewer_guild_id),
            name=unit_label(state, unit),
        ),
        display_texts.HP_BAR_LINE.format(
            bar=HP_BAR_EMPTY * HP_BAR_LENGTH, hp=0, max_hp=unit.max_hp
        ),
        item_line(
            display_texts.DEFEAT_REMAINING_LABEL,
            survivor_text(state, unit.guild_id),
        ),
    ]

    embed = discord.Embed(
        title=display_texts.DEFEAT_TITLE.format(
            name=familiar_name(unit.familiar_id)
        ),
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
        return display_texts.EMPTY
    if player_names and player_id in player_names:
        return player_names[player_id]
    return display_texts.PLAYER_MENTION.format(player_id=player_id)


def _hp_change_text(before: object, after: object) -> str:
    """「HP 51 → **38**」とHPの増減だけを返す。"""

    unknown = display_texts.LOG_HP_UNKNOWN

    return display_texts.LOG_HP_CHANGE.format(
        before=before if before is not None else unknown,
        after=after if after is not None else unknown,
    )


def _hp_bar_line(unit: BattleUnit | None, after: object) -> str | None:
    """変動後のHPバーの行を返す。使い魔が分からなければ ``None``。"""

    if unit is None:
        return None

    try:
        current = int(after)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        current = unit.current_hp

    return display_texts.HP_BAR_LINE.format(
        bar=hp_bar(current, unit.max_hp), hp=current, max_hp=unit.max_hp
    )


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
            return display_texts.LOG_DAMAGE_NULLIFIED.format(target=target)

        lines = []
        if detail.get("critical"):
            lines.append(display_texts.LOG_CRITICAL)

        # 括弧の中はHPの変動。何によるダメージかは、この行動ログの
        # 表題（⚔攻撃／✦SKILL／☠毒）が示すため繰り返しません。
        hp_after = detail.get("hp_after")
        lines.append(
            display_texts.LOG_DAMAGE.format(
                damage=amount,
                target=target,
                hp_change=_hp_change_text(detail.get("hp_before"), hp_after),
            )
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
                display_texts.LOG_HEAL.format(target=target, amount=log.amount or 0),
                _hp_change_line(
                    unit, detail.get("hp_before"), detail.get("hp_after")
                ),
            ]
        )

    if event == BattleEvent.STATUS_APPLY.value:
        target = unit_name(state, log.target_unit_id)
        status = STATUS_LABELS.get(detail.get("status", ""), detail.get("status", ""))
        if not detail.get("applied"):
            return display_texts.LOG_STATUS_NULLIFIED.format(
                target=target, status=status
            )
        return display_texts.LOG_STATUS_APPLIED.format(
            target=target, status=status, turns=detail.get("remaining", 1)
        )

    if event == BattleEvent.EFFECT.value:
        target = unit_name(state, log.target_unit_id)
        text = detail.get("text") or ""

        if log.target_unit_id is None:
            return text

        lines = [display_texts.LOG_EFFECT.format(target=target, text=text)]

        # ATK・SPDが実際に動いた場合だけ、その前後を並べる
        for label, before_key, after_key in (
            (display_texts.LOG_STAT_LABEL_ATK, "atk_before", "atk_after"),
            (display_texts.LOG_STAT_LABEL_SPEED, "speed_before", "speed_after"),
        ):
            before = detail.get(before_key)
            after = detail.get(after_key)
            if before is None or after is None or before == after:
                continue

            lines.append(
                display_texts.LOG_STAT_CHANGE.format(
                    label=label, before=before, after=after
                )
            )

        return "\n".join(lines)

    if event == BattleEvent.DEFEAT_CHECK.value:
        return display_texts.LOG_DEFEAT.format(
            name=unit_name(state, log.target_unit_id)
        )

    if event == BattleEvent.BEFORE_DEFEAT.value:
        return display_texts.LOG_BEFORE_DEFEAT.format(
            name=unit_name(state, log.target_unit_id),
            text=detail.get("text", display_texts.LOG_BEFORE_DEFEAT_DEFAULT),
        )

    if event == BattleEvent.REVIVE.value:
        return display_texts.LOG_REVIVE.format(
            name=unit_name(state, log.target_unit_id),
            text=detail.get("text", display_texts.LOG_REVIVE_DEFAULT),
        )

    if event == BattleEvent.POISON.value:
        return display_texts.LOG_POISON.format(
            name=unit_name(state, log.target_unit_id)
        )

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
            group.title = display_texts.LOG_TITLE_PASSIVE_MULTI.format(
                count=group.passive_count
            )
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
            body.extend(
                item_line(name, value or display_texts.EMPTY)
                for name, value in group.items
            )

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
                title=display_texts.LOG_TITLE_ROUND.format(round=log.round),
                color=COLOR_INFO,
            )
            group.items.append(
                (
                    display_texts.LOG_ROUND_ORDER_LABEL,
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
                title=display_texts.LOG_TITLE_ATTACK.format(
                    name=unit_name(state, log.actor_unit_id)
                ),
                color=COLOR_ATTACK,
                familiar_id=actor.familiar_id if actor else None,
                guild_id=actor.guild_id if actor else None,
            )
            continue

        if event == BattleEvent.SKILL.value:
            group = _Group(
                title=display_texts.LOG_TITLE_SKILL.format(
                    skill=detail.get("skill_name", "")
                ),
                color=COLOR_SKILL,
                familiar_id=actor.familiar_id if actor else None,
                guild_id=actor.guild_id if actor else None,
            )
            group.lines.append(detail.get("description", ""))
            if actor is not None:
                owner = _player_label(player_names, actor.player_id)
                group.items.append(
                    (
                        display_texts.LOG_SKILL_OWNER_LABEL.format(
                            name=familiar_name(actor.familiar_id), owner=owner
                        ),
                        display_texts.STATUS_LINE_SEPARATOR.join(
                            unit_status_lines(state, actor)
                        ),
                    )
                )

            for unit_id in detail.get("target_unit_ids") or ():
                target = state.unit(unit_id)
                if target is None:
                    continue
                group.items.append(
                    (
                        display_texts.LOG_SKILL_TARGET_LABEL.format(
                            name=familiar_name(target.familiar_id)
                        ),
                        display_texts.STATUS_LINE_SEPARATOR.join(
                            unit_status_lines(state, target)
                        ),
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
                    title=display_texts.LOG_TITLE_PASSIVE.format(skill=skill_name),
                    color=COLOR_PASSIVE,
                    familiar_id=actor.familiar_id if actor else None,
                    guild_id=actor.guild_id if actor else None,
                    passive_only=True,
                )
                group.lines.append(
                    display_texts.LOG_PASSIVE_LINE.format(
                        name=owner, skill=skill_name
                    )
                )
                if actor is not None:
                    group.items.append(
                        (
                            display_texts.LOG_SKILL_OWNER_LABEL.format(
                                name=familiar_name(actor.familiar_id),
                                owner=_player_label(player_names, actor.player_id),
                            ),
                            display_texts.STATUS_LINE_SEPARATOR.join(
                                unit_status_lines(state, actor)
                            ),
                        )
                    )
            else:
                # 攻撃やスキルの途中で発動したパッシブは、その流れの中へ差し込む
                group.lines.append(
                    display_texts.LOG_PASSIVE_LINE.format(
                        name=owner, skill=skill_name
                    )
                )

                # バトル開始時パッシブのように、両ギルド分がまとまることがある
                if (
                    group.passive_only
                    and actor is not None
                    and group.guild_id is not None
                    and actor.guild_id != group.guild_id
                ):
                    group.mixed_guilds = True

            if description:
                group.lines.append(
                    display_texts.LOG_SKILL_DESCRIPTION.format(description=description)
                )

            group.passive_count += 1
            continue

        if event == BattleEvent.SKIP.value:
            status = STATUS_LABELS.get(
                detail.get("status", ""), display_texts.LOG_SKIP_DEFAULT_STATUS
            )
            group = _Group(
                title=display_texts.LOG_TITLE_SKIP.format(
                    name=unit_name(state, log.actor_unit_id)
                ),
                color=COLOR_INFO,
                familiar_id=actor.familiar_id if actor else None,
                guild_id=actor.guild_id if actor else None,
            )
            group.lines.append(display_texts.LOG_SKIP_BODY.format(status=status))
            flush()
            continue

        if event == BattleEvent.TIMEOUT.value:
            group = _Group(
                title=display_texts.LOG_TITLE_TIMEOUT,
                color=COLOR_INFO,
                familiar_id=actor.familiar_id if actor else None,
                guild_id=actor.guild_id if actor else None,
            )
            group.lines.append(
                detail.get("text", display_texts.LOG_TIMEOUT_BODY)
            )
            continue

        if event == BattleEvent.BATTLE_END.value:
            continue

        if is_poison_damage:
            group = _Group(title=display_texts.LOG_TITLE_POISON, color=COLOR_DAMAGE)

        text = _describe_log(state, log, player_names)
        if text is None:
            continue

        if group is None:
            group = _Group(title=display_texts.LOG_TITLE_CHANGE, color=COLOR_INFO)

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
        return display_texts.STATUS_UNIT_DEFEATED.format(
            name=name, bar=bar, max_hp=unit.max_hp
        )

    bar = hp_bar(unit.current_hp, unit.max_hp)
    stats = display_texts.STATUS_UNIT_STATS.format(
        bar=bar,
        hp=unit.current_hp,
        max_hp=unit.max_hp,
        atk=atk_text(unit),
        speed=speed_text(unit),
    )

    marks = effect_marks(state, unit)

    lines = [
        display_texts.STATUS_UNIT_ALIVE.format(name=name, level=unit.level),
        stats,
    ]
    if marks:
        lines.append(
            display_texts.STATUS_UNIT_MARKS.format(
                marks=display_texts.MARK_SEPARATOR.join(marks)
            )
        )

    return "\n".join(lines)


def _active_uses_text(unit: BattleUnit, skill) -> str:
    """アクティブスキルの残り使用回数を短く返す。"""

    limit = skill.max_uses_per_battle
    if limit is None:
        return display_texts.SKILL_USES_UNLIMITED

    used = int(unit.active_skill_uses.get(skill.skill_id, 0))
    if used >= limit:
        return display_texts.SKILL_USES_EMPTY

    return display_texts.SKILL_USES_LEFT.format(count=limit - used)


def skill_lines(unit: BattleUnit) -> list[str]:
    """行動中の使い魔が持つアクティブ・パッシブスキルを並べる（19節）。

    自分の順番のときに「何を使えるのか」「何が自動で発動するのか」を
    その場で確認できるようにします。
    """

    skills = load_master_data().skills_of(unit.familiar_id)
    if not skills:
        return [
            item_line(display_texts.SKILL_NONE_LABEL, display_texts.SKILL_NONE)
        ]

    lines = [display_texts.SKILL_HEADING]

    for skill in skills:
        if skill.is_active:
            lines.append(
                display_texts.SKILL_ACTIVE.format(
                    name=skill.name, uses=_active_uses_text(unit, skill)
                )
            )
        else:
            lines.append(display_texts.SKILL_PASSIVE.format(name=skill.name))

        if skill.description:
            lines.append(
                display_texts.SKILL_DESCRIPTION.format(description=skill.description)
            )

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
        (display_texts.LINEUP_SIDE_ALLY, guild_id, True),
        (display_texts.LINEUP_SIDE_ENEMY, enemy_guild_id, False),
    ):
        name = guild_names.get(
            target_guild_id,
            common_texts.UNKNOWN_GUILD_NAME.format(guild_id=target_guild_id),
        )
        units = sorted(
            state.guild_units(target_guild_id),
            key=lambda unit: (-unit.speed, unit.battle_unit_id),
        )

        total_cost = sum(unit.cost for unit in units)
        cap = master.battle.max_total_cost
        cost_text = (
            display_texts.LINEUP_TOTAL_COST_NO_CAP.format(total=total_cost)
            if cap <= 0
            else display_texts.LINEUP_TOTAL_COST.format(total=total_cost, cap=cap)
        )

        lines.append(
            display_texts.LINEUP_GUILD_HEADING.format(side=side, name=name)
        )
        lines.append(
            display_texts.LINEUP_SUMMARY_LINE.format(
                cost=item_line(display_texts.LINEUP_TOTAL_COST_LABEL, cost_text),
                entry=item_line(
                    display_texts.LINEUP_ENTRY_LABEL,
                    display_texts.LINEUP_ENTRY_COUNT.format(count=len(units)),
                ),
            )
        )

        for index, unit in enumerate(units):
            mark = (
                SLOT_MARKS[index]
                if index < len(SLOT_MARKS)
                else display_texts.LINEUP_SLOT_NUMBER.format(number=index + 1)
            )
            owner = _player_label(player_names, unit.player_id)

            lines.append(
                display_texts.LINEUP_UNIT.format(
                    mark=mark,
                    name=unit_label(state, unit),
                    cost=unit.cost,
                    owner=owner,
                )
            )
            lines.append(
                display_texts.LINEUP_UNIT_STATS.format(
                    bar=hp_bar(unit.current_hp, unit.max_hp),
                    hp=unit.current_hp,
                    max_hp=unit.max_hp,
                    atk=atk_text(unit),
                    speed=speed_text(unit),
                )
            )

            for skill in master.skills_of(unit.familiar_id):
                kind = (
                    display_texts.SKILL_KIND_ACTIVE
                    if skill.is_active
                    else display_texts.SKILL_KIND_PASSIVE
                )
                if show_effects:
                    lines.append(
                        display_texts.LINEUP_SKILL.format(
                            kind=kind, name=skill.name
                        )
                    )
                    lines.append(
                        display_texts.LINEUP_SKILL_DESCRIPTION.format(
                            description=skill.description
                        )
                    )
                else:
                    lines.append(
                        display_texts.LINEUP_SKILL.format(
                            kind=kind, name=skill.name
                        )
                    )

            if not master.skills_of(unit.familiar_id):
                lines.append(display_texts.LINEUP_NO_SKILL)

        if not units:
            lines.append(display_texts.LINEUP_NO_UNITS)

        lines.append("")

    lines.append(display_texts.LINEUP_NOTE)

    description = "\n".join(lines)
    if len(description) > 4000:
        description = description[:4000] + display_texts.LINEUP_TRUNCATED

    return discord.Embed(
        title=display_texts.LINEUP_TITLE,
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
        item_line(
            display_texts.OPPONENT_TURN_UNIT_LABEL,
            display_texts.OPPONENT_TURN_UNIT_VALUE.format(
                name=unit_label(state, unit)
            ),
        ),
        item_line(display_texts.OPPONENT_TURN_STATS_LABEL, stat_line(state, unit)),
    ]

    marks = effect_marks(state, unit)

    if marks:
        lines.append(
            item_line(
                display_texts.OPPONENT_TURN_EFFECTS_LABEL,
                display_texts.MARK_SEPARATOR.join(marks),
            )
        )

    return discord.Embed(
        title=display_texts.OPPONENT_TURN_TITLE,
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
        name = guild_names.get(
            guild_id, common_texts.UNKNOWN_GUILD_NAME.format(guild_id=guild_id)
        )
        remaining = format_remaining_time(state.remaining_seconds.get(guild_id, 0))
        prefix = (
            display_texts.STATUS_CURRENT_PREFIX
            if guild_id == highlight_guild_id
            else ""
        )
        mark = side_mark(state, guild_id, viewer_guild_id)

        blocks = [_unit_line(state, unit) for unit in state.guild_units(guild_id)]

        body = "\n".join(blocks) if blocks else display_texts.EMPTY
        if len(body) > 1500:
            body = body[:1490] + display_texts.STATUS_TRUNCATED

        sections.append(
            item_line(
                display_texts.STATUS_GUILD_HEADING.format(
                    prefix=prefix, mark=mark, name=name
                ),
                display_texts.STATUS_GUILD_SUMMARY.format(
                    survivors=survivor_text(state, guild_id), remaining=remaining
                ),
            )
            + "\n"
            + body
        )

    master = load_master_data()

    embed = discord.Embed(
        title=display_texts.STATUS_TITLE,
        description="\n\n".join(sections)[:4000],
        color=COLOR_INFO,
    )
    legend = SIDE_LEGEND if viewer_guild_id is not None else ""
    embed.set_footer(text=display_texts.STATUS_FOOTER.format(legend=legend).strip())
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
        display_texts.HP_BAR_LINE.format(
            bar=hp_bar(unit.current_hp, unit.max_hp),
            hp=unit.current_hp,
            max_hp=unit.max_hp,
        ),
        "",
        item_line(display_texts.TURN_ATK_LABEL, atk_text(unit)),
        item_line(display_texts.TURN_SPEED_LABEL, speed_text(unit)),
    ]

    marks = effect_marks(state, unit)
    lines.append(
        item_line(
            display_texts.TURN_EFFECTS_LABEL,
            display_texts.MARK_SEPARATOR.join(marks)
            if marks
            else display_texts.TURN_EFFECTS_NONE,
        )
    )

    enemy_guild_id = state.enemy_guild_id(unit.guild_id)

    lines.extend(
        [
            "",
            *skill_lines(unit),
            "",
            item_line(
                display_texts.TURN_ORDER_LABEL, turn_position_text(state, unit)
            ),
            item_line(
                display_texts.TURN_SURVIVORS_LABEL,
                display_texts.TURN_SURVIVORS.format(
                    ally=survivor_text(state, unit.guild_id),
                    enemy=survivor_text(state, enemy_guild_id),
                ),
            ),
            item_line(
                display_texts.TURN_AUTO_ATTACK_LABEL,
                format_remaining_time(turn_seconds),
            ),
            item_line(
                display_texts.TURN_TIME_LEFT_LABEL,
                format_remaining_time(state.remaining_seconds.get(unit.guild_id, 0)),
            ),
        ]
    )

    embed = discord.Embed(
        title=display_texts.TURN_TITLE.format(name=unit_label(state, unit)),
        description="\n".join(lines)[:4000],
        color=COLOR_ALLY,
    )
    amount = master.battle.bet.coin if bet_coin is None else int(bet_coin)
    embed.set_footer(
        text=display_texts.TURN_FOOTER.format(
            coin=amount,
            win_xp=master.battle.bet.win_xp,
            lose_xp=master.battle.bet.lose_xp,
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

    guild_a = guild_names.get(
        state.guild_a_id,
        common_texts.UNKNOWN_GUILD_NAME.format(guild_id=state.guild_a_id),
    )
    guild_b = guild_names.get(
        state.guild_b_id,
        common_texts.UNKNOWN_GUILD_NAME.format(guild_id=state.guild_b_id),
    )

    if state.result == RESULT_GUILD_A:
        title = display_texts.RESULT_TITLE_WIN
        description = display_texts.RESULT_WIN.format(name=guild_a)
    elif state.result == RESULT_GUILD_B:
        title = display_texts.RESULT_TITLE_WIN
        description = display_texts.RESULT_WIN.format(name=guild_b)
    elif state.result == RESULT_DRAW:
        title = display_texts.RESULT_TITLE_DRAW
        description = display_texts.RESULT_DRAW
    elif state.result == RESULT_ABORTED:
        title = display_texts.RESULT_TITLE_ABORTED
        description = display_texts.RESULT_ABORTED
    else:
        title = display_texts.RESULT_TITLE_UNKNOWN
        description = display_texts.EMPTY

    reasons = display_texts.RESULT_REASONS

    master = load_master_data()

    lines = [
        description,
        "",
        item_line(
            display_texts.RESULT_MATCH_LABEL,
            display_texts.RESULT_MATCH.format(guild_a=guild_a, guild_b=guild_b),
        ),
        item_line(
            display_texts.RESULT_REASON_LABEL,
            reasons.get(
                state.end_reason or "", state.end_reason or display_texts.EMPTY
            ),
        ),
        item_line(display_texts.RESULT_ROUND_LABEL, state.current_round),
        item_line(
            display_texts.RESULT_BET_LABEL,
            display_texts.RESULT_BET.format(
                coin=master.battle.bet.coin if bet_coin is None else int(bet_coin)
            ),
        ),
    ]

    if reward_text:
        lines.append(item_line(display_texts.RESULT_REWARD_LABEL, reward_text))

    return discord.Embed(
        title=title,
        description="\n".join(lines)[:4000],
        color=COLOR_RESULT,
    )
