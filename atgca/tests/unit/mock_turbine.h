#pragma once

#include "core/turbine_base.h"
#include "core/types.h"

#include <string>

namespace atgca {

class MockTurbine : public TurbineBase {
public:
    MockTurbine(std::string id, float priority, bool critical,
                float request, float efficiency, float health, float relevance)
        : TurbineBase(std::move(id), priority, critical),
          request_(request),
          efficiency_(efficiency),
          health_(health),
          relevance_(relevance),
          executed_torque_(0.0f) {}

    void set_request(float request) { request_ = request; }
    void set_efficiency(float efficiency) { efficiency_ = efficiency; }
    void set_health(float health) { health_ = health; }
    void set_relevance(float relevance) { relevance_ = relevance; }

    void execute(float allocated_torque) override {
        executed_torque_ = allocated_torque;
    }

    float request_torque() override { return request_; }

    void update_telemetry() override {}

    TurbineTelemetry get_telemetry() const override {
        TurbineTelemetry tt;
        tt.turbine_id = id_;
        tt.requested_torque = request_;
        tt.allocated_torque = executed_torque_;
        tt.efficiency = efficiency_;
        tt.health = health_;
        tt.priority = priority_;
        tt.relevance = relevance_;
        tt.heat_index = 0.0f;
        tt.critical = critical_;
        return tt;
    }

    bool is_critical() const override { return critical_; }

    float executed_torque() const { return executed_torque_; }

private:
    float request_ = 0.0f;
    float efficiency_ = 1.0f;
    float health_ = 1.0f;
    float relevance_ = 0.5f;
    float executed_torque_ = 0.0f;
};

}  // namespace atgca
