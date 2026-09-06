#pragma once

#include <vector>

#include "luthier/preset.hpp"

namespace luthier {

// Formant filter bank（docs/02-engine-spec.md 層[3]、Issue #55）。
//
// Transient層とHarmonic層の加算結果（input）を受け取り、4バンドそれぞれの
// freq / Q / gain（いずれも時系列）からサンプル毎に共振フィルタの係数を導出して
// 処理し、全バンドの出力の和を返す（並列バンク。直列カスケードではなく各バンドが
// 独立に周波数・帯域幅・ゲインで効く。判断と根拠はPR本文参照）。
//
// - enabled が false のときは入力をそのまま返す（バイパス）。
// - freq / Q / gain はサンプル時刻 t = i / sample_rate_hz で evaluateTimeseries により
//   評価する（docs/03「時系列の表現」）。係数は毎サンプル再導出するため、時変係数の
//   急変でも発散しない（docs/adr/0006「係数もサンプルレートを引数として都度導出」）。
std::vector<double> applyFormant(const FormantLayer& layer,
                                 const std::vector<double>& input,
                                 double sample_rate_hz);

}  // namespace luthier