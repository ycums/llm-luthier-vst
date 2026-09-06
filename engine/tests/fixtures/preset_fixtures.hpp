#pragma once

// テスト専用のプリセットJSONフィクスチャ。
// `tests/test_preset_schema.py` の `_minimal_valid_preset()`（P1-04スキーマ側の
// テスト）と同じ構造を保ち、スキーマ側とエンジン側の検証がずれていないことを
// 両者で担保する。

#include <string>

#include <nlohmann/json.hpp>

namespace luthier::testfixtures {

inline nlohmann::json constantTimeseries(const std::string& unit, double value) {
    nlohmann::json ts;
    ts["unit"] = unit;
    ts["interp"] = "linear";
    nlohmann::json point;
    point["t"] = 0.0;
    point["v"] = value;
    ts["points"] = nlohmann::json::array();
    ts["points"].push_back(point);
    return ts;
}

inline nlohmann::json formantBand(double freqHz, double q, double gainDb) {
    nlohmann::json band;
    band["freq"] = constantTimeseries("hz", freqHz);
    band["q"] = constantTimeseries("linear", q);
    band["gain"] = constantTimeseries("db", gainDb);
    return band;
}

inline nlohmann::json minimalValidPreset() {
    nlohmann::json transient;
    transient["enabled"] = true;
    transient["gain"] = -6.0;
    transient["duration"] = 20.0;
    transient["seed"] = 0;
    transient["spectral_envelope"] = nlohmann::json::array();
    transient["spectral_envelope"].push_back(constantTimeseries("db", 0.0));

    nlohmann::json harmonic;
    harmonic["enabled"] = true;
    harmonic["f0"] = constantTimeseries("hz", 220.0);
    harmonic["partial_amplitudes"] = nlohmann::json::array();
    harmonic["partial_amplitudes"].push_back(constantTimeseries("linear", 1.0));
    harmonic["inharmonicity"] = 0.0;

    nlohmann::json formant;
    formant["enabled"] = true;
    formant["bands"] = nlohmann::json::array();
    formant["bands"].push_back(formantBand(500.0, 5.0, 0.0));
    formant["bands"].push_back(formantBand(1500.0, 5.0, 0.0));
    formant["bands"].push_back(formantBand(2500.0, 5.0, 0.0));
    formant["bands"].push_back(formantBand(3500.0, 5.0, 0.0));

    nlohmann::json layers;
    layers["transient"] = transient;
    layers["harmonic"] = harmonic;
    layers["formant"] = formant;

    nlohmann::json preset;
    preset["format_version"] = "0.1.0";
    preset["engine_spec_version"] = "0.1.0";
    preset["layers"] = layers;
    return preset;
}

}  // namespace luthier::testfixtures
