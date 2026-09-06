// docs/02-engine-spec.md「実装上の制約」（決定論・サンプルレート非依存）と、
// docs/03「meta はエンジンの動作に影響してはならない」（Issue #51 完了条件）の
// テスト。加えて Harmonic / Body 層（加算合成、docs/adr/0007）が、既知の
// パラメータから解析的に期待される信号（既知の倍音構成・既知のf0軌跡）を
// 出すことを検証する（Issue #53 完了条件・エビデンス要件 Q-013）。
//
// Transient / Formant 層は P1-08 では未実装（スコープ外、P1-09/P1-10）で常に
// 無音に寄与しないため、本ファイルの検証対象は Harmonic 層のみである。

#include <algorithm>
#include <cmath>
#include <cstddef>
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
using luthier::parsePreset;
using luthier::Preset;
using luthier::render;
using luthier::testfixtures::minimalValidPreset;

namespace {

// 解析検証で使う 2π の定数。実装（render.cpp）と同じ値から導出する。
constexpr double kTwoPi = 6.283185307179586476925286766559;

// 絶対許容誤差。既知解フィクスチャとの一致を判定する。
// std::sin の計算は規格上コンパイラ（libm）間で最終桁が異なることがありうる
// （docs/02「決定論」の範囲：同一バイナリ内のビット一致は保証し、コンパイラ間
// のビット一致は保証しない、P1-08 のPR本文を参照）。その差は相対 ~1e-15 程度
// であり、絶対値1e-6は周波数・倍音・補間の取り違えという実装誤りとは明確に
// 区別できる十分に小さい閾値である。
constexpr double kAbsTol = 1e-6;

// テスト用プリセット部品（Timeseriesの仕様）。
struct TsSpec {
    std::string interp = "linear";
    std::vector<std::pair<double, double>> pts;  // (t, v)
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

// Harmonic 層だけを意図どおりに構成したプリセット。transient / formant は
// 未実装層のため duration のみレンダ長決定に使う（transient.enabled=false で
// 出力には寄与しないことを別テストで保証）。
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

// 全サンプルを解析的に期待される値と絶対誤差で照合する。
// `expected` は (t, n) -> double の関数。
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

bool allZero(const std::vector<double>& samples) {
    return std::all_of(samples.begin(), samples.end(), [](double s) { return s == 0.0; });
}

}  // namespace

// ---------------------------------------------------------------------------
// 基本：単一倍音の純正弦波（定数 f0・定数振幅）
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

// ---------------------------------------------------------------------------
// 倍音構成：N倍音の和が、各倍音の振幅比どおりに合成される
// ---------------------------------------------------------------------------
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

// ---------------------------------------------------------------------------
// f0 が時系列として効く（グライド）：線形グライドが正しく追従する
// ---------------------------------------------------------------------------
TEST_CASE("render f0 tracks a time-varying f0 (glide) with instantaneous freq f0(t)") {
    // 位相の独立な解析期待値：f0 が時間変化するとき出力の瞬間周波数は f0(t) に
    // 一致すべきであり、位相φ(t) = 2π·∫₀ᵗ f(τ)dτ で表される。
    //   線形グライド f(τ) = f0s + (f0e - f0s)·τ/dur の ∫ の閉形式は
    //   φ(t) = 2π·( f0s·t + (f0e-f0s)/(2·dur)·t² )
    // である（単なる f(t)·t ではなく、その積分）。これは実装（位相積分）とは独立に
    // 解析的に導ける式であり、実装式 sin(2π·f(t)·t) を複写していない。
    //   ※f(t)·t を位相に入れた誤実装は、瞬間周波数が f(t)+t·f'(t) へオーバー
    //   シュートし、本期待値と最大 ~2.0 差で乖離するため、本テストは確実に検出する。
    const double fs = 48000.0;
    const double dur = 0.05;  // transient.duration と一致
    const double fStart = 220.0;
    const double fEnd = 440.0;
    Preset preset = parsePreset(harmonicPresetJson(
        {"linear", {{0.0, fStart}, {dur, fEnd}}},
        {{"linear", {{0.0, 1.0}}}},
        0.0, dur * 1000.0));
    auto samples = render(preset, fs);
    checkAllAgainst(samples, fs, [&](double t, std::size_t) {
        // 位相積分の閉形式：φ(t) = 2π·( fStart·t + (fEnd-fStart)/(2·dur)·t² )
        const double phi = kTwoPi * (fStart * t + ((fEnd - fStart) / (2.0 * dur)) * t * t);
        return std::sin(phi);
    });
}

// ---------------------------------------------------------------------------
// partial_amplitudes が時系列×N倍音で効く：時間変化する振幅比
// ---------------------------------------------------------------------------
TEST_CASE("time-varying partial amplitude alters the rendered level") {
    const double fs = 44100.0;
    const double dur = 0.05;
    // 1倍音は一定（定常）、2倍音は t=0 で振幅0 → t=dur で1.0 へ線形増加。
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

// ---------------------------------------------------------------------------
// inharmonicity：非整数次倍数のずれ（スカラー補正係数）
// ---------------------------------------------------------------------------
TEST_CASE("inharmonicity shifts non-fundamental partials off integer multiples") {
    const double fs = 44100.0;
    const double inh = 0.5;
    // 倍音2: 理想 2*f0=440Hz → 補正後 2*f0*(1+inh*(2-1)) = 660Hz。
    Preset preset = parsePreset(harmonicPresetJson(
        {"linear", {{0.0, 220.0}}},
        {{"linear", {{0.0, 0.5}}}, {"linear", {{0.0, 0.5}}}},
        inh, 50.0));
    auto samples = render(preset, fs);
    checkAllAgainst(samples, fs, [&](double t, std::size_t) {
        const double f1 = 220.0 * 1.0 * (1.0 + inh * 0.0);             // = 220
        const double f2 = 220.0 * 2.0 * (1.0 + inh * (2.0 - 1.0));    // = 660
        return 0.5 * std::sin(kTwoPi * f1 * t) + 0.5 * std::sin(kTwoPi * f2 * t);
    });
}

// ---------------------------------------------------------------------------
// 決定論・サンプルレート非依存・enabled=false・meta非依存
// ---------------------------------------------------------------------------
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

TEST_CASE("computeRenderDurationSeconds accounts for transient.duration") {
    nlohmann::json j = minimalValidPreset();
    j["layers"]["transient"]["duration"] = 5000.0;  // 5秒。全Timeseriesはt=0のみ。
    Preset preset = parsePreset(j);
    REQUIRE(computeRenderDurationSeconds(preset) == 5.0);
}