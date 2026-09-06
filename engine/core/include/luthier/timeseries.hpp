#pragma once

#include <cmath>
#include <string>
#include <vector>

namespace luthier {

// docs/03-preset-format.md「時系列の表現」。curve（ModulationRoute）も
// 同じ語彙を再利用する（docs/03-preset-format.schema.json のコメント参照）。
enum class Interp { Linear, Exp, Step, Spline };

struct TimeseriesPoint {
  double t = 0.0; // 秒。ノートオンを0.0とする（docs/03）。
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

namespace detail {

// 自然3次スプライン（両端の2階微分=0）を、入力の knot 集合から評価する。
// 実装はトーマス法で2階微分 M[i] を解いてから区間公式で評価する（n が小さく、
// 呼び出しは主にオフラインCLIレンダラなので毎回 O(n) でよい。docs/01）。
inline std::vector<double>
splineSecondDerivatives(const std::vector<TimeseriesPoint> &pts) {
  const std::size_t n = pts.size();
  std::vector<double> M(n, 0.0);
  if (n < 3)
    return M;

  std::vector<double> diag(n, 0.0), rhs(n, 0.0), super(n, 0.0), sub(n, 0.0);
  for (std::size_t i = 1; i + 1 < n; ++i) {
    const double h_prev = pts[i].t - pts[i - 1].t;
    const double h_next = pts[i + 1].t - pts[i].t;
    sub[i] = h_prev;   // 前区間への係数
    super[i] = h_next; // 後区間への係数
    diag[i] = 2.0 * (h_prev + h_next);
    rhs[i] = 6.0 * ((pts[i + 1].v - pts[i].v) / h_next -
                    (pts[i].v - pts[i - 1].v) / h_prev);
  }
  // Thomas法（M[0] と M[n-1] は0で固定）
  for (std::size_t i = 2; i + 1 < n; ++i) {
    const double m = sub[i] / diag[i - 1];
    diag[i] -= m * super[i - 1];
    rhs[i] -= m * rhs[i - 1];
  }
  M[n - 2] = rhs[n - 2] / diag[n - 2];
  // 後退代入：M[n-3] を M[1] まで解く。M[0] は自然境界で0固定のまま。
  for (long k = static_cast<long>(n) - 3; k >= 1; --k) {
    M[static_cast<std::size_t>(k)] = (rhs[static_cast<std::size_t>(k)] -
                                      super[static_cast<std::size_t>(k)] *
                                          M[static_cast<std::size_t>(k) + 1]) /
                                     diag[static_cast<std::size_t>(k)];
  }
  return M;
}

} // namespace detail

// Timeseries の補間評価（docs/03「時系列の表現」）。第2引数 t はノートオン0.0
// からの秒。
//
// - interp: linear / exp / step / spline
// - 点が1個の場合は定数（docs/03「スカラー専用の型を別に作らない」）
// - t が最初の点より前・最後の点より後では端点の値をそのまま返す（定数外挿）。
//   f0 より partial_amplitudes が長い時系列を持つ等、層ごとに参照範囲が異なる
//   場合の規約。P1-08 のPR本文に暫定値を記載（docs/06 Q-016）。
// - exp は対数域での線形補間（幾何平均）だが、端点のいずれかが非正の場合は
//   対数が定義されないため線形に縮退する（P1-08 のPR本文に反映）。
inline double sampleTimeseries(const Timeseries &ts, double t) {
  const auto &pts = ts.points;
  const std::size_t n = pts.size();
  if (n == 0)
    return 0.0; // 防御的。ローダーが points>=1 を保証（preset_loader）
  if (n == 1)
    return pts[0].v;
  if (t <= pts[0].t)
    return pts[0].v;
  if (t >= pts[n - 1].t)
    return pts[n - 1].v;

  // step はブレークポイントで新しい値へ跳ねる（直前の点の値を持続）。
  if (ts.interp == Interp::Step) {
    for (std::size_t i = n - 2;; --i) {
      if (t >= pts[i].t)
        return pts[i].v;
      if (i == 0)
        break;
    }
    return pts[0].v;
  }

  // linear / exp / spline 共通の区間探索。
  std::size_t i = 0;
  while (i + 1 < n && pts[i + 1].t <= t)
    ++i;
  const double t0 = pts[i].t, t1 = pts[i + 1].t;
  const double v0 = pts[i].v, v1 = pts[i + 1].v;
  const double span = t1 - t0;
  const double u = span > 0.0 ? (t - t0) / span : 0.0;

  switch (ts.interp) {
  case Interp::Linear:
    return v0 + (v1 - v0) * u;
  case Interp::Exp:
    // 幾何補間：v = v0 * (v1/v0)^u。v0,v1 とも正のときのみ。
    if (v0 > 0.0 && v1 > 0.0 && v0 != v1)
      return v0 * std::pow(v1 / v0, u);
    return v0 + (v1 - v0) * u; // 非正な端点では線形に縮退
  case Interp::Spline: {
    const auto M = detail::splineSecondDerivatives(pts);
    const double dx = t1 - t0;
    const double a = M[i] * (t1 - t) * (t1 - t) * (t1 - t) / (6.0 * dx);
    const double b = M[i + 1] * (t - t0) * (t - t0) * (t - t0) / (6.0 * dx);
    const double c = (v0 - M[i] * dx * dx / 6.0) * (t1 - t) / dx;
    const double d = (v1 - M[i + 1] * dx * dx / 6.0) * (t - t0) / dx;
    return a + b + c + d;
  }
  case Interp::Step: // unreachable（上で処理済み）
  default:
    return v0 + (v1 - v0) * u;
  }
}

} // namespace luthier