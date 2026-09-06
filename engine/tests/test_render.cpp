// docs/02-engine-spec.md「実装上の制約」（決定論・サンプルレート非依存）と、
// docs/03「meta はエンジンの動作に影響してはならない」（Issue #51 完了条件）の
// テスト。

#include <algorithm>
#include <cmath>

#include <catch2/catch_test_macros.hpp>
#include <nlohmann/json.hpp>

#include "fixtures/preset_fixtures.hpp"
#include "luthier/preset_loader.hpp"
#include "luthier/render.hpp"

using luthier::computeRenderDurationSeconds;
using luthier::parsePreset;
using luthier::Preset;
using luthier::renderSilence;
using luthier::testfixtures::minimalValidPreset;

namespace {
bool allZero(const std::vector<double>& samples) {
    return std::all_of(samples.begin(), samples.end(), [](double s) { return s == 0.0; });
}
}  // namespace

TEST_CASE("renderSilence produces only zero samples regardless of enabled flags") {
    nlohmann::json j = minimalValidPreset();
    Preset preset = parsePreset(j);
    auto samples = renderSilence(preset, 44100.0);
    REQUIRE_FALSE(samples.empty());
    REQUIRE(allZero(samples));

    // v0は4層ともDSP未実装（Issue #51スコープ）のため、enabledをfalseにしても
    // 出力は変わらない（無音のまま）。
    j["layers"]["transient"]["enabled"] = false;
    j["layers"]["harmonic"]["enabled"] = false;
    j["layers"]["formant"]["enabled"] = false;
    Preset disabledPreset = parsePreset(j);
    auto disabledSamples = renderSilence(disabledPreset, 44100.0);
    REQUIRE(disabledSamples == samples);
}

TEST_CASE("renderSilence is deterministic across repeated calls (same preset, same seed)") {
    Preset preset = parsePreset(minimalValidPreset());
    auto first = renderSilence(preset, 44100.0);
    auto second = renderSilence(preset, 44100.0);
    REQUIRE(first == second);
}

TEST_CASE("renderSilence sample count scales with sample rate (sample-rate independence)") {
    Preset preset = parsePreset(minimalValidPreset());
    const double durationS = computeRenderDurationSeconds(preset);
    REQUIRE(durationS > 0.0);

    for (double sr : {22050.0, 44100.0, 48000.0, 96000.0}) {
        auto samples = renderSilence(preset, sr);
        const auto expectedCount = static_cast<std::size_t>(std::llround(durationS * sr));
        REQUIRE(samples.size() == expectedCount);
        REQUIRE(allZero(samples));
    }
}

TEST_CASE("renderSilence output does not depend on meta content") {
    nlohmann::json j1 = minimalValidPreset();
    nlohmann::json j2 = minimalValidPreset();
    j2["meta"]["source"] = "totally_different_source.wav";
    j2["meta"]["generated_by"] = "some-other-tool";
    j2["meta"]["iteration"] = 42;

    Preset p1 = parsePreset(j1);
    Preset p2 = parsePreset(j2);
    REQUIRE(renderSilence(p1, 44100.0) == renderSilence(p2, 44100.0));
    REQUIRE(computeRenderDurationSeconds(p1) == computeRenderDurationSeconds(p2));
}

TEST_CASE("computeRenderDurationSeconds accounts for transient.duration") {
    nlohmann::json j = minimalValidPreset();
    j["layers"]["transient"]["duration"] = 5000.0;  // 5秒。全Timeseriesはt=0のみ。
    Preset preset = parsePreset(j);
    REQUIRE(computeRenderDurationSeconds(preset) == 5.0);
}
