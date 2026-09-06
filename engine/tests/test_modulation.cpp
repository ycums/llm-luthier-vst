// P1-11（#56）Modulation matrix の適用（加算方式）のテスト。
//
// docs/02 は変調源（Envelope / LFO）とルーティングの存在だけを定義し、適用式
// （source 値がどう目的地のパラメータ値に作用するか）を定義していない。P1-11 では
// 次を暫定で定め、本ファイルで解析的に検証する（理由と根拠は PR 本文・Q-018）。
//
// - 加算方式: dest(t) = base(t) + mod(t), ただし mod(t) = source(t) × route.depth
// - LFO: 波形(2π·rate·t) を [-1,1] に正規化し、LFO 自身の depth を乗算
//   （→ evalModulationSource の返り値）。同期 sync はヘッドレス v0 では位相に
//   影響しない（ノートオンが常に 0.0 から始まるため）
// - Envelope: ブレークポイント列を linear 補間（範囲外は定数外挿）
// - route.curve は v0.1 では linear のみ対応。exp/step/spline は PresetError
// - transient.duration はレンダ長そのものを決めるため時間変調の対象外（PresetError）
// - 無効な層へのルートは、その層が無音（寄与ゼロ）である限り出力に影響しない

#include <cmath>
#include <cstddef>
#include <string>
#include <vector>

#include <catch2/catch_approx.hpp>
#include <catch2/catch_test_macros.hpp>
#include <nlohmann/json.hpp>

#include "fixtures/preset_fixtures.hpp"
#include "luthier/modulation.hpp"
#include "luthier/preset.hpp"
#include "luthier/preset_error.hpp"
#include "luthier/preset_loader.hpp"
#include "luthier/render.hpp"

using Catch::Approx;
using luthier::buildModulationOffsets;
using luthier::evalModulationSource;
using luthier::Interp;
using luthier::Modulation;
using luthier::ModulationSource;
using luthier::ModulationSourceEnvelope;
using luthier::ModulationSourceLfo;
using luthier::parsePreset;
using luthier::Preset;
using luthier::PresetError;
using luthier::render;
using luthier::testfixtures::minimalValidPreset;
using luthier::validateModulatableDestinations;

namespace {

constexpr double kPi = 3.14159265358979323846;

ModulationSourceLfo makeLfoSource(const std::string& id, const std::string& waveform,
                                  double rate, double depth, bool sync = false) {
    ModulationSourceLfo l;
    l.id = id;
    l.waveform = waveform;
    l.rate = rate;
    l.depth = depth;
    l.sync = sync;
    return l;
}

ModulationSourceEnvelope makeEnvSource(const std::string& id,
                                       const std::vector<std::pair<double, double>>& pts) {
    ModulationSourceEnvelope e;
    e.id = id;
    for (const auto& [t, v] : pts) e.points.push_back(luthier::TimeseriesPoint{t, v});
    return e;
}

// 1つの source と 1つの route（to は任意のパス）を持つプリセット JSON。
nlohmann::json presetWithRoute(const std::string& to,
                               const ModulationSource& src,
                               double depth, const std::string& curve) {
    nlohmann::json j = minimalValidPreset();
    nlohmann::json source;
    if (std::holds_alternative<ModulationSourceLfo>(src)) {
        const auto& l = std::get<ModulationSourceLfo>(src);
        source["id"] = l.id;
        source["type"] = "lfo";
        source["waveform"] = l.waveform;
        source["rate"] = l.rate;
        source["depth"] = l.depth;
        source["sync"] = l.sync;
    } else {
        const auto& e = std::get<ModulationSourceEnvelope>(src);
        source["id"] = e.id;
        source["type"] = "envelope";
        source["points"] = nlohmann::json::array();
        for (const auto& p : e.points) {
            nlohmann::json pt;
            pt["t"] = p.t;
            pt["v"] = p.v;
            source["points"].push_back(pt);
        }
    }
    nlohmann::json route;
    route["from"] = std::holds_alternative<ModulationSourceLfo>(src)
                        ? std::get<ModulationSourceLfo>(src).id
                        : std::get<ModulationSourceEnvelope>(src).id;
    route["to"] = to;
    route["depth"] = depth;
    route["curve"] = curve;
    nlohmann::json modulation;
    modulation["sources"] = nlohmann::json::array();
    modulation["sources"].push_back(source);
    modulation["routes"] = nlohmann::json::array();
    modulation["routes"].push_back(route);
    j["modulation"] = modulation;
    return j;
}

bool allZero(const std::vector<double>& samples) {
    for (double s : samples)
        if (s != 0.0) return false;
    return true;
}

void requireAllApprox(const std::vector<double>& samples, double sampleRate,
                      double baseFreq, double tol = 1e-6) {
    for (std::size_t n = 0; n < samples.size(); ++n) {
        const double t = static_cast<double>(n) / sampleRate;
        const double want = std::sin(2.0 * kPi * baseFreq * t);
        if (std::abs(samples[n] - want) > tol) {
            FAIL("sample " << n << " t=" << t << " got=" << samples[n]
                           << " want=" << want);
        }
    }
}

}  // namespace

// ---------------------------------------------------------------------------
// LFO / Envelope の source 評価（evalModulationSource）
// ---------------------------------------------------------------------------

TEST_CASE("LFO sine source evaluates to waveform×depth (sample-rate free)") {
    const auto src = makeLfoSource("src1", "sine", 1.0, 0.5);
    // phase = 2π·1.0·t。sin が位相の整数倍で真の 0 を返さないのは浮動小数点の自然な
    // 挙動（~1e-17）のため、0 への一致はマージン付きで判定する。
    REQUIRE(evalModulationSource(src, 0.0) == Approx(0.0).margin(1e-9));
    REQUIRE(evalModulationSource(src, 0.25) == Approx(0.5));  // sin(π/2)*0.5
    REQUIRE(evalModulationSource(src, 0.5) == Approx(0.0).margin(1e-9));   // sin(π)=0
    REQUIRE(evalModulationSource(src, 0.75) == Approx(-0.5));  // sin(3π/2)*0.5
}

TEST_CASE("LFO triangle/square/saw evaluate to expected normalised values") {
    // triangle: u∈[0,1), tri = 1 - 4·|u-0.5|, ranges [-1,1]
    const auto tri = makeLfoSource("t", "triangle", 1.0, 1.0);
    REQUIRE(evalModulationSource(tri, 0.0) == Approx(-1.0));     // tri(0)=1-4*0.5=-1
    REQUIRE(evalModulationSource(tri, 0.125) == Approx(-0.5));   // 1-4*|0.125-0.5|=1-1.5=-0.5
    REQUIRE(evalModulationSource(tri, 0.25) == Approx(0.0));     // 1-4*0.25=0
    REQUIRE(evalModulationSource(tri, 0.5) == Approx(1.0));      // 1-4*0=1

    // square: u<0.5 -> +1, else -1
    const auto sq = makeLfoSource("s", "square", 1.0, 1.0);
    REQUIRE(evalModulationSource(sq, 0.1) == Approx(1.0));
    REQUIRE(evalModulationSource(sq, 0.6) == Approx(-1.0));

    // saw: 2u-1
    const auto saw = makeLfoSource("r", "saw", 1.0, 1.0);
    REQUIRE(evalModulationSource(saw, 0.0) == Approx(-1.0));
    REQUIRE(evalModulationSource(saw, 0.5) == Approx(0.0));
}

TEST_CASE("Unknown LFO waveform throws PresetError") {
    const auto src = makeLfoSource("bad", "garbagewave", 1.0, 1.0);
    REQUIRE_THROWS_AS(evalModulationSource(src, 0.1), PresetError);
}

TEST_CASE("Envelope source linearly interpolates breakpoints with constant extrapolation") {
    const auto env = makeEnvSource("e", {{0.0, 0.0}, {1.0, 1.0}});
    REQUIRE(evalModulationSource(env, 0.0) == Approx(0.0));
    REQUIRE(evalModulationSource(env, 0.5) == Approx(0.5));
    REQUIRE(evalModulationSource(env, 1.0) == Approx(1.0));
    REQUIRE(evalModulationSource(env, 2.0) == Approx(1.0));  // constant extrapolation
    REQUIRE(evalModulationSource(env, -1.0) == Approx(0.0));
}

// ---------------------------------------------------------------------------
// buildModulationOffsets（疎行列、加算量 = source × depth）
// ---------------------------------------------------------------------------

TEST_CASE("Envelope constant source adds source×depth to the destination") {
    const auto env = makeEnvSource("e", {{0.0, 2.0}});  // constant value 2.0
    const Preset p = parsePreset(presetWithRoute("harmonic.f0", env, 0.5, "linear"));
    const auto offsets = buildModulationOffsets(p, 44100.0, 100);
    const auto it = offsets.find("harmonic.f0");
    REQUIRE(it != offsets.end());
    REQUIRE(it->second.size() == 100);
    for (double v : it->second) REQUIRE(v == Approx(1.0));  // 2.0 × 0.5
}

TEST_CASE("Offsets length matches sample_count") {
    const auto env = makeEnvSource("e", {{0.0, 1.0}});
    const Preset p = parsePreset(presetWithRoute("formant.bands[0].freq", env, 1.0, "linear"));
    const auto offsets = buildModulationOffsets(p, 48000.0, 240);
    REQUIRE(offsets.at("formant.bands[0].freq").size() == 240);
}

TEST_CASE("LFO offset is sample-rate independent (same offset at same t)") {
    const auto src = makeLfoSource("l", "sine", 4.0, 1.0);
    const Preset p = parsePreset(presetWithRoute("harmonic.inharmonicity", src, 1.0, "linear"));
    const auto off48 = buildModulationOffsets(p, 48000.0, 48000);
    const auto off96 = buildModulationOffsets(p, 96000.0, 96000);
    const auto& a = off48.at("harmonic.inharmonicity");
    const auto& b = off96.at("harmonic.inharmonicity");
    // t=0.25s, 0.5s に対応する index での変調量が一致（phase=2π·4·t なのでレート非依存）
    REQUIRE(a[0.25 * 48000] == Approx(b[0.25 * 96000]));
    REQUIRE(a[0.5 * 48000] == Approx(b[0.5 * 96000]));
}

TEST_CASE("non-linear route curve throws PresetError") {
    const auto env = makeEnvSource("e", {{0.0, 1.0}});
    const Preset p = parsePreset(presetWithRoute("harmonic.f0", env, 1.0, "exp"));
    REQUIRE_THROWS_AS(buildModulationOffsets(p, 44100.0, 10), PresetError);
}

TEST_CASE("route targeting transient.duration is rejected as non-time-modulatable") {
    const auto env = makeEnvSource("e", {{0.0, 1.0}});
    const Preset p = parsePreset(presetWithRoute("transient.duration", env, 1.0, "linear"));
    // パス自体は routing で実在と認められる（routing テスト済み）が、意味検証で弾く
    REQUIRE_THROWS_AS(validateModulatableDestinations(p), PresetError);
}

// ---------------------------------------------------------------------------
// render() への統合（加算が実際に音へ現れる）
// ---------------------------------------------------------------------------

TEST_CASE("render: constant envelope modulates harmonic f0 from 220 to 264 Hz") {
    const double fs = 44100.0;
    const auto env = makeEnvSource("e", {{0.0, 1.0}});
    // base f0 = 220Hz、envelope 定数1.0 × depth 44 = +44Hz -> 264Hz
    nlohmann::json j = presetWithRoute("harmonic.f0", env, 44.0, "linear");
    j["layers"]["transient"]["enabled"] = false;
    j["layers"]["formant"]["enabled"] = false;
    // 単一倍音（partial[0]=1.0）
    nlohmann::json amp;
    amp["unit"] = "linear";
    amp["interp"] = "linear";
    nlohmann::json pt;
    pt["t"] = 0.0;
    pt["v"] = 1.0;
    amp["points"] = nlohmann::json::array();
    amp["points"].push_back(pt);
    j["layers"]["harmonic"]["partial_amplitudes"] = nlohmann::json::array();
    j["layers"]["harmonic"]["partial_amplitudes"].push_back(amp);

    const Preset p = parsePreset(j);
    const auto samples = render(p, fs);
    REQUIRE_FALSE(samples.empty());
    requireAllApprox(samples, fs, 264.0);
}

TEST_CASE("render: modulation is deterministic across repeated calls") {
    const auto env = makeEnvSource("e", {{0.0, 1.0}});
    const Preset p = parsePreset(presetWithRoute("harmonic.inharmonicity", env, 0.2, "linear"));
    REQUIRE(render(p, 44100.0) == render(p, 44100.0));
}

TEST_CASE("render: route to a disabled layer produces no contribution (stays silent)") {
    const auto env = makeEnvSource("e", {{0.0, 1.0}});
    nlohmann::json j = presetWithRoute("harmonic.f0", env, 100.0, "linear");
    j["layers"]["transient"]["enabled"] = false;
    j["layers"]["formant"]["enabled"] = false;
    j["layers"]["harmonic"]["enabled"] = false;  // 変調先の層を無効に
    const Preset p = parsePreset(j);
    const auto samples = render(p, 44100.0);
    REQUIRE_FALSE(samples.empty());
    REQUIRE(allZero(samples));
}