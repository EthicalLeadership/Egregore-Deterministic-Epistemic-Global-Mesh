cmake_minimum_required(VERSION 3.20)
project(atgca VERSION 1.0.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

# Library
add_library(atgca_core STATIC
    src/core/types.cpp
    src/core/config_loader.cpp
    src/core/state_machine.cpp
    src/core/hysteresis_controller.cpp
    src/core/torque_allocator.cpp
    src/core/gearbox_core.cpp
    src/core/turbine_base.cpp
)

target_include_directories(atgca_core PUBLIC src)

# Tests via FetchContent GoogleTest
include(FetchContent)
FetchContent_Declare(
    googletest
    URL https://github.com/google/googletest/archive/refs/tags/v1.14.0.zip
)
FetchContent_MakeAvailable(googletest)

enable_testing()

add_executable(atgca_test
    tests/unit/test_conservation.cpp
    tests/unit/test_hysteresis.cpp
    tests/unit/test_gearbox.cpp
)

target_link_libraries(atgca_test atgca_core GTest::gtest_main)

include(GoogleTest)
gtest_discover_tests(atgca_test)
