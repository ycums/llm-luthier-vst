#pragma once

#include <cstdint>
#include <filesystem>
#include <stdexcept>
#include <string>
#include <vector>

namespace luthier {

class WavWriteError : public std::runtime_error {
public:
    explicit WavWriteError(const std::string& message) : std::runtime_error(message) {}
};

// モノラル・32-bit IEEE float のcanonical WAV（RIFF/WAVE、fmtチャンク＋
// WAVE_FORMAT_IEEE_FLOAT必須のfactチャンク＋dataチャンク）を書き出す。
//
// 具体形式（ビット深度・チャンネル構成）の決定は本Issue（P1-06 #51）の範囲
// （docs/adr/0006-engine-core-language-and-build.md）。32-bit floatを選んだ
// 理由：内部表現（double）から量子化・ディザ方針を新たに決めずに変換でき、
// harness/README.md「音声I/Oの規則」が読めるサブタイプ（16bit整数/24bit整数/
// 32bit浮動小数のいずれか）に含まれる。
//
// `samples` の各値は書き出し時に float（単精度）へ変換する。バイト順は常に
// リトルエンディアンで書き出す（ホストのエンディアンに関わらず）。
void writeWavFloatMono(const std::filesystem::path& path,
                       const std::vector<double>& samples,
                       std::uint32_t sample_rate_hz);

}  // namespace luthier
