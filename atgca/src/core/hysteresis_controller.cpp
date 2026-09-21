#include "core/hysteresis_controller.h"

namespace atgca {

HysteresisController::HysteresisController(const HysteresisConfig& config)
    : config_(config), last_desired_(GearState::Idle), stability_frames_(0), frames_since_last_change_(0) {}

std::uint32_t HysteresisController::required_delay(GearState from, GearState to) const {
    if (from == to) return 0;

    // Protection entry is immediate (fail-closed).
    if (to == GearState::Protection) return 0;

    // Explicit sacred delays from Section 3, Module 1.
    if (from == GearState::EngageSoft && to == GearState::Cruise) {
        return config_.soft_to_cruise_frames;
    }
    if (from == GearState::Cruise && to == GearState::Overdrive) {
        return config_.cruise_to_overdrive_frames;
    }
    if (from == GearState::Overdrive && to == GearState::Cruise) {
        return config_.overdrive_to_cruise_frames;
    }
    if (from == GearState::Protection && to == GearState::Cruise) {
        return config_.protection_to_cruise_frames;
    }

    // Up-shifts from Idle and down-shifts are not delayed per the spec tables.
    if (from == GearState::Idle && to == GearState::EngageSoft) return 0;
    if (from == GearState::EngageSoft && to == GearState::Idle) return 0;
    if (from == GearState::Cruise && to == GearState::EngageSoft) return 0;

    // Default anti-oscillation guard for any other transition.
    return config_.min_frames_between_changes;
}

GearState HysteresisController::update(GearState current, GearState desired) {
    ++frames_since_last_change_;

    if (desired != last_desired_) {
        last_desired_ = desired;
        stability_frames_ = 0;
    }
    ++stability_frames_;

    std::uint32_t delay = required_delay(current, desired);

    // If the desired state has been stable long enough, allow transition.
    // Delay of N frames means the transition happens on frame N+1.
    if (stability_frames_ > delay && frames_since_last_change_ > delay) {
        if (desired != current) {
            frames_since_last_change_ = 0;
        }
        return desired;
    }

    return current;
}

}  // namespace atgca
