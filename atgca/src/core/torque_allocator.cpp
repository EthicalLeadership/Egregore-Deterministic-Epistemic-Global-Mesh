#include "core/torque_allocator.h"

#include <algorithm>
#include <cmath>

namespace atgca {

TorqueAllocator::TorqueAllocator(const TorqueConfig& config) : config_(config), banked_torque_(0.0f) {}

float TorqueAllocator::allocate(std::vector<TurbineTelemetry>& turbines, float gear_ratio) {
    const float t_avail = config_.max_total * gear_ratio;

    if (turbines.empty()) {
        banked_torque_ = t_avail;
        return 0.0f;
    }

    // Compute weights: P_i * E_i * R_i
    float sum_weights = 0.0f;
    for (auto& t : turbines) {
        float weight = t.priority * t.efficiency * t.requested_torque;
        if (weight < 0.0f) weight = 0.0f;
        sum_weights += weight;
    }

    if (sum_weights <= k_epsilon) {
        // No demand: bank everything.
        for (auto& t : turbines) {
            t.allocated_torque = 0.0f;
        }
        banked_torque_ = t_avail;
        return 0.0f;
    }

    // Raw allocation according to the conservation formula.
    std::vector<float> raw(turbines.size());
    for (std::size_t i = 0; i < turbines.size(); ++i) {
        raw[i] = (turbines[i].priority * turbines[i].efficiency * turbines[i].requested_torque) / sum_weights * t_avail;
    }

    // Apply minimum allocation floor for active turbines without over-drawing.
    float extra_needed = 0.0f;
    std::vector<std::size_t> active_indices;
    for (std::size_t i = 0; i < turbines.size(); ++i) {
        if (turbines[i].requested_torque > k_epsilon) {
            active_indices.push_back(i);
            if (raw[i] < config_.min_alloc) {
                extra_needed += config_.min_alloc - raw[i];
                raw[i] = config_.min_alloc;
            }
        } else {
            raw[i] = 0.0f;
        }
    }

    // If floors caused over-allocation, reclaim from turbines that got more than min_alloc.
    if (extra_needed > k_epsilon) {
        float reclaimable = 0.0f;
        for (std::size_t i : active_indices) {
            if (raw[i] > config_.min_alloc) {
                reclaimable += raw[i] - config_.min_alloc;
            }
        }

        if (reclaimable >= extra_needed) {
            // Reclaim proportionally from above-min allocations.
            for (std::size_t i : active_indices) {
                if (raw[i] > config_.min_alloc) {
                    float above = raw[i] - config_.min_alloc;
                    raw[i] -= above * (extra_needed / reclaimable);
                }
            }
        } else {
            // Cannot satisfy all floors without over-allocation; scale down uniformly.
            // This is a degraded but bounded case.
            for (std::size_t i : active_indices) {
                raw[i] *= t_avail / (t_avail + extra_needed);
            }
        }
    }

    // Clamp and compute final sum.
    float sum_allocated = 0.0f;
    for (std::size_t i = 0; i < turbines.size(); ++i) {
        raw[i] = std::clamp(raw[i], 0.0f, 1.0f);
        turbines[i].allocated_torque = raw[i];
        sum_allocated += raw[i];
    }

    // Enforce hard conservation ceiling.
    if (sum_allocated > t_avail && t_avail > k_epsilon) {
        float scale = t_avail / sum_allocated;
        sum_allocated = 0.0f;
        for (auto& t : turbines) {
            t.allocated_torque *= scale;
            sum_allocated += t.allocated_torque;
        }
    }

    banked_torque_ = std::max(0.0f, t_avail - sum_allocated);

    if (t_avail <= k_epsilon) {
        return 0.0f;
    }
    return std::fabs(sum_allocated - t_avail) / t_avail;
}

}  // namespace atgca
