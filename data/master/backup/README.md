# マスターデータのバックアップ

スキル効果を固定値から割合（ATKのX%など）へ変える前の状態を保存しています。
効果の強さを戻したいときや、換算が意図どおりだったかを確かめたいときに参照します。

| ファイル | 内容 |
| --- | --- |
| `skills_fixed_values.json` | 割合化する前の `skills.json`（固定値のまま） |
| `balance_fixed_values.json` | 同時点の `balance.json` |

このフォルダはBotから読み込みません。`game.master_data` が読むのは
`data/master/` 直下のファイルだけです。
