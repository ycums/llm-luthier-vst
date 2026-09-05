# harness

`docs/04-metrics.md` が定義する客観評価指標を算出するハーネス本体。以降の
すべての指標実装（P0-05以降）は、この直下のモジュールを土台にする。

## モジュール

| モジュール | 内容 |
|---|---|
| `harness/audio_io.py` | 決定論的な音声I/O層。WAVの読み込み、ステレオ→モノ変換、サンプルレート一致チェック |
| `harness/cli.py` | エントリポイント（`llm-luthier-harness` / `python -m harness`） |
| `harness/fixture_gen.py` | 既知解テスト用の合成フィクチャ生成器（差が既知の音源ペアを決定論的に生成） |
| `harness/metrics.py` | 全体指標（マルチスケールスペクトル距離 / MFCC距離 / ラウドネス差）の算出（P0-05） |
| `harness/spectrogram.py` | スペクトログラム画像対の生成器（PRに添付するエビデンス。図示のみで指標算出はしない） |

## フィクチャ生成（P0-04）

既知解テスト用の音源ペアを生成する。指標の正しさを検証するための入力（Issue #4）。信号生成コードはシンセエンジンに流用しないこと。

```
python -m harness generate-fixtures --seed 0 --out <dir>
```

- `<dir>` 直下に `a_identical` / `b_gain` / `c_lowpass` / `d_noise` / `e_attack_shift` / `f_f0_glide` の6ペア（各 `target.wav` / `candidate.wav`）と `metadata.json` を生成する
- `metadata.json` に各ペアの「既知の差（dB・カットオフ・SNR・遅延・f0）」を値として記録する。テストはこの値を参照する
- 同一 `--seed` で2回実行すると全WAVとメタデータがビット単位で一致する（決定論）
- 生成物はバージョン管理に含めない。`.venv` や一時ディレクトリに生成して使う

## 全体指標の算出（P0-05）

2つのWAVパスから `docs/04-metrics.md` の全体指標（マルチスケールスペクトル距離 / MFCC距離 /
ラウドネス差）を算出し、`docs/04-metrics.schema.json` に valid な指標ベクトルJSONを標準出力に出す。

```
python -m harness metrics <target.wav> <candidate.wav>
```

- 区間別・帯域別・軌跡の各指標（P0-06〜P0-08）は本コマンドのスコープ外であり、欠測
  （`value: null` + `missing_reason`）として出力する
- 各指標の定義・ラウドネス差の符号の向き・使用したFFTサイズ集合は `harness/metrics.py` の
  docstringに書く（実装と規則の説明を分離すると乖離するため）
- 同一入力に対して2回実行した出力JSONはビット単位で一致する（決定論）

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
