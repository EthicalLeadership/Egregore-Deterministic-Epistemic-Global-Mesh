#include "core/gearbox_core.h"

#include <algorithm>
#include <cmath>
#include <limits>

namespace atgca {

GearboxCore::GearboxCore(Config config)
    : config_(std::move(config)),
      state_machine_(config_),
      hysteresis_(config_.hysteresis),
      allocator_(config_.torque),
      current_state_(GearState::Idle),
      telemetry_() {}

void GearboxCore::register_turbine(TurbineBase* turbine) {
    if (turbine == nullptr) return;
    if (turbines_.size() >= config_.system.max_turbines) return;
    turbines_.push_back(turbine);
    TurbineTelemetry tt;
    tt.turbine_id = turbine->id();
    tt.priority = turbine->priority();
    tt.critical = turbine->is_critical();
    turbine_telemetry_.push_back(tt);
}

float GearboxCore::predict_load() const {
    if (turbine_telemetry_.empty()) return 0.0f;
    float sum = 0.0f;
    for (const auto& t : turbine_telemetry_) {
        sum += t.relevance * t.priority;
    }
    float predicted = sum / static_cast<float>(turbine_telemetry_.size());
    return std::clamp(predicted, 0.0f, 1.0f);
}

float GearboxCore::compute_health() const {
    if (turbine_telemetry_.empty()) return 1.0f;
    float sum = 0.0f;
    for (const auto& t : turbine_telemetry_) {
        sum += t.health;
    }
    return sum / static_cast<float>(turbine_telemetry_.size());
}

void GearboxCore::tick(float delta_time, float thermal_c, bool crash_flag) {
    ++frame_id_;

    // 1. Gather requests and update per-turbine telemetry.
    float total_demand = 0.0f;
    for (std::size_t i = 0; i < turbines_.size(); ++i) {
        turbines_[i]->update_telemetry();
        TurbineTelemetry tt = turbines_[i]->get_telemetry();
        // Preserve registered priority/critical metadata from the turbine object.
        tt.turbine_id = turbines_[i]->id();
        tt.priority = turbines_[i]->priority();
        tt.critical = turbines_[i]->is_critical();
        tt.requested_torque = turbines_[i]->request_torque();
        turbine_telemetry_[i] = tt;
        total_demand += tt.requested_torque;
    }

    // 2. Predict next frame.
    float predicted_load = predict_load();
    float ai_confidence = 0.9f;  // Phase 1 heuristic confidence.

    // 3. Update gearbox state with hysteresis.
    float demand = total_demand;
    float health = compute_health();
    GearState desired = state_machine_.desired_state(current_state_, demand, predicted_load, ai_confidence, thermal_c, health, crash_flag);
    GearState next = hysteresis_.update(current_state_, desired);
    current_state_ = next;

    // 4. Allocate torque.
    float ratio = state_machine_.gear_ratio(current_state_);
    float conservation_error = allocator_.allocate(turbine_telemetry_, ratio);

    // 5. Execute turbines with allocated torque.
    for (std::size_t i = 0; i < turbines_.size(); ++i) {
        turbines_[i]->execute(turbine_telemetry_[i].allocated_torque);
    }

    // 6. Collect gearbox telemetry.
    telemetry_.current_state = current_state_;
    telemetry_.gear_ratio = ratio;
    telemetry_.total_torque = config_.torque.max_total;
    telemetry_.hysteresis_timer = static_cast<float>(hysteresis_.stability_frames());
    telemetry_.thermal_envelope_c = thermal_c;
    telemetry_.conservation_error = conservation_error;

    (void)delta_time;  // Reserved for future time-based budgets.
}

}  // namespace atgca
