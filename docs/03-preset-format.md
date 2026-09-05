# 03. 中間プリセット形式 v0

> 改訂前提の暫定版。`docs/02-engine-spec.md` と一対一で連動する。片方だけを変更してはならない。

## 位置づけ

LLM側とバイナリ側の唯一の境界面。LLMが書き、エンジンが読む。

**スカラー値の羅列ではない。** 時系列を第一級の値として持つことがこの形式の要件である（理由は `docs/02-engine-spec.md`）。

## 形式

JSON。理由：

- LLMが直接生成・編集できる
- diffが人間に読める（PRレビューが成立する）
- スキーマ検証の道具が揃っている

バイナリ形式は採らない。フェーズ2でファイルサイズが問題になった場合に再検討する。

## 時系列の表現

すべての時間変化する値は、共通の `Timeseries` 型で表す。

```json
{
  "unit": "hz",
  "interp": "linear",
  "points": [
    { "t": 0.0,   "v": 220.0 },
    { "t": 0.045, "v": 261.6 },
    { "t": 0.500, "v": 261.6 }
  ]
}
```

- `t` は秒。ノートオンを 0.0 とする
- `interp` は `linear` / `exp` / `step` / `spline`
- 点が1個だけの場合は定数として扱う。**スカラー専用の型を別に作らない**（推定側の分岐が増えるため）

## 全体構造

```json
{
  "format_version": "0.1.0",
  "engine_spec_version": "0.1.0",
  "meta": {
    "source": "sample_042.wav",
    "generated_by": "fit-loop",
    "iteration": 17
  },
  "layers": {
    "transient": { "enabled": true,  "gain": {...}, "duration": {...}, "spectral_envelope": [...] },
    "harmonic":  { "enabled": true,  "f0": {...}, "partial_amplitudes": [...], "inharmonicity": {...} },
    "formant":   { "enabled": true,  "bands": [ { "freq": {...}, "q": {...}, "gain": {...} } ] }
  },
  "modulation": {
    "sources": [ { "id": "env1", "type": "envelope", "points": [...] } ],
    "routes":  [ { "from": "env1", "to": "formant.bands[0].freq", "depth": 0.4, "curve": "linear" } ]
  }
}
```

構造は `docs/03-preset-format.schema.json`（JSON Schema）で機械可読に定義する。バージョニングは本ドキュメント「バージョニング」と同じセマンティックバージョニングに従い、現在のバージョンは各プリセットの `format_version` に記録する。未知のフィールドの不許可（下記）は同スキーマの `additionalProperties: false` として表現している。

## 必須の規約

- `format_version` と `engine_spec_version` は必須。**両方**を持つ理由は、形式は変えずにエンジン能力だけが増えるケースがあるため
- 未知のフィールドを見つけたエンジンは**エラーで停止する**。黙って無視しない。無視すると、推定側が書いたつもりのパラメータが効いていないことに誰も気づけない
- ルーティング先はドット記法のパス文字列で指定する。存在しないパスもエラー
- `meta` はエンジンの動作に影響してはならない。デバッグとトレーサビリティ専用

## バージョニング

セマンティックバージョニングに従う。

| 変更 | バージョン |
|---|---|
| フィールド追加（省略可能） | minor |
| フィールド削除・意味変更・必須化 | major |
| 説明の修正のみ | patch |

major を上げる場合はマイグレーションスクリプトを同PRに含める。ゴールデンプリセットが読めなくなるとCIリグレッションの履歴が途切れるため。

## 未確定

- `spectral_envelope` の帯域分割方式（等間隔 / メル / バーク）
- `partial_amplitudes` の倍音数上限
- 時系列の点数上限（プリセットサイズと推定コストのトレードオフ）

いずれも `docs/06-open-questions.md` を参照。
