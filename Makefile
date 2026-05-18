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
#   make esp32-flash-a                # flash multi-mint firmware to Board A
#   make esp32-flash-b                # flash multi-mint firmware to Board B
#   make esp32-test-multi-mint-a      # full multi-mint test on Board A
#   make esp32-test-multi-mint-b      # full multi-mint test on Board B
#   make esp32-test-all-boards        # test both ESP32 boards
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

HARDWARE_LOCK := hardware.lock

define require_hardware_lock
	@if [ ! -f "$(HARDWARE_LOCK)" ]; then \
		echo "$(RED)$(BOLD)Hardware not locked — run 'make lock PHASE=\"description\"' first$(RESET)"; \
		echo "$(YELLOW)Other LLM sessions may be using the hardware (ESP32 boards + routers).$(RESET)"; \
		exit 1; \
	fi
endef

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
	@echo "$(CYAN)--- Hardware mutex (ESP32 + routers) ---$(RESET)"
	@echo "  make lock                 PHASE='testing foo'   # acquire lock"
	@echo "  make unlock                                      # release lock"
	@echo "  make lock-status                                 # check lock"
	@echo "  make force-unlock                                # force-release"
	@echo ""
	@echo "$(CYAN)--- Variables ---$(RESET)"
	@echo "  ROUTER  - router label from routers.env (default: alpha)"
	@echo "  SSID    - upstream WiFi SSID"
	@echo "  PASS    - upstream WiFi passphrase"
	@echo "  MINT    - mint URL for block/unblock (default: https://nofee.testnut.cashu.space)"
	@echo "  VIA     - intermediate router for rescue"
	@echo "  PHASE   - description for router lock"
	@echo ""
	@echo "$(CYAN)--- Arch component extraction tests (Board A, tollgate_core) ---$(RESET)"
	@echo "  make arch-build                   # build tollgate_core firmware"
	@echo "  make arch-flash-a                 # flash to Board A (requires lock)"
	@echo "  make arch-bootlog-a               # capture boot log (requires lock)"
	@echo "  make arch-connect-a               # WiFi connect to Board A"
	@echo "  make arch-test-smoke              # smoke test (~30s)"
	@echo "  make arch-test-network            # network test (~15s)"
	@echo "  make arch-test-api                # API endpoint test (~20s)"
	@echo "  make arch-test-dns-fw             # DNS + firewall test (~30s)"
	@echo "  make arch-test-reset              # reset auth cycle (~30s)"
	@echo "  make arch-test-session            # session expiry (~80s)"
	@echo "  make arch-test-phase2             # full API test (~90s)"
	@echo "  make arch-test-full               # all tests (~4min)"
	@echo "  make arch-test-cleanup            # disconnect + reset auth"

# ===========================================================================
#  SMOKE TESTS
# ===========================================================================

.PHONY: smoke-degraded smoke-upstream smoke-upstream-full smoke-pin-upstream \
        smoke-dynamic-rebuild smoke-offline smoke-recovery \
        smoke-degraded-recovery smoke-degraded-connect

smoke-degraded: ## Single-router degraded mode lifecycle (~3 min)
	$(call require_hardware_lock)
	@$(MAKE) -C mint-health r-smoke-degraded ROUTER=$(ROUTER) MINT="$(MINT)"

smoke-upstream: ## Two-router degraded upstream payment (~5 min)
	$(call require_hardware_lock)
	@$(MAKE) -C mint-health r-smoke-degraded-upstream ROUTER=$(ROUTER) MINT="$(MINT)"

smoke-upstream-full: ## Full upstream WiFi smoke test (requires SSID+PASS)
	$(call require_hardware_lock)
	@if [ -z "$(SSID)" ]; then echo "$(RED)Error: SSID required. make smoke-upstream-full SSID=MyNet PASS=secret$(RESET)"; exit 1; fi
	@$(MAKE) -C upstream-wifi r-smoke SSID="$(SSID)" PASS="$(PASS)" ROUTER=$(ROUTER)

smoke-pin-upstream: ## Two-router: verify upstream pin prevents scan-away
	$(call require_hardware_lock)
	@$(MAKE) -C mint-health r-smoke-pin-upstream ROUTER=$(ROUTER) MINT="$(MINT)"

smoke-dynamic-rebuild: ## Full→degraded→full lifecycle (tests onReachableSetChanged)
	$(call require_hardware_lock)
	@$(MAKE) -C mint-health r-smoke-dynamic-rebuild ROUTER=$(ROUTER) MINT="$(MINT)"

smoke-offline: ## Block mint + restart + verify degraded (~2 min)
	$(call require_hardware_lock)
	@$(MAKE) -C mint-health r-smoke-offline ROUTER=$(ROUTER) MINT="$(MINT)"

smoke-recovery: ## Unblock mint + wait for recovery (~15 min)
	$(call require_hardware_lock)
	@$(MAKE) -C mint-health r-smoke-recovery ROUTER=$(ROUTER) MINT="$(MINT)"

smoke-degraded-recovery: ## Degraded→recovery without restart (tests in-process recovery)
	$(call require_hardware_lock)
	@$(MAKE) -C mint-health r-smoke-degraded-recovery ROUTER=$(ROUTER) MINT="$(MINT)"

smoke-degraded-connect: ## WARNING: connect to upstream while degraded (may strand router)
	$(call require_hardware_lock)
	@$(MAKE) -C mint-health r-smoke-degraded-connect ROUTER=$(ROUTER) MINT="$(MINT)"

# ===========================================================================
#  STARTUP HYGIENE TESTS
# ===========================================================================

.PHONY: test-startup-hygiene test-startup-hygiene-dead-only

test-startup-hygiene: ## Boot with dead STA, verify auto-switch (~2 min)
	$(call require_hardware_lock)
	@$(MAKE) -C mint-health r-test-startup-hygiene ROUTER=$(ROUTER)

test-startup-hygiene-dead-only: ## Boot with ONLY dead STA, emergency scan recovery (~3 min)
	$(call require_hardware_lock)
	@$(MAKE) -C mint-health r-test-startup-hygiene-dead-only ROUTER=$(ROUTER)

# ===========================================================================
#  PLAYWRIGHT TESTS
# ===========================================================================

.PHONY: test-playwright test-captive-portal test-captive-portal-happy test-cashu-payment

test-playwright: ## Run Playwright LuCI admin UI tests (requires TOLLGATE_LUCI_PASSWORD)
	@if [ ! -d node_modules ]; then echo "$(YELLOW)Run npm install first$(RESET)"; exit 1; fi
	@cd tests && npx playwright test --config=playwright.config.mjs

test-captive-portal: ## Run Playwright captive portal tests against router
	$(call require_hardware_lock)
	@$(MAKE) -C mint-health r-test-captive-portal ROUTER=$(ROUTER)

test-captive-portal-happy: ## Run only happy-path captive portal tests
	$(call require_hardware_lock)
	@$(MAKE) -C mint-health r-test-captive-portal-happy ROUTER=$(ROUTER)

test-cashu-payment: ## Run cashu e2e payment Playwright test
	$(call require_hardware_lock)
	@$(MAKE) -C mint-health r-test-cashu-payment ROUTER=$(ROUTER)

# ===========================================================================
#  FULL TEST SUITES
# ===========================================================================

.PHONY: full-degraded full-upstream full-all

full-degraded: ## Full mint health test suite (~20 min)
	$(call require_hardware_lock)
	@$(MAKE) -C mint-health r-full ROUTER=$(ROUTER) MINT="$(MINT)"

full-upstream: ## Full upstream WiFi test suite (requires SSID+PASS, ~30 min)
	$(call require_hardware_lock)
	@if [ -z "$(SSID)" ]; then echo "$(RED)Error: SSID required. make full-upstream SSID=MyNet PASS=secret$(RESET)"; exit 1; fi
	@$(MAKE) -C mint-health r-full-upstream SSID="$(SSID)" PASS="$(PASS)" ROUTER=$(ROUTER)

full-all: ## Run all test suites (lint + playwright + degraded + upstream)
	$(call require_hardware_lock)
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
	$(call require_hardware_lock)
	@$(MAKE) -C mint-health r-deploy ROUTER=$(ROUTER)

deploy-cli: ## Cross-compile and deploy CLI only (no service restart)
	$(call require_hardware_lock)
	@$(MAKE) -C mint-health r-deploy-cli ROUTER=$(ROUTER)

status: ## Check tollgate service status
	@$(MAKE) -C mint-health r-status ROUTER=$(ROUTER)

shell: ## Interactive SSH session on router
	$(call require_hardware_lock)
	@$(MAKE) -C mint-health r-shell ROUTER=$(ROUTER)

logs: ## Tail tollgate logs (Ctrl+C to stop)
	$(call require_hardware_lock)
	@$(MAKE) -C mint-health r-logs ROUTER=$(ROUTER)

check-sta-health: ## Verify no stale/duplicate STA sections
	@$(MAKE) -C mint-health r-check-sta-health ROUTER=$(ROUTER)

fix-dns: ## Fix NetBird DNS hijack on router
	$(call require_hardware_lock)
	@$(MAKE) -C mint-health r-fix-dns ROUTER=$(ROUTER)

cleanup: ## Remove mint blocks and restore config
	$(call require_hardware_lock)
	@$(MAKE) -C mint-health r-cleanup ROUTER=$(ROUTER)

setup-fresh: ## Setup freshly flashed router (deploy + config + restart)
	$(call require_hardware_lock)
	@$(MAKE) -C mint-health r-setup-fresh ROUTER=$(ROUTER) MINT="$(MINT)"

fund-wallet: ## Fund wallet with 1013 sats from test mint
	$(call require_hardware_lock)
	@$(MAKE) -C mint-health r-fund-wallet ROUTER=$(ROUTER)

restore-prod: ## Restore production config and restart
	$(call require_hardware_lock)
	@$(MAKE) -C mint-health r-restore-prod-config ROUTER=$(ROUTER) MINT="$(MINT)"

diagnose-config: ## Verify service reads config from /etc/tollgate
	@$(MAKE) -C mint-health r-diagnose-config-path ROUTER=$(ROUTER)

test-default-mints: ## Verify default mint config
	@$(MAKE) -C mint-health r-test-default-mints ROUTER=$(ROUTER)

rescue-router: ## Rescue offline router via another router (ROUTER=beta VIA=alpha)
	$(call require_hardware_lock)
	@if [ -z "$(VIA)" ]; then echo "$(RED)Error: VIA required. make rescue-router ROUTER=beta VIA=alpha$(RESET)"; exit 1; fi
	@$(MAKE) -C mint-health r-rescue-router ROUTER=$(ROUTER) VIA=$(VIA)

save-upstream: ## Save current upstream SSID for later restore
	@$(MAKE) -C mint-health r-save-upstream ROUTER=$(ROUTER)

restore-upstream: ## Restore previously saved upstream SSID
	$(call require_hardware_lock)
	@$(MAKE) -C mint-health r-restore-upstream ROUTER=$(ROUTER)

# ===========================================================================
#  MINT BLOCKING (for manual degraded mode testing)
# ===========================================================================

.PHONY: block-mint unblock-mint restart-service check-merchant check-degraded

block-mint: ## Block mint via /etc/hosts override
	$(call require_hardware_lock)
	@$(MAKE) -C mint-health r-block-mint ROUTER=$(ROUTER) MINT="$(MINT)"

unblock-mint: ## Remove mint /etc/hosts override
	$(call require_hardware_lock)
	@$(MAKE) -C mint-health r-unblock-mint ROUTER=$(ROUTER) MINT="$(MINT)"

restart-service: ## Restart tollgate service
	$(call require_hardware_lock)
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
	$(call require_hardware_lock)
	@$(MAKE) -C mint-health s-shell ROUTER=$(ROUTER)

serial-status: ## Check tollgate status via serial
	$(call require_hardware_lock)
	@$(MAKE) -C mint-health s-status ROUTER=$(ROUTER)

serial-watch: ## Watch serial output (Ctrl+C to stop)
	$(call require_hardware_lock)
	@$(MAKE) -C mint-health s-watch ROUTER=$(ROUTER)

serial-cold-boot: ## Full cold boot test with serial monitoring
	$(call require_hardware_lock)
	@$(MAKE) -C mint-health s-cold-boot-test ROUTER=$(ROUTER)

serial-boot-log: ## Capture full boot output
	$(call require_hardware_lock)
	@$(MAKE) -C mint-health s-boot-log ROUTER=$(ROUTER)

serial-recovery: ## Emergency recovery via serial (CMD='...')
	$(call require_hardware_lock)
	@if [ -z "$(CMD)" ]; then echo "$(RED)Error: CMD required. make serial-recovery ROUTER=alpha CMD='wifi reload'$(RESET)"; exit 1; fi
	@$(MAKE) -C mint-health s-recovery ROUTER=$(ROUTER) CMD="$(CMD)"

serial-cleanup: ## Cleanup mint blocks via serial
	$(call require_hardware_lock)
	@$(MAKE) -C mint-health s-cleanup ROUTER=$(ROUTER)

# ===========================================================================
#  HYBRID (SSH first, serial fallback)
# ===========================================================================

.PHONY: hybrid-status hybrid-cleanup hybrid-restart-and-watch

hybrid-status: ## Status check — SSH first, serial fallback
	$(call require_hardware_lock)
	@$(MAKE) -C mint-health h-status ROUTER=$(ROUTER)

hybrid-cleanup: ## Cleanup — SSH first, serial fallback
	$(call require_hardware_lock)
	@$(MAKE) -C mint-health h-cleanup ROUTER=$(ROUTER)

hybrid-restart-and-watch: ## Restart service via SSH, verify via serial if SSH drops
	$(call require_hardware_lock)
	@$(MAKE) -C mint-health h-restart-and-watch ROUTER=$(ROUTER)

# ===========================================================================
#  HARDWARE MUTEX (unified — protects OpenWRT routers; ESP32 has per-board)
# ===========================================================================

.PHONY: lock unlock lock-status force-unlock

LOCK_DIR := locks

lock: ## Acquire router hardware lock — set PHASE='description'
	@if [ -f "$(HARDWARE_LOCK)" ]; then \
		echo "$(RED)$(BOLD)Cannot acquire lock — hardware already locked:$(RESET)"; \
		echo ""; \
		cat $(HARDWARE_LOCK); \
		echo ""; \
		echo "$(YELLOW)Use 'make force-unlock' to override (with caution).$(RESET)"; \
		exit 1; \
	fi; \
	branch=$$(git branch --show-current 2>/dev/null || echo "unknown"); \
	worktree=$$(pwd); \
	echo "locked: true" > $(HARDWARE_LOCK); \
	echo "branch: $$branch" >> $(HARDWARE_LOCK); \
	echo "worktree: $$worktree" >> $(HARDWARE_LOCK); \
	echo "session: $$USER@$$HOSTNAME" >> $(HARDWARE_LOCK); \
	echo "timestamp: $$(date -u '+%Y-%m-%dT%H:%M:%SZ')" >> $(HARDWARE_LOCK); \
	echo "phase: $(PHASE)" >> $(HARDWARE_LOCK); \
	echo "$(GREEN)$(BOLD)Router hardware lock acquired$(RESET)"; \
	cat $(HARDWARE_LOCK)

unlock: ## Release router hardware lock
	@if [ ! -f "$(HARDWARE_LOCK)" ]; then \
		echo "$(YELLOW)No lock file found — already unlocked.$(RESET)"; \
		exit 0; \
	fi; \
	echo "$(YELLOW)Releasing hardware lock...$(RESET)"; \
	rm -f $(HARDWARE_LOCK); \
	echo "$(GREEN)Hardware lock released.$(RESET)"

lock-status: ## Show all lock statuses (routers + ESP32 boards)
	@echo "$(BOLD)=== Router Lock ===$(RESET)"
	@if [ ! -f "$(HARDWARE_LOCK)" ]; then \
		echo "$(GREEN)Router hardware unlocked — available.$(RESET)"; \
	else \
		echo "$(YELLOW)$(BOLD)Router hardware locked:$(RESET)"; \
		echo ""; \
		cat $(HARDWARE_LOCK); \
	fi
	@echo ""
	@echo "$(BOLD)=== ESP32 Board Locks ===$(RESET)"
	@$(MAKE) -C esp32 lock-status

force-unlock: ## Force-release router hardware lock (use with caution)
	@if [ ! -f "$(HARDWARE_LOCK)" ]; then \
		echo "$(YELLOW)No lock file found — already unlocked.$(RESET)"; \
		exit 0; \
	fi; \
	echo "$(RED)$(BOLD)WARNING: Force-releasing router hardware lock!$(RESET)"; \
	echo "$(RED)Previous holder:$(RESET)"; \
	cat $(HARDWARE_LOCK); \
	rm -f $(HARDWARE_LOCK); \
	echo "$(GREEN)Hardware lock force-released.$(RESET)"

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
	@echo ""
	@echo "$(CYAN)--- ESP32 board tests ---$(RESET)"
	@echo "  make esp32-flash-a                       # flash multi-mint firmware to Board A"
	@echo "  make esp32-flash-b                       # flash multi-mint firmware to Board B"
	@echo "  make esp32-test-multi-mint-a             # full multi-mint test on Board A"
	@echo "  make esp32-test-multi-mint-b             # full multi-mint test on Board B"
	@echo "  make esp32-test-all-boards               # test both ESP32 boards"
	@echo ""
	@echo "$(CYAN)--- ESP32 ContextVM tests ---$(RESET)"
	@echo "  make esp32-test-cvm-a                    # CVM announcement test on Board A"
	@echo "  make esp32-test-cvm-b                    # CVM announcement test on Board B"
	@echo "  make esp32-test-cvm-mcp-a                # MCP tools/call end-to-end on Board A"
	@echo "  make esp32-cvm-pubkey-a                  # print Board A CVM npub"
	@echo "  make esp32-cvm-pubkey-b                  # print Board B CVM npub"

# ===========================================================================
#  ESP32 BOARD TESTS (per-board locks)
# ===========================================================================

 .PHONY: esp32-flash-a esp32-flash-b esp32-flash-c \
         esp32-monitor-a esp32-monitor-b esp32-monitor-c \
         esp32-connect-a esp32-connect-b esp32-disconnect \
         esp32-test-discovery-a esp32-test-discovery-b \
         esp32-test-mints-a esp32-test-mints-b \
         esp32-test-multi-mint-a esp32-test-multi-mint-b esp32-test-all-boards \
         esp32-test-cvm-a esp32-test-cvm-b esp32-test-cvm-mcp-a \
         esp32-cvm-pubkey-a esp32-cvm-pubkey-b \
         esp32-lock-a esp32-lock-b esp32-lock-c \
         esp32-unlock-a esp32-unlock-b esp32-unlock-c \
         esp32-lock-status \
         esp32-force-unlock-a esp32-force-unlock-b esp32-force-unlock-c \
         esp32-build

esp32-flash-a: ## Flash firmware to Board A (requires lock-a)
	@$(MAKE) -C esp32 flash-a

esp32-flash-b: ## Flash firmware to Board B (requires lock-b)
	@$(MAKE) -C esp32 flash-b

esp32-flash-c: ## Flash firmware to Board C (requires lock-c)
	@$(MAKE) -C esp32 flash-c

esp32-monitor-a: ## Serial monitor Board A (requires lock-a)
	@$(MAKE) -C esp32 monitor-a

esp32-monitor-b: ## Serial monitor Board B (requires lock-b)
	@$(MAKE) -C esp32 monitor-b

esp32-monitor-c: ## Serial monitor Board C (requires lock-c)
	@$(MAKE) -C esp32 monitor-c

esp32-connect-a: ## Connect laptop to Board A AP (requires lock-a)
	@$(MAKE) -C esp32 connect-a

esp32-connect-b: ## Connect laptop to Board B AP (requires lock-b)
	@$(MAKE) -C esp32 connect-b

esp32-disconnect: ## Disconnect from board AP
	@$(MAKE) -C esp32 disconnect

esp32-test-discovery-a: ## Test discovery on Board A (requires lock-a)
	@$(MAKE) -C esp32 test-discovery-a

esp32-test-discovery-b: ## Test discovery on Board B (requires lock-b)
	@$(MAKE) -C esp32 test-discovery-b

esp32-test-mints-a: ## Test /mints on Board A (requires lock-a)
	@$(MAKE) -C esp32 test-mints-a

esp32-test-mints-b: ## Test /mints on Board B (requires lock-b)
	@$(MAKE) -C esp32 test-mints-b

esp32-test-multi-mint-a: ## Full multi-mint test on Board A (requires lock-a)
	@$(MAKE) -C esp32 test-multi-mint-a

esp32-test-multi-mint-b: ## Full multi-mint test on Board B (requires lock-b)
	@$(MAKE) -C esp32 test-multi-mint-b

esp32-test-all-boards: ## Test both boards (requires lock-a + lock-b)
	@$(MAKE) -C esp32 test-all-boards

 esp32-build: ## Build ESP32 firmware (no lock)
	@$(MAKE) -C esp32 build

esp32-test-cvm-a: ## CVM announcement test on Board A (requires lock-a)
	@$(MAKE) -C esp32 test-cvm-a

esp32-test-cvm-b: ## CVM announcement test on Board B (requires lock-b)
	@$(MAKE) -C esp32 test-cvm-b

esp32-test-cvm-mcp-a: ## MCP tools/call test on Board A (requires lock-a)
	@$(MAKE) -C esp32 test-cvm-mcp-a

esp32-cvm-pubkey-a: ## Print Board A's CVM npub (no lock)
	@$(MAKE) -C esp32 cvm-pubkey-a

esp32-cvm-pubkey-b: ## Print Board B's CVM npub (no lock)
	@$(MAKE) -C esp32 cvm-pubkey-b

esp32-lock-a: ## Acquire Board A lock (PHASE='description')
	@$(MAKE) -C esp32 lock-a PHASE="$(PHASE)"

esp32-lock-b: ## Acquire Board B lock
	@$(MAKE) -C esp32 lock-b PHASE="$(PHASE)"

esp32-lock-c: ## Acquire Board C lock
	@$(MAKE) -C esp32 lock-c PHASE="$(PHASE)"

esp32-unlock-a: ## Release Board A lock
	@$(MAKE) -C esp32 unlock-a

esp32-unlock-b: ## Release Board B lock
	@$(MAKE) -C esp32 unlock-b

esp32-unlock-c: ## Release Board C lock
	@$(MAKE) -C esp32 unlock-c

esp32-lock-status: ## Show all ESP32 board lock statuses
	@$(MAKE) -C esp32 lock-status

esp32-force-unlock-a: ## Force-release Board A lock
	@$(MAKE) -C esp32 force-unlock-a

esp32-force-unlock-b: ## Force-release Board B lock
	@$(MAKE) -C esp32 force-unlock-b

esp32-force-unlock-c: ## Force-release Board C lock
	@$(MAKE) -C esp32 force-unlock-c

# ===========================================================================
#  ARCH COMPONENT EXTRACTION TESTS (tollgate_core on Board A)
# ===========================================================================

arch-build: ## Build arch (tollgate_core) firmware
	@$(MAKE) -C esp32 arch-build

arch-flash-a: ## Flash arch firmware to Board A (requires lock)
	@$(MAKE) -C esp32 arch-flash-a

arch-monitor-a: ## Serial monitor for arch Board A (requires lock)
	@$(MAKE) -C esp32 arch-monitor-a

arch-bootlog-a: ## Capture boot log from Board A (requires lock)
	@$(MAKE) -C esp32 arch-bootlog-a

arch-connect-a: ## WiFi connect to Board A AP
	@$(MAKE) -C esp32 arch-connect-a

arch-disconnect: ## Disconnect WiFi from Board A AP
	@$(MAKE) -C esp32 arch-disconnect

arch-test-smoke: ## Smoke test (~30s)
	@$(MAKE) -C esp32 arch-test-smoke

arch-test-network: ## Network test (~15s)
	@$(MAKE) -C esp32 arch-test-network

arch-test-api: ## API endpoint test (~20s)
	@$(MAKE) -C esp32 arch-test-api

arch-test-dns-fw: ## DNS + Firewall test (~30s)
	@$(MAKE) -C esp32 arch-test-dns-fw

arch-test-reset: ## Reset auth cycle test (~30s)
	@$(MAKE) -C esp32 arch-test-reset

arch-test-session: ## Session expiry test (~80s)
	@$(MAKE) -C esp32 arch-test-session

arch-test-phase2: ## Phase 2 API test (~90s)
	@$(MAKE) -C esp32 arch-test-phase2

arch-test-cleanup: ## Disconnect WiFi + reset auth
	@$(MAKE) -C esp32 arch-test-cleanup

arch-test-full: ## Run all arch E2E tests (~4min)
	@$(MAKE) -C esp32 arch-test-full
