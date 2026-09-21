#pragma once

#include <cmath>
#include <cstdlib>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

namespace atgca {
namespace test {

struct TestFailure : public std::runtime_error {
    TestFailure(const char* file, int line, const std::string& msg)
        : std::runtime_error(std::string(file) + ":" + std::to_string(line) + ": " + msg) {}
};

using TestFunc = void (*)();

struct TestCase {
    const char* name;
    TestFunc func;
};

inline std::vector<TestCase>& test_registry() {
    static std::vector<TestCase> registry;
    return registry;
}

inline void register_test(const char* name, TestFunc func) {
    test_registry().push_back({name, func});
}

inline int run_all_tests() {
    int passed = 0;
    int failed = 0;
    for (const auto& tc : test_registry()) {
        try {
            tc.func();
            std::cout << "[PASS] " << tc.name << std::endl;
            ++passed;
        } catch (const TestFailure& e) {
            std::cerr << "[FAIL] " << tc.name << "\n  " << e.what() << std::endl;
            ++failed;
        } catch (const std::exception& e) {
            std::cerr << "[FAIL] " << tc.name << "\n  Exception: " << e.what() << std::endl;
            ++failed;
        }
    }
    std::cout << "\n" << passed << " passed, " << failed << " failed" << std::endl;
    return failed == 0 ? 0 : 1;
}

}  // namespace test
}  // namespace atgca

#define TEST(name)                                                \
    void test_##name();                                           \
    struct test_##name##_registrar {                              \
        test_##name##_registrar() {                               \
            ::atgca::test::register_test(#name, test_##name);     \
        }                                                         \
    } test_##name##_instance;                                     \
    void test_##name()

#define ASSERT_TRUE(expr, ...)                                    \
    do {                                                          \
        if (!(expr)) {                                            \
            std::string msg = "Assertion failed: " #expr;         \
            ::atgca::test::append_msg(msg, ##__VA_ARGS__);        \
            throw ::atgca::test::TestFailure(__FILE__, __LINE__, msg); \
        }                                                         \
    } while (0)

#define ASSERT_FALSE(expr, ...) ASSERT_TRUE(!(expr), ##__VA_ARGS__)

#define ASSERT_EQ(a, b, ...)                                      \
    do {                                                          \
        if ((a) != (b)) {                                         \
            std::string msg = std::string("Expected equality: ") + #a + " == " + #b; \
            ::atgca::test::append_msg(msg, ##__VA_ARGS__);        \
            throw ::atgca::test::TestFailure(__FILE__, __LINE__, msg); \
        }                                                         \
    } while (0)

#define ASSERT_NEAR(a, b, eps, ...)                               \
    do {                                                          \
        if (std::fabs((a) - (b)) > (eps)) {                       \
            std::string msg = std::string("Expected near: ") + #a + " ~= " + #b; \
            ::atgca::test::append_msg(msg, ##__VA_ARGS__);        \
            throw ::atgca::test::TestFailure(__FILE__, __LINE__, msg); \
        }                                                         \
    } while (0)

namespace atgca {
namespace test {

inline void append_msg([[maybe_unused]] std::string& base) {}

inline void append_msg(std::string& base, const std::string& extra) {
    base += " | " + extra;
}

}  // namespace test
}  // namespace atgca
