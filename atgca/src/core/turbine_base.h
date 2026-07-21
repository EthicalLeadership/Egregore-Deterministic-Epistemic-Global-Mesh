#pragma once

#include "core/types.h"

#include <string>

namespace atgca {

class TurbineBase {
public:
    explicit TurbineBase(std::string id, float priority = 0.5f, bool critical = false);
    virtual ~TurbineBase() = default;

    // Core contract.
    virtual void execute(float allocated_torque) = 0;
    virtual float request_torque() = 0;
    virtual void update_telemetry() = 0;
    virtual TurbineTelemetry get_telemetry() const = 0;
    virtual bool is_critical() const = 0;

    const std::string& id() const { return id_; }
    float priority() const { return priority_; }

protected:
    std::string id_;
    float priority_ = 0.5f;
    bool critical_ = false;
};

}  // namespace atgca
