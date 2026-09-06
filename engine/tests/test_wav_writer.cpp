// harness/README.md「音声I/Oの規則」（16bit整数/24bit整数/32bit浮動小数の
// いずれも読み込める）に適合するWAVを書き出すことのテスト（Issue #51 完了条件）。

#include <cstdint>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <vector>

#include <catch2/catch_test_macros.hpp>

#include "luthier/wav_writer.hpp"

using luthier::writeWavFloatMono;

namespace {

std::vector<char> readAll(const std::filesystem::path& path) {
    std::ifstream in(path, std::ios::binary);
    return std::vector<char>((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
}

std::uint32_t readU32LE(const std::vector<char>& bytes, std::size_t offset) {
    return static_cast<std::uint32_t>(static_cast<std::uint8_t>(bytes[offset])) |
           (static_cast<std::uint32_t>(static_cast<std::uint8_t>(bytes[offset + 1])) << 8) |
           (static_cast<std::uint32_t>(static_cast<std::uint8_t>(bytes[offset + 2])) << 16) |
           (static_cast<std::uint32_t>(static_cast<std::uint8_t>(bytes[offset + 3])) << 24);
}

std::uint16_t readU16LE(const std::vector<char>& bytes, std::size_t offset) {
    return static_cast<std::uint16_t>(static_cast<std::uint8_t>(bytes[offset]) |
                                      (static_cast<std::uint16_t>(static_cast<std::uint8_t>(bytes[offset + 1])) << 8));
}

}  // namespace

TEST_CASE("writeWavFloatMono writes a canonical float32 mono WAV header") {
    std::vector<double> samples{0.0, 0.0, 0.0};
    auto path = std::filesystem::temp_directory_path() / "luthier_test_wav_writer_header.wav";
    writeWavFloatMono(path, samples, 44100);

    auto bytes = readAll(path);
    REQUIRE(bytes.size() >= 44);
    REQUIRE(std::string(bytes.begin(), bytes.begin() + 4) == "RIFF");
    REQUIRE(std::string(bytes.begin() + 8, bytes.begin() + 12) == "WAVE");
    REQUIRE(std::string(bytes.begin() + 12, bytes.begin() + 16) == "fmt ");
    REQUIRE(readU16LE(bytes, 20) == 3);   // WAVE_FORMAT_IEEE_FLOAT
    REQUIRE(readU16LE(bytes, 22) == 1);   // モノラル
    REQUIRE(readU32LE(bytes, 24) == 44100u);
    REQUIRE(readU16LE(bytes, 34) == 32);  // bits per sample

    std::filesystem::remove(path);
}

TEST_CASE("writeWavFloatMono round-trips sample values through the data chunk") {
    std::vector<double> samples{0.0, 0.25, -0.5, 0.125};
    auto path = std::filesystem::temp_directory_path() / "luthier_test_wav_writer_roundtrip.wav";
    writeWavFloatMono(path, samples, 48000);

    auto bytes = readAll(path);
    // fmt/factチャンクの並びに依存しすぎないよう、"data"チャンクを走査して探す。
    std::size_t offset = 12;
    std::size_t dataOffset = 0;
    std::size_t dataSize = 0;
    while (offset + 8 <= bytes.size()) {
        std::string id(bytes.begin() + static_cast<long>(offset), bytes.begin() + static_cast<long>(offset) + 4);
        std::uint32_t size = readU32LE(bytes, offset + 4);
        if (id == "data") {
            dataOffset = offset + 8;
            dataSize = size;
            break;
        }
        offset += 8 + size;
    }
    REQUIRE(dataSize == samples.size() * 4);
    for (std::size_t i = 0; i < samples.size(); ++i) {
        std::uint32_t bits = readU32LE(bytes, dataOffset + i * 4);
        float f = 0.0f;
        std::memcpy(&f, &bits, sizeof(f));
        REQUIRE(static_cast<double>(f) == static_cast<double>(static_cast<float>(samples[i])));
    }

    std::filesystem::remove(path);
}

TEST_CASE("writeWavFloatMono is deterministic: identical input yields byte-identical files") {
    std::vector<double> samples{0.0, 0.0, 0.0, 0.0, 0.0};
    auto pathA = std::filesystem::temp_directory_path() / "luthier_test_wav_writer_det_a.wav";
    auto pathB = std::filesystem::temp_directory_path() / "luthier_test_wav_writer_det_b.wav";
    writeWavFloatMono(pathA, samples, 44100);
    writeWavFloatMono(pathB, samples, 44100);

    REQUIRE(readAll(pathA) == readAll(pathB));

    std::filesystem::remove(pathA);
    std::filesystem::remove(pathB);
}
