"""coinとATMの文言（残高照会・送金・残高変更・月次報酬・負債）。

編集のしかたは ``texts/__init__.py`` を読んでください。
``{name}`` のような波かっこは、Botが値を入れ替える場所です。
金額は「12,300」のように3桁区切りで入ります（「coin」の文字はこちらに書きます）。
"""

# ==================================================
# ATMパネル（coinを使うチャンネルの一番上）
# ==================================================
PANEL_BUTTON_TRANSFER = "送金"
PANEL_BUTTON_BALANCE = "残高照会"

# 残高照会。{balance} には現在の残高が入ります。
BALANCE_TITLE = "残高照会"
BALANCE_BODY = "現在の残高は **{balance} coin**"


# ==================================================
# 送金
# ==================================================
# 送金ボタンを押した直後の案内と、送る相手を選ぶメニュー
TRANSFER_SELECT_PROMPT = "送金先を選択してください。"
TRANSFER_SELECT_PLACEHOLDER = "送金先を選択してください"

# 金額とコメントの入力画面。{name} には送る相手の表示名が入ります。
TRANSFER_MODAL_TITLE = "{name} へ送金"
TRANSFER_MODAL_AMOUNT_LABEL = "送金額"
TRANSFER_MODAL_AMOUNT_PLACEHOLDER = "1000以上の数字を入力"
TRANSFER_MODAL_NOTE_LABEL = "コメント"
TRANSFER_MODAL_NOTE_PLACEHOLDER = "任意"

# 送金できたとき、送った人へ出るお知らせ。
# {user} は相手のリンク、{amount} は送った金額。
TRANSFER_DONE_TITLE = "送金通知"
TRANSFER_DONE_BODY = "{user} へ **{amount} coin** 送金"

# 送金できたとき、受け取った人へ届くDM。
# {name} は送った人の表示名、{amount} は金額、{note} はコメント（無いときは「なし」）。
TRANSFER_DM_TITLE = "RAGNA Bank"
TRANSFER_DM_BODY = """**{name}** から **{amount} coin** 送金されました。
備考：{note}"""

# 送金ログ。{sender} は送った人、{target} は受け取った人、{amount} は金額。
TRANSFER_LOG_TITLE = "送金ログ"
TRANSFER_LOG_BODY = """送金：{sender}
対象：{target}
金額：{amount} coin"""

# コメントが入力されていたときだけ、送金ログの最後に足す行
TRANSFER_LOG_NOTE = "備考：{note}"

# コメントが入力されていないとき
NOTE_EMPTY = "なし"

# うまくいかなかったとき
TRANSFER_TO_SELF = "自分には送金できません。"
TRANSFER_TO_BOT = "Botには送金できません。"
TRANSFER_MINIMUM = "1000coin以上から送金できます。"
TRANSFER_NOT_ENOUGH = "残高不足です。"
TRANSFER_AMOUNT_NOT_NUMBER = "送金額は数字で入力してください。"
TRANSFER_TARGET_NOT_FOUND = "送金先のユーザーを取得できませんでした。"
TRANSFER_UNAVAILABLE = "coin管理機能を取得できませんでした。"


# ==================================================
# 残高変更（運営コマンド）
# ==================================================
# 増やしたか減らしたかの言い方。結果メッセージと残高変更ログに入ります。
ACTION_ADD = "増加"
ACTION_SUBTRACT = "減少"

# ユーザーを指定したとき。{user} は対象者のリンク、{amount} は金額、
# {action} は上の「増加」か「減少」。
CHANGE_TITLE = "残高変更"
CHANGE_DONE = """{user} の残高を
**{amount} coin {action}**しました。"""

# ロールを指定したとき。{role} はロール名、{count} は変更した人数。
CHANGE_DONE_ROLE = "{role}（{count}人）の残高を **{amount} coin {action}**しました。"

# 指定のしかたが正しくなかったとき
CHANGE_TARGET_REQUIRED = "ユーザーまたはロールを指定してください。"
CHANGE_TARGET_DUPLICATED = "ユーザーとロールは同時に指定できません。"

# 残高変更ログ。{actor} は実行した人、{target} は対象（ユーザーまたはロール）、
# {amount} は金額、{action} は「増加」「減少」「支給」「徴収」のいずれか。
CHANGE_LOG_TITLE = "残高変更通知"
CHANGE_LOG_BODY = """実行者：{actor}
対象：{target}
結果：**{amount} coin {action}**"""


# ==================================================
# 月次報酬（毎月ロールごとに配るcoin）
# ==================================================
# 支給・徴収の言い方
MONTHLY_ACTION_GRANT = "支給"
MONTHLY_ACTION_COLLECT = "徴収"

# 受け取った人へ届くDM。{role} はロール名、{amount} は金額、
# {action} は上の「支給」か「徴収」、{balance} は処理後の残高。
MONTHLY_DM_TITLE = "RAGNA Bank"
MONTHLY_DM_BODY = """月次報酬（{role}）

{amount} coin を{action}しました。
現在の残高：**{balance} coin**"""


# ==================================================
# 負債（残高がマイナスになったとき）
# ==================================================
# 残高がマイナスになった人へ届くDM
DEBT_TITLE = "負債通知"
DEBT_DM_BODY = """残高（coin）がマイナスになりました。
『魔物』となり、利用制限がかかります。"""

# 負債ログ。{user} は対象者のリンク、{balance} は残高、
# {removed_roles} は取り上げたロール名（無いときは下の「なし」）。
DEBT_LOG_BODY = """対象：{user}
残高：**{balance} coin**
削除ロール：{removed_roles}"""

# 取り上げたロールが1つも無かったとき
REMOVED_ROLES_EMPTY = "なし"

# 残高が0以上に戻った人へ届くDM
DEBT_CLEARED_TITLE = "返済通知"
DEBT_CLEARED_DM_BODY = "返済が完了したため『小人』となります。"

# 負債解消ログ。{user} は対象者のリンク、{balance} は残高。
DEBT_CLEARED_LOG_BODY = """対象：{user}
残高：**{balance} coin**"""
