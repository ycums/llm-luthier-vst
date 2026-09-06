#include "luthier/render.hpp"

#include <algorithm>
#include <cmath>

namespace luthier {
namespace {

double maxT(const Timeseries& ts, double acc) {
    for (const auto& p : ts.points) acc = std::max(acc, p.t);
    return acc;
}

double maxT(const TimeseriesArray& arr, double acc) {
    for (const auto& ts : arr) acc = maxT(ts, acc);
    return acc;
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

std::vector<double> renderSilence(const Preset& preset, double sample_rate_hz) {
    const double durationS = computeRenderDurationSeconds(preset);
    const auto sampleCount = static_cast<std::size_t>(std::llround(durationS * sample_rate_hz));
    // v0は4層のDSPを一切実装しない（Issue #51スコープ）ため、`enabled` や
    // 各パラメータの値に関わらず常に無音を返す。
    return std::vector<double>(sampleCount, 0.0);
}

}  // namespace luthier
