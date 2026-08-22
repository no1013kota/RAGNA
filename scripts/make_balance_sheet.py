"""ゲームバランスを検討するためのExcelを作る。

数式入りなので、③パラメータの数値を書き換えると、ガチャ試算・成長試算・
デッキ試算・AIプロンプトがすべて自動で計算し直されます。

    pip install openpyxl        # 初回だけ。Bot本体には不要なライブラリです
    python scripts/make_balance_sheet.py

出力先: docs/balance/バランス設計シート.xlsx

使い魔・スキル・バランス値は ``data/master/*.json`` から読み込むため、
マスターデータを更新したあとに実行し直せば、シートも最新になります。

■ レベルの扱い
ゲームで最初に手に入るのは Lv1 です（``min_level`` も ``initial_level`` も1）。
``familiars.json`` の能力値は計算用の基準値で、Lv1の能力値は
``基準値 ×（1 + 成長率 × 1）`` になります。シートは Lv1 から表示します。
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

# ==================================================
# ③パラメータの行番号。数値はすべて B列。数式がここを参照する。
# ==================================================
R_COUNT = 5  # C=5, B=6, A=7, S=8
R_COUNT_SUM = 9
R_RATE = 13  # C=13, B=14, A=15, S=16
R_RATE_SUM = 17
R_GACHA_COST = 21
R_GROW = 25  # C=25..S=28（B=固定HP C=固定ATK D=成長率HP% E=成長率ATK%）
R_MAX_LEVEL = 32
R_MATERIALS = 33
R_MODE = 34
R_SPD_VALUE = 35
R_SPD_INTERVAL = 36
R_FUSION_RATE = 40
R_SELL = 44  # C=44, B=45, A=46, S=47
R_DECK_COST = 51
R_DECK_UNITS = 52

# ⑤成長試算：ランクごとに HP/ATK/SPD の3行。Lv1がC列、Lv10がL列。
G_START = 5
G_ROWS = {rank: G_START + index * 3 for index, rank in enumerate(RANKS)}
LV1_COL, LV10_COL = "C", "L"


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


def stat_formula(base: float, rank_row: int, level_ref: str, stat: str) -> str:
    """レベル ``level_ref`` での能力値を返す数式（先頭の "=" は付けない）。

    ``base`` は ``familiars.json`` の基準値。ゲーム本体と同じ
    「基準値 ×（1 + 成長率 × レベル）」で計算します。
    """

    fixed_col = "B" if stat == "hp" else "C"
    rate_col = "D" if stat == "hp" else "E"

    return (
        f'ROUND(IF({PARAM}B{R_MODE}="固定",'
        f"{base}+{PARAM}{fixed_col}{rank_row}*{level_ref},"
        f"{base}*(1+{PARAM}{rate_col}{rank_row}/100*{level_ref})),0)"
    )


def speed_formula(base: float, level_ref: str) -> str:
    """レベル ``level_ref`` でのSPDを返す数式（上限100）。"""

    bonus = f"(INT(({level_ref}-1)/{PARAM}B{R_SPD_INTERVAL})+1)*{PARAM}B{R_SPD_VALUE}"
    return f"MIN(100,{base}+{bonus})"


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
        "　黄色 … あなたが書き換えるところ（③パラメータに集めてあります）",
        "　水色 … 自動計算。書き換えると数式が消えるので触らないでください",
        "",
        "■ シートの並び",
        "　② 使い魔一覧　… 全50体の能力値とスキル。Lv1とLv10を並べています",
        "　③ パラメータ　… ここだけ書き換えます。ほかのシートはここを見て計算します",
        "　④ ガチャ試算　… 最大レベルにするまでに必要な単発回数とcoin",
        "　⑤ 成長試算　　… ランクごとの平均能力値を Lv1〜Lv10 で並べたもの",
        "　⑥ デッキ試算　… 合計COST20以内の編成と、その合計・平均ステータス",
        "　⑦ AIプロンプト … 生成AIに戦闘シミュレーションを頼むための文章",
        "",
        "■ 今回の前提",
        "　・レベルは Lv1 が最初です。ガチャで出るのも Lv1 です",
        "　・ガチャは単発だけで計算しています（10連・保証枠は考えていません）",
        "　・プレイヤーの収入は考えていません。必要coinだけを出しています",
        "",
        "■ 成長のしかたは2通り書けます",
        "　③パラメータの「成長のしかた」を『固定』か『率』に切り替えてください。",
        "　　固定 … 合成1回ごとに決まった数だけ増える（例 HP+2）",
        "　　率　 … 割合で増える（例 5% ずつ）",
        "　どちらの数値も③に並べて書いてあるので、切り替えて見比べられます。",
        "",
        "■ 覚えておくと便利なこと",
        "　・最大レベルにするには「同じ使い魔」が（最大レベル−1）×素材数＋1体 必要です",
        "　・ATKは4〜11程度しかありません。固定で+1すると1回で10〜25%増える計算です",
        "　・SはAよりATKもSPDも低めです。Sの強みはHPとスキルとCOSTです",
        "　・スキルはATK・最大HPの割合で効くので、能力値を上げると威力も上がります",
        "　・SPDは上限100で頭打ちになります",
        "",
        "■ 決まったら",
        "　数値が決まったら教えてください。data/master/*.json へ反映して",
        "　Discordへ配信します。手で書き換える必要はありません。",
        "",
        "※ このファイルは scripts/make_balance_sheet.py で作り直せます。",
        "　 能力値やスキルは data/master/ の最新の内容を読み込んでいます。",
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
def build_list(wb: Workbook, familiars: list[dict], skills: dict) -> None:
    ws = wb.create_sheet(SH_LIST)
    widths(ws, {
        "A": 15, "B": 7, "C": 7, "D": 7,
        "E": 8, "F": 8, "G": 8,
        "H": 8, "I": 8, "J": 8,
        "K": 14, "L": 56, "M": 10,
        "N": 14, "O": 56, "P": 16,
    })

    title(ws, 1, "  ② 使い魔一覧　— 能力値とスキル", 16)
    note(ws, 2, "　　Lv1・Lv10の能力値は③パラメータの成長設定で変わります。スキルは data/master/ の内容です。", 16)

    header(ws, 3, ["", "", "", "", "Lv1", "", "", "Lv10", "", "",
                   "ACTIVEスキル", "", "", "PASSIVEスキル", "", ""])
    header(ws, 4, ["名前", "ランク", "COST", "性別",
                   "HP", "ATK", "SPD", "HP", "ATK", "SPD",
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
        row = 5 + index
        rank_row = R_GROW + RANKS.index(fam["rank"])
        max_level = f"{PARAM}B{R_MAX_LEVEL}"

        cell_flat(ws, row, 1, fam["name"])
        cell_flat(ws, row, 2, fam["rank"])
        cell_flat(ws, row, 3, fam["cost"])
        cell_flat(ws, row, 4, GENDER.get(fam.get("gender"), "—"))

        # Lv1（ゲームで最初に手に入る状態）
        cell_calc(ws, row, 5, "=" + stat_formula(fam["base_hp"], rank_row, "1", "hp"), "0")
        cell_calc(ws, row, 6, "=" + stat_formula(fam["base_atk"], rank_row, "1", "atk"), "0")
        cell_calc(ws, row, 7, "=" + speed_formula(fam["speed"], "1"), "0")

        # 最大レベル
        cell_calc(ws, row, 8, "=" + stat_formula(fam["base_hp"], rank_row, max_level, "hp"), "0")
        cell_calc(ws, row, 9, "=" + stat_formula(fam["base_atk"], rank_row, max_level, "atk"), "0")
        cell_calc(ws, row, 10, "=" + speed_formula(fam["speed"], max_level), "0")

        active = next(
            (skills[s] for s in fam.get("skills", [])
             if skills.get(s, {}).get("skill_type") == "active"), None)
        passive = next(
            (skills[s] for s in fam.get("skills", [])
             if skills.get(s, {}).get("skill_type") == "passive"), None)

        if active:
            cell_flat(ws, row, 11, active["name"])
            cell_flat(ws, row, 12, active["description"]).alignment = Alignment(wrap_text=True, vertical="top")
            uses = active.get("max_uses_per_battle")
            cell_flat(ws, row, 13, f"1バトル{uses}回" if uses else "制限なし")
        else:
            for col in (11, 12, 13):
                cell_flat(ws, row, col, "—")

        if passive:
            cell_flat(ws, row, 14, passive["name"])
            cell_flat(ws, row, 15, passive["description"]).alignment = Alignment(wrap_text=True, vertical="top")
            cell_flat(ws, row, 16, trigger_label.get(passive.get("trigger"), passive.get("trigger") or "—"))
        else:
            for col in (14, 15, 16):
                cell_flat(ws, row, col, "—")

    ws.freeze_panes = "E5"
    ws.auto_filter.ref = f"A4:P{4 + len(rows)}"


# ==================================================
# ③ パラメータ
# ==================================================
def build_params(wb: Workbook, balance: dict, gacha: dict, counts: dict) -> None:
    ws = wb.create_sheet(SH_PARAM)
    widths(ws, {"A": 26, "B": 15, "C": 15, "D": 17, "E": 17, "F": 52})

    pool = gacha["pools"][0]
    fam = balance["familiar"]
    battle = balance["battle"]

    title(ws, 1, "  ③ パラメータ　— 黄色のセルを書き換えてください", 6)
    note(ws, 2, "　　ここの数値だけが計算の入り口です。ほかのシートは全部ここを見ています。", 6)

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
    section(ws, 23, "■ 合成1回あたりの成長量（固定値・成長率の両方を書いておけます）", 6)
    header(ws, 24, ["ランク", "固定値 HP", "固定値 ATK", "成長率 HP（%）", "成長率 ATK（%）", "説明"])
    fixed_hp = {"C": 0, "B": 1, "A": 2, "S": 3}
    fixed_atk = {"C": 0, "B": 0, "A": 1, "S": 1}
    for index, rank in enumerate(RANKS):
        row = R_GROW + index
        cell_flat(ws, row, 1, f"{rank}ランク")
        cell_in(ws, row, 2, fixed_hp[rank])
        cell_in(ws, row, 3, fixed_atk[rank])
        cell_in(ws, row, 4, fam["hp_growth_rate_per_level"] * 100, "0.0")
        cell_in(ws, row, 5, fam["atk_growth_rate_per_level"] * 100, "0.0")
    ws.cell(row=R_GROW, column=6,
            value="下の「成長のしかた」で、固定値と成長率のどちらを使うか選びます").font = NOTE_FONT
    ws.cell(row=R_GROW + 1, column=6,
            value="0にすると、そのランクは合成しても伸びません").font = NOTE_FONT

    # --- 成長の共通設定 ---
    section(ws, 30, "■ 成長の共通設定", 6)
    header(ws, 31, ["項目", "値", "", "", "", "説明"])
    for row, label, value, memo in (
        (R_MAX_LEVEL, "最大レベル", fam["max_level"], "Lv1が最初です。下げると必要な体数が減ります"),
        (R_MATERIALS, "1レベルに必要な素材数", 1, "必要な体数 =（最大レベル−1）× この数 ＋ 1"),
        (R_MODE, "成長のしかた", "率", "「固定」か「率」と入力してください"),
        (R_SPD_VALUE, "SPD増加量（1回）", fam["speed_growth_value"], "SPDは上限100で頭打ちになります"),
        (R_SPD_INTERVAL, "SPDが上がる間隔（Lv）", 2, "2なら Lv1,3,5,7,9 で上がります"),
    ):
        cell_flat(ws, row, 1, label)
        cell_in(ws, row, 2, value)
        ws.cell(row=row, column=6, value=memo).font = NOTE_FONT

    # --- 合成費用 ---
    section(ws, 38, "■ 合成費用", 6)
    header(ws, 39, ["項目", "値（%）", "", "", "", "説明"])
    cell_flat(ws, R_FUSION_RATE, 1, "素材1体あたり")
    cell_in(ws, R_FUSION_RATE, 2, fam["fusion_cost_rate_per_material"] * 100, "0.0")
    ws.cell(row=R_FUSION_RATE, column=6,
            value="売却額に対する割合。50なら売却額の半分。0にすると合成は無料").font = NOTE_FONT

    # --- 売却額 ---
    section(ws, 42, "■ 売却額（Lv1）", 6)
    header(ws, 43, ["ランク", "売却額（coin）", "", "", "", "説明"])
    for index, rank in enumerate(RANKS):
        row = R_SELL + index
        cell_flat(ws, row, 1, f"{rank}ランク")
        cell_in(ws, row, 2, fam["sell_base_prices"][rank], "#,##0")
    ws.cell(row=R_SELL, column=6, value="合成費用もこの金額を基準に決まります").font = NOTE_FONT

    # --- バトルの枠 ---
    section(ws, 49, "■ バトルの枠（⑥デッキ試算が使います）", 6)
    header(ws, 50, ["項目", "値", "", "", "", "説明"])
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
def build_growth(wb: Workbook, familiars: list[dict]) -> None:
    ws = wb.create_sheet(SH_GROWTH)
    widths(ws, {"A": 10, "B": 8, **{chr(ord("C") + i): 8 for i in range(10)}, "M": 15})

    title(ws, 1, "  ⑤ 成長試算　— ランクごとの平均能力値を Lv1〜Lv10 で並べたもの", 13)
    note(ws, 2, "　　そのランクの平均です。Lv1が最初の状態で、③パラメータの成長設定で全部動きます。", 13)

    header(ws, 4, ["ランク", "項目"] + [f"Lv{level}" for level in range(1, 11)]
                  + ["Lv1→Lv10（倍）"])

    for index, rank in enumerate(RANKS):
        fams = [f for f in familiars if f["rank"] == rank]
        avg = {
            "HP": round(sum(f["base_hp"] for f in fams) / len(fams), 2),
            "ATK": round(sum(f["base_atk"] for f in fams) / len(fams), 2),
            "SPD": round(sum(f["speed"] for f in fams) / len(fams), 2),
        }
        rank_row = R_GROW + index
        base_row = G_ROWS[rank]

        for offset, label in enumerate(("HP", "ATK", "SPD")):
            row = base_row + offset
            cell_flat(ws, row, 1, f"{rank}ランク" if offset == 0 else "")
            cell_flat(ws, row, 2, label)

            for level in range(1, 11):
                col = 2 + level  # Lv1 が C列(3)
                if label == "SPD":
                    body = speed_formula(avg["SPD"], str(level))
                else:
                    body = stat_formula(avg[label], rank_row, str(level), label.lower())
                cell_calc(
                    ws, row, col,
                    f'=IF({level}>{PARAM}B{R_MAX_LEVEL},"—",{body})',
                    "0" if label == "SPD" else "0.0",
                )
            cell_calc(
                ws, row, 13,
                f'=IF({LV10_COL}{row}="—","—",{LV10_COL}{row}/{LV1_COL}{row})',
                "0.00",
            )

    section(ws, 18, "■ 見るときのポイント", 13)
    points = [
        "　・Lv1がゲームで最初に手に入る状態です。そこから合成でLv10まで上げます。",
        "　・ATKは4〜11程度しかありません。固定で+1すると1回で10〜25%増える計算になります。",
        "　・SはAよりATKもSPDも低めです。SはHPとスキルとCOSTで差がついています。",
        "　・SPDは上限100で頭打ちです。もともと速い使い魔ほど伸びしろが小さくなります。",
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
        "G": 13, "H": 13, "I": 14, "J": 13, "K": 13, "L": 14, "M": 20,
    })

    cost = {"C": 2, "B": 3, "A": 4, "S": 5}
    max_cost = balance["battle"]["max_total_cost"]
    max_units = balance["battle"]["max_units"]

    title(ws, 1, f"  ⑥ デッキ試算　— 合計COST{max_cost}以内の編成と、その合計・平均ステータス", 13)
    note(ws, 2, "　　ランクの組み合わせごとに、⑤成長試算の平均能力値を足し合わせています。", 13)
    note(ws, 3, "　　HPとATKは編成ぶんの合計、SPDは1体あたりの平均です。", 13)

    header(ws, 5, ["S", "A", "B", "C", "体数", "COST",
                   "Lv1 HP合計", "Lv1 ATK合計", "Lv1 SPD平均",
                   "Lv10 HP合計", "Lv10 ATK合計", "Lv10 SPD平均", "備考"])

    decks = []
    for size in range(1, max_units + 1):
        for combo in combinations_with_replacement("SABC", size):
            total = sum(cost[rank] for rank in combo)
            if total <= max_cost:
                decks.append(({rank: combo.count(rank) for rank in "SABC"}, size, total))

    decks.sort(key=lambda d: (-d[1], -d[2], -d[0]["S"], -d[0]["A"]))

    hp_rows = {rank: G_ROWS[rank] for rank in RANKS}
    atk_rows = {rank: G_ROWS[rank] + 1 for rank in RANKS}
    spd_rows = {rank: G_ROWS[rank] + 2 for rank in RANKS}

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

        guard = f'{GROWTH}{LV10_COL}{hp_rows["S"]}="—"'
        cell_calc(ws, row, 7, "=" + total_of(hp_rows, LV1_COL, row), "0")
        cell_calc(ws, row, 8, "=" + total_of(atk_rows, LV1_COL, row), "0")
        cell_calc(ws, row, 9, f"=({total_of(spd_rows, LV1_COL, row)})/E{row}", "0")
        cell_calc(ws, row, 10, f'=IF({guard},"—",{total_of(hp_rows, LV10_COL, row)})', "0")
        cell_calc(ws, row, 11, f'=IF({guard},"—",{total_of(atk_rows, LV10_COL, row)})', "0")
        cell_calc(ws, row, 12, f'=IF({guard},"—",({total_of(spd_rows, LV10_COL, row)})/E{row})', "0")

        if counts["S"] == size:
            memo = "S単一"
        elif counts["C"] == size:
            memo = "C単一（最安）"
        elif total == max_cost:
            memo = "COST上限ぴったり"
        else:
            memo = ""
        cell_flat(ws, row, 13, memo)

    last = 5 + len(decks)
    ws.freeze_panes = "A6"
    ws.auto_filter.ref = f"A5:M{last}"

    for offset, text in enumerate((
        f"　編成の組み合わせ: {len(decks)}通り（体数{max_units}以下・COST{max_cost}以内）",
        "　※ ランクごとの平均値で計算しています。実際は使い魔ごとに能力値が違います。",
    )):
        ws.cell(row=last + 2 + offset, column=1, value=text).font = NOTE_FONT
        ws.merge_cells(start_row=last + 2 + offset, start_column=1,
                       end_row=last + 2 + offset, end_column=13)


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
        f'"・成長のしかた: "&{PARAM}B{R_MODE}&CHAR(10)&'
        f'"・固定値の場合: C(HP+"&{PARAM}B{R_GROW}&"/ATK+"&{PARAM}C{R_GROW}&") '
        f'B(HP+"&{PARAM}B{R_GROW+1}&"/ATK+"&{PARAM}C{R_GROW+1}&") '
        f'A(HP+"&{PARAM}B{R_GROW+2}&"/ATK+"&{PARAM}C{R_GROW+2}&") '
        f'S(HP+"&{PARAM}B{R_GROW+3}&"/ATK+"&{PARAM}C{R_GROW+3}&")"&CHAR(10)&'
        f'"・成長率の場合: C(HP"&{PARAM}D{R_GROW}&"%/ATK"&{PARAM}E{R_GROW}&"%) '
        f'B(HP"&{PARAM}D{R_GROW+1}&"%/ATK"&{PARAM}E{R_GROW+1}&"%) '
        f'A(HP"&{PARAM}D{R_GROW+2}&"%/ATK"&{PARAM}E{R_GROW+2}&"%) '
        f'S(HP"&{PARAM}D{R_GROW+3}&"%/ATK"&{PARAM}E{R_GROW+3}&"%)"&CHAR(10)&'
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
        '"・ガチャで手に入るのはLv1です。同じ使い魔を合成するとレベルが上がり、"&CHAR(10)&'
        '"　HPとATKが伸びます。SPDは決まったレベルで少しずつ上がり、上限は100です。"&CHAR(10)&'
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
        "　AIの提案した数値を③パラメータに入れると、④⑤⑥がその場で計算し直されます。",
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
    build_list(wb, familiars, skills)
    build_params(wb, balance, gacha, counts)
    build_gacha(wb)
    build_growth(wb, familiars)
    build_deck(wb, balance)
    build_prompt(wb)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    wb.save(OUT_PATH)

    print(f"作成しました: {OUT_PATH.relative_to(ROOT)}")
    print(f"  シート: {' / '.join(wb.sheetnames)}")
    print(f"  使い魔 {len(familiars)}体 / スキル {len(skills)}件")
    return 0


if __name__ == "__main__":
    sys.exit(main())
