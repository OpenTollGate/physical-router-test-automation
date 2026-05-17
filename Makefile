# ---------------------------------------------------------------------------
# Makefile — Top-level test runner for physical router test automation
#
# Wraps mint-health/Makefile and upstream-wifi/Makefile targets into a
# single entry point.  All variables (ROUTER, SSID, PASS, etc.) are
# forwarded to the sub-Makefiles.
#
# Quick reference:
#   make help                         # show all targets
#   make smoke-degraded ROUTER=alpha  # single-router degraded lifecycle (~3 min)
#   make smoke-upstream               # two-router degraded payment (~5 min)
#     make smoke-upstream-full SSID=MyNet PASS=secret ROUTER=alpha  # full upstream suite
#   make smoke-pin-upstream           # two-router pin-upstream test
#   make smoke-dynamic-rebuild ROUTER=alpha  # full→degraded→full lifecycle
#   make smoke-offline ROUTER=alpha   # block + restart + verify degraded
#   make smoke-recovery ROUTER=alpha  # unblock + wait for recovery
#   make test-startup-hygiene ROUTER=alpha  # boot with dead STA, verify switch
#   make test-startup-hygiene-dead-only ROUTER=alpha  # boot with ONLY dead STA
#   make test-captive-portal ROUTER=alpha  # Playwright captive portal tests
#   make test-cashu-payment ROUTER=alpha   # Playwright e2e cashu payment
#   make test-playwright              # Playwright LuCI admin UI tests
#   make test-all ROUTER=alpha        # everything (lint + playwright + smoke-degraded)
#   make deploy ROUTER=alpha          # cross-compile + deploy binaries
#   make status ROUTER=alpha          # check service status
#   make shell ROUTER=alpha           # interactive SSH session
#   make rescue-router ROUTER=beta VIA=alpha  # rescue offline router
#
# Setup:
#   cp mint-health/routers.env.example mint-health/routers.env
#   cp upstream-wifi/routers.env.example upstream-wifi/routers.env
#   npm install && npx playwright install
# ---------------------------------------------------------------------------

ROUTER ?= alpha
SSID   ?=
PASS   ?=
MINT   ?= https://nofee.testnut.cashu.space
VIA    ?=
PHASE  ?=

BOLD   := \033[1m
GREEN  := \033[32m
RED    := \033[31m
YELLOW := \033[33m
CYAN   := \033[36m
RESET  := \033[0m

# ===========================================================================
#  HELP
# ===========================================================================

.PHONY: help
help: ## Show this help
	@echo "$(BOLD)Physical Router Test Automation$(RESET)"
	@echo "$(BOLD)=====================================$(RESET)"
	@echo ""
	@echo "Usage:  make <target> ROUTER=alpha SSID=x PASS=y"
	@echo ""
	@echo "$(CYAN)--- Smoke tests (quick, 2-5 min) ---$(RESET)"
	@grep -E '^[a-z][a-z0-9_-]*:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  $(CYAN)%-40s$(RESET) %s\n", $$1, $$2}'
	@echo ""
	@echo "$(CYAN)--- Full test suites (20+ min) ---$(RESET)"
	@echo "  make full-degraded         ROUTER=alpha    # full degraded lifecycle suite"
	@echo "  make full-upstream         SSID=x PASS=y   # full upstream WiFi suite"
	@echo "  make full-all              ROUTER=alpha     # all suites combined"
	@echo ""
	@echo "$(CYAN)--- Playwright tests ---$(RESET)"
	@echo "  make test-playwright                        # LuCI admin UI tests (requires .env)"
	@echo "  make test-captive-portal  ROUTER=alpha      # captive portal browser tests"
	@echo "  make test-cashu-payment   ROUTER=alpha      # e2e cashu payment via browser"
	@echo ""
	@echo "$(CYAN)--- Router management ---$(RESET)"
	@echo "  make deploy               ROUTER=alpha      # cross-compile + deploy binaries"
	@echo "  make status               ROUTER=alpha      # check service status"
	@echo "  make shell                ROUTER=alpha      # interactive SSH session"
	@echo "  make logs                 ROUTER=alpha      # tail tollgate logs"
	@echo "  make check-sta-health     ROUTER=alpha      # verify no stale/duplicate STAs"
	@echo "  make fix-dns              ROUTER=alpha      # fix NetBird DNS hijack"
	@echo "  make cleanup              ROUTER=alpha      # remove blocks and restore config"
	@echo "  make rescue-router        ROUTER=beta VIA=alpha  # rescue offline router"
	@echo ""
	@echo "$(CYAN)--- Serial console (no-network) ---$(RESET)"
	@echo "  make serial-shell         ROUTER=alpha      # interactive serial console"
	@echo "  make serial-status        ROUTER=alpha      # status via serial"
	@echo "  make serial-cold-boot     ROUTER=alpha      # cold boot test with serial"
	@echo "  make serial-recovery      ROUTER=alpha CMD='wifi reload'  # emergency command"
	@echo ""
	@echo "$(CYAN)--- Router mutex ---$(RESET)"
	@echo "  make lock                 PHASE='testing foo'   # acquire lock"
	@echo "  make unlock                                      # release lock"
	@echo "  make lock-status                                 # check lock"
	@echo ""
	@echo "$(CYAN)--- Variables ---$(RESET)"
	@echo "  ROUTER  - router label from routers.env (default: alpha)"
	@echo "  SSID    - upstream WiFi SSID"
	@echo "  PASS    - upstream WiFi passphrase"
	@echo "  MINT    - mint URL for block/unblock (default: https://nofee.testnut.cashu.space)"
	@echo "  VIA     - intermediate router for rescue"
	@echo "  PHASE   - description for router lock"

# ===========================================================================
#  SMOKE TESTS
# ===========================================================================

.PHONY: smoke-degraded smoke-upstream smoke-upstream-full smoke-pin-upstream \
        smoke-dynamic-rebuild smoke-offline smoke-recovery \
        smoke-degraded-recovery smoke-degraded-connect

smoke-degraded: ## Single-router degraded mode lifecycle (~3 min)
	@$(MAKE) -C mint-health r-smoke-degraded ROUTER=$(ROUTER) MINT="$(MINT)"

smoke-upstream: ## Two-router degraded upstream payment (~5 min)
	@$(MAKE) -C mint-health r-smoke-degraded-upstream ROUTER=$(ROUTER) MINT="$(MINT)"

smoke-upstream-full: ## Full upstream WiFi smoke test (requires SSID+PASS)
	@if [ -z "$(SSID)" ]; then echo "$(RED)Error: SSID required. make smoke-upstream-full SSID=MyNet PASS=secret$(RESET)"; exit 1; fi
	@$(MAKE) -C upstream-wifi r-smoke SSID="$(SSID)" PASS="$(PASS)" ROUTER=$(ROUTER)

smoke-pin-upstream: ## Two-router: verify upstream pin prevents scan-away
	@$(MAKE) -C mint-health r-smoke-pin-upstream ROUTER=$(ROUTER) MINT="$(MINT)"

smoke-dynamic-rebuild: ## Full→degraded→full lifecycle (tests onReachableSetChanged)
	@$(MAKE) -C mint-health r-smoke-dynamic-rebuild ROUTER=$(ROUTER) MINT="$(MINT)"

smoke-offline: ## Block mint + restart + verify degraded (~2 min)
	@$(MAKE) -C mint-health r-smoke-offline ROUTER=$(ROUTER) MINT="$(MINT)"

smoke-recovery: ## Unblock mint + wait for recovery (~15 min)
	@$(MAKE) -C mint-health r-smoke-recovery ROUTER=$(ROUTER) MINT="$(MINT)"

smoke-degraded-recovery: ## Degraded→recovery without restart (tests in-process recovery)
	@$(MAKE) -C mint-health r-smoke-degraded-recovery ROUTER=$(ROUTER) MINT="$(MINT)"

smoke-degraded-connect: ## WARNING: connect to upstream while degraded (may strand router)
	@$(MAKE) -C mint-health r-smoke-degraded-connect ROUTER=$(ROUTER) MINT="$(MINT)"

# ===========================================================================
#  STARTUP HYGIENE TESTS
# ===========================================================================

.PHONY: test-startup-hygiene test-startup-hygiene-dead-only

test-startup-hygiene: ## Boot with dead STA, verify auto-switch (~2 min)
	@$(MAKE) -C mint-health r-test-startup-hygiene ROUTER=$(ROUTER)

test-startup-hygiene-dead-only: ## Boot with ONLY dead STA, emergency scan recovery (~3 min)
	@$(MAKE) -C mint-health r-test-startup-hygiene-dead-only ROUTER=$(ROUTER)

# ===========================================================================
#  PLAYWRIGHT TESTS
# ===========================================================================

.PHONY: test-playwright test-captive-portal test-captive-portal-happy test-cashu-payment

test-playwright: ## Run Playwright LuCI admin UI tests (requires TOLLGATE_LUCI_PASSWORD)
	@if [ ! -d node_modules ]; then echo "$(YELLOW)Run npm install first$(RESET)"; exit 1; fi
	@cd tests && npx playwright test --config=playwright.config.mjs

test-captive-portal: ## Run Playwright captive portal tests against router
	@$(MAKE) -C mint-health r-test-captive-portal ROUTER=$(ROUTER)

test-captive-portal-happy: ## Run only happy-path captive portal tests
	@$(MAKE) -C mint-health r-test-captive-portal-happy ROUTER=$(ROUTER)

test-cashu-payment: ## Run cashu e2e payment Playwright test
	@$(MAKE) -C mint-health r-test-cashu-payment ROUTER=$(ROUTER)

# ===========================================================================
#  FULL TEST SUITES
# ===========================================================================

.PHONY: full-degraded full-upstream full-all

full-degraded: ## Full mint health test suite (~20 min)
	@$(MAKE) -C mint-health r-full ROUTER=$(ROUTER) MINT="$(MINT)"

full-upstream: ## Full upstream WiFi test suite (requires SSID+PASS, ~30 min)
	@if [ -z "$(SSID)" ]; then echo "$(RED)Error: SSID required. make full-upstream SSID=MyNet PASS=secret$(RESET)"; exit 1; fi
	@$(MAKE) -C mint-health r-full-upstream SSID="$(SSID)" PASS="$(PASS)" ROUTER=$(ROUTER)

full-all: ## Run all test suites (lint + playwright + degraded + upstream)
	@echo "$(BOLD)=======================================$(RESET)"
	@echo "$(BOLD)  Full Test Suite [$(ROUTER)]$(RESET)"
	@echo "$(BOLD)=======================================$(RESET)"
	@echo ""
	@echo "$(CYAN)1/3 — Playwright LuCI tests...$(RESET)"
	@-$(MAKE) test-playwright
	@echo ""
	@echo "$(CYAN)2/3 — Mint health degraded lifecycle...$(RESET)"
	@$(MAKE) full-degraded ROUTER=$(ROUTER) MINT="$(MINT)"
	@echo ""
	@echo "$(CYAN)3/3 — Captive portal + cashu payment...$(RESET)"
	@-$(MAKE) test-captive-portal ROUTER=$(ROUTER)
	@-$(MAKE) test-cashu-payment ROUTER=$(ROUTER)
	@echo ""
	@echo "$(BOLD)=======================================$(RESET)"
	@echo "$(GREEN)$(BOLD)  Full test suite complete [$(ROUTER)]$(RESET)"
	@echo "$(BOLD)=======================================$(RESET)"

# ===========================================================================
#  ROUTER MANAGEMENT
# ===========================================================================

.PHONY: deploy deploy-cli status shell logs check-sta-health fix-dns cleanup \
        setup-fresh fund-wallet restore-prod diagnose-config test-default-mints \
        rescue-router save-upstream restore-upstream

deploy: ## Cross-compile and deploy daemon + CLI to router
	@$(MAKE) -C mint-health r-deploy ROUTER=$(ROUTER)

deploy-cli: ## Cross-compile and deploy CLI only (no service restart)
	@$(MAKE) -C mint-health r-deploy-cli ROUTER=$(ROUTER)

status: ## Check tollgate service status
	@$(MAKE) -C mint-health r-status ROUTER=$(ROUTER)

shell: ## Interactive SSH session on router
	@$(MAKE) -C mint-health r-shell ROUTER=$(ROUTER)

logs: ## Tail tollgate logs (Ctrl+C to stop)
	@$(MAKE) -C mint-health r-logs ROUTER=$(ROUTER)

check-sta-health: ## Verify no stale/duplicate STA sections
	@$(MAKE) -C mint-health r-check-sta-health ROUTER=$(ROUTER)

fix-dns: ## Fix NetBird DNS hijack on router
	@$(MAKE) -C mint-health r-fix-dns ROUTER=$(ROUTER)

cleanup: ## Remove mint blocks and restore config
	@$(MAKE) -C mint-health r-cleanup ROUTER=$(ROUTER)

setup-fresh: ## Setup freshly flashed router (deploy + config + restart)
	@$(MAKE) -C mint-health r-setup-fresh ROUTER=$(ROUTER) MINT="$(MINT)"

fund-wallet: ## Fund wallet with 1013 sats from test mint
	@$(MAKE) -C mint-health r-fund-wallet ROUTER=$(ROUTER)

restore-prod: ## Restore production config and restart
	@$(MAKE) -C mint-health r-restore-prod-config ROUTER=$(ROUTER) MINT="$(MINT)"

diagnose-config: ## Verify service reads config from /etc/tollgate
	@$(MAKE) -C mint-health r-diagnose-config-path ROUTER=$(ROUTER)

test-default-mints: ## Verify default mint config
	@$(MAKE) -C mint-health r-test-default-mints ROUTER=$(ROUTER)

rescue-router: ## Rescue offline router via another router (ROUTER=beta VIA=alpha)
	@if [ -z "$(VIA)" ]; then echo "$(RED)Error: VIA required. make rescue-router ROUTER=beta VIA=alpha$(RESET)"; exit 1; fi
	@$(MAKE) -C mint-health r-rescue-router ROUTER=$(ROUTER) VIA=$(VIA)

save-upstream: ## Save current upstream SSID for later restore
	@$(MAKE) -C mint-health r-save-upstream ROUTER=$(ROUTER)

restore-upstream: ## Restore previously saved upstream SSID
	@$(MAKE) -C mint-health r-restore-upstream ROUTER=$(ROUTER)

# ===========================================================================
#  MINT BLOCKING (for manual degraded mode testing)
# ===========================================================================

.PHONY: block-mint unblock-mint restart-service check-merchant check-degraded

block-mint: ## Block mint via /etc/hosts override
	@$(MAKE) -C mint-health r-block-mint ROUTER=$(ROUTER) MINT="$(MINT)"

unblock-mint: ## Remove mint /etc/hosts override
	@$(MAKE) -C mint-health r-unblock-mint ROUTER=$(ROUTER) MINT="$(MINT)"

restart-service: ## Restart tollgate service
	@$(MAKE) -C mint-health r-restart-service ROUTER=$(ROUTER)

check-merchant: ## Verify full merchant mode (online)
	@$(MAKE) -C mint-health r-check-merchant ROUTER=$(ROUTER)

check-degraded: ## Verify degraded merchant mode (offline)
	@$(MAKE) -C mint-health r-check-degraded ROUTER=$(ROUTER)

# ===========================================================================
#  SERIAL CONSOLE
# ===========================================================================

.PHONY: serial-shell serial-status serial-logs serial-cold-boot serial-recovery \
        serial-cleanup serial-watch serial-boot-log

serial-shell: ## Interactive serial console (picocom)
	@$(MAKE) -C mint-health s-shell ROUTER=$(ROUTER)

serial-status: ## Check tollgate status via serial
	@$(MAKE) -C mint-health s-status ROUTER=$(ROUTER)

serial-watch: ## Watch serial output (Ctrl+C to stop)
	@$(MAKE) -C mint-health s-watch ROUTER=$(ROUTER)

serial-cold-boot: ## Full cold boot test with serial monitoring
	@$(MAKE) -C mint-health s-cold-boot-test ROUTER=$(ROUTER)

serial-boot-log: ## Capture full boot output
	@$(MAKE) -C mint-health s-boot-log ROUTER=$(ROUTER)

serial-recovery: ## Emergency recovery via serial (CMD='...')
	@if [ -z "$(CMD)" ]; then echo "$(RED)Error: CMD required. make serial-recovery ROUTER=alpha CMD='wifi reload'$(RESET)"; exit 1; fi
	@$(MAKE) -C mint-health s-recovery ROUTER=$(ROUTER) CMD="$(CMD)"

serial-cleanup: ## Cleanup mint blocks via serial
	@$(MAKE) -C mint-health s-cleanup ROUTER=$(ROUTER)

# ===========================================================================
#  HYBRID (SSH first, serial fallback)
# ===========================================================================

.PHONY: hybrid-status hybrid-cleanup hybrid-restart-and-watch

hybrid-status: ## Status check — SSH first, serial fallback
	@$(MAKE) -C mint-health h-status ROUTER=$(ROUTER)

hybrid-cleanup: ## Cleanup — SSH first, serial fallback
	@$(MAKE) -C mint-health h-cleanup ROUTER=$(ROUTER)

hybrid-restart-and-watch: ## Restart service via SSH, verify via serial if SSH drops
	@$(MAKE) -C mint-health h-restart-and-watch ROUTER=$(ROUTER)

# ===========================================================================
#  ROUTER MUTEX
# ===========================================================================

.PHONY: lock unlock lock-status force-unlock

lock: ## Acquire router lock (set PHASE='description')
	@$(MAKE) -C mint-health r-lock PHASE="$(PHASE)"

unlock: ## Release router lock
	@$(MAKE) -C mint-health r-unlock

lock-status: ## Show current router lock status
	@$(MAKE) -C mint-health r-status-lock

force-unlock: ## Force-release router lock (use with caution)
	@$(MAKE) -C mint-health force-unlock

# ===========================================================================
#  SETUP
# ===========================================================================

.PHONY: setup

setup: ## Install dependencies (npm + playwright + serial)
	@echo "$(BOLD)=== Installing dependencies ===$(RESET)"
	@echo "$(CYAN)1/3 — npm install...$(RESET)"
	@npm install
	@echo "$(CYAN)2/3 — Playwright browsers...$(RESET)"
	@npx playwright install
	@echo "$(CYAN)3/3 — Serial dependencies...$(RESET)"
	@pip3 install -q -r scripts/requirements-serial.txt 2>/dev/null || echo "$(YELLOW)pip install skipped (optional for serial tests)$(RESET)"
	@echo ""
	@echo "$(GREEN)Dependencies installed.$(RESET)"
	@echo ""
	@echo "$(YELLOW)Next steps:$(RESET)"
	@echo "  cp mint-health/routers.env.example mint-health/routers.env   # fill in real values"
	@echo "  cp upstream-wifi/routers.env.example upstream-wifi/routers.env"
	@echo "  cp .env.example .env                                         # fill in LuCI credentials"
