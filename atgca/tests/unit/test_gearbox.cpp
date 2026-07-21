#include "test_registry.h"
#include "mock_turbine.h"
#include "core/gearbox_core.h"
#include "core/config_loader.h"

#include <cmath>
#include <sstream>
#include <string>

using namespace atgca;

namespace {

std::string serialize_telemetry(const GearboxCore& gearbox) {
    std::ostringstream oss;
    const auto& gt = gearbox.telemetry();
    oss << static_cast<int>(gt.current_state) << ","
        << gt.gear_ratio << ","
        << gt.total_torque << ","
        << gt.hysteresis_timer << ","
        << gt.thermal_envelope_c << ","
        << gt.conservation_error;

    for (const auto& tt : gearbox.turbine_telemetry()) {
        oss << "|" << tt.turbine_id << ","
            << tt.requested_torque << ","
            << tt.allocated_torque << ","
            << tt.efficiency << ","
            << tt.health << ","
            << tt.priority << ","
            << tt.relevance << ","
            << tt.heat_index << ","
            << (tt.critical ? 1 : 0);
    }
    return oss.str();
}

void run_deterministic_scenario(std::string& output) {
    Config cfg = ConfigLoader::load_default();
    GearboxCore gearbox(cfg);

    MockTurbine render("render", 0.9f, true, 0.5f, 1.0f, 1.0f, 0.8f);
    MockTurbine ai("ai", 0.8f, false, 0.3f, 0.9f, 1.0f, 0.7f);
    MockTurbine io("io", 0.7f, true, 0.2f, 0.95f, 1.0f, 0.6f);

    gearbox.register_turbine(&render);
    gearbox.register_turbine(&ai);
    gearbox.register_turbine(&io);

    std::ostringstream oss;
    for (int frame = 0; frame < 120; ++frame) {
        float t = static_cast<float>(frame) / 20.0f;
        render.set_request(0.3f + 0.2f * std::sin(t));
        ai.set_request(0.2f + 0.1f * std::cos(t * 1.5f));
        io.set_request(0.1f + 0.05f * std::sin(t * 0.5f));

        float thermal = 40.0f + 5.0f * std::sin(t * 0.25f);
        gearbox.tick(1.0f / 60.0f, thermal, false);
        oss << gearbox.frame_id() << ":" << serialize_telemetry(gearbox) << "\n";
    }
    output = oss.str();
}

}  // namespace

TEST(determinism_same_seed_same_output) {
    std::string run_a;
    std::string run_b;
    run_deterministic_scenario(run_a);
    run_deterministic_scenario(run_b);

    ASSERT_EQ(run_a, run_b);
}

TEST(determinism_thermal_surge_is_repeatable) {
    Config cfg = ConfigLoader::load_default();

    auto run = [&]() {
        GearboxCore gearbox(cfg);
        MockTurbine t("render", 0.9f, true, 0.6f, 1.0f, 1.0f, 0.8f);
        gearbox.register_turbine(&t);

        for (int i = 0; i < 60; ++i) {
            gearbox.tick(1.0f / 60.0f, (i == 30) ? 90.0f : 40.0f, false);
        }
        return serialize_telemetry(gearbox);
    };

    ASSERT_EQ(run(), run());
}
