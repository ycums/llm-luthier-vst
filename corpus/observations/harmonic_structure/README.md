# 倍音構造観測（P1-02 / Issue #47）

`python -m harness observe-harmonics --manifest corpus/manifest.json --out corpus/observations/harmonic_structure`
の出力（`harness/harmonic_observation.py`、`docs/06-open-questions.md` Q-001 の判断材料）。

`index.json` と `<id>.json`（`harness/harmonic_observation.schema.json` に valid）は
`harness.corpus_runner` の `corpus/baseline/` と同じ位置づけの生成物であり、
`AGENTS.md` 第5節「変更ファイル数」の集計対象からは除外する（Q-012の暫定の扱いと同じ扱い、
`docs/06-open-questions.md` 参照）。

## 既知の限界：この実行環境では全音源を観測できていない

この観測は、`corpus/manifest.json` の非同梱音源（`bundled: false`、8/11音源：
`violin_pizzicato` / `violin_vibrato` / `violin_tuning` / `crash_cymbal` / `snare_drum_rim` /
`rain_light` / `tibetan_singing_bowl` / `kalimba_sample`）を Wikimedia Commons から取得できない
サンドボックス環境（ネットワークポリシーにより `upload.wikimedia.org` への到達がブロックされる）
で実行した。結果、`status="missing_audio"` になっている。

実測できたのは同梱済みの3音源（`sine_440hz` / `white_noise` / `spoken_hello`）のみである：

- `sine_440hz`：単一倍音のみの合成音。`non_integer_partial_ratio` はほぼ0（0.0006）、
  `harmonic_amplitude_motion` は倍音自体が存在しないため欠測（正しい振る舞い）
- `white_noise`：有声フレーム比率が閾値未満のため `status="no_pitch"`（音高を持たない
  音源として明示的に記録。完了条件の該当項目を満たす実例）
- `spoken_hello`：発話。`harmonic_amplitude_motion=0.586`、`non_integer_partial_ratio=0.055`

**Q-001（Harmonic層の合成方式）が求める「コーパスの倍音構造の多様性」の判断には、
打楽器・弦楽器を含む残り8音源の実測が必要である。** P1-03（Q-001の決定）に進む前に、
Wikimedia Commonsへのネットワークアクセスがある環境（ローカル開発機、または将来
CIに組み込んだ場合）で `corpus/fetch_and_verify.py` を実行してから本コマンドを再実行し、
`corpus/observations/harmonic_structure/` を更新すること。ツール自体は
`tests/test_harmonic_observation.py` でTDDに従い検証済みであり、追加の実装は不要。
