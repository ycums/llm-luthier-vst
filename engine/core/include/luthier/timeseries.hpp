#pragma once

#include <string>
#include <vector>

namespace luthier {

// docs/03-preset-format.md「時系列の表現」。curve（ModulationRoute）も
// 同じ語彙を再利用する（docs/03-preset-format.schema.json のコメント参照）。
enum class Interp { Linear, Exp, Step, Spline };

struct TimeseriesPoint {
    double t = 0.0;  // 秒。ノートオンを0.0とする（docs/03）。
    double v = 0.0;
};

// すべての時間変化する値の共通表現（docs/03）。
// 点が1個だけの場合は定数として扱う（docs/03「スカラー専用の型を別に作らない」）。
struct Timeseries {
    std::string unit;
    Interp interp = Interp::Linear;
    std::vector<TimeseriesPoint> points;
};

// 帯域別・倍音別など、Timeseriesの配列（docs/03-preset-format.schema.json
// の timeseries_array）。
using TimeseriesArray = std::vector<Timeseries>;

}  // namespace luthier
