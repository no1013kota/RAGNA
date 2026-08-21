"""ギルドバトルの操作画面（パネル・申請・募集・バトル専用チャンネル）。

固定の ``custom_id`` を持つ常設Viewはこのモジュールにまとめ、Bot再起動後も
同じボタンが動くようにします（29節）。操作対象は ``custom_id`` へ埋め込まず、
``interaction.message.id`` と ``interaction.channel.id`` からDBを引いて特定します。

一時的（ephemeral）なSelectやボタンには ``custom_id`` を付けません
（discord.pyが自動採番するため）。
"""

from __future__ import annotations

import logging

import discord
import config

from cogs import game_shared
from database.battle import (
    claim_battle_recruitment,
    create_battle_recruitment,
    create_battle_request,
    get_active_battle_for_guild,
    get_battle,
    get_battle_for_channel,
    get_battle_lock,
    get_battle_recruitment,
    get_battle_recruitment_by_message,
    get_battle_request,
    get_battle_request_by_message,
    get_player_battle_familiars,
    set_player_battle_familiars,
    add_battle_entry,
    get_battle_entries,
    get_battle_roster,
    remove_battle_entry,
    get_pending_battle_request_for_guild,
    load_battle_state,
    resolve_battle_recruitment,
    resolve_battle_request,
    set_battle_recruitment_message,
    set_battle_request_message,
    set_battle_roster,
    swap_battle_entry,
)
from database.familiar import get_owned_familiar, get_owned_familiars
from database.guild import (
    get_active_guilds,
    get_guild,
    get_guild_by_channel,
    get_guild_members,
    get_guild_ranking,
    get_guild_ranking_position,
    get_player_guild,
)
from game import battle_embed, battle_engine, effects
from game.master_data import load_master_data
from game.models import (
    ACTION_ATTACK,
    ACTION_SKILL,
    BattleAction,
    BattleRuleError,
)
from utils import ensure_panel_message, remove_legacy_panels

from . import service
from texts import battle as battle_texts
from texts import common as common_texts
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
def _familiar_name(familiar_id: str) -> str:
    """使い魔IDから表示名を取得する。"""

    master = load_master_data()
    familiar = master.get_familiar(familiar_id)
    return familiar.name if familiar else familiar_id


def master_guild_of_channel(interaction: discord.Interaction) -> tuple[dict | None, str | None]:
    """マスター専用TCの操作であることを確認し、ギルド行を返す（29節・34.1節）。"""

    blocked = game_shared.game_block_reason(interaction.user)
    if blocked is not None:
        return None, blocked

    channel = interaction.channel
    if channel is None:
        return None, common_texts.CHANNEL_ERROR

    guild_row = get_guild_by_channel(channel.id)
    if guild_row is None or guild_row.get("master_text_channel_id") != channel.id:
        return None, battle_texts.NOT_MASTER_CHANNEL

    if guild_row["status"] != "active":
        return None, battle_texts.GUILD_NOT_ACTIVE

    if guild_row["master_id"] != interaction.user.id:
        return None, game_shared.error_message("not_master")

    return guild_row, None


def roster_guild_of_channel(interaction: discord.Interaction) -> tuple[dict | None, str | None]:
    """使い魔バトルチャンネルの操作であることを確認し、ギルド行を返す（34.1節）。"""

    blocked = game_shared.game_block_reason(interaction.user)
    if blocked is not None:
        return None, blocked

    channel = interaction.channel
    if channel is None:
        return None, common_texts.CHANNEL_ERROR

    guild_row = get_guild_by_channel(channel.id)
    if guild_row is None or guild_row.get("battle_member_channel_id") != channel.id:
        return None, battle_texts.NOT_BATTLE_MEMBER_CHANNEL

    if guild_row["status"] != "active":
        return None, battle_texts.GUILD_NOT_ACTIVE

    return guild_row, None


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


def _unit_option(unit, state=None) -> discord.SelectOption:
    """戦闘用使い魔を選択肢へ変換する。

    ``state`` を渡すと、現在ATKに加えてバフ・デバフ・状態異常の有無も表示します。
    攻撃対象を選ぶときに「強化されている敵かどうか」が分かるようにするためです。
    """

    label = battle_texts.FAMILIAR_LABEL.format(
        name=_familiar_name(unit.familiar_id), level=unit.level
    )
    description = battle_texts.UNIT_STATS.format(
        hp=unit.current_hp,
        max_hp=unit.max_hp,
        atk=battle_embed.atk_text(unit),
        speed=battle_embed.speed_text(unit),
    )

    if state is not None:
        marks = _effect_marks(state, unit)
        if marks:
            description = battle_texts.UNIT_STATS_WITH_EFFECTS.format(
                stats=description, marks=marks
            )

    return discord.SelectOption(
        label=label[:100],
        description=description[:100],
        value=str(unit.battle_unit_id),
    )


def _effect_marks(state, unit) -> str:
    """かかっている効果を短くまとめた文字列を返す。

    バフ・デバフは含めません。ATKとSPDの増減は「9（+2）」の形で数値に出ている
    ため、記号で重ねると読みにくくなります（``battle_embed.effect_marks``と同じ方針）。
    """

    return " ".join(battle_embed.effect_marks(state, unit))


async def _apply_and_report(
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


# ==================================================
# 出場者セット（9節）
# ==================================================
def default_familiar_counts(member_count: int) -> list[int]:
    """出場者ごとの使い魔体数の初期値を、合計上限まで均等に配る（9節）。"""

    master = load_master_data()

    if member_count <= 0:
        return []

    limit = master.familiar_limit_per_member(member_count)
    total = min(master.battle.max_units, limit * member_count)

    base = total // member_count
    counts = [max(1, base)] * member_count

    # 端数は先頭の出場者から1体ずつ、上限に触れない範囲で足す。
    remainder = total - sum(counts)
    index = 0
    while remainder > 0 and index < member_count:
        if counts[index] < limit:
            counts[index] += 1
            remainder -= 1
        else:
            index += 1

    return counts


async def apply_roster(
    interaction: discord.Interaction,
    guild_row: dict,
    assignments: list[tuple[int, int]],
) -> None:
    """出場者セットを確定し、結果を知らせる。

    使い魔バトルチャンネルは所属メンバー全員へ開放しているため、出場者が
    変わってもチャンネル権限は張り替えません（9.1節）。
    """

    guild_id = int(guild_row["guild_id"])
    master = load_master_data()

    result = set_battle_roster(guild_id, assignments)
    if not result["ok"]:
        await game_shared.respond(
            interaction, game_shared.error_message(result["error"])
        )
        return

    user_ids = [user_id for user_id, _ in assignments]
    discord_guild = interaction.guild

    if discord_guild is not None:
        await ensure_roster_panel(interaction.client, discord_guild, guild_row)

    lines = [
        game_shared.item_line(
            battle_texts.LABEL_MEMBERS,
            battle_texts.ROSTER_MEMBER_COUNT.format(count=len(user_ids)),
        ),
        game_shared.item_line(
            battle_texts.LABEL_FAMILIARS,
            battle_texts.ROSTER_FAMILIAR_COUNT.format(
                count=sum(count for _, count in assignments),
                max_units=master.battle.max_units,
            ),
        ),
        "",
    ]
    lines.extend(
        battle_texts.ROSTER_ASSIGN_LINE.format(user_id=user_id, count=count)
        for user_id, count in assignments
    )

    added = "・".join(f"<@{user_id}>" for user_id in result["added"])
    removed = "・".join(f"<@{user_id}>" for user_id in result["removed"])
    lines.append("")

    if added:
        lines.append(game_shared.item_line(battle_texts.LABEL_ADDED, added))
    if removed:
        lines.append(game_shared.item_line(battle_texts.LABEL_REMOVED, removed))

    if result.get("adopted"):
        lines.append(
            battle_texts.ROSTER_ADOPTED_NOTE.format(count=len(result["adopted"]))
        )

    if result.get("released"):
        lines.append(
            battle_texts.ROSTER_RELEASED_NOTE.format(count=len(result["released"]))
        )

    lines.append(
        battle_texts.ROSTER_SWAP_HINT.format(
            channel=game_shared.CHANNEL_LABELS["battle_member"]
        )
    )

    await game_shared.respond(interaction, "\n".join(lines))


class RosterSelectView(discord.ui.View):
    """所属メンバーから出場者を選ぶ一時View。"""

    def __init__(self, guild_row: dict, members: list[dict]) -> None:
        super().__init__(timeout=EPHEMERAL_TIMEOUT)

        master = load_master_data()
        self.guild_row = guild_row
        self.member_order = [int(row["user_id"]) for row in members]
        self.member_names = {
            int(row["user_id"]): str(row["display_name"]) for row in members
        }

        options = [
            discord.SelectOption(
                label=row["display_name"][:100],
                description=(
                    battle_texts.OPTION_GUILD_MASTER
                    if row["member_role"] == "master"
                    else None
                ),
                value=str(row["user_id"]),
            )
            for row in members[:SELECT_LIMIT]
        ]

        select = discord.ui.Select(
            placeholder=battle_texts.ROSTER_SELECT_PLACEHOLDER.format(
                max_members=master.battle.max_members
            ),
            min_values=master.battle.min_members,
            max_values=min(master.battle.max_members, len(options)),
            options=options,
        )
        select.callback = self._select_callback
        self.add_item(select)
        self._select = select

    async def _select_callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        guild_id = int(self.guild_row["guild_id"])

        # 権限と状態をDBから再確認する（29節）
        guild_row = get_guild(guild_id)
        if guild_row is None or guild_row["master_id"] != interaction.user.id:
            await game_shared.respond(interaction, game_shared.error_message("not_master"))
            return

        chosen = {int(value) for value in self._select.values}
        # Selectの返却順は保証されないため、メンバー一覧の順でスロットを決める
        user_ids = [user_id for user_id in self.member_order if user_id in chosen]

        master = load_master_data()
        limit = master.familiar_limit_per_member(len(user_ids))
        defaults = default_familiar_counts(len(user_ids))

        # 1人1体しか置けない人数では選ぶ余地がないため、そのまま確定する。
        # 選択肢がある場合も、Discordの5行制限に収まらない人数は初期値で確定する。
        if limit <= 1 or len(user_ids) > SELECT_ROWS_FOR_COUNTS:
            await apply_roster(
                interaction, guild_row, list(zip(user_ids, defaults))
            )
            return

        await game_shared.respond(
            interaction,
            battle_texts.ROSTER_COUNT_PROMPT.format(
                limit=limit, max_units=master.battle.max_units
            ),
            view=RosterCountView(
                guild_row=guild_row,
                user_ids=user_ids,
                names=self.member_names,
                limit=limit,
                defaults=defaults,
            ),
        )


class RosterCountView(discord.ui.View):
    """出場者ごとの使い魔体数を決める一時View（9節）。"""

    def __init__(
        self,
        *,
        guild_row: dict,
        user_ids: list[int],
        names: dict[int, str],
        limit: int,
        defaults: list[int],
    ) -> None:
        super().__init__(timeout=EPHEMERAL_TIMEOUT)

        master = load_master_data()

        self.guild_row = guild_row
        self.user_ids = user_ids
        self.counts = {
            user_id: count for user_id, count in zip(user_ids, defaults)
        }
        self.max_units = master.battle.max_units
        self._selects: dict[int, discord.ui.Select] = {}

        for row, user_id in enumerate(user_ids):
            current = self.counts[user_id]
            select = discord.ui.Select(
                placeholder=battle_texts.ROSTER_COUNT_LABEL.format(
                    name=names.get(user_id, user_id), count=current
                ),
                min_values=1,
                max_values=1,
                row=row,
                options=[
                    discord.SelectOption(
                        label=battle_texts.ROSTER_COUNT_LABEL.format(
                            name=names.get(user_id, user_id), count=count
                        )[:100],
                        value=f"{user_id}:{count}",
                        default=count == current,
                    )
                    for count in range(1, limit + 1)
                ],
            )
            select.callback = self._count_callback
            self.add_item(select)
            self._selects[user_id] = select

        confirm = discord.ui.Button(
            label=battle_texts.ROSTER_COUNT_CONFIRM,
            style=discord.ButtonStyle.success,
            row=len(user_ids),
        )
        confirm.callback = self._confirm_callback
        self.add_item(confirm)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != int(self.guild_row["master_id"]):
            await game_shared.respond(
                interaction, game_shared.error_message("not_master")
            )
            return False

        return True

    async def _count_callback(self, interaction: discord.Interaction) -> None:
        for select in self._selects.values():
            if not select.values:
                continue

            user_id, _, count = str(select.values[0]).partition(":")
            self.counts[int(user_id)] = int(count)

        total = sum(self.counts[user_id] for user_id in self.user_ids)

        try:
            await interaction.response.edit_message(
                content=(
                    battle_texts.ROSTER_COUNT_HEADING
                    + "\n"
                    + game_shared.item_line(
                        battle_texts.LABEL_CURRENT_TOTAL,
                        battle_texts.ROSTER_COUNT_TOTAL.format(
                            total=total, max_units=self.max_units
                        ),
                    )
                ),
                view=self,
            )
        except discord.HTTPException:
            logger.warning("体数選択の更新に失敗しました")

    async def _confirm_callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        guild_row = get_guild(int(self.guild_row["guild_id"]))
        if guild_row is None or guild_row["master_id"] != interaction.user.id:
            await game_shared.respond(
                interaction, game_shared.error_message("not_master")
            )
            return

        assignments = [(user_id, self.counts[user_id]) for user_id in self.user_ids]
        await apply_roster(interaction, guild_row, assignments)

        self.stop()


# ==================================================
# 使い魔セット（10.3節）
# ==================================================
class RosterFamiliarAddView(PagedSelectView):
    """出場者が持ち込む使い魔を1体追加する一時View（9節）。"""

    def __init__(self, guild_id: int, options: list[discord.SelectOption]) -> None:
        super().__init__(options, placeholder=battle_texts.ENTRY_ADD_PLACEHOLDER)

        self.guild_id = guild_id

    async def on_choice(self, interaction: discord.Interaction, value: str) -> None:
        await interaction.response.defer(ephemeral=True)

        guild_row = get_guild(self.guild_id)
        if guild_row is None or guild_row["status"] != "active":
            await game_shared.respond(
                interaction, game_shared.error_message("guild_not_found")
            )
            return

        if guild_row["roster_locked"]:
            await game_shared.respond(
                interaction, game_shared.error_message("roster_locked")
            )
            return

        master = load_master_data()

        result = add_battle_entry(
            self.guild_id,
            interaction.user.id,
            int(value),
            max_units=master.battle.max_units,
            max_total_cost=master.battle.max_total_cost,
        )

        if not result["ok"]:
            await game_shared.respond(
                interaction, entry_error_message(result["error"], detail=result)
            )
            return

        owned = get_owned_familiar(int(value))
        name = (
            battle_texts.FAMILIAR_LABEL_BOLD.format(
                name=_familiar_name(owned["familiar_id"]), level=owned["level"]
            )
            if owned
            else battle_texts.FAMILIAR_FALLBACK
        )
        total = len(get_battle_entries(self.guild_id))

        await game_shared.respond(
            interaction,
            battle_texts.ENTRY_ADDED.format(
                name=name, count=total, max_units=master.battle.max_units
            ),
        )


class RosterFamiliarRemoveView(PagedSelectView):
    """自分がセットした使い魔を1体解除する一時View。"""

    def __init__(self, guild_id: int, options: list[discord.SelectOption]) -> None:
        super().__init__(options, placeholder=battle_texts.ENTRY_REMOVE_PLACEHOLDER)

        self.guild_id = guild_id

    async def on_choice(self, interaction: discord.Interaction, value: str) -> None:
        await interaction.response.defer(ephemeral=True)

        result = remove_battle_entry(self.guild_id, interaction.user.id, int(value))
        if not result["ok"]:
            await game_shared.respond(
                interaction, entry_error_message(result["error"])
            )
            return

        master = load_master_data()
        total = len(get_battle_entries(self.guild_id))

        await game_shared.respond(
            interaction,
            battle_texts.ENTRY_REMOVED.format(
                count=total, max_units=master.battle.max_units
            ),
        )


class RosterFamiliarSwapView(PagedSelectView):
    """セット済みの1体を、別の使い魔へ入れ替える一時View（9.3節）。"""

    def __init__(
        self,
        options: list[discord.SelectOption],
        *,
        guild_id: int,
        removed_instance_id: int,
    ) -> None:
        super().__init__(options, placeholder=battle_texts.ENTRY_SWAP_PLACEHOLDER)

        self.guild_id = guild_id
        self.removed_instance_id = removed_instance_id

    async def on_choice(self, interaction: discord.Interaction, value: str) -> None:
        await interaction.response.defer(ephemeral=True)

        master = load_master_data()

        result = swap_battle_entry(
            self.guild_id,
            interaction.user.id,
            self.removed_instance_id,
            int(value),
            max_units=master.battle.max_units,
            max_total_cost=master.battle.max_total_cost,
        )

        if not result["ok"]:
            await game_shared.respond(
                interaction, entry_error_message(result["error"], detail=result)
            )
            return

        owned = get_owned_familiar(int(value))
        name = (
            battle_texts.FAMILIAR_LABEL_BOLD.format(
                name=_familiar_name(owned["familiar_id"]), level=owned["level"]
            )
            if owned
            else battle_texts.FAMILIAR_FALLBACK
        )

        await game_shared.respond(
            interaction, battle_texts.ENTRY_SWAPPED.format(name=name)
        )


class RosterFamiliarSwapTargetView(PagedSelectView):
    """入れ替える枠を選ぶ一時View（9.3節）。"""

    def __init__(
        self, options: list[discord.SelectOption], *, guild_id: int
    ) -> None:
        super().__init__(options, placeholder=battle_texts.ENTRY_SWAP_SLOT_PLACEHOLDER)

        self.guild_id = guild_id

    async def on_choice(self, interaction: discord.Interaction, value: str) -> None:
        await interaction.response.defer(ephemeral=True)

        options = build_settable_familiar_options(self.guild_id, interaction.user.id)
        if not options:
            issues = settable_blockers(self.guild_id, interaction.user.id)
            await game_shared.respond(
                interaction,
                battle_texts.ENTRY_NO_SWAPPABLE_HEADING + "\n" + "\n".join(issues),
            )
            return

        owned = get_owned_familiar(int(value))
        current = (
            battle_texts.FAMILIAR_LABEL.format(
                name=_familiar_name(owned["familiar_id"]), level=owned["level"]
            )
            if owned
            else battle_texts.ENTRY_SWAP_FALLBACK
        )

        await game_shared.respond(
            interaction,
            battle_texts.ENTRY_SWAP_PROMPT.format(familiar=current),
            view=RosterFamiliarSwapView(
                options, guild_id=self.guild_id, removed_instance_id=int(value)
            ),
        )


class RosterFamiliarActionView(discord.ui.View):
    """使い魔セットの追加・入れ替え・解除を選ぶ一時View（9節）。"""

    def __init__(
        self,
        *,
        guild_id: int,
        user_id: int,
        can_add: bool,
        can_remove: bool,
    ) -> None:
        super().__init__(timeout=300)

        self.guild_id = guild_id
        self.user_id = user_id
        self.add_familiar.disabled = not can_add
        self.swap_familiar.disabled = not can_remove
        self.remove_familiar.disabled = not can_remove

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await game_shared.respond(interaction, battle_texts.OWNER_ONLY)
            return False

        return True

    @discord.ui.button(
        label=battle_texts.ENTRY_BUTTON_ADD, style=discord.ButtonStyle.success
    )
    async def add_familiar(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        options = build_settable_familiar_options(self.guild_id, interaction.user.id)

        if not options:
            # 理由を具体的に伝える（ランク不足・全部セット済みなど）
            issues = settable_blockers(self.guild_id, interaction.user.id)
            await game_shared.respond(
                interaction,
                battle_texts.ENTRY_NO_SETTABLE_HEADING + "\n" + "\n".join(issues),
            )
            return

        await game_shared.respond(
            interaction,
            battle_texts.ENTRY_ADD_PROMPT,
            view=RosterFamiliarAddView(self.guild_id, options),
        )

    @discord.ui.button(
        label=battle_texts.ENTRY_BUTTON_SWAP, style=discord.ButtonStyle.primary
    )
    async def swap_familiar(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        options = build_current_entry_options(self.guild_id, interaction.user.id)

        if not options:
            await game_shared.respond(interaction, battle_texts.ENTRY_NO_SWAPPABLE)
            return

        await game_shared.respond(
            interaction,
            battle_texts.ENTRY_SWAP_SLOT_PROMPT,
            view=RosterFamiliarSwapTargetView(options, guild_id=self.guild_id),
        )

    @discord.ui.button(
        label=battle_texts.ENTRY_BUTTON_REMOVE, style=discord.ButtonStyle.secondary
    )
    async def remove_familiar(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        options = build_current_entry_options(self.guild_id, interaction.user.id)

        if not options:
            await game_shared.respond(interaction, battle_texts.ENTRY_NO_REMOVABLE)
            return

        await game_shared.respond(
            interaction,
            battle_texts.ENTRY_REMOVE_PROMPT,
            view=RosterFamiliarRemoveView(self.guild_id, options),
        )


def familiar_sort_key(familiar_id: str, level: int) -> tuple[int, int, str]:
    """ランク順 → レベル順に並べるための並び替えキーを返す。

    強いランクが先、同じランクなら高いレベルが先です。
    """

    master = load_master_data()

    familiar = master.get_familiar(familiar_id)
    order = list(master.familiar.rank_order)

    try:
        strength = order.index(familiar.rank) if familiar else -1
    except ValueError:
        strength = -1

    return (-strength, -int(level), familiar.name if familiar else familiar_id)


def familiar_option(
    instance_id: int,
    familiar_id: str,
    level: int,
    *,
    prefix: str = "",
    count: int = 1,
) -> discord.SelectOption | None:
    """所有使い魔をセレクトの選択肢へ変換する。

    ランクは絵文字（アイコン）と先頭文字の両方で示します。先頭文字があると、
    セレクトの見出しに「S〜Aランク」のような範囲を出せます。``count`` が2以上の
    ときは「×3」を付け、同じ使い魔を何度も並べません。
    """

    master = load_master_data()

    familiar = master.get_familiar(familiar_id)
    if familiar is None:
        return None

    stats = master.level_stats(familiar_id, level)
    description = (
        battle_texts.OPTION_STATS.format(
            hp=stats.max_hp,
            atk=stats.atk,
            speed=stats.speed,
            cost=familiar.cost,
        )
        if stats
        else familiar.description
    )

    label = battle_texts.OPTION_LABEL.format(
        prefix=prefix, rank=familiar.rank, name=familiar.name, level=level
    )
    if count > 1:
        label = battle_texts.OPTION_LABEL_COUNT.format(label=label, count=count)

    return discord.SelectOption(
        label=label[:100],
        description=description[:100],
        value=str(instance_id),
        emoji=game_shared.RANK_EMOJIS.get(familiar.rank),
    )


def grouped_familiar_options(
    rows: list[dict], *, prefix: str = ""
) -> list[discord.SelectOption]:
    """所有使い魔を「同じ種類・同じレベル」でまとめ、ランク順 → レベル順で返す。

    同じ使い魔が複数あっても選択肢は1つにまとめ、体数を「×3」で示します。
    どの個体を選んでも結果は同じなので、代表として最小の個体IDを使います。
    """

    groups: dict[tuple[str, int], dict] = {}

    for row in rows:
        familiar_id = str(row["familiar_id"])
        level = int(row["level"])
        instance_id = int(row["instance_id"])
        key = (familiar_id, level)

        group = groups.get(key)
        if group is None:
            groups[key] = {
                "familiar_id": familiar_id,
                "level": level,
                "instance_id": instance_id,
                "count": 1,
            }
            continue

        group["count"] += 1
        group["instance_id"] = min(group["instance_id"], instance_id)

    found: list[tuple[tuple[int, int, str], discord.SelectOption]] = []

    for group in groups.values():
        option = familiar_option(
            group["instance_id"],
            group["familiar_id"],
            group["level"],
            prefix=prefix,
            count=group["count"],
        )
        if option is not None:
            found.append(
                (familiar_sort_key(group["familiar_id"], group["level"]), option)
            )

    found.sort(key=lambda item: item[0])
    return [option for _, option in found]


def settable_familiars(guild_id: int, user_id: int) -> list[dict]:
    """まだセットしていない、使役できる所有使い魔を返す。"""

    master = load_master_data()

    rank_info = game_shared.get_player_rank_info(user_id)
    if rank_info is None:
        return []

    already_set = {
        int(entry["instance_id"]) for entry in get_battle_entries(guild_id)
    }

    found: list[dict] = []

    for owned in get_owned_familiars(user_id):
        if int(owned["instance_id"]) in already_set:
            continue

        familiar = master.get_familiar(owned["familiar_id"])
        if familiar is None:
            continue

        if not master.can_use_rank(
            rank_info["player_rank"],
            familiar.rank,
            is_sub_manager=rank_info["is_sub_manager"],
        ):
            continue

        found.append(dict(owned))

    return found


def settable_blockers(guild_id: int, user_id: int) -> list[str]:
    """セットできる使い魔が無い理由を、具体的に並べて返す（10.3節）。

    「セットできる使い魔がありません」だけでは、ランクが足りないのか、
    すでに全部セットしたのか、そもそも持っていないのかが分かりません。
    """

    master = load_master_data()

    rank_info = game_shared.get_player_rank_info(user_id)
    if rank_info is None:
        return [battle_texts.RANK_UNKNOWN]

    usable = master.usable_ranks(
        rank_info["player_rank"], is_sub_manager=rank_info["is_sub_manager"]
    )
    if not usable:
        return [
            battle_texts.NO_CLASS_ROLE,
            battle_texts.NO_CLASS_ROLE_HINT,
        ]

    owned = get_owned_familiars(user_id)
    if not owned:
        return [battle_texts.NO_OWNED_FAMILIAR]

    already_set = {
        int(entry["instance_id"]) for entry in get_battle_entries(guild_id)
    }
    remaining = [
        row for row in owned if int(row["instance_id"]) not in already_set
    ]

    if not remaining:
        return [
            battle_texts.ALL_ALREADY_SET,
            battle_texts.ALL_ALREADY_SET_HINT,
        ]

    # 残っているのに候補が無い＝使役できるランクが足りない
    ranks = sorted(
        {
            familiar.rank
            for row in remaining
            if (familiar := master.get_familiar(row["familiar_id"])) is not None
        }
    )
    return [
        battle_texts.RANK_TOO_LOW.format(
            player_rank=rank_info["player_rank"], ranks="・".join(ranks)
        ),
        battle_texts.RANK_USABLE_HINT.format(ranks="・".join(usable)),
    ]


def build_settable_familiar_options(
    guild_id: int, user_id: int
) -> list[discord.SelectOption]:
    """まだセットしていない、使役可能な所有使い魔の選択肢を作る。

    同じ使い魔が複数ある場合は1つにまとめ、体数を「×3」で示します。
    """

    return grouped_familiar_options(settable_familiars(guild_id, user_id))


def build_entry_overview(
    guild_id: int, *, viewer_id: int, assigned: int
) -> str:
    """出場する使い魔を、他のメンバー分も含めてまとめて表示する（9節）。"""

    master = load_master_data()

    roster = get_battle_roster(guild_id)
    entries = get_battle_entries(guild_id)
    mine = [entry for entry in entries if int(entry["user_id"]) == viewer_id]

    by_user: dict[int, list[dict]] = {}
    for entry in entries:
        by_user.setdefault(int(entry["user_id"]), []).append(entry)

    lines = [
        battle_texts.ENTRY_OVERVIEW_HEADING,
        game_shared.item_line(
            battle_texts.LABEL_GUILD_TOTAL,
            battle_texts.ENTRY_GUILD_TOTAL.format(
                count=len(entries), max_units=master.battle.max_units
            ),
        ),
        game_shared.item_line(
            battle_texts.LABEL_YOUR_SLOT,
            battle_texts.ENTRY_YOUR_SLOT.format(
                count=len(mine), assigned=assigned
            ),
        ),
        "",
    ]

    for member in roster:
        user_id = int(member["user_id"])
        count = int(member["familiar_count"] or 0)
        owned_entries = by_user.get(user_id, [])

        is_viewer = user_id == viewer_id
        mark = (
            battle_texts.ENTRY_MARK_YOU if is_viewer else battle_texts.ENTRY_MARK_OTHER
        )
        suffix = battle_texts.ENTRY_SUFFIX_YOU if is_viewer else ""
        lines.append(
            battle_texts.ENTRY_MEMBER_LINE.format(
                mark=mark,
                user_id=user_id,
                suffix=suffix,
                count=len(owned_entries),
                assigned=count,
            )
        )

        if not owned_entries:
            lines.append(battle_texts.ENTRY_UNSET)
            continue

        for entry in owned_entries:
            owned = get_owned_familiar(int(entry["instance_id"]))
            if owned is None:
                lines.append(battle_texts.ENTRY_NOT_OWNED)
                continue

            familiar_id = str(owned["familiar_id"])
            level = int(owned["level"])
            familiar = master.get_familiar(familiar_id)
            stats = master.level_stats(familiar_id, level)

            detail = (
                battle_texts.FAMILIAR_STATS.format(
                    hp=stats.max_hp, atk=stats.atk, speed=stats.speed
                )
                if stats
                else battle_texts.DASH
            )
            rank = game_shared.rank_label(familiar.rank) if familiar else "?"
            cost = familiar.cost if familiar else battle_texts.DASH

            lines.append(
                battle_texts.ENTRY_FAMILIAR_LINE.format(
                    rank=rank,
                    name=_familiar_name(familiar_id),
                    level=level,
                    cost=cost,
                )
            )
            lines.append(battle_texts.ENTRY_DETAIL_LINE.format(detail=detail))

    total_cost = 0
    for entry in entries:
        owned = get_owned_familiar(int(entry["instance_id"]))
        familiar = master.get_familiar(owned["familiar_id"]) if owned else None
        if familiar is not None:
            total_cost += familiar.cost

    cap = master.battle.max_total_cost
    cost_text = (
        f"{total_cost}"
        if cap <= 0
        else battle_texts.ENTRY_COST_WITH_LIMIT.format(cost=total_cost, limit=cap)
    )
    if cap > 0 and total_cost > cap:
        cost_text = battle_texts.ENTRY_COST_OVER.format(cost=cost_text)

    lines.append("")
    lines.append(game_shared.item_line(battle_texts.LABEL_TOTAL_COST, cost_text))
    if cap > 0:
        lines.append(battle_texts.COST_TABLE_NOTE)
    lines.append(battle_texts.ENTRY_OVERVIEW_FOOTER)

    return "\n".join(lines)[:1900]


def build_current_entry_options(
    guild_id: int, user_id: int
) -> list[discord.SelectOption]:
    """自分がセット済みの使い魔の選択肢を、ランク順 → レベル順で作る。"""

    found: list[tuple[tuple[int, int, str], discord.SelectOption]] = []

    for entry in get_battle_entries(guild_id):
        if int(entry["user_id"]) != user_id:
            continue

        owned = get_owned_familiar(int(entry["instance_id"]))
        if owned is None:
            continue

        familiar_id = str(owned["familiar_id"])
        level = int(owned["level"])

        option = familiar_option(
            int(entry["instance_id"]),
            familiar_id,
            level,
            prefix=battle_texts.ENTRY_SLOT_PREFIX.format(slot=entry["entry_slot"]),
        )
        if option is not None:
            found.append((familiar_sort_key(familiar_id, level), option))

    found.sort(key=lambda item: item[0])
    return [option for _, option in found]


def roster_is_set(guild_id: int) -> bool:
    """出場者セットが済んでいるか（12節の前提条件）。"""

    return bool(get_battle_roster(guild_id))


def entry_error_message(
    code: str | None,
    *,
    limit: int | None = None,
    detail: dict | None = None,
) -> str:
    """使い魔セット固有のエラーを日本語へ変換する。"""

    master = load_master_data()
    detail = detail or {}

    if limit is None:
        limit = detail.get("limit")

    if code == "entries_full":
        return battle_texts.ENTRY_ERROR_FULL.format(
            max_units=master.battle.max_units
        )
    if code == "cost_over":
        current = int(detail.get("current_cost", 0))
        adding = int(detail.get("adding_cost", 0))
        cap = int(detail.get("max_total_cost", master.battle.max_total_cost))
        return battle_texts.ENTRY_ERROR_COST_OVER.format(
            current=current, adding=adding, limit=cap
        )
    if code == "member_limit":
        return battle_texts.ENTRY_ERROR_MEMBER_LIMIT.format(
            limit=limit if limit is not None else 0
        )
    if code == "already_set":
        return battle_texts.ENTRY_ERROR_ALREADY_SET
    if code == "not_set":
        return battle_texts.ENTRY_ERROR_NOT_SET

    return game_shared.error_message(code)


# ==================================================
# バトルレートの選択（12節）
# ==================================================
def bet_rate_option(rate) -> discord.SelectOption:
    """レート1件をセレクトの選択肢へ変換する。

    説明欄には「いくら賭けるのか」と「勝った側が受け取ること」を必ず出します。
    選ぶ前に負担と見返りが分かるようにするためです。
    """

    return discord.SelectOption(
        label=rate.name[:100],
        description=battle_texts.BET_RATE_OPTION_DESCRIPTION.format(
            coin=game_shared.format_coin(rate.coin)
        )[:100],
        value=rate.rate_id,
    )


class BetRateSelectView(discord.ui.View):
    """バトルのレート（ベット額）を選ぶ一時View（12節）。

    選んだ時点で確定します。確認ボタンを挟まないのは、レートを選ぶこと自体が
    「この額で対戦相手を探す」という意思表示だからです。
    """

    def __init__(self, *, guild_row: dict, action: str, on_choose) -> None:
        super().__init__(timeout=EPHEMERAL_TIMEOUT)

        self.guild_row = guild_row
        self._on_choose = on_choose
        self._running = False

        select = discord.ui.Select(
            placeholder=battle_texts.BET_RATE_PLACEHOLDER.format(action=action)[:150],
            min_values=1,
            max_values=1,
            options=[bet_rate_option(rate) for rate in service.bet_rates()],
        )
        select.callback = self._select_callback
        self.add_item(select)
        self._select = select

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != int(self.guild_row["master_id"]):
            await game_shared.respond(
                interaction, game_shared.error_message("not_master")
            )
            return False

        return True

    async def _select_callback(self, interaction: discord.Interaction) -> None:
        if self._running:
            await game_shared.respond(interaction, common_texts.ALREADY_RUNNING)
            return

        rate = service.bet_rate(self._select.values[0])
        if rate is None:
            await game_shared.respond(interaction, battle_texts.BET_RATE_UNAVAILABLE)
            return

        self._running = True

        # 連打による二重投稿を防ぐため、選んだ直後に操作不能にする
        for item in self.children:
            item.disabled = True

        try:
            await interaction.response.edit_message(view=self)
        except discord.HTTPException:
            logger.warning("レート選択の更新に失敗しました")

        try:
            await self._on_choose(interaction, rate)
        finally:
            self.stop()


def bet_rate_guide(next_step: str) -> str:
    """レート選択の案内文を作る。``next_step`` は選んだ直後に起きること。"""

    return battle_texts.BET_RATE_GUIDE.format(next_step=next_step)


def opponent_options(guild_id: int) -> list[discord.SelectOption]:
    """バトル申請を送れる相手ギルドの選択肢を作る。"""

    options: list[discord.SelectOption] = []

    for candidate in get_active_guilds():
        candidate_id = int(candidate["guild_id"])
        if candidate_id == guild_id:
            continue
        if get_battle_lock(candidate_id) is not None:
            continue

        options.append(
            discord.SelectOption(
                label=candidate["name"][:100],
                description=battle_texts.OPPONENT_OPTION_RECORD.format(
                    wins=candidate["wins"],
                    losses=candidate["losses"],
                    draws=candidate["draws"],
                )[:100],
                value=str(candidate_id),
            )
        )

    return options


def bet_confirmation(guild_id: int, bet_coin: int) -> str:
    """決めたベット額を、1人あたりの分担額まで含めて説明する。"""

    roster = get_battle_roster(guild_id)

    return "\n".join(
        [
            game_shared.item_line(
                battle_texts.LABEL_BET, game_shared.format_coin(bet_coin)
            ),
            battle_texts.BET_SHARE_LINE.format(
                notice=service.bet_share_notice(bet_coin, len(roster))
            ),
            battle_texts.BET_TRANSFER_NOTE,
        ]
    )


# ==================================================
# バトル申請（12.1節）
# ==================================================
class OpponentSelectView(PagedSelectView):
    """バトル申請の相手ギルドを選ぶ一時View。"""

    def __init__(
        self,
        guild_row: dict,
        options: list[discord.SelectOption],
        *,
        bet_coin: int,
    ) -> None:
        super().__init__(options, placeholder=battle_texts.OPPONENT_SELECT_PLACEHOLDER)

        self.guild_row = guild_row
        self.bet_coin = bet_coin

    async def on_choice(self, interaction: discord.Interaction, value: str) -> None:
        await interaction.response.defer(ephemeral=True)

        guild_id = int(self.guild_row["guild_id"])
        guild_row = get_guild(guild_id)
        if guild_row is None or guild_row["master_id"] != interaction.user.id:
            await game_shared.respond(interaction, game_shared.error_message("not_master"))
            return

        opponent_id = int(value)
        opponent_row = get_guild(opponent_id)
        if opponent_row is None or opponent_row["status"] != "active":
            await game_shared.respond(
                interaction, game_shared.error_message("guild_not_found")
            )
            return

        result = create_battle_request(
            guild_id, opponent_id, bet_coin=self.bet_coin
        )
        if not result["ok"]:
            await game_shared.respond(
                interaction, game_shared.error_message(result["error"])
            )
            return

        request_id = int(result["request_id"])
        discord_guild = interaction.guild
        channel = None
        if discord_guild is not None and opponent_row.get("master_text_channel_id"):
            channel = discord_guild.get_channel(
                int(opponent_row["master_text_channel_id"])
            )

        if not isinstance(channel, discord.TextChannel):
            resolve_battle_request(request_id, "cancelled")
            await game_shared.respond(
                interaction, battle_texts.REQUEST_NO_OPPONENT_CHANNEL
            )
            return

        embed = discord.Embed(
            title=battle_texts.REQUEST_EMBED_TITLE,
            description="\n".join(
                [
                    battle_texts.REQUEST_EMBED_INTRO.format(
                        guild_name=guild_row["name"]
                    ),
                    battle_texts.REQUEST_EMBED_NOTE,
                    "",
                    game_shared.item_line(
                        battle_texts.LABEL_REQUEST_FROM, guild_row["name"]
                    ),
                    game_shared.item_line(
                        battle_texts.LABEL_REQUEST_TO, opponent_row["name"]
                    ),
                    game_shared.item_line(
                        battle_texts.LABEL_RATE,
                        service.bet_rate_label(self.bet_coin) or battle_texts.DASH,
                    ),
                    game_shared.item_line(
                        battle_texts.LABEL_BET,
                        battle_texts.BET_PER_GUILD.format(
                            coin=game_shared.format_coin(self.bet_coin)
                        ),
                    ),
                    "",
                    battle_texts.REQUEST_EMBED_FOOTER,
                ]
            ),
            color=config.COLOR_PURPLE,
        )

        message = await channel.send(embed=embed, view=BattleRequestView())
        set_battle_request_message(request_id, channel.id, message.id)

        await game_shared.respond(
            interaction,
            battle_texts.REQUEST_SENT.format(
                guild_name=opponent_row["name"],
                bet=bet_confirmation(guild_id, self.bet_coin),
            ),
        )


class RequestCancelView(ConfirmView):
    """送信済みバトル申請を取り消す一時View。"""

    def __init__(self, request_id: int) -> None:
        super().__init__(confirm_label=battle_texts.REQUEST_CANCEL_BUTTON)

        self.request_id = request_id

    async def on_confirm(self, interaction: discord.Interaction) -> None:
        request_row = get_battle_request(self.request_id)
        if request_row is None:
            await game_shared.respond(interaction, game_shared.error_message("not_pending"))
            return

        guild_row = get_guild(int(request_row["from_guild_id"]))
        if guild_row is None or guild_row["master_id"] != interaction.user.id:
            await game_shared.respond(interaction, game_shared.error_message("not_master"))
            return

        result = resolve_battle_request(self.request_id, "cancelled")
        if not result["ok"]:
            await game_shared.respond(
                interaction, game_shared.error_message(result["error"])
            )
            return

        await _delete_request_message(interaction.client, result)

        opponent_row = get_guild(int(request_row["to_guild_id"]))
        if opponent_row is not None:
            # 取消はメンバー全員に関わるためギルドTCへ送る
            await _notify_guild_channel(
                interaction.client,
                opponent_row,
                title=battle_texts.REQUEST_CANCELLED_TITLE,
                description=battle_texts.REQUEST_CANCELLED_BODY.format(
                    guild_name=guild_row["name"]
                ),
                color=config.COLOR_GREY,
                channel_key="guild_text_channel_id",
            )

        await game_shared.respond(interaction, battle_texts.REQUEST_CANCELLED_DONE)


async def _delete_posted_message(
    bot: discord.Client, payload: dict, *, label: str
) -> None:
    """役目を終えた投稿（申請Embed・募集Embed）を削除する。

    ``payload`` は ``channel_id`` と ``message_id`` を持つDBの戻り値です。
    """

    channel_id = payload.get("channel_id")
    message_id = payload.get("message_id")
    if not channel_id or not message_id:
        return

    channel = bot.get_channel(int(channel_id))
    if not isinstance(channel, discord.TextChannel):
        return

    try:
        message = await channel.fetch_message(int(message_id))
        await message.delete()
    except discord.NotFound:
        return
    except (discord.HTTPException, discord.Forbidden):
        logger.warning(f"{label}の削除に失敗しました: message_id={message_id}")


async def _delete_request_message(bot: discord.Client, payload: dict) -> None:
    """回答済み・取消済みの申請Embedを削除する。"""

    await _delete_posted_message(bot, payload, label="バトル申請Embed")


async def _notify_guild_channel(
    bot: discord.Client,
    guild_row: dict,
    *,
    title: str,
    description: str,
    color: int,
    channel_key: str = "master_text_channel_id",
) -> None:
    """ギルドの指定チャンネルへ通知を送る。

    既定はギルドマスター専用TCですが、メンバー全員へ知らせたい内容は
    ``channel_key="guild_text_channel_id"`` でギルドTCへ送ります。
    """

    channel_id = guild_row.get(channel_key)
    if not channel_id:
        return

    channel = bot.get_channel(int(channel_id))
    if not isinstance(channel, discord.TextChannel):
        return

    embed = discord.Embed(title=title, description=description, color=color)

    try:
        await channel.send(embed=embed)
    except (discord.HTTPException, discord.Forbidden):
        logger.warning(f"ギルドへの通知に失敗しました: channel_id={channel_id}")


# ==================================================
# 公開バトル募集（12.2節）
# ==================================================
class RecruitmentCancelView(ConfirmView):
    """公開バトル募集を取り消す一時View。"""

    def __init__(self, recruitment_id: int) -> None:
        super().__init__(confirm_label=battle_texts.RECRUIT_CANCEL_BUTTON)

        self.recruitment_id = recruitment_id

    async def on_confirm(self, interaction: discord.Interaction) -> None:
        recruitment = get_battle_recruitment(self.recruitment_id)
        if recruitment is None:
            await game_shared.respond(interaction, game_shared.error_message("not_open"))
            return

        guild_row = get_guild(int(recruitment["guild_id"]))
        if guild_row is None or guild_row["master_id"] != interaction.user.id:
            await game_shared.respond(interaction, game_shared.error_message("not_master"))
            return

        result = resolve_battle_recruitment(self.recruitment_id, "cancelled")
        if not result["ok"]:
            await game_shared.respond(
                interaction, game_shared.error_message(result["error"])
            )
            return

        # 取り消した募集は残しても申し込めないため、投稿ごと片づける
        await _delete_recruitment_message(interaction.client, result)
        await game_shared.respond(interaction, battle_texts.RECRUIT_CANCELLED_DONE)


def recruitment_embed(
    guild_row: dict, *, state_text: str, bet_coin: int | None = None
) -> discord.Embed:
    """12.2節の募集Embedを組み立てる。"""

    master = load_master_data()
    amount = service.default_bet_coin() if bet_coin is None else int(bet_coin)
    rate_name = service.bet_rate_label(amount)

    return discord.Embed(
        title=battle_texts.RECRUIT_EMBED_TITLE,
        description="\n".join(
            [
                battle_texts.RECRUIT_EMBED_INTRO.format(
                    guild_name=guild_row["name"]
                ),
                "",
                game_shared.item_line(
                    battle_texts.LABEL_MEMBER_RANGE,
                    battle_texts.RECRUIT_MEMBER_RANGE.format(
                        min_members=master.battle.min_members,
                        max_members=master.battle.max_members,
                    ),
                ),
                game_shared.item_line(
                    battle_texts.LABEL_RATE, rate_name or battle_texts.DASH
                ),
                game_shared.item_line(
                    battle_texts.LABEL_BET,
                    battle_texts.BET_PER_GUILD.format(
                        coin=game_shared.format_coin(amount)
                    ),
                ),
                game_shared.item_line(battle_texts.LABEL_STATE, state_text),
                "",
                battle_texts.RECRUIT_EMBED_FOOTER,
            ]
        ),
        color=config.COLOR_PURPLE,
    )


async def _delete_recruitment_message(bot: discord.Client, payload: dict) -> None:
    """取り消した募集の投稿を削除する（12.2節）。"""

    await _delete_posted_message(bot, payload, label="募集Embed")


async def close_recruitment_message(
    bot: discord.Client, payload: dict, *, state_text: str
) -> None:
    """募集Embedを残したまま状態を更新し、ボタンを無効化する（12.2節）。"""

    channel_id = payload.get("channel_id")
    message_id = payload.get("message_id")
    if not channel_id or not message_id:
        return

    channel = bot.get_channel(int(channel_id))
    if not isinstance(channel, discord.TextChannel):
        return

    guild_row = get_guild(int(payload["guild_id"]))
    if guild_row is None:
        return

    view = BattleRecruitmentView()
    for item in view.children:
        item.disabled = True

    try:
        message = await channel.fetch_message(int(message_id))
        await message.edit(
            embed=recruitment_embed(
                guild_row,
                state_text=state_text,
                bet_coin=payload.get("bet_coin"),
            ),
            view=view,
        )
    except discord.NotFound:
        return
    except (discord.HTTPException, discord.Forbidden):
        logger.warning(f"募集Embedの更新に失敗しました: message_id={message_id}")


# ==================================================
# バトル専用チャンネルの操作（16節・17節・19節）
# ==================================================
class ActionChoiceView(discord.ui.View):
    """スキルをキャンセルした後に行動を選び直す一時View（19.2節）。"""

    def __init__(self, battle_id: int, unit_id: int) -> None:
        super().__init__(timeout=EPHEMERAL_TIMEOUT)

        self.battle_id = battle_id
        self.unit_id = unit_id

        skill = discord.ui.Button(
            label=battle_texts.BUTTON_SKILL, style=discord.ButtonStyle.primary
        )
        skill.callback = self._skill_callback
        self.add_item(skill)

        attack = discord.ui.Button(
            label=battle_texts.BUTTON_ATTACK, style=discord.ButtonStyle.danger
        )
        attack.callback = self._attack_callback
        self.add_item(attack)

    async def _skill_callback(self, interaction: discord.Interaction) -> None:
        await open_skill_selection(interaction)

    async def _attack_callback(self, interaction: discord.Interaction) -> None:
        await open_attack_selection(interaction)


class AttackTargetView(PagedSelectView):
    """通常攻撃の対象を選ぶ一時View。"""

    def __init__(
        self,
        battle_id: int,
        unit_id: int,
        expected_seq: int,
        options: list[discord.SelectOption],
    ) -> None:
        super().__init__(options, placeholder=battle_texts.ATTACK_TARGET_PLACEHOLDER)

        self.battle_id = battle_id
        self.unit_id = unit_id
        self.expected_seq = expected_seq

    async def on_choice(self, interaction: discord.Interaction, value: str) -> None:
        await interaction.response.defer(ephemeral=True)

        action = BattleAction(
            action_type=ACTION_ATTACK,
            actor_unit_id=self.unit_id,
            target_unit_id=int(value),
        )
        # 攻撃の結果はバトル専用チャンネルの行動ログへ出るため、
        # 実行者だけへ届く「実行しました」の控えは出さない。
        await _apply_and_report(
            interaction,
            self.battle_id,
            action,
            expected_seq=self.expected_seq,
            success_message=None,
        )


class SkillSelectView(PagedSelectView):
    """使用するアクティブスキルを選ぶ一時View。"""

    def __init__(
        self,
        battle_id: int,
        unit_id: int,
        expected_seq: int,
        options: list[discord.SelectOption],
    ) -> None:
        super().__init__(options, placeholder=battle_texts.SKILL_SELECT_PLACEHOLDER)

        self.battle_id = battle_id
        self.unit_id = unit_id
        self.expected_seq = expected_seq

    async def on_choice(self, interaction: discord.Interaction, value: str) -> None:
        master = load_master_data()
        skill = master.get_skill(value)
        if skill is None:
            await game_shared.respond(interaction, battle_texts.SKILL_NOT_FOUND)
            return

        flow = SkillTargetFlow(
            battle_id=self.battle_id,
            unit_id=self.unit_id,
            expected_seq=self.expected_seq,
            skill=skill,
        )
        await flow.advance(interaction)


class SkillTargetFlow:
    """スキルの対象選択を、グループごとに順番へ分解して進める。

    ロキ「虚実反転」のように対象グループが2つあるスキルや、スルト「終末の進軍」の
    ように同じ対象を複数回選べるグループ（``allow_duplicate``）にも対応します。
    """

    def __init__(self, *, battle_id: int, unit_id: int, expected_seq: int, skill) -> None:
        self.battle_id = battle_id
        self.unit_id = unit_id
        self.expected_seq = expected_seq
        self.skill = skill
        self.groups = list(skill.targets)
        self.group_index = 0
        self.pick_index = 0
        self.selections: dict[str, list[int]] = {}

    # ==================================================
    # 進行
    # ==================================================
    def _current_group(self):
        if self.group_index >= len(self.groups):
            return None
        return self.groups[self.group_index]

    def _record(self, group, unit_ids: list[int]) -> None:
        """選んだ対象を記録する。

        前段階のSelectは利用者の画面に残るため、同じグループを選び直せます。
        追加ではなく現在位置への上書きにして、選択数が ``group.count`` を
        超えないようにします（超えると使用確定時に検証エラーになり、
        スキルを使えなくなる）。
        """

        if group.allow_duplicate:
            # 1体ずつ複数回選ぶスキル（終末の進軍）は、今の順番から入れ直す
            chosen = list(self.selections.get(group.key, []))[: self.pick_index]
            chosen.extend(unit_ids)
            self.selections[group.key] = chosen
            return

        self.selections[group.key] = list(unit_ids)

    async def advance(self, interaction: discord.Interaction) -> None:
        """次の対象選択、または最終確認へ進む。"""

        group = self._current_group()
        if group is None:
            await self._show_confirm(interaction)
            return

        state = load_battle_state(self.battle_id)
        unit = state.unit(self.unit_id) if state else None
        if state is None or unit is None:
            await game_shared.respond(interaction, battle_texts.BATTLE_LOAD_ERROR)
            return

        candidates = battle_engine.selectable_targets(state, unit, group)
        if not candidates:
            await game_shared.respond(interaction, battle_texts.NO_SELECTABLE_TARGET)
            return

        if group.allow_duplicate:
            need = 1
            label = battle_texts.SKILL_TARGET_LABEL_EACH.format(
                skill=self.skill.name,
                target=group.display_label,
                index=self.pick_index + 1,
            )
        else:
            need = min(group.count, len(candidates))
            label = battle_texts.SKILL_TARGET_LABEL.format(
                skill=self.skill.name,
                target=group.display_label,
                count=need,
            )

        options = [_unit_option(candidate, state) for candidate in candidates]
        view = SkillTargetSelectView(self, group, options, need=need, label=label)

        await game_shared.respond(interaction, label, view=view)

    async def submit_group(
        self, interaction: discord.Interaction, group, unit_ids: list[int]
    ) -> None:
        """1回分の選択を記録し、次の段階へ進む。"""

        self._record(group, unit_ids)

        if group.allow_duplicate:
            self.pick_index += 1
            if self.pick_index < group.count:
                await self.advance(interaction)
                return

        self.group_index += 1
        self.pick_index = 0
        await self.advance(interaction)

    async def _show_confirm(self, interaction: discord.Interaction) -> None:
        state = load_battle_state(self.battle_id)
        if state is None:
            await game_shared.respond(interaction, battle_texts.BATTLE_LOAD_ERROR)
            return

        lines = [self.skill.description]
        for group in self.groups:
            names = [
                battle_embed.unit_name(state, unit_id)
                for unit_id in self.selections.get(group.key, [])
            ]
            if names:
                lines.append(
                    game_shared.item_line(
                        group.display_label, "・".join(names)
                    )
                )

        embed = discord.Embed(
            title=battle_texts.SKILL_CONFIRM_TITLE.format(skill=self.skill.name),
            description="\n".join(lines)[:2000],
            color=battle_embed.COLOR_SKILL,
        )

        await game_shared.respond(
            interaction, embed=embed, view=SkillConfirmView(self)
        )

    async def execute(self, interaction: discord.Interaction) -> None:
        """最終確認後にスキルを実行する。"""

        action = BattleAction(
            action_type=ACTION_SKILL,
            actor_unit_id=self.unit_id,
            skill_id=self.skill.skill_id,
            selections={
                key: tuple(values) for key, values in self.selections.items()
            },
        )

        applied = await _apply_and_report(
            interaction,
            self.battle_id,
            action,
            expected_seq=self.expected_seq,
            success_message=battle_texts.SKILL_USED.format(skill=self.skill.name),
        )

        if not applied or self.skill.consumes_attack:
            return

        # 攻撃権を消費しないスキルの後は、続けて攻撃を選ばせる（19.2節）
        await open_attack_selection(
            interaction, notice=battle_texts.ATTACK_AFTER_SKILL_PROMPT
        )


class SkillTargetSelectView(discord.ui.View):
    """スキルの対象を選ぶ一時View。"""

    def __init__(
        self,
        flow: SkillTargetFlow,
        group,
        options: list[discord.SelectOption],
        *,
        need: int,
        label: str,
    ) -> None:
        super().__init__(timeout=EPHEMERAL_TIMEOUT)

        self.flow = flow
        self.group = group

        select = discord.ui.Select(
            placeholder=label[:150],
            min_values=need,
            max_values=need,
            options=options[:SELECT_LIMIT],
        )
        select.callback = self._select_callback
        self.add_item(select)
        self._select = select

        cancel = discord.ui.Button(
            label=battle_texts.CANCEL_BUTTON, style=discord.ButtonStyle.secondary
        )
        cancel.callback = self._cancel_callback
        self.add_item(cancel)

    async def _select_callback(self, interaction: discord.Interaction) -> None:
        unit_ids = [int(value) for value in self._select.values]
        await self.flow.submit_group(interaction, self.group, unit_ids)

    async def _cancel_callback(self, interaction: discord.Interaction) -> None:
        await game_shared.respond(
            interaction,
            battle_texts.SKILL_CANCELLED,
            view=ActionChoiceView(self.flow.battle_id, self.flow.unit_id),
        )


class SkillConfirmView(discord.ui.View):
    """スキル使用の最終確認（19.2節）。キャンセルで行動選択へ戻る。"""

    def __init__(self, flow: SkillTargetFlow) -> None:
        super().__init__(timeout=EPHEMERAL_TIMEOUT)

        self.flow = flow

        confirm = discord.ui.Button(
            label=battle_texts.SKILL_CONFIRM_BUTTON, style=discord.ButtonStyle.primary
        )
        confirm.callback = self._confirm_callback
        self.add_item(confirm)

        cancel = discord.ui.Button(
            label=battle_texts.CANCEL_BUTTON, style=discord.ButtonStyle.secondary
        )
        cancel.callback = self._cancel_callback
        self.add_item(cancel)

    async def _confirm_callback(self, interaction: discord.Interaction) -> None:
        for item in self.children:
            item.disabled = True

        await interaction.response.defer(ephemeral=True)
        await self.flow.execute(interaction)

    async def _cancel_callback(self, interaction: discord.Interaction) -> None:
        await game_shared.respond(
            interaction,
            battle_texts.SKILL_CANCELLED,
            view=ActionChoiceView(self.flow.battle_id, self.flow.unit_id),
        )


async def open_attack_selection(
    interaction: discord.Interaction, *, notice: str | None = None
) -> None:
    """通常攻撃の対象選択を開く。"""

    channel = interaction.channel
    if channel is None:
        await game_shared.respond(interaction, common_texts.CHANNEL_ERROR)
        return

    battle_row, state, unit, error = load_actor(channel.id, interaction.user.id)
    if error is not None:
        await game_shared.respond(interaction, error)
        return

    choices = battle_engine.attack_target_choices(state, unit)
    if not choices:
        await game_shared.respond(interaction, battle_texts.NO_ATTACK_TARGET)
        return

    options = [_unit_option(choice, state) for choice in choices]
    view = AttackTargetView(
        int(battle_row["battle_id"]), unit.battle_unit_id, state.action_seq, options
    )

    # 自分の攻撃力と、その内訳（バフが乗っているか）を先に見せる
    attack_power = effects.attack_atk(state, unit)
    lines = [notice or battle_texts.ATTACK_PROMPT, ""]
    lines.append(
        game_shared.item_line(
            battle_texts.LABEL_YOUR_ATK,
            f"**{battle_embed.stat_with_delta(attack_power, unit.base_atk)}**",
        )
    )

    marks = _effect_marks(state, unit)
    lines.append(
        game_shared.item_line(
            battle_texts.LABEL_EFFECTS, marks or battle_texts.EFFECT_NONE
        )
    )

    await game_shared.respond(interaction, "\n".join(lines), view=view)


async def open_skill_selection(interaction: discord.Interaction) -> None:
    """アクティブスキルの選択を開く。"""

    channel = interaction.channel
    if channel is None:
        await game_shared.respond(interaction, common_texts.CHANNEL_ERROR)
        return

    battle_row, state, unit, error = load_actor(channel.id, interaction.user.id)
    if error is not None:
        await game_shared.respond(interaction, error)
        return

    if unit.state_flags.get("skill_used_this_turn"):
        await game_shared.respond(interaction, battle_texts.SKILL_ALREADY_USED)
        return

    skills = battle_engine.available_skills(state, unit)
    if not skills:
        await game_shared.respond(interaction, battle_texts.NO_AVAILABLE_SKILL)
        return

    options = []
    for skill in skills:
        used = int(unit.active_skill_uses.get(skill.skill_id, 0))
        limit = skill.max_uses_per_battle
        remaining = (
            battle_texts.SKILL_UNLIMITED
            if limit is None
            else battle_texts.SKILL_REMAINING.format(count=limit - used)
        )
        options.append(
            discord.SelectOption(
                label=skill.name[:100],
                description=battle_texts.SKILL_OPTION_DESCRIPTION.format(
                    remaining=remaining, description=skill.description
                )[:100],
                value=skill.skill_id,
            )
        )

    view = SkillSelectView(
        int(battle_row["battle_id"]), unit.battle_unit_id, state.action_seq, options
    )
    await game_shared.respond(interaction, battle_texts.SKILL_PROMPT, view=view)


# ==================================================
# 降参（26.1節）
# ==================================================
class SurrenderConfirmView(ConfirmView):
    """降参の最終確認。"""

    def __init__(self, guild_id: int, battle_id: int) -> None:
        super().__init__(confirm_label=battle_texts.SURRENDER_CONFIRM_BUTTON)

        self.guild_id = guild_id
        self.battle_id = battle_id

    async def on_confirm(self, interaction: discord.Interaction) -> None:
        guild_row = get_guild(self.guild_id)
        if guild_row is None or guild_row["master_id"] != interaction.user.id:
            await game_shared.respond(interaction, game_shared.error_message("not_master"))
            return

        try:
            applied = await service.apply_action(
                interaction.client,
                self.battle_id,
                service.surrender_action(self.guild_id),
                elapsed_seconds=0,
                expected_seq=None,
            )
        except BattleRuleError as exc:
            await game_shared.respond(interaction, str(exc))
            return

        if not applied:
            await game_shared.respond(interaction, battle_texts.SURRENDER_FAILED)
            return

        await game_shared.game_admin_log(
            interaction.client,
            action="ギルドバトル降参",
            executor_id=interaction.user.id,
            target_guild_id=self.guild_id,
            target_battle_id=self.battle_id,
        )
        await game_shared.respond(interaction, battle_texts.SURRENDER_DONE)


# ==================================================
# 常設View：ギルドマスター専用TCのバトルパネル（8.2節）
# ==================================================
class GuildBattlePanelView(discord.ui.View):
    """ギルドマスターがバトル準備と対戦成立を操作するパネル。"""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    # ==================================================
    # メンバーセット
    # ==================================================
    @discord.ui.button(
        label=battle_texts.BUTTON_SET_MEMBERS,
        style=discord.ButtonStyle.primary,
        custom_id="guild_battle:set_members",
    )
    async def set_members(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        guild_row, error = master_guild_of_channel(interaction)
        if error is not None:
            await game_shared.respond(interaction, error)
            return

        if guild_row["roster_locked"]:
            await game_shared.respond(
                interaction, game_shared.error_message("roster_locked")
            )
            return

        discord_guild = interaction.guild
        members = []
        for row in get_guild_members(int(guild_row["guild_id"])):
            member = (
                discord_guild.get_member(row["user_id"])
                if discord_guild is not None
                else None
            )
            members.append(
                {
                    "user_id": row["user_id"],
                    "member_role": row["member_role"],
                    "display_name": (
                        member.display_name if member else f"ID:{row['user_id']}"
                    ),
                }
            )

        if not members:
            await game_shared.respond(interaction, battle_texts.NO_GUILD_MEMBERS)
            return

        await game_shared.respond(
            interaction,
            battle_texts.ROSTER_SELECT_PROMPT,
            view=RosterSelectView(guild_row, members),
        )

    # ==================================================
    # セット確認（11節）
    # ==================================================
    @discord.ui.button(
        label=battle_texts.BUTTON_CHECK_ROSTER,
        style=discord.ButtonStyle.secondary,
        custom_id="guild_battle:check_roster",
    )
    async def check_roster(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        guild_row, error = master_guild_of_channel(interaction)
        if error is not None:
            await game_shared.respond(interaction, error)
            return

        await game_shared.respond(
            interaction, embed=build_roster_embed(int(guild_row["guild_id"]))
        )

    # ==================================================
    # バトル申請（12.1節）
    # ==================================================
    @discord.ui.button(
        label=battle_texts.BUTTON_REQUEST,
        style=discord.ButtonStyle.success,
        custom_id="guild_battle:request",
    )
    async def request_battle(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        guild_row, error = master_guild_of_channel(interaction)
        if error is not None:
            await game_shared.respond(interaction, error)
            return

        guild_id = int(guild_row["guild_id"])

        pending = get_pending_battle_request_for_guild(guild_id)
        if pending is not None:
            if int(pending["from_guild_id"]) == guild_id:
                opponent = get_guild(int(pending["to_guild_id"]))
                name = (
                    opponent["name"] if opponent else battle_texts.OPPONENT_FALLBACK
                )
                await game_shared.respond(
                    interaction,
                    battle_texts.REQUEST_PENDING_OUT.format(guild_name=name),
                    view=RequestCancelView(int(pending["request_id"])),
                )
            else:
                await game_shared.respond(
                    interaction, battle_texts.REQUEST_PENDING_IN
                )
            return

        # 申し込めない理由をすべて具体的に出す（12節）
        issues = service.entry_blockers(interaction.client, guild_id)
        if issues:
            await game_shared.respond(
                interaction,
                service.blocker_message(
                    issues, action=battle_texts.ACTION_REQUEST
                ),
            )
            return

        if not opponent_options(guild_id):
            await game_shared.respond(interaction, battle_texts.NO_OPPONENT_AVAILABLE)
            return

        async def open_opponent_select(
            rate_interaction: discord.Interaction, rate
        ) -> None:
            options = opponent_options(guild_id)
            if not options:
                await game_shared.respond(
                    rate_interaction, battle_texts.NO_OPPONENT_AVAILABLE
                )
                return

            await game_shared.respond(
                rate_interaction,
                battle_texts.OPPONENT_SELECT_PROMPT.format(
                    rate=rate.name, bet=bet_confirmation(guild_id, rate.coin)
                ),
                view=OpponentSelectView(guild_row, options, bet_coin=rate.coin),
            )

        await game_shared.respond(
            interaction,
            bet_rate_guide(battle_texts.NEXT_STEP_OPPONENT),
            view=BetRateSelectView(
                guild_row=guild_row,
                action=battle_texts.ACTION_REQUEST,
                on_choose=open_opponent_select,
            ),
        )

    # ==================================================
    # バトル募集（12.2節）
    # ==================================================
    @discord.ui.button(
        label=battle_texts.BUTTON_RECRUIT,
        style=discord.ButtonStyle.success,
        custom_id="guild_battle:recruit",
    )
    async def recruit_battle(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        guild_row, error = master_guild_of_channel(interaction)
        if error is not None:
            await game_shared.respond(interaction, error)
            return

        guild_id = int(guild_row["guild_id"])

        lock = get_battle_lock(guild_id)
        if lock is not None:
            if lock["lock_type"] == "recruitment":
                await game_shared.respond(
                    interaction,
                    battle_texts.RECRUIT_PENDING,
                    view=RecruitmentCancelView(int(lock["reference_id"])),
                )
            else:
                await game_shared.respond(
                    interaction, game_shared.error_message("guild_busy")
                )
            return

        if not config.GUILD_BATTLE_RECRUITMENT_CHANNEL_ID:
            await game_shared.respond(
                interaction, battle_texts.RECRUIT_CHANNEL_UNSET
            )
            return

        channel = interaction.client.get_channel(
            config.GUILD_BATTLE_RECRUITMENT_CHANNEL_ID
        )
        if not isinstance(channel, discord.TextChannel):
            await game_shared.respond(
                interaction, battle_texts.RECRUIT_CHANNEL_NOT_FOUND
            )
            return

        # 募集する前に、開始条件を満たしているかを具体的に確認する（12.2節）
        issues = service.entry_blockers(interaction.client, guild_id)
        if issues:
            await game_shared.respond(
                interaction,
                service.blocker_message(issues, action=battle_texts.ACTION_RECRUIT),
            )
            return

        async def post_recruitment(
            rate_interaction: discord.Interaction, rate
        ) -> None:
            result = create_battle_recruitment(guild_id, bet_coin=rate.coin)
            if not result["ok"]:
                await game_shared.respond(
                    rate_interaction, game_shared.error_message(result["error"])
                )
                return

            recruitment_id = int(result["recruitment_id"])
            message = await channel.send(
                embed=recruitment_embed(
                    guild_row,
                    state_text=battle_texts.RECRUIT_STATE_OPEN,
                    bet_coin=rate.coin,
                ),
                view=BattleRecruitmentView(),
            )
            set_battle_recruitment_message(recruitment_id, channel.id, message.id)

            await game_shared.respond(
                rate_interaction,
                battle_texts.RECRUIT_POSTED.format(
                    channel=channel.mention,
                    rate=rate.name,
                    bet=bet_confirmation(guild_id, rate.coin),
                ),
            )

        await game_shared.respond(
            interaction,
            bet_rate_guide(battle_texts.NEXT_STEP_RECRUIT),
            view=BetRateSelectView(
                guild_row=guild_row,
                action=battle_texts.ACTION_RECRUIT,
                on_choose=post_recruitment,
            ),
        )

    # ==================================================
    # 降参（26.1節）
    # ==================================================
    @discord.ui.button(
        label=battle_texts.BUTTON_SURRENDER,
        style=discord.ButtonStyle.danger,
        custom_id="guild_battle:surrender",
    )
    async def surrender(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        guild_row, error = master_guild_of_channel(interaction)
        if error is not None:
            await game_shared.respond(interaction, error)
            return

        guild_id = int(guild_row["guild_id"])
        battle_row = get_active_battle_for_guild(guild_id)
        if battle_row is None or battle_row["status"] != "in_progress":
            await game_shared.respond(interaction, battle_texts.NO_ACTIVE_BATTLE)
            return

        await game_shared.respond(
            interaction,
            battle_texts.SURRENDER_CONFIRM,
            view=SurrenderConfirmView(guild_id, int(battle_row["battle_id"])),
        )


# ==================================================
# 常設View：使い魔バトルチャンネルのパネル
# 事前登録は全メンバー、出場する使い魔の差し替えは出場者だけ
# ==================================================
class BattleMemberPanelView(discord.ui.View):
    """バトル用使い魔の事前登録と、出場者による差し替えをまとめたパネル。"""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label=battle_texts.BUTTON_REGISTER,
        style=discord.ButtonStyle.primary,
        custom_id="guild_battle:register_familiars",
    )
    async def register(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        """バトルで使う使い魔を、順番付きで登録する（9.1節）。

        出場者に選ばれていなくても、バトルの進行中でも操作できます。
        """

        blocked = game_shared.game_block_reason(interaction.user)
        if blocked is not None:
            await game_shared.respond(interaction, blocked)
            return

        await interaction.response.defer(ephemeral=True)

        try:
            status = build_register_status(interaction.user.id)
            view = BattleFamiliarRegisterView(interaction.user.id)
        except Exception:
            logger.exception(
                "バトル使い魔登録の表示に失敗しました: user_id=%s", interaction.user.id
            )
            await game_shared.respond(
                interaction, game_shared.UNEXPECTED_ERROR_MESSAGE
            )
            return

        await game_shared.respond(interaction, status, view=view)

    @discord.ui.button(
        label=battle_texts.BUTTON_SET_FAMILIAR,
        style=discord.ButtonStyle.success,
        custom_id="guild_battle:set_familiar",
    )
    async def set_familiar(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        """出場者が、割り当ての範囲で使い魔を差し替える（9.3節）。"""

        guild_row, error = roster_guild_of_channel(interaction)
        if error is not None:
            await game_shared.respond(interaction, error)
            return

        guild_id = int(guild_row["guild_id"])

        if guild_row["roster_locked"]:
            await game_shared.respond(
                interaction, game_shared.error_message("roster_locked")
            )
            return

        roster = get_battle_roster(guild_id)
        assigned = next(
            (
                int(row["familiar_count"] or 0)
                for row in roster
                if int(row["user_id"]) == interaction.user.id
            ),
            None,
        )

        if assigned is None:
            await game_shared.respond(interaction, battle_texts.NOT_IN_ROSTER)
            return

        rank_info = game_shared.get_player_rank_info(interaction.user.id)
        if rank_info is None:
            # 27節: 推測でランクを補わず、操作を止めて運営ログへ記録する
            await game_shared.game_admin_log(
                interaction.client,
                action="プレイヤーランク未同期",
                executor_id=interaction.user.id,
                target_user_id=interaction.user.id,
                target_guild_id=guild_id,
                success=False,
                reason="player_roles が未同期のため使い魔セットを中止",
            )
            await game_shared.respond(interaction, battle_texts.RANK_UNKNOWN)
            return

        master = load_master_data()
        entries = get_battle_entries(guild_id)
        mine = [entry for entry in entries if int(entry["user_id"]) == interaction.user.id]

        status = build_entry_overview(
            guild_id, viewer_id=interaction.user.id, assigned=assigned
        )

        view = RosterFamiliarActionView(
            guild_id=guild_id,
            user_id=interaction.user.id,
            can_add=len(entries) < master.battle.max_units and len(mine) < assigned,
            can_remove=bool(mine),
        )

        await game_shared.respond(interaction, status, view=view)


# ==================================================
# 常設View：バトル申請Embed（12.1節）
# ==================================================
class BattleRequestView(discord.ui.View):
    """受け取ったバトル申請へ回答するボタン。"""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    def _resolve(self, interaction: discord.Interaction) -> tuple[dict | None, str | None]:
        """メッセージIDから申請を特定し、押せる相手かを確認する。"""

        blocked = game_shared.game_block_reason(interaction.user)
        if blocked is not None:
            return None, blocked

        if interaction.message is None:
            return None, battle_texts.REQUEST_LOAD_ERROR

        request_row = get_battle_request_by_message(interaction.message.id)
        if request_row is None:
            return None, battle_texts.REQUEST_NOT_FOUND

        if request_row["status"] != "pending":
            return None, game_shared.error_message("not_pending")

        guild_row = get_guild(int(request_row["to_guild_id"]))
        if guild_row is None or guild_row["status"] != "active":
            return None, game_shared.error_message("guild_not_found")

        if guild_row["master_id"] != interaction.user.id:
            return None, game_shared.error_message("not_master")

        return request_row, None

    @discord.ui.button(
        label=battle_texts.BUTTON_APPROVE,
        style=discord.ButtonStyle.success,
        custom_id="guild_battle:request_approve",
    )
    async def approve(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        request_row, error = self._resolve(interaction)
        if error is not None:
            await game_shared.respond(interaction, error)
            return

        await interaction.response.defer(ephemeral=True)

        result = resolve_battle_request(int(request_row["request_id"]), "approved")
        if not result["ok"]:
            await game_shared.respond(
                interaction, game_shared.error_message(result["error"])
            )
            return

        bet_coin = request_row.get("bet_coin")

        await _delete_request_message(interaction.client, result)
        await game_shared.respond(
            interaction,
            battle_texts.REQUEST_APPROVED.format(
                bet=bet_confirmation(
                    int(request_row["to_guild_id"]),
                    service.default_bet_coin() if bet_coin is None else int(bet_coin),
                )
            ),
        )

        started = await service.try_start_battle(
            interaction.client,
            int(request_row["from_guild_id"]),
            int(request_row["to_guild_id"]),
            bet_coin=bet_coin,
        )

        # 開始できなかった理由は、直した人にだけ見せれば足りる（13節）
        if not started["ok"] and started.get("message"):
            await game_shared.respond(interaction, started["message"])

    @discord.ui.button(
        label=battle_texts.BUTTON_REJECT,
        style=discord.ButtonStyle.danger,
        custom_id="guild_battle:request_reject",
    )
    async def reject(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        request_row, error = self._resolve(interaction)
        if error is not None:
            await game_shared.respond(interaction, error)
            return

        await interaction.response.defer(ephemeral=True)

        result = resolve_battle_request(int(request_row["request_id"]), "rejected")
        if not result["ok"]:
            await game_shared.respond(
                interaction, game_shared.error_message(result["error"])
            )
            return

        await _delete_request_message(interaction.client, result)

        from_guild = get_guild(int(request_row["from_guild_id"]))
        to_guild = get_guild(int(request_row["to_guild_id"]))
        if from_guild is not None:
            await _notify_guild_channel(
                interaction.client,
                from_guild,
                title=battle_texts.REQUEST_RESULT_TITLE,
                description=battle_texts.REQUEST_REJECTED_BODY.format(
                    guild_name=(
                        to_guild["name"]
                        if to_guild
                        else battle_texts.OPPONENT_FALLBACK
                    )
                ),
                color=config.COLOR_RED,
            )

        await game_shared.respond(interaction, battle_texts.REQUEST_REJECTED_DONE)


# ==================================================
# 常設View：公開バトル募集Embed（12.2節）
# ==================================================
class BattleRecruitmentView(discord.ui.View):
    """公開バトル募集へ申し込むボタン。"""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label=battle_texts.BUTTON_APPLY,
        style=discord.ButtonStyle.success,
        custom_id="guild_battle:recruit_apply",
    )
    async def apply(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        blocked = game_shared.game_block_reason(interaction.user)
        if blocked is not None:
            await game_shared.respond(interaction, blocked)
            return

        if interaction.message is None:
            await game_shared.respond(interaction, battle_texts.RECRUIT_LOAD_ERROR)
            return

        recruitment = get_battle_recruitment_by_message(interaction.message.id)
        if recruitment is None:
            await game_shared.respond(interaction, battle_texts.RECRUIT_NOT_FOUND)
            return

        if recruitment["status"] != "open":
            await game_shared.respond(interaction, game_shared.error_message("already_matched"))
            return

        challenger = get_player_guild(interaction.user.id)
        if challenger is None or challenger["master_id"] != interaction.user.id:
            await game_shared.respond(interaction, battle_texts.APPLY_MASTER_ONLY)
            return

        challenger_id = int(challenger["guild_id"])
        if challenger_id == int(recruitment["guild_id"]):
            await game_shared.respond(interaction, game_shared.error_message("same_guild"))
            return

        await interaction.response.defer(ephemeral=True)

        # 12.2節：開始前チェックを通過できないギルドは対戦相手として確定させず、
        # その理由を申込者へ表示する。募集を消費してしまわないよう先に確認する。
        issues = service.entry_blockers(interaction.client, challenger_id)
        if issues:
            await game_shared.respond(
                interaction,
                service.blocker_message(issues, action=battle_texts.ACTION_APPLY),
            )
            return

        result = claim_battle_recruitment(
            int(recruitment["recruitment_id"]), challenger_id
        )
        if not result["ok"]:
            await game_shared.respond(
                interaction, game_shared.error_message(result["error"])
            )
            return

        bet_coin = result.get("bet_coin")
        if bet_coin is None:
            bet_coin = recruitment.get("bet_coin")

        await close_recruitment_message(
            interaction.client, result, state_text=battle_texts.RECRUIT_STATE_CLOSED
        )
        await game_shared.respond(
            interaction,
            battle_texts.MATCH_MADE.format(
                bet=bet_confirmation(
                    challenger_id,
                    service.default_bet_coin() if bet_coin is None else int(bet_coin),
                )
            ),
        )

        started = await service.try_start_battle(
            interaction.client,
            int(result["guild_id"]),
            challenger_id,
            bet_coin=bet_coin,
        )

        if not started["ok"] and started.get("message"):
            await game_shared.respond(interaction, started["message"])


# ==================================================
# 常設View：バトル専用チャンネル（16節）
# ==================================================
class BattleCommandView(discord.ui.View):
    """ターン通知に付ける行動ボタン。押せるのは現在の行動者本人だけ。"""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label=battle_texts.BUTTON_SKILL,
        style=discord.ButtonStyle.primary,
        custom_id="guild_battle:skill",
    )
    async def use_skill(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await open_skill_selection(interaction)

    @discord.ui.button(
        label=battle_texts.BUTTON_ATTACK,
        style=discord.ButtonStyle.danger,
        custom_id="guild_battle:attack",
    )
    async def attack(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await open_attack_selection(interaction)


# ==================================================
# 常設View：ランキングパネル（26.2節）
# ==================================================
class BattleRankingPanelView(discord.ui.View):
    """ギルドバトルの通算ランキングを表示するパネル。"""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label=battle_texts.BUTTON_RANKING,
        style=discord.ButtonStyle.primary,
        custom_id="guild_battle:ranking",
    )
    async def ranking(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        blocked = game_shared.game_block_reason(interaction.user)
        if blocked is not None:
            await game_shared.respond(interaction, blocked)
            return

        await game_shared.respond(
            interaction, embed=build_ranking_embed(interaction.user.id)
        )


# ==================================================
# Embed・パネルの組み立て
# ==================================================
def build_roster_embed(guild_id: int) -> discord.Embed:
    """11節の編成確認Embedを組み立てる（出場者1～5人・使い魔最大5体）。"""

    master = load_master_data()
    guild_row = get_guild(guild_id)
    roster = get_battle_roster(guild_id)
    entries = get_battle_entries(guild_id)

    # 出場者ごとにセット済みの使い魔をまとめる
    by_user: dict[int, list[str]] = {}
    for entry in entries:
        owned = get_owned_familiar(int(entry["instance_id"]))
        text = (
            battle_texts.FAMILIAR_LABEL.format(
                name=_familiar_name(owned["familiar_id"]), level=owned["level"]
            )
            if owned is not None and owned["status"] == "owned"
            else battle_texts.ROSTER_NOT_OWNED
        )
        by_user.setdefault(int(entry["user_id"]), []).append(text)

    lines: list[str] = []
    ready_count = 0

    for index, member in enumerate(roster):
        mark = (
            battle_embed.SLOT_MARKS[index]
            if index < len(battle_embed.SLOT_MARKS)
            else f"{index + 1}."
        )

        user_id = int(member["user_id"])
        assigned = int(member["familiar_count"] or 0)
        familiars = by_user.get(user_id, [])

        if familiars and len(familiars) >= assigned:
            ready = battle_texts.ROSTER_READY_OK
            ready_count += 1
            familiar_text = "\n　".join(familiars)
        elif familiars:
            ready = battle_texts.ROSTER_READY_SHORT
            familiar_text = "\n　".join(familiars)
        else:
            ready = battle_texts.ROSTER_READY_NONE
            familiar_text = battle_texts.ROSTER_UNSET

        lines.append(
            battle_texts.ROSTER_EMBED_MEMBER.format(
                mark=mark,
                user_id=user_id,
                count=len(familiars),
                assigned=assigned,
                familiars=familiar_text,
                ready=ready,
            )
        )

    if not lines:
        lines.append(battle_texts.ROSTER_EMPTY)

    # 編成ロック中は操作しても弾かれるだけで理由が分からないため、状態を先に示す
    locked = bool(guild_row and guild_row["roster_locked"])
    state_text = (
        battle_texts.ROSTER_LOCKED if locked else battle_texts.ROSTER_UNLOCKED
    )

    header = [
        game_shared.item_line(battle_texts.LABEL_STATE, state_text),
        game_shared.item_line(
            battle_texts.LABEL_MEMBERS,
            battle_texts.ROSTER_MEMBER_SUMMARY.format(
                count=len(roster), max_members=master.battle.max_members
            ),
        ),
        game_shared.item_line(
            battle_texts.LABEL_FAMILIARS,
            battle_texts.ROSTER_FAMILIAR_SUMMARY.format(
                count=len(entries), max_units=master.battle.max_units
            ),
        ),
        "",
    ]

    if locked:
        header.insert(1, battle_texts.ROSTER_LOCKED_NOTE)

    embed = discord.Embed(
        title=battle_texts.ROSTER_EMBED_TITLE,
        description=("\n".join(header) + "\n\n".join(lines))[:4000],
        color=config.COLOR_BLUE,
    )
    # embed.set_footer(
    #     text=(
    #         f"{guild_row['name'] if guild_row else ''}　"
    #         f"使い魔をセット済み {ready_count}/{len(roster)}人"
    #     )
    # )
    return embed


def build_ranking_embed(user_id: int) -> discord.Embed:
    """ギルドランキングEmbedを組み立てる（26.2節）。"""

    master = load_master_data()
    ranking_balance = master.battle.ranking

    ranking = get_guild_ranking(
        ranking_balance.display_limit,
        win_points=ranking_balance.win_points,
        draw_points=ranking_balance.draw_points,
        lose_points=ranking_balance.lose_points,
    )

    if ranking:
        lines = [
            battle_texts.RANKING_LINE.format(
                rank=row["rank"],
                name=row["name"],
                points=row["points"],
                wins=row["wins"],
                losses=row["losses"],
                draws=row["draws"],
            )
            for row in ranking
        ]
        description = "\n".join(lines)[:4000]
    else:
        description = battle_texts.RANKING_EMPTY

    own_guild = get_player_guild(user_id)
    if own_guild is not None:
        position = get_guild_ranking_position(
            int(own_guild["guild_id"]),
            win_points=ranking_balance.win_points,
            draw_points=ranking_balance.draw_points,
            lose_points=ranking_balance.lose_points,
        )
        position_text = (
            battle_texts.RANKING_POSITION.format(position=position)
            if position
            else battle_texts.RANKING_NO_POSITION
        )
        description = (
            f"{description}\n\n"
            + game_shared.item_line(
                battle_texts.LABEL_YOUR_GUILD,
                battle_texts.RANKING_OWN_GUILD.format(
                    name=own_guild["name"], position=position_text
                ),
            )
        )

    embed = discord.Embed(
        title=battle_texts.RANKING_EMBED_TITLE,
        description=description[:4000],
        color=config.COLOR_GOLD,
    )
    embed.set_footer(
        text=battle_texts.RANKING_FOOTER.format(
            win=ranking_balance.win_points,
            draw=ranking_balance.draw_points,
            lose=ranking_balance.lose_points,
        )
    )

    return embed


def battle_panel_embed() -> discord.Embed:
    """ギルドマスター専用TCへ置くバトルパネル（8.2節）。"""

    master = load_master_data()

    return discord.Embed(
        title=BATTLE_PANEL_TITLE,
        description=battle_texts.BATTLE_PANEL_BODY.format(
            max_units=master.battle.max_units
        ),
        color=config.COLOR_PURPLE,
    )


def roster_panel_embed() -> discord.Embed:
    """使い魔バトルチャンネルへ置くパネル（9節）。"""

    master = load_master_data()

    return discord.Embed(
        title=ROSTER_PANEL_TITLE,
        description=battle_texts.ROSTER_PANEL_BODY.format(
            max_units=master.battle.max_units
        ),
        color=config.COLOR_BLUE,
    )


def ranking_panel_embed() -> discord.Embed:
    """ギルド受付チャンネルへ置くギルドランキングパネル。"""

    return discord.Embed(
        title=RANKING_PANEL_TITLE,
        description=battle_texts.RANKING_PANEL_BODY,
        color=config.COLOR_GOLD,
    )


async def ensure_roster_panel(
    bot: discord.Client, guild: discord.Guild, guild_row: dict
) -> None:
    """使い魔バトルチャンネルへパネルを（無ければ）設置する。

    ギルド情報パネルはギルド情報チャンネルへ移したため、ここには置きません。
    改名前に作られたギルドのチャンネルは、ここで新しい名前へ寄せます。
    """

    channel_id = guild_row.get("battle_member_channel_id")
    if not channel_id:
        return

    await game_shared.ensure_channel_name(
        guild,
        channel_id,
        game_shared.CHANNEL_LABELS["battle_member"],
        legacy_names=game_shared.LEGACY_CHANNEL_NAMES["battle_member"],
    )
    await remove_legacy_panels(
        bot, guild, int(channel_id), titles=LEGACY_ROSTER_PANEL_TITLES
    )

    await ensure_panel_message(
        bot,
        guild,
        int(channel_id),
        panel_title=ROSTER_PANEL_TITLE,
        embed=roster_panel_embed(),
        view=BattleMemberPanelView(),
        panel_name="使い魔セットパネル",
    )


async def ensure_battle_panel(
    bot: discord.Client, guild: discord.Guild, guild_row: dict
) -> None:
    """ギルドマスター専用TCへバトルパネルを（無ければ）設置する（8.2節）。"""

    channel_id = guild_row.get("master_text_channel_id")
    if not channel_id:
        return

    await ensure_panel_message(
        bot,
        guild,
        int(channel_id),
        panel_title=BATTLE_PANEL_TITLE,
        embed=battle_panel_embed(),
        view=GuildBattlePanelView(),
        panel_name="ギルドバトルパネル",
    )


# ==================================================
# バトル使い魔の事前登録（9節）
# ギルドに所属していなくても、バトル中でも登録できる
# ==================================================
def registered_familiar_lines(user_id: int) -> list[str]:
    """事前登録した使い魔を、優先順の行にして返す。"""

    master = load_master_data()

    lines: list[str] = []
    for row in get_player_battle_familiars(user_id):
        stats = master.level_stats(str(row["familiar_id"]), int(row["level"]))
        detail = (
            battle_texts.FAMILIAR_STATS.format(
                hp=stats.max_hp, atk=stats.atk, speed=stats.speed
            )
            if stats
            else battle_texts.DASH
        )

        lines.append(
            game_shared.item_line(
                battle_texts.REGISTER_PRIORITY_LABEL.format(
                    priority=row["priority"]
                ),
                battle_texts.REGISTER_LINE.format(
                    name=_familiar_name(row["familiar_id"]),
                    level=row["level"],
                    detail=detail,
                ),
            )
        )

    return lines


def registration_sync_notice(result: dict | None) -> list[str]:
    """事前登録の変更が出場する使い魔へどう反映されたかを知らせる（9.1節）。

    出場者に選ばれていない、または編成ロック中で反映しなかった場合は
    何も返しません。合計COST上限でセットできなかった使い魔がある場合は、
    その理由も伝えます。黙って出場体数が減ると気づけないためです。
    """

    if not result or not result.get("ok"):
        return []

    master = load_master_data()

    adopted = result.get("adopted") or []
    released = result.get("released") or []
    skipped = result.get("cost_skipped") or []

    if not adopted and not released and not skipped:
        return []

    lines = [
        battle_texts.REGISTER_SYNC_NOTE.format(
            adopted=len(adopted), released=len(released)
        )
    ]

    if skipped:
        names = "・".join(
            battle_texts.FAMILIAR_LABEL.format(
                name=_familiar_name(owned["familiar_id"]), level=owned["level"]
            )
            for instance_id in skipped
            if (owned := get_owned_familiar(int(instance_id))) is not None
        )
        lines.append(
            battle_texts.REGISTER_COST_SKIPPED.format(
                limit=master.battle.max_total_cost,
                count=len(skipped),
                names=names or battle_texts.DASH,
            )
        )
        lines.append(battle_texts.REGISTER_COST_SKIPPED_HINT)

    return lines


def build_register_status(user_id: int, result: dict | None = None) -> str:
    """事前登録の現在の内容と操作の案内を作る。"""

    master = load_master_data()

    lines = registered_familiar_lines(user_id)
    body = lines or [battle_texts.REGISTER_EMPTY]

    return "\n".join(
        [
            game_shared.item_line(
                battle_texts.LABEL_REGISTERED,
                battle_texts.REGISTER_COUNT.format(
                    count=len(lines), max_units=master.battle.max_units
                ),
            ),
            "",
            *body,
            "",
            *registration_sync_notice(result),
            battle_texts.REGISTER_NOTE_ORDER,
            battle_texts.REGISTER_NOTE_SYNC,
            battle_texts.REGISTER_NOTE_ANYTIME,
        ]
    )


def build_registerable_options(user_id: int) -> list[discord.SelectOption]:
    """事前登録に追加できる使い魔の選択肢を作る。

    同じ使い魔が複数ある場合は1つにまとめ、体数を「×3」で示します。
    """

    registered = {
        int(row["instance_id"]) for row in get_player_battle_familiars(user_id)
    }

    return grouped_familiar_options(
        [
            dict(owned)
            for owned in get_owned_familiars(user_id)
            if int(owned["instance_id"]) not in registered
        ]
    )


def build_registered_options(user_id: int) -> list[discord.SelectOption]:
    """事前登録している使い魔の選択肢を、登録順で作る。"""

    options: list[discord.SelectOption] = []

    for row in get_player_battle_familiars(user_id):
        option = familiar_option(
            int(row["instance_id"]),
            str(row["familiar_id"]),
            int(row["level"]),
            prefix=battle_texts.REGISTER_SLOT_PREFIX.format(
                priority=row["priority"]
            ),
        )
        if option is not None:
            options.append(option)

    return options


class RegisterAddView(PagedSelectView):
    """事前登録の末尾へ使い魔を1体追加する一時View。"""

    def __init__(self, options: list[discord.SelectOption]) -> None:
        super().__init__(options, placeholder=battle_texts.REGISTER_ADD_PLACEHOLDER)

    async def on_choice(self, interaction: discord.Interaction, value: str) -> None:
        await interaction.response.defer(ephemeral=True)

        master = load_master_data()
        user_id = interaction.user.id

        current = [
            int(row["instance_id"]) for row in get_player_battle_familiars(user_id)
        ]

        if len(current) >= master.battle.max_units:
            await game_shared.respond(
                interaction,
                battle_texts.REGISTER_FULL.format(
                    max_units=master.battle.max_units
                ),
            )
            return

        if int(value) in current:
            await game_shared.respond(interaction, battle_texts.REGISTER_ALREADY)
            return

        result = set_player_battle_familiars(user_id, [*current, int(value)])
        if not result["ok"]:
            await game_shared.respond(
                interaction, game_shared.error_message(result["error"])
            )
            return

        await game_shared.respond(
            interaction,
            build_register_status(user_id, result),
            view=BattleFamiliarRegisterView(user_id),
        )


class RegisterRemoveView(PagedSelectView):
    """事前登録から1体だけ取り消す一時View（9.1節）。"""

    async def on_choice(self, interaction: discord.Interaction, value: str) -> None:
        await interaction.response.defer(ephemeral=True)

        user_id = interaction.user.id
        current = [
            int(row["instance_id"])
            for row in get_player_battle_familiars(user_id)
        ]

        target = int(value)
        if target not in current:
            await game_shared.respond(
                interaction, battle_texts.REGISTER_NOT_REGISTERED
            )
            return

        # 取り消した分を詰めて、以降の優先順を1つ繰り上げる
        current.remove(target)
        result = set_player_battle_familiars(user_id, current)

        await game_shared.respond(
            interaction,
            build_register_status(user_id, result),
            view=BattleFamiliarRegisterView(user_id),
        )


class RegisterReplaceTargetView(PagedSelectView):
    """入れ替える枠を選ぶ一時View（9.1節）。"""

    async def on_choice(self, interaction: discord.Interaction, value: str) -> None:
        await interaction.response.defer(ephemeral=True)

        user_id = interaction.user.id
        options = build_registerable_options(user_id)

        if not options:
            await game_shared.respond(
                interaction, battle_texts.REGISTER_NO_REPLACEMENT
            )
            return

        current = get_player_battle_familiars(user_id)
        target = next(
            (row for row in current if int(row["instance_id"]) == int(value)), None
        )
        if target is None:
            await game_shared.respond(
                interaction, battle_texts.REGISTER_NOT_REGISTERED
            )
            return

        await game_shared.respond(
            interaction,
            battle_texts.REGISTER_REPLACE_PROMPT.format(
                priority=target["priority"],
                name=_familiar_name(target["familiar_id"]),
                level=target["level"],
            ),
            view=RegisterReplaceView(options, replaced_instance_id=int(value)),
        )


class RegisterReplaceView(PagedSelectView):
    """選んだ枠を別の使い魔へ入れ替える一時View（9.1節）。"""

    def __init__(
        self,
        options: list[discord.SelectOption],
        *,
        replaced_instance_id: int,
    ) -> None:
        super().__init__(options, placeholder=battle_texts.REGISTER_REPLACE_PLACEHOLDER)

        self.replaced_instance_id = replaced_instance_id

    async def on_choice(self, interaction: discord.Interaction, value: str) -> None:
        await interaction.response.defer(ephemeral=True)

        user_id = interaction.user.id
        current = [
            int(row["instance_id"])
            for row in get_player_battle_familiars(user_id)
        ]

        if self.replaced_instance_id not in current:
            await game_shared.respond(
                interaction, battle_texts.REGISTER_NOT_REGISTERED
            )
            return

        new_instance_id = int(value)
        if new_instance_id in current:
            await game_shared.respond(interaction, battle_texts.REGISTER_ALREADY)
            return

        # 優先順を変えずに、同じ位置だけ差し替える
        current[current.index(self.replaced_instance_id)] = new_instance_id

        result = set_player_battle_familiars(user_id, current)
        if not result["ok"]:
            await game_shared.respond(
                interaction, game_shared.error_message(result["error"])
            )
            return

        await game_shared.respond(
            interaction,
            build_register_status(user_id, result),
            view=BattleFamiliarRegisterView(user_id),
        )


class BattleFamiliarRegisterView(discord.ui.View):
    """事前登録の追加・取消を操作する一時View。"""

    def __init__(self, user_id: int) -> None:
        super().__init__(timeout=EPHEMERAL_TIMEOUT)

        master = load_master_data()
        registered = get_player_battle_familiars(user_id)

        self.user_id = user_id
        self.add_familiar.disabled = len(registered) >= master.battle.max_units
        self.replace_familiar.disabled = not registered
        self.remove_one.disabled = not registered
        self.undo.disabled = not registered
        self.clear_all.disabled = not registered

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await game_shared.respond(interaction, battle_texts.OWNER_ONLY)
            return False

        return True

    @discord.ui.button(
        label=battle_texts.REGISTER_BUTTON_ADD, style=discord.ButtonStyle.success
    )
    async def add_familiar(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        options = build_registerable_options(interaction.user.id)

        if not options:
            await game_shared.respond(
                interaction, battle_texts.REGISTER_NO_CANDIDATES
            )
            return

        await game_shared.respond(
            interaction,
            battle_texts.REGISTER_ADD_PROMPT,
            view=RegisterAddView(options),
        )

    @discord.ui.button(
        label=battle_texts.REGISTER_BUTTON_REPLACE, style=discord.ButtonStyle.primary
    )
    async def replace_familiar(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        options = build_registered_options(interaction.user.id)

        if not options:
            await game_shared.respond(interaction, battle_texts.REGISTER_NONE)
            return

        await game_shared.respond(
            interaction,
            battle_texts.REGISTER_REPLACE_SLOT_PROMPT,
            view=RegisterReplaceTargetView(
                options, placeholder=battle_texts.REGISTER_REPLACE_SLOT_PLACEHOLDER
            ),
        )

    @discord.ui.button(
        label=battle_texts.REGISTER_BUTTON_REMOVE_ONE,
        style=discord.ButtonStyle.secondary,
    )
    async def remove_one(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        options = build_registered_options(interaction.user.id)

        if not options:
            await game_shared.respond(interaction, battle_texts.REGISTER_NONE)
            return

        await game_shared.respond(
            interaction,
            battle_texts.REGISTER_REMOVE_PROMPT,
            view=RegisterRemoveView(
                options, placeholder=battle_texts.REGISTER_REMOVE_PLACEHOLDER
            ),
        )

    @discord.ui.button(
        label=battle_texts.REGISTER_BUTTON_UNDO, style=discord.ButtonStyle.secondary
    )
    async def undo(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        current = [
            int(row["instance_id"])
            for row in get_player_battle_familiars(interaction.user.id)
        ]

        if not current:
            await game_shared.respond(interaction, battle_texts.REGISTER_NONE)
            return

        result = set_player_battle_familiars(interaction.user.id, current[:-1])

        await game_shared.respond(
            interaction,
            build_register_status(interaction.user.id, result),
            view=BattleFamiliarRegisterView(interaction.user.id),
        )

    @discord.ui.button(
        label=battle_texts.REGISTER_BUTTON_CLEAR, style=discord.ButtonStyle.danger
    )
    async def clear_all(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        result = set_player_battle_familiars(interaction.user.id, [])

        await game_shared.respond(
            interaction,
            build_register_status(interaction.user.id, result),
            view=BattleFamiliarRegisterView(interaction.user.id),
        )
