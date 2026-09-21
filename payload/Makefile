.PHONY: sandbox dashboard test test-tooling lint format help

PYTHON := $(shell test -x .venv/bin/python && echo .venv/bin/python || echo python)
DASHBOARD_HOST ?= 127.0.0.1
DASHBOARD_PORT ?= 8000

help:
	@echo "Egregore build targets"
	@echo "  make sandbox      Run the build-time CBI-0 pipeline sandbox"
	@echo "  make dashboard    Start the Interface Synod dashboard server"
	@echo "  make test         Run the full test suite"
	@echo "  make test-tooling Run tooling tests only"
	@echo "  make lint         Run ruff and black checks"
	@echo "  make format       Run ruff and black formatters"

sandbox:
	$(PYTHON) scripts/pipeline_sandbox.py

dashboard:
	$(PYTHON) scripts/dashboard_server.py --host $(DASHBOARD_HOST) --port $(DASHBOARD_PORT)

test:
	$(PYTHON) -m pytest tests/ -q

test-tooling:
	$(PYTHON) -m pytest tests/tooling/ -q

lint:
	$(PYTHON) -m ruff check src/ tests/
	$(PYTHON) -m black --check src/ tests/

format:
	$(PYTHON) -m ruff format src/ tests/
	$(PYTHON) -m black src/ tests/
