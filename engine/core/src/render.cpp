#include "luthier/render.hpp"

#include <algorithm>
#include <cstddef>
#include <cmath>
#include <vector>

namespace luthier {
namespace {

constexpr double kTwoPi = 6.283185307179586476925286766559;

double maxT(const Timeseries& ts, double acc) {
    for (const auto& p : ts.points) acc = std::max(acc, p.t);
    return acc;
}

double maxT(const TimeseriesArray& arr, double acc) {
    for (const auto& ts : arr) acc = maxT(ts, acc);
    return acc;
}

// Harmonic周期（倍音k）の周波数[Hz]。inharmonicity の補正式は P1-08 の
// 実装詳細として次の規約を採る（docs/adr/0007「補正式自体はP1-08の実装詳細」、
// docs/06 Q-016）：
//   freq_k = f0 * k * (1 + inharmonicity * (k - 1))
// k=1（基音）は inharmonicity の影響を受けず f0 のまま。k>=2 では
// inharmonicity>0 のとき周波数が整数倍（k*f0）から正方向へずれ、非整数次
// （inharmonic）になる。inharmonicity=0 で純調和（k*f0）に一致する。
inline double partialFrequency(const Timeseries& f0, std::size_t k, double inharmonicity, double t) {
    const double base = sampleTimeseries(f0, t);
    return base * static_cast<double>(k) * (1.0 + inharmonicity * (static_cast<double>(k) - 1.0));
}

}  // namespace

double computeRenderDurationSeconds(const Preset& preset) {
    double maxTime = 0.0;
    maxTime = maxT(preset.transient.spectral_envelope, maxTime);
    maxTime = maxT(preset.harmonic.f0, maxTime);
    maxTime = maxT(preset.harmonic.partial_amplitudes, maxTime);
    for (const auto& band : preset.formant.bands) {
        maxTime = maxT(band.freq, maxTime);
        maxTime = maxT(band.q, maxTime);
        maxTime = maxT(band.gain, maxTime);
    }
    if (preset.modulation.has_value()) {
        for (const auto& src : preset.modulation->sources) {
            if (const auto* env = std::get_if<ModulationSourceEnvelope>(&src)) {
                for (const auto& p : env->points) maxTime = std::max(maxTime, p.t);
            }
        }
    }
    const double transientDurationS = preset.transient.duration_ms / 1000.0;
    return std::max(maxTime, transientDurationS);
}

std::vector<double> render(const Preset& preset, double sample_rate_hz) {
    const double durationS = computeRenderDurationSeconds(preset);
    const std::size_t sampleCount =
        static_cast<std::size_t>(std::llround(durationS * sample_rate_hz));
    std::vector<double> out(sampleCount, 0.0);

    // P1-08はHarmonic層のみ実装（他層はP1-09/10/11、未実装の間は無音に寄与しない）。
    if (!preset.harmonic.enabled) return out;

    const HarmonicLayer& h = preset.harmonic;
    const std::size_t numPartials = h.partial_amplitudes.size();

    // 加算合成（docs/adr/0007）：各サンプルで f0 を補間してから、全倍音の
    // 正弦波を振幅 partial_amplitudes で足す。位相は「時刻tでの瞬間角速度が
    // 2π·f_k(t) に一致する」よう、時間積分で累積する（隣接サンプルの周波数を
    // 用いた梯形則で phase[k] += 2π·f_k(t)·dt）。
    //   f(t)·t を位相に直接使う（sin(2π·f·t)）と、瞬間角速度が
    //   d/dt(2π·f(t)·t)=2π(f(t)+t·f'(t)) になり、グライド時に出力の瞬間周波数が
    //   f0 タイムラインの値からオーバーシュートする（docs/02「f0=基本周波数の
    //   時間変化、グライドはここで表現する」を満たさない）。位相積分により各
    //   倍音の瞬間周波数は f_k(t) そのものになる。乱数・履歴状態を持たないため
    //   同一バイナリ内でビット決定論的。
    std::vector<double> phase(numPartials, 0.0);  // 倍音ごとの累積位相[rad]
    for (std::size_t n = 0; n < sampleCount; ++n) {
        const double t = static_cast<double>(n) / sample_rate_hz;
        const double tNext = static_cast<double>(n + 1) / sample_rate_hz;
        double s = 0.0;
        for (std::size_t k = 1; k <= numPartials; ++k) {
            const double f = partialFrequency(h.f0, k, h.inharmonicity, t);
            const double fNext = partialFrequency(h.f0, k, h.inharmonicity, tNext);
            const double amp = sampleTimeseries(h.partial_amplitudes[k - 1], t);
            s += amp * std::sin(phase[k - 1]);
            // 次のサンプルへ位相を前進（f の梯形積分。f が線形なら正確に ∫f dt の閉形式と一致）。
            phase[k - 1] += kTwoPi * (0.5 / sample_rate_hz) * (f + fNext);
        }
        out[n] = s;
    }
    return out;
}

}  // namespace luthier