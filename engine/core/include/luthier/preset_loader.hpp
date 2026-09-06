#pragma once

#include <filesystem>

#include <nlohmann/json.hpp>

#include "luthier/preset.hpp"
#include "luthier/preset_error.hpp"

namespace luthier {

// docs/03-preset-format.md の構造検証（必須フィールド・未知フィールド不許可・
// 型・format_version/engine_spec_versionの対応チェック・ルーティング先パスの
// 実在チェック）を行い、Presetを返す。検証に失敗した場合は必ず PresetError を
// 送出する（黙って無視しない、docs/03）。
Preset parsePreset(const nlohmann::json& j);

// ファイルを読み込んで parsePreset を呼ぶ。ファイルが開けない・JSONとして
// パースできない場合も PresetError を送出する。
Preset loadPreset(const std::filesystem::path& path);

}  // namespace luthier
