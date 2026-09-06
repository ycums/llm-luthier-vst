#include "luthier/formant.hpp"

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <string>
#include <vector>

#include "luthier/render.hpp"
#include "luthier/timeseries.hpp"

namespace luthier {
namespace {

constexpr double kPi = 3.14159265358979323846;

// TPT 状態変数バンドパス（Simper 型）。係数は毎サンプル導出できるため、時変係数の
// 急変でも状態が発散しない。中心周波数で共振ピークを持ち、中心ゲインはQに依存
// （≈Q/√2）する。docs/adr/0006「各層の信号生成はスカラーループで書く」。
struct SvfBandpass {
    double a1 = 0.0, a2 = 0.0, a3 = 0.0;
    double ic1 = 0.0, ic2 = 0.0;

    void setCoefficients(double freq_hz, double q, double sample_rate_hz) {
        // ナイキスト以上・負の freq を端にクランプして tan() の発散と極の単位円外
        // を防ぐ（現在のバンドの正しい導出範囲を超える入力に対する防御）。
        const double f = std::clamp(freq_hz, 0.0, 0.45 * sample_rate_hz);
        const double g = std::tan(kPi * f / sample_rate_hz);
        const double k = (q > 0.0) ? (1.0 / q) : 1e-6;
        a1 = 1.0 / (1.0 + g * (g + k));
        a2 = g * a1;
        a3 = g * a2;
    }

    double process(double x) {
        const double v3 = x - ic2;
        const double v1 = a1 * ic1 + a2 * v3;            // bandpass
        const double v2 = ic2 + a2 * ic1 + a3 * v3;      // lowpass（状態更新のみに使用）
        ic1 = 2.0 * v1 - ic1;
        ic2 = 2.0 * v2 - ic2;
        return v1;
    }
};

double dbToLin(double db) { return std::pow(10.0, db / 20.0); }

// P1-11（#56）：変調オフセット参照（render.cpp の modOffsetAt と同じ規約）。
double modOffsetAt(const ModulationOffsetMap* off, const std::string& path, std::size_t i) {
    if (off == nullptr) return 0.0;
    auto it = off->find(path);
    if (it == off->end()) return 0.0;
    return it->second[i];
}

std::string bandPath(std::size_t b, const char* field) {
    return std::string("formant.bands[") + std::to_string(b) + "]." + field;
}

}  // namespace

std::vector<double> applyFormant(const FormantLayer& layer,
                                 const std::vector<double>& input,
                                 double sample_rate_hz,
                                 const ModulationOffsetMap* mod_offsets) {
    if (!layer.enabled) return input;

    std::vector<SvfBandpass> state(layer.bands.size());
    const double inv_sr = (sample_rate_hz > 0.0) ? (1.0 / sample_rate_hz) : 0.0;
    std::vector<double> out(input.size(), 0.0);

    for (std::size_t i = 0; i < input.size(); ++i) {
        const double t = double(i) * inv_sr;
        double acc = 0.0;
        for (std::size_t b = 0; b < layer.bands.size(); ++b) {
            const FormantBand& band = layer.bands[b];
            // P1-11（#56）：freq/Q/gain の基底値に変調オフセットを加算する。
            const double freq = sampleTimeseries(band.freq, t) +
                                modOffsetAt(mod_offsets, bandPath(b, "freq"), i);
            const double q = sampleTimeseries(band.q, t) +
                             modOffsetAt(mod_offsets, bandPath(b, "q"), i);
            const double gainDb = sampleTimeseries(band.gain, t) +
                                  modOffsetAt(mod_offsets, bandPath(b, "gain"), i);
            state[b].setCoefficients(freq, q, sample_rate_hz);
            acc += dbToLin(gainDb) * state[b].process(input[i]);
        }
        out[i] = acc;
    }
    return out;
}

}  // namespace luthier