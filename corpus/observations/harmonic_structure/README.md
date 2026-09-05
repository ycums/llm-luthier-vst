# 倍音構造観測（P1-02 / Issue #47）

`python -m harness observe-harmonics --manifest corpus/manifest.json --out corpus/observations/harmonic_structure`
の出力（`harness/harmonic_observation.py`、`docs/06-open-questions.md` Q-001 の判断材料）。

`index.json` と `<id>.json`（`harness/harmonic_observation.schema.json` に valid）は
`harness.corpus_runner` の `corpus/baseline/` と同じ位置づけの生成物であり、
`AGENTS.md` 第5節「変更ファイル数」の集計対象からは除外する（Q-012の暫定の扱いと同じ扱い、
`docs/06-open-questions.md` 参照）。

## 実行結果：コーパス全11音源を観測済み

`corpus/manifest.json` の全11音源（同梱3音源 + Wikimedia Commons由来の非同梱8音源）に対し観測を実行した。
非同梱音源は `corpus/fetch_and_verify.py` で取得・SHA256検証済み（`missing_audio` は0件）。

| id | status | voiced_frame_ratio | harmonic_amplitude_motion | non_integer_partial_ratio |
| --- | --- | --- | --- | --- |
| sine_440hz | ok | 1.000 | (missing: 倍音自体が存在しない) | 0.0006 |
| white_noise | no_pitch | 0.088 | — | — |
| violin_pizzicato | ok | 0.511 | 1.036 | 0.340 |
| violin_vibrato | ok | 0.517 | 1.311 | 0.125 |
| violin_tuning | ok | 0.973 | 1.131 | 0.255 |
| crash_cymbal | no_pitch | 0.045 | — | — |
| snare_drum_rim | no_pitch | 0.000 | — | — |
| rain_light | ok | 0.204 | 0.640 | 0.065 |
| tibetan_singing_bowl | ok | 0.992 | 2.264 | 0.147 |
| kalimba_sample | ok | 0.624 | 4.248 | 0.385 |
| spoken_hello | ok | 0.491 | 0.586 | 0.055 |

要約：`ok`=8、`no_pitch`=3、`missing_audio`=0。

観測できた範囲でのメモ（**方式の決定はP1-03で行う。本PRは観測のみ**）：

- `harmonic_amplitude_motion` は音源ごとに0.59（`spoken_hello`）〜4.25（`kalimba_sample`）まで
  約7倍の開きがあり、`non_integer_partial_ratio` も0.06〜0.38まで幅がある。コーパスは
  「倍音構造の多様性」の観点で単調ではない
- `rain_light`（軽い雨音）は打楽器・ノイズ系（`white_noise` / `crash_cymbal` / `snare_drum_rim`）と異なり
  `voiced_frame_ratio`が閾値0.15を上回り`status="ok"`になった。無声音源が必ず`no_pitch`になるとは
  限らない実例として記録する
- `sine_440hz` は単一倍音のみのため`harmonic_amplitude_motion`が欠測（設計通りの挙動）

生の観測値はP1-03のQ-001判断で参照すること。
