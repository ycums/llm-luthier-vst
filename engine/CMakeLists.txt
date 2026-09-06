# エンジンコア・ヘッドレスCLIレンダラのトップレベルビルド設定
# （docs/adr/0006-engine-core-language-and-build.md）。
#
# ハーネス（Python、pyproject.toml）とはビルド系統を分離するため、
# CMakeLists.txt はリポジトリルートではなくこの engine/ 直下に置く。

cmake_minimum_required(VERSION 3.21)
project(llm_luthier_engine LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

# 【Issue #67 検証用の一時変更】
# /utf-8 を外して test-windows ジョブが日本語ロケール破綻を検出できるか実測する。
# この変更は検証専用であり、main にはマージしない。
if(MSVC)
    # add_compile_options(/utf-8)
endif()

# 決定論・サンプルレート非依存の担保（docs/adr/0006「決定論・サンプルレート
# 非依存の担保」）：自動ベクトル化・FMA融合を誘発しうる積極的な最適化フラグを
# ここで追加しない。コンパイラ・ビルド種別の既定に委ねる。

enable_testing()

add_subdirectory(core)
add_subdirectory(cli)
add_subdirectory(tests)
