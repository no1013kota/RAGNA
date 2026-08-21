"""宿屋の文言（入室プラン・VC設定・招待や出禁などの権限操作）。

編集のしかたは ``texts/__init__.py`` を読んでください。
``{name}`` のような波かっこは、Botが値を入れ替える場所です。
"""

# ==================================================
# プラン名
# ==================================================
# 3つのプランの呼び名です。プラン選択メニュー・作成されるチャンネル名・購入通知
# など、宿屋のあちこちに出ます。料金表（``config.py`` の HOTEL_PLANS）もここを
# 見ているため、書き換えるとまとめて変わります。
# ※作成済みの宿はこの名前で記録されています。宿が1つも無い時間に変えてください。
PLAN_STANDARD = "ツーショ"
PLAN_SECRET = "シークレット"
PLAN_PREMIUM = "プレミアム"


# ==================================================
# 宿屋パネル（宿屋パネルチャンネル）
# ==================================================
# パネルの説明文。{standard_price}・{secret_price}・{premium_price} には
# それぞれのプランの料金が「5,000」の形で入ります。
PANEL_BODY = """​
**ツーショ：{standard_price} coin**
-# ※七聖・騎士は無料

**シークレット：{secret_price} coin**
ー ツーショ＋指定した人だけが見える

**プレミアム：{premium_price} coin**
ー すべて自由にできる＋人数も無制限

-# ※宿は15時間後に自動削除します"""

# パネルのボタン
PANEL_BUTTON_PLAN = "入室プラン"
PANEL_BUTTON_RENAME = "VC名変更"
PANEL_BUTTON_LIMIT = "人数変更"
PANEL_BUTTON_STATUS = "ステータス変更"


# ==================================================
# 宿のVC設定（パネルのボタンから開く入力画面）
# ==================================================
# 宿のVCに入っていない人がボタンを押したとき
NOT_IN_VOICE = "VCにいるときだけ使用できます。"

# ----- 人数変更 -----
LIMIT_MODAL_TITLE = "人数変更"
LIMIT_MODAL_LABEL = "人数"
LIMIT_MODAL_PLACEHOLDER = "1～3（プレミアムは0で無制限）"

# 数字以外を入力したとき
LIMIT_INVALID_NUMBER = "数字を入力してください。"

# プレミアムで0～99以外を入力したとき
LIMIT_OUT_OF_RANGE_PREMIUM = "0～99で入力してください。"

# ツーショ・シークレットで1～3以外を入力したとき
LIMIT_OUT_OF_RANGE = "1～3人にしてください。"

# 変更できたとき。{limit} には人数（0のときは下の「無制限」）が入ります。
LIMIT_CHANGED = "人数を **{limit}** に変更しました。"
LIMIT_UNLIMITED = "無制限"

# ----- VC名変更 -----
RENAME_MODAL_TITLE = "VC名変更"
RENAME_MODAL_LABEL = "VC名"
RENAME_DONE = "VC名を変更しました。"

# ----- ステータス変更 -----
STATUS_MODAL_TITLE = "ステータス変更"
STATUS_MODAL_LABEL = "ステータス"
STATUS_MODAL_PLACEHOLDER = "雑談中・ゲーム中など"
STATUS_DONE = "ステータスを変更しました。"


# ==================================================
# プランを選んで宿を作る
# ==================================================
PLAN_SELECT_PLACEHOLDER = "プランを選択してください"

# プラン選択メニューに出る、それぞれの説明
PLAN_STANDARD_DESCRIPTION = "2人まで利用可能"
PLAN_SECRET_DESCRIPTION = "通話相手のみ閲覧可能"
PLAN_PREMIUM_DESCRIPTION = "人数・用途フリー"

# 宿屋を使えないロールの人が選んだとき
DENY_ROLE = "現在のロールでは宿屋を利用できません。"

# coinが足りなかったとき
NOT_ENOUGH_BALANCE = "残高が不足しています。"

# 無料抽選に当たったとき（作成完了メッセージの最後に付きます）
FREE_LOTTERY_WON = "宿屋無料抽選に当選したため、無料になりました。"

# 作られるチャンネルの名前。{plan} はプラン名、{name} は購入した人の表示名。
VOICE_CHANNEL_NAME = "{plan}-{name}"
TEXT_CHANNEL_NAME = "宿設定-{plan}"

# 作成に失敗したとき。{refund} には、下の2つのどちらかが入ります。
CREATE_FAILED = """宿屋の作成に失敗しました。
{refund}"""

# {amount} には返金額が「5,000」の形で入ります。
CREATE_FAILED_REFUNDED = "支払った **{amount} coin** は返金しました。"
CREATE_FAILED_NO_PAYMENT = "coinの支払いは発生していません。"

# 作成できたとき。{voice_channel} と {text_channel} にはチャンネルのリンクが入ります。
CREATED = "{voice_channel} を作成しました。"
CREATED_WITH_TEXT = """{voice_channel} を作成しました。
{text_channel} で宿の設定ができます。"""


# ==================================================
# 宿の中の権限パネル（シークレット・プレミアムの設定用チャンネル）
# ==================================================
PERMISSION_PANEL_TITLE = "権限設定"

# プレミアムの説明。{voice_channel} には宿のVCのリンクが入ります。
PREMIUM_PANEL_BODY = """{voice_channel}
ここは宿を作成した人しか見えないよ！
-# ※共有したら見えるようになるよ

🔓 公開
🔒 非公開

👥 招待
🚫 出禁
🤝 共有"""

# シークレットの説明。{voice_channel} には宿のVCのリンクが入ります。
SECRET_PANEL_BODY = """{voice_channel}
※ここは宿を作成した人しか見えないよ！

👥 招待
🚫 出禁"""

# 権限パネルのボタン
BUTTON_OPEN = "🔓 公開"
BUTTON_CLOSE = "🔒 非公開"
BUTTON_INVITE = "👥 招待"
BUTTON_DENY = "🚫 出禁"
BUTTON_SHARE = "🤝 共有"

# 招待・出禁で「誰を指定するか」を選ぶボタン
BUTTON_TARGET_USER = "👤 ユーザー"
BUTTON_TARGET_ROLE = "🏷 ロール"

# 公開・非公開を切り替えたとき
OPENED = "宿を公開しました。"
CLOSED = "宿を非公開にしました。"

# 宿を買った人がサーバーから居なくなっているとき
OWNER_NOT_FOUND = "宿の購入者が見つかりません。"


# ==================================================
# 招待・出禁・共有
# ==================================================
USER_SELECT_PLACEHOLDER = "ユーザーを選択してください"
ROLE_SELECT_PLACEHOLDER = "ロールを選択してください"
SHARE_SELECT_PLACEHOLDER = "共有するユーザーを選択"

# ----- 招待・出禁 -----
# Botを選んだとき
TARGET_BOT = "Botは指定できません。"

# 宿屋を使えないロールの人を招待しようとしたとき
INVITE_DENIED_USER = "宿屋を利用できないユーザーは招待できません。"

# 宿屋を使えないロールそのものを指定したとき
TARGET_DENIED_ROLE = "このロールは指定できません。"

# 設定できたとき。{target} は相手のリンク、{mode} は下の「招待」か「出禁」。
TARGET_UPDATED = "{target} を **{mode}**"
MODE_INVITE = "招待"
MODE_DENY = "出禁"

# ----- 共有（宿の設定を一緒に触れるようにする） -----
SHARE_BOT = "Botには共有できません。"
SHARE_DENIED_USER = "宿屋を利用できないユーザーには共有できません。"
SHARE_SELF = "自分には付与できません。"
SHARE_NOT_IN_VOICE = "指定したユーザーがVCにいません。"
SHARE_ALREADY = "既に共有しています。"

# 共有できたとき。{user} には相手のリンクが入ります。
SHARE_DONE = "{user} に権限を付与しました。"


# ==================================================
# 購入者へのDM・運営向けのログ
# ==================================================
# 公開・非公開を切り替えたときに、購入者へ送るDM
VISIBILITY_DM_TITLE = "【宿】設定変更"

# {channel} は宿のVC名、{mode} は「公開」か「非公開」、
# {heading} は「閲覧不可」か「閲覧可能」、{targets} はその一覧。
VISIBILITY_DM_BODY = """{channel} を {mode} にしました。
### {heading}
{targets}"""

VISIBILITY_MODE_PUBLIC = "公開"
VISIBILITY_MODE_PRIVATE = "非公開"

# 公開にしたときは「見えない人」、非公開にしたときは「見える人」を並べます。
VISIBILITY_HEADING_HIDDEN = "閲覧不可"
VISIBILITY_HEADING_VISIBLE = "閲覧可能"

# 一覧の並び。{name} にはロール名・表示名が入ります。
PERMISSION_LIST_ROLE = "（ロール）{name}"
PERMISSION_LIST_USER = "（ユーザー）{name}"

# 一覧に誰もいないとき
PERMISSION_LIST_EMPTY = "なし"

# 宿屋利用ログ・宿のVCチャットへ送る購入通知。
# {user} は購入した人のリンク、{plan} はプラン名、{expires_at} は「01/23 04:56」の形。
CREATE_LOG_TITLE = "購入通知"
CREATE_LOG_BODY = """購入者：{user}
プラン：**{plan}**
終了時刻：{expires_at}"""

# 宿屋権限変更ログ。{actor} は操作した人、{channel} は宿のVC名、
# {mode} は「招待」か「出禁」、{kind} は下の「ユーザー」か「ロール」、
# {target} は指定された相手のリンク。
PERMISSION_LOG_TITLE = "権限変更"
PERMISSION_LOG_BODY = """実行者：{actor}
通話：{channel}
設定：**{mode}**
対象：{kind}（{target}）"""

TARGET_KIND_USER = "ユーザー"
TARGET_KIND_ROLE = "ロール"
