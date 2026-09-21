CXX := g++
CXXFLAGS := -std=c++20 -Wall -Wextra -Werror -O2 -I./src

SRC_DIR := src
TEST_DIR := tests/unit
BUILD_DIR := build

CORE_SRCS := \
	$(SRC_DIR)/core/types.cpp \
	$(SRC_DIR)/core/config_loader.cpp \
	$(SRC_DIR)/core/state_machine.cpp \
	$(SRC_DIR)/core/hysteresis_controller.cpp \
	$(SRC_DIR)/core/torque_allocator.cpp \
	$(SRC_DIR)/core/gearbox_core.cpp \
	$(SRC_DIR)/core/turbine_base.cpp

TEST_SRCS := \
	$(TEST_DIR)/test_conservation.cpp \
	$(TEST_DIR)/test_hysteresis.cpp \
	$(TEST_DIR)/test_gearbox.cpp \
	$(TEST_DIR)/main.cpp

OBJS := $(patsubst %.cpp,$(BUILD_DIR)/%.o,$(notdir $(CORE_SRCS) $(TEST_SRCS)))

.PHONY: all test clean

all: $(BUILD_DIR)/atgca_test

$(BUILD_DIR)/atgca_test: $(OBJS)
	@mkdir -p $(BUILD_DIR)
	$(CXX) $(CXXFLAGS) $^ -o $@

$(BUILD_DIR)/%.o: $(SRC_DIR)/core/%.cpp
	@mkdir -p $(BUILD_DIR)
	$(CXX) $(CXXFLAGS) -c $< -o $@

$(BUILD_DIR)/%.o: $(TEST_DIR)/%.cpp
	@mkdir -p $(BUILD_DIR)
	$(CXX) $(CXXFLAGS) -c $< -o $@

test: $(BUILD_DIR)/atgca_test
	./$(BUILD_DIR)/atgca_test

clean:
	rm -rf $(BUILD_DIR)
