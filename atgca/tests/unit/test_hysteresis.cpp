#include "test_registry.h"
#include "mock_turbine.h"
#include "core/gearbox_core.h"
#include "core/config_loader.h"
#include "core/hysteresis_controller.h"

using namespace atgca;

TEST(hysteresis_engage_soft_to_cruise_requires_10_frames) {
    Config cfg = ConfigLoader::load_default();
    GearboxCore gearbox(cfg);

    MockTurbine t("render", 0.9f, true, 0.5f, 1.0f, 1.0f, 0.5f);
    gearbox.register_turbine(&t);

    // Demand > 0.4 should eventually push to Cruise, but must wait 10 frames in EngageSoft.
    for (int i = 0; i < 13; ++i) {
        gearbox.tick(1.0f / 60.0f, 40.0f, false);
        if (i < 11) {
            ASSERT_TRUE(gearbox.state() != GearState::Cruise,
                        "Transitioned to Cruise too early at frame " + std::to_string(i));
        }
    }
    ASSERT_EQ(gearbox.state(), GearState::Cruise);
}

TEST(hysteresis_cruise_to_overdrive_requires_6_frames) {
    Config cfg = ConfigLoader::load_default();
    GearboxCore gearbox(cfg);

    MockTurbine t("render", 0.9f, true, 0.5f, 1.0f, 1.0f, 0.5f);
    gearbox.register_turbine(&t);

    // Reach Cruise with moderate demand.
    for (int i = 0; i < 15; ++i) {
        gearbox.tick(1.0f / 60.0f, 40.0f, false);
    }
    ASSERT_EQ(gearbox.state(), GearState::Cruise);

    // Now demand surge with high AI confidence proxy (relevance = 1.0).
    t.set_request(0.9f);
    t.set_relevance(1.0f);

    int cruise_count = 0;
    for (int i = 0; i < 12; ++i) {
        gearbox.tick(1.0f / 60.0f, 40.0f, false);
        if (gearbox.state() == GearState::Cruise) ++cruise_count;
    }

    // Must spend at least 6 frames in Cruise before Overdrive.
    ASSERT_TRUE(cruise_count >= 6,
                "Moved to Overdrive before 6-frame hysteresis delay");
    ASSERT_EQ(gearbox.state(), GearState::Overdrive);
}

TEST(hysteresis_protection_is_immediate) {
    Config cfg = ConfigLoader::load_default();
    GearboxCore gearbox(cfg);

    MockTurbine t("render", 0.9f, true, 0.5f, 1.0f, 1.0f, 0.5f);
    gearbox.register_turbine(&t);

    // Run a few frames at normal temp to leave Idle.
    for (int i = 0; i < 5; ++i) {
        gearbox.tick(1.0f / 60.0f, 40.0f, false);
    }

    // Thermal runaway should force Protection immediately.
    gearbox.tick(1.0f / 60.0f, 90.0f, false);
    ASSERT_EQ(gearbox.state(), GearState::Protection);
}

TEST(hysteresis_protection_exit_requires_20_frames) {
    Config cfg = ConfigLoader::load_default();
    GearboxCore gearbox(cfg);

    MockTurbine t("render", 0.9f, true, 0.5f, 1.0f, 1.0f, 0.5f);
    gearbox.register_turbine(&t);

    // Enter Protection.
    gearbox.tick(1.0f / 60.0f, 90.0f, false);
    ASSERT_EQ(gearbox.state(), GearState::Protection);

    // Recover thermal and health.
    for (int i = 0; i < 25; ++i) {
        gearbox.tick(1.0f / 60.0f, 40.0f, false);
        if (i < 20) {
            ASSERT_TRUE(gearbox.state() == GearState::Protection,
                        "Left Protection too early at frame " + std::to_string(i));
        }
    }
    ASSERT_EQ(gearbox.state(), GearState::Cruise);
}

TEST(hysteresis_bypass_attempt_fails) {
    HysteresisController hc(ConfigLoader::load_default().hysteresis);

    // Start in EngageSoft, immediately demand Cruise.
    GearState current = GearState::EngageSoft;
    GearState desired = GearState::Cruise;

    // First frame must not transition.
    GearState next = hc.update(current, desired);
    ASSERT_EQ(next, GearState::EngageSoft);

    // Second frame still too early.
    next = hc.update(current, desired);
    ASSERT_EQ(next, GearState::EngageSoft);
}
