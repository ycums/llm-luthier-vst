# luthier_core：プラグインフレームワークに依存しない静的ライブラリ
# （docs/01-architecture.md「エンジンコアはプラグインフレームワークに依存しない
# 層として切り出す」）。cli / tests のみがこれに依存し、他のどのディレクトリにも
# 依存しない（docs/adr/0006「依存の向きは一方向に固定する」）。

include(FetchContent)

FetchContent_Declare(
    nlohmann_json
    GIT_REPOSITORY https://github.com/nlohmann/json.git
    GIT_TAG v3.11.3
)
FetchContent_MakeAvailable(nlohmann_json)

add_library(luthier_core STATIC
    src/preset_loader.cpp
    src/routing.cpp
    src/render.cpp
    src/wav_writer.cpp
)

target_include_directories(luthier_core PUBLIC include)
target_link_libraries(luthier_core PUBLIC nlohmann_json::nlohmann_json)
target_compile_features(luthier_core PUBLIC cxx_std_20)
