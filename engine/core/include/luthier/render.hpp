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

// docs/02-engine-spec.md「実装上の制約」：レンダは決定論的、かつサンプルレート
// 非依存であること。本Issue（P1-06 #51）は4層のDSPを一切実装しない
// （スコープ外：P1-08/P1-09/P1-10/P1-11）ため、`enabled` の値やパラメータの
// 値に関わらず、返る全サンプルは常に0.0（無音）である。
std::vector<double> renderSilence(const Preset& preset, double sample_rate_hz);

}  // namespace luthier
