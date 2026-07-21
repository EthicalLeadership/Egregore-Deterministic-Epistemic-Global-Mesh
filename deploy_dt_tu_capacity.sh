#!/usr/bin/env bash
# BLACKSTAR DT/TU CAPACITY MODEL DEPLOYMENT SCRIPT
# Run this on Pioneer 1 from ~/egregore

set -euo pipefail

echo "=== BLACKSTAR DT/TU CAPACITY MODEL DEPLOYMENT ==="

SRC="/mnt/agents/output/egregore_dt_tu_build"
DEST="$HOME/egregore"

if [ ! -d "$SRC" ]; then
    echo "ERROR: Source directory not found: $SRC"
    echo "This script must be run after the build files are generated."
    exit 1
fi

echo "Copying source files..."

# Domain layer
cp "$SRC/src/egregore/domain/units.py" "$DEST/src/egregore/domain/"
cp "$SRC/src/egregore/domain/work_unit.py" "$DEST/src/egregore/domain/"
cp "$SRC/src/egregore/domain/work_unit_defaults.py" "$DEST/src/egregore/domain/"

# Interface layer
cp "$SRC/src/egregore/interface/admission_ports.py" "$DEST/src/egregore/interface/"
cp "$SRC/src/egregore/interface/model_host_ports.py" "$DEST/src/egregore/interface/"

# Application layer
cp "$SRC/src/egregore/application/admission_controller.py" "$DEST/src/egregore/application/"
cp "$SRC/src/egregore/application/capacity_orchestrator.py" "$DEST/src/egregore/application/"

# Kernel layer
cp "$SRC/src/egregore/kernel/scheduler/tu_budget.py" "$DEST/src/egregore/kernel/scheduler/"
cp "$SRC/src/egregore/kernel/scheduler/epoch_scheduler.py" "$DEST/src/egregore/kernel/scheduler/"
cp "$SRC/src/egregore/kernel/scheduler/dt_monitor.py" "$DEST/src/egregore/kernel/scheduler/"
cp "$SRC/src/egregore/kernel/scheduler/powertrain_coupling.py" "$DEST/src/egregore/kernel/scheduler/"

# Infrastructure layer
cp "$SRC/src/egregore/infrastructure/metrics/tu_metrics.py" "$DEST/src/egregore/infrastructure/metrics/"
cp "$SRC/src/egregore/infrastructure/metrics/thermal_monitor.py" "$DEST/src/egregore/infrastructure/metrics/"
cp "$SRC/src/egregore/infrastructure/distributed/cluster_aggregator.py" "$DEST/src/egregore/infrastructure/distributed/"
cp "$SRC/src/egregore/infrastructure/model_host/llama_cpp_host.py" "$DEST/src/egregore/infrastructure/model_host/"

# Tests
cp "$SRC/tests/domain/test_units.py" "$DEST/tests/domain/"
cp "$SRC/tests/domain/test_work_unit.py" "$DEST/tests/domain/"
cp "$SRC/tests/application/test_admission_controller.py" "$DEST/tests/application/"
cp "$SRC/tests/application/test_capacity_orchestrator.py" "$DEST/tests/application/"
cp "$SRC/tests/kernel/scheduler/test_tu_budget.py" "$DEST/tests/kernel/scheduler/"
cp "$SRC/tests/kernel/scheduler/test_dt_monitor.py" "$DEST/tests/kernel/scheduler/"
cp "$SRC/tests/infrastructure/metrics/test_tu_metrics.py" "$DEST/tests/infrastructure/metrics/"
cp "$SRC/tests/infrastructure/metrics/test_thermal_monitor.py" "$DEST/tests/infrastructure/metrics/"
cp "$SRC/tests/infrastructure/distributed/test_cluster_aggregator.py" "$DEST/tests/infrastructure/distributed/"
cp "$SRC/tests/interface/test_model_host_ports.py" "$DEST/tests/interface/"
cp "$SRC/tests/test_tu_validation.py" "$DEST/tests/"

echo ""
echo "=== DEPLOYMENT COMPLETE ==="
echo "Files copied: 22 source + 11 test = 33 files"
echo ""
echo "Next steps:"
echo "  1. source .venv/bin/activate"
echo "  2. PYTHONPATH=src python -m pytest tests/domain/test_units.py -v"
echo "  3. PYTHONPATH=src python -m pytest tests/ -v"
