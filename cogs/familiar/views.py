"""使い魔パネル（ガチャ／管理）のDiscord UI層（GAME_SPEC 10.2節）。

常設パネルのViewだけが固定 ``custom_id`` を持ち、押した先の一時的なView・
セレクトには ``custom_id`` を付けません（discord.pyが自動採番します）。
所有状態と使用中判定は、ボタンが見えていても必ずDBから再確認します。
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
    get_same_familiars,
    sell_familiar,
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
class _InstanceSelect(discord.ui.Select):
    """所有使い魔を1体選ぶセレクト。一時Viewのため custom_id は付けない。"""

    def __init__(self, rows: list[dict[str, Any]], *, placeholder: str):
        super().__init__(
            placeholder=placeholder,
            min_values=1,
            max_values=1,
            options=service.build_instance_options(rows),
        )

    async def callback(self, interaction: discord.Interaction):
        view: InstancePageView = self.view  # type: ignore[assignment]
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
        view: InstancePageView = self.view  # type: ignore[assignment]
        await view.move_page(interaction, self.delta)


class InstancePageView(discord.ui.View):
    """所有使い魔をページングしながら1体選ばせる一時View。"""

    placeholder = "使い魔を選択してください"
    header_label = "所有使い魔"

    def __init__(self, *, user_id: int, rows: list[dict[str, Any]], timeout: float = 300):
        super().__init__(timeout=timeout)

        self.user_id = user_id
        self.rows = rows
        self.page = 0

        self._rebuild()

    # ----- ページ操作 -----
    @property
    def page_count(self) -> int:
        return max(1, -(-len(self.rows) // PAGE_SIZE))

    def page_rows(self) -> list[dict[str, Any]]:
        start = self.page * PAGE_SIZE
        return self.rows[start : start + PAGE_SIZE]

    def header(self) -> str:
        text = f"**{self.header_label}**：{len(self.rows)}体（{self.page + 1}/{self.page_count}ページ）"
        return text

    def _rebuild(self) -> None:
        self.clear_items()
        self.add_item(_InstanceSelect(self.page_rows(), placeholder=self.placeholder))

        if self.page_count > 1:
            self.add_item(
                _PageButton(label="◀ 前の25件", delta=-1, disabled=self.page == 0)
            )
            self.add_item(
                _PageButton(
                    label="次の25件 ▶",
                    delta=1,
                    disabled=self.page >= self.page_count - 1,
                )
            )

    async def move_page(self, interaction: discord.Interaction, delta: int) -> None:
        self.page = min(max(self.page + delta, 0), self.page_count - 1)
        self._rebuild()

        await interaction.response.edit_message(content=self.header(), view=self)

    # ----- 共通の実行者確認 -----
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await game_shared.respond(interaction, "この操作は実行者だけが使用できます。")
            return False

        return True

    async def on_select(self, interaction: discord.Interaction, instance_id: int) -> None:
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
            embed = service.build_rate_list_embed(pool)
        except Exception:
            logger.exception("排出使い魔一覧の作成に失敗しました")
            await game_shared.respond(interaction, UNEXPECTED_ERROR_MESSAGE)
            return

        if not embed.fields:
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

            results = service.draw_results(pool, self.count)

            # coin減算と全抽選結果の保存は draw_gacha が1つのDB処理で確定する。
            outcome = draw_gacha(
                interaction.user.id,
                pool_id=pool.pool_id,
                count=self.count,
                cost=self.cost,
                results=results,
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
# 一覧・合成・売却を1つのパネルにまとめる
# ==================================================
class FamiliarManagePanelView(discord.ui.View):
    """使い魔の一覧・合成・売却をまとめた常設View。"""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="使い魔一覧",
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

        view = FamiliarListView(user_id=interaction.user.id, rows=owned)
        await interaction.followup.send(content=view.header(), view=view, ephemeral=True)

    @discord.ui.button(
        label="使い魔合成",
        style=discord.ButtonStyle.success,
        custom_id="familiar:fuse",
    )
    async def start_fusion(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await _open_fusion(interaction)

    @discord.ui.button(
        label="使い魔売却",
        style=discord.ButtonStyle.danger,
        custom_id="familiar:sell",
    )
    async def start_sell(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await _open_sell(interaction)


class FamiliarListView(InstancePageView):
    """所有使い魔を選んで詳細を表示する。"""

    placeholder = "詳細を見る使い魔を選択してください"
    header_label = "所有使い魔"

    async def on_select(self, interaction: discord.Interaction, instance_id: int) -> None:
        await interaction.response.defer(ephemeral=True)

        row = _own_instance(self.user_id, instance_id)
        if row is None:
            await interaction.followup.send(
                game_shared.error_message("not_owned"), ephemeral=True
            )
            return

        try:
            embed, thumbnail = service.build_familiar_detail_embed(row)
        except Exception:
            logger.exception("使い魔詳細の作成に失敗しました: instance_id=%s", instance_id)
            await interaction.followup.send(UNEXPECTED_ERROR_MESSAGE, ephemeral=True)
            return

        if thumbnail is not None:
            await interaction.followup.send(embed=embed, file=thumbnail, ephemeral=True)
        else:
            await interaction.followup.send(embed=embed, ephemeral=True)


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
        await interaction.followup.send(
            (
                "合成できる使い魔がありません。\n"
                "-# 同じ種類を2体以上所有し、最大レベル未満の使い魔が必要です。\n"
                f"{LOCKED_NOTICE}"
            ),
            ephemeral=True,
        )
        return

    view = FuseBaseView(user_id=interaction.user.id, rows=bases)
    await interaction.followup.send(
        content=f"{view.header()}\n{LOCKED_NOTICE}", view=view, ephemeral=True
    )


class FuseBaseView(InstancePageView):
    """レベルアップさせるベース個体を選ぶ。"""

    placeholder = "レベルアップさせる使い魔を選択してください"
    header_label = "合成できる使い魔"

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
            materials = service.exclude_locked(
                get_same_familiars(
                    self.user_id,
                    str(base["familiar_id"]),
                    exclude_instance_id=instance_id,
                ),
                locked,
            )
        except Exception:
            logger.exception("合成素材の取得に失敗しました: instance_id=%s", instance_id)
            await interaction.followup.send(UNEXPECTED_ERROR_MESSAGE, ephemeral=True)
            return

        if instance_id in locked:
            await interaction.followup.send(
                game_shared.error_message("in_use"), ephemeral=True
            )
            return

        if not materials:
            await interaction.followup.send(
                (
                    "素材にできる同じ種類の使い魔がありません。\n"
                    f"{LOCKED_NOTICE}"
                ),
                ephemeral=True,
            )
            return

        view = FuseMaterialView(
            user_id=self.user_id,
            rows=materials,
            base_instance_id=instance_id,
        )
        await interaction.followup.send(
            content=(
                f"ベース：**{service.instance_title(base)}**\n"
                "-# 素材にした使い魔は消費されます。\n"
                f"{view.header()}"
            ),
            view=view,
            ephemeral=True,
        )


class FuseMaterialView(InstancePageView):
    """素材にする個体を選び、合成を実行する。"""

    placeholder = "素材にする使い魔を選択してください"
    header_label = "素材候補"

    def __init__(
        self,
        *,
        user_id: int,
        rows: list[dict[str, Any]],
        base_instance_id: int,
        timeout: float = 300,
    ):
        self.base_instance_id = base_instance_id
        super().__init__(user_id=user_id, rows=rows, timeout=timeout)

    async def on_select(self, interaction: discord.Interaction, instance_id: int) -> None:
        await interaction.response.defer(ephemeral=True)

        master = load_master_data()

        try:
            outcome = fuse_familiar(
                self.user_id,
                base_instance_id=self.base_instance_id,
                material_instance_id=instance_id,
                max_level=master.familiar.max_level,
                locked_instance_ids=get_locked_instance_ids(),
            )
        except Exception:
            logger.exception(
                "合成に失敗しました: base=%s material=%s",
                self.base_instance_id,
                instance_id,
            )
            await interaction.followup.send(UNEXPECTED_ERROR_MESSAGE, ephemeral=True)
            return

        if not outcome["ok"]:
            await interaction.followup.send(
                game_shared.error_message(outcome["error"]), ephemeral=True
            )
            return

        embed = service.build_fusion_result_embed(
            str(outcome["familiar_id"]), level=int(outcome["level"])
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

        self.stop()


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
        content=f"{view.header()}\n{LOCKED_NOTICE}", view=view, ephemeral=True
    )


class SellSelectView(InstancePageView):
    """売却する個体を選ぶ。実行前に必ず確認画面を挟む。"""

    placeholder = "売却する使い魔を選択してください"
    header_label = "売却できる使い魔"

    async def on_select(self, interaction: discord.Interaction, instance_id: int) -> None:
        await interaction.response.defer(ephemeral=True)

        row = _own_instance(self.user_id, instance_id)
        if row is None:
            await interaction.followup.send(
                game_shared.error_message("not_owned"), ephemeral=True
            )
            return

        if instance_id in get_locked_instance_ids():
            await interaction.followup.send(
                game_shared.error_message("in_use"), ephemeral=True
            )
            return

        price = service.sell_price(str(row["familiar_id"]), int(row["level"]))
        embed = service.build_sell_confirm_embed(row, price=price)

        await interaction.followup.send(
            embed=embed,
            view=SellConfirmView(
                user_id=self.user_id, instance_id=instance_id, price=price
            ),
            ephemeral=True,
        )


class SellConfirmView(discord.ui.View):
    """売却の最終確認。"""

    def __init__(self, *, user_id: int, instance_id: int, price: int):
        super().__init__(timeout=180)

        self.user_id = user_id
        self.instance_id = instance_id
        self.price = price
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

            row = _own_instance(self.user_id, self.instance_id)
            if row is None:
                await game_shared.respond(
                    interaction, game_shared.error_message("not_owned")
                )
                return

            # 確認画面を開いてからレベルが変わっている場合があるため、価格を再計算する。
            price = service.sell_price(str(row["familiar_id"]), int(row["level"]))

            outcome = sell_familiar(
                self.user_id,
                instance_id=self.instance_id,
                price=price,
                locked_instance_ids=get_locked_instance_ids(),
            )

            if not outcome["ok"]:
                await game_shared.respond(
                    interaction, game_shared.error_message(outcome["error"])
                )
                return

            await game_shared.respond(
                interaction,
                (
                    f"**{service.instance_title(row)}** を売却しました。\n"
                    f"受取額：**{game_shared.format_coin(int(outcome['price']))}**"
                ),
            )

        except Exception:
            logger.exception(
                "売却に失敗しました: user_id=%s instance_id=%s",
                self.user_id,
                self.instance_id,
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
GACHA_PANEL_TITLE = "使い魔ガチャ"
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
        f"単発：{game_shared.format_coin(pool.single_cost)}",
        f"{pool.multi_count}連：{game_shared.format_coin(pool.multi_cost)}",
    ]

    if pool.guaranteed_slot:
        lines.append(
            f"-# {pool.multi_count}連の{pool.guaranteed_slot}枠目はBランク以上を保証します。"
        )

    embed = discord.Embed(
        title=GACHA_PANEL_TITLE,
        description="\n".join(lines),
        color=game_shared.RANK_COLORS.get("S", 0xFEE75C),
    )

    table = service.build_rank_table(pool)
    if table:
        embed.add_field(
            name="排出率",
            value="\n".join(
                f"{game_shared.rank_label(rank)}：{service.format_rate(permille)}"
                for rank, permille in reversed(table)
            ),
            inline=False,
        )

    notice = service.rank_table_notice(pool)
    if notice:
        embed.set_footer(text=notice)

    return embed


def build_manage_panel_embed() -> discord.Embed:
    """使い魔管理パネルのEmbedを作る（一覧・合成・売却を1枚にまとめる）。"""

    master = load_master_data()

    prices = "／".join(
        f"{game_shared.rank_label(rank)} {master.familiar.sell_base_prices[rank]:,}"
        for rank in reversed(master.familiar.rank_order)
        if rank in master.familiar.sell_base_prices
    )

    return discord.Embed(
        title=MANAGE_PANEL_TITLE,
        description=(
            "​\n"
            "**使い魔一覧**\n"
            "-# 所有している使い魔の能力・スキル・画像を確認できます。\n\n"
            "**使い魔合成**\n"
            "-# 同じ種類の使い魔を素材にしてレベルアップします。\n"
            f"-# 上限はLv.{master.familiar.max_level}です。素材にした使い魔は消費されます。\n\n"
            "**使い魔売却**\n"
            "-# 不要な使い魔をcoinへ換金します。取り消しできません。\n"
            f"-# 基準価格：{prices}\n"
            "-# 売却額は「基準価格 × (レベル + 1)」です。\n\n"
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
