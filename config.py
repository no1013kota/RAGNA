"""環境変数とDiscordサーバー固有IDをまとめた設定ファイル。

秘密情報はこのファイルへ直接書かず、Railway Variablesまたはローカルの
``.env`` から読み込みます。
"""

import os

from dotenv import load_dotenv


load_dotenv()


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(
            f"{name} が設定されていません。"
            "RailwayのVariablesまたはローカルの.envに設定してください。"
        )
    return value


TOKEN = _required_env("DISCORD_BOT_TOKEN")

try:
    GUILD_ID = int(_required_env("DISCORD_GUILD_ID"))
except ValueError as exc:
    raise RuntimeError("DISCORD_GUILD_ID は数字で設定してください。") from exc

if GUILD_ID <= 0:
    raise RuntimeError("DISCORD_GUILD_ID は正の数字で設定してください。")

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
if LOG_LEVEL not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
    raise RuntimeError(
        "LOG_LEVEL は DEBUG / INFO / WARNING / ERROR / CRITICAL から選んでください。"
    )

# ==================================================
# RAGNA設定
# ==================================================
START_COIN = 30000  # 初期coin
TRIAL_MEMBER_PERIOD_DAYS = 14  # 仮メンバー期間
MAX_HOTEL_FREE_RATE = 99  # 宿屋無料率上限

# ==================================================
# Embedカラー
# ==================================================
COLOR_BLUE = 0xBEDBFF  # 青
COLOR_GREEN = 0xBEFFD7  # 緑
COLOR_RED = 0xFFB7B7  # 赤
COLOR_PURPLE = 0x5C21FF  # 紫
COLOR_YELLOW = 0xFBFFCD  # 黄
COLOR_GOLD = 0xFEE75C  # 金
COLOR_GREY = 0x2B2D31  # 灰
COLOR_WHITE = 0xFFFFF0  # 白

# ==================================================
# ロール
# ==================================================
ROLE_MANAGER = 1510278173592387775  # 運営
ROLE_SUB_MANAGER = 1513148112766504970  # 準運営（七聖）
ROLE_MEMBER = 1513176814879506544  # 本メンバー（騎士）
ROLE_TRIAL_MEMBER = 1513177006429438123  # 仮メンバー（精霊）
ROLE_ASSOCIATE_MEMBER = 1517507111947472926  # 準メンバー（小人）
ROLE_DEMOTED = 1525486923991220427  # 評価落ち（魔物）

ROLE_APPLICANT = 1514247405195755721  # 面接前（観測待ち）
ROLE_INTERVIEWING = 1535564250305789952  # 面接中（観測中）
ROLE_ENROLLMENT_WAITING = 1533053592192417933  # 確認待ち

# 評価員
ROLE_EVALUATION_MANAGER = 1513452348670738482  # 評価管理者（大天使）
ROLE_EVALUATOR = 1513452147130241105  # 評価員（天使）
ROLE_A_PLUS = 1515357612655509574  # A+
ROLE_B_PLUS = 1515357675221811240  # B+

# クラス
ROLE_CLASS_S = 1513178239454150697  # Sクラス
ROLE_CLASS_A = 1513178276716216570  # Aクラス
ROLE_CLASS_B = 1513178306281996328  # Bクラス
ROLE_CLASS_C = 1513178341019222056  # Cクラス

# ==================================================
# 権限グループ
# ==================================================
# 本メンバー以上
ROLE_GROUP_MEMBER = [ROLE_MANAGER, ROLE_MEMBER, ROLE_SUB_MANAGER]

# 運営階級
ROLE_GROUP_MANAGEMENT = [ROLE_MANAGER, ROLE_SUB_MANAGER]

# ==================================================
# コマンド権限
# ==================================================
# クラス管理
PERMISSION_CLASS_MANAGEMENT = [ROLE_MANAGER, ROLE_EVALUATION_MANAGER]

# 招待報酬
PERMISSION_INVITE_REWARD = [ROLE_MANAGER, ROLE_EVALUATION_MANAGER]

# お問い合わせ設定
TICKET_TYPES = {
    "manager": {
        "name": "運営宛",
        "description": "運営への相談・対応が必要な場合",
        "roles": [ROLE_MANAGER],
    },
    "general": {
        "name": "総合窓口",
        "description": "問い合わせ先に迷った場合はこちら",
        "roles": [ROLE_MANAGER, ROLE_SUB_MANAGER],
    },
    "report": {
        "name": "通報",
        "description": "規約違反・迷惑行為などの報告",
        "roles": [ROLE_MANAGER],
    },
    "evaluation": {
        "name": "精霊・世界樹",
        "description": "精霊・評価・世界樹に関する相談",
        "roles": [ROLE_MANAGER, ROLE_EVALUATION_MANAGER],
    },
}

# ==================================================
# チャンネル
# ==================================================
# 仮メンバー自己紹介
CHANNEL_TRIAL_MEMBER_MALE = 1536290258239619092  # 仮メンバー男
CHANNEL_TRIAL_MEMBER_FEMALE = 1510566232808751135  # 仮メンバー女

# 本メンバー自己紹介
CHANNEL_MEMBER_MALE = 1510563687600099398  # 本メンバー男
CHANNEL_MEMBER_FEMALE = 1510563744290177154  # 本メンバー女

INTRODUCTION_CHANNELS = [
    CHANNEL_TRIAL_MEMBER_MALE,
    CHANNEL_TRIAL_MEMBER_FEMALE,
    CHANNEL_MEMBER_MALE,
    CHANNEL_MEMBER_FEMALE,
]

# 評価
CHANNEL_EVALUATION_PANEL = 1530579846797852834  # 評価パネル

FORUM_EVAL_A = 1515333431775727656  # Aクラス評価
FORUM_EVAL_B = 1515336167191609386  # Bクラス評価
FORUM_EVAL_C = 1515336084106776606  # Cクラス評価

FORUM_ARCHIVE_A = 1515336394183020725  # Aクラス評価アーカイブ
FORUM_ARCHIVE_B = 1515336545836732518  # Bクラス評価アーカイブ
FORUM_ARCHIVE_C = 1515336567844245604  # Cクラス評価アーカイブ

FORUM_LEAVE_ARCHIVE = 1528608241553117244  # 脱退アーカイブ
FORUM_EVALUATION_ARCHIVE = 1528662130578952395  # 評価終了アーカイブ
FORUM_DEMOTED_ARCHIVE = 1533441919362007142  # 評価落ちアーカイブ（貧民）

# ログ
CHANNEL_CLASS_CHANGE_LOG = 1515509476763897997  # クラス変更ログ
CHANNEL_TRIAL_MEMBER_END_LOG = 1515608971703095348  # 転生ログ
CHANNEL_SURVEY_LOG = 1523270858909683722  # アンケートログ
CHANNEL_MEMBER_JOIN_LOG = 1513834787062812692  # サーバー参加ログ
CHANNEL_MEMBER_LEAVE_LOG = 1513836678442254336  # サーバー脱退ログ
CHANNEL_BAN_LOG = 1533054969760976946  # BANログ

# カテゴリ
CATEGORY_EVALUATION_LOG = 1513795751149310002  # 職員室
CATEGORY_WORLD_TREE = 1511163946558554214  # 世界樹
CATEGORY_HOTEL = 1513135808121540789  # 宿屋
CATEGORY_TICKET = 1513137583910293605  # お問い合わせ
CATEGORY_TICKET_ARCHIVE = 1514241444255240356  # お問い合わせ保存

# コインログ
CHANNEL_BALANCE_LOG = 1513835696782184468  # 残高変更ログ
CHANNEL_DEBT_LOG = 1513836560951545996  # 負債ログ
CHANNEL_DEBT_CLEAR_LOG = 1525486542968193224  # 負債解消ログ
CHANNEL_TRANSFER_LOG = 1513835656097300610  # 送金ログ

# 宿屋
CHANNEL_HOTEL_PANEL = 1530852789201141821  # 宿屋パネル
CHANNEL_HOTEL_LOG = 1513834907540000920  # 宿屋利用ログ
HOTEL_PERMISSION_LOG_CHANNEL_ID = 1526456390539939971  # 宿屋権限変更ログ

# 招待ポイント
CHANNEL_INVITE_POINT_PANEL = 1533052313621627031  # 招待ptパネル
CHANNEL_INVITE_POINT_USE_LOG = 1533052978808881313  # 招待pt使用ログ
CHANNEL_INVITE_REWARD_LOG = 1515608181487964240  # 招待報酬ログ

INVITE_TRIAL_MEMBER_EXTENSION_DAYS = 2  # 仮メンバー期間延長日数
INVITE_COIN_REWARD = 30000  # coin報酬
INVITE_DEMOTED_COIN_REWARD = 50000  # 評価落ちcoin救済額
INVITE_MEMBER_FREE_RATE = 3  # 本メンバー宿屋無料率増加量
INVITE_TICKET_COST = 5  # 特典チケット必要pt

# お問い合わせ
CHANNEL_TICKET_PANEL = 1523188012429869176  # お問い合わせパネル

# ==================================================
# ATMパネル
# ==================================================
ATM_PANEL_CHANNELS = [
    CHANNEL_EVALUATION_PANEL,
    CHANNEL_HOTEL_PANEL,
    CHANNEL_INVITE_POINT_PANEL,
    CHANNEL_TICKET_PANEL,
]

# ==================================================
# 月間報酬
# ==================================================
MONTHLY_ROLE_REWARDS = {
    ROLE_SUB_MANAGER: 300000,  # 準運営（七聖）
    ROLE_MEMBER: 50000,  # 本メンバー（騎士）
    ROLE_ASSOCIATE_MEMBER: -30000,  # 準メンバー（小人）
    ROLE_EVALUATION_MANAGER: 10000,  # 評価管理者（大天使）
    ROLE_A_PLUS: 50000,  # A+
    ROLE_B_PLUS: 30000,  # B+
}

# ==================================================
# 宿屋設定
# ==================================================
HOTEL_STANDARD_PRICE = 5000  # ツーショ料金
HOTEL_SECRET_PRICE = 20000  # シークレット料金
HOTEL_PREMIUM_PRICE = 100000  # プレミアム料金

HOTEL_DURATION_HOURS = 15  # 宿屋利用時間

HOTEL_DENY_ROLES = [
    ROLE_INTERVIEWING,  # 面接中
    ROLE_APPLICANT,  # 面接前
    ROLE_ASSOCIATE_MEMBER,  # 準メンバー
    ROLE_DEMOTED,  # 評価落ち
    ROLE_ENROLLMENT_WAITING,  # 確認待ち
]

HOTEL_FREE_ROLES = ROLE_GROUP_MEMBER  # 本メンバー以上

HOTEL_PLANS = {
    "ツーショ": {"price": HOTEL_STANDARD_PRICE, "limit": 2, "default_private": False},
    "シークレット": {"price": HOTEL_SECRET_PRICE, "limit": 2, "default_private": True},
    "プレミアム": {"price": HOTEL_PREMIUM_PRICE, "limit": 0, "default_private": True},
}

# ==================================================
# RAGNA Online（ギルド・使い魔・ギルドバトル）
# ==================================================
# 仕様は docs/GAME_SPEC.md を参照。
# 料金・確率・戦闘定数などのゲームバランス値はコードへ書かず、
# data/master/ 配下のマスターデータで管理します（game.master_data が読み込みます）。


def _optional_channel_env(name: str) -> int:
    """未設定を許容するチャンネルID環境変数を読み込む。

    テスト環境と本番環境でIDが異なるため、実在IDはコードへ書かず
    Railway Variablesまたは ``.env`` から読み込みます。未設定の場合は0を返し、
    該当パネルの設置だけを見送ります（Bot全体は起動します）。
    """

    value = os.getenv(name, "").strip()
    if not value:
        return 0

    try:
        channel_id = int(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} は数字で設定してください。") from exc

    if channel_id <= 0:
        raise RuntimeError(f"{name} は正の数字で設定してください。")

    return channel_id


def _flag_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name, "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}


# ゲーム機能全体の公開スイッチ。段階公開のため既定はOFF。
GAME_ENABLED = _flag_env("GAME_ENABLED", False)

# ギルド紹介チャンネル（設立パネル・申請確認）
GUILD_INTRO_CHANNEL_ID = _optional_channel_env("GUILD_INTRO_CHANNEL_ID")

# メンバー募集チャンネル（募集Embedと参加申請）。ギルド紹介とは別のチャンネル
GUILD_MEMBER_RECRUITMENT_CHANNEL_ID = _optional_channel_env(
    "GUILD_MEMBER_RECRUITMENT_CHANNEL_ID"
)

# 使い魔の4パネルを置くチャンネル
FAMILIAR_PANEL_CHANNEL_ID = _optional_channel_env("FAMILIAR_PANEL_CHANNEL_ID")

# 公開バトル募集チャンネル（バトル募集Embed）
GUILD_BATTLE_RECRUITMENT_CHANNEL_ID = _optional_channel_env(
    "GUILD_BATTLE_RECRUITMENT_CHANNEL_ID"
)

# ギルド受付チャンネル（ギルドランキングパネル）
GUILD_RECEPTION_CHANNEL_ID = _optional_channel_env("GUILD_RECEPTION_CHANNEL_ID")

# 運営操作ログの転送先（任意）。未設定でも game_admin_logs テーブルへは必ず記録します。
# バトルの進行ログは、対戦成立時にギルドカテゴリー内へ自動生成する
# バトル専用チャンネルへ投稿します（GAME_SPEC 34.14節）。
GAME_ADMIN_LOG_CHANNEL_ID = _optional_channel_env("GAME_ADMIN_LOG_CHANNEL_ID")

# ギルドを設立できるDiscordロール（騎士・七星）
GUILD_FOUNDER_ROLES = [ROLE_MEMBER, ROLE_SUB_MANAGER]

# プレイヤーランクの正式な参照元は player_roles テーブル。
# ここではDiscordロールとゲーム内ランクの対応だけを定義します。
PLAYER_RANK_ROLES = {
    ROLE_CLASS_S: "S",
    ROLE_CLASS_A: "A",
    ROLE_CLASS_B: "B",
    ROLE_CLASS_C: "C",
}

# 使い魔画像の配置先（正式画像がない個体は default.png を使用）
FAMILIAR_IMAGE_DIRECTORY = "assets/familiars"
FAMILIAR_DEFAULT_IMAGE = "default.png"
