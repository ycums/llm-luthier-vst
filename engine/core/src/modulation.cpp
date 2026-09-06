#include "luthier/modulation.hpp"

#include <cmath>
#include <string>
#include <unordered_map>
#include <variant>
#include <vector>

#include "luthier/preset_error.hpp"

namespace luthier {
namespace {

constexpr double kTwoPi = 6.283185307179586476925286766559;

// 位相 u ∈ [0,1)（1周期を正規化）に対する三角波。[−1,1]。
double triangleWave(double u) {
    return 1.0 - 4.0 * std::fabs(u - 0.5);
}

double waveformValue(const std::string& waveform, double u) {
    if (waveform == "sine") return std::sin(kTwoPi * u);
    if (waveform == "triangle") return triangleWave(u);
    if (waveform == "square") return u < 0.5 ? 1.0 : -1.0;
    if (waveform == "saw") return 2.0 * u - 1.0;
    throw PresetError("未知の LFO 波形 '" + waveform + "'（v0.1 対応: sine/triangle/square/saw、Q-018）");
}

// ルートのカーブ。v0.1 では linear のみ。それ以外は推測で埋めず PresetError で停止
// （docs/adr/0006・P1-09 の規約、Q-018）。
void requireLinearCurve(Interp curve, const std::string& to) {
    if (curve != Interp::Linear) {
        throw PresetError("route の curve は v0.1 では linear のみ対応（destination '" + to +
                          "'、Q-018）。exp/step/spline は適用式が未定義");
    }
}

}  // namespace

double evalModulationSource(const ModulationSource& src, double t) {
    return std::visit(
        [t](const auto& s) -> double {
            using T = std::decay_t<decltype(s)>;
            if constexpr (std::is_same_v<T, ModulationSourceLfo>) {
                // 位相は sample_rate に依存しない（t で決まる）。sync はヘッドレス
                // v0 ではノートオンが常に 0.0 から始まり、tempo/clock が存在しないため
                // 位相に影響しない（Q-018）。
                const double phaseU =
                    s.rate * t - std::floor(s.rate * t);  // u ∈ [0,1)
                return waveformValue(s.waveform, phaseU) * s.depth;
            } else {  // ModulationSourceEnvelope
                const auto& pts = s.points;
                const std::size_t n = pts.size();
                if (n == 0) return 0.0;  // ローダーが points>=1 を保証
                if (n == 1) return pts[0].v;
                if (t <= pts[0].t) return pts[0].v;
                if (t >= pts[n - 1].t) return pts[n - 1].v;
                std::size_t i = 0;
                while (i + 1 < n && pts[i + 1].t <= t) ++i;
                const double t0 = pts[i].t, t1 = pts[i + 1].t;
                const double span = t1 - t0;
                const double u = span > 0.0 ? (t - t0) / span : 0.0;
                return pts[i].v + (pts[i + 1].v - pts[i].v) * u;
            }
        },
        src);
}

void validateModulatableDestinations(const Preset& preset) {
    if (!preset.modulation.has_value()) return;
    for (const auto& route : preset.modulation->routes) {
        // transient.duration はレンダ長そのものを決めるパラメータであり、時間変調する
        // と「各サンプルの長さ」が不定になり正当な適用が定義できない（暫定。Q-018）。
        // routing はパス実在のみ検証するため、ここが意味検証の最後の砦。
        if (route.to == "transient.duration") {
            throw PresetError("'transient.duration' はレンダ長を決めるため時間変調の対象にできない（Q-018）");
        }
    }
}

std::unordered_map<std::string, std::vector<double>> buildModulationOffsets(
    const Preset& preset, double sample_rate_hz, std::size_t sample_count) {
    validateModulatableDestinations(preset);
    std::unordered_map<std::string, std::vector<double>> offsets;
    if (!preset.modulation.has_value() || preset.modulation->routes.empty()) return offsets;

    // 同一 destination への複数ルートの加算。source は id で引く。
    std::unordered_map<std::string, const ModulationSource*> sourcesById;
    for (const auto& src : preset.modulation->sources) {
        sourcesById.emplace(std::visit([](const auto& s) { return s.id; }, src), &src);
    }

    // route.to ごとに source×depth を加算。iteration 順は preserve（決定論のため
    // unordered_map の反復順に依存しない）。
    struct Accumulator {
        std::vector<const ModulationSource*> sources;
        std::vector<double> depths;
    };
    std::vector<std::string> destOrder;
    std::unordered_map<std::string, Accumulator> acc;
    for (const auto& route : preset.modulation->routes) {
        requireLinearCurve(route.curve, route.to);
        auto it = sourcesById.find(route.from);
        if (it == sourcesById.end()) {
            throw PresetError("存在しない変調源id '" + route.from + "' へのルート（routing で検出済みのはず）");
        }
        auto& a = acc[route.to];
        if (a.sources.empty()) destOrder.push_back(route.to);
        a.sources.push_back(it->second);
        a.depths.push_back(route.depth);
    }

    for (const auto& dest : destOrder) {
        const auto& a = acc.at(dest);
        std::vector<double> vec(sample_count, 0.0);
        for (std::size_t i = 0; i < sample_count; ++i) {
            const double t = static_cast<double>(i) / sample_rate_hz;
            double mod = 0.0;
            for (std::size_t r = 0; r < a.sources.size(); ++r) {
                mod += evalModulationSource(*a.sources[r], t) * a.depths[r];
            }
            vec[i] = mod;
        }
        offsets.emplace(dest, std::move(vec));
    }
    return offsets;
}

}  // namespace luthier