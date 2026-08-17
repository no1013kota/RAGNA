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

-- 月次報酬をユーザー・ロール単位で一度だけ支給するための記録
CREATE TABLE IF NOT EXISTS monthly_reward_grants (
year_month TEXT NOT NULL,
user_id INTEGER NOT NULL,
role_id INTEGER NOT NULL,
amount INTEGER NOT NULL,
created_at TEXT NOT NULL,

PRIMARY KEY (
    year_month,
    user_id,
    role_id
)
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
-- RAGNA Online：プレイヤーランク
-- Discordロールから同期するゲーム内ランクの正式な参照元
-- ==================================================
CREATE TABLE IF NOT EXISTS player_roles (
user_id INTEGER PRIMARY KEY,
player_rank TEXT CHECK (player_rank IN ('S', 'A', 'B', 'C')),
is_manager INTEGER NOT NULL DEFAULT 0,
is_sub_manager INTEGER NOT NULL DEFAULT 0,
is_member INTEGER NOT NULL DEFAULT 0,
synced_at TEXT NOT NULL
);

-- ==================================================
-- RAGNA Online：ギルド
-- ==================================================
CREATE TABLE IF NOT EXISTS guilds (
guild_id INTEGER PRIMARY KEY AUTOINCREMENT,
name TEXT NOT NULL,
description TEXT,
master_id INTEGER NOT NULL,
capacity INTEGER NOT NULL DEFAULT 5,
status TEXT NOT NULL DEFAULT 'active'
    CHECK (status IN ('active', 'archived', 'deleted')),
recruitment_status TEXT NOT NULL DEFAULT 'closed'
    CHECK (recruitment_status IN ('open', 'closed')),
category_id INTEGER,
guild_text_channel_id INTEGER,
guild_voice_channel_id INTEGER,
master_text_channel_id INTEGER,
battle_member_channel_id INTEGER,
recruitment_channel_id INTEGER,
recruitment_message_id INTEGER,
roster_locked INTEGER NOT NULL DEFAULT 0,
wins INTEGER NOT NULL DEFAULT 0,
losses INTEGER NOT NULL DEFAULT 0,
draws INTEGER NOT NULL DEFAULT 0,
created_at TEXT NOT NULL,
updated_at TEXT NOT NULL,
archived_at TEXT,
channels_purged_at TEXT
);

-- ==================================================
-- RAGNA Online：ギルド所属
-- user_idの一意インデックスで「1プレイヤー1ギルド」を保証する
-- ==================================================
CREATE TABLE IF NOT EXISTS guild_members (
guild_id INTEGER NOT NULL REFERENCES guilds(guild_id),
user_id INTEGER NOT NULL,
member_role TEXT NOT NULL DEFAULT 'member'
    CHECK (member_role IN ('master', 'member')),
joined_at TEXT NOT NULL,

PRIMARY KEY (
    guild_id,
    user_id
)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_guild_members_user
ON guild_members(user_id);

-- ==================================================
-- RAGNA Online：ギルド参加申請
-- ==================================================
CREATE TABLE IF NOT EXISTS guild_join_requests (
request_id INTEGER PRIMARY KEY AUTOINCREMENT,
guild_id INTEGER NOT NULL REFERENCES guilds(guild_id),
user_id INTEGER NOT NULL,
status TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending', 'approved', 'rejected', 'cancelled', 'auto_cancelled')),
channel_id INTEGER,
message_id INTEGER,
created_at TEXT NOT NULL,
updated_at TEXT NOT NULL,
resolved_by INTEGER
);

-- 同じギルドへの未処理申請は1件だけにする
CREATE UNIQUE INDEX IF NOT EXISTS idx_guild_join_requests_pending
ON guild_join_requests(guild_id, user_id)
WHERE status = 'pending';

CREATE INDEX IF NOT EXISTS idx_guild_join_requests_user
ON guild_join_requests(user_id, status);

CREATE INDEX IF NOT EXISTS idx_guild_join_requests_message
ON guild_join_requests(message_id);

-- ==================================================
-- RAGNA Online：使い魔マスター
-- data/master/familiars.json から起動時に同期する
-- ==================================================
CREATE TABLE IF NOT EXISTS familiars (
familiar_id TEXT PRIMARY KEY,
name TEXT NOT NULL,
rank TEXT NOT NULL,
base_hp INTEGER NOT NULL,
base_atk INTEGER NOT NULL,
speed INTEGER NOT NULL,
cost INTEGER NOT NULL,
gender TEXT CHECK (gender IN ('male', 'female', 'none')),
image_file TEXT,
description TEXT,
enabled INTEGER NOT NULL DEFAULT 1,
version INTEGER NOT NULL DEFAULT 1,
updated_at TEXT NOT NULL
);

-- ==================================================
-- RAGNA Online：スキル定義
-- conditions・effectsはJSON配列として保存する
-- ==================================================
CREATE TABLE IF NOT EXISTS familiar_skills (
skill_id TEXT PRIMARY KEY,
name TEXT NOT NULL,
description TEXT NOT NULL,
skill_type TEXT NOT NULL CHECK (skill_type IN ('active', 'passive')),
trigger TEXT,
target_type TEXT,
priority INTEGER NOT NULL DEFAULT 100,
max_uses_per_battle INTEGER,
consumes_attack INTEGER NOT NULL DEFAULT 0,
targets TEXT NOT NULL DEFAULT '[]',
conditions TEXT NOT NULL DEFAULT '[]',
effects TEXT NOT NULL DEFAULT '[]',
enabled INTEGER NOT NULL DEFAULT 1,
version INTEGER NOT NULL DEFAULT 1,
updated_at TEXT NOT NULL
);

-- ==================================================
-- RAGNA Online：使い魔とスキルの対応
-- ==================================================
CREATE TABLE IF NOT EXISTS familiar_skill_links (
familiar_id TEXT NOT NULL REFERENCES familiars(familiar_id),
skill_id TEXT NOT NULL REFERENCES familiar_skills(skill_id),
slot INTEGER NOT NULL,

PRIMARY KEY (
    familiar_id,
    skill_id
)
);

-- ==================================================
-- RAGNA Online：所有使い魔
-- 合成・売却で消費した個体は状態を残して保存する
-- ==================================================
CREATE TABLE IF NOT EXISTS player_familiars (
instance_id INTEGER PRIMARY KEY AUTOINCREMENT,
user_id INTEGER NOT NULL,
familiar_id TEXT NOT NULL REFERENCES familiars(familiar_id),
level INTEGER NOT NULL DEFAULT 1,
status TEXT NOT NULL DEFAULT 'owned'
    CHECK (status IN ('owned', 'fused', 'sold')),
obtained_at TEXT NOT NULL,
updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_player_familiars_owner
ON player_familiars(user_id, status);

-- ==================================================
-- RAGNA Online：ガチャ設定
-- ==================================================
CREATE TABLE IF NOT EXISTS familiar_gacha_pools (
pool_id TEXT PRIMARY KEY,
name TEXT NOT NULL,
single_cost INTEGER NOT NULL,
multi_cost INTEGER NOT NULL,
multi_count INTEGER NOT NULL,
is_public INTEGER NOT NULL DEFAULT 0,
updated_at TEXT NOT NULL
);

-- 確率は千分率の整数で保存し、浮動小数の誤差を持ち込まない
CREATE TABLE IF NOT EXISTS familiar_gacha_entries (
pool_id TEXT NOT NULL REFERENCES familiar_gacha_pools(pool_id),
slot_type TEXT NOT NULL CHECK (slot_type IN ('normal', 'guaranteed')),
rank TEXT NOT NULL,
weight_permille INTEGER NOT NULL,

PRIMARY KEY (
    pool_id,
    slot_type,
    rank
)
);

-- ==================================================
-- RAGNA Online：使い魔取引履歴
-- ==================================================
CREATE TABLE IF NOT EXISTS familiar_transactions (
id INTEGER PRIMARY KEY AUTOINCREMENT,
user_id INTEGER NOT NULL,
type TEXT NOT NULL CHECK (type IN ('gacha', 'fusion', 'sell')),
instance_id INTEGER,
familiar_id TEXT,
level INTEGER,
coin_amount INTEGER NOT NULL DEFAULT 0,
material_instance_id INTEGER,
note TEXT,
created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_familiar_transactions_user
ON familiar_transactions(user_id, created_at);

-- ==================================================
-- RAGNA Online：バトル占有ロック
-- 申請・募集・進行中バトルをギルドごとに1件へ制限する
-- ==================================================
CREATE TABLE IF NOT EXISTS guild_battle_locks (
guild_id INTEGER PRIMARY KEY REFERENCES guilds(guild_id),
lock_type TEXT NOT NULL CHECK (lock_type IN ('request', 'recruitment', 'battle')),
reference_id INTEGER NOT NULL,
created_at TEXT NOT NULL
);

-- ==================================================
-- RAGNA Online：バトル申請
-- ==================================================
CREATE TABLE IF NOT EXISTS guild_battle_requests (
-- ギルドごとのベット額（申請したギルドマスターが決める）
bet_coin INTEGER,
request_id INTEGER PRIMARY KEY AUTOINCREMENT,
from_guild_id INTEGER NOT NULL REFERENCES guilds(guild_id),
to_guild_id INTEGER NOT NULL REFERENCES guilds(guild_id),
status TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending', 'approved', 'rejected', 'cancelled')),
channel_id INTEGER,
message_id INTEGER,
created_at TEXT NOT NULL,
updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_guild_battle_requests_message
ON guild_battle_requests(message_id);

-- ==================================================
-- RAGNA Online：バトル募集
-- ==================================================
CREATE TABLE IF NOT EXISTS guild_battle_recruitments (
-- ギルドごとのベット額（募集したギルドマスターが決める）
bet_coin INTEGER,
recruitment_id INTEGER PRIMARY KEY AUTOINCREMENT,
guild_id INTEGER NOT NULL REFERENCES guilds(guild_id),
status TEXT NOT NULL DEFAULT 'open'
    CHECK (status IN ('open', 'matched', 'cancelled')),
channel_id INTEGER,
message_id INTEGER,
created_at TEXT NOT NULL,
updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_guild_battle_recruitments_message
ON guild_battle_recruitments(message_id);

-- ==================================================
-- RAGNA Online：ギルドバトル
-- ==================================================
CREATE TABLE IF NOT EXISTS guild_battles (
battle_id INTEGER PRIMARY KEY AUTOINCREMENT,
-- ギルドごとのベット額（出場者で均等に分担する）
bet_coin INTEGER,
guild_a_id INTEGER NOT NULL REFERENCES guilds(guild_id),
guild_b_id INTEGER NOT NULL REFERENCES guilds(guild_id),
status TEXT NOT NULL
    CHECK (status IN ('preparing', 'in_progress', 'paused', 'finished', 'aborted')),
result TEXT CHECK (result IN ('guild_a', 'guild_b', 'draw', 'aborted')),
end_reason TEXT,
current_round INTEGER NOT NULL DEFAULT 0,
turn_index INTEGER NOT NULL DEFAULT 0,
turn_order TEXT NOT NULL DEFAULT '[]',
current_unit_id INTEGER,
action_seq INTEGER NOT NULL DEFAULT 0,
log_seq INTEGER NOT NULL DEFAULT 0,
guild_a_remaining_seconds INTEGER NOT NULL,
guild_b_remaining_seconds INTEGER NOT NULL,
turn_started_at TEXT,
turn_deadline_at TEXT,
guild_a_status_message_id INTEGER,
guild_b_status_message_id INTEGER,
guild_a_turn_message_id INTEGER,
guild_b_turn_message_id INTEGER,
guild_a_channel_id INTEGER,
guild_b_channel_id INTEGER,
channels_deleted_at TEXT,
started_at TEXT,
finished_at TEXT,
created_at TEXT NOT NULL,
updated_at TEXT NOT NULL
);

-- 対戦成立時に作るバトル専用チャンネルからバトルを引くためのインデックス
CREATE INDEX IF NOT EXISTS idx_guild_battles_channel_a
ON guild_battles(guild_a_channel_id);

CREATE INDEX IF NOT EXISTS idx_guild_battles_channel_b
ON guild_battles(guild_b_channel_id);

CREATE INDEX IF NOT EXISTS idx_guild_battles_status
ON guild_battles(status);

-- ==================================================
-- RAGNA Online：出場者セット
-- ギルドごとに1～5人を保持し、バトル終了時に解除する
-- 使い魔のセットは guild_battle_entries が持つ
-- ==================================================
CREATE TABLE IF NOT EXISTS guild_battle_members (
guild_id INTEGER NOT NULL REFERENCES guilds(guild_id),
user_id INTEGER NOT NULL,
slot INTEGER NOT NULL,
familiar_count INTEGER NOT NULL DEFAULT 0,
instance_id INTEGER REFERENCES player_familiars(instance_id),
updated_at TEXT NOT NULL,

PRIMARY KEY (
    guild_id,
    user_id
)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_guild_battle_members_slot
ON guild_battle_members(guild_id, slot);

-- ==================================================
-- RAGNA Online：バトル用使い魔の事前登録（9節）
-- ギルドに関係なく、誰でもいつでも順番付きで登録できる。
-- 出場者セット時に、この順番どおりに自動採用する。
-- ==================================================
CREATE TABLE IF NOT EXISTS player_battle_familiars (
user_id INTEGER NOT NULL,
priority INTEGER NOT NULL,
instance_id INTEGER NOT NULL REFERENCES player_familiars(instance_id),
updated_at TEXT NOT NULL,

PRIMARY KEY (
    user_id,
    priority
)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_player_battle_familiars_instance
ON player_battle_familiars(user_id, instance_id);

-- ==================================================
-- RAGNA Online：出場する使い魔（1ギルドあたり最大5体）
-- 1人が複数体を出せるため、出場者セットとは別に持つ（9節）
-- ==================================================
CREATE TABLE IF NOT EXISTS guild_battle_entries (
guild_id INTEGER NOT NULL REFERENCES guilds(guild_id),
entry_slot INTEGER NOT NULL,
user_id INTEGER NOT NULL,
instance_id INTEGER NOT NULL REFERENCES player_familiars(instance_id),
updated_at TEXT NOT NULL,

PRIMARY KEY (
    guild_id,
    entry_slot
)
);

-- 同じ個体を2枠へセットできないようにする
CREATE UNIQUE INDEX IF NOT EXISTS idx_guild_battle_entries_instance
ON guild_battle_entries(guild_id, instance_id);

CREATE INDEX IF NOT EXISTS idx_guild_battle_entries_user
ON guild_battle_entries(guild_id, user_id);

-- ==================================================
-- RAGNA Online：戦闘用使い魔
-- 所有使い魔とは完全に分離した、バトル中だけの能力値
-- ==================================================
CREATE TABLE IF NOT EXISTS guild_battle_units (
battle_unit_id INTEGER PRIMARY KEY AUTOINCREMENT,
battle_id INTEGER NOT NULL REFERENCES guild_battles(battle_id),
guild_id INTEGER NOT NULL,
player_id INTEGER NOT NULL,
familiar_instance_id INTEGER NOT NULL,
familiar_id TEXT NOT NULL,
level INTEGER NOT NULL,
max_hp INTEGER NOT NULL,
current_hp INTEGER NOT NULL,
base_atk INTEGER NOT NULL,
current_atk INTEGER NOT NULL,
speed INTEGER NOT NULL,
base_speed INTEGER NOT NULL DEFAULT 0,
cost INTEGER NOT NULL,
gender TEXT,
slot INTEGER NOT NULL,
alive INTEGER NOT NULL DEFAULT 1,
order_seed INTEGER NOT NULL DEFAULT 0,
active_skill_uses TEXT NOT NULL DEFAULT '{}',
passive_uses TEXT NOT NULL DEFAULT '{}',
state_flags TEXT NOT NULL DEFAULT '{}',
updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_guild_battle_units_battle
ON guild_battle_units(battle_id);

CREATE INDEX IF NOT EXISTS idx_guild_battle_units_player
ON guild_battle_units(player_id);

-- ==================================================
-- RAGNA Online：バフ・デバフ・状態異常
-- ==================================================
CREATE TABLE IF NOT EXISTS guild_battle_effects (
effect_id INTEGER PRIMARY KEY AUTOINCREMENT,
battle_id INTEGER NOT NULL REFERENCES guild_battles(battle_id),
battle_unit_id INTEGER NOT NULL,
effect_type TEXT NOT NULL,
value INTEGER,
duration_type TEXT NOT NULL,
remaining INTEGER,
applied_round INTEGER,
source_unit_id INTEGER,
source_skill_id TEXT,
params TEXT NOT NULL DEFAULT '{}',
created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_guild_battle_effects_unit
ON guild_battle_effects(battle_id, battle_unit_id);

-- ==================================================
-- RAGNA Online：行動ログ
-- 詳細ログは365日保存し、定期保守で削除する
-- ==================================================
CREATE TABLE IF NOT EXISTS guild_battle_logs (
log_id INTEGER PRIMARY KEY AUTOINCREMENT,
battle_id INTEGER NOT NULL REFERENCES guild_battles(battle_id),
sequence INTEGER NOT NULL,
round INTEGER NOT NULL,
event_type TEXT NOT NULL,
actor_unit_id INTEGER,
target_unit_id INTEGER,
skill_id TEXT,
amount INTEGER,
detail TEXT,
created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_guild_battle_logs_battle
ON guild_battle_logs(battle_id, sequence);

-- ==================================================
-- RAGNA Online：バトル報酬
-- reward_dateは日本時間の日付。1日3試合までの判定に使う
-- ==================================================
CREATE TABLE IF NOT EXISTS guild_battle_rewards (
battle_id INTEGER NOT NULL REFERENCES guild_battles(battle_id),
user_id INTEGER NOT NULL,
guild_id INTEGER NOT NULL,
coin INTEGER NOT NULL,
xp INTEGER NOT NULL,
reward_date TEXT NOT NULL,
created_at TEXT NOT NULL,

PRIMARY KEY (
    battle_id,
    user_id
)
);

CREATE INDEX IF NOT EXISTS idx_guild_battle_rewards_daily
ON guild_battle_rewards(user_id, reward_date);

-- 同じ2ギルド間で1日1試合だけ報酬対象にするための記録
CREATE TABLE IF NOT EXISTS guild_battle_pair_rewards (
reward_date TEXT NOT NULL,
low_guild_id INTEGER NOT NULL,
high_guild_id INTEGER NOT NULL,
battle_id INTEGER NOT NULL,
created_at TEXT NOT NULL,

PRIMARY KEY (
    reward_date,
    low_guild_id,
    high_guild_id
)
);

-- ==================================================
-- RAGNA Online：運営操作ログ
-- 表示名やメッセージ本文は保存しない。保存期間は180日
-- ==================================================
CREATE TABLE IF NOT EXISTS game_admin_logs (
id INTEGER PRIMARY KEY AUTOINCREMENT,
executor_id INTEGER,
action TEXT NOT NULL,
target_user_id INTEGER,
target_guild_id INTEGER,
target_battle_id INTEGER,
success INTEGER NOT NULL DEFAULT 1,
reason TEXT,
operation_id TEXT,
created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_game_admin_logs_created
ON game_admin_logs(created_at);

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
