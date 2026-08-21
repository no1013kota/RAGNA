"""バトル中にDiscordへ表示される文言と記号。

編集のしかたは ``texts/__init__.py`` を読んでください。
``{name}`` のような波かっこは、Botが値を入れ替える場所です。

==================================================
バトル中の画面は、次のEmbedが順番に流れていきます
==================================================
1. 編成表     … バトル開始時に1回だけ。両ギルドの出場者を並べます。
2. ラウンド見出し … 「── ラウンド 1 ──」と、そのラウンドの行動順。
3. 行動ログ   … 攻撃・スキル・パッシブ・毒など、起きたことを1件ずつ。
                 戦闘不能になった使い魔は、そのあとに専用のEmbedが続きます。
4. 戦況       … 「【戦況】」。両ギルドの生存数とHPバーの一覧。
                 ラウンドの区切りごとに1件残ります。
5. 相手のターンの通知 … 相手ギルドが行動している間の待ち表示。
6. 自分の行動順の通知 … 行動できるようになった人へ出す、操作用の情報。
7. 結果       … 勝敗の表題と決着理由。

「行動ログ」は、表題（``LOG_TITLE_*``）＋本文の行（``LOG_*``）＋
末尾の「【項目】結果」（``ITEM_LINE``）の3つでできています。
"""

# ==================================================
# 味方・相手の目印と凡例
# ==================================================
# バトル専用チャンネルはギルドごとに分かれているため、同じ出来事でも
# 見ているギルドに合わせて「自分＝🔵／相手＝🔴」で示します。
MARK_ALLY = "🔵"
MARK_ENEMY = "🔴"

# どちらのギルドから見た画面か分からないときの目印（A側・B側）
MARK_GUILD_A = "🟦"
MARK_GUILD_B = "🟥"

# 戦況Embedのfooter（一番下の小さな文字）に出る凡例
SIDE_LEGEND = "🔵自ギルド 🔴相手ギルド"


# ==================================================
# HPバーの記号
# ==================================================
# HPバーの長さ（文字数）。数字なので「"」で囲みません。
HP_BAR_LENGTH = 10

# 残っているHPの部分
HP_BAR_FILLED = "█"

# 減ったHPの部分
HP_BAR_EMPTY = "░"

# HPバーと数値の1行。{bar} はHPバー、{hp} は現在HP、{max_hp} は最大HP。
# 「`」で囲むとDiscordで等幅になり、バーの長さがそろって見えます。
HP_BAR_LINE = "`{bar}` {hp}/{max_hp}"


# ==================================================
# どのEmbedでも使う部品
# ==================================================
# Embedの本文へ並べる「【項目】結果」の1行。
# {label} が項目名、{value} がその中身。
ITEM_LINE = "【{label}】{value}"

# 値が無い・分からないときに置く記号。
# 行動順・対象・決着理由などが空のときに出ます。
EMPTY = "—"

# 使い魔のデータが見つからなかったときの呼び方
UNKNOWN_UNIT = "不明"

# 使い魔の表示名。{name} は使い魔名、{level} はレベル。
UNIT_LABEL = "{name} Lv.{level}"

# 生き残っている数。{alive} は生存数、{total} は出場した総数。
SURVIVOR = "{alive}/{total}体"

# 能力値の増減つき表記。{value} が現在値、{delta} が基礎値からの増減。
# 「{delta:+d}」と書くと「+2」「-2」のように必ず符号が付きます。
STAT_WITH_DELTA = "{value}（{delta:+d}）"

# 使い魔1体の能力をまとめた1行
STAT_LINE = "HP {hp}/{max_hp}／ATK {atk}／SPD {speed}"

# 倒れている使い魔の能力行
STAT_LINE_DEFEATED = "HP 0/{max_hp}（戦闘不能）"

# 残り時間。{minutes} は分、{seconds} は秒。
# 「{seconds:02d}」と書くと「05」のように必ず2桁になります。
REMAINING_TIME = "{minutes}分{seconds:02d}秒"

# 持ち主の名前が分からないときの呼び方（Discordのメンションになります）
PLAYER_MENTION = "<@{player_id}>"

# かかっている効果に付ける印。{text} には効果の呼び名が入ります。
MARK_STATUS = "☠{text}"
MARK_OTHER = "◆{text}"

# 効果の印を横に並べるときの区切り
MARK_SEPARATOR = " "

# スキルEmbedの「【使い魔名（持ち主）】」の右側で、能力と効果をつなぐ区切り
STATUS_LINE_SEPARATOR = " ／ "

# スキルの種類。行動順の通知と編成表の両方に出ます。
SKILL_KIND_ACTIVE = "ACTIVE"
SKILL_KIND_PASSIVE = "PASSIVE"


# ==================================================
# 行動順の並び（ラウンド見出しの「【行動順】」）
# ==================================================
# 1体分。{mark} は🔵か🔴、{name} は使い魔名。
TURN_QUEUE_ENTRY = "{mark}{name}"

# いま行動している使い魔だけ、この形で強調します。
TURN_QUEUE_CURRENT = "▶**{label}**"

# 使い魔と使い魔のあいだの区切り
TURN_QUEUE_SEPARATOR = " → "

# 表示しきれない分があるときに末尾へ付ける記号
TURN_QUEUE_MORE = "…"

# このラウンドの行動が全部終わっているとき
TURN_QUEUE_FINISHED = "このラウンドの行動は終わりです"

# このラウンドで何番目に動くか。{position} は何番目、{total} は行動する体数。
TURN_POSITION = "{position}/{total}番目"


# ==================================================
# 行動ログの表題（Embedの一番上に太字で出ます）
# ==================================================
# ラウンドの区切り。{round} はラウンド数。
LOG_TITLE_ROUND = "── ラウンド {round} ──"

# 通常攻撃。{name} は攻撃した使い魔名。
LOG_TITLE_ATTACK = "⚔ {name}の攻撃"

# アクティブスキル。{skill} はスキル名。
LOG_TITLE_SKILL = "✦ SKILL「{skill}」"

# パッシブスキルが1件だけ発動したとき。{skill} はスキル名。
LOG_TITLE_PASSIVE = "✦ PASSIVE「{skill}」"

# パッシブが複数まとまったとき（バトル開始時など）。{count} は件数。
LOG_TITLE_PASSIVE_MULTI = "✦ PASSIVE SKILL（{count}件）"

# 状態異常などで行動できなかったとき。{name} は使い魔名。
LOG_TITLE_SKIP = "⏭ {name}は行動できない"

# 持ち時間内に操作されず、自動攻撃になったとき
LOG_TITLE_TIMEOUT = "⏱ 時間切れ"

# 毒のダメージだけを出すEmbed
LOG_TITLE_POISON = "☠ 毒"

# どの行動にも属さない変化をまとめるEmbed
LOG_TITLE_CHANGE = "戦況の変化"


# ==================================================
# 行動ログの中身（1行ずつ）
# ==================================================
# ラウンド見出しEmbedに付く項目名
LOG_ROUND_ORDER_LABEL = "行動順"

# スキルEmbedの項目名。{name} は使い魔名、{owner} は持ち主の名前。
LOG_SKILL_OWNER_LABEL = "{name}（{owner}）"

# スキルの対象になった使い魔の項目名。{name} は使い魔名。
LOG_SKILL_TARGET_LABEL = "{name}（対象）"

# パッシブが発動したことを示す行。{name} は使い魔名、{skill} はスキル名。
LOG_PASSIVE_LINE = "◇ **PASSIVE** {name}「{skill}」"

# パッシブの効果説明。「-# 」で始めるとDiscordで小さな文字になります。
LOG_SKILL_DESCRIPTION = "-# {description}"

# 行動できなかった理由。{status} は「麻痺」などの状態異常名。
LOG_SKIP_BODY = "{status}のため行動をスキップしました。"

# 理由の状態異常が分からなかったときの呼び方
LOG_SKIP_DEFAULT_STATUS = "行動不能"

# 時間切れEmbedの本文
LOG_TIMEOUT_BODY = "自動攻撃を実行しました。"

# --- ダメージ ---
# ダメージを打ち消したとき。{target} は狙われた使い魔名。
LOG_DAMAGE_NULLIFIED = "🛡 {target} へのダメージを無効化"

# 会心の一撃。ダメージ行の1行上に出ます。
LOG_CRITICAL = "⚡ **CRITICAL**"

# ダメージの本体。{damage} はダメージ量、{target} は使い魔名、
# {hp_change} には下の LOG_HP_CHANGE が入ります。
LOG_DAMAGE = "💥 **{damage}** ダメージ → {target}（{hp_change}）"

# HPの増減。{before} は変化前、{after} は変化後。
LOG_HP_CHANGE = "HP {before} → **{after}**"

# 変化前後のHPが記録されていなかったときに置く記号
LOG_HP_UNKNOWN = "?"

# --- 回復 ---
# {target} は回復した使い魔名、{amount} は回復量。
LOG_HEAL = "💚 {target} のHPを **{amount}** 回復"

# --- 状態異常 ---
# 状態異常を防いだとき。{target} は使い魔名、{status} は状態異常名。
LOG_STATUS_NULLIFIED = "{target}：{status}は無効化された"

# 状態異常が付いたとき。{turns} は残りターン数。
LOG_STATUS_APPLIED = "{target}：{status}（残り{turns}ターン）"

# --- その他の効果 ---
# バフ・デバフなどが付いたとき。{text} は効果の呼び名。
LOG_EFFECT = "{target}：{text}"

# ATK・SPDが実際に動いたときだけ、その下に並ぶ行。
# 行頭は全角スペースで字下げしています。
LOG_STAT_CHANGE = "　{label} {before} → **{after}**"
LOG_STAT_LABEL_ATK = "ATK"
LOG_STAT_LABEL_SPEED = "SPD"

# --- 戦闘不能まわり ---
# 行動ログの中の1行。{name} は使い魔名。
LOG_DEFEAT = "💀 {name} は戦闘不能"

# 倒れるはずが耐えたとき。{text} には耐え方の説明が入ります。
LOG_BEFORE_DEFEAT = "🛡 {name} は{text}"
LOG_BEFORE_DEFEAT_DEFAULT = "耐えた"

# 復活したとき。{text} には復活の仕方の説明が入ります。
LOG_REVIVE = "✨ {name} が{text}"
LOG_REVIVE_DEFAULT = "復活"

# 毒のダメージを受けたとき
LOG_POISON = "☠ {name} は毒を受けている"


# ==================================================
# 戦闘不能のEmbed（行動ログのあとに1体ずつ続きます）
# ==================================================
# 表題。{name} は使い魔名。
DEFEAT_TITLE = "💀 {name} 戦闘不能"

# 本文の1行目。{mark} は🔵か🔴、{name} は「ロキ Lv.3」の形。
DEFEAT_LINE = "{mark}**{name}** は倒れた"

# そのギルドの生き残りを示す項目名
DEFEAT_REMAINING_LABEL = "残り"


# ==================================================
# 編成表（バトル開始時に1回だけ出ます）
# ==================================================
LINEUP_TITLE = "⚔ バトル開始"

# 出場枠の番号。11体目以降は下の LINEUP_SLOT_NUMBER を使います。
SLOT_MARKS = ("①", "②", "③", "④", "⑤", "⑥", "⑦", "⑧", "⑨", "⑩")
LINEUP_SLOT_NUMBER = "{number}."

# どちらのギルドかの見出し
LINEUP_SIDE_ALLY = "🔵自ギルド"
LINEUP_SIDE_ENEMY = "🔴相手ギルド"

# ギルド名の行。{side} は上の見出し、{name} はギルド名。
LINEUP_GUILD_HEADING = "**{side}：{name}**"

# ギルド名の下に並ぶ2つの項目
LINEUP_TOTAL_COST_LABEL = "合計COST"
LINEUP_ENTRY_LABEL = "出場"

# 合計COST。上限があるときは「12/20」、上限なしのときは「12」。
LINEUP_TOTAL_COST = "{total}/{cap}"
LINEUP_TOTAL_COST_NO_CAP = "{total}"

# 出場した体数
LINEUP_ENTRY_COUNT = "{count}体"

# 上の2項目を横に並べる行。行頭と項目のあいだは全角スペースです。
LINEUP_SUMMARY_LINE = "　{cost}　{entry}"

# 使い魔1体の見出し。{mark} は枠番号、{name} は「ロキ Lv.3」、
# {cost} はCOST、{owner} は持ち主の名前。
LINEUP_UNIT = "{mark} **{name}**　COST {cost}　{owner}"

# その下に続く能力の行
LINEUP_UNIT_STATS = "　`{bar}` {hp}/{max_hp}　ATK {atk}　SPD {speed}"

# 持っているスキル。{kind} は ACTIVE か PASSIVE、{name} はスキル名。
LINEUP_SKILL = "　{kind}「{name}」"

# スキルの効果説明（自ギルドの使い魔にだけ出ます）
LINEUP_SKILL_DESCRIPTION = "　-# {description}"

# スキルを1つも持っていない使い魔
LINEUP_NO_SKILL = "　スキルなし"

# 出場する使い魔が1体もいないギルド
LINEUP_NO_UNITS = "出場する使い魔がいません。"

# 編成表の一番下に付く注意書き
LINEUP_NOTE = "-# 相手ギルドはスキル名だけを表示しています。"

# 長すぎてDiscordに収まらないとき、末尾へ付ける断り書き
LINEUP_TRUNCATED = "\n-# 表示を省略しました。"


# ==================================================
# 戦況（ラウンドの区切りごとに出る一覧）
# ==================================================
STATUS_TITLE = "【戦況】"

# ギルドの見出し。{prefix} は下の STATUS_CURRENT_PREFIX、
# {mark} は🔵か🔴、{name} はギルド名。
STATUS_GUILD_HEADING = "{prefix}{mark}{name}"

# いま行動しているギルドの頭に付く印
STATUS_CURRENT_PREFIX = "▶ "

# ギルドの見出しの右側。{survivors} は生存数、{remaining} は残り持ち時間。
STATUS_GUILD_SUMMARY = "{survivors}　残り持ち時間 {remaining}"

# 生きている使い魔の1行目。{name} は使い魔名、{level} はレベル。
STATUS_UNIT_ALIVE = "**{name}** Lv.{level}"

# その下に続くHPバーと能力の行
STATUS_UNIT_STATS = "`{bar}` {hp}/{max_hp}　ATK {atk}　SPD {speed}"

# かかっている効果の行（あるときだけ出ます）
STATUS_UNIT_MARKS = "　{marks}"

# 倒れている使い魔。「~~」で囲むとDiscordで取り消し線になります。
STATUS_UNIT_DEFEATED = "~~{name}~~ 💀 戦闘不能\n`{bar}` 0/{max_hp}"

# 長すぎてDiscordに収まらないとき、末尾へ付ける記号
STATUS_TRUNCATED = "\n…"

# 一番下の小さな文字。{legend} には SIDE_LEGEND が入ります。
STATUS_FOOTER = "{legend}　☠状態異常 ◆その他"


# ==================================================
# 相手のターンの通知
# ==================================================
OPPONENT_TURN_TITLE = "⏳ 相手のターンです"

# 本文に並ぶ「【項目】結果」の項目名
OPPONENT_TURN_UNIT_LABEL = "行動する使い魔"
OPPONENT_TURN_STATS_LABEL = "ステータス"
OPPONENT_TURN_EFFECTS_LABEL = "かかっている効果"

# 行動する使い魔の名前（太字にしています）
OPPONENT_TURN_UNIT_VALUE = "**{name}**"


# ==================================================
# 自分の行動順の通知
# ==================================================
# 表題。{name} は「ロキ Lv.3」の形。
TURN_TITLE = "▶ {name} の行動順です"

# 本文に並ぶ「【項目】結果」の項目名
TURN_ATK_LABEL = "現在ATK"
TURN_SPEED_LABEL = "現在SPD"
TURN_EFFECTS_LABEL = "かかっている効果"
TURN_ORDER_LABEL = "行動順"
TURN_SURVIVORS_LABEL = "生存"
TURN_AUTO_ATTACK_LABEL = "自動攻撃まで"
TURN_TIME_LEFT_LABEL = "残り持ち時間"

# 効果が1つもかかっていないとき
TURN_EFFECTS_NONE = "なし"

# 生存数。{ally} は自ギルド、{enemy} は相手ギルド。
TURN_SURVIVORS = "味方 {ally}／敵 {enemy}"

# 一番下の小さな文字。{coin} はベット額、{win_xp}・{lose_xp} は増減するXP。
# 「{coin:,}」と書くと「1,000」のように3桁ごとにカンマが入ります。
TURN_FOOTER = (
    "ベット ギルドごと {coin:,} coin（出場者で均等に分担）／"
    "勝利 {win_xp} XP・敗北 {lose_xp} XP"
)

# --- 使えるスキルの一覧 ---
# スキルの一覧の見出し
SKILL_HEADING = "**スキル**"

# スキルを1つも持っていない使い魔
SKILL_NONE_LABEL = "スキル"
SKILL_NONE = "なし"

# 自分で使うスキル。{name} はスキル名、{uses} は下の残り回数。
SKILL_ACTIVE = "ACTIVE「{name}」（{uses}）"

# 自動で発動するスキル
SKILL_PASSIVE = "PASSIVE「{name}」"

# スキルの効果説明
SKILL_DESCRIPTION = "-# {description}"

# アクティブスキルの残り使用回数
SKILL_USES_UNLIMITED = "回数制限なし"
SKILL_USES_EMPTY = "使用済"
SKILL_USES_LEFT = "あと{count}回"


# ==================================================
# 結果（バトルが終わったときのEmbed）
# ==================================================
# 表題
RESULT_TITLE_WIN = "🏆 GUILD BATTLE 終了"
RESULT_TITLE_DRAW = "🤝 GUILD BATTLE 終了"
RESULT_TITLE_ABORTED = "⛔ GUILD BATTLE 中止"
RESULT_TITLE_UNKNOWN = "GUILD BATTLE 終了"

# 本文の1行目。{name} は勝ったギルド名。
RESULT_WIN = "**{name}** の勝利"
RESULT_DRAW = "引き分け"
RESULT_ABORTED = "運営により中止されました。勝敗は記録されません。"

# 本文に並ぶ「【項目】結果」の項目名
RESULT_MATCH_LABEL = "対戦"
RESULT_REASON_LABEL = "決着理由"
RESULT_ROUND_LABEL = "ラウンド数"
RESULT_BET_LABEL = "ベット額"
RESULT_REWARD_LABEL = "清算"

# 対戦したギルド
RESULT_MATCH = "{guild_a} vs {guild_b}"

# ベット額。「{coin:,}」で3桁ごとにカンマが入ります。
RESULT_BET = "ギルドごと {coin:,} coin"

# 決着した理由。
# 左側（"wipe" など）はプログラムが使う名前なので変えないでください。
# 右側の日本語だけを書き換えてください。
RESULT_REASONS = {
    "wipe": "相手ギルドの全滅",
    "double_wipe": "同時全滅",
    "time_over": "持ち時間切れ",
    "surrender": "降参",
    "engine_stalled": "進行不能のため引き分け",
}


# ==================================================
# 効果の呼び名（バフ・デバフ・状態異常）
# ==================================================
# 戦況Embedの「◆…」と、行動ログの効果行に出る呼び名です。
# 状態異常そのものの名前（麻痺・毒など）は game/models.py にあります。
EFFECT_ATK_MODIFIER = "ATK{amount:+d}"
EFFECT_SPEED_MODIFIER = "SPD{amount:+d}"
EFFECT_STATUS_IMMUNE = "状態異常無効"
EFFECT_ACTIVE_LOCK = "ACTIVE使用禁止"
EFFECT_DAMAGE_REDUCTION = "被ダメージ-{amount}"
EFFECT_HEAL_BLOCK = "回復阻害"
EFFECT_POISON_AMPLIFY = "猛毒増幅"
EFFECT_SURVIVE = "戦闘不能耐性"
EFFECT_ATK_SWAP = "ATK交換中"

# 攻撃対象を1体に縛る効果。戦況の一覧と行動ログで呼び方が違います。
EFFECT_TAUNT = "攻撃対象固定"
EFFECT_TAUNT_LOG = "攻撃対象を固定"

# 効果があと何ターン続くか（戦況の一覧に出る書き方）
EFFECT_DURATION_TURNS = "残{count}"
EFFECT_DURATION_ATTACKS = "次{count}回"
EFFECT_DURATION_ROUND_END = "ラウンド終了まで"
EFFECT_DURATION_PERMANENT = "常時"

# 呼び名のうしろへ残り時間を付ける形。{text} が呼び名、{duration} が上の書き方。
EFFECT_WITH_DURATION = "{text}（{duration}）"

# 効果があと何ターン続くか（行動ログに出る書き方）
EFFECT_LOG_DURATION_TURNS = "{text}（残{count}ターン）"
EFFECT_LOG_DURATION_NEXT_ATTACK = "{text}（次の攻撃のみ）"


# ==================================================
# スキル発動時のログ（game/skill_engine.py が使う）
# ==================================================
# 状態異常を防がれたとき。{status} は「毒」などの状態異常名。
LOG_STATUS_NULLIFIED = "{status}は無効化された"

# 状態異常を付けたとき。{status} は状態異常名、{turns} は残りターン数。
LOG_STATUS_DETAIL = "{status}（残{turns}ターン）"

# 毒を付けたとき。{status} は「毒」、{damage} は1ターンあたりのダメージ。
LOG_STATUS_POISON_DETAIL = "{status} {damage}ダメージ×{turns}ターン"

# 上の行の後ろに足す、猛毒増幅が乗っているときの印
LOG_STATUS_AMPLIFIED = "{text}／猛毒増幅"

# 状態異常を消したとき。{names} は消した状態異常名を「・」でつないだもの。
LOG_CLEANSE_STATUS = "{names}を解除"

# デバフを消したとき。{count} は消した数。
LOG_CLEANSE_DEBUFF = "デバフ{count}件を解除"

# ATKを入れ替えたとき。{first} と {second} は入れ替え前のATK。
LOG_ATK_SWAP = "現在ATKを交換（{first} ⇄ {second}）"

# 条件なしでずっと効いているパッシブが、バトル開始時に登録されたとき
LOG_ALWAYS_ACTIVE = "常時発動"


# ==================================================
# 行動できなかったときに出る案内
# ==================================================
# バトル中に操作を受け付けられなかったとき、そのままエフェメラルで表示されます。
# 対象の選び方が足りないとき。{skill} はスキル名、{count} は選ぶ数。
ERROR_SELECT_COUNT = "「{skill}」の対象を{count}体選択してください。"

ERROR_DUPLICATE_TARGET = "同じ対象を重複して選択できません。"
ERROR_TARGET_UNAVAILABLE = "選択した対象は現在指定できません。"
ERROR_SKILL_UNAVAILABLE = "このスキルは現在使用できません。"
ERROR_SKILL_NOT_OWNED = "この使い魔は指定のスキルを持っていません。"
ERROR_SKILL_NOT_FOUND = "スキル定義が見つかりません。"
ERROR_SKILL_ALREADY_USED = "このターンは既にスキルを使用しています。"
ERROR_TARGET_NOT_ATTACKABLE = "その対象は現在攻撃できません。"
ERROR_NOT_YOUR_TURN = "現在の行動順ではありません。"
ERROR_NOT_ACCEPTING = "現在は行動を受け付けていません。"
ERROR_BATTLE_NOT_RUNNING = "このバトルは進行中ではありません。"
ERROR_BATTLE_ALREADY_STARTED = "このバトルは既に開始しています。"
ERROR_UNKNOWN_ACTION = "未対応の行動です。"
ERROR_STATS_FAILED = "使い魔の能力値を計算できませんでした。"

# 使い魔のデータが見つからないとき。{familiar_id} は使い魔の内部ID。
ERROR_FAMILIAR_NOT_FOUND = "使い魔マスターが見つかりません: {familiar_id}"
