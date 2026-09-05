# harness

`docs/04-metrics.md` が定義する客観評価指標を算出するハーネス本体。以降の
すべての指標実装（P0-05以降）は、この直下のモジュールを土台にする。

## モジュール

| モジュール | 内容 |
|---|---|
| `harness/audio_io.py` | 決定論的な音声I/O層。WAVの読み込み、ステレオ→モノ変換、サンプルレート一致チェック |
| `harness/cli.py` | エントリポイント（`llm-luthier-harness` / `python -m harness`） |
| `harness/fixture_gen.py` | 既知解テスト用の合成フィクチャ生成器（差が既知の音源ペアを決定論的に生成） |

## フィクチャ生成（P0-04）

既知解テスト用の音源ペアを生成する。指標の正しさを検証するための入力（Issue #4）。信号生成コードはシンセエンジンに流用しないこと。

```
python -m harness generate-fixtures --seed 0 --out <dir>
```

- `<dir>` 直下に `a_identical` / `b_gain` / `c_lowpass` / `d_noise` / `e_attack_shift` / `f_f0_glide` の6ペア（各 `target.wav` / `candidate.wav`）と `metadata.json` を生成する
- `metadata.json` に各ペアの「既知の差（dB・カットオフ・SNR・遅延・f0）」を値として記録する。テストはこの値を参照する
- 同一 `--seed` で2回実行すると全WAVとメタデータがビット単位で一致する（決定論）
- 生成物はバージョン管理に含めない。`.venv` や一時ディレクトリに生成して使う

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
