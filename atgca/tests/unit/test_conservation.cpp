#include "test_registry.h"
#include "mock_turbine.h"
#include "core/gearbox_core.h"
#include "core/config_loader.h"

#include <cmath>
#include <sstream>

using namespace atgca;

TEST(conservation_1000_frames) {
    Config cfg = ConfigLoader::load_default();
    GearboxCore gearbox(cfg);

    MockTurbine render("render", 0.9f, true, 0.5f, 1.0f, 1.0f, 0.8f);
    MockTurbine ai("ai", 0.8f, false, 0.4f, 0.9f, 1.0f, 0.7f);
    MockTurbine io("io", 0.7f, true, 0.3f, 0.95f, 1.0f, 0.6f);

    gearbox.register_turbine(&render);
    gearbox.register_turbine(&ai);
    gearbox.register_turbine(&io);

    for (int frame = 0; frame < 1000; ++frame) {
        // Vary demand sinusoidally to stress the allocator.
        float t = static_cast<float>(frame) / 100.0f;
        render.set_request(0.3f + 0.2f * std::sin(t));
        ai.set_request(0.2f + 0.15f * std::cos(t * 1.3f));
        io.set_request(0.1f + 0.1f * std::sin(t * 0.7f));

        gearbox.tick(1.0f / 60.0f, 40.0f, false);

        float total_allocated = 0.0f;
        for (const auto& tt : gearbox.turbine_telemetry()) {
            total_allocated += tt.allocated_torque;
        }

        float ratio = gearbox.gear_ratio();
        float t_avail = cfg.torque.max_total * ratio;

        if (t_avail > k_epsilon) {
            float error = std::fabs(total_allocated - t_avail) / t_avail;
            ASSERT_TRUE(error <= k_torque_conservation_target,
                        "Frame " + std::to_string(frame) +
                        " conservation error " + std::to_string(error) + " exceeds 1%");
            ASSERT_TRUE(gearbox.telemetry().conservation_error <= k_torque_conservation_target,
                        "Gearbox telemetry conservation error exceeds 1%");
        }

        // Per-turbine allocations must be non-negative and bounded.
        for (const auto& tt : gearbox.turbine_telemetry()) {
            ASSERT_TRUE(tt.allocated_torque >= 0.0f);
            ASSERT_TRUE(tt.allocated_torque <= 1.0f);
        }
    }
}

TEST(conservation_zero_demand_banks_all) {
    Config cfg = ConfigLoader::load_default();
    GearboxCore gearbox(cfg);

    MockTurbine render("render", 0.9f, true, 0.0f, 1.0f, 1.0f, 0.5f);
    gearbox.register_turbine(&render);

    gearbox.tick(1.0f / 60.0f, 40.0f, false);

    ASSERT_TRUE(gearbox.turbine_telemetry()[0].allocated_torque <= k_epsilon);
}

TEST(conservation_no_turbines) {
    Config cfg = ConfigLoader::load_default();
    GearboxCore gearbox(cfg);

    gearbox.tick(1.0f / 60.0f, 40.0f, false);

    ASSERT_EQ(gearbox.state(), GearState::Idle);
}

