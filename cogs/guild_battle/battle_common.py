"""ギルドバトルの土台となる部品（定数・戦闘状態の読み出し・一時Viewの基底）。

このモジュールは ``cogs/guild_battle`` の最下層です。他の ``*_views.py`` から
参照されるだけで、こちらから参照し返すことはありません（循環importの防止）。

``familiar_display_name`` ``unit_option`` ``effect_marks`` ``apply_and_report``
は複数のモジュールから使う公開の助っ人です。
"""

from __future__ import annotations

import logging

import discord

from cogs import game_shared
from database.battle import (
    get_battle,
    get_battle_for_channel,
    load_battle_state,
)
from game import battle_embed
from game.master_data import load_master_data
from game.models import BattleRuleError

from . import service
from texts import battle as battle_texts
from texts import panels as panel_texts


logger = logging.getLogger(__name__)


# 公開スイッチがOFFのときに返す共通メッセージ（34.1節）
DISABLED_MESSAGE = game_shared.DISABLED_MESSAGE

# 常設パネルのタイトル（``ensure_panel_message`` の重複判定に使う）
BATTLE_PANEL_TITLE = panel_texts.BATTLE
ROSTER_PANEL_TITLE = panel_texts.BATTLE_ROSTER
RANKING_PANEL_TITLE = panel_texts.BATTLE_RANKING

# 改名前のパネル表題。見つけたら片づけて、新しいパネルへ置き換える。
LEGACY_ROSTER_PANEL_TITLES = panel_texts.BATTLE_ROSTER_LEGACY
LEGACY_FAMILIAR_PANEL_TITLES = panel_texts.BATTLE_LEGACY
LEGACY_RANKING_PANEL_TITLES = panel_texts.BATTLE_RANKING_LEGACY

# 一時Viewの有効時間（秒）
EPHEMERAL_TIMEOUT = 300

# Discordの選択肢の上限
SELECT_LIMIT = 25

# 1メッセージに置ける操作行の上限は5行。体数選択は確定ボタンで1行使うため、
# セレクトを並べられるのは最大4人分まで（超える人数は初期値で確定する）。
SELECT_ROWS_FOR_COUNTS = 4

# 1メッセージに並べられる操作行の数（Discordの仕様）
MAX_SELECT_ROWS = 5

# セレクトの見出しでランクを読み取るための先頭文字
RANK_INITIALS = frozenset({"S", "A", "B", "C"})


# ==================================================
# 共通ヘルパ
# ==================================================
def familiar_display_name(familiar_id: str) -> str:
    """使い魔IDから表示名を取得する。"""

    master = load_master_data()
    familiar = master.get_familiar(familiar_id)
    return familiar.name if familiar else familiar_id


def load_actor(channel_id: int, user_id: int):
    """バトル専用チャンネルから、現在の行動者と戦闘状態を取り出す（16節・34.1節）。

    ここでは利用資格（本メンバー以上）を確認しません。進行中のバトルの途中で
    資格を失った人の行動まで止めると、相手ギルドを巻き込んで試合が進まなく
    なるためです。資格の確認は、バトルを始める前の入口で行います。
    """

    if not game_shared.is_game_enabled():
        return None, None, None, DISABLED_MESSAGE

    battle_row = get_battle_for_channel(channel_id)
    if battle_row is None:
        return None, None, None, battle_texts.NO_BATTLE_IN_CHANNEL

    if battle_row["status"] != "in_progress":
        return None, None, None, battle_texts.BATTLE_NOT_IN_PROGRESS

    state = load_battle_state(int(battle_row["battle_id"]))
    if state is None:
        return None, None, None, battle_texts.BATTLE_LOAD_ERROR

    unit = state.current_unit()
    if unit is None:
        return None, None, None, battle_texts.NO_ACTION_ACCEPTED

    if unit.player_id != user_id:
        return None, None, None, battle_texts.NOT_YOUR_TURN

    return battle_row, state, unit, None


def unit_option(unit, state=None) -> discord.SelectOption:
    """戦闘用使い魔を選択肢へ変換する。

    ``state`` を渡すと、現在ATKに加えてバフ・デバフ・状態異常の有無も表示します。
    攻撃対象を選ぶときに「強化されている敵かどうか」が分かるようにするためです。
    """

    label = battle_texts.FAMILIAR_LABEL.format(
        name=familiar_display_name(unit.familiar_id), level=unit.level
    )
    description = battle_texts.UNIT_STATS.format(
        hp=unit.current_hp,
        max_hp=unit.max_hp,
        atk=battle_embed.atk_text(unit),
        speed=battle_embed.speed_text(unit),
    )

    if state is not None:
        marks = effect_marks(state, unit)
        if marks:
            description = battle_texts.UNIT_STATS_WITH_EFFECTS.format(
                stats=description, marks=marks
            )

    return discord.SelectOption(
        label=label[:100],
        description=description[:100],
        value=str(unit.battle_unit_id),
    )


def effect_marks(state, unit) -> str:
    """かかっている効果を短くまとめた文字列を返す。

    バフ・デバフは含めません。ATKとSPDの増減は「9（+2）」の形で数値に出ている
    ため、記号で重ねると読みにくくなります（``battle_embed.effect_marks``と同じ方針）。
    """

    return " ".join(battle_embed.effect_marks(state, unit))


async def apply_and_report(
    interaction: discord.Interaction,
    battle_id: int,
    action,
    *,
    expected_seq: int,
    success_message: str | None,
) -> bool:
    """行動を適用し、失敗した理由だけを実行者へ ephemeral で返す。

    ``success_message`` に ``None`` を渡すと、成功時は何も送りません。
    結果はバトル専用チャンネルの行動ログへ出るため、通常攻撃のように
    「実行しました」だけの控えが不要な行動で使います。
    """

    battle_row = get_battle(battle_id)
    elapsed = service.elapsed_seconds_since_turn_start(battle_row)

    try:
        applied = await service.apply_action(
            interaction.client,
            battle_id,
            action,
            elapsed_seconds=elapsed,
            expected_seq=expected_seq,
        )
    except BattleRuleError as exc:
        await game_shared.respond(interaction, str(exc))
        return False
    except Exception:
        logger.exception(f"バトル行動の処理に失敗しました: battle_id={battle_id}")
        await game_shared.respond(interaction, battle_texts.ACTION_FAILED)
        return False

    if not applied:
        await game_shared.respond(interaction, battle_texts.ACTION_RACED)
        return False

    if success_message is not None:
        await game_shared.respond(interaction, success_message)

    return True


# ==================================================
# 一時View（custom_idを付けない）
# ==================================================
class PagedSelectView(discord.ui.View):
    """候補を1件選ばせる一時View。

    Discordのセレクトは1つ25件までなので、候補が多い場合はセレクトを複数並べて
    **すべて同時に表示**します。1メッセージに置ける操作行は5行までなので、
    それを超える件数だけページ送りへ切り替えます。
    """

    def __init__(
        self,
        options: list[discord.SelectOption],
        *,
        placeholder: str,
    ) -> None:
        super().__init__(timeout=EPHEMERAL_TIMEOUT)

        self._options = options
        self._placeholder = placeholder
        self.page = 0
        self._render()

    @property
    def _per_page(self) -> int:
        """1ページに表示する件数。"""

        return SELECT_LIMIT * MAX_SELECT_ROWS

    @property
    def page_count(self) -> int:
        return max(1, -(-len(self._options) // self._per_page))

    def _render(self) -> None:
        self.clear_items()

        start = self.page * self._per_page
        page_options = self._options[start : start + self._per_page]

        paging = self.page_count > 1
        # ページ送りボタンを置く場合は1行分を空ける
        rows = MAX_SELECT_ROWS - (1 if paging else 0)
        chunks = [
            page_options[index : index + SELECT_LIMIT]
            for index in range(0, len(page_options), SELECT_LIMIT)
        ][:rows]

        self._selects: list[discord.ui.Select] = []

        for row, chunk in enumerate(chunks):
            label = self._chunk_label(chunk)
            select = discord.ui.Select(
                placeholder=f"{self._placeholder}{label}"[:150],
                min_values=1,
                max_values=1,
                options=chunk,
                row=row,
            )
            select.callback = self._select_callback
            self.add_item(select)
            self._selects.append(select)

        if paging:
            previous = discord.ui.Button(
                label=battle_texts.PAGE_PREVIOUS,
                style=discord.ButtonStyle.secondary,
                row=rows,
                disabled=self.page == 0,
            )
            previous.callback = self._previous_callback
            self.add_item(previous)

            following = discord.ui.Button(
                label=battle_texts.PAGE_NEXT,
                style=discord.ButtonStyle.secondary,
                row=rows,
                disabled=self.page >= self.page_count - 1,
            )
            following.callback = self._next_callback
            self.add_item(following)

    def _chunk_label(self, chunk: list[discord.SelectOption]) -> str:
        """セレクトの見出しに、その塊が何を含むかを付ける。

        ランク順に並んでいるため、先頭と末尾のランクを見れば範囲が分かります。
        """

        ranks = [
            option.label[:1]
            for option in chunk
            if option.label and option.label[:1] in RANK_INITIALS
        ]
        if not ranks:
            return ""

        if ranks[0] == ranks[-1]:
            return battle_texts.SELECT_RANK_ONE.format(rank=ranks[0])

        return battle_texts.SELECT_RANK_RANGE.format(first=ranks[0], last=ranks[-1])

    async def _previous_callback(self, interaction: discord.Interaction) -> None:
        self.page = max(0, self.page - 1)
        self._render()
        await interaction.response.edit_message(view=self)

    async def _next_callback(self, interaction: discord.Interaction) -> None:
        self.page = min(self.page_count - 1, self.page + 1)
        self._render()
        await interaction.response.edit_message(view=self)

    async def _select_callback(self, interaction: discord.Interaction) -> None:
        for select in self._selects:
            if select.values:
                await self.on_choice(interaction, select.values[0])
                return

    async def on_choice(self, interaction: discord.Interaction, value: str) -> None:
        """選択されたときの処理。継承先で実装する。"""

        raise NotImplementedError


class ConfirmView(discord.ui.View):
    """2段階確認用の一時View。"""

    def __init__(self, *, confirm_label: str = battle_texts.CONFIRM_BUTTON) -> None:
        super().__init__(timeout=EPHEMERAL_TIMEOUT)

        confirm = discord.ui.Button(
            label=confirm_label, style=discord.ButtonStyle.danger
        )
        confirm.callback = self._confirm_callback
        self.add_item(confirm)

        cancel = discord.ui.Button(
            label=battle_texts.CANCEL_BUTTON, style=discord.ButtonStyle.secondary
        )
        cancel.callback = self._cancel_callback
        self.add_item(cancel)

        self._confirm = confirm

    async def _confirm_callback(self, interaction: discord.Interaction) -> None:
        # 連打による二重実行を防ぐため、押下直後に操作不能にする
        for item in self.children:
            item.disabled = True

        try:
            await interaction.response.edit_message(view=self)
        except discord.HTTPException:
            pass

        await self.on_confirm(interaction)

    async def _cancel_callback(self, interaction: discord.Interaction) -> None:
        for item in self.children:
            item.disabled = True

        try:
            await interaction.response.edit_message(
                content=battle_texts.CANCELLED_OPERATION, view=self
            )
        except discord.HTTPException:
            pass

    async def on_confirm(self, interaction: discord.Interaction) -> None:
        """確定されたときの処理。継承先で実装する。"""

        raise NotImplementedError
