# 使い魔画像

RAGNA Onlineの使い魔サムネイル画像を置く場所です。

## 置き方

- ファイル名は `<使い魔ID>.png`（例：`loki.png`、`yuki_onna.png`）。
  使い魔IDは [戦闘ルール・使い魔データ](../../docs/BATTLE_RULES.md) の「ID」と
  `data/master/familiars.json` の `familiar_id` に一致させます。
- 正式画像がない個体には、共通の仮画像 `default.png` を表示します。
  `default.png` も無い場合、Embedはサムネイルなしで表示されます。
- 画像は行動ログEmbedと使い魔一覧のサムネイルに使われます。
  正方形で256×256ピクセル程度を目安にしてください。

## 仮画像について

現在、個別の使い魔画像は未登録です。同梱の `default.png` は仮画像で、
次のコマンドで再生成できます（外部ライブラリは不要です）。

```bash
python scripts/make_familiar_placeholder.py
```

正式画像を配置すると、その使い魔だけ自動的に正式画像へ切り替わります。

## 注意

使用する画像は、運営が権利を保有するもの、または利用許諾を確認したものに
限ります（GAME_SPEC 10.6節・32.5節）。権利確認が済んでいない画像は置かないでください。
