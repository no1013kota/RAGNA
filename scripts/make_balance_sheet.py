"""ゲームバランスを検討するためのExcelを作る。

数式入りなので、②のLv1能力値と③パラメータを書き換えると、
ガチャ試算・成長試算・デッキ試算・AIプロンプトが自動で計算し直されます。

    pip install openpyxl        # 初回だけ。Bot本体には不要なライブラリです
    python scripts/make_balance_sheet.py

出力先: docs/balance/バランス設計シート.xlsx

■ このシートが前提にしている成長のしかた
    Lvn の能力値 = Lv1の能力値 ＋ 固定値 ×（n − 1）
    SPDは成長しません（Lv1の値のまま）

ゲームで最初に手に入るのは Lv1 です。②のLv1列には、いまのマスターデータから
計算した Lv1 の能力値を初期値として入れてあります（手で書き換えられます）。
"""

from __future__ import annotations

import json
import sys

from itertools import combinations_with_replacement
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side


ROOT = Path(__file__).resolve().parents[1]
MASTER = ROOT / "data" / "master"
OUT_DIR = ROOT / "docs" / "balance"
OUT_PATH = OUT_DIR / "バランス設計シート.xlsx"

INPUT_FILL = PatternFill("solid", fgColor="FFF3C4")  # 書き換えてよい
CALC_FILL = PatternFill("solid", fgColor="EAF3FF")  # 自動計算
HEAD_FILL = PatternFill("solid", fgColor="D9D9D9")
TITLE_FILL = PatternFill("solid", fgColor="2B2D31")

TITLE_FONT = Font(bold=True, size=14, color="FFFFFF")
HEAD_FONT = Font(bold=True, size=10)
NOTE_FONT = Font(size=9, color="666666")
WARN_FONT = Font(bold=True, color="CC0000")

THIN = Side(style="thin", color="BBBBBB")
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

RANKS = ("C", "B", "A", "S")
RANK_ORDER = {"S": 0, "A": 1, "B": 2, "C": 3}
GENDER = {"male": "男性", "female": "女性", "none": "なし", None: "—"}

SH_GUIDE = "① 使い方"
SH_LIST = "② 使い魔一覧"
SH_PARAM = "③ パラメータ"
SH_GACHA = "④ ガチャ試算"
SH_GROWTH = "⑤ 成長試算"
SH_DECK = "⑥ デッキ試算"
SH_PROMPT = "⑦ AIプロンプト"

PARAM = f"'{SH_PARAM}'!"
GROWTH = f"'{SH_GROWTH}'!"
LIST = f"'{SH_LIST}'!"

# 表示するレベル（Lv1は入力、それ以外は計算）
SHOW_LEVELS = (1, 5, 10)

# ==================================================
# ③パラメータの行番号。数値はすべて B列（成長量だけ B=HP, C=ATK）。
# ==================================================
R_COUNT = 5  # C=5, B=6, A=7, S=8
R_COUNT_SUM = 9
R_RATE = 13  # C=13, B=14, A=15, S=16
R_RATE_SUM = 17
R_GACHA_COST = 21
R_GROW = 25  # C=25..S=28（B=固定値HP, C=固定値ATK）
R_MAX_LEVEL = 32
R_MATERIALS = 33
R_FUSION_RATE = 37
R_SELL = 41  # C=41, B=42, A=43, S=44
R_DECK_COST = 48
R_DECK_UNITS = 49

# ②使い魔一覧：Lv1がE〜G、Lv5がH〜J、Lv10がK〜M
LIST_START = 5
LIST_LV1_COLS = ("E", "F", "G")

# ⑤成長試算：ランクごとに HP/ATK/SPD の3行。Lv1がC列。
G_START = 5
G_ROWS = {rank: G_START + index * 3 for index, rank in enumerate(RANKS)}


def level_column(level: int) -> str:
    """⑤成長試算で、そのレベルが入る列名を返す（Lv1=C）。"""

    return chr(ord("C") + level - 1)


def load(name: str) -> dict:
    return json.loads((MASTER / f"{name}.json").read_text(encoding="utf-8"))


def title(ws, row: int, text: str, width: int) -> None:
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=width)
    cell = ws.cell(row=row, column=1, value=text)
    cell.fill = TITLE_FILL
    cell.font = TITLE_FONT
    cell.alignment = Alignment(vertical="center")
    ws.row_dimensions[row].height = 22


def section(ws, row: int, text: str, width: int) -> None:
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=width)
    cell = ws.cell(row=row, column=1, value=text)
    cell.fill = HEAD_FILL
    cell.font = HEAD_FONT


def note(ws, row: int, text: str, width: int) -> None:
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=width)
    ws.cell(row=row, column=1, value=text).font = NOTE_FONT


def header(ws, row: int, labels: list[str]) -> None:
    for offset, label in enumerate(labels):
        cell = ws.cell(row=row, column=1 + offset, value=label)
        cell.fill = HEAD_FILL
        cell.font = HEAD_FONT
        cell.border = BOX
        cell.alignment = Alignment(horizontal="center", wrap_text=True)


def cell_in(ws, row: int, col: int, value, fmt: str | None = None):
    cell = ws.cell(row=row, column=col, value=value)
    cell.fill = INPUT_FILL
    cell.border = BOX
    if fmt:
        cell.number_format = fmt
    return cell


def cell_calc(ws, row: int, col: int, value, fmt: str | None = None):
    cell = ws.cell(row=row, column=col, value=value)
    cell.fill = CALC_FILL
    cell.border = BOX
    if fmt:
        cell.number_format = fmt
    return cell


def cell_flat(ws, row: int, col: int, value, fmt: str | None = None):
    cell = ws.cell(row=row, column=col, value=value)
    cell.border = BOX
    if fmt:
        cell.number_format = fmt
    return cell


def widths(ws, spec: dict[str, int]) -> None:
    for column, width in spec.items():
        ws.column_dimensions[column].width = width


def grown(lv1_ref: str, rank_row: int, level_ref: str, stat: str) -> str:
    """Lv1の値から成長させた能力値の数式（先頭の "=" は付けない）。

    Lvn = Lv1 ＋ 固定値 ×（n − 1）
    """

    column = "B" if stat == "hp" else "C"
    return f"{lv1_ref}+{PARAM}{column}{rank_row}*({level_ref}-1)"


def lv1_stats(fam: dict, balance: dict) -> tuple[int, int, int]:
    """いまのマスターデータでの Lv1 の能力値を返す（②の初期値に使う）。"""

    from decimal import Decimal, ROUND_HALF_UP

    def half_up(value: float) -> int:
        return int(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    f = balance["familiar"]
    hp = half_up(fam["base_hp"] * (1 + f["hp_growth_rate_per_level"]))
    atk = half_up(fam["base_atk"] * (1 + f["atk_growth_rate_per_level"]))
    speed = min(f["speed_max"], fam["speed"] + f["speed_growth_value"])
    return hp, atk, speed


# ==================================================
# ① 使い方
# ==================================================
def build_guide(wb: Workbook) -> None:
    ws = wb.create_sheet(SH_GUIDE)
    widths(ws, {"A": 3, "B": 104})

    title(ws, 1, "  ラグナオンライン バランス設計シート", 2)

    lines = [
        "",
        "■ 触ってよいのは黄色のセルだけです",
        "　黄色 … あなたが書き換えるところ",
        "　水色 … 自動計算。書き換えると数式が消えるので触らないでください",
        "",
        "■ 書き換えるところは2か所です",
        "　② 使い魔一覧のE〜G列 … 使い魔ごとの Lv1 の HP・ATK・SPD",
        "　③ パラメータ　　　　 … 使い魔の数、排出率、成長量、費用など",
        "",
        "■ 成長のしかた",
        "　　Lvn の能力値 ＝ Lv1の能力値 ＋ 固定値 ×（n − 1）",
        "　　SPDは成長しません（Lv1の値のまま）",
        "　固定値は③パラメータでランクごとに決めます。0にすると伸びません。",
        "",
        "■ シートの並び",
        "　② 使い魔一覧　… 全50体の能力値（Lv1・Lv5・Lv10）とスキル",
        "　③ パラメータ　… 使い魔の数、排出率、成長量、ガチャ費用、合成費用、売却額",
        "　④ ガチャ試算　… 最大レベルにするまでに必要な単発回数とcoin",
        "　⑤ 成長試算　　… ランクごとの平均能力値を Lv1〜Lv10 で並べたもの",
        "　⑥ デッキ試算　… 合計COST20以内の編成と、Lv1・Lv5・Lv10のステータス",
        "　⑦ AIプロンプト … 生成AIに戦闘シミュレーションを頼むための文章",
        "",
        "■ 今回の前提",
        "　・レベルは Lv1 が最初です。ガチャで出るのも Lv1 です",
        "　・ガチャは単発だけで計算しています（10連・保証枠は考えていません）",
        "　・プレイヤーの収入は考えていません。必要coinだけを出しています",
        "",
        "■ 覚えておくと便利なこと",
        "　・最大レベルにするには「同じ使い魔」が（最大レベル−1）×素材数＋1体 必要です",
        "　・ATKは4〜12程度です。固定で+1すると1回で10〜25%増える計算になります",
        "　・SはAよりATKもSPDも低めです。Sの強みはHPとスキルとCOSTです",
        "　・スキルはATK・最大HPの割合で効くので、能力値を上げると威力も上がります",
        "",
        "■ 決まったら",
        "　数値が決まったら教えてください。data/master/*.json へ反映して",
        "　Discordへ配信します。手で書き換える必要はありません。",
        "",
        "※ このファイルは scripts/make_balance_sheet.py で作り直せます。",
        "　 ②のLv1の初期値とスキルは data/master/ の最新の内容から作っています。",
    ]
    for index, text in enumerate(lines, start=2):
        cell = ws.cell(row=index, column=2, value=text)
        if text.startswith("■"):
            cell.font = Font(bold=True, size=11)
        elif text.startswith("※"):
            cell.font = NOTE_FONT


# ==================================================
# ② 使い魔一覧
# ==================================================
def build_list(wb: Workbook, familiars: list[dict], skills: dict, balance: dict) -> int:
    ws = wb.create_sheet(SH_LIST)
    widths(ws, {
        "A": 15, "B": 7, "C": 7, "D": 7,
        "E": 8, "F": 8, "G": 8,
        "H": 8, "I": 8, "J": 8,
        "K": 8, "L": 8, "M": 8,
        "N": 14, "O": 54, "P": 10,
        "Q": 14, "R": 54, "S": 16,
    })

    title(ws, 1, "  ② 使い魔一覧　— 能力値とスキル", 19)
    note(ws, 2, "　　黄色のLv1列（E〜G）が入力です。Lv5・Lv10は「Lv1＋固定値×(レベル−1)」で計算します。SPDは成長しません。", 19)

    header(ws, 3, ["", "", "", "", "Lv1（入力）", "", "", "Lv5", "", "", "Lv10", "", "",
                   "ACTIVEスキル", "", "", "PASSIVEスキル", "", ""])
    header(ws, 4, ["名前", "ランク", "COST", "性別",
                   "HP", "ATK", "SPD", "HP", "ATK", "SPD", "HP", "ATK", "SPD",
                   "名前", "内容", "使用回数",
                   "名前", "内容", "発動タイミング"])

    trigger_label = {
        "battle_start": "バトル開始時", "on_attack": "攻撃した時",
        "on_damaged": "ダメージを受けた時", "on_defeat": "誰かが倒れた時",
        "before_defeat": "自分が倒れる直前", "turn_start": "自分のターン開始時",
        "always": "常時", "before_status_apply": "状態異常を受ける直前",
        "on_revive": "蘇生された時", "on_kill": "敵を倒した時",
        "round_start": "ラウンド開始時", "before_action": "行動の直前",
        "on_heal": "回復した時", "on_skill": "スキルを使った時",
    }

    rows = sorted(familiars, key=lambda f: (RANK_ORDER[f["rank"]], -f["base_atk"]))

    for index, fam in enumerate(rows):
        row = LIST_START + index
        rank_row = R_GROW + RANKS.index(fam["rank"])
        hp, atk, speed = lv1_stats(fam, balance)

        cell_flat(ws, row, 1, fam["name"])
        cell_flat(ws, row, 2, fam["rank"])
        cell_flat(ws, row, 3, fam["cost"])
        cell_flat(ws, row, 4, GENDER.get(fam.get("gender"), "—"))

        # Lv1（手動入力）
        cell_in(ws, row, 5, hp)
        cell_in(ws, row, 6, atk)
        cell_in(ws, row, 7, speed)

        # Lv5（固定）と最大レベル（③の設定）
        for offset, level_ref in ((0, "5"), (1, f"{PARAM}B{R_MAX_LEVEL}")):
            base_col = 8 + offset * 3
            cell_calc(ws, row, base_col, "=" + grown(f"E{row}", rank_row, level_ref, "hp"), "0")
            cell_calc(ws, row, base_col + 1, "=" + grown(f"F{row}", rank_row, level_ref, "atk"), "0")
            cell_calc(ws, row, base_col + 2, f"=G{row}", "0")  # SPDは成長しない

        active = next(
            (skills[s] for s in fam.get("skills", [])
             if skills.get(s, {}).get("skill_type") == "active"), None)
        passive = next(
            (skills[s] for s in fam.get("skills", [])
             if skills.get(s, {}).get("skill_type") == "passive"), None)

        if active:
            cell_flat(ws, row, 14, active["name"])
            cell_flat(ws, row, 15, active["description"]).alignment = Alignment(wrap_text=True, vertical="top")
            uses = active.get("max_uses_per_battle")
            cell_flat(ws, row, 16, f"1バトル{uses}回" if uses else "制限なし")
        else:
            for col in (14, 15, 16):
                cell_flat(ws, row, col, "—")

        if passive:
            cell_flat(ws, row, 17, passive["name"])
            cell_flat(ws, row, 18, passive["description"]).alignment = Alignment(wrap_text=True, vertical="top")
            cell_flat(ws, row, 19, trigger_label.get(passive.get("trigger"), passive.get("trigger") or "—"))
        else:
            for col in (17, 18, 19):
                cell_flat(ws, row, col, "—")

    ws.freeze_panes = "E5"
    ws.auto_filter.ref = f"A4:S{4 + len(rows)}"
    return LIST_START + len(rows) - 1  # 最終行


# ==================================================
# ③ パラメータ
# ==================================================
def build_params(wb: Workbook, balance: dict, gacha: dict, counts: dict) -> None:
    ws = wb.create_sheet(SH_PARAM)
    widths(ws, {"A": 26, "B": 16, "C": 16, "D": 12, "E": 12, "F": 54})

    pool = gacha["pools"][0]
    fam = balance["familiar"]
    battle = balance["battle"]

    title(ws, 1, "  ③ パラメータ　— 黄色のセルを書き換えてください", 6)
    note(ws, 2, "　　使い魔ごとのLv1能力値は②使い魔一覧のE〜G列にあります。", 6)

    # --- 使い魔の数 ---
    section(ws, 3, "■ 各ランクの使い魔の数", 6)
    header(ws, 4, ["ランク", "体数", "", "", "", "説明"])
    for index, rank in enumerate(RANKS):
        row = R_COUNT + index
        cell_flat(ws, row, 1, f"{rank}ランク")
        cell_in(ws, row, 2, counts[rank])
    ws.cell(row=R_COUNT, column=6,
            value="数を減らすと、同じ使い魔が出やすくなります（＝合成しやすい）").font = NOTE_FONT
    cell_flat(ws, R_COUNT_SUM, 1, "合計")
    cell_calc(ws, R_COUNT_SUM, 2, f"=SUM(B{R_COUNT}:B{R_COUNT+3})")

    # --- 排出率 ---
    section(ws, 11, "■ ガチャの排出率（単発）", 6)
    header(ws, 12, ["ランク", "排出率（%）", "", "", "", "説明"])
    for index, rank in enumerate(RANKS):
        row = R_RATE + index
        cell_flat(ws, row, 1, f"{rank}ランク")
        cell_in(ws, row, 2, pool["rates"]["normal"][rank] / 10, "0.0")
    ws.cell(row=R_RATE, column=6, value="合計が100%になるようにしてください").font = NOTE_FONT
    cell_flat(ws, R_RATE_SUM, 1, "合計（%）")
    cell_calc(ws, R_RATE_SUM, 2, f"=SUM(B{R_RATE}:B{R_RATE+3})", "0.0")
    ws.cell(row=R_RATE_SUM, column=6,
            value=f'=IF(ROUND(B{R_RATE_SUM},3)=100,"OK","★合計が100%ではありません")').font = WARN_FONT

    # --- ガチャ費用 ---
    section(ws, 19, "■ ガチャ費用", 6)
    header(ws, 20, ["項目", "値（coin）", "", "", "", "説明"])
    cell_flat(ws, R_GACHA_COST, 1, "単発の費用")
    cell_in(ws, R_GACHA_COST, 2, pool["single_cost"], "#,##0")
    ws.cell(row=R_GACHA_COST, column=6, value="1回引くのにかかるcoin").font = NOTE_FONT

    # --- 成長量 ---
    section(ws, 23, "■ 合成1回あたりの成長量（固定値）", 6)
    header(ws, 24, ["ランク", "固定値 HP", "固定値 ATK", "", "", "説明"])
    fixed_hp = {"C": 0, "B": 1, "A": 2, "S": 3}
    fixed_atk = {"C": 0, "B": 0, "A": 1, "S": 1}
    for index, rank in enumerate(RANKS):
        row = R_GROW + index
        cell_flat(ws, row, 1, f"{rank}ランク")
        cell_in(ws, row, 2, fixed_hp[rank])
        cell_in(ws, row, 3, fixed_atk[rank])
    ws.cell(row=R_GROW, column=6,
            value="Lvn の能力値 ＝ Lv1の能力値 ＋ 固定値 ×（n − 1）").font = NOTE_FONT
    ws.cell(row=R_GROW + 1, column=6,
            value="0にすると、そのランクは合成しても伸びません").font = NOTE_FONT
    ws.cell(row=R_GROW + 2, column=6,
            value="SPDは成長しません（Lv1の値のまま）").font = NOTE_FONT

    # --- 成長の共通設定 ---
    section(ws, 30, "■ 成長の共通設定", 6)
    header(ws, 31, ["項目", "値", "", "", "", "説明"])
    for row, label, value, memo in (
        (R_MAX_LEVEL, "最大レベル", fam["max_level"], "Lv1が最初です。下げると必要な体数が減ります"),
        (R_MATERIALS, "1レベルに必要な素材数", 1, "必要な体数 =（最大レベル−1）× この数 ＋ 1"),
    ):
        cell_flat(ws, row, 1, label)
        cell_in(ws, row, 2, value)
        ws.cell(row=row, column=6, value=memo).font = NOTE_FONT

    # --- 合成費用 ---
    section(ws, 35, "■ 合成費用", 6)
    header(ws, 36, ["項目", "値（%）", "", "", "", "説明"])
    cell_flat(ws, R_FUSION_RATE, 1, "素材1体あたり")
    cell_in(ws, R_FUSION_RATE, 2, fam["fusion_cost_rate_per_material"] * 100, "0.0")
    ws.cell(row=R_FUSION_RATE, column=6,
            value="売却額に対する割合。50なら売却額の半分。0にすると合成は無料").font = NOTE_FONT

    # --- 売却額 ---
    section(ws, 39, "■ 売却額（Lv1）", 6)
    header(ws, 40, ["ランク", "売却額（coin）", "", "", "", "説明"])
    for index, rank in enumerate(RANKS):
        row = R_SELL + index
        cell_flat(ws, row, 1, f"{rank}ランク")
        cell_in(ws, row, 2, fam["sell_base_prices"][rank], "#,##0")
    ws.cell(row=R_SELL, column=6, value="合成費用もこの金額を基準に決まります").font = NOTE_FONT

    # --- バトルの枠 ---
    section(ws, 46, "■ バトルの枠（⑥デッキ試算が使います）", 6)
    header(ws, 47, ["項目", "値", "", "", "", "説明"])
    for row, label, value, memo in (
        (R_DECK_COST, "合計COST上限", battle["max_total_cost"], "編成できる合計COST"),
        (R_DECK_UNITS, "出場できる体数", battle["max_units"], "1ギルドが出せる使い魔の数"),
    ):
        cell_flat(ws, row, 1, label)
        cell_in(ws, row, 2, value)
        ws.cell(row=row, column=6, value=memo).font = NOTE_FONT

    ws.freeze_panes = "A5"


# ==================================================
# ④ ガチャ試算
# ==================================================
def build_gacha(wb: Workbook) -> None:
    ws = wb.create_sheet(SH_GACHA)
    widths(ws, {"A": 18, "B": 12, "C": 20, "D": 20, "E": 22, "F": 18, "G": 18, "H": 40})

    title(ws, 1, "  ④ ガチャ試算　— 単発だけで計算しています", 8)
    note(ws, 2, "　　③パラメータを変えると自動で変わります。10連・保証枠は考慮していません。", 8)

    section(ws, 4, "■ 最大レベルに必要な体数", 8)
    cell_flat(ws, 5, 1, "必要な体数")
    cell_calc(ws, 5, 2, f"=({PARAM}B{R_MAX_LEVEL}-1)*{PARAM}B{R_MATERIALS}+1", "0")
    ws.cell(row=5, column=8, value="（最大レベル−1）× 1レベルの素材数 ＋ 本体1体").font = NOTE_FONT

    section(ws, 7, "■ Lv1から最大レベルにするまで", 8)
    header(ws, 8, ["ランク", "使い魔の数", "特定1体が出る確率（%）",
                   "1体そろえる単発回数", "最大Lvに必要な単発回数",
                   "ガチャ費用（coin）", "合成費用（coin）", "合計（coin）"])

    for index, rank in enumerate(RANKS):
        row = 9 + index
        cnt = f"{PARAM}B{R_COUNT + index}"
        rate = f"{PARAM}B{R_RATE + index}"
        sell = f"{PARAM}B{R_SELL + index}"

        cell_flat(ws, row, 1, f"{rank}ランク")
        cell_calc(ws, row, 2, f"={cnt}")
        cell_calc(ws, row, 3, f"=IF({cnt}=0,0,{rate}/{cnt})", "0.0000")
        cell_calc(ws, row, 4, f'=IF(C{row}=0,"—",100/C{row})', "#,##0")
        cell_calc(ws, row, 5, f'=IF(C{row}=0,"—",$B$5*100/C{row})', "#,##0")
        cell_calc(ws, row, 6, f'=IF(C{row}=0,"—",E{row}*{PARAM}B{R_GACHA_COST})', "#,##0")
        cell_calc(ws, row, 7, f"=($B$5-1)*{sell}*{PARAM}B{R_FUSION_RATE}/100", "#,##0")
        cell_calc(ws, row, 8, f'=IF(C{row}=0,"—",F{row}+G{row})', "#,##0")

    section(ws, 14, "■ 参考：1体だけ引きたい場合", 8)
    header(ws, 15, ["ランク", "そのランクが出る確率（%）", "1体出るまでの単発回数",
                    "そのぶんのcoin", "", "", "", ""])
    for index, rank in enumerate(RANKS):
        row = 16 + index
        rate = f"{PARAM}B{R_RATE + index}"
        cell_flat(ws, row, 1, f"{rank}ランク")
        cell_calc(ws, row, 2, f"={rate}", "0.0")
        cell_calc(ws, row, 3, f'=IF({rate}=0,"—",100/{rate})', "#,##0")
        cell_calc(ws, row, 4, f'=IF({rate}=0,"—",C{row}*{PARAM}B{R_GACHA_COST})', "#,##0")

    ws.freeze_panes = "A9"


# ==================================================
# ⑤ 成長試算
# ==================================================
def build_growth(wb: Workbook, list_last_row: int) -> None:
    ws = wb.create_sheet(SH_GROWTH)
    widths(ws, {"A": 10, "B": 8, **{chr(ord("C") + i): 8 for i in range(10)}, "M": 15})

    title(ws, 1, "  ⑤ 成長試算　— ランクごとの平均能力値を Lv1〜Lv10 で並べたもの", 13)
    note(ws, 2, "　　Lv1は②使い魔一覧の平均です。②やLv③の成長量を変えると全部動きます。", 13)
    note(ws, 3, "　　Lvn ＝ Lv1 ＋ 固定値 ×（n − 1）。SPDは成長しません。", 13)

    header(ws, 4, ["ランク", "項目"] + [f"Lv{level}" for level in range(1, 11)]
                  + ["Lv1→Lv10（倍）"])

    rank_range = f"{LIST}$B${LIST_START}:$B${list_last_row}"

    for index, rank in enumerate(RANKS):
        rank_row = R_GROW + index
        base_row = G_ROWS[rank]

        for offset, (label, col) in enumerate(
            (("HP", LIST_LV1_COLS[0]), ("ATK", LIST_LV1_COLS[1]), ("SPD", LIST_LV1_COLS[2]))
        ):
            row = base_row + offset
            cell_flat(ws, row, 1, f"{rank}ランク" if offset == 0 else "")
            cell_flat(ws, row, 2, label)

            # Lv1 は②の同ランクの平均
            cell_calc(
                ws, row, 3,
                f'=AVERAGEIF({rank_range},"{rank}",'
                f"{LIST}${col}${LIST_START}:${col}${list_last_row})",
                "0.0",
            )

            for level in range(2, 11):
                col_index = 2 + level
                if label == "SPD":
                    body = "$C" + str(row)  # SPDは成長しない
                else:
                    body = grown("$C" + str(row), rank_row, str(level), label.lower())
                cell_calc(
                    ws, row, col_index,
                    f'=IF({level}>{PARAM}B{R_MAX_LEVEL},"—",{body})',
                    "0" if label == "SPD" else "0.0",
                )
            cell_calc(
                ws, row, 13,
                f'=IF(L{row}="—","—",L{row}/C{row})',
                "0.00",
            )

    section(ws, 18, "■ 見るときのポイント", 13)
    points = [
        "　・Lv1がゲームで最初に手に入る状態です。そこから合成でLv10まで上げます。",
        "　・ATKは4〜12程度です。固定で+1すると1回で10〜25%増える計算になります。",
        "　・SはAよりATKもSPDも低めです。SはHPとスキルとCOSTで差がついています。",
        "　・SPDは成長しないので、行動順は最初から最後まで変わりません。",
        "　・スキルはATK・最大HPの割合で効くため、能力値を上げるとスキル威力も一緒に上がります。",
    ]
    for offset, text in enumerate(points):
        ws.cell(row=19 + offset, column=1, value=text).font = NOTE_FONT
        ws.merge_cells(start_row=19 + offset, start_column=1, end_row=19 + offset, end_column=13)

    ws.freeze_panes = "C5"


# ==================================================
# ⑥ デッキ試算
# ==================================================
def build_deck(wb: Workbook, balance: dict) -> None:
    ws = wb.create_sheet(SH_DECK)
    widths(ws, {
        "A": 7, "B": 7, "C": 7, "D": 7, "E": 8, "F": 8,
        "G": 12, "H": 12, "I": 13,
        "J": 12, "K": 12, "L": 13,
        "M": 13, "N": 13, "O": 14, "P": 20,
    })

    cost = {"C": 2, "B": 3, "A": 4, "S": 5}
    max_cost = balance["battle"]["max_total_cost"]
    max_units = balance["battle"]["max_units"]

    title(ws, 1, f"  ⑥ デッキ試算　— 合計COST{max_cost}以内の編成と、そのステータス", 16)
    note(ws, 2, "　　ランクの組み合わせごとに、⑤成長試算の平均能力値を足し合わせています。", 16)
    note(ws, 3, "　　HPとATKは編成ぶんの合計、SPDは1体あたりの平均です。", 16)

    labels = ["S", "A", "B", "C", "体数", "COST"]
    for level in SHOW_LEVELS:
        labels += [f"Lv{level} HP合計", f"Lv{level} ATK合計", f"Lv{level} SPD平均"]
    labels.append("備考")
    header(ws, 5, labels)

    decks = []
    for size in range(1, max_units + 1):
        for combo in combinations_with_replacement("SABC", size):
            total = sum(cost[rank] for rank in combo)
            if total <= max_cost:
                decks.append(({rank: combo.count(rank) for rank in "SABC"}, size, total))

    decks.sort(key=lambda d: (-d[1], -d[2], -d[0]["S"], -d[0]["A"]))

    stat_rows = {
        "HP": {rank: G_ROWS[rank] for rank in RANKS},
        "ATK": {rank: G_ROWS[rank] + 1 for rank in RANKS},
        "SPD": {rank: G_ROWS[rank] + 2 for rank in RANKS},
    }

    def total_of(row_map: dict[str, int], col: str, deck_row: int) -> str:
        return "+".join(
            f"{chr(ord('A') + index)}{deck_row}*{GROWTH}{col}{row_map[rank]}"
            for index, rank in enumerate(("S", "A", "B", "C"))
        )

    for index, (counts, size, total) in enumerate(decks):
        row = 6 + index
        for offset, rank in enumerate(("S", "A", "B", "C")):
            cell_flat(ws, row, 1 + offset, counts[rank])
        cell_flat(ws, row, 5, size)
        cell_calc(ws, row, 6, total)

        for slot, level in enumerate(SHOW_LEVELS):
            col = level_column(level)
            base = 7 + slot * 3
            guard = f'{GROWTH}{col}{stat_rows["HP"]["S"]}="—"'
            cell_calc(ws, row, base,
                      f'=IF({guard},"—",{total_of(stat_rows["HP"], col, row)})', "0")
            cell_calc(ws, row, base + 1,
                      f'=IF({guard},"—",{total_of(stat_rows["ATK"], col, row)})', "0")
            cell_calc(ws, row, base + 2,
                      f'=IF({guard},"—",({total_of(stat_rows["SPD"], col, row)})/E{row})', "0")

        if counts["S"] == size:
            memo = "S単一"
        elif counts["C"] == size:
            memo = "C単一（最安）"
        elif total == max_cost:
            memo = "COST上限ぴったり"
        else:
            memo = ""
        cell_flat(ws, row, 16, memo)

    last = 5 + len(decks)
    ws.freeze_panes = "G6"
    ws.auto_filter.ref = f"A5:P{last}"

    for offset, text in enumerate((
        f"　編成の組み合わせ: {len(decks)}通り（体数{max_units}以下・COST{max_cost}以内）",
        "　※ ランクごとの平均値で計算しています。実際は使い魔ごとに能力値が違います。",
    )):
        ws.cell(row=last + 2 + offset, column=1, value=text).font = NOTE_FONT
        ws.merge_cells(start_row=last + 2 + offset, start_column=1,
                       end_row=last + 2 + offset, end_column=16)


# ==================================================
# ⑦ AIプロンプト
# ==================================================
def build_prompt(wb: Workbook) -> None:
    ws = wb.create_sheet(SH_PROMPT)
    widths(ws, {"A": 3, "B": 110})

    title(ws, 1, "  ⑦ AIプロンプト　— 生成AIに戦闘シミュレーションを頼むための文章", 2)
    note(ws, 2, "　　下のB6セルをコピーして、そのままAIに貼り付けてください。③パラメータの値が入ります。", 2)

    ws.cell(row=4, column=2, value="■ コピーする文章（③パラメータの値が自動で入ります）").font = Font(bold=True, size=11)

    param_line = (
        f'"【パラメータ】"&CHAR(10)&'
        f'"・使い魔の数: C="&{PARAM}B{R_COUNT}&"体 B="&{PARAM}B{R_COUNT+1}&"体 '
        f'A="&{PARAM}B{R_COUNT+2}&"体 S="&{PARAM}B{R_COUNT+3}&"体"&CHAR(10)&'
        f'"・排出率(単発): C="&{PARAM}B{R_RATE}&"% B="&{PARAM}B{R_RATE+1}&"% '
        f'A="&{PARAM}B{R_RATE+2}&"% S="&{PARAM}B{R_RATE+3}&"%"&CHAR(10)&'
        f'"・レベル: Lv1〜Lv"&{PARAM}B{R_MAX_LEVEL}&"（1レベル上げるのに同じ使い魔"&{PARAM}B{R_MATERIALS}&"体）"&CHAR(10)&'
        f'"・合成1回あたりの成長量（固定値）: '
        f'C(HP+"&{PARAM}B{R_GROW}&"/ATK+"&{PARAM}C{R_GROW}&") '
        f'B(HP+"&{PARAM}B{R_GROW+1}&"/ATK+"&{PARAM}C{R_GROW+1}&") '
        f'A(HP+"&{PARAM}B{R_GROW+2}&"/ATK+"&{PARAM}C{R_GROW+2}&") '
        f'S(HP+"&{PARAM}B{R_GROW+3}&"/ATK+"&{PARAM}C{R_GROW+3}&")"&CHAR(10)&'
        f'"・ガチャ単発費用: "&TEXT({PARAM}B{R_GACHA_COST},"#,##0")&" coin"&CHAR(10)&'
        f'"・合計COST上限: "&{PARAM}B{R_DECK_COST}&"／出場"&{PARAM}B{R_DECK_UNITS}&"体"'
    )

    body = (
        '"あなたはゲームバランス設計の専門家です。"&CHAR(10)&'
        '"Discord上で動く対戦ゲーム「ラグナオンライン」のバランスを見てください。"&CHAR(10)&CHAR(10)&'
        '"【ゲームの仕組み】"&CHAR(10)&'
        '"・ギルド同士が使い魔を出し合って戦うターン制バトルです。"&CHAR(10)&'
        '"・使い魔にはC/B/A/Sのランクがあり、COSTはC=2 B=3 A=4 S=5です。"&CHAR(10)&'
        '"・編成は合計COSTの上限内で組みます。SPDの高い順に行動します。"&CHAR(10)&'
        '"・ガチャで手に入るのはLv1です。同じ使い魔を合成するとレベルが上がります。"&CHAR(10)&'
        '"・成長は次の式です: Lvn の能力値 ＝ Lv1の能力値 ＋ 固定値 ×（n − 1）"&CHAR(10)&'
        '"・SPDは成長しません。行動順は最初から最後まで変わりません。"&CHAR(10)&'
        '"・スキルの効果量はATKや最大HPに対する割合で決まるため、"&CHAR(10)&'
        '"　能力値を上げるとスキルの威力も一緒に上がります。"&CHAR(10)&CHAR(10)&'
        + param_line +
        '&CHAR(10)&CHAR(10)&'
        '"【お願いしたいこと】"&CHAR(10)&'
        '"1. この設定で、どのランクの使い魔が有利になるか教えてください。"&CHAR(10)&'
        '"2. 合計COST上限の中で、一番強い編成の組み方は何になりますか。"&CHAR(10)&'
        '"　 （高COSTを少数か、低COSTを多数か、どちらが有利になるか）"&CHAR(10)&'
        '"3. 壊れている（強すぎる・弱すぎる）ところがあれば指摘してください。"&CHAR(10)&'
        '"4. 直すとしたら、どのパラメータをいくつにするのが良いか提案してください。"&CHAR(10)&CHAR(10)&'
        '"【答え方】"&CHAR(10)&'
        '"・数字の根拠を必ず示してください。"&CHAR(10)&'
        '"・「なんとなく強い」ではなく、何ターンで倒せるかなどで比べてください。"&CHAR(10)&'
        '"・日本語で、専門用語を使わずに説明してください。"'
    )

    cell = ws.cell(row=6, column=2, value="=" + body)
    cell.alignment = Alignment(wrap_text=True, vertical="top")
    cell.fill = CALC_FILL
    cell.border = BOX
    ws.row_dimensions[6].height = 430

    for offset, text in enumerate([
        "■ 使い魔のデータも渡したいとき",
        "　②使い魔一覧のシートをコピーして、上の文章のあとに貼り付けてください。",
        "　50体ぶんの能力値とスキルが渡るので、より細かく見てもらえます。",
        "",
        "■ 結果をシートに戻すとき",
        "　AIの提案した数値を②③に入れると、④⑤⑥がその場で計算し直されます。",
        "　見比べて良さそうなら教えてください。data/master/*.json へ反映します。",
    ], start=8):
        cell = ws.cell(row=offset, column=2, value=text)
        cell.font = Font(bold=True, size=11) if text.startswith("■") else NOTE_FONT


def main() -> int:
    balance = load("balance")
    gacha = load("gacha")
    familiars = load("familiars")["familiars"]
    skills = {s["skill_id"]: s for s in load("skills")["skills"]}

    counts = {rank: len([f for f in familiars if f["rank"] == rank]) for rank in RANKS}

    wb = Workbook()
    wb.remove(wb.active)

    build_guide(wb)
    list_last_row = build_list(wb, familiars, skills, balance)
    build_params(wb, balance, gacha, counts)
    build_gacha(wb)
    build_growth(wb, list_last_row)
    build_deck(wb, balance)
    build_prompt(wb)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    wb.save(OUT_PATH)

    print(f"作成しました: {OUT_PATH.relative_to(ROOT)}")
    print(f"  シート: {' / '.join(wb.sheetnames)}")
    print(f"  使い魔 {len(familiars)}体（②の{LIST_START}〜{list_last_row}行）/ スキル {len(skills)}件")
    print(f"  表示レベル: {' / '.join(f'Lv{n}' for n in SHOW_LEVELS)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
