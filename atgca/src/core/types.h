#pragma once

#include <cstddef>
#include <cstdint>
#include <limits>
#include <string>
#include <vector>

namespace atgca {

enum class GearState : std::uint8_t {
    Idle = 0,
    EngageSoft,
    Cruise,
    Overdrive,
    Protection
};

inline const char* gear_state_name(GearState state) {
    switch (state) {
        case GearState::Idle: return "Idle";
        case GearState::EngageSoft: return "EngageSoft";
        case GearState::Cruise: return "Cruise";
        case GearState::Overdrive: return "Overdrive";
        case GearState::Protection: return "Protection";
    }
    return "Unknown";
}

struct TurbineTelemetry {
    std::string turbine_id;
    float requested_torque = 0.0f;  // [0..1]
    float allocated_torque = 0.0f;  // [0..1]
    float efficiency = 1.0f;        // [0..1]
    float health = 1.0f;            // [0..1]
    float priority = 0.5f;          // [0..1]
    float relevance = 0.5f;         // [0..1]
    float heat_index = 0.0f;        // [0..1]
    bool critical = false;
};

struct GearboxTelemetry {
    GearState current_state = GearState::Idle;
    float gear_ratio = 0.0f;
    float total_torque = 1.0f;
    float hysteresis_timer = 0.0f;
    float thermal_envelope_c = 0.0f;
    float conservation_error = 0.0f;
};

struct GearRatioAdvice {
    float recommended_ratio = 0.0f;
    float confidence = 0.0f;
    std::string rationale;
};

struct HysteresisConfig {
    std::uint32_t soft_to_cruise_frames = 10;
    std::uint32_t cruise_to_overdrive_frames = 6;
    std::uint32_t overdrive_to_cruise_frames = 16;
    std::uint32_t protection_to_cruise_frames = 20;
    std::uint32_t min_frames_between_changes = 10;
};

struct GearRatioConfig {
    float idle = 0.0f;
    float engage_soft = 0.33f;
    float cruise = 1.0f;
    float overdrive = 1.5f;
    float protection = 0.0f;
};

struct TorqueConfig {
    float max_total = 1.0f;
    float min_alloc = 0.01f;
    bool banking_enabled = true;
};

struct ThermalConfig {
    float warning_threshold_c = 80.0f;
    float protection_threshold_c = 85.0f;
    float recovery_threshold_c = 75.0f;
};

struct SystemConfig {
    std::string name = "ATGCA";
    std::string version = "1.0.0";
    std::uint32_t tick_rate_hz = 60;
    std::uint32_t max_turbines = 16;
};

struct TurbineProfile {
    float priority = 0.5f;
    bool critical = false;
};

struct Config {
    SystemConfig system;
    TorqueConfig torque;
    GearRatioConfig gear_ratios;
    HysteresisConfig hysteresis;
    ThermalConfig thermal;
    std::vector<std::pair<std::string, TurbineProfile>> turbines;
};

constexpr float k_epsilon = 1e-6f;
constexpr float k_frame_variance_target = 0.03f;
constexpr float k_torque_conservation_target = 0.01f;

}  // namespace atgca
