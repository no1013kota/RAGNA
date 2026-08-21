"""ギルドまわりの文言（設立・募集・参加申請・管理・脱退・解散・ギルド情報）。

編集のしかたは ``texts/__init__.py`` を読んでください。
``{name}`` のような波かっこは、Botが値を入れ替える場所です。
"""

# ==================================================
# ギルド設立パネル（ギルド紹介チャンネル）
# ==================================================
# パネルの説明文。ボタンは「ギルド設立」「申請確認・取消」の2つ。
# {create_cost}       … 設立費用（例「1,000,000 coin」）
# {initial_capacity}  … 設立直後のメンバー定員
# {max_capacity}      … 枠を拡張したときの最大メンバー数
INTRO_PANEL_BODY = """​
**ギルド設立**
-# 設立費用：{create_cost}
-# 初期定員：{initial_capacity}人（最大{max_capacity}人）
-# 騎士・七星だけが設立できます。
-# ※既にギルドへ所属している場合は設立できません。

**申請確認・取消**
-# 自分の参加申請を確認・取消可能。"""

INTRO_BUTTON_CREATE = "ギルド設立"
INTRO_BUTTON_MY_REQUESTS = "申請確認・取消"


# ==================================================
# ギルド名・ギルド説明の入力（設立・変更で共通）
# ==================================================
# 入力欄の薄い文字（プレースホルダ）。{min_length} {max_length} は入力できる文字数。
NAME_PLACEHOLDER = "{min_length}〜{max_length}文字"
DESCRIPTION_PLACEHOLDER = "{min_length}〜{max_length}文字"

# 文字数が範囲から外れていたとき。{min_length} {max_length} は入力できる文字数。
NAME_LENGTH_ERROR = "ギルド名は{min_length}文字以上{max_length}文字以下で入力してください。"
DESCRIPTION_LENGTH_ERROR = (
    "ギルド説明は{min_length}文字以上{max_length}文字以下で入力してください。"
)


# ==================================================
# ギルド設立（5.2節）
# ==================================================
# ギルド名を入力する画面の表題と入力欄の見出し
CREATE_MODAL_TITLE = "ギルド設立"
CREATE_NAME_LABEL = "ギルド名"

# DM（サーバーの外）から押されたとき
NOT_IN_SERVER = "サーバー内で操作してください。"

# 設立できるロールを持っていないとき
CREATE_ROLE_REQUIRED = (
    "ギルドを設立できるのは騎士・七星・運営のロールを持つプレイヤーだけです。"
)

# 既にどこかのギルドへ入っているとき。{guild_name} は所属中のギルド名。
CREATE_ALREADY_IN_GUILD = "既に「{guild_name}」へ所属しているため設立できません。"

# ロールの同期状況を確認できず、設立を中止したとき
CREATE_RANK_SYNC_ERROR = (
    "ロール情報を確認できませんでした。時間をおいて再度お試しください。"
)

# 専用チャンネルを作れず、設立費用を返金したとき。{create_cost} は返した金額。
CREATE_CHANNEL_FAILED_REFUNDED = """ギルド専用チャンネルの作成に失敗しました。
設立費用 {create_cost} は返金しました。"""

# 専用チャンネルを作れず、返金も確認できなかったとき
CREATE_CHANNEL_FAILED_NOT_REFUNDED = """ギルド専用チャンネルの作成に失敗しました。
返金処理を確認できませんでした。運営へお問い合わせください。"""

# 登録したギルドを読み直せなかったとき
CREATE_SAVE_ERROR = "ギルド情報の保存を確認できませんでした。"

# 案内に載せるギルドTCの呼び方。チャンネルへのリンクを作れなかったときに使います。
CREATE_CHANNEL_FALLBACK = "ギルドTC"

# 設立できたときの案内。
# {guild_name} はギルド名、{create_cost} は設立費用、{channel} はギルドTCのリンク。
CREATE_SUCCESS = """ギルド「{guild_name}」を設立しました。
設立費用：{create_cost}
専用チャンネルを作成しました：{channel}"""


# ==================================================
# 申請確認・取消（自分が出している参加申請）
# ==================================================
# 未処理の申請が1件も無いとき
MY_REQUESTS_EMPTY = "未処理の参加申請はありません。"

MY_REQUESTS_PROMPT = "取り消す参加申請を選択してください。"
MY_REQUESTS_PLACEHOLDER = "取り消す参加申請を選択してください"

# 取り消せたときの案内。{guild_name} は申請先のギルド名。
MY_REQUESTS_CANCELLED = "「{guild_name}」への参加申請を取り消しました。"


# ==================================================
# メンバー募集Embed（ギルド紹介チャンネル・6.1節）
# ==================================================
RECRUITMENT_BUTTON_JOIN = "参加申請"

# Embedへ並べる項目名
RECRUITMENT_LABEL_NAME = "ギルド名"
RECRUITMENT_LABEL_DESCRIPTION = "ギルド説明"
RECRUITMENT_LABEL_MEMBERS = "メンバー"
RECRUITMENT_LABEL_STATUS = "状態"

# 人数の書き方。{count} は現在の人数、{capacity} は定員。
RECRUITMENT_MEMBER_COUNT = "{count} / {capacity}"

# 募集状態の表示名。
# 左側（"open" など）はプログラムが使う名前なので変えないでください。
RECRUITMENT_LABELS = {
    "open": "募集中",
    "closed": "募集停止",
}

# 解散したギルドの募集Embedに出る状態
RECRUITMENT_ARCHIVED = "解散済み"


# ==================================================
# 参加申請（6.2節）
# ==================================================
# 押された募集Embedを読み取れなかったとき
JOIN_MESSAGE_ERROR = "募集情報を取得できませんでした。"

# 募集Embedは残っているが、募集の記録が消えているとき
JOIN_RECRUITMENT_ENDED = "この募集は既に終了しています。"

# ギルドマスター専用TCへ申請Embedを送れなかったとき
JOIN_POST_FAILED = "参加申請の送信に失敗しました。時間をおいて再度お試しください。"

# 申請できたときの案内。{guild_name} は申請先のギルド名。
JOIN_SENT = """「{guild_name}」へ参加申請しました。
結果はDMでお知らせします。"""

# ギルドマスター専用TCへ出る申請Embedの中身
JOIN_REQUEST_LABEL_APPLICANT = "申請者"

# 申請Embedの下段。{guild_name} は申請先のギルド名。
JOIN_REQUEST_FOOTER = "ギルド：{guild_name}"

JOIN_REQUEST_BUTTON_APPROVE = "承認"
JOIN_REQUEST_BUTTON_REJECT = "拒否"

# 押されたEmbedを読み取れなかったとき
JOIN_REQUEST_MESSAGE_ERROR = "申請情報を取得できませんでした。"

# 申請の記録が見つからないとき
JOIN_REQUEST_NOT_FOUND = "この参加申請は見つかりません。"

# 申請してから承認までの間に、申請者が遊べる資格を失っていたとき。
# {user_id} は申請者、{reason} は利用できない理由の1行目が入ります。
JOIN_REQUEST_APPLICANT_BLOCKED = """<@{user_id}> は現在ラグナオンラインを利用できないため、参加を承認できません。
-# {reason}"""

# 承認・拒否したギルドマスターへの案内。{user_id} は申請者。
JOIN_REQUEST_APPROVED = "<@{user_id}> の参加を承認しました。"
JOIN_REQUEST_REJECTED = "<@{user_id}> の参加を拒否しました。"

# 申請者へ送るDM。{guild_name} は申請先のギルド名。
JOIN_RESULT_DM_TITLE = "ギルド参加申請"
JOIN_APPROVED_DM = "「{guild_name}」への参加が承認されました。"
JOIN_REJECTED_DM = "「{guild_name}」への参加は見送られました。"


# ==================================================
# ギルド管理パネル（ギルドマスター専用TC・8.1節）
# ==================================================
# パネルの説明文。
# {member_slot_cost}       … メンバー枠1つ分の費用（例「100,000 coin」）
# {max_capacity}           … 拡張できる最大メンバー数
# {rename_cost}            … ギルド名変更の費用
# {description_min_length} … ギルド説明に入力できる最小文字数
# {description_max_length} … ギルド説明に入力できる最大文字数
MANAGE_PANEL_BODY = """​
**メンバー追放**
-# 一般メンバーをギルドから外します。

**マスター譲渡**
-# 所属メンバー1人へマスターを譲ります。

**メンバー枠拡張**
-# 1枠 {member_slot_cost} 自動引き落としで拡張可能。（最大{max_capacity}人）

**ギルド名変更**
-# {rename_cost} 自動引き落としで変更可能。

**ギルド説明変更**
-# {description_min_length}〜{description_max_length}文字で変更可能。

**メンバー募集**
-# 募集の開始・停止を切り替えます。

**ギルド解散**
-# 再確認のうえギルドを解散します。"""

MANAGE_BUTTON_KICK = "メンバー追放"
MANAGE_BUTTON_TRANSFER = "マスター譲渡"
MANAGE_BUTTON_EXPAND = "メンバー枠拡張"
MANAGE_BUTTON_RENAME = "ギルド名変更"
MANAGE_BUTTON_DESCRIPTION = "ギルド説明変更"
MANAGE_BUTTON_RECRUIT = "メンバー募集"
MANAGE_BUTTON_DISBAND = "ギルド解散"


# ==================================================
# メンバー追放（7.2節）
# ==================================================
# 追放できる一般メンバーが1人もいないとき
KICK_NO_MEMBERS = "追放できる一般メンバーがいません。"

KICK_PROMPT = "追放するメンバーを選択してください。"
KICK_PLACEHOLDER = "追放するメンバーを選択してください"

# ギルドマスター自身を選んだとき
KICK_MASTER_DENIED = "ギルドマスターは追放できません。"

# 追放された人へ送るDM。{guild_name} はギルド名。
KICK_DM_TITLE = "ギルド追放"
KICK_DM_BODY = "ギルド「{guild_name}」から追放されました。"

# 追放したギルドマスターへの案内。{user_id} は追放した相手。
KICK_DONE = "<@{user_id}> をギルドから追放しました。"


# ==================================================
# マスター譲渡（7.3節）
# ==================================================
# 譲渡できるメンバーが1人もいないとき
TRANSFER_NO_MEMBERS = "譲渡できるメンバーがいません。"

TRANSFER_PROMPT = "マスターを譲渡するメンバーを選択してください。"
TRANSFER_PLACEHOLDER = "譲渡するメンバーを選択してください"

# 譲渡前の確認。{user_id} は譲渡する相手。
TRANSFER_CONFIRM = """<@{user_id}> へギルドマスターを譲渡します。
譲渡後、あなたは一般メンバーになります。よろしいですか？"""

TRANSFER_BUTTON_CONFIRM = "譲渡する"

# 新しいギルドマスターへ送るDM。{guild_name} はギルド名。
TRANSFER_DM_TITLE = "ギルドマスター譲渡"
TRANSFER_DM_BODY = "ギルド「{guild_name}」のギルドマスターになりました。"

# 譲渡した元マスターへの案内。{user_id} は譲渡した相手。
TRANSFER_DONE = "<@{user_id}> へギルドマスターを譲渡しました。"


# ==================================================
# メンバー枠拡張（7.4節）
# ==================================================
# 拡張前の確認。
# {capacity} は現在の定員、{next_capacity} は拡張後の定員、{cost} は費用。
EXPAND_CONFIRM = """メンバー枠を1つ拡張します（{capacity}人 → {next_capacity}人）。
費用：{cost}
よろしいですか？"""

EXPAND_BUTTON_CONFIRM = "拡張する"

# 拡張できたときの案内。{capacity} は拡張後の定員、{cost} は支払った金額。
EXPAND_DONE = """メンバー枠を拡張しました。
定員：{capacity}人
支払い：{cost}"""


# ==================================================
# ギルド名変更（5.2節）
# ==================================================
RENAME_MODAL_TITLE = "ギルド名変更"
RENAME_NAME_LABEL = "新しいギルド名"

# 変更できたときの案内。
# {old_name} は変更前のギルド名、{new_name} は変更後、{cost} は支払った金額。
RENAME_DONE = """ギルド名を「{old_name}」から「{new_name}」へ変更しました。
支払い：{cost}"""


# ==================================================
# ギルド説明変更（6.1節）
# ==================================================
DESCRIPTION_MODAL_TITLE = "ギルド説明変更"
DESCRIPTION_LABEL = "ギルド説明"

# 変更できたとき
DESCRIPTION_DONE = "ギルド説明を更新しました。"


# ==================================================
# メンバー募集の開始・停止（6.1節）
# ==================================================
# 「メンバー募集」ボタンを押した直後の案内。{status} は今の募集状態。
RECRUIT_CONTROL_PROMPT = """現在の募集状態：{status}
募集を開始するにはギルド説明の登録が必要です。"""

RECRUIT_BUTTON_START = "募集開始"
RECRUIT_BUTTON_STOP = "募集停止"
RECRUIT_BUTTON_DESCRIPTION = "説明変更"

# ギルド説明が未登録のまま募集を開始しようとしたとき
RECRUIT_DESCRIPTION_REQUIRED = """募集を開始するにはギルド説明の登録が必要です。
「ギルド説明変更」から登録してください。"""

RECRUIT_START_FAILED = "募集を開始できませんでした。"
RECRUIT_STARTED = "メンバー募集を開始しました。"
RECRUIT_STOP_FAILED = "募集を停止できませんでした。"
RECRUIT_STOPPED = "メンバー募集を停止しました。"


# ==================================================
# ギルド解散（7.5節）
# ==================================================
# 1回目の確認。{guild_name} はギルド名、{archive_days} は保存日数。
DISBAND_CONFIRM = """ギルド「{guild_name}」を解散します。
・所属メンバー全員がギルドから外れます。
・支払い済みの費用は返金されません。
・専用カテゴリーは{archive_days}日間保存後に削除されます。"""

DISBAND_BUTTON_NEXT = "確認へ進む"

# 2回目（最終）の確認。{guild_name} はギルド名。
DISBAND_FINAL_CONFIRM = """最終確認：ギルド「{guild_name}」を解散します。
この操作は取り消せません。本当によろしいですか？"""

DISBAND_BUTTON_CONFIRM = "解散する"

# 元メンバー全員へ送るDM。{guild_name} は解散したギルド名。
DISBAND_DM_TITLE = "ギルド解散"
DISBAND_DM_BODY = "所属していたギルド「{guild_name}」が解散しました。"

# 解散できたときの案内。{guild_name} はギルド名、{archive_days} は保存日数。
DISBAND_DONE = """ギルド「{guild_name}」を解散しました。
専用カテゴリーは{archive_days}日間保存されます。"""

# 解散はできたが、カテゴリーのアーカイブに失敗したとき
DISBAND_DONE_ARCHIVE_FAILED = """ギルド「{guild_name}」を解散しました。
専用カテゴリーは{archive_days}日間保存されます。
※カテゴリーのアーカイブに失敗しました。運営へご連絡ください。"""


# ==================================================
# メンバー用パネル（ギルド情報チャンネル・8.3節）
# ==================================================
# パネルの説明文。表題はギルド名になります。
MEMBER_PANEL_BODY = """​
**ギルド情報**
-# ギルド名・説明・メンバー・戦績を確認できます。

**ギルド脱退**
-# 確認のうえギルドを脱退します。
-# ギルドマスターは脱退できません。"""

MEMBER_BUTTON_INFO = "ギルド情報"
MEMBER_BUTTON_LEAVE = "ギルド脱退"


# ==================================================
# ギルド情報（8.3節）
# ==================================================
# Embedへ並べる項目名
INFO_LABEL_DESCRIPTION = "ギルド説明"
INFO_LABEL_MASTER = "ギルドマスター"
INFO_LABEL_MEMBERS = "メンバー"
INFO_LABEL_RECORD = "戦績"
INFO_LABEL_RECRUITMENT = "募集状態"
INFO_LABEL_MEMBER_LIST = "メンバー一覧"

# 人数の書き方。{count} は現在の人数、{capacity} は定員。
INFO_MEMBER_COUNT = "{count} / {capacity}"

# 戦績の書き方。{wins} は勝ち数、{losses} は負け数、{draws} は引き分け数。
INFO_RECORD = "{wins}勝 {losses}敗 {draws}分"

# メンバー一覧の1行。{badge} は下の印、{name} は表示名。
INFO_MEMBER_LINE = "{badge} {name}"
INFO_MASTER_BADGE = "👑"
INFO_MEMBER_BADGE = "・"

# メンバーが1人も読み取れなかったとき
INFO_MEMBER_LIST_EMPTY = "—"


# ==================================================
# ギルド脱退（7.1節）
# ==================================================
# 脱退前の確認。{guild_name} はギルド名。
LEAVE_CONFIRM = "ギルド「{guild_name}」から脱退します。よろしいですか？"

LEAVE_BUTTON_CONFIRM = "脱退する"

# 脱退できたときの案内。{guild_name} はギルド名。
LEAVE_DONE = "ギルド「{guild_name}」から脱退しました。"


# ==================================================
# 脱退・解散・追放ができない理由（7.1節・7.2節・7.5節）
# ==================================================
BATTLE_IN_PROGRESS = "ギルドバトルが進行中のため実行できません。"
ROSTER_LOCKED = "バトルの編成がロックされているため実行できません。"

LEAVE_BLOCKED_REQUEST = (
    "バトル申請中のため脱退できません。申請の取消後に実行してください。"
)
LEAVE_BLOCKED_RECRUITMENT = (
    "バトル募集中のため脱退できません。募集の取消後に実行してください。"
)
LEAVE_BLOCKED_PREPARING = "ギルドバトルの準備中のため脱退できません。"


# ==================================================
# 確認画面（追放・譲渡・拡張・脱退・解散で使い回す）
# ==================================================
# 「実行する」ボタンの名前。譲渡・解散などでは上の専用の名前に置き換わります。
CONFIRM_BUTTON_DEFAULT = "実行する"
CONFIRM_BUTTON_CANCEL = "キャンセル"

# 「キャンセル」を押したとき
CONFIRM_CANCELLED = "操作をキャンセルしました。"


# ==================================================
# うまくいかなかったときの案内
# ==================================================
# ギルド専用チャンネル以外でボタンを押したとき
NOT_GUILD_CHANNEL = "このチャンネルはギルド専用チャンネルとして登録されていません。"

# 解散済みのギルドのパネルを操作したとき
ARCHIVED = "このギルドは既に解散しています。"

# そのギルドのメンバーでない人が、メンバー用パネルを操作したとき
NOT_A_MEMBER = "このギルドに所属していません。"
