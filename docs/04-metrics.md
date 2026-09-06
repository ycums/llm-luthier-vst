# 04. 客観評価指標

> このドキュメントが定義するハーネスが、**フェーズ0で最初に作るもの**である。
> エンジンより先に作る。これがないとPRのエビデンスが成立しない。

## 二つの役割を兼ねる

同じ実装が2箇所で使われる。二度作らないこと。

1. **フィッティングの評価器**（ループ1）：残差を測り、切り分けの材料を出す
2. **CIのリグレッションテスト**：コミットごとにゴールデン音源をレンダし、指標の悪化を検出する

## 出力はベクトルであること

**スカラー1個に潰してはならない。** 潰すと `docs/01-architecture.md` の残差切り分けが成立しなくなり、「能力ギャップか、フィット誤差か」の判定ができなくなる。

出力は最低限、以下を含む構造化データとする。

### 全体指標

| 指標 | 目的 |
|---|---|
| マルチスケールスペクトル距離 | 複数のFFTサイズで測り、時間分解能と周波数分解能の両方を見る |
| MFCC距離 | 知覚的な音色の近さ |
| ラウドネス差 | ゲインだけずれているケースの切り分け |

### 区間別指標

時間軸を分割して個別に出す。アタック部の誤差が定常部に埋もれるのを防ぐため。全体指標と
同じ3指標（マルチスケールスペクトル距離／MFCC距離／ラウドネス差）を、区間ごとに切り出した
波形に対して算出する。

| 区間 | 目的 |
|---|---|
| アタック（既定 0〜20ms、**仮の値**） | トランジェント層の妥当性 |
| 遷移部 | ピッチ変化区間。グライド表現の妥当性 |
| 定常部 | ハーモニック・フォルマント層の妥当性 |
| リリース | 減衰特性 |

区間境界は実装に埋め込まず、外部入力（設定またはマニフェストの注釈）として与える。
アタックの終端（20ms）だけは既定値を持つが、これは**仮の値**であり妥当性は未解決
（`docs/06-open-questions.md` Q-009）。遷移部・定常部・リリースの境界には既定値を置かない。
音源ごとの注釈が与えられていない場合、該当区間は欠測（`value: null` + `missing_reason`）として
出力する。エラーにはしない。使用した境界の実値は `calc_conditions.segment_boundaries_s` に
記録する。

### 帯域別指標

周波数軸を分割した誤差分布。どの帯域に山が残るかがフォルマント層の帯域数不足を示す。

### 軌跡指標

| 指標 | 目的 |
|---|---|
| トランジェント包絡の相関 | アタックの形が合っているか |
| f0軌跡の距離 | ピッチの追従 |
| フォルマント軌跡の距離 | 母音・奏法変化の追従 |

## 出力形式

JSON。CIとフィッティングループの両方が機械可読に扱えること。人間向けの整形出力は別途スクリプトで行う。

構造は `docs/04-metrics.schema.json`（JSON Schema）で機械可読に定義する。バージョニングは `docs/03-preset-format.md` と同じセマンティックバージョニングに従い、現在のバージョンは `calc_conditions.schema_version` に記録する。

各指標の値は「値」または「欠測（値と理由）」のどちらかを取る（欠測は valid な状態として許容される）。

```json
{ "value": 0.0, "missing_reason": null }
```

```json
{ "value": null, "missing_reason": "スペクトログラムの該当区間が無音のため算出不能" }
```

```json
{
  "target": "sample_042.wav",
  "overall": {
    "msstft": { "value": 0.0, "missing_reason": null },
    "mfcc": { "value": 0.0, "missing_reason": null },
    "loudness_diff_db": { "value": 0.0, "missing_reason": null }
  },
  "segments": {
    "attack": {
      "msstft": { "value": 0.0, "missing_reason": null },
      "mfcc": { "value": 0.0, "missing_reason": null },
      "loudness_diff_db": { "value": 0.0, "missing_reason": null }
    },
    "transition": {
      "msstft": { "value": null, "missing_reason": "遷移部の区間境界（transition_end_s）が注釈として与えられていない" },
      "mfcc": { "value": null, "missing_reason": "遷移部の区間境界（transition_end_s）が注釈として与えられていない" },
      "loudness_diff_db": { "value": null, "missing_reason": "遷移部の区間境界（transition_end_s）が注釈として与えられていない" }
    },
    "sustain": {
      "msstft": { "value": null, "missing_reason": "定常部の区間境界（transition_end_s / sustain_end_s）が注釈として与えられていない" },
      "mfcc": { "value": null, "missing_reason": "定常部の区間境界（transition_end_s / sustain_end_s）が注釈として与えられていない" },
      "loudness_diff_db": { "value": null, "missing_reason": "定常部の区間境界（transition_end_s / sustain_end_s）が注釈として与えられていない" }
    },
    "release": {
      "msstft": { "value": 0.0, "missing_reason": null },
      "mfcc": { "value": 0.0, "missing_reason": null },
      "loudness_diff_db": { "value": 0.0, "missing_reason": null }
    }
  },
  "bands": [
    { "lo_hz": 0, "hi_hz": 200, "error": { "value": 0.0, "missing_reason": null } }
  ],
  "trajectories": {
    "transient_env_corr": { "value": 0.0, "missing_reason": null },
    "f0_dist": { "value": 0.0, "missing_reason": null },
    "formant_dist": { "value": 0.0, "missing_reason": null }
  },
  "calc_conditions": {
    "schema_version": "2.1.0",
    "fft_sizes": [512, 2048, 8192],
    "band_edges_hz": [0, 200, 800, 2000, 5000, 20000],
    "segment_boundaries_s": { "attack_end_s": 0.02, "transition_end_s": null, "sustain_end_s": 0.45 },
    "estimation_algorithms": [
      { "name": "yin", "version": "n/a" },
      { "name": "stft-peak-tracking", "version": "1", "n_formants": 3, "frame_length": 1600, "hop_length": 320, "fft_size": 1024, "max_track_gap_hz": 300.0 }
    ],
    "render": {
      "renderer": "luthier-render",
      "preset_path": "corpus/presets/provisional_v0.json",
      "sample_rate_hz": 44100,
      "sr_mismatch_policy": "render_at_target_sample_rate"
    }
  }
}
```

## ゴールデン音源セット

コーパスの要件（`docs/01-architecture.md` のループ2はコーパス単位で回る）。

- 10〜20音源
- ジャンル・音色が意図的に散っていること。似た音を並べると過適合を検出できない
- ライセンス上、リポジトリに含めてよいものであること。含められないものはハッシュと取得手順のみ記録する
- 一度確定したら**変えない**。変える場合はADRを書く。コーパスを変えると指標の履歴が比較不能になる

## CIでの扱い

- コミットごとに全ゴールデン音源をレンダし、指標を出力する
- 前回コミットとの差分を出す
- **悪化した指標があればPRに明示する。** 自動でブロックするかどうかは、閾値が経験的に決まるまで保留（`docs/06-open-questions.md`）
- レンダは決定論的でなければならない（`docs/02-engine-spec.md`）。非決定的だと差分がノイズに埋もれる

### 基準（baseline）指標JSONの取得方法（Issue #37 / P0-12a）

「前回コミットとの差分を出す」ための基準を、**`corpus/baseline/` 配下にコミットして持ち回る**
（`harness.corpus_runner.run_corpus` の出力形式そのまま：`corpus/baseline/index.json` と
`corpus/baseline/<id>.json`）。

**検討した候補と却下理由：**

| 候補 | 却下理由 |
|---|---|
| CI成果物（GitHub Actions artifact）から取得する | 既定の保持期間（90日）で失効する。長期間動きのないブランチや低頻度のコミットで基準を失う。取得に `actions/download-artifact` 等の追加のワークフロー設定とAPI呼び出しが要り、ローカル実行（フィッティングループ、`docs/01-architecture.md` ループ1）では同じ経路を使えず、ローカル用とCI用で取得ロジックが二重化する |
| 直前実行の結果をキャッシュ（GitHub Actions cache等）で持ち回る | キャッシュは使用されないと数日で退避されうる保証のない領域であり、artifact以上に「基準が黙って消える」リスクが高い。ローカル実行から参照できない点はartifact案と同じ |

**採用理由：**

- リポジトリ直下のファイルなので、ローカル実行（ループ1の評価器）とCI実行が完全に同じ経路で基準を読む。取得ロジックの二重化が発生しない
- 失効・退避がない。git履歴に残る限り基準は消えない
- 基準の更新そのものがPRの差分として現れ、レビュアが「いつ・どのPRで基準が動いたか」をコミット履歴からそのまま追える
- `harness.corpus_runner.run_corpus` の出力をそのまま置くだけなので、基準を読む側（差分ツール）は基準の生成元を一切知らなくてよい（`corpus_runner.py` がcandidateパスの由来を知らないのと同じ設計）
- 音声そのもの（`corpus/audio/`）と異なり、指標JSONはテキストでKB単位に収まるため、コミットしてもリポジトリの肥大化にはつながらない（ADR 0004が音声の非同梱を選んだ理由とは事情が異なる）

**更新手順（暫定）：** `docs/04-metrics.md`「ゴールデン音源セット」に変更がなくても、レンダ結果に
影響する変更（`AGENTS.md` 第4節「音に影響する変更」）を含むPRでは、そのPR自身の中で
`python -m harness run-corpus --manifest corpus/manifest.json --out corpus/baseline/` を実行し直し、
`corpus/baseline/` の差分をレビュー対象に含めた上でコミットする。基準の更新を自動化するかどうか
（マージ後にCIが自動でコミットする等）は、この手動運用が実際に何回・どの程度の手間で破綻するか
観測してから決める（`AGENTS.md` 第8節「3回ルール」）。先回りして自動化しない。

**P1-07時点の既知の限界：** `corpus/manifest.json` の `candidate_path`（自己比較の seam）は、
レンダラが実装されたため **`preset_path` に置き換えられ、candidate はレンダラの出力**
（`harness.corpus_runner.py` 参照）になった。レンダのサンプルレートはターゲットに合わせ、
方針は `calc_conditions.render.sr_mismatch_policy` に記録される（docs/04-metrics.schema.json）。
なお、P1-06時点の暫定プリセットが鳴らすのは無音であるため（docs/02「この時点では4層とも
音を出さない」）、candidate は全エントリで無音になる。したがって指標値は「無音 vs ターゲット
音源」の距離となり、軌跡指標（無音側の包絡・f0・フォルマントが定義されない）は欠測として
出力される。これは「悪化」ではなく、比較対象そのものが変わったことによる変化である
（#52 注意、前後の値は比較不能）。層のDSP実装（P1-08以降）まで、この状態が続く。

## 未確定

- 各指標の重み付け（そもそも重み付けして総合スコアを作るべきかを含む）
- 成功判定の閾値
- 「N回反復しても下がらない」のN
- 区間分割の境界（アタック20msは仮）

これらは**観測してから決める**。今の時点でそれらしい数字を埋めないこと（`AGENTS.md` 第6節）。
