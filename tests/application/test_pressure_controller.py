from egregore.application.pressure_controller import PressureConfig, PressureController
from egregore.infrastructure.metrics.tu_metrics import EpochMetrics


class TestPressureController:
    def test_idle_shrink(self):
        ctrl = PressureController()
        metrics = EpochMetrics(
            epoch_number=1,
            duration_ms=100,
            work_units_submitted=10,
            work_units_admitted=1,
            work_units_rejected=1,
            tu_allocated=5,
            tu_remaining=95,
            dt_consumed=1.0,
        )
        rec = ctrl.evaluate(metrics, total_tu=100, max_backlog=100)
        assert rec == -1  # idle, some backlog

    def test_overload_expand(self):
        ctrl = PressureController()
        metrics = EpochMetrics(
            epoch_number=1,
            duration_ms=100,
            work_units_submitted=50,
            work_units_admitted=45,
            work_units_rejected=5,
            tu_allocated=95,
            tu_remaining=5,
            dt_consumed=5.0,
        )
        rec = ctrl.evaluate(metrics, total_tu=100, max_backlog=100)
        assert rec == 1  # overloaded

    def test_optimal_hold(self):
        ctrl = PressureController()
        metrics = EpochMetrics(
            epoch_number=1,
            duration_ms=100,
            work_units_submitted=10,
            work_units_admitted=10,
            work_units_rejected=0,
            tu_allocated=80,
            tu_remaining=20,
            dt_consumed=2.0,
        )
        rec = ctrl.evaluate(metrics, total_tu=100, max_backlog=100)
        assert rec == 0  # optimal

    def test_rate_limit(self):
        ctrl = PressureController(PressureConfig(max_lane_delta_per_epoch=1))
        metrics = EpochMetrics(
            epoch_number=1,
            duration_ms=100,
            work_units_submitted=100,
            work_units_admitted=90,
            work_units_rejected=10,
            tu_allocated=99,
            tu_remaining=1,
            dt_consumed=10.0,
        )
        rec = ctrl.evaluate(metrics, total_tu=100, max_backlog=100)
        assert abs(rec) <= 1

    def test_trend(self):
        ctrl = PressureController()
        for i in range(5):
            metrics = EpochMetrics(
                epoch_number=i + 1,
                duration_ms=100,
                work_units_submitted=10,
                work_units_admitted=10,
                work_units_rejected=0,
                tu_allocated=80,
                tu_remaining=20,
                dt_consumed=2.0,
            )
            ctrl.evaluate(metrics, total_tu=100, max_backlog=100)
        trend = ctrl.get_trend()
        assert trend["status"] == "OK"
        assert trend["epochs_tracked"] == 5
