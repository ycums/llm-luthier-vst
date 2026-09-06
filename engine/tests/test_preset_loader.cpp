// docs/03-preset-format.md の構造検証（Issue #51 完了条件）のテスト。
// Red→Green→Refactor（AGENTS.md 第3節）：まずこれらの期待を先に書き、
// 実装（preset_loader.cpp）が無い/不完全な状態で意図した理由で失敗することを
// 確認してから実装した。

#include <catch2/catch_test_macros.hpp>
#include <nlohmann/json.hpp>

#include "fixtures/preset_fixtures.hpp"
#include "luthier/preset_loader.hpp"

using luthier::parsePreset;
using luthier::Preset;
using luthier::PresetError;
using luthier::testfixtures::minimalValidPreset;

TEST_CASE("parsePreset accepts a minimal valid preset") {
  nlohmann::json j = minimalValidPreset();
  Preset preset = parsePreset(j);

  REQUIRE(preset.format_version == "0.1.0");
  REQUIRE(preset.engine_spec_version == "0.1.0");
  REQUIRE(preset.transient.enabled);
  REQUIRE(preset.harmonic.enabled);
  REQUIRE(preset.formant.enabled);
  REQUIRE(preset.formant.bands.size() == 4);
  REQUIRE(preset.transient.seed == 0);
}

TEST_CASE("parsePreset rejects an unknown top-level field") {
  nlohmann::json j = minimalValidPreset();
  j["not_a_real_field"] = 1;
  REQUIRE_THROWS_AS(parsePreset(j), PresetError);
}

TEST_CASE("parsePreset rejects an unknown nested field") {
  nlohmann::json j = minimalValidPreset();
  j["layers"]["transient"]["extra_unknown_key"] = 1;
  REQUIRE_THROWS_AS(parsePreset(j), PresetError);
}

TEST_CASE("parsePreset rejects an unknown field inside a Timeseries") {
  nlohmann::json j = minimalValidPreset();
  j["layers"]["harmonic"]["f0"]["extra_unknown_key"] = 1;
  REQUIRE_THROWS_AS(parsePreset(j), PresetError);
}

TEST_CASE("parsePreset rejects a missing required top-level field") {
  nlohmann::json j = minimalValidPreset();
  j.erase("engine_spec_version");
  REQUIRE_THROWS_AS(parsePreset(j), PresetError);
}

TEST_CASE("parsePreset rejects a missing required nested field") {
  nlohmann::json j = minimalValidPreset();
  j["layers"]["harmonic"].erase("inharmonicity");
  REQUIRE_THROWS_AS(parsePreset(j), PresetError);
}

TEST_CASE("parsePreset rejects an unsupported format_version") {
  nlohmann::json j = minimalValidPreset();
  j["format_version"] = "9.9.9";
  REQUIRE_THROWS_AS(parsePreset(j), PresetError);
}

TEST_CASE("parsePreset rejects an unsupported engine_spec_version") {
  nlohmann::json j = minimalValidPreset();
  j["engine_spec_version"] = "9.9.9";
  REQUIRE_THROWS_AS(parsePreset(j), PresetError);
}

TEST_CASE("parsePreset rejects formant.bands with a count other than 4") {
  nlohmann::json j = minimalValidPreset();
  j["layers"]["formant"]["bands"].erase(3);
  REQUIRE_THROWS_AS(parsePreset(j), PresetError);
}

TEST_CASE("parsePreset accepts arbitrary meta content without validating it") {
  nlohmann::json j = minimalValidPreset();
  j["meta"]["anything_goes"] =
      "meta はエンジンの動作に影響してはならない（docs/03）";
  j["meta"]["iteration"] = 3;
  REQUIRE_NOTHROW(parsePreset(j));
}

TEST_CASE("parsePreset rejects a preset that is not a JSON object") {
  nlohmann::json j = nlohmann::json::array();
  REQUIRE_THROWS_AS(parsePreset(j), PresetError);
}

TEST_CASE("parsePreset rejects an unknown interp value") {
  nlohmann::json j = minimalValidPreset();
  j["layers"]["harmonic"]["f0"]["interp"] = "not_a_real_interp";
  REQUIRE_THROWS_AS(parsePreset(j), PresetError);
}
