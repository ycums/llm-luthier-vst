#include "luthier/preset_loader.hpp"

#include <cstdint>
#include <fstream>
#include <sstream>
#include <unordered_set>
#include <vector>

#include "luthier/routing.hpp"

namespace luthier {
namespace {

using nlohmann::json;

[[noreturn]] void fail(const std::string &ctx, const std::string &what) {
  throw PresetError(ctx + ": " + what);
}

void requireObject(const json &j, const std::string &ctx) {
  if (!j.is_object())
    fail(ctx, "オブジェクトである必要がある");
}

// docs/03「未知のフィールドを見つけたエンジンはエラーで停止する」の実装。
// `docs/03-preset-format.schema.json` の additionalProperties:false
// に対応する。
void rejectUnknownKeys(const json &obj,
                       const std::unordered_set<std::string> &allowed,
                       const std::string &ctx) {
  for (auto it = obj.begin(); it != obj.end(); ++it) {
    if (!allowed.contains(it.key())) {
      fail(ctx, "未知のフィールド '" + it.key() +
                    "' を検出した（docs/03: 黙って無視しない）");
    }
  }
}

void requireKeys(const json &obj, const std::vector<std::string> &required,
                 const std::string &ctx) {
  for (const auto &k : required) {
    if (!obj.contains(k))
      fail(ctx, "必須フィールド '" + k + "' がない");
  }
}

const json &field(const json &obj, const std::string &key,
                  const std::string &ctx) {
  auto it = obj.find(key);
  if (it == obj.end())
    fail(ctx, "必須フィールド '" + key + "' がない");
  return *it;
}

std::string requireString(const json &obj, const std::string &key,
                          const std::string &ctx) {
  const json &v = field(obj, key, ctx);
  if (!v.is_string())
    fail(ctx, "'" + key + "' は文字列である必要がある");
  return v.get<std::string>();
}

double requireNumber(const json &obj, const std::string &key,
                     const std::string &ctx) {
  const json &v = field(obj, key, ctx);
  if (!v.is_number())
    fail(ctx, "'" + key + "' は数値である必要がある");
  return v.get<double>();
}

bool requireBool(const json &obj, const std::string &key,
                 const std::string &ctx) {
  const json &v = field(obj, key, ctx);
  if (!v.is_boolean())
    fail(ctx, "'" + key + "' は真偽値である必要がある");
  return v.get<bool>();
}

std::int64_t requireInt(const json &obj, const std::string &key,
                        const std::string &ctx) {
  const json &v = field(obj, key, ctx);
  if (!v.is_number_integer())
    fail(ctx, "'" + key + "' は整数である必要がある");
  return v.get<std::int64_t>();
}

const json &requireArray(const json &obj, const std::string &key,
                         const std::string &ctx) {
  const json &v = field(obj, key, ctx);
  if (!v.is_array())
    fail(ctx, "'" + key + "' は配列である必要がある");
  return v;
}

Interp parseInterp(const std::string &s, const std::string &ctx) {
  if (s == "linear")
    return Interp::Linear;
  if (s == "exp")
    return Interp::Exp;
  if (s == "step")
    return Interp::Step;
  if (s == "spline")
    return Interp::Spline;
  fail(ctx, "未知の interp/curve 値 '" + s +
                "'（linear/exp/step/splineのいずれか）");
}

TimeseriesPoint parseTimeseriesPoint(const json &j, const std::string &ctx) {
  requireObject(j, ctx);
  rejectUnknownKeys(j, {"t", "v"}, ctx);
  requireKeys(j, {"t", "v"}, ctx);
  TimeseriesPoint p;
  p.t = requireNumber(j, "t", ctx);
  if (p.t < 0.0)
    fail(ctx, "'t' は0以上である必要がある");
  p.v = requireNumber(j, "v", ctx);
  return p;
}

Timeseries parseTimeseries(const json &j, const std::string &ctx) {
  requireObject(j, ctx);
  rejectUnknownKeys(j, {"unit", "interp", "points"}, ctx);
  requireKeys(j, {"unit", "interp", "points"}, ctx);
  Timeseries ts;
  ts.unit = requireString(j, "unit", ctx);
  if (ts.unit.empty())
    fail(ctx, "'unit' は空文字列であってはならない");
  ts.interp = parseInterp(requireString(j, "interp", ctx), ctx + ".interp");
  const json &pts = requireArray(j, "points", ctx);
  if (pts.empty())
    fail(ctx, "'points' は1個以上必要");
  for (std::size_t i = 0; i < pts.size(); ++i) {
    ts.points.push_back(parseTimeseriesPoint(
        pts[i], ctx + ".points[" + std::to_string(i) + "]"));
  }
  return ts;
}

TimeseriesArray parseTimeseriesArray(const json &j, const std::string &key,
                                     const std::string &ctx) {
  const json &arr = requireArray(j, key, ctx);
  if (arr.empty())
    fail(ctx, "'" + key + "' は1個以上必要");
  TimeseriesArray out;
  for (std::size_t i = 0; i < arr.size(); ++i) {
    out.push_back(parseTimeseries(arr[i], ctx + "." + key + "[" +
                                              std::to_string(i) + "]"));
  }
  return out;
}

TransientLayer parseTransient(const json &j, const std::string &ctx) {
  requireObject(j, ctx);
  rejectUnknownKeys(
      j, {"enabled", "spectral_envelope", "duration", "gain", "seed"}, ctx);
  requireKeys(j, {"enabled", "spectral_envelope", "duration", "gain", "seed"},
              ctx);
  TransientLayer t;
  t.enabled = requireBool(j, "enabled", ctx);
  t.spectral_envelope = parseTimeseriesArray(j, "spectral_envelope", ctx);
  t.duration_ms = requireNumber(j, "duration", ctx);
  if (t.duration_ms < 0.0)
    fail(ctx, "'duration' は0以上である必要がある");
  t.gain_db = requireNumber(j, "gain", ctx);
  t.seed = requireInt(j, "seed", ctx);
  return t;
}

HarmonicLayer parseHarmonic(const json &j, const std::string &ctx) {
  requireObject(j, ctx);
  rejectUnknownKeys(j, {"enabled", "f0", "partial_amplitudes", "inharmonicity"},
                    ctx);
  requireKeys(j, {"enabled", "f0", "partial_amplitudes", "inharmonicity"}, ctx);
  HarmonicLayer h;
  h.enabled = requireBool(j, "enabled", ctx);
  h.f0 = parseTimeseries(field(j, "f0", ctx), ctx + ".f0");
  h.partial_amplitudes = parseTimeseriesArray(j, "partial_amplitudes", ctx);
  h.inharmonicity = requireNumber(j, "inharmonicity", ctx);
  return h;
}

FormantBand parseFormantBand(const json &j, const std::string &ctx) {
  requireObject(j, ctx);
  rejectUnknownKeys(j, {"freq", "q", "gain"}, ctx);
  requireKeys(j, {"freq", "q", "gain"}, ctx);
  FormantBand b;
  b.freq = parseTimeseries(field(j, "freq", ctx), ctx + ".freq");
  b.q = parseTimeseries(field(j, "q", ctx), ctx + ".q");
  b.gain = parseTimeseries(field(j, "gain", ctx), ctx + ".gain");
  return b;
}

FormantLayer parseFormant(const json &j, const std::string &ctx) {
  requireObject(j, ctx);
  rejectUnknownKeys(j, {"enabled", "bands"}, ctx);
  requireKeys(j, {"enabled", "bands"}, ctx);
  FormantLayer f;
  f.enabled = requireBool(j, "enabled", ctx);
  const json &bands = requireArray(j, "bands", ctx);
  if (bands.size() != 4) {
    fail(ctx, "'bands' はv0では4個固定（docs/02）: 実際は" +
                  std::to_string(bands.size()) + "個");
  }
  for (std::size_t i = 0; i < bands.size(); ++i) {
    f.bands.push_back(
        parseFormantBand(bands[i], ctx + ".bands[" + std::to_string(i) + "]"));
  }
  return f;
}

ModulationSource parseModulationSource(const json &j, const std::string &ctx) {
  requireObject(j, ctx);
  requireKeys(j, {"id", "type"}, ctx);
  std::string id = requireString(j, "id", ctx);
  if (id.empty())
    fail(ctx, "'id' は空文字列であってはならない");
  std::string type = requireString(j, "type", ctx);

  if (type == "envelope") {
    rejectUnknownKeys(j, {"id", "type", "points"}, ctx);
    requireKeys(j, {"points"}, ctx);
    ModulationSourceEnvelope e;
    e.id = id;
    const json &pts = requireArray(j, "points", ctx);
    if (pts.empty())
      fail(ctx, "'points' は1個以上必要");
    for (std::size_t i = 0; i < pts.size(); ++i) {
      e.points.push_back(parseTimeseriesPoint(
          pts[i], ctx + ".points[" + std::to_string(i) + "]"));
    }
    return e;
  }
  if (type == "lfo") {
    rejectUnknownKeys(j, {"id", "type", "waveform", "rate", "depth", "sync"},
                      ctx);
    requireKeys(j, {"waveform", "rate", "depth", "sync"}, ctx);
    ModulationSourceLfo l;
    l.id = id;
    l.waveform = requireString(j, "waveform", ctx);
    if (l.waveform.empty())
      fail(ctx, "'waveform' は空文字列であってはならない");
    l.rate = requireNumber(j, "rate", ctx);
    if (l.rate <= 0.0)
      fail(ctx, "'rate' は正の数である必要がある");
    l.depth = requireNumber(j, "depth", ctx);
    l.sync = requireBool(j, "sync", ctx);
    return l;
  }
  fail(ctx, "未知の modulation source type '" + type +
                "'（envelope/lfoのいずれか）");
}

ModulationRoute parseModulationRoute(const json &j, const std::string &ctx) {
  requireObject(j, ctx);
  rejectUnknownKeys(j, {"from", "to", "depth", "curve"}, ctx);
  requireKeys(j, {"from", "to", "depth", "curve"}, ctx);
  ModulationRoute r;
  r.from = requireString(j, "from", ctx);
  if (r.from.empty())
    fail(ctx, "'from' は空文字列であってはならない");
  r.to = requireString(j, "to", ctx);
  if (r.to.empty())
    fail(ctx, "'to' は空文字列であってはならない");
  r.depth = requireNumber(j, "depth", ctx);
  r.curve = parseInterp(requireString(j, "curve", ctx), ctx + ".curve");
  return r;
}

Modulation parseModulation(const json &j, const std::string &ctx) {
  requireObject(j, ctx);
  rejectUnknownKeys(j, {"sources", "routes"}, ctx);
  Modulation m;
  if (j.contains("sources")) {
    const json &srcs = requireArray(j, "sources", ctx);
    if (srcs.size() > 4)
      fail(ctx, "'sources' はv0上限4を超えている（docs/02）");
    for (std::size_t i = 0; i < srcs.size(); ++i) {
      m.sources.push_back(parseModulationSource(
          srcs[i], ctx + ".sources[" + std::to_string(i) + "]"));
    }
  }
  if (j.contains("routes")) {
    const json &routes = requireArray(j, "routes", ctx);
    if (routes.size() > 32) {
      fail(ctx,
           "'routes' はv0上限32を超えている（docs/02: source4×destination8）");
    }
    for (std::size_t i = 0; i < routes.size(); ++i) {
      m.routes.push_back(parseModulationRoute(
          routes[i], ctx + ".routes[" + std::to_string(i) + "]"));
    }
  }
  return m;
}

// v0時点でエンジンが理解できるのは docs/03 の現行版のみ（厳密一致）。
// 将来のバージョン互換ポリシー（minor後方互換の許容など）は未確定
// （docs/06-open-questions.md Q-015、PR本文参照）。
bool isSupportedVersion(const std::string &v) { return v == "0.1.0"; }

} // namespace

Preset parsePreset(const json &j) {
  const std::string ctx = "$";
  requireObject(j, ctx);
  rejectUnknownKeys(
      j,
      {"format_version", "engine_spec_version", "meta", "layers", "modulation"},
      ctx);
  requireKeys(j, {"format_version", "engine_spec_version", "layers"}, ctx);

  Preset preset;

  preset.format_version = requireString(j, "format_version", ctx);
  if (!isSupportedVersion(preset.format_version)) {
    fail(ctx, "対応していない format_version '" + preset.format_version +
                  "'（対応: 0.1.0）");
  }
  preset.engine_spec_version = requireString(j, "engine_spec_version", ctx);
  if (!isSupportedVersion(preset.engine_spec_version)) {
    fail(ctx, "対応していない engine_spec_version '" +
                  preset.engine_spec_version + "'（対応: 0.1.0）");
  }

  if (j.contains("meta")) {
    const json &meta = j.at("meta");
    if (!meta.is_object())
      fail(ctx, "'meta' はオブジェクトである必要がある");
    // 内容は一切解釈しない（docs/03「meta
    // はエンジンの動作に影響してはならない」）。
    preset.meta = meta;
  }

  const json &layersJson = field(j, "layers", ctx);
  requireObject(layersJson, ctx + ".layers");
  rejectUnknownKeys(layersJson, {"transient", "harmonic", "formant"},
                    ctx + ".layers");
  requireKeys(layersJson, {"transient", "harmonic", "formant"},
              ctx + ".layers");
  preset.transient =
      parseTransient(field(layersJson, "transient", ctx + ".layers"),
                     ctx + ".layers.transient");
  preset.harmonic = parseHarmonic(
      field(layersJson, "harmonic", ctx + ".layers"), ctx + ".layers.harmonic");
  preset.formant = parseFormant(field(layersJson, "formant", ctx + ".layers"),
                                ctx + ".layers.formant");

  if (j.contains("modulation")) {
    preset.modulation =
        parseModulation(j.at("modulation"), ctx + ".modulation");
  }

  // ルーティング先パスの実在チェック（docs/03「存在しないパスもエラー」）。
  // Modulation matrixの実体（適用処理）はP1-11の範囲だが、パス解決の検証は
  // ここで行う（Issue #51 完了条件）。
  validateRoutingPaths(preset);

  return preset;
}

Preset loadPreset(const std::filesystem::path &path) {
  std::ifstream in(path, std::ios::binary);
  if (!in) {
    throw PresetError("プリセットファイルを開けない: " + path.string());
  }
  std::ostringstream buf;
  buf << in.rdbuf();

  json j;
  try {
    j = json::parse(buf.str());
  } catch (const json::parse_error &e) {
    throw PresetError(std::string("JSONとして解析できない: ") + e.what());
  }
  return parsePreset(j);
}

} // namespace luthier
