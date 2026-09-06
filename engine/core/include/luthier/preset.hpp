#pragma once

#include <cstdint>
#include <optional>
#include <string>
#include <variant>
#include <vector>

#include <nlohmann/json.hpp>

#include "luthier/timeseries.hpp"

namespace luthier {

// Transient / Noise 層（docs/02-engine-spec.md）。
struct TransientLayer {
    bool enabled = false;
    TimeseriesArray spectral_envelope;
    double duration_ms = 0.0;
    double gain_db = 0.0;
    std::int64_t seed = 0;
};

// Harmonic / Body 層（docs/02）。合成方式（Q-001、加算合成/ウェーブテーブル）は
// 未解決のため、ここではインタフェースのみを持つ。
struct HarmonicLayer {
    bool enabled = false;
    Timeseries f0;
    TimeseriesArray partial_amplitudes;
    double inharmonicity = 0.0;
};

struct FormantBand {
    Timeseries freq;
    Timeseries q;
    Timeseries gain;
};

// Formant filter bank（docs/02）。v0はバンド数4固定。
struct FormantLayer {
    bool enabled = false;
    std::vector<FormantBand> bands;
};

struct ModulationSourceEnvelope {
    std::string id;
    std::vector<TimeseriesPoint> points;
};

struct ModulationSourceLfo {
    std::string id;
    std::string waveform;
    double rate = 0.0;
    double depth = 0.0;
    bool sync = false;
};

using ModulationSource = std::variant<ModulationSourceEnvelope, ModulationSourceLfo>;

struct ModulationRoute {
    std::string from;  // 変調源のid（modulation.sources[].id）
    std::string to;    // ドット記法のルーティング先パス（docs/03）
    double depth = 0.0;
    Interp curve = Interp::Linear;
};

// Modulation matrix（docs/02）。実体（変調の適用）はP1-11の範囲。
struct Modulation {
    std::vector<ModulationSource> sources;
    std::vector<ModulationRoute> routes;
};

// docs/03-preset-format.md が定義する中間プリセット全体。
//
// `meta` はデバッグ・トレーサビリティ専用であり、意図的に不透明な nlohmann::json
// のまま保持して他のどのフィールドからも参照しない（docs/03「meta はエンジンの
// 動作に影響してはならない」）。render()（luthier/render.hpp）のシグネチャが
// meta を一切受け取らないことで、この制約をAPIレベルで保証する。
struct Preset {
    std::string format_version;
    std::string engine_spec_version;
    nlohmann::json meta;
    TransientLayer transient;
    HarmonicLayer harmonic;
    FormantLayer formant;
    std::optional<Modulation> modulation;
};

}  // namespace luthier
