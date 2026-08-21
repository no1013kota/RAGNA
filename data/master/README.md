# マスターデータの読み方・直し方

ゲームの数値と使い魔・スキルの内容は、このフォルダのJSONファイルにあります。
プログラムを読まずに、ここを書き換えるだけでバランスを変更できます。

Discordに表示される案内文やボタンの名前は、ここではなく `texts/` にあります。
どちらを直せばよいか迷ったら、「数値・使い魔・スキル」ならここ、「案内文」なら
`texts/` です。

## 書き換えるときの注意

- JSONは書式に厳しいため、**カンマ・かっこ・引用符を消さない**でください。
- 直したら次のコマンドで壊れていないか確認できます。

  ```bash
  python scripts/check_project.py
  python -m unittest discover -s tests
  ```

- `docs/BATTLE_RULES.md` にも同じ数値が書かれています。テストが両者のずれを
  検出するので、**数値を変えたらドキュメントも同じように直して**ください。

## ファイル

| ファイル | 内容 |
| --- | --- |
| `balance.json` | 料金・成長式・戦闘の定数・報酬 |
| `familiars.json` | 使い魔40体（名前・能力値・性別・COST・スキル） |
| `skills.json` | アクティブ・パッシブスキル |
| `gacha.json` | ガチャの料金と排出率 |
| `backup/` | 変更前の値の控え。Botは読み込みません |

## balance.json の主な値

### guild — ギルド

| 値 | 意味 |
| --- | --- |
| `create_cost` | ギルド設立にかかるcoin |
| `rename_cost` | ギルド名の変更にかかるcoin |
| `member_slot_cost` | メンバー枠を1つ増やすcoin |
| `initial_capacity` / `max_capacity` | 設立時の人数枠と、拡張の上限 |
| `name_min_length` / `name_max_length` | ギルド名の文字数 |
| `description_min_length` / `description_max_length` | ギルド説明の文字数 |
| `archive_days` | 解散したギルドの記録を残す日数 |
| `archive_name_prefix` | 解散済みギルドの名前の頭に付ける文字 |

### familiar — 使い魔

| 値 | 意味 |
| --- | --- |
| `min_level` / `max_level` | 使い魔のレベルの範囲 |
| `hp_growth_rate_per_level` | 1レベルごとに増えるHPの割合（0.05 = 5%） |
| `atk_growth_rate_per_level` | 1レベルごとに増えるATKの割合 |
| `speed_growth_levels` | SPDが上がるレベル |
| `speed_growth_value` | 1回あたり上がるSPD |
| `speed_max` | SPDの上限。スキルの「SPD-20%」はこの値が基準 |
| `sell_base_prices` | ランクごとの売却額（Lv.1のとき） |
| `sell_price_multiplier` | 売却額全体の倍率 |
| `fusion_cost_rate_per_material` | 合成費用（売却額に対する割合）。0で無料 |
| `usable_rank_offset` | 図鑑・ガチャまわりの内部処理用。触らないでください |

### battle — ギルドバトル

| 値 | 意味 |
| --- | --- |
| `max_units` | 1ギルドが出せる使い魔の数 |
| `max_total_cost` | 編成の合計COST上限。0で無制限 |
| `min_members` / `max_members` | 出場できる人数の範囲 |
| `familiars_per_member` | 出場人数ごとの、1人が出せる使い魔の数 |
| `critical_chance_permille` | クリティカルの確率（1000分率。100 = 10%） |
| `critical_multiplier` | クリティカル時のダメージ倍率 |
| `atk_buff_cap` / `atk_debuff_cap` | ATKの上げ幅・下げ幅の限界 |
| `same_skill_stack_limit` | 同じスキルの効果を重ねられる回数 |
| `turn_time_seconds` | 1ターンの持ち時間（秒）。300 = 5分 |
| `guild_time_seconds` | 1ギルドの持ち時間（秒）。1800 = 30分 |
| `surrender_reward_from_round` | このラウンド以降の降参は報酬が出る |
| `battle_channel_retention_days` | バトル専用チャンネルを残す日数 |
| `reward_daily_limit_per_player` | 1人が1日に受け取れる報酬の回数 |

#### battle.bet — 賭けるcoinとXP

負けた側のcoinが勝った側へ移ります。

| 値 | 意味 |
| --- | --- |
| `coin` | レートを選ばなかった場合の既定額 |
| `win_xp` / `lose_xp` / `draw_xp` | 勝ち・負け・引き分けでもらえるXP |
| `rates` | バトル申請・募集で選ぶレートの一覧 |

`rates` はセレクトメニューに**上から書いた順**で並びます。1つ足すときは、
既存の行をまるごとコピーして `rate_id`（英字。他と重複しないこと）、
`name`（画面に出る名前）、`coin`（金額）を書き換えてください。

#### battle.ranking — ギルドランキング

| 値 | 意味 |
| --- | --- |
| `win_points` / `draw_points` / `lose_points` | 勝ち・引き分け・負けの勝点 |
| `display_limit` | ランキングに表示する順位の数 |

## skills.json の効果量

スキルの効果量は固定値ではなく、**能力値に対する割合**で書きます。
レベルが上がって能力値が伸びれば、スキルの効果量も一緒に伸びます。

```json
{ "effect_type": "damage", "percent": 75, "percent_of": "actor_atk" }
```

`percent` は符号付きの百分率（マイナスなら下げる効果）、`percent_of` が基準です。

| `percent_of` | 基準になる値 |
| --- | --- |
| `actor_atk` | スキルを使った側の、その時点のATK |
| `actor_max_hp` | スキルを使った側の最大HP |
| `target_atk` | 効果を受ける側の、その時点のATK |
| `target_max_hp` | 効果を受ける側の最大HP |
| `speed_cap` | SPDの上限（`familiar.speed_max`。通常100） |

毒などの継続ダメージは `params` の中で `damage_percent` と `damage_percent_of`
を使います。

計算は四捨五入し、割合が0でない限り最低でも1は動きます。
`description` は画面にそのまま出るので、数値を変えたら文面も直してください。

詳しい仕様は `docs/GAME_SPEC.md` の19.1節にあります。
