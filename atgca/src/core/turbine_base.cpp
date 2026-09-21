#include "core/turbine_base.h"

namespace atgca {

TurbineBase::TurbineBase(std::string id, float priority, bool critical)
    : id_(std::move(id)), priority_(priority), critical_(critical) {}

}  // namespace atgca
