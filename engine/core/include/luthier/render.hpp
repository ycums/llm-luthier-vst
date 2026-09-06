#pragma once

#include <vector>

#include "luthier/preset.hpp"

namespace luthier {

// プリセットが持つ全時系列の最大時刻と transient.duration から、レンダすべき
// 長さ（秒）を決める。
//
// v0のプリセット形式にはノート全体の長さを表す専用フィールドが無い
// （docs/06-open-questions.md Q-014、暫定の扱い：PR本文参照）。ここでは
// プリセット自身が持つ時間軸の範囲（各Timeseriesの最大t、および
// transient.duration）から決定的に導出する。
double computeRenderDurationSeconds(const Preset& preset);

// Transient / Noise 層のレンダ（docs/02、P1-09 #54、docs/adr/0009）。
//
// - `enabled == false`、または `spectral_envelope` が空のときは空ベクトル
//   （この層の寄与はゼロ）を返す。
// - `enabled == true` のときは `duration_ms` に対応する長さのノイズを返す。
//   各帯域のスペクトル包絡を linear / step で補間し、ホワイトノイズを帯域フィルタ
//   したものを振幅スケールして加算する。`gain`(dB) と `seed` はそれぞれ全体振幅・
//   ノイズの再現性に効く。exp / spline 補間は v0.1 では未実装であり、黙って線形化
//   せず PresetError で停止する（P1-09 の規約、docs/adr/0009 / Q-004）。
std::vector<double> renderTransient(const TransientLayer& layer, double sample_rate_hz);

// プリセットをレンダし、モノラル浮動小数点サンプル列（[-1,1]）を返す。
//
// 層構成（docs/02-engine-spec.md 層[3]）：
//   [1] Transient / Noise ──┐
//   [2] Harmonic / Body    ──┼──→ [3] Formant filter bank ──→ out
//                           ┘
// 実装順（P1-08/P1-09/P1-10 のスタック統合）：
//   1. Transient（P1-09）と Harmonic 加算合成（P1-08）を出力長に足し合わせる。
//   2. その加算結果に Formant filter bank（P1-10）を適用する。`formant.enabled ==
//      false` のときは applyFormant 内で入力がそのまま返る（バイパス）。
// 無効な層は出力に一切寄与しない（各層の enabled ゲート）。
//
// docs/02-engine-spec.md「実装上の制約」：
// - 決定論（同一バイナリ内で常にビットレベルで同一）。スカラーループのみで書き、
//   乱数は seed で決定的な SplitMix64 のみを使用（docs/adr/0006・0009）。
// - サンプルレート非依存（レンダ時指定サンプルレートで離散化する）。
std::vector<double> render(const Preset& preset, double sample_rate_hz);

// 「すべての層を無効にした場合」の参照信号（全サンプル0.0）を返す。
// 層のDSPが実装された後も、無音の基準波形（レンダエビデンスの target 側など）に
// 使えるように残す。
std::vector<double> renderSilence(const Preset& preset, double sample_rate_hz);

}  // namespace luthier