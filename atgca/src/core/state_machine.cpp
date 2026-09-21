#include "core/state_machine.h"

#include <algorithm>

namespace atgca {

StateMachine::StateMachine(const Config& config) : config_(config) {}

GearState StateMachine::desired_state(GearState current,
                                      float demand,
                                      float predicted_load,
                                      float ai_confidence,
                                      float thermal_c,
                                      float health,
                                      bool crash_flag) const {
    // Protection is fail-closed and immediate on any entry condition.
    if (crash_flag || thermal_c >= config_.thermal.protection_threshold_c) {
        return GearState::Protection;
    }

    switch (current) {
        case GearState::Idle: {
            if (demand > 0.1f) {
                return GearState::EngageSoft;
            }
            return GearState::Idle;
        }

        case GearState::EngageSoft: {
            if (demand > 0.4f) {
                return GearState::Cruise;
            }
            if (demand <= 0.1f) {
                return GearState::Idle;
            }
            return GearState::EngageSoft;
        }

        case GearState::Cruise: {
            if (demand > 0.8f && predicted_load > 0.8f && ai_confidence > 0.85f) {
                return GearState::Overdrive;
            }
            if (demand < 0.2f) {
                return GearState::EngageSoft;
            }
            return GearState::Cruise;
        }

        case GearState::Overdrive: {
            if (predicted_load < 0.6f) {
                return GearState::Cruise;
            }
            return GearState::Overdrive;
        }

        case GearState::Protection: {
            // Exit only when healthy and cool.
            if (health > 0.9f && thermal_c <= config_.thermal.recovery_threshold_c) {
                return GearState::Cruise;
            }
            return GearState::Protection;
        }
    }

    return GearState::Idle;
}

float StateMachine::gear_ratio(GearState state) const {
    switch (state) {
        case GearState::Idle: return config_.gear_ratios.idle;
        case GearState::EngageSoft: return config_.gear_ratios.engage_soft;
        case GearState::Cruise: return config_.gear_ratios.cruise;
        case GearState::Overdrive: return config_.gear_ratios.overdrive;
        case GearState::Protection: return config_.gear_ratios.protection;
    }
    return 0.0f;
}

}  // namespace atgca
