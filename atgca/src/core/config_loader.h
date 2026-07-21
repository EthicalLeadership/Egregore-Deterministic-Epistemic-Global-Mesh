#pragma once

#include "core/types.h"

#include <optional>
#include <string>

namespace atgca {

class ConfigLoader {
public:
    static Config load_default();
    static std::optional<Config> load_from_file(const std::string& path);
};

}  // namespace atgca
