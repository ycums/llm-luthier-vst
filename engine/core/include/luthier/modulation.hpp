#pragma once

#include <cstddef>
#include <string>
#include <unordered_map>
#include <vector>

#include "luthier/preset.hpp"

namespace luthier {

// 変調源を時刻 t（秒）で評価した信号値を返す（docs/02、P1-11 #56）。
// 加算方式（docs/02 には適用式の規定がなく、P1-11 で暫定を決めた。理由は PR 本文参照）。
//
// - LFO: 波形は 2π·rate·t の位相で [-1,1] に正規化した波形値に、LFO 自身の
//   `depth` を乗算する。`rate` は Hz。`sync` はヘッドレス v0 ではノートオンが常に
//   0.0 から開始するため位相に影響しない（暫定。Q-018）。
//   波形は v0.1 では sine / triangle / square / saw をサポートし、それ以外は
//   PresetError（未知波形を黙って無視しない。docs/adr/0006・P1-09 の規約）。
// - Envelope: ブレークポイント列を linear 補間し、範囲外は端点値で定数外挿する。
//   暫定で linear のみ（Q-018）。
double evalModulationSource(const ModulationSource& src, double t);

// プリセットの全ルートについて、目的地パス→「各サンプルの加算変調量」を事前計算する
// （変調量 = 変調 source(t) × route.depth）。
//
// - 目的地パスは routing.cpp と同じドット記法（例: formant.bands[0].freq）。
// - `sample_index` は t = index / sample_rate_hz に対応する。
// - route.curve は v0.1 では linear のみ対応。それ以外（exp/step/spline）は未知の
//   作用を推測で埋めず PresetError で停止する（Q-018）。
// - 決定論・サンプルレート非依存（LFO の位相は sample_rate_hz に依存しない）。
std::unordered_map<std::string, std::vector<double>> buildModulationOffsets(
    const Preset& preset, double sample_rate_hz, std::size_t sample_count);

// 上の関数が destination のうち「時間変調を適用できない」ものを PresetError で
// 弾くための、destination パスの検証。transient.duration はレンダ長そのものを
// 決めるため、時間変調の対象にすると長さが不定になり正当な適用が定義できない
// （暫定。Q-018）。ここで弾く（routing の「パス実在」とは別の意味検証）。
void validateModulatableDestinations(const Preset& preset);

}  // namespace luthier