#pragma once

#include "core/config_loader.h"
#include "core/hysteresis_controller.h"
#include "core/state_machine.h"
#include "core/torque_allocator.h"
#include "core/turbine_base.h"
#include "core/types.h"

#include <cstdint>
#include <memory>
#include <vector>

namespace atgca {

class GearboxCore {
public:
    explicit GearboxCore(Config config);

    void register_turbine(TurbineBase* turbine);

    // Advance one frame.
    // thermal_c: current thermal reading in Celsius.
    // crash_flag: true if any turbine or system crash is detected.
    void tick(float delta_time, float thermal_c, bool crash_flag);

    GearState state() const { return current_state_; }
    float gear_ratio() const { return state_machine_.gear_ratio(current_state_); }
    const GearboxTelemetry& telemetry() const { return telemetry_; }
    const std::vector<TurbineTelemetry>& turbine_telemetry() const { return turbine_telemetry_; }

    std::uint64_t frame_id() const { return frame_id_; }

private:
    Config config_;
    StateMachine state_machine_;
    HysteresisController hysteresis_;
    TorqueAllocator allocator_;

    GearState current_state_ = GearState::Idle;
    GearboxTelemetry telemetry_;
    std::vector<TurbineBase*> turbines_;
    std::vector<TurbineTelemetry> turbine_telemetry_;
    std::uint64_t frame_id_ = 0;

    float predict_load() const;
    float compute_health() const;
};

}  // namespace atgca
