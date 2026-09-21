from egregore.infrastructure.metrics.tu_metrics import EpochMetrics, TUMetricsCollector


class TestTUMetricsCollector:
    def test_empty_report(self):
        collector = TUMetricsCollector()
        report = collector.generate_report()
        assert report["status"] == "NO_DATA"

    def test_record_epoch(self):
        collector = TUMetricsCollector()
        collector.record_epoch(
            EpochMetrics(
                epoch_number=1,
                duration_ms=100,
                work_units_submitted=10,
                work_units_admitted=8,
                work_units_rejected=2,
                tu_allocated=20,
                tu_remaining=80,
                dt_consumed=5.0,
            )
        )
        report = collector.generate_report()
        assert report["status"] == "OK"
        assert report["total_epochs"] == 1
        assert report["total_admitted"] == 8

    def test_multiple_epochs(self):
        collector = TUMetricsCollector()
        for i in range(5):
            collector.record_epoch(
                EpochMetrics(
                    epoch_number=i + 1,
                    duration_ms=100,
                    work_units_submitted=10,
                    work_units_admitted=9,
                    work_units_rejected=1,
                    tu_allocated=10,
                    tu_remaining=90,
                    dt_consumed=1.0,
                )
            )
        report = collector.generate_report()
        assert report["total_epochs"] == 5
        assert report["admission_rate"] == 0.9
