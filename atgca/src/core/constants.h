#pragma once

#include "core/types.h"

namespace atgca {

inline Config default_config() {
    Config cfg;
    cfg.system = {"ATGCA", "1.0.0", 60, 16};
    cfg.torque = {1.0f, 0.01f, true};
    cfg.gear_ratios = {0.0f, 0.33f, 1.0f, 1.5f, 0.0f};
    cfg.hysteresis = {10, 6, 16, 20, 10};
    cfg.thermal = {80.0f, 85.0f, 75.0f};
    cfg.turbines = {
        {"render", {0.9f, true}},
        {"ai", {0.8f, false}},
        {"io", {0.7f, true}},
        {"physics", {0.6f, false}},
        {"audio", {0.5f, false}},
    };
    return cfg;
}

}  // namespace atgca
