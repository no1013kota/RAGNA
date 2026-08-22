"""バトル専用チャンネルの操作（16節・17節・19節・26.1節）。

進行中のバトルの行動なので、利用資格ではなく ``load_actor`` の「いま手番か」で
判定します（途中で資格を失った人を止めると、相手ギルドまで試合が進まなくなる
ため。詳しくは ``battle_common.load_actor`` の説明を参照）。
"""

from __future__ import annotations

import discord

from cogs import game_shared
from database.battle import load_battle_state
from database.guild import get_guild
from game import battle_embed, battle_engine, effects
from game.master_data import load_master_data
from game.models import (
    ACTION_ATTACK,
    ACTION_SKILL,
    BattleAction,
    BattleRuleError,
)

from . import service
from .battle_common import (
    EPHEMERAL_TIMEOUT,
    SELECT_LIMIT,
    ConfirmView,
    PagedSelectView,
    apply_and_report,
    effect_marks,
    load_actor,
    unit_option,
)
from texts import battle as battle_texts
from texts import common as common_texts


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
        await apply_and_report(
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

        options = [unit_option(candidate, state) for candidate in candidates]
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

        applied = await apply_and_report(
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

    options = [unit_option(choice, state) for choice in choices]
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

    marks = effect_marks(state, unit)
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
