"""使い魔パネル（ガチャ／管理）のDiscord UI層（GAME_SPEC 10.2節）。

常設パネルのViewだけが固定 ``custom_id`` を持ち、押した先の一時的なView・
セレクトには ``custom_id`` を付けません（discord.pyが自動採番します）。
所有状態と使用中判定は、ボタンが見えていても必ずDBから再確認します。

Embedは ``field`` を使わず、本文へ「【項目】結果」の形で並べます。
"""

from __future__ import annotations

import logging

from typing import Any

import discord

from cogs import game_shared
from database.battle import get_locked_instance_ids  # 合成・売却の使用中判定に使う
from database.familiar import (
    draw_gacha,
    fuse_familiar,
    get_owned_familiar,
    get_owned_familiars,
    sell_familiars,
)
from game.master_data import load_master_data

from . import service


logger = logging.getLogger(__name__)


# ==================================================
# 共通メッセージ
# ==================================================
GAME_DISABLED_MESSAGE = game_shared.DISABLED_MESSAGE
MASTER_ERROR_MESSAGE = "使い魔データを読み込めませんでした。運営へお問い合わせください。"
UNEXPECTED_ERROR_MESSAGE = game_shared.UNEXPECTED_ERROR_MESSAGE
NO_FAMILIAR_MESSAGE = "所有している使い魔がありません。ガチャで入手してください。"
LOCKED_NOTICE = "-# 編成ロック中・進行中バトルで使用中の使い魔は表示されません。"

# セレクトの上限（Discordの仕様）
PAGE_SIZE = 25


async def _guard(interaction: discord.Interaction) -> bool:
    """公開状態を確認する。利用できない場合は理由を返して中断する。"""

    if not game_shared.is_game_enabled():
        await game_shared.respond(interaction, GAME_DISABLED_MESSAGE)
        return False

    return True


def _own_instance(user_id: int, instance_id: int) -> dict[str, Any] | None:
    """所有中（``status = 'owned'``）の個体だけを返す。"""

    row = get_owned_familiar(instance_id)
    if row is None:
        return None

    if int(row["user_id"]) != user_id or row["status"] != "owned":
        return None

    return row


# ==================================================
# 所有使い魔を選ぶ共通View（25件ずつページング）
# ==================================================
class _GroupSelect(discord.ui.Select):
    """所有使い魔を「同じ種類・同じレベル」でまとめて1つ選ばせるセレクト。"""

    def __init__(self, groups: list[dict[str, Any]], *, placeholder: str):
        super().__init__(
            placeholder=placeholder,
            min_values=1,
            max_values=1,
            options=service.build_group_options(groups),
        )

    async def callback(self, interaction: discord.Interaction):
        view: GroupPageView = self.view  # type: ignore[assignment]
        await view.on_select(interaction, int(self.values[0]))


class _PageButton(discord.ui.Button):
    """25件ずつのページ送りボタン。"""

    def __init__(self, *, label: str, delta: int, disabled: bool):
        super().__init__(
            label=label,
            style=discord.ButtonStyle.secondary,
            row=1,
            disabled=disabled,
        )
        self.delta = delta

    async def callback(self, interaction: discord.Interaction):
        view: GroupPageView = self.view  # type: ignore[assignment]
        await view.move_page(interaction, self.delta)


class GroupPageView(discord.ui.View):
    """まとめた所有使い魔をページングしながら1つ選ばせる一時View。"""

    placeholder = "使い魔を選択してください"

    def __init__(
        self,
        *,
        user_id: int,
        rows: list[dict[str, Any]],
        timeout: float = 300,
    ):
        super().__init__(timeout=timeout)

        self.user_id = user_id
        self.rows = rows
        self.groups = service.group_instances(rows)
        self.page = 0

        self._rebuild()

    # ----- ページ操作 -----
    @property
    def page_count(self) -> int:
        return max(1, -(-len(self.groups) // PAGE_SIZE))

    def page_groups(self) -> list[dict[str, Any]]:
        start = self.page * PAGE_SIZE
        return self.groups[start : start + PAGE_SIZE]

    def _rebuild(self) -> None:
        self.clear_items()
        self.add_item(_GroupSelect(self.page_groups(), placeholder=self.placeholder))

        if self.page_count > 1:
            self.add_item(
                _PageButton(label="◀ 前のページ", delta=-1, disabled=self.page == 0)
            )
            self.add_item(
                _PageButton(
                    label="次のページ ▶",
                    delta=1,
                    disabled=self.page >= self.page_count - 1,
                )
            )

    async def move_page(self, interaction: discord.Interaction, delta: int) -> None:
        self.page = min(max(self.page + delta, 0), self.page_count - 1)
        self._rebuild()

        try:
            await interaction.response.edit_message(view=self)
        except discord.HTTPException:
            logger.warning("使い魔選択のページ送りに失敗しました")

    # ----- 共通の実行者確認 -----
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await game_shared.respond(interaction, "この操作は実行者だけが使用できます。")
            return False

        return True

    def group_of(self, instance_id: int) -> dict[str, Any] | None:
        """代表個体IDからまとめた情報を引く。"""

        for group in self.groups:
            if int(group["instance_id"]) == instance_id:
                return group

        return None

    async def on_select(self, interaction: discord.Interaction, instance_id: int) -> None:
        raise NotImplementedError


# ==================================================
# 体数を選ぶ共通View
# ==================================================
class _CountSelect(discord.ui.Select):
    """「何体にするか」を選ばせるセレクト。"""

    def __init__(self, options: list[discord.SelectOption], *, placeholder: str):
        super().__init__(
            placeholder=placeholder,
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        view: CountView = self.view  # type: ignore[assignment]
        await view.on_count(interaction, int(self.values[0]))


class CountView(discord.ui.View):
    """体数を1つ選ばせる一時View。"""

    placeholder = "体数を選択してください"

    def __init__(
        self,
        *,
        user_id: int,
        options: list[discord.SelectOption],
        timeout: float = 300,
    ):
        super().__init__(timeout=timeout)

        self.user_id = user_id
        self.add_item(_CountSelect(options, placeholder=self.placeholder))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await game_shared.respond(interaction, "この操作は実行者だけが使用できます。")
            return False

        return True

    async def on_count(self, interaction: discord.Interaction, count: int) -> None:
        raise NotImplementedError


# ==================================================
# ガチャ（10.2節）
# ==================================================
class GachaPanelView(discord.ui.View):
    """ガチャパネルの常設View。"""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="単発ガチャ",
        style=discord.ButtonStyle.primary,
        custom_id="familiar:gacha_single",
    )
    async def gacha_single(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await _open_gacha_confirm(interaction, multi=False)

    @discord.ui.button(
        label="10連ガチャ",
        style=discord.ButtonStyle.success,
        custom_id="familiar:gacha_multi",
    )
    async def gacha_multi(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await _open_gacha_confirm(interaction, multi=True)

    @discord.ui.button(
        label="排出使い魔の確認",
        style=discord.ButtonStyle.secondary,
        custom_id="familiar:gacha_rates",
    )
    async def show_rates(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        """排出される使い魔とランクごとの確率を一覧表示する。"""

        if not await _guard(interaction):
            return

        try:
            pool = service.get_pool()
        except Exception:
            logger.exception("ガチャ設定の読み込みに失敗しました")
            await game_shared.respond(interaction, MASTER_ERROR_MESSAGE)
            return

        if pool is None:
            await game_shared.respond(interaction, "現在このガチャは利用できません。")
            return

        try:
            embed, has_familiars = service.build_rate_list_embed(pool)
        except Exception:
            logger.exception("排出使い魔一覧の作成に失敗しました")
            await game_shared.respond(interaction, UNEXPECTED_ERROR_MESSAGE)
            return

        if not has_familiars:
            await game_shared.respond(
                interaction, "排出できる使い魔が登録されていません。"
            )
            return

        await game_shared.respond(interaction, embed=embed)


async def _open_gacha_confirm(interaction: discord.Interaction, *, multi: bool) -> None:
    """実行前の確認画面を実行者だけへ表示する。"""

    if not await _guard(interaction):
        return

    try:
        pool = service.get_pool()
    except Exception:
        logger.exception("ガチャ設定の読み込みに失敗しました")
        await game_shared.respond(interaction, MASTER_ERROR_MESSAGE)
        return

    if pool is None or not pool.is_public:
        await game_shared.respond(interaction, "現在このガチャは利用できません。")
        return

    count, cost = service.gacha_plan(pool, multi=multi)

    if not service.build_rank_table(pool):
        await game_shared.respond(
            interaction, "抽選できる使い魔が登録されていないため、ガチャを利用できません。"
        )
        return

    embed = service.build_gacha_confirm_embed(pool, count=count, cost=cost)

    await game_shared.respond(
        interaction,
        embed=embed,
        view=GachaConfirmView(
            user_id=interaction.user.id,
            pool_id=pool.pool_id,
            count=count,
            cost=cost,
        ),
    )


class GachaConfirmView(discord.ui.View):
    """ガチャの実行確認。連打による二重実行を防ぐ一時View。"""

    def __init__(self, *, user_id: int, pool_id: str, count: int, cost: int):
        super().__init__(timeout=180)

        self.user_id = user_id
        self.pool_id = pool_id
        self.count = count
        self.cost = cost
        self._running = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await game_shared.respond(interaction, "この操作は実行者だけが使用できます。")
            return False

        return True

    @discord.ui.button(label="実行する", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self._running:
            await game_shared.respond(interaction, "処理中です。しばらくお待ちください。")
            return

        self._running = True

        # 押下直後に無効化してから処理する（連打による二重実行の防止）。
        for item in self.children:
            item.disabled = True

        try:
            await interaction.response.edit_message(view=self)
        except discord.HTTPException:
            logger.warning("ガチャ確認画面の更新に失敗しました")

        try:
            if not game_shared.is_game_enabled():
                await game_shared.respond(interaction, GAME_DISABLED_MESSAGE)
                return

            pool = service.get_pool(self.pool_id)
            if pool is None or not pool.is_public:
                await game_shared.respond(interaction, "現在このガチャは利用できません。")
                return

            master = load_master_data()
            results = service.draw_results(pool, self.count)

            # coin減算と全抽選結果の保存は draw_gacha が1つのDB処理で確定する。
            outcome = draw_gacha(
                interaction.user.id,
                pool_id=pool.pool_id,
                count=self.count,
                cost=self.cost,
                results=results,
                initial_level=master.familiar.min_level,
            )

            if not outcome["ok"]:
                await game_shared.respond(
                    interaction, game_shared.error_message(outcome["error"])
                )
                return

            embed = service.build_gacha_result_embed(
                pool,
                outcome["instances"],
                count=self.count,
                cost=self.cost,
            )
            await game_shared.respond(interaction, embed=embed)

        except service.GachaUnavailableError:
            logger.exception("ガチャの抽選対象が不足しています")
            await game_shared.respond(
                interaction,
                "抽選できる使い魔が登録されていないため、ガチャを利用できません。",
            )

        except Exception:
            logger.exception("ガチャの実行に失敗しました: user_id=%s", interaction.user.id)
            await game_shared.respond(interaction, UNEXPECTED_ERROR_MESSAGE)

        finally:
            self.stop()

    @discord.ui.button(label="キャンセル", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()

        try:
            await interaction.response.edit_message(
                content="キャンセルしました。", embed=None, view=None
            )
        except discord.HTTPException:
            logger.warning("ガチャ確認画面の取消表示に失敗しました")


# ==================================================
# 使い魔管理（10.2節）
# 一覧・合成・売却・図鑑を1つのパネルにまとめる
# ==================================================
class FamiliarManagePanelView(discord.ui.View):
    """使い魔の一覧・合成・売却・図鑑をまとめた常設View。"""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="一覧",
        style=discord.ButtonStyle.primary,
        custom_id="familiar:list",
    )
    async def show_list(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if not await _guard(interaction):
            return

        await interaction.response.defer(ephemeral=True)

        try:
            owned = get_owned_familiars(interaction.user.id)
        except Exception:
            logger.exception("所有使い魔の取得に失敗しました: user_id=%s", interaction.user.id)
            await interaction.followup.send(UNEXPECTED_ERROR_MESSAGE, ephemeral=True)
            return

        if not owned:
            await interaction.followup.send(NO_FAMILIAR_MESSAGE, ephemeral=True)
            return

        try:
            embed = service.build_owned_list_embed(owned)
            view = FamiliarListView(user_id=interaction.user.id, rows=owned)
        except Exception:
            logger.exception("使い魔一覧の作成に失敗しました: user_id=%s", interaction.user.id)
            await interaction.followup.send(UNEXPECTED_ERROR_MESSAGE, ephemeral=True)
            return

        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    @discord.ui.button(
        label="合成",
        style=discord.ButtonStyle.success,
        custom_id="familiar:fuse",
    )
    async def start_fusion(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await _open_fusion(interaction)

    @discord.ui.button(
        label="売却",
        style=discord.ButtonStyle.danger,
        custom_id="familiar:sell",
    )
    async def start_sell(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await _open_sell(interaction)

    @discord.ui.button(
        label="図鑑",
        style=discord.ButtonStyle.secondary,
        custom_id="familiar:codex",
    )
    async def open_codex(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await _open_codex(interaction)


class FamiliarListView(GroupPageView):
    """所有使い魔を選んで詳細を表示する。"""

    placeholder = "詳細を見る使い魔を選択してください"

    async def on_select(self, interaction: discord.Interaction, instance_id: int) -> None:
        await interaction.response.defer(ephemeral=True)

        row = _own_instance(self.user_id, instance_id)
        if row is None:
            await interaction.followup.send(
                game_shared.error_message("not_owned"), ephemeral=True
            )
            return

        group = self.group_of(instance_id)

        try:
            embed, icon = service.build_familiar_detail_embed(
                row, count=int(group["count"]) if group else 1
            )
        except Exception:
            logger.exception("使い魔詳細の作成に失敗しました: instance_id=%s", instance_id)
            await interaction.followup.send(UNEXPECTED_ERROR_MESSAGE, ephemeral=True)
            return

        if icon is not None:
            await interaction.followup.send(embed=embed, file=icon, ephemeral=True)
        else:
            await interaction.followup.send(embed=embed, ephemeral=True)


# ==================================================
# 図鑑（10.2節）
# 所有している使い魔の画像をめくって眺める
# ==================================================
async def _open_codex(interaction: discord.Interaction) -> None:
    """図鑑の1ページ目を開く。"""

    if not await _guard(interaction):
        return

    await interaction.response.defer(ephemeral=True)

    try:
        owned = get_owned_familiars(interaction.user.id)
        pages = service.codex_pages(owned)
    except Exception:
        logger.exception("図鑑の作成に失敗しました: user_id=%s", interaction.user.id)
        await interaction.followup.send(UNEXPECTED_ERROR_MESSAGE, ephemeral=True)
        return

    if not pages:
        await interaction.followup.send(NO_FAMILIAR_MESSAGE, ephemeral=True)
        return

    view = CodexView(user_id=interaction.user.id, pages=pages)
    embed, image = view.current()

    if image is not None:
        await interaction.followup.send(
            embed=embed, file=image, view=view, ephemeral=True
        )
    else:
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)


class _CodexPageButton(discord.ui.Button):
    """図鑑をめくるボタン。"""

    def __init__(self, *, label: str, delta: int):
        super().__init__(label=label, style=discord.ButtonStyle.secondary)
        self.delta = delta

    async def callback(self, interaction: discord.Interaction):
        view: CodexView = self.view  # type: ignore[assignment]
        await view.turn(interaction, self.delta)


class CodexView(discord.ui.View):
    """図鑑をめくる一時View。前後のページへ循環して移動できる。"""

    def __init__(
        self,
        *,
        user_id: int,
        pages: list[dict[str, Any]],
        timeout: float = 300,
    ):
        super().__init__(timeout=timeout)

        self.user_id = user_id
        self.pages = pages
        self.index = 0

        if len(pages) > 1:
            self.add_item(_CodexPageButton(label="◀ 前の使い魔", delta=-1))
            self.add_item(_CodexPageButton(label="次の使い魔 ▶", delta=1))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await game_shared.respond(interaction, "この操作は実行者だけが使用できます。")
            return False

        return True

    def current(self) -> tuple[discord.Embed, discord.File | None]:
        return service.build_codex_embed(self.pages, self.index)

    async def turn(self, interaction: discord.Interaction, delta: int) -> None:
        self.index = (self.index + delta) % len(self.pages)

        try:
            embed, image = self.current()
        except Exception:
            logger.exception("図鑑ページの作成に失敗しました: index=%s", self.index)
            await game_shared.respond(interaction, UNEXPECTED_ERROR_MESSAGE)
            return

        # 画像を差し替えるため、添付ファイルごと入れ替える。
        try:
            await interaction.response.edit_message(
                embed=embed,
                attachments=[image] if image is not None else [],
                view=self,
            )
        except discord.HTTPException:
            logger.warning("図鑑ページの更新に失敗しました")


# ==================================================
# 合成（10.2節）
# ==================================================
async def _open_fusion(interaction: discord.Interaction) -> None:
    """合成のベース個体選択を開く。"""

    if not await _guard(interaction):
        return

    await interaction.response.defer(ephemeral=True)

    try:
        owned = get_owned_familiars(interaction.user.id)
        locked = get_locked_instance_ids()
        bases = service.fusable_bases(owned, locked)
    except Exception:
        logger.exception("合成候補の取得に失敗しました: user_id=%s", interaction.user.id)
        await interaction.followup.send(UNEXPECTED_ERROR_MESSAGE, ephemeral=True)
        return

    if not owned:
        await interaction.followup.send(NO_FAMILIAR_MESSAGE, ephemeral=True)
        return

    if not bases:
        master = load_master_data()
        await interaction.followup.send(
            (
                "合成できる使い魔がありません。\n"
                f"-# 同じ種類を2体以上所有し、Lv.{master.familiar.max_level}未満の使い魔が必要です。\n"
                f"{LOCKED_NOTICE}"
            ),
            ephemeral=True,
        )
        return

    view = FuseBaseView(user_id=interaction.user.id, rows=bases)
    await interaction.followup.send(
        content=(
            "レベルアップさせる使い魔を選択してください。\n"
            "-# ※素材にした使い魔は消費されます。\n"
            f"{LOCKED_NOTICE}"
        ),
        view=view,
        ephemeral=True,
    )


class FuseBaseView(GroupPageView):
    """レベルアップさせるベース個体を選ぶ。"""

    placeholder = "レベルアップさせる使い魔を選択してください"

    async def on_select(self, interaction: discord.Interaction, instance_id: int) -> None:
        await interaction.response.defer(ephemeral=True)

        base = _own_instance(self.user_id, instance_id)
        if base is None:
            await interaction.followup.send(
                game_shared.error_message("not_owned"), ephemeral=True
            )
            return

        master = load_master_data()
        if int(base["level"]) >= master.familiar.max_level:
            await interaction.followup.send(
                game_shared.error_message("max_level"), ephemeral=True
            )
            return

        try:
            locked = get_locked_instance_ids()
            owned = get_owned_familiars(self.user_id)
            max_count = service.max_fusion_count(owned, locked, base=base)
        except Exception:
            logger.exception("合成素材の取得に失敗しました: instance_id=%s", instance_id)
            await interaction.followup.send(UNEXPECTED_ERROR_MESSAGE, ephemeral=True)
            return

        if instance_id in locked:
            await interaction.followup.send(
                game_shared.error_message("in_use"), ephemeral=True
            )
            return

        if max_count < 1:
            await interaction.followup.send(
                f"素材にできる同じ種類の使い魔がありません。\n{LOCKED_NOTICE}",
                ephemeral=True,
            )
            return

        view = FuseCountView(
            user_id=self.user_id,
            base_instance_id=instance_id,
            options=service.build_fusion_count_options(base, max_count),
        )
        await interaction.followup.send(
            content=(
                f"**{service.instance_title(base)}** を何体合成しますか？\n"
                f"-# 一度に{max_count}体まで合成できます。\n"
                "-# ※素材にした使い魔は消費されます。"
            ),
            view=view,
            ephemeral=True,
        )


class FuseCountView(CountView):
    """何体合成するかを選び、そのまま実行する。"""

    placeholder = "合成する体数を選択してください"

    def __init__(
        self,
        *,
        user_id: int,
        base_instance_id: int,
        options: list[discord.SelectOption],
        timeout: float = 300,
    ):
        super().__init__(user_id=user_id, options=options, timeout=timeout)

        self.base_instance_id = base_instance_id
        self._running = False

    async def on_count(self, interaction: discord.Interaction, count: int) -> None:
        if self._running:
            await game_shared.respond(interaction, "処理中です。しばらくお待ちください。")
            return

        self._running = True
        await interaction.response.defer(ephemeral=True)

        master = load_master_data()

        try:
            outcome = fuse_familiar(
                self.user_id,
                base_instance_id=self.base_instance_id,
                material_count=count,
                max_level=master.familiar.max_level,
                locked_instance_ids=get_locked_instance_ids(),
            )
        except Exception:
            logger.exception(
                "合成に失敗しました: base=%s count=%s", self.base_instance_id, count
            )
            await interaction.followup.send(UNEXPECTED_ERROR_MESSAGE, ephemeral=True)
            return

        finally:
            self.stop()

        if not outcome["ok"]:
            await interaction.followup.send(
                game_shared.error_message(outcome["error"]), ephemeral=True
            )
            return

        embed, icon = service.build_fusion_result_embed(
            str(outcome["familiar_id"]),
            before_level=int(outcome["before_level"]),
            level=int(outcome["level"]),
            material_count=len(outcome["material_instance_ids"]),
        )

        if icon is not None:
            await interaction.followup.send(embed=embed, file=icon, ephemeral=True)
        else:
            await interaction.followup.send(embed=embed, ephemeral=True)


# ==================================================
# 売却（10.2節）
# ==================================================
async def _open_sell(interaction: discord.Interaction) -> None:
    """売却する個体の選択を開く。"""

    if not await _guard(interaction):
        return

    await interaction.response.defer(ephemeral=True)

    try:
        owned = get_owned_familiars(interaction.user.id)
        candidates = service.exclude_locked(owned, get_locked_instance_ids())
    except Exception:
        logger.exception("売却候補の取得に失敗しました: user_id=%s", interaction.user.id)
        await interaction.followup.send(UNEXPECTED_ERROR_MESSAGE, ephemeral=True)
        return

    if not owned:
        await interaction.followup.send(NO_FAMILIAR_MESSAGE, ephemeral=True)
        return

    if not candidates:
        await interaction.followup.send(
            f"売却できる使い魔がありません。\n{LOCKED_NOTICE}", ephemeral=True
        )
        return

    view = SellSelectView(user_id=interaction.user.id, rows=candidates)
    await interaction.followup.send(
        content=(
            "売却する使い魔を選択してください。\n"
            "-# ※取り消しできません。\n"
            f"{LOCKED_NOTICE}"
        ),
        view=view,
        ephemeral=True,
    )


class SellSelectView(GroupPageView):
    """売却する使い魔を選ぶ。次に体数を選ばせる。"""

    placeholder = "売却する使い魔を選択してください"

    async def on_select(self, interaction: discord.Interaction, instance_id: int) -> None:
        await interaction.response.defer(ephemeral=True)

        row = _own_instance(self.user_id, instance_id)
        if row is None:
            await interaction.followup.send(
                game_shared.error_message("not_owned"), ephemeral=True
            )
            return

        locked = get_locked_instance_ids()
        if instance_id in locked:
            await interaction.followup.send(
                game_shared.error_message("in_use"), ephemeral=True
            )
            return

        group = self.group_of(instance_id)
        if group is None:
            await interaction.followup.send(
                game_shared.error_message("not_owned"), ephemeral=True
            )
            return

        # 選択後に編成へ入った個体を除き、売却できるものだけを対象にする。
        sellable = [
            candidate
            for candidate in group["instance_ids"]
            if candidate not in locked
        ]
        if not sellable:
            await interaction.followup.send(
                game_shared.error_message("in_use"), ephemeral=True
            )
            return

        unit_price = service.sell_price(str(row["familiar_id"]), int(row["level"]))

        view = SellCountView(
            user_id=self.user_id,
            row=row,
            instance_ids=sellable,
            unit_price=unit_price,
            options=service.build_count_options(len(sellable), unit_price=unit_price),
        )
        await interaction.followup.send(
            content=(
                f"**{service.instance_title(row)}** を何体売却しますか？\n"
                f"-# 1体あたり {game_shared.format_coin(unit_price)}／所有 {len(sellable)}体\n"
                "-# ※取り消しできません。"
            ),
            view=view,
            ephemeral=True,
        )


class SellCountView(CountView):
    """何体売却するかを選び、最終確認へ進む。"""

    placeholder = "売却する体数を選択してください"

    def __init__(
        self,
        *,
        user_id: int,
        row: dict[str, Any],
        instance_ids: list[int],
        unit_price: int,
        options: list[discord.SelectOption],
        timeout: float = 300,
    ):
        super().__init__(user_id=user_id, options=options, timeout=timeout)

        self.row = row
        self.instance_ids = instance_ids
        self.unit_price = unit_price

    async def on_count(self, interaction: discord.Interaction, count: int) -> None:
        await interaction.response.defer(ephemeral=True)

        targets = self.instance_ids[:count]

        embed = service.build_sell_confirm_embed(
            self.row,
            count=len(targets),
            unit_price=self.unit_price,
            available=len(self.instance_ids),
        )
        await interaction.followup.send(
            embed=embed,
            view=SellConfirmView(user_id=self.user_id, instance_ids=targets),
            ephemeral=True,
        )

        self.stop()


class SellConfirmView(discord.ui.View):
    """売却の最終確認。"""

    def __init__(self, *, user_id: int, instance_ids: list[int]):
        super().__init__(timeout=180)

        self.user_id = user_id
        self.instance_ids = instance_ids
        self._running = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await game_shared.respond(interaction, "この操作は実行者だけが使用できます。")
            return False

        return True

    @discord.ui.button(label="売却する", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self._running:
            await game_shared.respond(interaction, "処理中です。しばらくお待ちください。")
            return

        self._running = True

        for item in self.children:
            item.disabled = True

        try:
            await interaction.response.edit_message(view=self)
        except discord.HTTPException:
            logger.warning("売却確認画面の更新に失敗しました")

        try:
            if not game_shared.is_game_enabled():
                await game_shared.respond(interaction, GAME_DISABLED_MESSAGE)
                return

            # 確認画面を開いてからレベルが変わっている場合があるため、価格を再計算する。
            prices: dict[int, int] = {}

            for instance_id in self.instance_ids:
                row = _own_instance(self.user_id, instance_id)
                if row is None:
                    await game_shared.respond(
                        interaction, game_shared.error_message("not_owned")
                    )
                    return

                prices[instance_id] = service.sell_price(
                    str(row["familiar_id"]), int(row["level"])
                )

            outcome = sell_familiars(
                self.user_id,
                prices=prices,
                locked_instance_ids=get_locked_instance_ids(),
            )

            if not outcome["ok"]:
                await game_shared.respond(
                    interaction, game_shared.error_message(outcome["error"])
                )
                return

            await game_shared.respond(
                interaction, embed=service.build_sell_result_embed(outcome)
            )

        except Exception:
            logger.exception(
                "売却に失敗しました: user_id=%s instance_ids=%s",
                self.user_id,
                self.instance_ids,
            )
            await game_shared.respond(interaction, UNEXPECTED_ERROR_MESSAGE)

        finally:
            self.stop()

    @discord.ui.button(label="キャンセル", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()

        try:
            await interaction.response.edit_message(
                content="キャンセルしました。", embed=None, view=None
            )
        except discord.HTTPException:
            logger.warning("売却確認画面の取消表示に失敗しました")


# ==================================================
# パネルEmbed
# ==================================================
GACHA_PANEL_TITLE = "ガチャ"
MANAGE_PANEL_TITLE = "使い魔管理"


def build_gacha_panel_embed() -> discord.Embed:
    """ガチャパネルのEmbedを作る。料金と排出率はマスターデータから取得する。"""

    pool = service.get_pool()

    if pool is None:
        return discord.Embed(
            title=GACHA_PANEL_TITLE,
            description="​\nガチャ設定が読み込めていません。",
            color=game_shared.RANK_COLORS.get("S", 0xFEE75C),
        )

    lines = [
        "​",
        "**使い魔を入手できます。**",
        service.item_line("単発", game_shared.format_coin(pool.single_cost)),
        service.item_line(
            f"{pool.multi_count}連", game_shared.format_coin(pool.multi_cost)
        ),
    ]

    rates = service.rate_lines(pool, service.SLOT_NORMAL)
    if rates:
        lines.extend(["", "**排出率**", *rates])

    if pool.guaranteed_slot:
        guaranteed = service.rate_lines(pool, service.SLOT_GUARANTEED)
        if guaranteed:
            top = service.guaranteed_floor_rank(pool)
            lines.extend(
                [
                    "",
                    f"**{pool.multi_count}連の{pool.guaranteed_slot}枠目（保証枠）**",
                    *guaranteed,
                    f"-# ※{pool.guaranteed_slot}枠目は{top}ランク以上が確定します。",
                ]
            )

    embed = discord.Embed(
        title=GACHA_PANEL_TITLE,
        description="\n".join(lines),
        color=game_shared.RANK_COLORS.get("S", 0xFEE75C),
    )

    notice = service.rank_table_notice(pool)
    if notice:
        embed.set_footer(text=notice)

    return embed


def build_manage_panel_embed() -> discord.Embed:
    """使い魔管理パネルのEmbedを作る（一覧・合成・売却・図鑑を1枚にまとめる）。"""

    master = load_master_data()

    prices = "／".join(
        f"{rank} {master.familiar.sell_base_prices[rank]:,}"
        for rank in reversed(master.familiar.rank_order)
        if rank in master.familiar.sell_base_prices
    )

    return discord.Embed(
        title=MANAGE_PANEL_TITLE,
        description=(
            "​\n"
            "**一覧**\n"
            "-# 所有している使い魔の能力・スキル・画像を確認できます。\n\n"
            "**合成**\n"
            "-# 同じ種類の使い魔を素材にしてレベルアップします。"
            f"（上限はLv.{master.familiar.max_level}）\n"
            "-# ※素材にした使い魔は消費されます。\n\n"
            "**売却**\n"
            "-# 不要な使い魔をcoinへ換金します。\n"
            "-# ※取り消しできません。\n"
            f"-# 基準価格：{prices}\n"
            "-# 売却額は「基準価格 × レベル」です。\n\n"
            "**図鑑**\n"
            "-# 所有している使い魔の画像をめくって眺められます。\n"
            "-# 能力値とスキルも一緒に確認できます。\n\n"
            f"{LOCKED_NOTICE}"
        ),
        color=game_shared.RANK_COLORS.get("B", 0xBEDBFF),
    )


__all__ = [
    "GACHA_PANEL_TITLE",
    "MANAGE_PANEL_TITLE",
    "FamiliarManagePanelView",
    "GachaPanelView",
    "build_gacha_panel_embed",
    "build_manage_panel_embed",
]
