// luthier-render：プリセットJSONを読んでWAVを書き出すヘッドレスCLI
// （docs/01-architecture.md「プリセットを食わせてWAVを吐くCLIを、プラグイン本体と
// 同じエンジンコアからビルドする」）。
//
// 使い方: luthier-render <preset.json> <output.wav> [--sample-rate N]
//
// サンプルレートはプリセット自体には含まれない（レンダ時のパラメータ、
// docs/adr/0006「サンプルレート非依存…レンダ時に指定サンプルレートで離散化する」）
// ため、CLI引数として受け取る。未指定時は44100Hzを既定値とする。

#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <string>
#include <vector>

#include "luthier/preset_error.hpp"
#include "luthier/preset_loader.hpp"
#include "luthier/render.hpp"
#include "luthier/wav_writer.hpp"

namespace {

void printUsage(std::ostream &out) {
  out << "使い方: luthier-render <preset.json> <output.wav> [--sample-rate "
         "N]\n";
}

constexpr std::uint32_t kDefaultSampleRateHz = 44100;

} // namespace

int main(int argc, char **argv) {
  std::vector<std::string> positional;
  std::uint32_t sampleRate = kDefaultSampleRateHz;

  for (int i = 1; i < argc; ++i) {
    const std::string arg = argv[i];
    constexpr const char *kSampleRateFlag = "--sample-rate";
    if (arg == kSampleRateFlag) {
      if (i + 1 >= argc) {
        std::cerr << "エラー: " << kSampleRateFlag << " には値が必要\n";
        printUsage(std::cerr);
        return 2;
      }
      sampleRate = static_cast<std::uint32_t>(std::stoul(argv[++i]));
    } else if (arg.rfind(std::string(kSampleRateFlag) + "=", 0) == 0) {
      sampleRate = static_cast<std::uint32_t>(
          std::stoul(arg.substr(std::string(kSampleRateFlag).size() + 1)));
    } else {
      positional.push_back(arg);
    }
  }

  if (positional.size() != 2) {
    std::cerr
        << "エラー: 引数の数が不正（プリセットJSONパスと出力WAVパスが必要）\n";
    printUsage(std::cerr);
    return 2;
  }
  const std::string &presetPath = positional[0];
  const std::string &outputPath = positional[1];

  try {
    const luthier::Preset preset = luthier::loadPreset(presetPath);
    const std::vector<double> samples =
        luthier::render(preset, static_cast<double>(sampleRate));
    luthier::writeWavFloatMono(outputPath, samples, sampleRate);
  } catch (const luthier::PresetError &e) {
    std::cerr << "エラー: " << e.what() << "\n";
    return 1;
  } catch (const luthier::WavWriteError &e) {
    std::cerr << "エラー: " << e.what() << "\n";
    return 1;
  } catch (const std::exception &e) {
    std::cerr << "予期しないエラー: " << e.what() << "\n";
    return 1;
  }

  return 0;
}
