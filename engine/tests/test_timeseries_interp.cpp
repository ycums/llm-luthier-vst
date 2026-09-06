// docs/03-preset-format.md「時系列の表現」の Timeseries
// 補間（interp）のテスト。
//
// interp は linear / exp / step / spline のいずれか（docs/03）。点が1個だけの
// 場合は定数として扱う（docs/03「スカラー専用の型を別に作らない」）。t が
// 最初の点より前・最後の点より後では端点の値を持続する（レンダ長は全時系列の
// 最大t以下に決まる（docs/06 Q-014）が、f0 より partial が長い等で末尾以降を
// 参照するケースを想定した外挿の規約）。
//
// 既知のパラメータから解析的に期待できる値と突き合わせる（Issue #53 完了条件
// 「interp が実装され、点が1個の場合が定数として扱われる」）。

#include <cmath>

#include <catch2/catch_approx.hpp>
#include <catch2/catch_test_macros.hpp>

#include "luthier/timeseries.hpp"

using Catch::Approx;
using luthier::Interp;
using luthier::sampleTimeseries;
using luthier::Timeseries;
using luthier::TimeseriesPoint;

namespace {

Timeseries makeTs(std::initializer_list<std::pair<double, double>> pts,
                  Interp interp) {
  Timeseries ts;
  ts.interp = interp;
  for (const auto &[t, v] : pts)
    ts.points.push_back(TimeseriesPoint{t, v});
  return ts;
}

} // namespace

TEST_CASE("linear interp evaluates linearly between two points") {
  auto ts = makeTs({{0.0, 0.0}, {1.0, 2.0}}, Interp::Linear);
  REQUIRE(sampleTimeseries(ts, 0.0) == Approx(0.0));
  REQUIRE(sampleTimeseries(ts, 0.5) == Approx(1.0));
  REQUIRE(sampleTimeseries(ts, 0.25) == Approx(0.5));
  REQUIRE(sampleTimeseries(ts, 1.0) == Approx(2.0));
}

TEST_CASE("interp holds the endpoint value outside the point range (constant "
          "extrapolation)") {
  Timeseries ts = makeTs({{0.5, 3.0}, {2.5, 7.0}}, Interp::Linear);
  REQUIRE(sampleTimeseries(ts, 0.0) == Approx(3.0)); // 最初の点より前
  REQUIRE(sampleTimeseries(ts, 4.0) == Approx(7.0)); // 最後の点より後
}

TEST_CASE(
    "single-point timeseries is constant for every interp type (docs/03)") {
  for (Interp interp :
       {Interp::Linear, Interp::Exp, Interp::Step, Interp::Spline}) {
    Timeseries ts = makeTs({{0.0, 5.0}}, interp);
    REQUIRE(sampleTimeseries(ts, -1.0) == Approx(5.0));
    REQUIRE(sampleTimeseries(ts, 0.0) == Approx(5.0));
    REQUIRE(sampleTimeseries(ts, 2.0) == Approx(5.0));
  }
}

TEST_CASE(
    "exp interp is geometric (log-domain linear) between two positive points") {
  Timeseries ts = makeTs({{0.0, 1.0}, {1.0, 4.0}}, Interp::Exp);
  REQUIRE(sampleTimeseries(ts, 0.0) == Approx(1.0));
  REQUIRE(sampleTimeseries(ts, 0.5) == Approx(2.0));             // sqrt(1*4)
  REQUIRE(sampleTimeseries(ts, 0.25) == Approx(std::sqrt(2.0))); // 4^0.25
  REQUIRE(sampleTimeseries(ts, 1.0) == Approx(4.0));
}

TEST_CASE("exp interp falls back to linear when an endpoint is non-positive "
          "(exp undefined)") {
  // v0=0 は対数が未定義のため、区間を線形で補間する規約とする。
  Timeseries ts = makeTs({{0.0, 0.0}, {1.0, 2.0}}, Interp::Exp);
  REQUIRE(sampleTimeseries(ts, 0.5) == Approx(1.0));
  Timeseries neg = makeTs({{0.0, -1.0}, {1.0, -2.0}}, Interp::Exp);
  REQUIRE(sampleTimeseries(neg, 0.5) == Approx(-1.5));
}

TEST_CASE("step interp holds the value up to each breakpoint") {
  Timeseries ts = makeTs({{0.0, 1.0}, {0.5, 3.0}, {1.0, 5.0}}, Interp::Step);
  REQUIRE(sampleTimeseries(ts, 0.0) == Approx(1.0));
  REQUIRE(sampleTimeseries(ts, 0.2) == Approx(1.0));
  REQUIRE(sampleTimeseries(ts, 0.5) ==
          Approx(3.0)); // ブレークポイントで新しい値へ跳ねる
  REQUIRE(sampleTimeseries(ts, 0.7) == Approx(3.0));
  REQUIRE(sampleTimeseries(ts, 1.0) == Approx(5.0));
}

TEST_CASE("spline interp is a natural cubic spline through the points") {
  // データ点 (0,0),(1,1),(2,1) に対する自然3次スプラインの解析解:
  //   [0,1] 区間: S(x) = -0.25 x^3 + 1.25 x
  //   [1,2] 区間: S(x) = -0.25 (2-x)^3 + 1.25 (2-x) + (x-1)
  Timeseries ts = makeTs({{0.0, 0.0}, {1.0, 1.0}, {2.0, 1.0}}, Interp::Spline);
  REQUIRE(sampleTimeseries(ts, 0.0) == Approx(0.0));
  REQUIRE(sampleTimeseries(ts, 0.5) == Approx(0.59375));
  REQUIRE(sampleTimeseries(ts, 1.0) == Approx(1.0));
  REQUIRE(sampleTimeseries(ts, 1.5) == Approx(1.09375));
  REQUIRE(sampleTimeseries(ts, 2.0) == Approx(1.0));
}