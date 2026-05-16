.PHONY: smoke critical extended api phone smoke-phone test \
        smoke-mac critical-mac extended-mac api-mac test-mac \
        smoke-linux critical-linux api-linux test-linux \
        smoke-rust api-rust test-rust critical-rust \
        luci deploy setup sanitize publish clean \
        run-api run-phone run-luci run-all \
        collect render-report pr-smoke

# --- Pytest test tiers (raw pytest, no canonical run dir) ---

smoke:
	pytest -m smoke

critical:
	pytest -m critical

extended:
	pytest -m extended

api:
	pytest -m api

phone:
	pytest -m phone --publish

smoke-phone:
	pytest -m phone --quick-phone --publish

test:
	pytest

# --- macOS client (no phone) ---

smoke-mac:
	pytest -m smoke --client=mac

critical-mac:
	pytest -m critical --client=mac

api-mac:
	pytest -m api --client=mac

test-mac:
	pytest --client=mac

# --- Linux client (no phone) ---

smoke-linux:
	pytest -m smoke --client=linux

api-linux:
	pytest -m api --client=linux

test-linux:
	pytest --client=linux

# --- Rust v1 backend ---

smoke-rust:
	TOLLGATE_BACKEND=rust pytest -m smoke --backend=rust

api-rust:
	TOLLGATE_BACKEND=rust pytest -m api --backend=rust

test-rust:
	TOLLGATE_BACKEND=rust pytest --backend=rust

critical-rust:
	TOLLGATE_BACKEND=rust pytest -m critical --backend=rust

# --- Canonical run dir targets ---

run-api:
	./scripts/run-api.sh

run-phone:
	./scripts/run-phone.sh

run-luci:
	./scripts/run-tests.sh

run-all:
	./scripts/run-all.sh

# --- Playwright LuCI tests (legacy) ---

luci:
	npx playwright test

# --- Collect and render from latest run ---

RESULTS_DIR := results

collect:
	@run=$$(ls -dt $(RESULTS_DIR)/*/ 2>/dev/null | head -1); \
	if [ -z "$$run" ]; then echo "No results to collect"; exit 1; fi; \
	python3 scripts/collect-results.py --run-dir "$$run" --allow-failures

render-report:
	@run=$$(ls -dt $(RESULTS_DIR)/*/ 2>/dev/null | head -1); \
	if [ -z "$$run" ]; then echo "No results to render"; exit 1; fi; \
	python3 scripts/render-report.py --run-dir "$$run"

# --- Deploy ---

deploy:
	bash scripts/deploy.sh

# --- Setup ---

setup:
	pip install -r requirements.txt
	npm install
	npx playwright install

setup-python:
	bash scripts/setup-python.sh

# --- Results pipeline ---

sanitize:
	@run=$$(ls -dt $(RESULTS_DIR)/*/ 2>/dev/null | head -1); \
	if [ -z "$$run" ]; then echo "No results to sanitize"; exit 1; fi; \
	bash scripts/sanitize-results.sh "$$run"

publish:
	@run=$$(ls -dt $(RESULTS_DIR)/*/ 2>/dev/null | head -1); \
	if [ -z "$$run" ]; then echo "No results to publish"; exit 1; fi; \
	bash scripts/publish-report.sh "$$run"

# --- PR smoke test ---

pr-smoke:
	@echo "Usage: ./scripts/test-pr.sh --pr <N> [--reset] [--test api|all] [--publish]"

# --- Clean ---

clean:
	rm -rf $(RESULTS_DIR)/*
	rm -f report.html
	rm -rf .pytest_cache __pycache__
