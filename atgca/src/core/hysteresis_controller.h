#pragma once

#include "core/types.h"

namespace atgca {

class HysteresisController {
public:
    explicit HysteresisController(const HysteresisConfig& config);

    // Returns the actual next state, applying frame delays.
    GearState update(GearState current, GearState desired);

    // Current stability counter for the last desired state.
    std::uint32_t stability_frames() const { return stability_frames_; }

    std::uint32_t required_delay(GearState from, GearState to) const;

private:
    HysteresisConfig config_;
    GearState last_desired_ = GearState::Idle;
    std::uint32_t stability_frames_ = 0;
    std::uint32_t frames_since_last_change_ = 0;
};

}  // namespace atgca
