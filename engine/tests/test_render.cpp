// docs/02-engine-spec.md「実装上の制約」（決定論・サンプルレート非依存）と、
// docs/03「meta はエンジンの動作に影響してはならない」（Issue #51 完了条件）のテスト。
// 加えて、統合したレンダパイプライン（P1-08 Harmonic 加算合成 / P1-09 Transient/
// Noise、docs/adr/0007・0009）が既知のパラメータから解析的に期待される信号を出す
// ことを検証する（Issue #53/#54 完了条件・エビデンス要件 Q-013）。
//
// このファイルは、並列実装の3ブランチ（P1-08/P1-09）それぞれが独立に書き換えた
// test_render.cpp を、スタック統合（P1-09 を P1-08 に積む）の衝突解消で1つに併合した
// もの。両者のテストをすべて保持し、重複する名前（allZero ヘルパ、computeRenderDuration
// の同一テスト）は整理して統合した。

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <utility>
#include <vector>

#include <catch2/catch_approx.hpp>
#include <catch2/catch_test_macros.hpp>
#include <nlohmann/json.hpp>

#include "fixtures/preset_fixtures.hpp"
#include "luthier/preset_loader.hpp"
#include "luthier/render.hpp"

using Catch::Approx;
using luthier::computeRenderDurationSeconds;
using luthier::Interp;
using luthier::parsePreset;
using luthier::Preset;
using luthier::render;
using luthier::renderSilence;
using luthier::renderTransient;
using luthier::testfixtures::minimalValidPreset;
using luthier::Timeseries;
using luthier::TimeseriesArray;
using luthier::TimeseriesPoint;
using luthier::TransientLayer;

namespace {

// Harmonic 解析検証で使う 2π の定数。実装（render.cpp）と同じ値から導出する。
constexpr double kTwoPi = 6.283185307179586476925286766559;

// 絶対許容誤差。既知解フィクスチャとの一致を判定する。std::sin の計算は規格上
// コンパイラ（libm）間で最終桁が異なりうる（docs/02「決定論」：同一バイナリ内の
// ビット一致は保証し、コンパイラ間のビット一致は保証しない、P1-08 のPR本文）。その差
// は相対 ~1e-15 程度であり、絶対値1e-6は周波数・倍音・補間の取り違えという実装誤りと
// 明確に区別できる十分に小さい閾値である。
constexpr double kAbsTol = 1e-6;

bool allZero(const std::vector<double>& samples) {
    return std::all_of(samples.begin(), samples.end(), [](double s) { return s == 0.0; });
}

bool anyNonZero(const std::vector<double>& s) {
    for (double x : s)
        if (x != 0.0) return true;
    return false;
}

double segmentRms(const std::vector<double>& s, std::size_t begin, std::size_t end) {
    if (end <= begin || end > s.size()) return 0.0;
    double acc = 0.0;
    for (std::size_t i = begin; i < end; ++i) acc += s[i] * s[i];
    return std::sqrt(acc / static_cast<double>(end - begin));
}

// --- Harmonic テスト用のプリセット構築 ---

struct TsSpec {
    std::string interp = "linear";
    std::vector<std::pair<double, double>> pts;
};

nlohmann::json tsJson(const std::string& unit, const TsSpec& spec) {
    nlohmann::json j;
    j["unit"] = unit;
    j["interp"] = spec.interp;
    j["points"] = nlohmann::json::array();
    for (const auto& [t, v] : spec.pts) {
        nlohmann::json p;
        p["t"] = t;
        p["v"] = v;
        j["points"].push_back(p);
    }
    return j;
}

// Harmonic 層だけを意図どおりに構成したプリセット。transient は duration のみ
// レンダ長決定に使う（transient.enabled=false で出力には寄与しない）。
nlohmann::json harmonicPresetJson(const TsSpec& f0, const std::vector<TsSpec>& partials,
                                  double inharmonicity, double duration_ms) {
    nlohmann::json j = minimalValidPreset();
    j["layers"]["transient"]["enabled"] = false;
    j["layers"]["transient"]["duration"] = duration_ms;
    j["layers"]["harmonic"]["enabled"] = true;
    j["layers"]["harmonic"]["f0"] = tsJson("hz", f0);
    j["layers"]["harmonic"]["partial_amplitudes"] = nlohmann::json::array();
    for (const auto& p : partials) {
        j["layers"]["harmonic"]["partial_amplitudes"].push_back(tsJson("linear", p));
    }
    j["layers"]["harmonic"]["inharmonicity"] = inharmonicity;
    return j;
}

Timeseries dbBand(std::vector<std::pair<double, double>> pts) {
    Timeseries t;
    t.unit = "db";
    t.interp = Interp::Linear;
    for (auto& kv : pts) t.points.push_back(TimeseriesPoint{kv.first, kv.second});
    return t;
}

TransientLayer singleBandLayer(double durationMs, double gainDb, std::int64_t seed,
                               const Timeseries& env) {
    TransientLayer t;
    t.enabled = true;
    t.duration_ms = durationMs;
    t.gain_db = gainDb;
    t.seed = seed;
    t.spectral_envelope.push_back(env);
    return t;
}

// 全サンプルを解析的に期待される値と絶対誤差で照合する。`expected` は (t, n) -> double。
template <typename F>
void checkAllAgainst(const std::vector<double>& samples, double sampleRate, F expected,
                     double tol = kAbsTol) {
    REQUIRE_FALSE(samples.empty());
    for (std::size_t n = 0; n < samples.size(); ++n) {
        const double t = static_cast<double>(n) / sampleRate;
        const double want = expected(t, n);
        if (std::abs(samples[n] - want) > tol) {
            FAIL("sample " << n << " at t=" << t << ": rendered=" << samples[n]
                           << " expected=" << want);
        }
    }
}

}  // namespace

// ---------------------------------------------------------------------------
// renderSilence：参照無音の維持（P1-09）
// ---------------------------------------------------------------------------

TEST_CASE("renderSilence produces only zero samples regardless of enabled flags") {
    nlohmann::json j = minimalValidPreset();
    Preset preset = parsePreset(j);
    auto samples = renderSilence(preset, 44100.0);
    REQUIRE_FALSE(samples.empty());
    REQUIRE(allZero(samples));

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
    j["layers"]["transient"]["duration"] = 5000.0;
    Preset preset = parsePreset(j);
    REQUIRE(computeRenderDurationSeconds(preset) == 5.0);
}

// ---------------------------------------------------------------------------
// Harmonic / Body 層（加算合成、P1-08 #53）：解析検証
// ---------------------------------------------------------------------------

TEST_CASE("render of a single constant partial is a pure sine at f0") {
    const double fs = 44100.0;
    Preset preset = parsePreset(harmonicPresetJson(
        {"linear", {{0.0, 220.0}}}, {{"linear", {{0.0, 1.0}}}}, 0.0, 50.0));
    auto samples = render(preset, fs);
    checkAllAgainst(samples, fs, [&](double t, std::size_t) {
        return std::sin(kTwoPi * 220.0 * t);
    });
}

TEST_CASE("render of multiple constant partials sums their sine components") {
    const double fs = 44100.0;
    const double a1 = 0.8, a2 = 0.5;
    Preset preset = parsePreset(harmonicPresetJson(
        {"linear", {{0.0, 220.0}}},
        {{"linear", {{0.0, a1}}}, {"linear", {{0.0, a2}}}},
        0.0, 50.0));
    auto samples = render(preset, fs);
    checkAllAgainst(samples, fs, [&](double t, std::size_t) {
        return a1 * std::sin(kTwoPi * 220.0 * t) + a2 * std::sin(kTwoPi * 440.0 * t);
    });
}

TEST_CASE("render f0 tracks a time-varying f0 (glide) with instantaneous freq f0(t)") {
    const double fs = 48000.0;
    const double dur = 0.05;
    const double fStart = 220.0;
    const double fEnd = 440.0;
    Preset preset = parsePreset(harmonicPresetJson(
        {"linear", {{0.0, fStart}, {dur, fEnd}}},
        {{"linear", {{0.0, 1.0}}}},
        0.0, dur * 1000.0));
    auto samples = render(preset, fs);
    checkAllAgainst(samples, fs, [&](double t, std::size_t) {
        const double phi = kTwoPi * (fStart * t + ((fEnd - fStart) / (2.0 * dur)) * t * t);
        return std::sin(phi);
    });
}

TEST_CASE("time-varying partial amplitude alters the rendered level") {
    const double fs = 44100.0;
    const double dur = 0.05;
    Preset preset = parsePreset(harmonicPresetJson(
        {"linear", {{0.0, 220.0}}},
        {{"linear", {{0.0, 1.0}}}, {"linear", {{0.0, 0.0}, {dur, 1.0}}}},
        0.0, dur * 1000.0));
    auto samples = render(preset, fs);
    checkAllAgainst(samples, fs, [&](double t, std::size_t) {
        const double amp2 = t / dur;
        return 1.0 * std::sin(kTwoPi * 220.0 * t) + amp2 * std::sin(kTwoPi * 440.0 * t);
    });
}

TEST_CASE("inharmonicity shifts non-fundamental partials off integer multiples") {
    const double fs = 44100.0;
    const double inh = 0.5;
    Preset preset = parsePreset(harmonicPresetJson(
        {"linear", {{0.0, 220.0}}},
        {{"linear", {{0.0, 0.5}}}, {"linear", {{0.0, 0.5}}}},
        inh, 50.0));
    auto samples = render(preset, fs);
    checkAllAgainst(samples, fs, [&](double t, std::size_t) {
        const double f1 = 220.0 * 1.0 * (1.0 + inh * 0.0);
        const double f2 = 220.0 * 2.0 * (1.0 + inh * (2.0 - 1.0));
        return 0.5 * std::sin(kTwoPi * f1 * t) + 0.5 * std::sin(kTwoPi * f2 * t);
    });
}

TEST_CASE("render with harmonic disabled is silent (layer contributes nothing)") {
    nlohmann::json j = minimalValidPreset();
    j["layers"]["transient"]["enabled"] = false;
    j["layers"]["formant"]["enabled"] = false;
    j["layers"]["harmonic"]["enabled"] = false;
    Preset preset = parsePreset(j);
    auto samples = render(preset, 44100.0);
    REQUIRE_FALSE(samples.empty());
    REQUIRE(allZero(samples));
}

TEST_CASE("render is deterministic across repeated calls (same preset, same seed)") {
    Preset preset = parsePreset(minimalValidPreset());
    auto first = render(preset, 44100.0);
    auto second = render(preset, 44100.0);
    REQUIRE(first == second);
}

TEST_CASE("render sample count scales with sample rate (sample-rate independence)") {
    Preset preset = parsePreset(minimalValidPreset());
    const double durationS = computeRenderDurationSeconds(preset);
    REQUIRE(durationS > 0.0);
    for (double sr : {22050.0, 44100.0, 48000.0, 96000.0}) {
        auto samples = render(preset, sr);
        const auto expectedCount = static_cast<std::size_t>(std::llround(durationS * sr));
        REQUIRE(samples.size() == expectedCount);
    }
}

TEST_CASE("render output does not depend on meta content") {
    nlohmann::json j1 = minimalValidPreset();
    nlohmann::json j2 = minimalValidPreset();
    j2["meta"]["source"] = "totally_different_source.wav";
    j2["meta"]["generated_by"] = "some-other-tool";
    j2["meta"]["iteration"] = 42;

    Preset p1 = parsePreset(j1);
    Preset p2 = parsePreset(j2);
    REQUIRE(render(p1, 44100.0) == render(p2, 44100.0));
}

// ---------------------------------------------------------------------------
// Transient / Noise 層（P1-09 #54）
// ---------------------------------------------------------------------------

TEST_CASE("renderTransient: enabled single-band produces non-zero samples of the duration's length") {
    const auto s = renderTransient(
        singleBandLayer(3000.0, 0.0, 7, dbBand({{0.0, 0.0}, {3.0, 0.0}})), 44100.0);
    REQUIRE(s.size() == static_cast<std::size_t>(std::llround(3.0 * 44100.0)));
    REQUIRE(anyNonZero(s));
    double mean = 0.0;
    for (double v : s) mean += v;
    mean /= static_cast<double>(s.size());
    REQUIRE(std::fabs(mean) < 1e-3);
}

TEST_CASE("Transient: gain (dB) scales the output by the exact linear amplitude ratio") {
    const auto env = dbBand({{0.0, 0.0}, {3.0, 0.0}});
    auto hi = renderTransient(singleBandLayer(3000.0, 0.0, 3, env), 44100.0);
    auto lo = renderTransient(singleBandLayer(3000.0, -12.0, 3, env), 44100.0);
    REQUIRE(hi.size() == lo.size());
    REQUIRE(hi.size() == 132300u);
    const double ratio = std::pow(10.0, -12.0 / 20.0);
    for (std::size_t i = 0; i < hi.size(); ++i) {
        const double expected = hi[i] * ratio;
        REQUIRE(std::fabs(lo[i] - expected) <= 1e-6 * std::max(1.0, std::fabs(expected)));
    }
}

TEST_CASE("Transient: same seed is reproducible; different seed yields different noise") {
    auto env = dbBand({{0.0, 0.0}, {3.0, 0.0}});
    auto a1 = renderTransient(singleBandLayer(2000.0, 0.0, 0, env), 44100.0);
    auto a2 = renderTransient(singleBandLayer(2000.0, 0.0, 0, env), 44100.0);
    REQUIRE(a1 == a2);
    REQUIRE(anyNonZero(a1));

    auto b = renderTransient(singleBandLayer(2000.0, 0.0, 1, env), 44100.0);
    REQUIRE_FALSE(a1 == b);
    REQUIRE(a1.size() == b.size());
}

TEST_CASE("Transient: disabled layer contributes nothing (empty vector)") {
    TransientLayer t =
        singleBandLayer(3000.0, 0.0, 5, dbBand({{0.0, 0.0}, {3.0, 0.0}}));
    t.enabled = false;
    const auto s = renderTransient(t, 44100.0);
    REQUIRE(s.empty());
}

TEST_CASE("Transient: spectral envelope varies over time (attack ramp)") {
    const double durMs = 4000.0;
    auto env = dbBand({{0.0, -60.0}, {durMs / 1000.0, 0.0}});
    const auto s =
        renderTransient(singleBandLayer(durMs, 0.0, 11, std::move(env)), 44100.0);
    const std::size_t n = s.size();
    const std::size_t q = n / 4;
    const double earlyRms = segmentRms(s, 0, q);
    const double lateRms = segmentRms(s, 3 * q, 4 * q);
    REQUIRE(std::isfinite(earlyRms));
    REQUIRE(std::isfinite(lateRms));
    REQUIRE(lateRms > 100.0 * earlyRms);
}

TEST_CASE("Transient: multiple bands sum into the output") {
    TransientLayer t;
    t.enabled = true;
    t.duration_ms = 2000.0;
    t.gain_db = 0.0;
    t.seed = 9;
    t.spectral_envelope.push_back(dbBand({{0.0, 0.0}, {2.0, 0.0}}));
    t.spectral_envelope.push_back(dbBand({{0.0, 0.0}, {2.0, 0.0}}));
    const auto s = renderTransient(t, 44100.0);
    REQUIRE(s.size() == static_cast<std::size_t>(std::llround(2.0 * 44100.0)));
    REQUIRE(anyNonZero(s));
}

TEST_CASE("render: enabled transient contributes, disabled transient stays silent") {
    nlohmann::json j = minimalValidPreset();
    j["layers"]["harmonic"]["enabled"] = false;
    j["layers"]["formant"]["enabled"] = false;
    j["layers"]["transient"]["enabled"] = true;
    Preset enabled = parsePreset(j);

    const auto snd = render(enabled, 44100.0);
    REQUIRE_FALSE(snd.empty());
    REQUIRE(anyNonZero(snd));

    j["layers"]["transient"]["enabled"] = false;
    Preset disabled = parsePreset(j);
    const auto sil = render(disabled, 44100.0);
    REQUIRE(sil.size() == snd.size());
    REQUIRE(allZero(sil));
}

TEST_CASE("Transient: deterministic and duration-based across sample rates") {
    auto env = dbBand({{0.0, 0.0}, {0.25, -20.0}, {0.5, -3.0}});
    for (double sr : {22050.0, 44100.0, 48000.0, 96000.0}) {
        const auto s = renderTransient(singleBandLayer(500.0, 0.0, 4, env), sr);
        REQUIRE(s.size() == static_cast<std::size_t>(std::llround(0.5 * sr)));
        REQUIRE(anyNonZero(s));
    }
}