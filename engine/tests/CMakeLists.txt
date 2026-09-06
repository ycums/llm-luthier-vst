# luthier_core の単体テスト（Catch2）。ctest --test-dir engine/build
# --output-on-failure で実行する（docs/adr/0006）。

include(FetchContent)

FetchContent_Declare(
    Catch2
    GIT_REPOSITORY https://github.com/catchorg/Catch2.git
    GIT_TAG v3.7.1
)
FetchContent_MakeAvailable(Catch2)

add_executable(luthier_core_tests
    test_preset_loader.cpp
    test_routing.cpp
    test_render.cpp
    test_timeseries_interp.cpp
    test_wav_writer.cpp
)
target_link_libraries(luthier_core_tests PRIVATE luthier_core Catch2::Catch2WithMain)
target_include_directories(luthier_core_tests PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})
target_compile_features(luthier_core_tests PRIVATE cxx_std_20)

list(APPEND CMAKE_MODULE_PATH "${catch2_SOURCE_DIR}/extras")
include(Catch)
catch_discover_tests(luthier_core_tests)
