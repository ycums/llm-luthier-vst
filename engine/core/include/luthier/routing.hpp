#pragma once

#include <string>

#include "luthier/preset.hpp"

namespace luthier {

// 指定したプリセット内で、ドット記法のルーティング先パス（docs/03、例：
// formant.bands[0].freq）が実在する変調可能パラメータを指すかを検証する。
// 存在しない場合は PresetError を送出する（docs/03「ルーティング先は…
// 存在しないパスもエラー」）。
//
// Modulation matrixの実体（変調の適用処理）はP1-11の範囲外であり、ここでは
// パスの実在チェックのみを行う（Issue #51 完了条件「パス解決の失敗を検出する
// 経路はここで用意する」）。
void validateRoutingPath(const Preset& preset, const std::string& path);

// preset.modulation が持つ全ルートを検証する（to のパス実在チェックに加え、
// from が modulation.sources[].id のいずれかを指すことも検証する）。
// preset.modulation が無ければ何もしない。
void validateRoutingPaths(const Preset& preset);

}  // namespace luthier
