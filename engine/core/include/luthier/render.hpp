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

// プリセットをレンダし、モノラル浮動小数点サンプル列（[-1,1]）を返す。
//
// P1-08（#53）は Harmonic / Body 層（加算合成、docs/adr/0007）のみを実装した。
// Transient / Formant 層と Modulation matrix は P1-09 / P1-10 / P1-11 の
// スコープで、この時点では出力に一切寄与しない（無音）。したがって
// `harmonic.enabled == true` のときのみ Harmonic 層の加算合成出力を含み、
// それ以外は 0.0（無音）になる。
//
// docs/02-engine-spec.md「実装上の制約」：
// - 決定論（同一バイナリ内で常にビットレベルで同一）。スカラーループのみで
//   書き、乱数を使わない（docs/adr/0006「決定論・サンプルレート非依存の担保」）。
//   コンパイラ間のビット一致は保証しない範囲についてはP1-08のPR本文に明記。
// - サンプルレート非依存（レンダ時指定サンプルレートで離散化する）。
std::vector<double> render(const Preset& preset, double sample_rate_hz);

}  // namespace luthier