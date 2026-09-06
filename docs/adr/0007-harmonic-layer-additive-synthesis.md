# 0007. Harmonic層の合成方式（Q-001）

- **状態**：Accepted
- **日付**：2026-09-06

## 背景

`docs/02-engine-spec.md` の Harmonic / Body 層は「v0では加算合成とウェーブテーブルのどちらを採るかを未確定とし、インタフェースだけ先に切る」状態だった（Q-001、`docs/06-open-questions.md`）。Q-001 はフェーズ1の実装着手（P1-08、Harmonic層の実装）の明示的なブロッカーである。

Q-001 は決めるために必要な観測を「コーパスの倍音構造の多様性。倍音が時間的に大きく動く音が多いなら加算が有利」と定めており、この観測は P1-02（#47）がハーネス側（`harness/harmonic_observation.py`）で実施済みである。本ADRはその観測結果に基づいて方式を決定する。観測自体は決定を含まない（#47 のスコープ外の項目）。

## 観測結果（P1-02 #47、`corpus/observations/harmonic_structure/`）

`corpus/manifest.json` 全11音源に対して観測済み（`missing_audio` 0件）。うち音高を持つ8音源（`status="ok"`）の実測値：

| id | harmonic_amplitude_motion | non_integer_partial_ratio |
|---|---|---|
| sine_440hz | missing（単一倍音のみのため算出対象外） | 0.0006 |
| spoken_hello | 0.586 | 0.055 |
| rain_light | 0.640 | 0.065 |
| violin_pizzicato | 1.036 | 0.340 |
| violin_tuning | 1.131 | 0.255 |
| violin_vibrato | 1.311 | 0.125 |
| tibetan_singing_bowl | 2.264 | 0.147 |
| kalimba_sample | 4.248 | 0.385 |

`harmonic_amplitude_motion` は「基音に対する各倍音の振幅比の変動係数（標準偏差/平均）を倍音次数間で平均した値」であり（`harness/harmonic_observation.py` docstring）、1.0 は「標準偏差が平均に等しい」水準の大きな変動を意味する。観測対象の実在楽器音源6件のうち5件（`violin_pizzicato` / `violin_tuning` / `violin_vibrato` / `tibetan_singing_bowl` / `kalimba_sample`）が1.0を超え、最大は`sine_440hz`を除く純音以外の音源中で最小の`spoken_hello`（0.586）の**7倍以上**（`kalimba_sample`、4.248）に達する。すなわちコーパスの大半の楽器音源で、倍音バランスは時間的に大きく動く。

`non_integer_partial_ratio` も0.055〜0.385の幅があり、`violin_pizzicato`（0.340）・`kalimba_sample`（0.385）・`violin_tuning`（0.255）では非整数次成分がスペクトルエネルギーの1/4〜4割弱を占める。単一の `inharmonicity` スカラー（`docs/02` 現行仕様）に対して非整数次成分の寄与が音源依存で大きく変動することを示す（この点はQ-013にも記載済みの未確定`partial_amplitudes`倍音数上限とは別の観点であり、本ADRはこの上限自体には触れない）。

これらの数値は「どちらとも言えない」ほど拮抗してはおらず、明確に一方向（加算合成）を支持する。

## 検討した案

### 案A：加算合成（採用）

- 利点：
  - 上記観測が示す「倍音バランスの大きな時間変動」を、`partial_amplitudes`（倍音ごとの独立したTimeseries、`docs/02`/`docs/03`）でそのまま表現できる。各倍音の振幅を個別に、かつ滑らかに時間変化させることが加算合成の標準的な適用範囲であり、追加の近似を要さない
  - 観測された非整数次成分比率の音源依存の大きさ（0.055〜0.385）に対し、倍音単位で独立に扱えるため、将来 `inharmonicity` を倍音次数ごとの値に拡張する（現状はスカラー、`docs/02`）際も自然に拡張できる
  - 現行の中間プリセット形式（`docs/03-preset-format.schema.json` の `harmonic_layer`：`f0` / `partial_amplitudes` / `inharmonicity`）は、追加のフィールドなしにそのまま加算合成のパラメータとして解釈できる（詳細は後述「現行インタフェースとの整合性」）
- 欠点：倍音数（`docs/03` 未確定：倍音数上限）に比例して正弦波発振器の計算コストが線形に増える。ただしv0はオフラインCLIレンダラであり（`docs/01-architecture.md`）、リアルタイム制約はフェーズ4のプラグイン化まで課されない

### 案B：ウェーブテーブル

- 利点：単一サイクル波形をあらかじめ用意しテーブル間を補間するだけで済むため、倍音構造が時間的にほぼ静的な音源では加算合成よりCPUコストが低い
- **却下理由**：ウェーブテーブル合成の効率上の利点は「フレーム間で倍音構造があまり変化しない」ことを前提にしている。今回の観測はその前提と逆で、コーパスの大半の楽器音源で倍音バランスが大きく（変動係数1.0超）動く。この条件下でウェーブテーブルに追従させるには、テーブル数を増やすかフレームごとに新規テーブルを再構築する必要があり、いずれも「事前計算した単一サイクル波形の再生」というウェーブテーブルの効率上の利点を打ち消す。さらに非整数次成分比率が音源によって0.055〜0.385と大きく変動する観測結果に対し、非整数次（inharmonic）な倍音構造を単一サイクル波形として表現するには波形自体が周期性を持たない近似（結局は加算合成に近い内部実装）を要し、ウェーブテーブル固有の利点（テーブル読み出しの単純さ）を失う

## 決定

**加算合成**（案A）を Harmonic / Body 層の合成方式として採用する。各倍音を独立した正弦波発振器として実装し、振幅は `partial_amplitudes` の対応するTimeseriesに従って時間変化させ、周波数は `f0 * k *（1 + inharmonicity補正）` とする（inharmonicity の倍音次数依存の補正式自体はP1-08の実装詳細であり、本ADRはこれを規定しない）。

観測結果が一方向を明確に支持しているため、Q-001完了条件が要求する「どちらとも言えない場合の暫定選択」には該当しない。本決定は暫定ではなく確定とする。

## 現行インタフェースとの整合性（docs/03）

`docs/03-preset-format.schema.json` の `harmonic_layer`（`f0` / `partial_amplitudes` / `inharmonicity`、`required` は変更なし）は、加算合成のパラメータとして過不足なく解釈できることを確認した：

- `f0`（Timeseries）→ 基音周波数の時間変化。加算合成の各倍音発振器の周波数導出に直接使う
- `partial_amplitudes`（倍音ごとのTimeseries配列）→ 各倍音発振器の時間変化する振幅にそのまま対応する。これはウェーブテーブル方式では本来不要なフィールド構成であり、既存のフィールド設計自体が加算合成を前提にしていたことの傍証でもある
- `inharmonicity`（scalar）→ 各倍音の理想周波数（`k * f0`）からのずれの補正係数として使う

**フィールドの追加・削除・意味変更は不要**であり、`docs/03-preset-format.md` の改訂は行わない。`harmonic_layer.description`（`docs/03-preset-format.schema.json`）中の「合成方式（加算合成 / ウェーブテーブル）はQ-001未解決のため、インタフェースだけを定義する」という記述のみが本決定により古くなるため、本PRで本ADR（0007）を参照する記述に更新する（フィールド構造・`required` は不変）。

## `format_version` / `engine_spec_version` の更新要否

**更新しない。** `docs/03-preset-format.md`「バージョニング」の表に照らすと：

- フィールドの追加・削除・意味変更・必須化はない → minor / major の条件に該当しない
- `docs/03-preset-format.schema.json` の `harmonic_layer.description` 文言の更新は「説明の修正のみ」（patch相当）に該当する。ただし `format_version` はプリセットインスタンスごとに生成側が持つ値であり、リポジトリ内に「現在の正典バージョン」を保持する場所は同スキーマファイル冒頭の説明文（「本スキーマ自体の現在のバージョンは0.1.0」）のみである。本PRはこの説明文だけを 0.1.0 → 0.1.1 に更新する。これは中間プリセットのパース・検証結果を変えない純粋な記録更新であり、既存のプリセット（`format_version: "0.1.0"` を宣言するもの）の互換性には影響しない
- `docs/02-engine-spec.md` は本文中にバージョン番号を持たないため、`engine_spec_version` に対応する更新箇所自体が存在しない。Harmonic層が公開する能力（`f0` / `partial_amplitudes` / `inharmonicity` という内部パラメータの集合、`docs/02`「DAW露出パラメータについて」）は本決定の前後で変わらず、内部の合成アルゴリズムを確定させたに留まるため、`engine_spec_version` を上げる実体的な理由がない

## スコープ外（本ADRでは決めない）

- **Harmonic層の実装。** P1-08 の範囲（`AGENTS.md` 第5節「仕様変更を含むPRでの実装変更は不可」）
- **`inharmonicity` を倍音次数ごとの値に拡張するかどうか。** 現状のスカラー表現で足りるかはP1-08実装時に判断する。観測（`non_integer_partial_ratio` の音源依存の大きさ）は将来の拡張検討材料として本ADRに記録するに留める
- **`partial_amplitudes` の倍音数上限。** `docs/03`「未確定」のまま。レンダラの存在を前提とする観測が要るためフェーズ2（Issue #48 スコープ外の記載どおり）
- **Transient層 / Formant層 / Modulation matrixの仕様改訂。** それぞれ必要が生じた時点で別ADRを起こす

## 結果

- `docs/06-open-questions.md` Q-001 を resolved（本ADR番号）にする
- `docs/02-engine-spec.md` の Harmonic / Body 層の記述を、加算合成を採用した旨に改訂する
- `docs/03-preset-format.schema.json` の `harmonic_layer.description` を本ADRを参照する記述に更新し、スキーマ自己記述バージョンを 0.1.0 → 0.1.1 とする（フィールド構造・`required` は不変）
- `docs/03-preset-format.md` は変更しない（フィールドは既に加算合成を表現できるため）
- P1-08（Harmonic層の実装）が本ADRの決定に基づいて着手可能になる
