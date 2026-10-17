.PHONY: install install-dev lint test synth nag plan check cost simulate screenshot clean

PY ?= python3
CDK ?= cdk
export CDK_DISABLE_VERSION_CHECK = 1

install:
	$(PY) -m pip install -r requirements.txt

install-dev:
	$(PY) -m pip install -r requirements-dev.txt

lint:
	$(PY) -m ruff check .
	$(PY) -m ruff format --check .

test:
	$(PY) -m pytest -q

# Produces cdk.out/ without touching any AWS account. This is as far as CI ever goes.
synth:
	$(CDK) synth --no-notices --quiet

# cdk-nag AwsSolutions findings as synth annotations (the test suite asserts there are none).
nag:
	$(CDK) synth --no-notices --quiet -c nag=true

# `cdk diff`/`deploy` need credentials and a bootstrapped account; run them yourself, never from CI.
plan:
	$(CDK) diff --no-notices

check: lint test synth
	$(PY) simulator/simulate.py --dry-run --count 3 > /dev/null

cost:
	$(PY) scripts/cost.py
	@echo
	$(PY) scripts/cost.py --interval 60

simulate:
	$(PY) simulator/simulate.py --dry-run --count 10 --no-sleep

# Local dashboard against a fake API (no AWS): python scripts/mock_api.py, then open the URL.
screenshot:
	$(PY) scripts/screenshot_dashboard.py

clean:
	rm -rf cdk.out .pytest_cache .ruff_cache
	find . -name __pycache__ -type d -exec rm -rf {} +
