// docs/03「ルーティング先はドット記法のパス文字列で指定する。存在しないパスも
// エラー」（Issue #51 完了条件）のテスト。

#include <catch2/catch_test_macros.hpp>
#include <nlohmann/json.hpp>

#include "fixtures/preset_fixtures.hpp"
#include "luthier/preset_loader.hpp"

using luthier::parsePreset;
using luthier::Preset;
using luthier::PresetError;
using luthier::testfixtures::minimalValidPreset;

namespace {

Preset presetWithRoute(const std::string& to) {
    nlohmann::json j = minimalValidPreset();

    nlohmann::json point;
    point["t"] = 0.0;
    point["v"] = 1.0;

    nlohmann::json source;
    source["id"] = "env1";
    source["type"] = "envelope";
    source["points"] = nlohmann::json::array();
    source["points"].push_back(point);

    nlohmann::json route;
    route["from"] = "env1";
    route["to"] = to;
    route["depth"] = 0.5;
    route["curve"] = "linear";

    nlohmann::json modulation;
    modulation["sources"] = nlohmann::json::array();
    modulation["sources"].push_back(source);
    modulation["routes"] = nlohmann::json::array();
    modulation["routes"].push_back(route);

    j["modulation"] = modulation;
    return parsePreset(j);
}

}  // namespace

TEST_CASE("valid routing destinations resolve without error") {
    REQUIRE_NOTHROW(presetWithRoute("harmonic.f0"));
    REQUIRE_NOTHROW(presetWithRoute("harmonic.inharmonicity"));
    REQUIRE_NOTHROW(presetWithRoute("harmonic.partial_amplitudes[0]"));
    REQUIRE_NOTHROW(presetWithRoute("transient.duration"));
    REQUIRE_NOTHROW(presetWithRoute("transient.gain"));
    REQUIRE_NOTHROW(presetWithRoute("transient.spectral_envelope[0]"));
    REQUIRE_NOTHROW(presetWithRoute("formant.bands[0].freq"));
    REQUIRE_NOTHROW(presetWithRoute("formant.bands[3].gain"));
    REQUIRE_NOTHROW(presetWithRoute("formant.bands[1].q"));
}

TEST_CASE("an out-of-range array index is rejected") {
    REQUIRE_THROWS_AS(presetWithRoute("formant.bands[4].freq"), PresetError);
    REQUIRE_THROWS_AS(presetWithRoute("harmonic.partial_amplitudes[5]"), PresetError);
    REQUIRE_THROWS_AS(presetWithRoute("transient.spectral_envelope[9]"), PresetError);
}

TEST_CASE("an unknown parameter name is rejected") {
    REQUIRE_THROWS_AS(presetWithRoute("harmonic.not_a_real_param"), PresetError);
    REQUIRE_THROWS_AS(presetWithRoute("nonexistent_layer.foo"), PresetError);
    REQUIRE_THROWS_AS(presetWithRoute("formant.bands[0].nonexistent"), PresetError);
}

TEST_CASE("a non-modulatable parameter is rejected") {
    // 'seed' は離散的な乱数種であり連続変調の対象ではない（PR本文参照）。
    REQUIRE_THROWS_AS(presetWithRoute("transient.seed"), PresetError);
}

TEST_CASE("an incomplete path is rejected") {
    REQUIRE_THROWS_AS(presetWithRoute("transient"), PresetError);
    REQUIRE_THROWS_AS(presetWithRoute("formant.bands[0]"), PresetError);
}

TEST_CASE("a malformed path is rejected") {
    REQUIRE_THROWS_AS(presetWithRoute("harmonic."), PresetError);
    REQUIRE_THROWS_AS(presetWithRoute(".harmonic"), PresetError);
    REQUIRE_THROWS_AS(presetWithRoute("formant.bands[abc].freq"), PresetError);
    REQUIRE_THROWS_AS(presetWithRoute("formant..bands[0].freq"), PresetError);
    REQUIRE_THROWS_AS(presetWithRoute(""), PresetError);
}

TEST_CASE("a route referencing an unknown modulation source id is rejected") {
    nlohmann::json j = minimalValidPreset();

    nlohmann::json route;
    route["from"] = "no_such_source";
    route["to"] = "harmonic.f0";
    route["depth"] = 0.5;
    route["curve"] = "linear";

    nlohmann::json modulation;
    modulation["routes"] = nlohmann::json::array();
    modulation["routes"].push_back(route);

    j["modulation"] = modulation;
    REQUIRE_THROWS_AS(parsePreset(j), PresetError);
}
