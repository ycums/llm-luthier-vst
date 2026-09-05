# harness

`docs/04-metrics.md` が定義する客観評価指標を算出するハーネス本体。以降の
すべての指標実装（P0-05以降）は、この直下のモジュールを土台にする。

## モジュール

| モジュール | 内容 |
|---|---|
| `harness/audio_io.py` | 決定論的な音声I/O層。WAVの読み込み、ステレオ→モノ変換、サンプルレート一致チェック |
| `harness/cli.py` | エントリポイント（`llm-luthier-harness` / `python -m harness`） |
| `harness/fixture_gen.py` | 既知解テスト用の合成フィクチャ生成器（差が既知の音源ペアを決定論的に生成） |
| `harness/metrics.py` | 全体指標（マルチスケールスペクトル距離 / MFCC距離 / ラウドネス差、P0-05）と軌跡指標の一部（トランジェント包絡相関 / f0軌跡距離、P0-08）の算出 |
| `harness/spectrogram.py` | スペクトログラム画像対の生成器（PRに添付するエビデンス。図示のみで指標算出はしない） |
| `harness/corpus_runner.py` | コーパス全体（`corpus/manifest.json`）への指標算出の一括実行（P0-11） |
| `harness/metrics_diff.py` | `run-corpus` の出力2組（基準/今回）の機械可読な指標差分JSONの算出（P0-12b-1） |
| `harness/metrics_diff_report.py` | `metrics_diff.py` の差分JSONを人間向けのMarkdown表（変更前/変更後/差）に整形する（P0-12b-2） |

## フィクチャ生成（P0-04）

既知解テスト用の音源ペアを生成する。指標の正しさを検証するための入力（Issue #4）。信号生成コードはシンセエンジンに流用しないこと。

```
python -m harness generate-fixtures --seed 0 --out <dir>
```

- `<dir>` 直下に `a_identical` / `b_gain` / `c_lowpass` / `d_noise` / `e_attack_shift` / `f_f0_glide` の6ペア（各 `target.wav` / `candidate.wav`）と `metadata.json` を生成する
- `metadata.json` に各ペアの「既知の差（dB・カットオフ・SNR・遅延・f0）」を値として記録する。テストはこの値を参照する
- 同一 `--seed` で2回実行すると全WAVとメタデータがビット単位で一致する（決定論）
- 生成物はバージョン管理に含めない。`.venv` や一時ディレクトリに生成して使う

## 全体指標・軌跡指標の算出（P0-05 / P0-08）

2つのWAVパスから `docs/04-metrics.md` の全体指標（マルチスケールスペクトル距離 / MFCC距離 /
ラウドネス差）と、軌跡指標のうちトランジェント包絡相関・f0軌跡距離を算出し、
`docs/04-metrics.schema.json` に valid な指標ベクトルJSONを標準出力に出す。

```
python -m harness metrics <target.wav> <candidate.wav>
```

- 区間別・帯域別の各指標（P0-06 / P0-07）と、軌跡指標のうちフォルマント軌跡距離（P0-09）は
  本コマンドのスコープ外であり、欠測（`value: null` + `missing_reason`）として出力する
- f0軌跡距離は、target・candidateの双方が有声なフレームがない場合（無音・ノイズのみの入力等）
  も欠測になる。でたらめな値は返さない
- 各指標の定義・ラウドネス差の符号の向き・使用したFFTサイズ集合・f0推定アルゴリズムは
  `harness/metrics.py` のdocstringに書く（実装と規則の説明を分離すると乖離するため）
- 同一入力に対して2回実行した出力JSONはビット単位で一致する（決定論）

## コーパス実行（P0-11）

`corpus/manifest.json` の全エントリに対して指標算出を一括実行する。CIとフィッティング
ループの両方から呼ばれる唯一の入口になる（`docs/01-architecture.md`）。

```
python -m harness run-corpus --manifest corpus/manifest.json --out <dir>
```

- `<dir>` 直下に、音源1件につき1つのJSON（`<id>.json`、`docs/04-metrics.schema.json` に valid）と、
  全件を束ねるインデックス（`index.json`）を出力する
- **比較対象（candidate）のパスはマニフェストの `candidate_path` フィールドとして与える。**
  本コマンドはそのパスがどう作られたかを一切知らない
- **フェーズ0にはレンダラが存在しない**（`docs/00-vision.md` フェーズ1）。そのため現在の
  `corpus/manifest.json` の `candidate_path` は、各エントリ自身の音声パスと同じ値（自己比較。
  全指標が0になる）に設定された **seam** である。フェーズ1でレンダラが実装され次第、この値を
  レンダラ出力のパスに差し替える
- target またはcandidateの音声ファイルが手元に存在しないエントリは**欠測**として記録し、
  残りのエントリの処理を継続する（同梱不可の音源が取得できない環境でも完走する）。指標算出
  そのものが例外で失敗したエントリも同様に**失敗**として記録し、継続する。1件の失敗・欠測が
  全体を止めることはない
- 終了コードの意味：`0`＝欠測を含め全エントリが完走した（正常） / `1`＝1件以上のエントリで
  指標算出が例外により失敗した（一部失敗） / `2`＝マニフェスト自体が読めない等、実行そのものが
  成立しなかった（実行不能）
- 各音源の実行所要時間（秒）が `index.json` の該当エントリに記録される
- 音源ごとのJSON（`<id>.json`）は、同一入力に対して2回実行してもビット単位で一致する
  （決定論）。`index.json` は実測の所要時間を含むため、この値自体は実行ごとに変わる

## 指標差分（P0-12b-1 / P0-12b-2）

`run-corpus` の出力ディレクトリ2組（基準 / 今回）から、指標ごとの「前 / 後 / 差」を算出する。
`AGENTS.md` 第4節が要求する3列出力の実体。機械可読な差分JSON（`diff.json`、P0-12b-1）と、
それをPRに貼れる人間向けのMarkdown表に整形したもの（`report.md`、P0-12b-2）の両方を出力する。

```
python -m harness diff-corpus --baseline corpus/baseline --current <run-corpusの出力先> --out <dir>
```

- `<dir>` 直下に、機械可読な `diff.json` と、人間向けの `report.md` を出力する
- 基準（baseline）ディレクトリの由来（`corpus/baseline/` の取得方法）は本コマンドの関知するところではない。決定と理由は `docs/04-metrics.md`「基準（baseline）指標JSONの取得方法」（Issue #37）を参照
- `diff.json` の `targets` は、差の絶対値合計（`total_abs_diff`）が大きい順に並ぶ（#14 で図示する音源の選定に使う一覧）。`report.md` も同じ順序で音源ごとのセクションを並べる（`harness/metrics_diff_report.py` は並べ替えを行わず、`metrics_diff.py` が算出した順序をそのまま使う）
- 悪化した指標は `diff.json` では `worsened: true`、`report.md` では判定列に `⚠ 悪化` と明示される。向きは指標ごとに異なる（誤差・距離系は増加が悪化、`transient_env_corr` は減少が悪化、`loudness_diff_db` は0からの絶対距離の増加が悪化）。判定基準の詳細は `harness/metrics_diff.py` のdocstring参照
- 一方または両方が欠測の指標は、差を捏造せず `diff.json` では `diff: null`、`report.md` では前/後/差すべて「欠測」として出力し、悪化の判定対象にもしない
- 基準と今回で構成音源が異なる場合、共通するidだけを比較する。片方にしかないidは `only_in_baseline` / `only_in_current` に記録し、比較対象には含めない（`report.md` の冒頭にも一覧として表示する）
- **指標の値・悪化件数に関わらず終了コードは常に0**（`docs/06-open-questions.md` Q-006の暫定の扱いに従う）。非0を返すのは基準/今回のディレクトリ自体が読めない、または共通するidが1件もない等、算出そのものが成立しない場合のみ

## スペクトログラム画像の生成（P0-14）

音に影響する変更のPRに添付する「差分が最大だった1〜2音源のスペクトログラム画像（変更前後の対）」を生成する（`AGENTS.md` 第4節「エビデンス要件」）。

```
python -m harness spectrogram --target <before.wav> --candidate <after.wav> --out <dir> \
    --db-min -80.0 --db-max 0.0
```

- `<dir>` 直下に `target.png` / `candidate.png` /（既定で）`diff.png` と、`metadata.json`（サイドカー）を生成する
- `target.png` と `candidate.png` は、同一の周波数軸範囲（同一サンプルレートに由来）・同一の時間軸範囲・**同一のカラースケール範囲**（`--db-min`/`--db-max`）で描画される。この範囲は自動調整に任せず固定値として両方に渡すため、信号レベルが違っても2枚のスケールは食い違わない。指定した範囲は画像内のカラーバーラベルにも表示される
- `diff.png` は candidate と target の同一グリッド上での差（dB）を示す。`--no-diff` で生成を止められる
- FFTサイズ・ホップ長・窓関数（`--fft-size` / `--hop-length` / `--window`）は画像タイトルと `metadata.json` の両方に記録する。これらは図示のためだけの設定であり、`docs/06-open-questions.md` Q-004（帯域分割方式）や指標算出には一切影響しない
- target と candidate のサンプル数が異なる場合、STFTグリッドがずれるため `AudioLengthMismatchError` で停止する（暗黙のパディング・切り詰めはしない）
- 同一環境・同一入力で2回実行すると、生成される全ファイル（PNG・metadata.json）がビット単位で一致する（決定論）
- 生成物はバージョン管理に含めない。`--out` の出力先は `.gitignore` が無視する `renders/` 配下（例：`renders/spectrograms/<name>/`）を使うこと

### PRへの添付手順

1. 変更前・変更後のレンダ結果のうち、指標ベクトル差分が最大だった1〜2音源を選ぶ（選定ロジックは本ツールの範囲外。人間またはCIの手順が判断する）
2. 上記コマンドで `target.png` / `candidate.png` /（任意で）`diff.png` を生成する
3. 生成したPNGをPR本文に画像として貼り付ける（GitHubのPRエディタにドラッグ＆ドロップ、またはIssue/PRコメントの添付機能を使う）

## 音声I/Oの規則

規則の実体は `harness/audio_io.py` のdocstringに書く（実装と規則の説明を
分離すると乖離するため）。要点のみここに転記する：

- WAVは16bit整数 / 24bit整数 / 32bit浮動小数のいずれも読み込める。内部表現は
  常にfloat64に正規化する
- ステレオ→モノは**全チャンネルの算術平均**をとる。音量補正は行わない
- ターゲットと比較対象でサンプルレートが異なる場合、**暗黙にリサンプルせず
  例外（`SampleRateMismatchError`）で停止する**
- リサンプリングの実装自体は現時点でスコープ外

## テストの実行

```
pytest
```

上記コマンドが終了コード0で完走することを確認してから変更をPRにすること。
