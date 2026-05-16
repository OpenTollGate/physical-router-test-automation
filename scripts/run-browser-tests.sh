#!/usr/bin/env bash
set -euo pipefail

HOST="${TOLLGATE_BROWSER_HOST:-218}"
REMOTE_DIR="/tmp/tg-playwright"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
RESULTS_DIR="$REPO_DIR/results/browser"

export TOLLGATE_NDS_URL="${TOLLGATE_NDS_URL:-http://192.168.1.1:2050}"
export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-/home/ubuntu/.cache/ms-playwright}"

if [ "$(hostname -s 2>/dev/null || echo local)" = "$HOST" ] || [ "$(hostname -I 2>/dev/null | awk '{print $1}')" = "$HOST" ]; then
	echo "==> Running locally on $HOST"
	CONFIG="playwright.config-browser.js"
else
	echo "==> Deploying to $HOST:$REMOTE_DIR"
	ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 "$HOST" "mkdir -p $REMOTE_DIR"
	rsync -az --delete \
		--include='package.json' \
		--include='package-lock.json' \
		--include='playwright.config-browser.js' \
		--include='tests/browser/***' \
		--include='node_modules/***' \
		--exclude='*' \
		"$REPO_DIR/" "$HOST:$REMOTE_DIR/"
	CONFIG="$REMOTE_DIR/playwright.config-browser.js"
fi

echo "==> Running captive portal browser tests..."
echo "    NDS URL: $TOLLGATE_NDS_URL"
echo "    Browsers: $PLAYWRIGHT_BROWSERS_PATH"

REMOTE_CMD="cd $REMOTE_DIR && \
	sudo PLAYWRIGHT_BROWSERS_PATH=$PLAYWRIGHT_BROWSERS_PATH \
	TOLLGATE_NDS_URL=$TOLLGATE_NDS_URL \
	ip netns exec tg-poc-client \
	npx playwright test --config=$CONFIG \
	${*:-}"

if [ "$(hostname -s 2>/dev/null || echo local)" = "$HOST" ] || [ "$(hostname -I 2>/dev/null | awk '{print $1}')" = "$HOST" ]; then
	bash -c "$REMOTE_CMD"
else
	ssh -t -o StrictHostKeyChecking=no "$HOST" "$REMOTE_CMD"
fi

echo "==> Copying results back..."
mkdir -p "$RESULTS_DIR"

if [ "$(hostname -s 2>/dev/null || echo local)" != "$HOST" ] && [ "$(hostname -I 2>/dev/null | awk '{print $1}')" != "$HOST" ]; then
	rsync -az "$HOST:$REMOTE_DIR/results/browser/" "$RESULTS_DIR/"
fi

echo "==> Done. Results: $RESULTS_DIR"
