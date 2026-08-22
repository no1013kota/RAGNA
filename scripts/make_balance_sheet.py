"""ゲームバランスを検討するためのExcelを作る。

数式入りなので、パラメータシートの数値を書き換えると、必要な課金額や
到達までの月数、能力値、スキル威力が自動で計算し直されます。

    pip install openpyxl        # 初回だけ。Bot本体には不要なライブラリです
    python scripts/make_balance_sheet.py

出力先: docs/balance/バランス設計シート.xlsx

使い魔・スキル・バランス値は ``data/master/*.json`` から読み込むため、
マスターデータを更新したあとに実行し直せば、シートも最新になります。
"""

from __future__ import annotations

import json
import sys

from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter


ROOT = Path(__file__).resolve().parents[1]
MASTER = ROOT / "data" / "master"
OUT_DIR = ROOT / "docs" / "balance"
OUT_PATH = OUT_DIR / "バランス設計シート.xlsx"

# 色。書き換えてよいセルだけ黄色にして、触る場所を一目で分かるようにする。
INPUT_FILL = PatternFill("solid", fgColor="FFF3C4")
CALC_FILL = PatternFill("solid", fgColor="EAF3FF")
HEAD_FILL = PatternFill("solid", fgColor="D9D9D9")
TITLE_FILL = PatternFill("solid", fgColor="2B2D31")
WARN_FILL = PatternFill("solid", fgColor="FFD6D6")

TITLE_FONT = Font(bold=True, size=14, color="FFFFFF")
HEAD_FONT = Font(bold=True, size=10)
NOTE_FONT = Font(size=9, color="666666")

THIN = Side(style="thin", color="BBBBBB")
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

RANKS = ("C", "B", "A", "S")


def load(name: str) -> dict:
    return json.loads((MASTER / f"{name}.json").read_text(encoding="utf-8"))


def title(ws, row: int, text: str, width: int = 8) -> None:
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=width)
    cell = ws.cell(row=row, column=1, value=text)
    cell.fill = TITLE_FILL
    cell.font = TITLE_FONT
    cell.alignment = Alignment(vertical="center")
    ws.row_dimensions[row].height = 22


def section(ws, row: int, text: str, width: int = 8) -> None:
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=width)
    cell = ws.cell(row=row, column=1, value=text)
    cell.fill = HEAD_FILL
    cell.font = HEAD_FONT


def header(ws, row: int, labels: list[str]) -> None:
    for index, label in enumerate(labels, start=1):
        cell = ws.cell(row=row, column=index, value=label)
        cell.fill = HEAD_FILL
        cell.font = HEAD_FONT
        cell.border = BOX
        cell.alignment = Alignment(horizontal="center")


def note(ws, row: int, text: str, width: int = 8) -> None:
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=width)
    cell = ws.cell(row=row, column=1, value=text)
    cell.font = NOTE_FONT


def put_input(ws, row: int, col: int, value, fmt: str | None = None):
    cell = ws.cell(row=row, column=col, value=value)
    cell.fill = INPUT_FILL
    cell.border = BOX
    if fmt:
        cell.number_format = fmt
    return cell


def put_calc(ws, row: int, col: int, value, fmt: str | None = None):
    cell = ws.cell(row=row, column=col, value=value)
    cell.fill = CALC_FILL
    cell.border = BOX
    if fmt:
        cell.number_format = fmt
    return cell


def put_label(ws, row: int, col: int, value):
    cell = ws.cell(row=row, column=col, value=value)
    cell.border = BOX
    return cell


def widths(ws, spec: dict[str, int]) -> None:
    for column, width in spec.items():
        ws.column_dimensions[column].width = width


# ==================================================
# 1. 使い方
# ==================================================
def build_guide(wb: Workbook) -> None:
    ws = wb.create_sheet("① 使い方")
    widths(ws, {"A": 3, "B": 100})

    title(ws, 1, "  ラグナオンライン バランス設計シート", width=2)

    lines = [
        "",
        "■ このシートでできること",
        "　「ガチャの排出率」「使い魔の数」「成長のしかた」を変えると、",
        "　・特定の使い魔を最大レベルにするのに何coin・何か月かかるか",
        "　・各使い魔の最終的なHP／ATKがいくつになるか",
        "　・スキルの威力がどう変わるか",
        "　が自動で計算し直されます。",
        "",
        "■ 触ってよいのは黄色のセルだけです",
        "　黄色 … あなたが書き換えるところ",
        "　水色 … 自動計算。書き換えないでください（数式が消えます）",
        "",
        "■ シートの見かた",
        "　② パラメータ　… ここの数値を書き換えます。すべての計算の元になります",
        "　③ ガチャ試算　… 最大レベルまでに必要な回数・coin・月数が出ます（一番大事）",
        "　④ 成長試算　　… ランクごとの平均能力値が、今と案でどう変わるか",
        "　⑤ 使い魔一覧　… 全50体の能力値と、最終的な能力値",
        "　⑥ スキル影響　… ATKやHPを変えると、スキルの威力がどう動くか",
        "",
        "■ 今わかっている一番の問題",
        "　現在の設定では、同じ使い魔を10体そろえないと最大レベルになりません。",
        "　Cランクでも約133か月、Sランクでは約1421か月ぶんの収入が必要です。",
        "　つまり合成は事実上ほとんど使えない機能になっています。",
        "　③ガチャ試算の「必要な月数」を見ながら、現実的な数字を探してみてください。",
        "",
        "■ 効きやすいパラメータ（迷ったらここから）",
        "　1. 最大レベル　　　　　　… 下げると必要な体数が一気に減ります",
        "　2. 1レベルに必要な素材数 … 同上",
        "　3. 各ランクの使い魔の数　… 減らすと同じ使い魔が出やすくなります",
        "　4. 排出率　　　　　　　　… 上げると出やすくなります（合計1000を保つこと）",
        "　5. ガチャの費用　　　　　… 月の収入と見比べてください",
        "",
        "■ 決まったら",
        "　数値が決まったら教えてください。data/master/*.json へ反映して",
        "　Discordへ配信します。手で書き換える必要はありません。",
        "",
        "※ このファイルは scripts/make_balance_sheet.py で作り直せます。",
        "　 使い魔の能力値やスキルは data/master/ の最新の内容を読み込んでいます。",
    ]
    for index, text in enumerate(lines, start=2):
        cell = ws.cell(row=index, column=2, value=text)
        if text.startswith("■"):
            cell.font = Font(bold=True, size=11)
        elif text.startswith("※"):
            cell.font = NOTE_FONT


# ==================================================
# 2. パラメータ
# ==================================================
# 行番号を定数にしておく。他シートの数式がここを参照する。
P = {
    "single_cost": 5,
    "multi_cost": 6,
    "multi_count": 7,
    "guaranteed_slot": 8,
    "rate_normal": 12,  # C=12, B=13, A=14, S=15
    "rate_normal_sum": 16,
    "rate_guaranteed": 20,  # A=20, S=21
    "rate_guaranteed_sum": 22,
    "count": 26,  # C=26, B=27, A=28, S=29
    "count_sum": 30,
    "max_level": 34,
    "materials_per_level": 35,
    "growth_mode": 36,
    "growth_hp": 40,  # C=40, B=41, A=42, S=43
    "growth_atk": 40,  # 同じ行のD列
    "income": 47,
    "start_coin": 48,
    "sell_price": 52,  # C=52, B=53, A=54, S=55
    "fusion_rate": 56,
}


def build_params(wb: Workbook, balance: dict, gacha: dict, counts: dict) -> None:
    ws = wb.create_sheet("② パラメータ")
    widths(ws, {"A": 30, "B": 14, "C": 14, "D": 14, "E": 52})

    pool = gacha["pools"][0]
    fam = balance["familiar"]

    title(ws, 1, "  ② パラメータ　— 黄色のセルを書き換えてください", width=5)
    note(ws, 2, "　　「現在」列は今Discordで動いている値です（参考用）。書き換えるのは「試す値」列です。", width=5)

    # --- ガチャの費用 ---
    section(ws, 3, "■ ガチャの費用", width=5)
    for row, (label, value, memo) in enumerate(
        [
            ("単発の費用（coin）", pool["single_cost"], "1回引くのにかかるcoin"),
            ("10連の費用（coin）", pool["multi_cost"], "まとめて引くときの合計coin"),
            ("10連の回数", pool["multi_count"], "1回の10連で何体もらえるか"),
            ("保証枠の位置", pool["guaranteed_slot"], "何体目が保証枠か（10なら最後の1体）"),
        ],
        start=P["single_cost"],
    ):
        put_label(ws, row, 1, label)
        put_calc(ws, row, 2, value, "#,##0")
        put_input(ws, row, 3, value, "#,##0")
        ws.cell(row=row, column=5, value=memo).font = NOTE_FONT
    ws.cell(row=4, column=2, value="現在").font = HEAD_FONT
    ws.cell(row=4, column=3, value="試す値").font = HEAD_FONT

    # --- 通常枠の排出率 ---
    section(ws, 10, "■ 排出率（通常枠・千分率。合計を必ず1000にする）", width=5)
    ws.cell(row=11, column=2, value="現在").font = HEAD_FONT
    ws.cell(row=11, column=3, value="試す値").font = HEAD_FONT
    ws.cell(row=11, column=4, value="％").font = HEAD_FONT
    for index, rank in enumerate(RANKS):
        row = P["rate_normal"] + index
        put_label(ws, row, 1, f"{rank}ランク")
        put_calc(ws, row, 2, pool["rates"]["normal"][rank])
        put_input(ws, row, 3, pool["rates"]["normal"][rank])
        put_calc(ws, row, 4, f"=C{row}/10", '0.0"%"')
    put_label(ws, P["rate_normal_sum"], 1, "合計")
    put_calc(ws, P["rate_normal_sum"], 2, "=SUM(B12:B15)")
    put_calc(ws, P["rate_normal_sum"], 3, "=SUM(C12:C15)")
    ws.cell(
        row=P["rate_normal_sum"],
        column=5,
        value='=IF(C16=1000,"OK","★合計が1000ではありません")',
    ).font = Font(bold=True, color="CC0000")

    # --- 保証枠の排出率 ---
    section(ws, 18, "■ 排出率（10連の保証枠・千分率。合計1000）", width=5)
    ws.cell(row=19, column=2, value="現在").font = HEAD_FONT
    ws.cell(row=19, column=3, value="試す値").font = HEAD_FONT
    for index, rank in enumerate(("A", "S")):
        row = P["rate_guaranteed"] + index
        put_label(ws, row, 1, f"{rank}ランク")
        put_calc(ws, row, 2, pool["rates"]["guaranteed"].get(rank, 0))
        put_input(ws, row, 3, pool["rates"]["guaranteed"].get(rank, 0))
        put_calc(ws, row, 4, f"=C{row}/10", '0.0"%"')
    put_label(ws, P["rate_guaranteed_sum"], 1, "合計")
    put_calc(ws, P["rate_guaranteed_sum"], 2, "=SUM(B20:B21)")
    put_calc(ws, P["rate_guaranteed_sum"], 3, "=SUM(C20:C21)")
    ws.cell(
        row=P["rate_guaranteed_sum"],
        column=5,
        value='=IF(C22=1000,"OK","★合計が1000ではありません")',
    ).font = Font(bold=True, color="CC0000")

    # --- 使い魔の数 ---
    section(ws, 24, "■ 各ランクの使い魔の数", width=5)
    ws.cell(row=25, column=2, value="現在").font = HEAD_FONT
    ws.cell(row=25, column=3, value="試す値").font = HEAD_FONT
    for index, rank in enumerate(RANKS):
        row = P["count"] + index
        put_label(ws, row, 1, f"{rank}ランク（体）")
        put_calc(ws, row, 2, counts[rank])
        put_input(ws, row, 3, counts[rank])
        ws.cell(
            row=row,
            column=5,
            value="数を減らすと、同じ使い魔が出やすくなります（＝合成しやすい）",
        ).font = NOTE_FONT
    put_label(ws, P["count_sum"], 1, "合計")
    put_calc(ws, P["count_sum"], 2, "=SUM(B26:B29)")
    put_calc(ws, P["count_sum"], 3, "=SUM(C26:C29)")

    # --- 成長 ---
    section(ws, 32, "■ 成長のしかた", width=5)
    ws.cell(row=33, column=2, value="現在").font = HEAD_FONT
    ws.cell(row=33, column=3, value="試す値").font = HEAD_FONT
    put_label(ws, P["max_level"], 1, "最大レベル")
    put_calc(ws, P["max_level"], 2, fam["max_level"])
    put_input(ws, P["max_level"], 3, fam["max_level"])
    ws.cell(
        row=P["max_level"], column=5, value="下げると必要な体数が一気に減ります"
    ).font = NOTE_FONT

    put_label(ws, P["materials_per_level"], 1, "1レベル上げる素材数")
    put_calc(ws, P["materials_per_level"], 2, 1)
    put_input(ws, P["materials_per_level"], 3, 1)
    ws.cell(
        row=P["materials_per_level"],
        column=5,
        value="必要な体数 =（最大レベル−1）× この数 ＋ 1",
    ).font = NOTE_FONT

    put_label(ws, P["growth_mode"], 1, "成長のしかた")
    put_calc(ws, P["growth_mode"], 2, "率")
    put_input(ws, P["growth_mode"], 3, "率")
    ws.cell(
        row=P["growth_mode"],
        column=5,
        value='「率」…素の値に対する％で伸びる ／ 「固定」…毎回きまった数だけ増える',
    ).font = NOTE_FONT

    section(ws, 38, "■ ランクごとの成長量（上の「成長のしかた」に合わせて解釈されます）", width=5)
    header(ws, 39, ["ランク", "HP", "ATK", "", "説明"])
    for index, rank in enumerate(RANKS):
        row = P["growth_hp"] + index
        put_label(ws, row, 1, f"{rank}ランク")
        put_input(ws, row, 2, fam["hp_growth_rate_per_level"])
        put_input(ws, row, 3, fam["atk_growth_rate_per_level"])
        ws.cell(
            row=row,
            column=5,
            value="「率」なら 0.05＝5% ／ 「固定」なら 2＝毎回+2。0にすると合成不可",
        ).font = NOTE_FONT

    # --- 経済 ---
    section(ws, 45, "■ プレイヤーの収入", width=5)
    ws.cell(row=46, column=2, value="現在").font = HEAD_FONT
    ws.cell(row=46, column=3, value="試す値").font = HEAD_FONT
    put_label(ws, P["income"], 1, "月の収入（coin）")
    put_calc(ws, P["income"], 2, 50000, "#,##0")
    put_input(ws, P["income"], 3, 50000, "#,##0")
    ws.cell(
        row=P["income"], column=5, value="本メンバー（騎士）の月次報酬。config.py の値"
    ).font = NOTE_FONT
    put_label(ws, P["start_coin"], 1, "初期coin")
    put_calc(ws, P["start_coin"], 2, 30000, "#,##0")
    put_input(ws, P["start_coin"], 3, 30000, "#,##0")

    # --- 売却・合成費用 ---
    section(ws, 50, "■ 売却額と合成費用", width=5)
    ws.cell(row=51, column=2, value="現在").font = HEAD_FONT
    ws.cell(row=51, column=3, value="試す値").font = HEAD_FONT
    for index, rank in enumerate(RANKS):
        row = P["sell_price"] + index
        put_label(ws, row, 1, f"{rank}の売却額（Lv1）")
        put_calc(ws, row, 2, fam["sell_base_prices"][rank], "#,##0")
        put_input(ws, row, 3, fam["sell_base_prices"][rank], "#,##0")
    put_label(ws, P["fusion_rate"], 1, "合成費用（売却額比）")
    put_calc(ws, P["fusion_rate"], 2, fam["fusion_cost_rate_per_material"])
    put_input(ws, P["fusion_rate"], 3, fam["fusion_cost_rate_per_material"])
    ws.cell(
        row=P["fusion_rate"], column=5, value="素材1体につきかかる費用。0にすると合成は無料"
    ).font = NOTE_FONT

    ws.freeze_panes = "A5"


# ==================================================
# 3. ガチャ試算
# ==================================================
def build_gacha_calc(wb: Workbook) -> None:
    ws = wb.create_sheet("③ ガチャ試算")
    widths(ws, {"A": 22, "B": 15, "C": 15, "D": 15, "E": 15, "F": 18, "G": 40})

    title(ws, 1, "  ③ ガチャ試算　— ②パラメータを変えると自動で変わります", width=7)
    note(ws, 2, "　　一番見てほしいのは「必要な月数」です。ここが現実的かどうかで決めてください。", width=7)

    P2 = "'② パラメータ'!"

    section(ws, 4, "■ 必要な体数", width=7)
    put_label(ws, 5, 1, "最大レベルに必要な体数")
    put_calc(ws, 5, 2, f"=({P2}C34-1)*{P2}C35+1", "0")
    ws.cell(row=5, column=7, value="（最大レベル−1）× 1レベルの素材数 ＋ 本体1体").font = NOTE_FONT

    header(ws, 7, ["ランク", "使い魔の数", "特定1体の確率(単発)", "10連での期待数", "必要な10連回数", "必要coin", "本メンバーの月収入で"])

    # ②パラメータの行: 排出率 normal C12/B13/A14/S15、guaranteed A20/S21、数 C26..S29
    rate_rows = {"C": 12, "B": 13, "A": 14, "S": 15}
    guar_rows = {"A": 20, "S": 21}
    count_rows = {"C": 26, "B": 27, "A": 28, "S": 29}

    for index, rank in enumerate(RANKS):
        row = 8 + index
        rr, cr = rate_rows[rank], count_rows[rank]
        put_label(ws, row, 1, f"{rank}ランク")
        put_calc(ws, row, 2, f"={P2}C{cr}")

        # 単発で特定の1体を引く確率
        put_calc(ws, row, 3, f"=IF({P2}C{cr}=0,0,{P2}C{rr}/1000/{P2}C{cr})", "0.000%")

        # 10連での期待数 = (10連回数-1)×通常確率 + 保証枠の確率
        if rank in guar_rows:
            gr = guar_rows[rank]
            expected = (
                f"=IF({P2}C{cr}=0,0,"
                f"({P2}C7-1)*{P2}C{rr}/1000/{P2}C{cr}"
                f"+{P2}C{gr}/1000/{P2}C{cr})"
            )
        else:
            expected = f"=IF({P2}C{cr}=0,0,({P2}C7-1)*{P2}C{rr}/1000/{P2}C{cr})"
        put_calc(ws, row, 4, expected, "0.0000")

        put_calc(ws, row, 5, f"=IF(D{row}=0,\"—\",$B$5/D{row})", "#,##0")
        put_calc(ws, row, 6, f"=IF(D{row}=0,\"—\",E{row}*{P2}C6)", "#,##0")
        put_calc(
            ws,
            row,
            7,
            f'=IF(D{row}=0,"—",TEXT(F{row}/{P2}C47,"#,##0")&"か月")',
        )

    section(ws, 13, "■ 目安", width=7)
    guide = [
        "　1〜3か月　… すぐ手が届く。Cランク向き",
        "　3〜12か月　… 目標として良い。B〜Aランク向き",
        "　1〜3年　　… 長期目標。Sランク向き",
        "　3年以上　　… 事実上とどきません。設定を見直してください",
    ]
    for offset, text in enumerate(guide):
        ws.cell(row=14 + offset, column=1, value=text).font = NOTE_FONT
        ws.merge_cells(start_row=14 + offset, start_column=1, end_row=14 + offset, end_column=7)

    section(ws, 19, "■ 参考：1か月に引ける回数", width=7)
    put_label(ws, 20, 1, "単発で")
    put_calc(ws, 20, 2, f"={P2}C47/{P2}C5", "0.0")
    ws.cell(row=20, column=7, value="月の収入 ÷ 単発の費用").font = NOTE_FONT
    put_label(ws, 21, 1, "10連で")
    put_calc(ws, 21, 2, f"={P2}C47/{P2}C6", "0.00")
    ws.cell(row=21, column=7, value="月の収入 ÷ 10連の費用").font = NOTE_FONT

    section(ws, 23, "■ 参考：1年で手に入る使い魔の数", width=7)
    put_label(ws, 24, 1, "10連を回し続けた場合")
    put_calc(ws, 24, 2, f"=({P2}C47*12/{P2}C6)*{P2}C7", "#,##0")
    ws.cell(row=24, column=7, value="年収入 ÷ 10連費用 × 1回の体数").font = NOTE_FONT

    ws.freeze_panes = "A8"


# ==================================================
# 4. 成長試算
# ==================================================
def build_growth_calc(wb: Workbook, familiars: list[dict]) -> None:
    ws = wb.create_sheet("④ 成長試算")
    widths(ws, {"A": 14, "B": 12, "C": 12, "D": 14, "E": 14, "F": 12, "G": 12, "H": 34})

    title(ws, 1, "  ④ 成長試算　— ランクごとの平均能力値", width=8)
    note(ws, 2, "　　②パラメータの「成長のしかた」と「ランクごとの成長量」を変えると動きます。", width=8)

    P2 = "'② パラメータ'!"
    growth_rows = {"C": 40, "B": 41, "A": 42, "S": 43}

    header(ws, 4, ["ランク", "素のHP", "素のATK", "最終HP", "最終ATK", "HP倍率", "ATK倍率", "備考"])

    for index, rank in enumerate(RANKS):
        row = 5 + index
        fams = [f for f in familiars if f["rank"] == rank]
        avg_hp = sum(f["base_hp"] for f in fams) / len(fams)
        avg_atk = sum(f["base_atk"] for f in fams) / len(fams)
        gr = growth_rows[rank]

        put_label(ws, row, 1, f"{rank}ランク")
        put_calc(ws, row, 2, round(avg_hp, 1), "0.0")
        put_calc(ws, row, 3, round(avg_atk, 1), "0.0")

        # 率なら base*(1+r*(maxLv-1))、固定なら base + v*(maxLv-1)
        levels = f"({P2}C34-1)"
        put_calc(
            ws, row, 4,
            f'=IF({P2}C36="率",B{row}*(1+{P2}B{gr}*{levels}),B{row}+{P2}B{gr}*{levels})',
            "0.0",
        )
        put_calc(
            ws, row, 5,
            f'=IF({P2}C36="率",C{row}*(1+{P2}C{gr}*{levels}),C{row}+{P2}C{gr}*{levels})',
            "0.0",
        )
        put_calc(ws, row, 6, f"=IF(B{row}=0,0,D{row}/B{row})", '0.00"倍"')
        put_calc(ws, row, 7, f"=IF(C{row}=0,0,E{row}/C{row})", '0.00"倍"')

    section(ws, 10, "■ 見るときのポイント", width=8)
    points = [
        "　・ATKの素の値は4〜11しかありません。「固定」で+1すると1回で10〜25%増える計算になります。",
        "　・SはAよりATKもSPDも低めです（S=8.4 / A=8.6）。Sの強みはHPとスキルとCOSTです。",
        "　　「ランクが上＝ATKも上」にすると、素のデータには無い序列を後から作ることになります。",
        "　・スキルはATK・最大HPの割合で効くため、能力値を上げるとスキル威力も一緒に上がります（⑥参照）。",
    ]
    for offset, text in enumerate(points):
        ws.cell(row=11 + offset, column=1, value=text).font = NOTE_FONT
        ws.merge_cells(start_row=11 + offset, start_column=1, end_row=11 + offset, end_column=8)

    ws.freeze_panes = "A5"


# ==================================================
# 5. 使い魔一覧
# ==================================================
def build_familiars(wb: Workbook, familiars: list[dict]) -> None:
    ws = wb.create_sheet("⑤ 使い魔一覧")
    widths(ws, {"A": 16, "B": 6, "C": 8, "D": 8, "E": 8, "F": 7, "G": 8, "H": 10, "I": 10, "J": 12})

    title(ws, 1, "  ⑤ 使い魔一覧　— 素の能力値と、最大レベルでの能力値", width=10)
    note(ws, 2, "　　素の能力値は data/master/familiars.json の値です。ここを変えても反映されません。", width=10)

    header(ws, 4, ["名前", "ランク", "素のHP", "素のATK", "SPD", "COST", "性別", "最終HP", "最終ATK", "売却額(Lv1)"])

    P2 = "'② パラメータ'!"
    growth_rows = {"C": 40, "B": 41, "A": 42, "S": 43}
    sell_rows = {"C": 52, "B": 53, "A": 54, "S": 55}
    gender = {"male": "男性", "female": "女性", "none": "なし", None: "—"}

    order = {"S": 0, "A": 1, "B": 2, "C": 3}
    rows = sorted(familiars, key=lambda f: (order[f["rank"]], -f["base_atk"]))

    for index, f in enumerate(rows):
        row = 5 + index
        gr = growth_rows[f["rank"]]
        levels = f"({P2}C34-1)"
        put_label(ws, row, 1, f["name"])
        put_label(ws, row, 2, f["rank"])
        put_label(ws, row, 3, f["base_hp"])
        put_label(ws, row, 4, f["base_atk"])
        put_label(ws, row, 5, f["speed"])
        put_label(ws, row, 6, f["cost"])
        put_label(ws, row, 7, gender.get(f.get("gender"), "—"))
        put_calc(
            ws, row, 8,
            f'=ROUND(IF({P2}C36="率",C{row}*(1+{P2}B{gr}*{levels}),C{row}+{P2}B{gr}*{levels}),0)',
            "0",
        )
        put_calc(
            ws, row, 9,
            f'=ROUND(IF({P2}C36="率",D{row}*(1+{P2}C{gr}*{levels}),D{row}+{P2}C{gr}*{levels}),0)',
            "0",
        )
        put_calc(ws, row, 10, f"={P2}C{sell_rows[f['rank']]}", "#,##0")

    ws.freeze_panes = "A5"
    ws.auto_filter.ref = f"A4:J{4 + len(rows)}"


# ==================================================
# 6. スキル影響
# ==================================================
def build_skills(wb: Workbook, skills: list[dict], familiars: list[dict]) -> None:
    ws = wb.create_sheet("⑥ スキル影響")
    widths(ws, {"A": 18, "B": 16, "C": 6, "D": 22, "E": 10, "F": 14, "G": 12, "H": 12, "I": 12})

    title(ws, 1, "  ⑥ スキル影響　— 能力値を変えるとスキル威力がどう動くか", width=9)
    note(ws, 2, "　　スキルはATK・最大HPの割合で効きます。能力値を上げると威力も一緒に上がります。", width=9)
    note(
        ws,
        3,
        "　　※「受ける側の〜」が基準のスキルは、相手の能力値で決まります。"
        "ここでは目安として、使う側と同じ能力の相手に使った場合の数値を出しています。",
        width=9,
    )

    owner = {}
    for f in familiars:
        for sid in f.get("skills", []):
            owner[sid] = f

    basis_label = {
        "actor_atk": "使う側のATK",
        "actor_max_hp": "使う側の最大HP",
        "target_atk": "受ける側のATK",
        "target_max_hp": "受ける側の最大HP",
        "speed_cap": "SPDの上限(100)",
    }

    header(ws, 5, ["スキル名", "使い魔", "ランク", "効果", "割合", "基準", "素の値で", "最終値で", "変化"])

    P2 = "'② パラメータ'!"
    growth_rows = {"C": 40, "B": 41, "A": 42, "S": 43}

    row = 6
    for skill in skills:
        f = owner.get(skill["skill_id"])
        if f is None:
            continue
        for effect in skill.get("effects", []):
            percent = effect.get("percent")
            basis = effect.get("percent_of")
            if percent is None:
                percent = effect.get("params", {}).get("damage_percent")
                basis = effect.get("params", {}).get("damage_percent_of")
            if percent is None:
                continue

            gr = growth_rows[f["rank"]]
            levels = f"({P2}C34-1)"
            uses_hp = basis in ("actor_max_hp", "target_max_hp")
            base_value = f["base_hp"] if uses_hp else f["base_atk"]
            grow_col = "B" if uses_hp else "C"

            put_label(ws, row, 1, skill["name"])
            put_label(ws, row, 2, f["name"])
            put_label(ws, row, 3, f["rank"])
            put_label(ws, row, 4, effect["effect_type"])
            put_label(ws, row, 5, f"{percent}%")
            put_label(ws, row, 6, basis_label.get(basis, basis or "—"))

            if basis == "speed_cap":
                put_calc(ws, row, 7, abs(int(percent)), "0")
                put_calc(ws, row, 8, abs(int(percent)), "0")
                put_calc(ws, row, 9, "—")
            else:
                put_calc(ws, row, 7, f"=MAX(1,ROUND({base_value}*{abs(int(percent))}/100,0))", "0")
                put_calc(
                    ws, row, 8,
                    f'=MAX(1,ROUND(IF({P2}C36="率",{base_value}*(1+{P2}{grow_col}{gr}*{levels}),'
                    f'{base_value}+{P2}{grow_col}{gr}*{levels})*{abs(int(percent))}/100,0))',
                    "0",
                )
                put_calc(ws, row, 9, f"=IF(G{row}=0,0,H{row}/G{row})", '0.00"倍"')
            row += 1

    ws.freeze_panes = "A6"
    ws.auto_filter.ref = f"A5:I{row - 1}"


def main() -> int:
    balance = load("balance")
    gacha = load("gacha")
    familiars = load("familiars")["familiars"]
    skills = load("skills")["skills"]

    counts = {rank: len([f for f in familiars if f["rank"] == rank]) for rank in RANKS}

    wb = Workbook()
    wb.remove(wb.active)

    build_guide(wb)
    build_params(wb, balance, gacha, counts)
    build_gacha_calc(wb)
    build_growth_calc(wb, familiars)
    build_familiars(wb, familiars)
    build_skills(wb, skills, familiars)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    wb.save(OUT_PATH)

    print(f"作成しました: {OUT_PATH.relative_to(ROOT)}")
    print(f"  シート: {', '.join(wb.sheetnames)}")
    print(f"  使い魔 {len(familiars)}体 / スキル {len(skills)}件を読み込みました")
    return 0


if __name__ == "__main__":
    sys.exit(main())
