#include "luthier/render.hpp"

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <string>
#include <unordered_map>
#include <vector>

#include "luthier/formant.hpp"
#include "luthier/modulation.hpp"
#include "luthier/preset_error.hpp"
#include "luthier/timeseries.hpp"

namespace luthier {
namespace {

constexpr double kTwoPi = 6.283185307179586476925286766559;
constexpr double kPi = 3.14159265358979323846;

double maxT(const Timeseries& ts, double acc) {
    for (const auto& p : ts.points) acc = std::max(acc, p.t);
    return acc;
}

double maxT(const TimeseriesArray& arr, double acc) {
    for (const auto& ts : arr) acc = maxT(ts, acc);
    return acc;
}

// ---------------------------------------------------------------------------
// 共通の Timeseries 補間（docs/03「時系列の表現」）。3実装（P1-08 の sampleTimeseries /
// P1-09 の evalTimeseries / P1-10 の evaluateTimeseries）を、P1-08 で追加された
// 唯一の補間関数 `sampleTimeseries`（timeseries.hpp、linear/exp/step/spline 対応）に
// 統一する。P1-09 の「exp/spline は v0.1 では未実装 → 黙って線形化せず PresetError
// で停止する」規約（docs/adr/0009）は、層ごとの policy として呼び出し側で維持する（
// transientEnvelopeAt）。
// ---------------------------------------------------------------------------

// Transient 層のスペクトル包絡を評価する。exp / spline は v0.1 では未実装のため、
// 黙って直線近似に置き換えず PresetError で停止する（AGENTS.md 第7節、P1-09 規約）。
double transientEnvelopeAt(const Timeseries& env, double t) {
    if (env.interp != Interp::Linear && env.interp != Interp::Step) {
        throw PresetError("transient spectral_envelope: interp '" +
                          std::string(env.interp == Interp::Exp ? "exp" : "spline") +
                          "' は v0.1 では未実装（暫定では linear/step のみ。Q-004/ADR-0009）");
    }
    return sampleTimeseries(env, t);
}

// --- ノイズ層の決定論（docs/adr/0009） ---
//
// 擬似乱数生成は C++ 標準ライブラリの <random> を使わず、整数演算のみで書いた
// splitmix64 を自前実装する。標準ライブラリの分布クラスは実装間で出力が一致しないため、
// コンパイラをまたいだ再現のためには自前実装が必要（Issue #54）。ここでは「PRNG自体
// （整数64bitの列）は gcc/MSVC でビット一致する」ことを満たすため整数演算に限定する。
// 分布は振幅 [-1,1) の一様（白色雑音）。帯域フィルタ後の浮動小数は FMA 融合等で最下位
// ビットが揺れうるため、この層は「同一バイナリ／同一環境内でビット一致」の保証範囲。
// （ADR-0009）。
struct SplitMix64 {
    std::uint64_t state;
    explicit SplitMix64(std::int64_t seed) : state(static_cast<std::uint64_t>(seed)) {}

    std::uint64_t next() {
        std::uint64_t z = (state += 0x9E3779B97F4A7C15ULL);
        z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9ULL;
        z = (z ^ (z >> 27)) * 0x94D049BB133111EBULL;
        return z ^ (z >> 31);
    }

    // [-1, 1) の一様乱数（53ビット仮数）。
    double uniformMinusOneToOne() {
        const std::uint64_t mant = next() >> 11;
        return static_cast<double>(mant) * (2.0 / 9007199254740992.0) - 1.0;
    }
};

// 一極低域フィルタの係数。cutoff_hz <= 0 のときは低域フィルタなし（係数0）。
double lowpassAlpha(double cutoff_hz, double sample_rate) {
    if (cutoff_hz <= 0.0) return 0.0;
    const double w = 2.0 * kPi * cutoff_hz / sample_rate;
    return 1.0 - std::exp(-w);
}

// dB → 振幅（linear）。
double ampDb(double db) {
    return std::pow(10.0, db / 20.0);
}

// 変調オフセット列（P1-11）からの参照。オフセットが無い（nullptr またはパス不在）
// なら 0.0 を返し、加算変化量がゼロとして振る舞う。
double modOffsetAt(const ModulationOffsetMap* off, const std::string& path, std::size_t i) {
    if (off == nullptr) return 0.0;
    auto it = off->find(path);
    if (it == off->end()) return 0.0;
    return it->second[i];
}

// 層のパス名を構築する（routing.cpp のドット記法と一致）。
std::string partialPath(std::size_t k) {
    return "harmonic.partial_amplitudes[" + std::to_string(k) + "]";
}
std::string bandPath(std::size_t b, const char* field) {
    return std::string("formant.bands[") + std::to_string(b) + "]." + field;
}
std::string transientEnvPath(std::size_t b) {
    return "transient.spectral_envelope[" + std::to_string(b) + "]";
}

// Harmonic / Body 層の加算合成（docs/adr/0007、P1-08 #53）。サンプル列の長さは
// `sampleCount` で与え、`enabled == false` のときは全ゼロ（無音）。位相は「時刻tでの
// 瞬間角速度が 2π·f_k(t) に一致する」よう時間積分で累積する（滞搗則、独立した乱数・
// 履歴状態を持たず同一バイナリ内でビット決定論的）。
//
// P1-11（#56）：`mod_offsets` が nullptr でない場合、加算方式の変調を
// f0 / partial_amplitudes[k] / inharmonicity に適用する（dest = base + mod）。
std::vector<double> renderHarmonic(const HarmonicLayer& h, std::size_t sampleCount,
                                   double sample_rate_hz,
                                   const ModulationOffsetMap* mod_offsets) {
    std::vector<double> out(sampleCount, 0.0);
    if (!h.enabled) return out;
    const std::size_t numPartials = h.partial_amplitudes.size();
    if (numPartials == 0) return out;

    std::vector<double> phase(numPartials, 0.0);
    for (std::size_t n = 0; n < sampleCount; ++n) {
        const double t = static_cast<double>(n) / sample_rate_hz;
        const double tNext = static_cast<double>(n + 1) / sample_rate_hz;
        const double f0Mod = modOffsetAt(mod_offsets, "harmonic.f0", n);
        const double inhMod = modOffsetAt(mod_offsets, "harmonic.inharmonicity", n);
        const double inh = h.inharmonicity + inhMod;
        double s = 0.0;
        for (std::size_t k = 1; k <= numPartials; ++k) {
            const double f0 = sampleTimeseries(h.f0, t) + f0Mod;
            const double f0Next = sampleTimeseries(h.f0, tNext) + f0Mod;
            const double f = f0 * static_cast<double>(k) * (1.0 + inh * (static_cast<double>(k) - 1.0));
            const double fNext = f0Next * static_cast<double>(k) * (1.0 + inh * (static_cast<double>(k) - 1.0));
            double amp = sampleTimeseries(h.partial_amplitudes[k - 1], t) +
                         modOffsetAt(mod_offsets, partialPath(k - 1), n);
            s += amp * std::sin(phase[k - 1]);
            phase[k - 1] += kTwoPi * (0.5 / sample_rate_hz) * (f + fNext);
        }
        out[n] = s;
    }
    return out;
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

// Transient / Noise 層（docs/02）。帯域は 0..Nyquist を周波数等間隔で分割する
// （Q-004 の暫定値。docs/adr/0009）。帯域 b は [b/B, (b+1)/B]×Nyquist に対応し、
// 一極ローパス2段の差分（band = LP@hi - LP@lo）で帯域制限した白色ノイズに、その帯域の
// 時間変化する包絡（dB）を振幅スケールして加算する。`duration_ms` はこの層の長さ。
std::vector<double> renderTransient(const TransientLayer& layer, double sample_rate_hz,
                                    const ModulationOffsetMap* mod_offsets) {
    if (!layer.enabled || layer.spectral_envelope.empty()) return {};
    if (sample_rate_hz <= 0.0) {
        throw PresetError("transient render: sample_rate_hz は正の数である必要がある");
    }

    const double durationS = layer.duration_ms / 1000.0;
    const std::size_t n =
        static_cast<std::size_t>(std::llround(durationS * sample_rate_hz));
    if (n == 0) return {};

    const std::size_t B = layer.spectral_envelope.size();
    const double nyquist = sample_rate_hz / 2.0;

    // 帯域エッジ（周波数等間隔、Q-004 暫定）。
    std::vector<double> aLo(B), aHi(B);
    for (std::size_t b = 0; b < B; ++b) {
        aLo[b] = lowpassAlpha(static_cast<double>(b) / static_cast<double>(B) * nyquist,
                              sample_rate_hz);
        aHi[b] = lowpassAlpha(static_cast<double>(b + 1) / static_cast<double>(B) * nyquist,
                              sample_rate_hz);
    }

    std::vector<double> yHi(B, 0.0), yLo(B, 0.0);

    std::vector<double> out(n, 0.0);
    SplitMix64 rng(layer.seed);
    for (std::size_t i = 0; i < n; ++i) {
        const double x = rng.uniformMinusOneToOne();
        const double t = static_cast<double>(i) / sample_rate_hz;
        // P1-11（#56）：gain は dB 値に変調を加算してから振幅へ変換する。
        const double gainDb = layer.gain_db + modOffsetAt(mod_offsets, "transient.gain", i);
        const double gain = ampDb(gainDb);
        double acc = 0.0;
        for (std::size_t b = 0; b < B; ++b) {
            yHi[b] += aHi[b] * (x - yHi[b]);
            yLo[b] += aLo[b] * (x - yLo[b]);
            const double band = yHi[b] - yLo[b];
            const double envDb = transientEnvelopeAt(layer.spectral_envelope[b], t) +
                                 modOffsetAt(mod_offsets, transientEnvPath(b), i);
            const double amp = ampDb(envDb);
            acc += band * amp;
        }
        out[i] = acc * gain;
    }
    return out;
}

// 全レイヤーをレンダして足し合わせ、最後に Formant filter bank を通す。
std::vector<double> render(const Preset& preset, double sample_rate_hz) {
    const double durationS = computeRenderDurationSeconds(preset);
    const std::size_t sampleCount =
        static_cast<std::size_t>(std::llround(durationS * sample_rate_hz));

    // P1-11（#56）：変調オフセットを事前計算する。プリセットに modulation が無い、
    // またはルートが無い場合は空 map になり、各層でオフセット0として振る舞う。
    const ModulationOffsetMap offsets =
        buildModulationOffsets(preset, sample_rate_hz, sampleCount);
    const ModulationOffsetMap* offPtr = offsets.empty() ? nullptr : &offsets;

    // 層構成（docs/02-engine-spec.md 層[3]）：
    //   1) Transient（P1-09）+ Harmonic 加算合成（P1-08）を出力長に足し合わせる。
    //   2) 加算結果に Formant filter bank（P1-10）を適用。
    //      applyFormant は formant.enabled==false のとき入力をそのまま返す（バイパス）。
    std::vector<double> out =
        renderHarmonic(preset.harmonic, sampleCount, sample_rate_hz, offPtr);
    const std::vector<double> tr =
        renderTransient(preset.transient, sample_rate_hz, offPtr);
    const std::size_t m = std::min(tr.size(), out.size());
    for (std::size_t i = 0; i < m; ++i) out[i] += tr[i];
    return applyFormant(preset.formant, out, sample_rate_hz, offPtr);
}

std::vector<double> renderSilence(const Preset& preset, double sample_rate_hz) {
    const double durationS = computeRenderDurationSeconds(preset);
    const auto sampleCount =
        static_cast<std::size_t>(std::llround(durationS * sample_rate_hz));
    return std::vector<double>(sampleCount, 0.0);
}

}  // namespace luthier