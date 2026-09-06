// Formant filter bank（docs/02-engine-spec.md 層[3]、Issue #55）のテスト。
//
// 検証方針は docs/06-open-questions.md Q-013 の「既知解フィクスチャに対する解析的な
// 検証」。エンジンC++単体で、既知の freq / Q / gain・既知の遷移を与えて、出力の
// 共振ピーク位置・帯域制御・ゲイン反映・時変遷移・係数急変時の安定性・バイパス・
// サンプルレート非依存・決定論を検証する（Q-010 の stft-peak-tracking は指標側の
// 方式でありエンジン側の生成とは独立。この層の検証のために推定方式は選び直さない、
// Issue #55参照）。
//
// 共振フィルタには TPT 状態変数バンドパスを採用する。freq / Q / gain はサンプル毎に
// 時系列から評価して係数を毎サンプル再導出するため、係数の急変でも発散しないことを
// テストで検証する。

#include <algorithm>
#include <cmath>
#include <vector>

#include <catch2/catch_test_macros.hpp>

#include "luthier/formant.hpp"
#include "luthier/timeseries.hpp"

using luthier::FormantBand;
using luthier::FormantLayer;
using luthier::Interp;
using luthier::Timeseries;
using luthier::applyFormant;
using luthier::sampleTimeseries;

namespace {

constexpr double kPi = 3.14159265358979323846;

Timeseries lin(double v) {
    Timeseries t;
    t.interp = Interp::Linear;
    t.points.push_back({0.0, v});
    return t;
}

// 2点の線形時系列（遷移テスト用）。
Timeseries linRamp(double t0, double v0, double t1, double v1) {
    Timeseries t;
    t.interp = Interp::Linear;
    t.points.push_back({t0, v0});
    t.points.push_back({t1, v1});
    return t;
}

FormantBand band(double f, double q, double g) {
    FormantBand b;
    b.freq = lin(f);
    b.q = lin(q);
    b.gain = lin(g);
    return b;
}

FormantLayer layer(const std::vector<FormantBand>& bs) {
    FormantLayer l;
    l.enabled = true;
    l.bands = bs;
    return l;
}

std::vector<double> sine(double f, int n, double sr) {
    std::vector<double> x(n);
    for (int i = 0; i < n; ++i) x[i] = std::sin(2.0 * kPi * f * double(i) / sr);
    return x;
}

// 帯域全体（200..4000Hz、200Hz間隔）の定常サインの和。遷移テストの入力。
std::vector<double> broadband(int n, double sr) {
    std::vector<double> x(n, 0.0);
    for (double f = 200.0; f <= 4000.0; f += 200.0) {
        const std::vector<double> s = sine(f, n, sr);
        for (int i = 0; i < n; ++i) x[i] += s[i];
    }
    for (double& v : x) v /= 12.0;
    return x;
}

double rms(const std::vector<double>& s, std::size_t skip) {
    double acc = 0.0;
    std::size_t m = 0;
    for (std::size_t i = skip; i < s.size(); ++i) {
        acc += s[i] * s[i];
        ++m;
    }
    return std::sqrt(acc / double(m));
}

// 定常サイン入力に対する、単一共振バンドの定常RMSゲイン（スキップした定常部で測る）。
double centerGain(const FormantLayer& l, double f, double sr, int n) {
    const std::vector<double> x = sine(f, n, sr);
    return rms(applyFormant(l, x, sr), static_cast<std::size_t>(n / 2));
}

// 相対誤差で近いことをチェックする（CatchのApproxマッチャを使わない数値比較）。
bool nearRel(double actual, double expected, double relTol) {
    const double denom = std::max(std::fabs(expected), 1e-12);
    return std::fabs(actual - expected) / denom <= relTol;
}

}  // namespace

// ---------------------------------------------------------------------------
// evaluateTimeseries（docs/03「時系列の表現」）
// ---------------------------------------------------------------------------

TEST_CASE("evaluateTimeSeries: point set of one is a constant") {
    Timeseries ts = lin(123.0);
    REQUIRE(sampleTimeseries(ts, 0.0) == 123.0);
    REQUIRE(sampleTimeseries(ts, 1000.0) == 123.0);
}

TEST_CASE("evaluateTimeSeries: linear interpolation and endpoint clamping") {
    Timeseries ts;
    ts.interp = Interp::Linear;
    ts.points.push_back({0.0, 0.0});
    ts.points.push_back({1.0, 10.0});
    CHECK(sampleTimeseries(ts, 0.0) == 0.0);
    CHECK(sampleTimeseries(ts, 0.5) == 5.0);
    CHECK(sampleTimeseries(ts, 1.0) == 10.0);
    CHECK(sampleTimeseries(ts, -1.0) == 0.0);   // clamp
    CHECK(sampleTimeseries(ts, 2.0) == 10.0);   // clamp
}

TEST_CASE("evaluateTimeSeries: step interpolation holds the left value") {
    Timeseries ts;
    ts.interp = Interp::Step;
    ts.points.push_back({0.0, 1.0});
    ts.points.push_back({1.0, 2.0});
    CHECK(sampleTimeseries(ts, 0.5) == 1.0);
    CHECK(sampleTimeseries(ts, 1.5) == 2.0);
}

TEST_CASE("evaluateTimeSeries: exp interpolates in log domain") {
    Timeseries ts;
    ts.interp = Interp::Exp;
    ts.points.push_back({0.0, 1.0});
    ts.points.push_back({1.0, 100.0});
    CHECK(nearRel(sampleTimeseries(ts, 0.5), 10.0, 1e-9));
}

// ---------------------------------------------------------------------------
// 共振：ピークが中心周波数にある
// ---------------------------------------------------------------------------

TEST_CASE("formant: the resonance peak sits at the band centre frequency") {
    const double sr = 44100.0;
    const int n = sr / 2;
    const FormantLayer L = layer({band(1000.0, 5.0, 0.0)});
    const double gCenter = centerGain(L, 1000.0, sr, n);
    const double gOff1 = centerGain(L, 700.0, sr, n);
    const double gOff2 = centerGain(L, 1300.0, sr, n);
    REQUIRE(gCenter > gOff1);
    REQUIRE(gCenter > gOff2);
    // 中心が離れたプローブより明確に大きい
    REQUIRE(gCenter > 1.5 * gOff1);
    REQUIRE(gCenter > 1.5 * gOff2);
}

// ---------------------------------------------------------------------------
// Q: 帯域制御
// ---------------------------------------------------------------------------

TEST_CASE("formant: higher Q concentrates energy at the centre (bandwidth)") {
    const double sr = 44100.0;
    const int n = sr / 4;
    const double fC = 1000.0, fOff = 1300.0;
    const FormantLayer lLowQ = layer({band(fC, 1.0, 0.0)});
    const FormantLayer lHighQ = layer({band(fC, 20.0, 0.0)});

    const double cLow = centerGain(lLowQ, fC, sr, n);
    const double oLow = centerGain(lLowQ, fOff, sr, n);
    const double cHigh = centerGain(lHighQ, fC, sr, n);
    const double oHigh = centerGain(lHighQ, fOff, sr, n);

    // Q は共振の帯域幅の逆数。中心からのオフセットに対するゲインの落ち込み
    // （中心/オフセット比）が高Qほど大きい、つまり共振ピークが帯域の狭い位置に
    // 集中することを確認する。
    // 絶対値（oHigh と oLow）は比較しない：バンドパスの中心ゲインが Q/√2 で
    // Qに比例して高くなり、絶対値では帯域幅の差が単調にならないため。
    REQUIRE((cHigh / oHigh) > (cLow / oLow));
}

// ---------------------------------------------------------------------------
// gain: dB が振幅に線形反映される
// ---------------------------------------------------------------------------

TEST_CASE("formant: gain in dB scales the output amplitude linearly") {
    const double sr = 44100.0;
    const int n = sr / 4;
    const auto outGain = [&](double db) {
        const FormantLayer l = layer({band(1000.0, 5.0, db)});
        return centerGain(l, 1000.0, sr, n);
    };
    const double g0 = outGain(0.0);
    const double gM12 = outGain(-12.0);
    const double gP6 = outGain(6.0);
    // -12dB = 10^(-12/20) ≈ 0.2512
    CHECK(nearRel(gM12 / g0, 0.2512, 0.02));
    // +6dB = 10^(6/20) ≈ 1.995
    CHECK(nearRel(gP6 / g0, 1.995, 0.02));
}

// ---------------------------------------------------------------------------
// バイパス（enabled: false）
// ---------------------------------------------------------------------------

TEST_CASE("formant: disabled layer passes the input through unchanged") {
    const double sr = 44100.0;
    const int n = sr / 4;
    const std::vector<double> in = broadband(n, sr);
    FormantLayer off = layer({band(1000.0, 5.0, 0.0)});
    off.enabled = false;
    REQUIRE(applyFormant(off, in, sr) == in);
}

// ---------------------------------------------------------------------------
// 決定論
// ---------------------------------------------------------------------------

TEST_CASE("formant: output is bit-identical across repeated calls") {
    const double sr = 44100.0;
    const int n = sr / 2;
    const FormantLayer l = layer({band(1000.0, 5.0, 3.0), band(2000.0, 8.0, -2.0),
                                  band(3000.0, 6.0, 1.0), band(4000.0, 4.0, 0.5)});
    const std::vector<double> x = broadband(n, sr);
    REQUIRE(applyFormant(l, x, sr) == applyFormant(l, x, sr));
}

// ---------------------------------------------------------------------------
// サンプルレート非依存：共振ピーク位置（Hz）がサンプルレートに依らず保たれる
// ---------------------------------------------------------------------------

TEST_CASE("formant: peak stays at the same physical Hz across sample rates") {
    const double fC = 800.0;  // 22050のナイキスト(11025)より十分下
    const std::vector<double> srs = {22050.0, 44100.0, 48000.0, 88200.0};
    std::vector<double> peaks;
    for (double sr : srs) {
        const int n = static_cast<int>(sr);  // 1秒
        const FormantLayer l = layer({band(fC, 5.0, 0.0)});
        const double gC = centerGain(l, fC, sr, n);
        const double gOff = centerGain(l, 1.6 * fC, sr, n);
        REQUIRE(gC > gOff);  // ピークは常に fC の位置
        peaks.push_back(gC);
    }
    // どのサンプルレートでも中心ゲインが一致（係数がサンプルレート正しく導出）
    for (double p : peaks) CHECK(nearRel(p, peaks[0], 0.01));
}

// ---------------------------------------------------------------------------
// 時変対象：フォルマント遷移（共振ピークが動く）
// ---------------------------------------------------------------------------

TEST_CASE("formant: a time-varying freq produces a moving resonance (glide)") {
    const double sr = 44100.0;
    const double dur = 1.0;
    const int n = static_cast<int>(sr * dur);
    FormantLayer l;
    l.enabled = true;
    FormantBand b;
    b.q = lin(5.0);
    b.gain = lin(0.0);
    b.freq = linRamp(0.0, 400.0, dur, 3000.0);  // 1秒で400→3000Hz
    l.bands = {b};

    const std::vector<double> x = broadband(n, sr);
    const std::vector<double> y = applyFormant(l, x, sr);

    // 低域プローブ（400Hz）と高域プローブ（3000Hz）の狭域帯域を掛けて窓ごとの
    // エネギーでピーク位置を測る（Q-010のstft-peak-trackingに替わる単体の解析検証）。
    const auto lowE = [&](std::size_t a, std::size_t b) {
        const std::vector<double> seg(y.begin() + a, y.begin() + b);
        return rms(applyFormant(layer({band(400.0, 20.0, 0.0)}), seg, sr), 0);
    };
    const auto highE = [&](std::size_t a, std::size_t b) {
        const std::vector<double> seg(y.begin() + a, y.begin() + b);
        return rms(applyFormant(layer({band(3000.0, 20.0, 0.0)}), seg, sr), 0);
    };

    const std::size_t early0 = static_cast<std::size_t>(0.02 * sr);
    const std::size_t early1 = static_cast<std::size_t>(0.18 * sr);
    const std::size_t late0 = static_cast<std::size_t>(0.80 * sr);
    const std::size_t late1 = static_cast<std::size_t>(0.96 * sr);

    const double eLow = lowE(early0, early1);
    const double eHigh = highE(early0, early1);
    const double lLow = lowE(late0, late1);
    const double lHigh = highE(late0, late1);

    // 初期は低域にエネルギッシュ、終期は高域にエネルギッシュ
    CHECK(eLow > eHigh);
    CHECK(lHigh > lLow);
}

// ---------------------------------------------------------------------------
// 時変係数の安定性：係数の両急な上下に耐える(発散・大きな不連続なし)
// ---------------------------------------------------------------------------

TEST_CASE("formant: time-varying coefficients stay finite and bounded under abrupt jumps") {
    const double sr = 44100.0;
    const int n = static_cast<int>(sr * 2.0);
    std::vector<double> in(n, 0.0);
    in[0] = 1.0;  // インパルス

    // freq / Q / gain が 10msごとに急変する4バンド層
    FormantLayer l;
    l.enabled = true;
    FormantBand b;
    b.freq.interp = Interp::Step;
    b.q.interp = Interp::Step;
    b.gain.interp = Interp::Step;
    b.freq.points.push_back({0.0, 200.0});
    b.freq.points.push_back({0.01, 4000.0});
    b.freq.points.push_back({0.02, 200.0});
    b.freq.points.push_back({0.03, 4000.0});
    b.q.points.push_back({0.0, 20.0});
    b.q.points.push_back({0.01, 1.0});
    b.q.points.push_back({0.02, 20.0});
    b.q.points.push_back({0.03, 1.0});
    b.gain.points.push_back({0.0, 6.0});
    b.gain.points.push_back({0.01, -12.0});
    b.gain.points.push_back({0.02, 6.0});
    b.gain.points.push_back({0.03, -12.0});
    l.bands = {b, b, b, b};

    const std::vector<double> y = applyFormant(l, in, sr);

    double maxAbs = 0.0, maxJump = 0.0;
    bool finite = true;
    for (std::size_t i = 0; i < y.size(); ++i) {
        if (!std::isfinite(y[i])) finite = false;
        maxAbs = std::max(maxAbs, std::fabs(y[i]));
        if (i > 0) maxJump = std::max(maxJump, std::fabs(y[i] - y[i - 1]));
    }
    REQUIRE(finite);                          // NaN/Inf なし
    REQUIRE(maxAbs < 1e6);                    // 発散しない（実測~0.03、十分マージン）
    REQUIRE(maxJump < 1e3);                   // 一瞬跳ぶ不連続ノイズなし
}