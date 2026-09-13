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

**更新手順（Issue #86 で決定）：** `docs/04-metrics.md`「ゴールデン音源セット」に変更がなくても、
レンダ結果に影響する変更（`AGENTS.md` 第4節「音に影響する変更」）を含むPRでは、そのPR自身の中で
`python -m harness run-corpus --manifest corpus/manifest.json --out corpus/baseline/` を実行し直し、
`corpus/baseline/` の差分をレビュー対象に含めた上でコミットする。**この手順自体は変わらない。**

この手動運用は、S-006（`docs/log/session-log.md`）で**4回連続で守られなかった**
（P1-08〜P1-11、PR #74 / #76 / #75 / #80）。`AGENTS.md` 第8節「3回ルール」の閾値（3回）を、
この観測（4回）は超えている。旧「更新手順（暫定）」の文言自体が「この手動運用が実際に何回・
どの程度の手間で破綻するか観測してから決める」というトリガー条件を事前に登録していた
（`AGENTS.md` 第8節「3回ルール」）。そのトリガーが実際に発火したため、以下のとおり
再発防止の方式を決定する。

**採用：CIで鮮度を検証する。** `corpus/baseline/` が現在のエンジンでの `run-corpus` 出力と
一致しているかをPR自身のCIで検証し、一致しなければCIを失敗させる。**検証の実装は本Issue（決定PR）
とは別のPRで行う**（`AGENTS.md` 第5節「仕様変更を含むPRでの実装変更は不可」。実装Issueは本決定の
確定後に起票し、決定前に先回りして立てない。これは `AGENTS.md` 第8節「先回りして環境を整えない」
の趣旨に基づく判断であり、同節に同文言の規定があるわけではない）。

- **不変条件**：Linux/gcc（`ubuntu-latest`。`docs/adr/0008` が「開発・CIの基準環境」と定める
  組み合わせ）でのCI実行に限り、`corpus/baseline/<id>.json` は、そのPRの現在のエンジンで実行した
  `run-corpus` の対応する出力 `<id>.json` と一致する。ただしその実行で
  `status: "missing"`（`harness/corpus_runner.py`。非同梱音源の取得失敗を含む）になったエントリは
  比較対象から除外し、一致・不一致のどちらの判定も行わない（詳細は次項）
- **Windows（MSVC）のジョブでは本検証を行わない。** `docs/06-open-questions.md` Q-016 は
  「コンパイラ間（gcc/MSVC）のビット一致は保証しない」ことを既に決定として記録している。
  `corpus/baseline/` は単一のスナップショットであり、Windows/MSVCジョブの `run-corpus` 出力と
  比較すると、エンジンに変更がなくてもlibm差でビット不一致になりうる。Windowsジョブでも検証すると、
  コード変更の有無に関わらず恒常的に赤くなる誤検出を作るため、`ubuntu-latest` のジョブに限定する
- **一致の判定方法：許容誤差付き比較（`math.isclose(rel_tol=1e-12, abs_tol=1e-12)`。
  Issue #88 で決定したビット完全一致を Issue #107 / #108 で改訂）。**
  Issue #88 実装時は、`corpus/baseline/` を生成した環境と `ubuntu-latest` 相当のLinux/gcc環境での
  実測（下記「一致の判定方法の実測（Issue #88）」）に基づき、許容誤差なしのビット完全一致を
  採用していた。その後 Issue #96 で、同一イメージ・同一入力の `ubuntu-latest` の2 run で判定が
  割れる事象が実測された。NumPy / OpenBLAS が実行時にCPUに応じて演算カーネルを選ぶことで
  ULP差（最大相対2.4e-15）が生じる機構は、ローカルの実測でCIの1値をビット単位で再現できており
  実在する（`transient_env_corr`）。ただし失敗runが実際にどのCPUで動いたかは未確認であり、
  `f0_dist`・`loudness_diff_db`の差はローカルのどの設定でも再現しておらず原因は未特定
  （`docs/adr/0008`「8.」、断定しない）。下記「一致の判定方法の実測（Issue #88）」の
  「環境差による誤検出は起きない」という結論は、ローカル1環境での実測に基づくものであり、
  #96 で覆された。この観測を踏まえ、`docs/adr/0008`「8.」(c) 3. の決定に従い、
  両側とも値を持つ指標は `math.isclose(rel_tol=1e-12, abs_tol=1e-12)` で判定するよう変更した
  （許容誤差の桁の根拠は同節参照）。片方だけが欠測の場合と、baseline側にエントリが無い場合は、
  従来どおり許容誤差なしで不一致とする。判定・比較ロジックの実装は
  `harness/baseline_freshness.py`。既存の `harness/metrics_diff.py`（`diff-corpus`）の
  `diff_metrics_vector` をそのまま転用し（二重実装を避ける）、「悪化したか」ではなく
  「値が変わっていないか」を判定する専用のラッパーとして実装した
- **CI上の「決定論の確認」はレンダWAVのバイト列で行う（Issue #108、`docs/adr/0008`「8.」(c) 4.）。**
  上記の変更により、別run（別マシン）間の指標JSONの一致は保証対象ではなくなったため、
  `.github/workflows/metrics.yml` の `Compute vectors checksum` は `renders/*.wav` のハッシュを
  用いてエンジンの決定論（`docs/02-engine-spec.md`）を確認する

### 一致の判定方法の実測（Issue #88）

`docs/adr/0008` の基準環境（Linux/gcc・`ubuntu-latest`）に近いローカル環境（Ubuntu 24.04 /
gcc 13.3.0）でエンジンをビルドし、非同梱音源を取得したうえで `run-corpus` を実行し、
コミット済みの `corpus/baseline/` と比較した。

- 鮮度が保たれていた10音源（`tibetan_singing_bowl` を除く全音源）は、両側とも欠測でない
  実数値の指標（`overall` / `segments` / `bands` / `trajectories` 合計138件）が**すべて**
  浮動小数点の等価比較（`==`）でビット単位一致した（差分ゼロ）。マイクロな丸め誤差の類は
  一切観測されなかった。欠測（`value: null`）の有無（区間境界未注釈による欠測パターン）も
  基準/今回で完全に一致した
- 唯一 `tibetan_singing_bowl` だけが大きく不一致（例：`overall.loudness_diff_db` が
  基準 `-209.59` に対し実測 `39.18`）だったが、これは許容誤差の問題ではなく**実際の陳腐化**
  だった。原因はPR #85で記録済み：Wikimediaのレート制限により当時の実行でこの音源だけ
  再取得に失敗し、既存の（`candidate_path` がレンダラ出力に差し替わる前の、無音レンダ時代の）
  baselineをやむを得ずそのまま保持していた。この不一致は、本Issueで実装した検証が実際に
  陳腐化を検出できることの実地証跡でもある（下記「動作確認」参照）
- 以上から、`corpus/baseline/` の生成環境とCI実行環境の間に、許容誤差を要するような系統的な
  数値差は実測で確認されなかった。ビット完全一致を要求しても、環境差による誤検出は起きない

**動作確認（陳腐化の検出）：** 上記の実測データそのものが「`corpus/baseline/` が意図的に
（というより実際に）古いままの状態でチェックを走らせ、失敗することを確認した」証跡である。
`tibetan_singing_bowl` の陳腐化を検出したCLI実行（`python -m harness check-baseline-freshness`）は
終了コード1を返し、不一致の指標パス（`overall.msstft` 等）と直し方（`run-corpus` の再実行手順）を
含むメッセージを出力した。本PRはこの陳腐化を修正するため、`corpus/baseline/` を全音源分
再生成してコミットした（`tibetan_singing_bowl` を含む。差分は本PRに含まれる）

**制約への対応（非同梱音源の取得失敗を誤検出にしない）：** コーパス11音源のうち8音源は非同梱で、
`corpus/fetch_and_verify.py` の取得はWikimediaのレート制限（`429 Too Many Requests`）で失敗しうる
（PR #85 で実際に発生。`tibetan_singing_bowl`）。`run-corpus` は取得できなかったエントリを
`status: "missing"` として欠測記録し、残りの処理を継続する設計に既になっている
（`harness/README.md`「コーパス実行」）。本検証は、**現在の実行で `status: "missing"` になった
エントリを比較対象から除外する**ことで、取得失敗を「baselineが古い」と誤検出しない。除外された
エントリは「一致した」とも「不一致だった」とも判定されず、単に検証対象外になる（既存の
`diff-corpus` が欠測指標を悪化判定の対象外にするのと同じ設計、`harness/metrics_diff.py`）。

**実装上の補足（`status: "error"` も同様に除外する）：** `harness/baseline_freshness.py` は、
セルフレビュー（`/code-review`）の指摘を受け、`status: "missing"` に加えて `status: "error"`
（レンダ・指標算出が例外で失敗）のエントリも比較対象から除外する。missing・errorのいずれも
出力される指標ベクトルは全欠測（`build_missing_metrics_vector`、同じ形）であり、除外しなければ
一時的なレンダ失敗を「baselineが古い」と必ず誤検出する。この不変条件の輪郭自体（Issue #86が
決定した対象は non-bundled音源の取得失敗＝missing）は変えていない：`status: "error"` は
`run-corpus` 自体の終了コード1で別途CIが失敗するため（`.github/workflows/metrics.yml`
「Run corpus」ステップ）、`ubuntu-latest` ジョブの実際の挙動（本検証に到達する前にジョブが失敗する）
はこの補足の有無で変わらない。ライブラリ関数単体の頑健性のための実装詳細として記録する。

**却下した案：**

| 案 | 却下理由 |
|---|---|
| 手動運用の継続（現状維持） | `AGENTS.md` 第8節の3回ルールの閾値（3回）を、S-006は4回で超えている。トリガー条件は事前に自ら登録したものであり、発火した以上「採らない理由」ではなく**採る理由**の提示が要る。それを示せない以上、維持は選べない |
| マージ後にCIが自動コミットする | `corpus/baseline/` をコミットして持ち回る採用理由（上記「採用理由」）は「基準の更新そのものがPRの差分として現れ、レビュアがいつ・どのPRで基準が動いたかを履歴で追える」ことだった。マージ後の自動コミットは、音に影響する変更のPRとbaseline更新のコミットを分離し、この利点を消す。レビュアはDSP変更のPRを見ても、そのPRが指標に与えた実際の影響（baseline差分）を同じPR内で確認できなくなる。加えて、自動コミットジョブ自体が非同梱音源の取得失敗等で機能しなかった場合、PRのCIのように開発者が注視する場がなく、S-006と同型の「気づかれない陳腐化」の発生箇所を移すだけになりうる |
| PRテンプレートのチェック項目にする | 今回破綻したのは、まさに `docs/04-metrics.md` に明文化された手動手順である（S-006はPR #74 / #76 / #75 / #80の4件、いずれも文書化済みの手順を見た上で見落とした）。文書化された手順が4回見落とされた実績がある以上、確認欄1つの追加がなぜ違う結果を生むのかの根拠がない |

**フェーズ2との関係：** 本決定はフェーズ2（自動フィット）着手前に解決しておく必要があるとされていた
（#86「完了条件」）。フェーズ1の消化計画（#45）はまだフェーズ2に入っておらず、本決定はその条件を
満たす形で行われている。

**スコープ外：** 本決定はQ-006（CIで指標悪化をブロックするか）に触れない。本決定が検証するのは
「`corpus/baseline/` が現在のエンジンに追いついているか」という鮮度の不変条件であり、「指標値が
良いか悪いか」の合否判定ではない。Q-006は本決定後も未解決のまま残る。

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
