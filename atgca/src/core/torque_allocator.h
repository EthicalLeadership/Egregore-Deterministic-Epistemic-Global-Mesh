#pragma once

#include "core/types.h"

#include <vector>

namespace atgca {

class TorqueAllocator {
public:
    explicit TorqueAllocator(const TorqueConfig& config);

    // Allocate torque envelopes to turbines. Modifies telemetry in place.
    // Returns the conservation error (|Σ allocated - available| / available).
    float allocate(std::vector<TurbineTelemetry>& turbines, float gear_ratio);

    float banked_torque() const { return banked_torque_; }

private:
    TorqueConfig config_;
    float banked_torque_ = 0.0f;
};

}  // namespace atgca
