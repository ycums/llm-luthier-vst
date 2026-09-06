# ヘッドレスCLIレンダラ luthier-render（docs/01-architecture.md「ヘッドレスレンダは
# 必須」）。luthier_core のみに依存する（docs/adr/0006）。

add_executable(luthier-render src/main.cpp)
target_link_libraries(luthier-render PRIVATE luthier_core)
target_compile_features(luthier-render PRIVATE cxx_std_20)
