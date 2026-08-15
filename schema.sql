-- RAGNA Bot Database
-- SQLite Schema

-- ==================================================
-- 仮メンバー情報
-- ==================================================
CREATE TABLE IF NOT EXISTS trial_members (
user_id INTEGER PRIMARY KEY,
class TEXT NOT NULL,
start_date TEXT NOT NULL,
end_date TEXT NOT NULL,
intro_url TEXT,
evaluation_thread_id INTEGER,
created_at TEXT NOT NULL
);

-- ==================================================
-- 評価履歴
-- ==================================================
CREATE TABLE IF NOT EXISTS evaluations (
id INTEGER PRIMARY KEY AUTOINCREMENT,
trial_member_id INTEGER NOT NULL,
evaluator_id INTEGER NOT NULL,
voice_score INTEGER,
conversation_score INTEGER,
charm_score INTEGER,
overall_score INTEGER,
note TEXT,
created_at TEXT NOT NULL
);

-- ==================================================
-- 追記履歴
-- ==================================================
CREATE TABLE IF NOT EXISTS comments (
id INTEGER PRIMARY KEY AUTOINCREMENT,
trial_member_id INTEGER NOT NULL,
evaluator_id INTEGER NOT NULL,
voice_score INTEGER,
conversation_score INTEGER,
charm_score INTEGER,
overall_score INTEGER,
note TEXT,
created_at TEXT NOT NULL
);

-- ==================================================
-- 評価による延長管理
-- 総合評価による延長管理（評価員ごとに1回）
-- ==================================================
CREATE TABLE IF NOT EXISTS evaluation_extensions (
trial_member_id INTEGER NOT NULL,
evaluator_id INTEGER NOT NULL,
extended_at TEXT NOT NULL,

PRIMARY KEY (
    trial_member_id,
    evaluator_id
)
);

-- ==================================================
-- 仮メンバー終了アンケートDM
-- ==================================================
CREATE TABLE IF NOT EXISTS trial_member_end_surveys (
ended_trial_member_id INTEGER PRIMARY KEY,
channel_id INTEGER NOT NULL,
message_id INTEGER NOT NULL,
expires_at TEXT NOT NULL
);

-- ==================================================
-- メンバー情報
-- ==================================================
CREATE TABLE IF NOT EXISTS members (
user_id INTEGER PRIMARY KEY,
class TEXT NOT NULL,
join_date TEXT NOT NULL,
invite_points INTEGER DEFAULT 0
);

-- ==================================================
-- 仮メンバー終了アンケート
-- ==================================================
CREATE TABLE IF NOT EXISTS evaluator_reviews (
id INTEGER PRIMARY KEY AUTOINCREMENT,
ended_trial_member_id INTEGER NOT NULL,
evaluator_id INTEGER NOT NULL,
comment TEXT,
created_at TEXT NOT NULL
);

-- ==================================================
-- VC時間
-- ==================================================
CREATE TABLE IF NOT EXISTS vc_time (
user_id INTEGER PRIMARY KEY,
total_minutes INTEGER NOT NULL DEFAULT 0,
monthly_minutes INTEGER NOT NULL DEFAULT 0,
total_xp INTEGER NOT NULL DEFAULT 0,
monthly_xp INTEGER NOT NULL DEFAULT 0
);

-- ==================================================
-- VC月間データ管理
-- ==================================================
CREATE TABLE IF NOT EXISTS vc_month_state (
id INTEGER PRIMARY KEY CHECK (id = 1),
year_month TEXT NOT NULL
);

-- ==================================================
-- 毎月1日の給料重複確認
-- ==================================================
CREATE TABLE IF NOT EXISTS monthly_rewards (
id INTEGER PRIMARY KEY CHECK (id = 1),
year_month TEXT NOT NULL
);

-- ==================================================
-- coin残高
-- ==================================================
CREATE TABLE IF NOT EXISTS balances (
user_id INTEGER PRIMARY KEY,
balance INTEGER DEFAULT 0
);

-- ==================================================
-- coin履歴
-- ==================================================
CREATE TABLE IF NOT EXISTS transactions (
id INTEGER PRIMARY KEY AUTOINCREMENT,
type TEXT NOT NULL,
executor_id INTEGER,
target_id INTEGER,
amount INTEGER,
note TEXT,
created_at TEXT NOT NULL
);

-- ==================================================
-- 招待報酬
-- ==================================================
CREATE TABLE IF NOT EXISTS invite_rewards (
id INTEGER PRIMARY KEY AUTOINCREMENT,
executor_id INTEGER NOT NULL,
target_id INTEGER NOT NULL,
points INTEGER NOT NULL,
reason TEXT,
created_at TEXT NOT NULL
);

-- ==================================================
-- 招待ポイント特典
-- ==================================================
CREATE TABLE IF NOT EXISTS invite_benefits (
user_id INTEGER PRIMARY KEY,
hotel_free_rate INTEGER NOT NULL DEFAULT 0,
has_start_ticket INTEGER NOT NULL DEFAULT 0
);

-- ==================================================
-- クラス変更履歴
-- ==================================================
CREATE TABLE IF NOT EXISTS class_changes (
id INTEGER PRIMARY KEY AUTOINCREMENT,
executor_id INTEGER NOT NULL,
target_id INTEGER NOT NULL,
old_class TEXT NOT NULL,
new_class TEXT NOT NULL,
created_at TEXT NOT NULL
);

-- ==================================================
-- 評価ログ
-- ==================================================
CREATE TABLE IF NOT EXISTS evaluation_log_channels (
evaluator_id INTEGER PRIMARY KEY,
channel_id INTEGER NOT NULL
);

-- ==================================================
-- 宿屋管理
-- ==================================================
CREATE TABLE IF NOT EXISTS hotel_rooms (
channel_id INTEGER PRIMARY KEY,
text_channel_id INTEGER,
owner_id INTEGER NOT NULL,
plan TEXT NOT NULL,
created_at TEXT NOT NULL,
expires_at TEXT NOT NULL,
is_private INTEGER NOT NULL,
max_users INTEGER NOT NULL
);

-- ==================================================
-- 宿屋管理者テーブル
-- ==================================================
CREATE TABLE IF NOT EXISTS hotel_managers (
channel_id INTEGER NOT NULL,
user_id INTEGER NOT NULL,

PRIMARY KEY (
    channel_id,
    user_id
)
);

-- ==================================================
-- お問い合わせ
-- ==================================================
CREATE TABLE IF NOT EXISTS tickets (
channel_id INTEGER PRIMARY KEY,
owner_id INTEGER NOT NULL,
ticket_type TEXT NOT NULL,
status TEXT NOT NULL,
created_at TEXT NOT NULL
);

-- ==================================================
-- SQLite高速化
-- ==================================================
CREATE INDEX IF NOT EXISTS idx_eval_trial_member
ON evaluations(trial_member_id);

CREATE INDEX IF NOT EXISTS idx_eval_evaluator
ON evaluations(evaluator_id);

CREATE INDEX IF NOT EXISTS idx_comments_trial_member
ON comments(trial_member_id);

CREATE INDEX IF NOT EXISTS idx_evaluator_reviews
ON evaluator_reviews(evaluator_id);