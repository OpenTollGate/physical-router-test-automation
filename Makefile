.PHONY: smoke critical extended api phone smoke-phone test \
        smoke-mac critical-mac extended-mac api-mac test-mac \
        smoke-linux critical-linux api-linux test-linux \
        luci deploy setup sanitize publish clean

# --- Pytest test tiers ---

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

# --- Playwright LuCI tests ---

luci:
	npx playwright test

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

RESULTS_DIR := results

sanitize:
	@run=$$(ls -dt $(RESULTS_DIR)/test-* 2>/dev/null | head -1); \
	if [ -z "$$run" ]; then echo "No results to sanitize"; exit 1; fi; \
	bash scripts/sanitize-results.sh "$$run/raw" "$$run/sanitized"

publish:
	@run=$$(ls -dt $(RESULTS_DIR)/test-* 2>/dev/null | head -1); \
	if [ -z "$$run" ]; then echo "No results to publish"; exit 1; fi; \
	bash scripts/publish-report.sh "$$run"

clean:
	rm -rf $(RESULTS_DIR)/test-*
	rm -f report.html
	rm -rf .pytest_cache __pycache__
