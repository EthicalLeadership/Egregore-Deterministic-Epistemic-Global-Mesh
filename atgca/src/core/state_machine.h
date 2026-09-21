#pragma once

#include "core/types.h"

namespace atgca {

class StateMachine {
public:
    explicit StateMachine(const Config& config);

    // Returns the state the gearbox *wants* to be in based on current state
    // and instantaneous conditions. Hysteresis is applied separately.
    GearState desired_state(GearState current,
                            float demand,
                            float predicted_load,
                            float ai_confidence,
                            float thermal_c,
                            float health,
                            bool crash_flag) const;

    float gear_ratio(GearState state) const;

private:
    Config config_;
};

}  // namespace atgca
