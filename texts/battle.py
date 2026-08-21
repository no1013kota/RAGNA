"""ギルドバトルの文言（パネル・出場登録・申請・募集・バトル中の操作・清算）。

編集のしかたは ``texts/__init__.py`` を読んでください。
``{name}`` のような波かっこは、Botが値を入れ替える場所です。

「権限がありません。」のような、どの機能でも使う文は ``texts/common.py`` に
あります。使い魔やスキルの名前・説明は ``data/master/*.json`` にあります。
"""

# ==================================================
# 共通（いろいろな画面で使い回す部品）
# ==================================================
# 値が無いときに出す記号
DASH = "—"

# ページ送りのボタン
PAGE_PREVIOUS = "◀ 前へ"
PAGE_NEXT = "次へ ▶"

# 確認画面のボタン
CONFIRM_BUTTON = "実行する"
CANCEL_BUTTON = "やめる"

# 確認画面で「やめる」を押したとき
CANCELLED_OPERATION = "操作を取り消しました。"

# 他人あての一時画面を、本人以外が操作しようとしたとき
OWNER_ONLY = "この操作は本人だけが使用できます。"

# 使い魔を選ぶセレクトの見出しに付ける、その塊のランク範囲。
# {rank} は1種類だけのとき、{first}〜{last} は幅があるとき。
SELECT_RANK_ONE = "（{rank}ランク）"
SELECT_RANK_RANGE = "（{first}〜{last}ランク）"

# 使い魔の呼び方。{name} は使い魔名、{level} はレベル。
FAMILIAR_LABEL = "{name} Lv.{level}"
FAMILIAR_LABEL_BOLD = "**{name} Lv.{level}**"

# 使い魔名が読めなかったときの呼び方
FAMILIAR_FALLBACK = "使い魔"

# セレクトの選択肢の見出し。{prefix} は「3体目：」などの前置き、{rank} はランク。
OPTION_LABEL = "{prefix}{rank} {name} Lv.{level}"

# 同じ使い魔をまとめたときに付ける体数。{label} は上の見出し。
OPTION_LABEL_COUNT = "{label} ×{count}"

# セレクトの選択肢の説明欄（所有している使い魔）
OPTION_STATS = "HP {hp}／ATK {atk}／SPD {speed}／COST {cost}"

# バトル中の使い魔の説明欄。{hp}/{max_hp} は現在HPと最大HP。
UNIT_STATS = "HP {hp}/{max_hp}／ATK {atk}／SPD {speed}"

# 上の説明欄へ、かかっている効果を付け足すとき
UNIT_STATS_WITH_EFFECTS = "{stats}／{marks}"

# 使い魔の能力を1行にまとめた表示
FAMILIAR_STATS = "HP {hp}／ATK {atk}／SPD {speed}"


# ==================================================
# 見出し（Embedの「【項目】結果」の左側）
# ==================================================
LABEL_STATE = "状態"
LABEL_MEMBERS = "出場者"
LABEL_FAMILIARS = "使い魔"
LABEL_ADDED = "追加"
LABEL_REMOVED = "除外"
LABEL_CURRENT_TOTAL = "現在の合計"
LABEL_GUILD_TOTAL = "ギルド合計"
LABEL_YOUR_SLOT = "あなたの枠"
LABEL_TOTAL_COST = "合計COST"
LABEL_REGISTERED = "登録済み"
LABEL_RATE = "レート"
LABEL_BET = "ベット額"
LABEL_MEMBER_RANGE = "出場人数"
LABEL_REQUEST_FROM = "申請元"
LABEL_REQUEST_TO = "申請先"
LABEL_GUILD_TIME = "ギルドの持ち時間"
LABEL_TURN_TIME = "1操作の制限時間"
LABEL_XP = "XP"
LABEL_YOUR_ATK = "あなたの攻撃力"
LABEL_EFFECTS = "かかっている効果"
LABEL_MOVED_COIN = "移動したcoin"
LABEL_YOUR_GUILD = "あなたのギルド"


# ==================================================
# ギルドバトルパネル（ギルドマスター専用TC）
# ==================================================
# パネルの説明文。{max_units} はギルドが出せる使い魔の上限体数。
# 1行目は「見えない文字」（ゼロ幅スペース）で1行あけています。消さないでください。
BATTLE_PANEL_BODY = """​
**最大{max_units}体どうしのギルドバトル**
-# バトル申請またはバトル募集を行い、それに他のギルドが応募することでバトル開始します。
-# まずはギルドマスターがメンバーセットで出場者と「1人あたりの使い魔の体数」を決めます
-# メンバーは「#使い魔バトル」で使いたい使い魔を優先度順にセットします。
-# バトルに出場する使い魔は、ギルドマスターが決めた1人あたりの使い魔の体数の数、優先度順に選択されます。
"""

# パネルのボタン
BUTTON_SET_MEMBERS = "メンバーセット"
BUTTON_CHECK_ROSTER = "セット確認"
BUTTON_REQUEST = "バトル申請"
BUTTON_RECRUIT = "バトル募集"
BUTTON_SURRENDER = "降参"


# ==================================================
# 使い魔セットパネル（「使い魔バトル」チャンネル）
# ==================================================
# パネルの説明文。{max_units} はギルドが出せる使い魔の上限体数。
ROSTER_PANEL_BODY = """​
**事前登録**
-# あなたがバトルで使う使い魔を、優先度順に登録します。（{max_units}体まで）
-# ギルドマスターがバトル出場者を選択したとき、この優先度で使い魔が選択されます。

**出場する使い魔**
-# ギルドマスターがギルドマスター専用tcからバトル出場者を選択すると
-# 選ばれた人が、実際に使う使い魔を確認できます。
-# あなたが使う使い魔の数もギルドマスターが決めています。

**2つは常に同じ内容になります**
-# 事前登録を変えると、出場する使い魔もその優先度へ入れ替わります。
-# 出場する使い魔を変えると、事前登録の順番もそれに合わせます。
-# ただしバトルが成立して編成ロック中の間は、出場する使い魔は変わりません。
"""

# パネルのボタン
BUTTON_REGISTER = "事前登録"
BUTTON_SET_FAMILIAR = "出場する使い魔"


# ==================================================
# メンバーセット（ギルドマスターが出場者を決める）
# ==================================================
# 「メンバーセット」を押した直後の案内
ROSTER_SELECT_PROMPT = "バトルへ出場するメンバーを選んでください。"

# 出場者を選ぶセレクト。{max_members} は出場できる人数の上限。
ROSTER_SELECT_PLACEHOLDER = "出場者を選択（最大{max_members}人）"

# メンバー一覧で、ギルドマスターに付ける説明
OPTION_GUILD_MASTER = "ギルドマスター"

# ギルドに誰もいなかったとき
NO_GUILD_MEMBERS = "所属メンバーがいません。"

# 出場者を選んだあと、1人あたりの体数を決める画面の案内。
# {limit} は1人あたりの上限、{max_units} はギルド合計の上限。
ROSTER_COUNT_PROMPT = """出場者ごとに使い魔の体数を決めて「確定」を押してください。
-# 1人あたり最大{limit}体、ギルド合計{max_units}体までです。
-# 体数の分だけ、本人の事前登録から自動でセットします。"""

# 体数を選び直したときに出し直す1行目（上の案内の1行目と同じ文にしています）
ROSTER_COUNT_HEADING = "出場者ごとに使い魔の体数を決めて「確定」を押してください。"

# 体数セレクトの見出しと選択肢。{name} は表示名、{count} は体数。
ROSTER_COUNT_LABEL = "{name}：{count}体"

# 体数を選び直したときに出す合計。{total} は現在の合計、{max_units} は上限。
ROSTER_COUNT_TOTAL = "{total}/{max_units}体"

# 体数を決めたあとの確定ボタン
ROSTER_COUNT_CONFIRM = "確定"

# ---- 出場者セットが終わったあとの報告 ----
# 【出場者】の右側。{count} は人数。
ROSTER_MEMBER_COUNT = "{count}人"

# 【使い魔】の右側。{count} は合計体数、{max_units} は上限。
ROSTER_FAMILIAR_COUNT = "{count}体（最大{max_units}体）"

# 出場者ごとの割り当て。{user_id} は本人、{count} は体数。
ROSTER_ASSIGN_LINE = "<@{user_id}>：{count}体"

# 事前登録から自動でセットしたとき。{count} は体数。
ROSTER_ADOPTED_NOTE = "-# 事前登録の順番から{count}体を自動でセットしました。"

# 割り当てが減って、セットを外した使い魔があるとき。{count} は体数。
ROSTER_RELEASED_NOTE = "-# 割り当ての変更にともない、{count}体の使い魔セットを解除しました。"

# 出場者が自分で差し替えられることの案内。{channel} はチャンネル名。
ROSTER_SWAP_HINT = "-# 出場者は「#{channel}」で、自分の使い魔を差し替えられます。"


# ==================================================
# セット確認（ギルドマスター専用TCの編成確認Embed）
# ==================================================
ROSTER_EMBED_TITLE = "【ギルドバトル編成】"

# 編成を変えられるかどうか
ROSTER_LOCKED = "🔒 編成ロック中（バトルが成立したため、終了まで変更できません）"
ROSTER_UNLOCKED = "変更できます"

# ロック中だけ出す補足
ROSTER_LOCKED_NOTE = (
    "-# ロック中は出場者・使い魔の変更と、セット中の使い魔の合成・売却、"
    "脱退・追放・解散ができません。事前登録だけはいつでも変更できます。"
)

# 【出場者】【使い魔】の右側。{count} は現在数、{max_members}／{max_units} は上限。
ROSTER_MEMBER_SUMMARY = "{count}人（最大{max_members}人）"
ROSTER_FAMILIAR_SUMMARY = "{count}体（最大{max_units}体）"

# 出場者1人分の表示。{mark} は行頭の記号、{count}/{assigned} はセット済み／割り当て、
# {familiars} は使い魔の一覧、{ready} は下の準備状況。
ROSTER_EMBED_MEMBER = """{mark} <@{user_id}>（{count}/{assigned}体）
使い魔：{familiars}
準備：{ready}"""

# 準備状況
ROSTER_READY_OK = "✅"
ROSTER_READY_SHORT = "⚠ 割り当てに足りません"
ROSTER_READY_NONE = "❌"

# 使い魔をセットしていない出場者
ROSTER_UNSET = "未設定"

# セットした使い魔を手放してしまっていたとき
ROSTER_NOT_OWNED = "所有していません"

# 出場者がまだ決まっていないとき
ROSTER_EMPTY = "出場者が選択されていません。"


# ==================================================
# 事前登録（使い魔バトルチャンネルの「事前登録」）
# ==================================================
# 現在の登録内容。{count} は登録数、{max_units} は上限。
REGISTER_COUNT = "{count}/{max_units}体"

# 1体も登録していないとき
REGISTER_EMPTY = "-# まだ登録していません。"

# 登録した1体の見出しと中身。{priority} は優先順、{detail} は能力。
REGISTER_PRIORITY_LABEL = "{priority}番目"
REGISTER_LINE = "{name} Lv.{level}　{detail}"

# 画面のいちばん下に出す案内
REGISTER_NOTE_ORDER = "-# 登録した順番のまま、メンバーセット時に自動でセットされます。"
REGISTER_NOTE_SYNC = (
    "-# 出場者に選ばれている間は、ここを変えると"
    "「出場する使い魔」も同じ順番へ入れ替わります。"
)
REGISTER_NOTE_ANYTIME = "-# 出場者でなくても、バトル中でもいつでも変更できます。"

# 事前登録を変えたとき、出場する使い魔へも反映したことの報告。
# {adopted} はセットした体数、{released} は解除した体数。
REGISTER_SYNC_NOTE = (
    "-# 出場する使い魔も、この優先順へ入れ替えました"
    "（セット {adopted}体／解除 {released}体）。"
)

# 合計COST上限のせいでセットできなかった使い魔があるとき。
# {limit} は合計COSTの上限、{count} は体数、{names} は使い魔名。
REGISTER_COST_SKIPPED = (
    "⚠ ギルドの合計COST上限（{limit}）のため、"
    "{count}体をセットできませんでした：{names}"
)
REGISTER_COST_SKIPPED_HINT = (
    "-# COSTの低い使い魔を上位へ登録し直すか、ほかの出場者と調整してください。"
)

# ---- 事前登録のボタン ----
REGISTER_BUTTON_ADD = "登録を追加"
REGISTER_BUTTON_REPLACE = "入れ替え"
REGISTER_BUTTON_REMOVE_ONE = "個別に取消"
REGISTER_BUTTON_UNDO = "最後を取消"
REGISTER_BUTTON_CLEAR = "すべて取消"

# ---- 追加 ----
REGISTER_ADD_PROMPT = "登録する使い魔を選んでください。選んだ順が優先順になります。"
REGISTER_ADD_PLACEHOLDER = "登録する使い魔を選択"

# 登録できる使い魔が1体も無いとき
REGISTER_NO_CANDIDATES = "登録できる使い魔がありません。ガチャで入手してください。"

# 上限まで登録済みのとき。{max_units} は登録できる体数の上限。
REGISTER_FULL = "登録できるのは{max_units}体までです。"

# ---- 入れ替え ----
REGISTER_REPLACE_SLOT_PROMPT = "入れ替える枠を選んでください。優先順はそのままです。"
REGISTER_REPLACE_SLOT_PLACEHOLDER = "入れ替える枠を選択"
REGISTER_REPLACE_PLACEHOLDER = "入れ替える使い魔を選択"

# 枠を選んだあとの案内。{priority} は優先順、{name}／{level} は今その枠にいる使い魔。
REGISTER_REPLACE_PROMPT = """**{priority}番目**（{name} Lv.{level}）を、どの使い魔へ入れ替えますか？
-# 優先順はそのままです。"""

# 入れ替え先の候補が無いとき
REGISTER_NO_REPLACEMENT = "入れ替えられる使い魔がありません。ガチャで入手してください。"

# ---- 取消 ----
REGISTER_REMOVE_PROMPT = "取り消す使い魔を選んでください。以降の優先順は1つ繰り上がります。"
REGISTER_REMOVE_PLACEHOLDER = "取り消す使い魔を選択"

# セレクトの見出しに付ける優先順。{priority} は番号。
REGISTER_SLOT_PREFIX = "{priority}番目："

# ---- 事前登録のうまくいかなかったとき ----
REGISTER_NONE = "登録されている使い魔がありません。"
REGISTER_ALREADY = "その使い魔は既に登録されています。"
REGISTER_NOT_REGISTERED = "その使い魔は登録されていません。"


# ==================================================
# 出場する使い魔（使い魔バトルチャンネルの「出場する使い魔」）
# ==================================================
ENTRY_OVERVIEW_HEADING = "**出場する使い魔**"

# 【ギルド合計】【あなたの枠】の右側。
ENTRY_GUILD_TOTAL = "{count}/{max_units}体"
ENTRY_YOUR_SLOT = "{count}/{assigned}体"

# 出場者1人分の行。{mark} は行頭の記号、{suffix} は自分だけに付く目印。
ENTRY_MEMBER_LINE = "{mark} <@{user_id}>{suffix}　{count}/{assigned}体"
ENTRY_MARK_YOU = "▶"
ENTRY_MARK_OTHER = "・"
ENTRY_SUFFIX_YOU = "（あなた）"

# 使い魔をセットしていない出場者（行頭は全角スペースで字下げしています）
ENTRY_UNSET = "　未設定"

# セットした使い魔を手放してしまっていたとき（行頭は全角スペース）
ENTRY_NOT_OWNED = "　所有していません"

# セットした使い魔1体の表示（行頭は全角スペース）。{rank} はランク、{cost} はCOST。
ENTRY_FAMILIAR_LINE = "　{rank} **{name}** Lv.{level}　COST {cost}"

# その使い魔の能力（行頭は全角スペース2つ）
ENTRY_DETAIL_LINE = "　　{detail}"

# 【合計COST】の右側。上限を決めているときだけ「/上限」が付きます。
ENTRY_COST_WITH_LIMIT = "{cost}/{limit}"
ENTRY_COST_OVER = "{cost} ⚠ 上限超過"

# COSTの決まりかたと、体数の決まりかたの案内
COST_TABLE_NOTE = "-# COSTはランクで決まります：S 5／A 4／B 3／C 2"
ENTRY_OVERVIEW_FOOTER = (
    "-# 体数はギルドマスターが割り当てます。枠のなかで自由に差し替えできます。"
)

# ---- 出場する使い魔のボタン ----
ENTRY_BUTTON_ADD = "使い魔を追加"
ENTRY_BUTTON_SWAP = "入れ替え"
ENTRY_BUTTON_REMOVE = "セットを解除"

# ---- 追加 ----
ENTRY_ADD_PROMPT = "セットする使い魔を選んでください。"
ENTRY_ADD_PLACEHOLDER = "セットする使い魔を選択"

# セットできたとき。{name} は使い魔、{count}/{max_units} はギルド合計。
ENTRY_ADDED = """{name} をセットしました。（ギルド合計 {count}/{max_units}体）
-# 事前登録の先頭も、いま出場する使い魔の順番に合わせました。"""

# セットできる使い魔が無いときの見出し（この下に理由を並べます）
ENTRY_NO_SETTABLE_HEADING = "**セットできる使い魔がありません。**"

# ---- 入れ替え ----
ENTRY_SWAP_SLOT_PROMPT = "入れ替える枠を選んでください。"
ENTRY_SWAP_SLOT_PLACEHOLDER = "入れ替える枠を選択"
ENTRY_SWAP_PLACEHOLDER = "入れ替える使い魔を選択"

# 枠を選んだあとの案内。{familiar} は今その枠にいる使い魔。
ENTRY_SWAP_PROMPT = "**{familiar}** を、どの使い魔へ入れ替えますか？"

# 使い魔名が読めなかったときの呼び方
ENTRY_SWAP_FALLBACK = "この使い魔"

# 入れ替えられたとき。{name} は入れ替え後の使い魔。
ENTRY_SWAPPED = """{name} へ入れ替えました。
-# 事前登録の同じ順番も、この使い魔へ入れ替えました。
-# 外した使い魔は1つ下の控えへ下げます（登録が上限まで埋まっている場合は登録から抜けます）。"""

# 入れ替え先の候補が無いときの見出し（この下に理由を並べます）
ENTRY_NO_SWAPPABLE_HEADING = "**入れ替えられる使い魔がありません。**"

# 入れ替える枠が無いとき
ENTRY_NO_SWAPPABLE = "入れ替えられる使い魔がありません。"

# ---- 解除 ----
ENTRY_REMOVE_PROMPT = "解除する使い魔を選んでください。"
ENTRY_REMOVE_PLACEHOLDER = "解除する使い魔を選択"

# 解除できたとき。{count}/{max_units} はギルド合計。
ENTRY_REMOVED = """セットを解除しました。（ギルド合計 {count}/{max_units}体）
-# 事前登録からも外しました。使うときは登録し直してください。"""

# 解除できる使い魔が無いとき
ENTRY_NO_REMOVABLE = "解除できる使い魔がありません。"

# セレクトの見出しに付ける枠番号。{slot} は何体目か。
ENTRY_SLOT_PREFIX = "{slot}体目："

# ---- 出場者に選ばれていない人が押したとき ----
NOT_IN_ROSTER = """あなたは出場者に選ばれていません。
-# 「事前登録」で使い魔を登録しておくと、出場者に選ばれたときに順番どおり自動でセットされます。"""

# ---- 使い魔セットのうまくいかなかったとき ----
# ギルド全体で上限まで埋まっている。{max_units} は上限体数。
ENTRY_ERROR_FULL = "このギルドは既に{max_units}体セット済みです。解除してから追加してください。"

# 合計COST上限を超える。{current} は現在のCOST、{adding} は増えるCOST、{limit} は上限。
ENTRY_ERROR_COST_OVER = """編成の合計COST上限を超えます（現在{current} + {adding} > 上限{limit}）。
-# COSTの低い使い魔へ入れ替えるか、先に1体解除してください。
-# COSTはランクで決まります：S 5／A 4／B 3／C 2"""

# 自分の割り当てを超える。{limit} は自分に割り当てられた体数。
ENTRY_ERROR_MEMBER_LIMIT = (
    "あなたに割り当てられた体数は{limit}体です。先に1体解除してから差し替えてください。"
)

ENTRY_ERROR_ALREADY_SET = "その使い魔は既にセットされています。"
ENTRY_ERROR_NOT_SET = "その使い魔はセットされていません。"


# ==================================================
# セットできる使い魔が無い理由（10.3節）
# ==================================================
# プレイヤーランクが読めなかったとき
RANK_UNKNOWN = "プレイヤーランクを確認できませんでした。運営へ連絡してください。"

# クラスロールが付いていないとき
NO_CLASS_ROLE = "クラスロール（S・A・B・C）が付いていないため、使い魔を使役できません。"
NO_CLASS_ROLE_HINT = "-# 運営へ連絡してクラスロールを付けてもらってください。"

# 使い魔を1体も持っていないとき
NO_OWNED_FAMILIAR = "使い魔を所有していません。ガチャで入手してください。"

# 持っている使い魔をすべてセット済みのとき
ALL_ALREADY_SET = "所有している使い魔はすべてセット済みです。"
ALL_ALREADY_SET_HINT = (
    "-# 入れ替えるには「入れ替え」から、外すには「セットを解除」から操作してください。"
)

# 残っている使い魔のランクが高すぎるとき。
# {player_rank} は自分のランク、{ranks} は残っている使い魔のランク。
RANK_TOO_LOW = "あなたのランク（{player_rank}）では、残っている使い魔（{ranks}）を使役できません。"

# 使役できるランクの案内。{ranks} は使役できるランク。
RANK_USABLE_HINT = "-# 使役できるのは {ranks} までです（自分のランクより1段階上まで）。"


# ==================================================
# バトルレートとベット額（12節）
# ==================================================
# レートを選ぶ画面の案内。{next_step} は選んだ直後に起きること。
BET_RATE_GUIDE = """**バトルレートを選んでください。**選ぶとすぐに{next_step}。
-# 賭けたcoinは、負けたギルドから勝ったギルドへ移ります。
-# ギルドの負担額は出場者で均等に分担します。"""

# 上の {next_step} に入る文
NEXT_STEP_OPPONENT = "相手ギルドの選択へ進みます"
NEXT_STEP_RECRUIT = "募集を投稿します"

# レートのセレクト。{action} は「バトル申請」などの操作名。
BET_RATE_PLACEHOLDER = "{action}のバトルレートを選択"

# レート1件の説明欄。{coin} はギルドごとのベット額。
BET_RATE_OPTION_DESCRIPTION = "ギルドごと {coin}／勝ったギルドが受け取ります"

# レートを1つも決めていないマスターデータのときに使う名前
BET_RATE_DEFAULT_NAME = "標準レート"

# 選んだレートが見つからなかったとき
BET_RATE_UNAVAILABLE = "そのレートは選べません。"

# 【ベット額】の右側。{coin} は金額。
BET_PER_GUILD = "ギルドごと {coin}"

# ベット額を決めたあとの確認文（1人あたりの分担額を添える）
BET_SHARE_LINE = "-# {notice}"
BET_TRANSFER_NOTE = (
    "-# 負けた側のcoinは勝った側へ移ります。残高が足りない場合は"
    "持っているぶんだけ移ります。"
)

# 1人あたりの分担額。{coin} はギルド合計、{count} は出場者数、{each} は1人分。
BET_SHARE_NOTICE = "ギルド合計 {coin}／出場者{count}人で分担（1人 {each}）"

# 1人分が人によって違うとき。{low}〜{high} は少ない人と多い人の額。
BET_SHARE_RANGE = "{low}〜{high} coin"

# 出場者がまだ決まっていないときの分担額表示。{coin} はギルド合計。
BET_SHARE_SIMPLE = "ベット額：{coin}"

# バトル開始時と結果に出すベットの説明。
# {coin} はギルドごとのベット額、{win_xp}／{lose_xp} はもらえるXP。
BET_NOTICE = (
    "ギルドごとに {coin} をベットします（出場者で均等に分担）。"
    "負けた側のcoinは勝った側へ移ります（勝利 {win_xp} XP／敗北 {lose_xp} XP）。"
)


# ==================================================
# バトル申請（12.1節）
# ==================================================
# 相手ギルドを選ぶ画面。{rate} はレート名、{bet} はベット額の確認文。
OPPONENT_SELECT_PROMPT = """**{rate}** で対戦を申し込むギルドを選んでください。
{bet}"""
OPPONENT_SELECT_PLACEHOLDER = "対戦を申し込むギルドを選択"

# 相手ギルドの選択肢に出す戦績
OPPONENT_OPTION_RECORD = "{wins}勝 {losses}敗 {draws}分"

# 相手ギルド名が読めなかったときの呼び方
OPPONENT_FALLBACK = "相手ギルド"

# 申し込める相手がいないとき
NO_OPPONENT_AVAILABLE = "現在申し込めるギルドがありません。"

# 相手ギルドのマスター専用TCへ届けられなかったとき
REQUEST_NO_OPPONENT_CHANNEL = "相手ギルドのマスター専用TCが見つかりませんでした。"

# ---- 相手ギルドへ届く申請Embed ----
REQUEST_EMBED_TITLE = "⚔ ギルドバトル申請"
REQUEST_EMBED_INTRO = "**{guild_name}** から対戦の申し込みが届きました。"
REQUEST_EMBED_NOTE = "承認すると開始前チェックを行い、条件を満たしていればバトルが始まります。"
REQUEST_EMBED_FOOTER = (
    "-# 承認すると、負けた側のcoinが勝った側へ移ります。"
    "出場者で均等に分担します。"
)

# 申請Embedのボタン
BUTTON_APPROVE = "承認"
BUTTON_REJECT = "拒否"

# ---- 申請を送った側への返事 ----
# {guild_name} は相手ギルド名、{bet} はベット額の確認文。
REQUEST_SENT = """**{guild_name}** へバトル申請を送信しました。
{bet}
-# 相手が回答する前なら、もう一度「バトル申請」から取り消せます。"""

# 申請中にもう一度「バトル申請」を押したとき。{guild_name} は相手ギルド名。
REQUEST_PENDING_OUT = "**{guild_name}** へ申請中です。取り消せます。"

# 相手から申請が届いている状態で「バトル申請」を押したとき
REQUEST_PENDING_IN = "受信中のバトル申請があります。先に承認または拒否してください。"

# ---- 申請の取消 ----
REQUEST_CANCEL_BUTTON = "申請を取り消す"
REQUEST_CANCELLED_DONE = "バトル申請を取り消しました。"

# 相手ギルドのギルドTCへ届く取消のお知らせ
REQUEST_CANCELLED_TITLE = "⚔ ギルドバトル申請の取消"
REQUEST_CANCELLED_BODY = "**{guild_name}** がバトル申請を取り消しました。"

# ---- 申請への回答 ----
# 承認したとき。{bet} はベット額の確認文。
REQUEST_APPROVED = """申請を承認しました。開始前チェックを行います。
{bet}"""

REQUEST_REJECTED_DONE = "申請を拒否しました。"

# 申請元のギルドTCへ届く拒否のお知らせ
REQUEST_RESULT_TITLE = "⚔ ギルドバトル申請の結果"
REQUEST_REJECTED_BODY = "**{guild_name}** がバトル申請を拒否しました。"

# ---- 申請が見つからなかったとき ----
REQUEST_LOAD_ERROR = "申請情報を取得できませんでした。"
REQUEST_NOT_FOUND = "この申請は見つかりませんでした。"


# ==================================================
# バトル募集（12.2節）
# ==================================================
# 募集チャンネルへ貼るEmbed
RECRUIT_EMBED_TITLE = "⚔ GUILD BATTLE"
RECRUIT_EMBED_INTRO = "「**{guild_name}**」が対戦ギルドを募集しています。"
RECRUIT_EMBED_FOOTER = (
    "-# 申し込むと、負けた側のcoinが勝った側へ移ります。"
    "出場者で均等に分担します。"
)

# 【出場人数】の右側。{min_members}〜{max_members} は出場できる人数。
RECRUIT_MEMBER_RANGE = "{min_members}～{max_members}人"

# 【状態】の右側
RECRUIT_STATE_OPEN = "対戦相手募集中"
RECRUIT_STATE_CLOSED = "募集終了"

# 募集Embedのボタン
BUTTON_APPLY = "対戦申請"

# 募集を投稿できたとき。{channel} は募集チャンネル、{rate} はレート名、
# {bet} はベット額の確認文。
RECRUIT_POSTED = """{channel} へ **{rate}** のバトル募集を投稿しました。
{bet}"""

# 募集中にもう一度「バトル募集」を押したとき
RECRUIT_PENDING = "現在バトルを募集中です。取り消せます。"

# 募集の取消
RECRUIT_CANCEL_BUTTON = "募集を取り消す"
RECRUIT_CANCELLED_DONE = "バトル募集を取り消し、募集の投稿も削除しました。"

# 募集チャンネルが用意できていないとき
RECRUIT_CHANNEL_UNSET = "ギルドバトル募集チャンネルが設定されていません。"
RECRUIT_CHANNEL_NOT_FOUND = "ギルドバトル募集チャンネルが見つかりません。"

# 募集が見つからなかったとき
RECRUIT_LOAD_ERROR = "募集情報を取得できませんでした。"
RECRUIT_NOT_FOUND = "この募集は見つかりませんでした。"

# ギルドマスター以外が対戦申請を押したとき
APPLY_MASTER_ONLY = "ギルドマスターだけが対戦を申し込めます。"

# 対戦が決まったとき。{bet} はベット額の確認文。
MATCH_MADE = """対戦が成立しました。開始前チェックを行います。
{bet}"""


# ==================================================
# 出場条件の確認（バトルを始める前のチェック・13節）
# ==================================================
# ギルドそのものが使えないとき
READY_GUILD_INACTIVE = "ギルドが見つからない、または活動中ではありません。"
READY_DISCORD_GUILD_MISSING = "Discordサーバー情報を取得できませんでした。"

# ギルドのチャンネルが無いとき。{label} には下の名前が入ります。
READY_CHANNEL_MISSING = "{label}が見つかりません。"
READY_CHANNEL_CATEGORY = "ギルドカテゴリー"
READY_CHANNEL_GUILD_TEXT = "ギルドTC"
READY_CHANNEL_MASTER_TEXT = "ギルドマスター専用TC"
READY_CHANNEL_BATTLE_MEMBER = "使い魔バトルチャンネル"

# 既にバトル中のとき
READY_BATTLE_IN_PROGRESS = "このギルドには既に進行中のバトルがあります。"

# 出場者・使い魔がそろっていないとき
READY_NO_ROSTER = (
    "出場者がセットされていません。"
    "ギルドマスター専用tcの「メンバーセット」で決めてください。"
)
READY_TOO_MANY_MEMBERS = "出場者は最大{max_members}人です（現在{count}人）。"
READY_NO_ENTRIES = "使い魔がセットされていません。"
READY_TOO_MANY_UNITS = "使い魔は最大{max_units}体です（現在{count}体）。"

# 出場者ごとの確認。{mention} はその人へのメンション。
READY_NOT_IN_GUILD = "{mention} は現在このギルドへ所属していません。"
READY_PLAYER_UNKNOWN = "{mention} のプレイヤー情報を確認できません。"
READY_NOT_MEMBER_RANK = (
    "{mention} は本メンバーではないため出場できません。出場者から外してください。"
)
READY_PLAYER_BUSY = "{mention} は他の進行中バトルへ参加しています。"

# 割り当てた体数まで埋まっていないとき。{assigned} は割り当て、{current} は現在数。
READY_SHORT_ASSIGNMENT = (
    "{mention} の割り当ては{assigned}体ですが、{current}体しかセットされていません。"
)

# セットされた使い魔ごとの確認
READY_NOT_IN_ROSTER = "{mention} は出場者から外れています。"
READY_NOT_OWNED = "{mention} がセットした使い魔を現在所有していません。"
READY_MASTER_DATA_MISSING = "{mention} がセットした使い魔のマスターデータがありません。"
READY_RANK_UNKNOWN = "{mention} のプレイヤーランクを確認できません。"

# 使役できないランクの使い魔をセットしているとき。
# {familiar} は使い魔名、{rank} はそのランク。
READY_RANK_TOO_LOW = "{mention} は{familiar}（{rank}）を使役できません。"

# 合計COSTが上限を超えているとき。{total} は合計、{limit} は上限。
READY_COST_OVER = (
    "編成の合計COSTが上限を超えています（{total}／{limit}）。"
    "COSTの低い使い魔へ入れ替えてください。"
)


# ==================================================
# 申請・募集を受け付けられない理由（12節）
# ==================================================
LOCK_RECRUITING = "このギルドは現在バトルを募集中です。先に募集を取り消してください。"
LOCK_REQUEST_PENDING = (
    "このギルドには回答待ちのバトル申請があります。"
    "先に取り消すか、相手の回答を待ってください。"
)
LOCK_IN_BATTLE = "このギルドは現在バトル中です。終了までお待ちください。"
LOCK_OTHER = "このギルドは現在ほかの対戦手続き中です。"

# 直すことを並べる画面の見出し。{action} には下の操作名が入ります。
BLOCKER_HEADING = "**{action}できません。** 次の点を直してください。"

# 上の {action} と、レート選択の見出しに入る操作名
ACTION_REQUEST = "バトル申請"
ACTION_RECRUIT = "バトル募集"
ACTION_APPLY = "対戦申請"

# 直すことを1つずつ並べる行
ISSUE_LINE = "・{issue}"


# ==================================================
# バトルを開始できなかったとき（13節）
# ==================================================
START_FAILURE_TITLE = "**⚔ ギルドバトルを開始できませんでした**"
START_FAILURE_NOTE = "次の条件を満たしてから、改めて申請・募集してください。"

# そのギルドには問題が無かったとき
START_FAILURE_OK = "条件を満たしています。"

# 開始処理そのものに失敗したとき
START_FAILED_REASON = "バトルの開始処理に失敗しました。時間をおいて試してください。"


# ==================================================
# バトル開始の告知（バトル専用チャンネル）
# ==================================================
OPENING_TITLE = "⚔ GUILD BATTLE 開始"

# 対戦カード。{guild_a}／{guild_b} は両ギルドの名前。
OPENING_VERSUS = "**{guild_a}** vs **{guild_b}**"

# 【ベット額】の右側。{coin} はギルドごとのベット額。
OPENING_BET = "ギルドごと {coin}（出場者で均等に分担）"

# 【XP】の右側
OPENING_XP = "勝利 {win_xp}／敗北 {lose_xp}"

# いちばん下に小さく出すベットの説明。{notice} は BET_NOTICE の文。
OPENING_BET_NOTE = "-# {notice}"


# ==================================================
# バトル中の操作（16節・17節・19節）
# ==================================================
# ターン通知に付くボタン
BUTTON_SKILL = "特殊スキル"
BUTTON_ATTACK = "攻撃"

# ---- 攻撃 ----
ATTACK_PROMPT = "攻撃対象を選んでください。"
ATTACK_TARGET_PLACEHOLDER = "攻撃対象を選択"

# 攻撃権を使わないスキルのあと、続けて攻撃対象を選ばせるときの案内
ATTACK_AFTER_SKILL_PROMPT = "続けて攻撃対象を選んでください。"

# 効果がかかっていないとき（【かかっている効果】の右側）
EFFECT_NONE = "なし"

# 攻撃できる相手がいないとき
NO_ATTACK_TARGET = "攻撃できる対象がいません。"

# ---- スキル ----
SKILL_PROMPT = "使用するスキルを選んでください。"
SKILL_SELECT_PLACEHOLDER = "使用するスキルを選択"

# スキルの選択肢の説明欄。{remaining} は下の残り回数、{description} はスキルの説明。
SKILL_OPTION_DESCRIPTION = "{remaining}　{description}"
SKILL_REMAINING = "残り{count}回"
SKILL_UNLIMITED = "回数無制限"

# 対象を選ぶ画面。{skill} はスキル名、{target} は「敵」などの対象の呼び名。
SKILL_TARGET_LABEL = "{skill}：{target}を{count}体選択"
SKILL_TARGET_LABEL_EACH = "{skill}：{target}{index}体目"

# 使う直前の確認
SKILL_CONFIRM_TITLE = "✦ SKILL「{skill}」を使用しますか？"
SKILL_CONFIRM_BUTTON = "使用する"

# 使ったあとの控え
SKILL_USED = "スキル「{skill}」を使用しました。"

# 「やめる」を押したとき
SKILL_CANCELLED = "スキルの使用をやめました。行動を選び直してください。"

# ---- スキルが使えないとき ----
SKILL_NOT_FOUND = "スキル定義が見つかりません。"
SKILL_ALREADY_USED = "このターンは既にスキルを使用しています。"
NO_AVAILABLE_SKILL = "現在使用できるスキルがありません。"
NO_SELECTABLE_TARGET = "選択できる対象がいません。"

# ---- 行動を受け付けられないとき ----
NO_BATTLE_IN_CHANNEL = "このチャンネルで進行中のバトルはありません。"
BATTLE_NOT_IN_PROGRESS = "このバトルは現在進行中ではありません。"
BATTLE_LOAD_ERROR = "バトル情報を取得できませんでした。"
NO_ACTION_ACCEPTED = "現在は行動を受け付けていません。"
NOT_YOUR_TURN = "現在の行動順のプレイヤーだけが操作できます。"
ACTION_FAILED = "行動を処理できませんでした。"
ACTION_RACED = "他の操作が先に処理されました。"


# ==================================================
# 降参（26.1節）
# ==================================================
SURRENDER_CONFIRM = """本当に降参しますか？
降参したギルドの敗北として戦績へ記録されます。"""
SURRENDER_CONFIRM_BUTTON = "降参する"
SURRENDER_DONE = "降参しました。"
SURRENDER_FAILED = "降参を処理できませんでした。"

# 進行中のバトルが無いのに「降参」を押したとき
NO_ACTIVE_BATTLE = "進行中のバトルがありません。"


# ==================================================
# 清算（バトルが終わったあとのcoinとXP・26.2節）
# ==================================================
SETTLEMENT_TITLE = "バトル清算"

# coinが動かなかったとき
SETTLEMENT_NO_MOVE = "coinの移動はありません。"

# 勝ち・負け・引き分けの見出し（左側の win / lose / draw は変えないでください）
OUTCOME_LABELS = {
    "win": "勝利",
    "lose": "敗北",
    "draw": "引き分け",
}

# 上の見出しの出し方
SETTLEMENT_GROUP_HEADING = "**{outcome}**"

# 出場者1人分の結果。{coin} は増減、{xp} はもらえたXP。
SETTLEMENT_MEMBER_LINE = "<@{user_id}>　{coin}／{xp}"
SETTLEMENT_COIN = "{coin} coin"
SETTLEMENT_COIN_ZERO = "±0 coin"
SETTLEMENT_XP = "{xp} XP"
SETTLEMENT_XP_CAPPED = "XP上限"

# 既に清算が済んでいたとき
SETTLEMENT_ALREADY_DONE = "このバトルはすでに清算済みです。"

# ---- 報酬を出さない理由 ----
NO_REWARD_ABORTED = "運営による強制中止のため報酬はありません。"
NO_REWARD_EARLY_SURRENDER = "{round}巡目より前の降参のため、両ギルドとも報酬はありません。"
NO_REWARD_NO_RESULT = "勝敗が確定しなかったため報酬はありません。"


# ==================================================
# ギルドランキング（26.2節）
# ==================================================
# ギルド受付チャンネルへ置くパネルの説明文
RANKING_PANEL_BODY = """​
**ギルドバトルの通算成績**
-# ボタンを押すと上位ギルドと自分のギルド順位を表示します。"""

# パネルのボタン
BUTTON_RANKING = "ランキング"

RANKING_EMBED_TITLE = "🏆 ギルドランキング"

# 1ギルド分の行。{rank} は順位、{points} は点数。
RANKING_LINE = "{rank}. **{name}**　{points}点（{wins}勝 {losses}敗 {draws}分）"

# 【あなたのギルド】の右側。{position} は下の順位表示。
RANKING_OWN_GUILD = "**{name}**　{position}"
RANKING_POSITION = "{position}位"
RANKING_NO_POSITION = "順位なし"

# まだ1ギルドも戦っていないとき
RANKING_EMPTY = "まだランキング対象のギルドがありません。"

# Embedのいちばん下に出す配点
RANKING_FOOTER = "勝利{win}点 / 引き分け{draw}点 / 敗北{lose}点"


# ==================================================
# 再起動後の復旧（29節）
# ==================================================
# 再開できるかを点検したときの問題点。{guild_id} はギルドの番号。
RECOVERY_GUILD_INACTIVE = "ギルド{guild_id}が活動中ではありません。"
RECOVERY_NO_CHANNEL = "ギルド{guild_id}のバトル専用チャンネルがありません。"
RECOVERY_MEMBER_MOVED = "<@{user_id}> の所属が変わっています。"


# ==================================================
# 運営用コマンド（/バトル中止・/バトル再開）
# ==================================================
# 運営以外が実行したとき
ADMIN_ONLY = "運営だけが実行できます。"

# /バトル中止
ABORT_REASON_REQUIRED = "中止理由を入力してください。"
ABORT_BATTLE_NOT_FOUND = "指定のバトルが見つかりません。"
ABORT_FAILED = "中止できませんでした。"
ABORT_DONE = "バトル {battle_id} を強制中止しました。勝敗と報酬は記録されません。"

# /バトル再開
RESUME_FAILED = (
    "再開できませんでした。ギルド・チャンネル・出場者の状態を確認し、"
    "復旧できない場合は `/バトル中止` で終了させてください。"
)
RESUME_DONE = "バトル {battle_id} を再開しました。"


# ==================================================
# うまくいかなかったときの案内（そのほか）
# ==================================================
# ボタンを押した場所が違うとき
NOT_MASTER_CHANNEL = "このチャンネルはギルドマスター専用TCではありません。"
NOT_BATTLE_MEMBER_CHANNEL = "このチャンネルは使い魔バトルチャンネルではありません。"

# ギルドが解散などで活動していないとき
GUILD_NOT_ACTIVE = "このギルドは現在活動中ではありません。"
