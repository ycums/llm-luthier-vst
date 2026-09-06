#include "luthier/wav_writer.hpp"

#include <array>
#include <cstring>
#include <fstream>

namespace luthier {
namespace {

void writeU32LE(std::ofstream &out, std::uint32_t v) {
  std::array<char, 4> b{
      static_cast<char>(v & 0xFF), static_cast<char>((v >> 8) & 0xFF),
      static_cast<char>((v >> 16) & 0xFF), static_cast<char>((v >> 24) & 0xFF)};
  out.write(b.data(), static_cast<std::streamsize>(b.size()));
}

void writeU16LE(std::ofstream &out, std::uint16_t v) {
  std::array<char, 2> b{static_cast<char>(v & 0xFF),
                        static_cast<char>((v >> 8) & 0xFF)};
  out.write(b.data(), static_cast<std::streamsize>(b.size()));
}

void writeTag(std::ofstream &out, const char (&tag)[5]) { out.write(tag, 4); }

} // namespace

void writeWavFloatMono(const std::filesystem::path &path,
                       const std::vector<double> &samples,
                       std::uint32_t sample_rate_hz) {
  std::ofstream out(path, std::ios::binary | std::ios::trunc);
  if (!out)
    throw WavWriteError("出力WAVを開けない: " + path.string());

  constexpr std::uint16_t kFormatTagIeeeFloat = 3;
  constexpr std::uint16_t kChannels = 1;
  constexpr std::uint16_t kBitsPerSample = 32;
  constexpr std::uint16_t kBytesPerSample = kBitsPerSample / 8;

  const auto byteRate =
      static_cast<std::uint32_t>(sample_rate_hz) * kChannels * kBytesPerSample;
  const std::uint16_t blockAlign = kChannels * kBytesPerSample;
  const auto dataBytes =
      static_cast<std::uint32_t>(samples.size()) * kBytesPerSample;
  // WAVE_FORMAT_IEEE_FLOATは "fact" チャンクが必須。
  // RIFFサイズ = "WAVE"(4) + fmt チャンク(8+16) + fact チャンク(8+4) + data
  // チャンク(8+dataBytes)
  const std::uint32_t riffSize = 4 + (8 + 16) + (8 + 4) + (8 + dataBytes);

  writeTag(out, "RIFF");
  writeU32LE(out, riffSize);
  writeTag(out, "WAVE");

  writeTag(out, "fmt ");
  writeU32LE(out, 16);
  writeU16LE(out, kFormatTagIeeeFloat);
  writeU16LE(out, kChannels);
  writeU32LE(out, sample_rate_hz);
  writeU32LE(out, byteRate);
  writeU16LE(out, blockAlign);
  writeU16LE(out, kBitsPerSample);

  writeTag(out, "fact");
  writeU32LE(out, 4);
  writeU32LE(out, static_cast<std::uint32_t>(samples.size()));

  writeTag(out, "data");
  writeU32LE(out, dataBytes);
  for (double s : samples) {
    float f = static_cast<float>(s);
    std::uint32_t bits;
    std::memcpy(&bits, &f, sizeof(bits));
    writeU32LE(out, bits);
  }

  if (!out)
    throw WavWriteError("出力WAVの書き込みに失敗した: " + path.string());
}

} // namespace luthier
