.PHONY: help install test tune lint gym public all clean
.DEFAULT_GOAL := help

PY ?= python3
SEEDS ?= 40

help:  ## Show this help
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install:  ## Create a venv and install dependencies
	$(PY) -m venv .venv
	./.venv/bin/pip install --upgrade pip
	./.venv/bin/pip install -r requirements.txt
	@echo "Activate with: source .venv/bin/activate"

test:  ## Unit tests. No browser, no network, no API key (~0.1s)
	$(PY) -m pytest tests/unit -q

tune:  ## Regenerate every number in the README (offline, ~1s)
	$(PY) -m bantay.gym.offline --seeds $(SEEDS)

lint:  ## Validate every Robot keyword resolves, without launching a browser
	$(PY) -m robot --dryrun --outputdir reports/dryrun tests/

gym:  ## Full resilience measurement in a real browser (needs Chrome)
	$(PY) -m bantay.gym.run --seeds $(SEEDS)

resilience:  ## Robot resilience suite against the mutation gym (needs Chrome)
	$(PY) -m robot --listener bantay.listener.BantayListener \
		--outputdir reports/robot tests/gym/

public:  ## Suite against saucedemo.com (needs Chrome + network)
	$(PY) -m robot --listener bantay.listener.BantayListener \
		--outputdir reports/robot tests/web/

all: test lint tune  ## Everything that runs without a browser

clean:  ## Remove generated reports
	rm -rf reports/dryrun reports/robot reports/gym reports/tuning reports/patches
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
