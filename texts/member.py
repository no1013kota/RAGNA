"""メンバーまわりの文言。

メンバー登録・招待pt・仮メンバー評価・クラスチェンジ・ランキング・XP・自己紹介を
1つにまとめています。見出し（``====`` の行）を目印に探してください。

編集のしかたは ``texts/__init__.py`` を読んでください。
``{name}`` のような波かっこは、Botが値を入れ替える場所です。
"""

# ==================================================
# 参加・脱退ログ（サーバー参加ログ／サーバー脱退ログ）
# ==================================================
# {user} には参加した人のリンクが入ります。
JOIN_LOG_BODY = "{user} **が参加しました**"

# {user} は退出した人のリンク、{user_id} はその人のID。
LEAVE_LOG_BODY = """{user} **が退出しました**
{user_id}"""

# ログEmbedの下に小さく出る名前。{display_name} は表示名、{name} はユーザー名。
FOOTER_NAME = "{display_name} / @{name}"


# ==================================================
# メンバー確認（運営コマンド）
# ==================================================
# 先頭の2行。{balance} は残高、{invite_points} は招待pt。
# この下に、すぐ下の「XPと通話時間」が続きます。
MEMBER_CHECK_HEADER = """【残高】{balance} Coin
【招待】{invite_points} pt
"""


# ==================================================
# XP（XP確認コマンド・メンバー確認で共通）
# ==================================================
# {monthly_xp}・{total_xp} はXP、{monthly_time}・{total_time} は「3時間20分」の形。
XP_BODY = """【XP】
今月：{monthly_xp} XP
累計：{total_xp} XP

【通話時間】
今月：{monthly_time}
累計：{total_time}"""


# ==================================================
# ユーザーBAN（運営コマンド）
# ==================================================
# BANできたときの返事とBANログ。
# {user} は対象者のリンク、{user_id} はそのID、{reason} は入力した理由。
BAN_DONE_TITLE = "BAN完了"
BAN_DONE_BODY = """対象者：{user}
ユーザーID：{user_id}
理由：{reason}"""

# BANログ。{actor} は実行した人のリンク。
BAN_LOG_TITLE = "ユーザーBAN"
BAN_LOG_BODY = """実行者：{actor}
対象者：{user}
理由：{reason}"""

# 入力が正しくなかったとき
BAN_ID_NOT_NUMBER = "ユーザーIDは数字で入力してください。"
BAN_SELF = "自分自身をBANすることはできません。"
BAN_BOT = "Bot自身をBANすることはできません。"
BAN_REASON_REQUIRED = "BAN理由を入力してください。"

# うまくいかなかったとき
BAN_USER_NOT_FOUND = "指定されたユーザーが見つかりません。"
BAN_USER_FETCH_FAILED = "ユーザー情報の取得に失敗しました。"
BAN_CHECK_FAILED = "BAN状態の確認に失敗しました。"
BAN_ALREADY = "指定されたユーザーはすでにBANされています。"
BAN_FAILED = "ユーザーのBANに失敗しました。"


# ==================================================
# 招待ptパネル（招待ptチャンネル）
# ==================================================
PANEL_INVITE_BODY = """​
**ポイント確認**
-# 自分の招待ptを確認できます

**ポイント使用**
-# 招待ptを好きな特典に交換できます"""

PANEL_BUTTON_POINT_CHECK = "pt確認"
PANEL_BUTTON_POINT_USE = "pt使用"

# pt確認。{points} には持っている招待ptが入ります。
INVITE_CHECK_TITLE = "招待ポイント確認"
INVITE_CHECK_BODY = "あなたは **{points} pt** です。"

# pt使用を押したときの案内
INVITE_USE_PROMPT = "現在の招待ポイント：**{points} pt**"

# 使えるptが無いとき・特典の対象ロールでないとき
INVITE_NO_POINTS = "使用できる招待ポイントがありません。"
INVITE_ROLE_NOT_ELIGIBLE = "現在のロールでは招待ポイントを使用できません。"


# ==================================================
# 招待ptの特典選択
# ==================================================
BENEFIT_SELECT_PLACEHOLDER = "交換する特典を選択してください"

# 精霊（仮メンバー）向け。{days} は1ptあたりの延長日数。
BENEFIT_TRIAL_EXTENSION = "精霊期間を延長"
BENEFIT_TRIAL_EXTENSION_DESCRIPTION = "1ptにつき精霊期間を{days}日延長"

# どのロールでも選べるcoin交換。{coin} は1ptあたりのcoin。
BENEFIT_COIN = "coinへ交換"
BENEFIT_COIN_DESCRIPTION = "1ptにつき{coin} coin"

# 七聖・騎士向け。{rate} は1ptあたりに増える無料確率。
BENEFIT_HOTEL_RATE = "宿屋の無料確率を上げる"
BENEFIT_HOTEL_RATE_DESCRIPTION = "1ptにつき無料確率を{rate}% 増加"

# 小人向け。{cost} は交換に必要なpt。
BENEFIT_TICKET = "精霊チケット"
BENEFIT_TICKET_DESCRIPTION = "{cost}ptで確認待ちロールを取得"


# ==================================================
# 招待ptの使用ポイント数選択
# ==================================================
AMOUNT_SELECT_PROMPT = "使用するポイント数を選択してください。"
AMOUNT_SELECT_PLACEHOLDER = "使用するポイント数を選択してください"

# 選択肢。{points} には数字が入ります。
AMOUNT_OPTION = "{points}pt使用"

# 25pt以上を使いたいときの選択肢。{max_points} は持っているpt。
AMOUNT_OPTION_CUSTOM = "25pt以上を指定"
AMOUNT_OPTION_CUSTOM_DESCRIPTION = "25～{max_points}ptの範囲で入力"

# 25pt以上を入力する画面
AMOUNT_MODAL_TITLE = "使用ポイント数"
AMOUNT_MODAL_LABEL = "使用するポイント数"
AMOUNT_MODAL_PLACEHOLDER = "25以上の数字を入力してください"

# 入力が正しくなかったとき
AMOUNT_NOT_NUMBER = "ポイント数は数字で入力してください。"
AMOUNT_TOO_SMALL = "25pt以上を入力してください。"
AMOUNT_MUST_BE_POSITIVE = "使用ポイントは1以上で指定してください。"

# 持っているptより多く使おうとしたとき。{points} は今のpt。
AMOUNT_OVER_OWNED = """所持ポイントを超えています。
現在：**{points}pt**"""

# 他の人のメニュー・入力画面を操作しようとしたとき
NOT_YOUR_MENU = "このメニューは操作できません。"
NOT_YOUR_MODAL = "この入力画面は操作できません。"


# ==================================================
# 招待ptの特典を受け取ったとき
# ==================================================
# 交換できたときの返事。{benefit} は特典名、{points} は使ったpt、
# {result} は下の「結果」の文、{remaining} は残りのpt。
INVITE_USE_TITLE = "招待ポイント使用"
INVITE_USE_BODY = """特典：**{benefit}**
使用：**{points}pt**
結果：{result}
残り：**{remaining}pt**"""

# 招待pt使用ログ。{user} は使った人のリンク。
INVITE_USE_LOG_BODY = """対象者：{user}
特典：**{benefit}**
使用：**{points} pt**
残り：**{remaining} pt**
結果：{result}"""

# 特典名（ログと結果表示に出ます）
BENEFIT_NAME_TRIAL_EXTENSION = "精霊期間延長"
BENEFIT_NAME_HOTEL_RATE = "宿屋無料確率"
BENEFIT_NAME_COIN = "coin交換"
BENEFIT_NAME_TICKET = "精霊チケット"

# 結果の文。{days} は延長した日数、{old_rate}→{new_rate} は無料確率、{coin} はcoin。
RESULT_TRIAL_EXTENSION = "精霊期間を{days}日延長"
RESULT_HOTEL_RATE = "{old_rate}% → {new_rate}%"
RESULT_COIN = "{coin} coin付与"
RESULT_TICKET = "確認待ちロールを付与"

# 精霊チケットを交換できたとき。{cost} は使ったpt。
TICKET_DONE_TITLE = "精霊チケット交換"
TICKET_DONE_BODY = """精霊チケットを交換しました。
**{cost}pt** 使用
"""

# 精霊チケットが交換できないとき
TICKET_ROLE_REQUIRED = "小人のみ精霊チケットを交換できます。"
TICKET_WAITING_ROLE_NOT_FOUND = "確認待ちロールが見つかりません。"
TICKET_ALREADY_OWNED = "すでに精霊チケットを所持しています。"
TICKET_NOT_ENOUGH_POINTS = "精霊チケットの交換には **{cost}pt** 必要です。"
TICKET_ROLE_FAILED = "確認待ちロールの付与に失敗しました。"

# 宿屋無料確率の特典が使えないとき。{rate} は今の確率、{max_rate} は上限、
# {max_points} はあと何pt使えるか。
HOTEL_RATE_MAX = "宿屋無料確率はすでに **{rate}%** です。"
HOTEL_RATE_OVER = """宿の無料確率が上限を超えます。
現在：**{rate}%**
上限：**{max_rate}%**
使用可能：最大 **{max_points}pt**"""

# 特典の対象ロールでなくなっていたとき
NOT_TRIAL_MEMBER = "現在、精霊ではありません。"
NOT_MEMBER = "現在、騎士または七聖ではありません。"
NOT_ASSOCIATE_MEMBER = "現在、小人ではありません。"
NOT_DEMOTED = "現在、魔物ではありません。"

# そのほかうまくいかなかったとき
INVITE_NOT_ENOUGH_POINTS = "招待ポイントが不足しています。"
INVITE_NOT_ENOUGH_POINTS_DETAIL = """招待ポイントが不足しています。
現在：**{points}pt**"""
TRIAL_MEMBER_NOT_FOUND_FOR_EXTENSION = "精霊情報が見つからないため延長できませんでした。"
BENEFIT_UNKNOWN = "選択した特典を確認できませんでした。"


# ==================================================
# 招待報酬（運営コマンド）
# ==================================================
# 付与できたときの返事。{user} は対象者のリンク、{points} は付与pt、{total} は累計pt。
INVITE_REWARD_TITLE = "招待報酬"
INVITE_REWARD_BODY = """{user} へ **{points}pt** 付与しました。
現在：**{total}pt**"""

# 招待報酬ログ。{actor} は実行した人のリンク。
INVITE_REWARD_LOG_TITLE = "招待報酬付与"
INVITE_REWARD_LOG_BODY = """実行者：{actor}
対象者：{user}
付与：**{points}pt**
累計：**{total}pt**"""

# 理由が入力されていたときだけ、ログの最後に足す行
INVITE_REWARD_LOG_REASON = "理由：{reason}"

# Botを指定したとき
INVITE_REWARD_BOT = "Botには招待ポイントを付与できません。"


# ==================================================
# 仮メンバー評価：召喚（運営コマンド）
# ==================================================
# 召喚できたときの返事。{user} は対象者のリンク、{class_name} はクラス、
# {start_type} は下の「召喚」か「再召喚」、{end_date} は転生予定日、
# {coin_text} は下の初期所持金の案内。
SUMMON_DONE = """{user} を **{class_name}クラス**へ{start_type}させました。
転生予定：{end_date}{coin_text}"""

SUMMON_TYPE_FIRST = "召喚"
SUMMON_TYPE_AGAIN = "再召喚"

# 初期所持金の案内（上の {coin_text} に入ります）。行の先頭で改行しています。
SUMMON_COIN_GRANTED = """
初期所持金：{coin} coin"""
SUMMON_COIN_SKIPPED = """
初期所持金の再付与はありません。"""

# 召喚できないとき
SUMMON_BOT = "Botは選択できません。"
SUMMON_DEMOTED = "魔物は返済するまで精霊になれません。"
SUMMON_ALREADY = "対象者はすでに精霊として登録されています。"
SUMMON_CLASS_INVALID = "クラスは A・B・C のいずれかで入力してください。"
SUMMON_THREAD_FAILED = "評価スレッドの作成に失敗しました。"
SUMMON_FORUM_NOT_FOUND = "評価フォーラムが見つかりません。"
SUMMON_ROLE_FAILED = "ロールの変更に失敗しました。"


# ==================================================
# 仮メンバー評価：評価スレッド
# ==================================================
# 評価フォーラムに作られるスレッドの1つ目の投稿。
# {user} は精霊のリンク、{start_date} は召喚日、{intro} は自己紹介へのリンク
# （無いときは texts/common.py の「未登録」）。
# {end_date_line} には、すぐ下の「転生予定」の行がそのまま入ります。
THREAD_BODY = """{user}

【召喚日】{start_date}
{end_date_line}
{intro}"""

# 転生予定の行。{end_date} には転生予定日が入ります。
# 招待ptで期間を延ばしたときは、この行だけを探して書き換えます。
# 探すときは「{end_date} より前の部分」を目印にするので、日付は必ず行の最後に
# 置いてください。
THREAD_END_DATE_LINE = "【転生予定】{end_date}"

# 評価スレッドを保存するときのスレッド名
TRIAL_ARCHIVE_THREAD_NAME = "{display_name}/{name}"

# 評価スレッドをコピーするときの、1件ごとの見出し。
# {author} は書いた人の表示名、{content} は本文。
EVALUATION_COPY_MESSAGE = """【{author}】
{content}"""

# 長すぎる投稿を切り詰めたときに、末尾へ付ける印
EVALUATION_COPY_TRUNCATED = """
...(省略)"""


# ==================================================
# 仮メンバー評価：評価パネル（評価パネルチャンネル）
# ==================================================
PANEL_EVALUATION_BODY = """​
**評価**
-# 指定した精霊の評価を登録します

**追記**
-# 登録済みのスレッドに評価を追記します

**評価チェック**
-# 評価可能な精霊をXP順に表示します"""

PANEL_BUTTON_EVALUATE = "評価"
PANEL_BUTTON_COMMENT = "追記"
PANEL_BUTTON_EVALUATION_CHECK = "評価チェック"

# ボタンを押したあとの案内
EVALUATE_SELECT_PROMPT = "評価する精霊を選択してください。"
COMMENT_SELECT_PROMPT = "追記する精霊を選択してください。"
EVALUATE_SELECT_PLACEHOLDER = "評価する精霊を選択"
COMMENT_SELECT_PLACEHOLDER = "追記する精霊を選択"

# 精霊以外を選んだとき
SELECT_TRIAL_MEMBER = "精霊を選択してください。"


# ==================================================
# 仮メンバー評価：評価チェック
# ==================================================
EVALUATION_CHECK_TITLE = "評価チェック"

# 1人ぶんの行。{class_name} はクラス、{user} は精霊のリンク、{xp} はXP、
# {status} は下の「評価済 ✅」か「未評価」。
EVALUATION_CHECK_LINE = "{class_name} {user} {xp}XP {status}"
EVALUATION_CHECK_DONE = "評価済 ✅"
EVALUATION_CHECK_NOT_YET = "未評価"

# 評価できる精霊が1人もいないとき
EVALUATION_CHECK_EMPTY = "現在評価できる精霊はいません。"

# 一覧の下に出るページ番号
PAGE_FOOTER = "{page} / {total}ページ"

# ページ送りのボタン
PAGE_PREVIOUS = "◀ 前"
PAGE_NEXT = "次 ▶"


# ==================================================
# 仮メンバー評価：評価・追記の入力画面
# ==================================================
# {name} には精霊の表示名が入ります。
EVALUATION_MODAL_TITLE = "{name} の評価"
COMMENT_MODAL_TITLE = "{name} への追記"

# 入力欄の名前（評価・追記で共通）
INPUT_VOICE = "声/音質"
INPUT_TALK = "トーク力"
INPUT_CHARM = "個性/魅力"
INPUT_OVERALL = "総合評価"
INPUT_NOTE = "コメント"

# 評価のときの入力例
EVALUATION_PLACEHOLDER_SCORE = "1～10"
EVALUATION_PLACEHOLDER_OVERALL = "1～5"
INPUT_NOTE_PLACEHOLDER = "任意"

# 追記のときの入力例（入れた項目だけが書き換わります）
COMMENT_PLACEHOLDER_SCORE = "変更する場合のみ1～10で入力"
COMMENT_PLACEHOLDER_OVERALL = "変更する場合のみ1～5で入力"

# 数字以外を入力したとき
INPUT_NOT_NUMBER = "数字で入力してください。"


# ==================================================
# 仮メンバー評価：評価スレッドへの書き込み
# ==================================================
# 評価スレッドと評価員の評価シートへ、同じ形で並べます。
# {score} には点数、{note} にはコメントが入ります。
EVALUATION_LINE_VOICE = "【声/音質】{score}"
EVALUATION_LINE_TALK = "【トーク力】{score}"
EVALUATION_LINE_CHARM = "【個性/魅力】{score}"
EVALUATION_LINE_OVERALL = "【総合評価】{score}"
EVALUATION_LINE_NOTE = "【コメント】{note}"

# コメントが入力されていないとき
NOTE_EMPTY = "なし"

# 評価員の評価シートの見出し
EVALUATION_LOG_TITLE = "評価"
COMMENT_LOG_TITLE = "追記"

# 評価員ごとに作られる評価シートのチャンネル名
EVALUATOR_SHEET_CHANNEL_NAME = "評価シート-{name}"

# 登録できたときの返事。{channel} には評価シートのリンクが入ります。
EVALUATION_DONE = "評価を登録しました。"
COMMENT_DONE = "追記を登録しました。"
EVALUATION_SHEET_LINK = """
{channel} で内容を確認できます。"""

# 評価・追記ができないとき
EVALUATION_NO_PERMISSION = "評価権限がありません。"
COMMENT_NO_PERMISSION = "追記権限がありません。"
COMMENT_EMPTY = "追記内容を1つ以上入力してください。"
SCORE_OUT_OF_RANGE = "条件に沿って入力してください。"
OVERALL_OUT_OF_RANGE = "総合評価は1～5で入力してください。"
NOT_REGISTERED = "対象者は精霊として登録されていません。"
EVALUATION_CLASS_NOT_ALLOWED = "この精霊は評価できません。"
COMMENT_CLASS_NOT_ALLOWED = "この精霊には追記できません。"
THREAD_NOT_FOUND = "評価スレッドが見つかりません。"
THREAD_DELETED = "評価スレッドが削除されています。"
THREAD_FETCH_FAILED = "評価スレッドの取得に失敗しました。"
THREAD_SEND_FAILED = "評価スレッドへの送信に失敗しました。"
TRIAL_MEMBER_ROLE_NOT_FOUND = "精霊ロールが見つかりません。"
COG_NOT_FOUND = "管理機能を取得失敗。"
GUILD_NOT_FOUND_SHORT = "サーバー情報を取得失敗。"


# ==================================================
# 仮メンバー評価：評価シートの保存（評価員が脱退したとき）
# ==================================================
EVALUATOR_ARCHIVE_TITLE = "評価シート保存"

# {user} は評価員のリンク、{roles} は持っていたロール（無いときは下の「なし」）。
EVALUATOR_ARCHIVE_BODY = """対象：{user}
ロール：{roles}"""

EVALUATOR_ARCHIVE_NO_ROLE = "なし"

# 保存先スレッドの名前
EVALUATOR_ARCHIVE_THREAD_NAME = "{display_name} ({name})"


# ==================================================
# 仮メンバー評価：転生（仮メンバー期間の終了）
# ==================================================
# 転生の理由（転生ログの「理由：」に出ます）
TRIAL_END_REASON_AUTO = "自動転生"
TRIAL_END_REASON_LEAVE = "サーバー脱退"
TRIAL_END_REASON_MANUAL = "転生"

# 転生ログ。{user} は対象者のリンク、{reason} は上の理由。
TRIAL_END_LOG_TITLE = "転生"
TRIAL_END_LOG_BODY = """対象者：{user}
理由：{reason}"""

# 運営が手動で転生させたときの返事
TRIAL_END_DONE = "{user} をロール変更させました。"

# coinがマイナスになって評価落ちへ移るとき、保存スレッドの先頭に置く説明。
# {user} は対象者のリンク、{class_name} はそのときのクラス。
DEMOTED_ARCHIVE_TITLE = "評価落ち移行"
DEMOTED_ARCHIVE_BODY = """対象者：{user}
クラス：**{class_name}クラス**"""


# ==================================================
# 仮メンバー評価：転生後アンケート（DM）
# ==================================================
SURVEY_TITLE = "アンケート"
SURVEY_BODY = """今後の運営改善のため、
簡単なアンケートにご協力ください。

※回答期限：3日
※回答結果は運営が保管しています"""

SURVEY_BUTTON_GOOD_EVALUATOR = "👍 一番印象の良い天使"
SURVEY_BUTTON_SKIP = "⏭ スキップ"

# 天使を選ぶ画面
SURVEY_SELECT_PROMPT = "一番印象の良い天使を選択してください。"
SURVEY_SELECT_PLACEHOLDER = "天使を選択"

# 理由の入力画面
SURVEY_MODAL_TITLE = "天使アンケート"
SURVEY_MODAL_LABEL = "理由（任意）"

# 回答が終わったとき
SURVEY_THANKS = "回答ありがとうございました。"
SURVEY_SKIPPED = "アンケートをスキップしました。"

# 回答できないとき
SURVEY_FINISHED = "このアンケートは終了しています。"
SURVEY_EVALUATOR_ROLE_NOT_FOUND = "天使ロールが見つかりません。"
SURVEY_NO_EVALUATOR = "現在天使が登録されていません。"

# アンケートログ。{user} は回答した人、{evaluator} は選ばれた天使、
# {comment} は理由（未入力のときは「なし」）。
SURVEY_LOG_TITLE = "アンケート回答"
SURVEY_LOG_BODY = """回答者：{user}
対象：{evaluator}
理由：{comment}"""

# 選ばれた天使がサーバーから居なくなっているときの表示
SURVEY_LOG_EVALUATOR_ID = "ID: {evaluator_id}"


# ==================================================
# クラスチェンジ：クラス変更候補（評価状況コマンド）
# ==================================================
# 表題。{class_name} は絞り込んだクラス。
CANDIDATE_TITLE_ALL = "クラス変更候補｜全体"
CANDIDATE_TITLE_CLASS = "クラス変更候補｜{class_name}クラス"

# 上位・下位の一覧。{top} と {worst} に下の行が並びます。
CANDIDATE_BODY = """**■ 総合評価 TOP5**
{top}

**■ 総合評価 ワースト3**
{worst}"""

# 1人ぶんの行。{rank} は順位、{score} は平均点、{count} は評価した人数、
# {user} は精霊のリンク。※全角スペースで区切っています。
CANDIDATE_LINE = "**{rank}位**　{score} / 5　評価{count}人　{user}"

# 対象がいないとき
CANDIDATE_NOT_ENOUGH = "2人以上から評価を受けている精霊がいません。"
CANDIDATE_EMPTY = "対象者が見つかりません。"


# ==================================================
# クラスチェンジ：クラス分布（コマンド）
# ==================================================
# {total} は全体の人数、{s}〜{c} は各クラスの人数、{s_percent} などは割合。
# 【A層】はA+とA、【B層】はB+とBの合計です。
DISTRIBUTION_TITLE = "クラス分布"
DISTRIBUTION_BODY = """【全体】{total}人

【S】{s}人（{s_percent}%）
【A+】{a_plus}人（{a_plus_percent}%）
【A】{a}人（{a_percent}%）
【B+】{b_plus}人（{b_plus_percent}%）
【B】{b}人（{b_percent}%）
【C】{c}人（{c_percent}%）

【A層】{a_group}人（{a_group_percent}%）
【B層】{b_group}人（{b_group_percent}%）"""

DISTRIBUTION_EMPTY = "クラスロールを持っているメンバーが見つかりません。"


# ==================================================
# クラスチェンジ：クラス変更（コマンド）
# ==================================================
# 変更できたときの返事。{user} は対象者のリンク、
# {old_class} は変更前、{new_class} は変更後のクラス。
CLASS_CHANGE_DONE = "{user} のクラスを **{old_class} → {new_class}** へ変更しました。"

# クラス変更ログ。{actor} は実行した人、{note} は備考。
CLASS_CHANGE_LOG_TITLE = "クラス変更"
CLASS_CHANGE_LOG_BODY = """実行者：{actor}
対象者：{user}
変更：**{old_class} → {new_class}**
備考：{note}"""

# 備考が入力されていなかったとき
CLASS_CHANGE_NOTE_DEFAULT = "定期クラス替え"

# 変更できないとき
CLASS_CHANGE_SAME = "対象者はすでに **{class_name}クラス** です。"
CLASS_ROLE_NOT_FOUND = "{class_name}クラスロールが見つかりません。"
CLASS_CHANGE_COG_NOT_FOUND = "管理機能を取得できませんでした。"
CLASS_CHANGE_NEW_THREAD_FAILED = "新しい評価スレッドの作成に失敗しました。"
CLASS_CHANGE_NEW_THREAD_NOT_FOUND = "新しい評価スレッドを取得できませんでした。"
CLASS_CHANGE_THREAD_MOVE_FAILED = "評価スレッドの移動に失敗しました。"
CLASS_CHANGE_ROLE_FAILED = "クラスロールの変更に失敗しました。"


# ==================================================
# ランキング（コマンド）
# ==================================================
# 表題。ロールを指定したときは、下の RANKING_TITLE_WITH_ROLE で
# 「（ロール名） （表題）」の形にします。
RANKING_TITLE_COIN = "coinランキング"
RANKING_TITLE_INVITE = "招待ptランキング"
RANKING_TITLE_VC_MONTHLY = "通話時間ランキング（今月）"
RANKING_TITLE_VC_TOTAL = "通話時間ランキング（累計）"
RANKING_TITLE_XP_MONTHLY = "XPランキング（今月）"
RANKING_TITLE_XP_TOTAL = "XPランキング（累計）"
RANKING_TITLE_SURVEY = "アンケートランキング"

RANKING_TITLE_WITH_ROLE = "{role} {title}"

# 50人を超えて2枚目以降になったときの表題
RANKING_TITLE_CONTINUED = "{title}（続き）"

# 1人ぶんの行。{rank} は順位、{value} は「1,234 coin」などの値、{user} はリンク。
RANKING_LINE = "{rank}位 {value} {user}"

# 値の書き方。{value} は3桁区切りの数、{unit} は下の単位。
RANKING_VALUE = "{value} {unit}"

# 単位。※ここはランキングの種類を見分けるためにも使っているので、
#   同じ単位を2つの種類に付けないでください。
RANKING_UNIT_COIN = "coin"
RANKING_UNIT_POINT = "pt"
RANKING_UNIT_XP = "XP"
RANKING_UNIT_VOTE = "票"

# 該当者がいないとき
RANKING_EMPTY = "対象者が見つかりません。"
RANKING_NO_EVALUATOR = "天使が見つかりません。"


# ==================================================
# 自己紹介（自己紹介チャンネルの入力テンプレート）
# ==================================================
# 自己紹介チャンネルへ自動で貼り直される雛形です。
# 書き換えると、次の投稿から新しい雛形に入れ替わります。
INTRODUCTION_TEMPLATE = """【名前】
【年齢】
【性格】
【声質】
【好き】
【嫌い】
【アピール】
【紹介者】"""
