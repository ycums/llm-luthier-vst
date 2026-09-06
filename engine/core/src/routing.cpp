#include "luthier/routing.hpp"

#include <cctype>
#include <string>
#include <unordered_set>
#include <variant>
#include <vector>

#include "luthier/preset_error.hpp"

namespace luthier {
namespace {

struct PathSegment {
    std::string name;
    bool has_index = false;
    std::size_t index = 0;
};

[[noreturn]] void malformed(const std::string& path, const std::string& why) {
    throw PresetError("不正なルーティング先パス '" + path + "': " + why);
}

// "formant.bands[0].freq" のようなドット記法パスを分解する。
// docs/03-preset-format.schema.json の modulation_route.to パターン
// (^[a-zA-Z_][a-zA-Z0-9_]*(\.[a-zA-Z_][a-zA-Z0-9_]*(\[[0-9]+\])?)*$) と
// 同じ文法をここで独自に検証する（エンジンはJSON Schemaファイル自体を
// 読み込みには使わない。docs/adr/0006参照）。
std::vector<PathSegment> tokenize(const std::string& path) {
    std::vector<PathSegment> segments;
    std::size_t i = 0;
    const std::size_t n = path.size();
    auto isIdentStart = [](char c) { return std::isalpha(static_cast<unsigned char>(c)) || c == '_'; };
    auto isIdentChar = [](char c) { return std::isalnum(static_cast<unsigned char>(c)) || c == '_'; };

    if (n == 0) malformed(path, "空のパスは指定できない");

    while (i < n) {
        if (!isIdentStart(path[i])) malformed(path, "識別子の先頭が不正な位置 " + std::to_string(i));
        std::size_t start = i;
        while (i < n && isIdentChar(path[i])) ++i;

        PathSegment seg;
        seg.name = path.substr(start, i - start);

        if (i < n && path[i] == '[') {
            ++i;
            std::size_t digitsStart = i;
            while (i < n && std::isdigit(static_cast<unsigned char>(path[i]))) ++i;
            if (i == digitsStart || i >= n || path[i] != ']') {
                malformed(path, "'" + seg.name + "' の配列添字が不正");
            }
            seg.has_index = true;
            seg.index = static_cast<std::size_t>(std::stoull(path.substr(digitsStart, i - digitsStart)));
            ++i;  // consume ']'
        }
        segments.push_back(seg);

        if (i < n) {
            if (path[i] != '.') malformed(path, "区切りは '.' のみ許容される");
            ++i;
            if (i >= n) malformed(path, "末尾が '.' で終わっている");
        }
    }
    return segments;
}

[[noreturn]] void badPath(const std::string& path, const std::string& why) {
    throw PresetError("存在しないルーティング先パス '" + path + "': " + why);
}

void expectNoIndex(const PathSegment& seg, const std::string& path) {
    if (seg.has_index) badPath(path, "'" + seg.name + "' に添字は付けられない");
}

void expectFinal(const std::vector<PathSegment>& segs, std::size_t idx, const std::string& path) {
    if (idx != segs.size() - 1) badPath(path, "'" + segs[idx].name + "' より先のパスは存在しない");
}

void resolveTransient(const TransientLayer& layer, const std::vector<PathSegment>& segs, std::size_t idx,
                      const std::string& path) {
    const auto& seg = segs[idx];
    if (seg.name == "duration" || seg.name == "gain") {
        expectNoIndex(seg, path);
        expectFinal(segs, idx, path);
        return;
    }
    if (seg.name == "spectral_envelope") {
        if (!seg.has_index) badPath(path, "'spectral_envelope' には添字が必要");
        if (seg.index >= layer.spectral_envelope.size()) {
            badPath(path, "'spectral_envelope[" + std::to_string(seg.index) + "]' は範囲外（実際の個数: " +
                              std::to_string(layer.spectral_envelope.size()) + "）");
        }
        expectFinal(segs, idx, path);
        return;
    }
    // 'enabled' は離散フラグ、'seed' は離散的な乱数種であり、いずれも連続的な
    // 変調の対象として意味を持たないため、変調先として不許可とする
    // （判断根拠はPR本文参照）。
    badPath(path, "'transient." + seg.name + "' は変調可能なパラメータではない");
}

void resolveHarmonic(const HarmonicLayer& layer, const std::vector<PathSegment>& segs, std::size_t idx,
                     const std::string& path) {
    const auto& seg = segs[idx];
    if (seg.name == "f0" || seg.name == "inharmonicity") {
        expectNoIndex(seg, path);
        expectFinal(segs, idx, path);
        return;
    }
    if (seg.name == "partial_amplitudes") {
        if (!seg.has_index) badPath(path, "'partial_amplitudes' には添字が必要");
        if (seg.index >= layer.partial_amplitudes.size()) {
            badPath(path, "'partial_amplitudes[" + std::to_string(seg.index) + "]' は範囲外（実際の個数: " +
                              std::to_string(layer.partial_amplitudes.size()) + "）");
        }
        expectFinal(segs, idx, path);
        return;
    }
    badPath(path, "'harmonic." + seg.name + "' は変調可能なパラメータではない");
}

void resolveFormant(const FormantLayer& layer, const std::vector<PathSegment>& segs, std::size_t idx,
                    const std::string& path) {
    const auto& seg = segs[idx];
    if (seg.name != "bands") {
        badPath(path, "'formant." + seg.name + "' は変調可能なパラメータではない");
    }
    if (!seg.has_index) badPath(path, "'bands' には添字が必要");
    if (seg.index >= layer.bands.size()) {
        badPath(path, "'bands[" + std::to_string(seg.index) + "]' は範囲外（実際の個数: " +
                          std::to_string(layer.bands.size()) + "）");
    }
    if (idx + 1 >= segs.size()) {
        badPath(path, "'bands[" + std::to_string(seg.index) + "]' の先（freq/q/gain）が必要");
    }
    const auto& sub = segs[idx + 1];
    if (sub.name != "freq" && sub.name != "q" && sub.name != "gain") {
        badPath(path, "'bands[" + std::to_string(seg.index) + "]." + sub.name + "' は存在しない");
    }
    expectNoIndex(sub, path);
    expectFinal(segs, idx + 1, path);
}

}  // namespace

void validateRoutingPath(const Preset& preset, const std::string& path) {
    std::vector<PathSegment> segs = tokenize(path);
    const auto& root = segs[0];
    expectNoIndex(root, path);
    if (segs.size() < 2) badPath(path, "層名だけでは変調先として不完全（例：formant.bands[0].freq）");

    if (root.name == "transient") {
        resolveTransient(preset.transient, segs, 1, path);
    } else if (root.name == "harmonic") {
        resolveHarmonic(preset.harmonic, segs, 1, path);
    } else if (root.name == "formant") {
        resolveFormant(preset.formant, segs, 1, path);
    } else {
        badPath(path, "未知のルート層 '" + root.name + "'（transient/harmonic/formantのいずれでもない）");
    }
}

void validateRoutingPaths(const Preset& preset) {
    if (!preset.modulation.has_value()) return;

    std::unordered_set<std::string> sourceIds;
    for (const auto& src : preset.modulation->sources) {
        std::string id = std::visit([](const auto& s) { return s.id; }, src);
        sourceIds.insert(id);
    }
    for (const auto& route : preset.modulation->routes) {
        if (!sourceIds.contains(route.from)) {
            throw PresetError("存在しない変調源id '" + route.from + "' を参照するルートがある");
        }
        validateRoutingPath(preset, route.to);
    }
}

}  // namespace luthier
