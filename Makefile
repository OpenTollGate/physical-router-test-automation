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
	@echo "  make hw-deploy              ROUTER=alpha      # cross-compile + deploy binaries"
	@echo "  make deploy-develop       ROUTER=alpha      # deploy from develop worktree"
	@echo "  make test-develop-smoke   ROUTER=alpha      # CLI config smoke tests"
	@echo "  make test-develop-playwright ROUTER=alpha    # Playwright tests vs develop"
	@echo "  make status               ROUTER=alpha      # check service status"
	@echo "  make shell                ROUTER=alpha      # interactive SSH session"
	@echo "  make logs                 ROUTER=alpha      # tail tollgate logs"
	@echo "  make check-sta-health     ROUTER=alpha      # verify no stale/duplicate STAs"
	@echo "  make fix-dns              ROUTER=alpha      # fix NetBird DNS hijack"
	@echo "  make cleanup              ROUTER=alpha      # remove blocks and restore config"
	@echo "  make rescue-router        ROUTER=beta VIA=alpha  # rescue offline router"
	@echo ""
	@echo "$(CYAN)--- Hostname & SSL tests ---$(RESET)"
	@echo "  make test-hostname        ROUTER=alpha      # verify hostname is set"
	@echo "  make test-ssl-full        ROUTER=alpha      # full SSL lifecycle test"
	@echo "  make test-ssl-self-signed ROUTER=alpha      # test self-signed SSL apply"
	@echo "  make test-ssl-remove      ROUTER=alpha      # test SSL remove"
	@echo "  make ssl-status           ROUTER=alpha      # show SSL status (read-only)"
	@echo "  make ssl-remove-force     ROUTER=alpha      # force-remove SSL config"
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
	@echo "  make arch-generate-spiffs         # generate SPIFFS with auto-detected WPA mode"
	@echo "  make arch-flash-spiffs-a          # flash SPIFFS to Board A (requires lock)"
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
	@echo ""
	@echo "$(CYAN)--- Local Relay tests (Board B, feature/local-relay) ---$(RESET)"
	@echo "  make relay-build                  # build relay firmware (no lock)"
	@echo "  make relay-flash-b                # flash to Board B (requires lock-b)"
	@echo "  make relay-test-smoke             # verify port 4869 reachable"
	@echo "  make relay-test-nip11             # NIP-11 info document test"
	@echo "  make relay-test-pubsub            # WS publish + subscribe test"
	@echo "  make relay-test-sync              # verify sync to public relays"
	@echo "  make relay-test-full              # all relay tests (~1min)"

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

.PHONY: hw-deploy deploy-ci deploy-cli status shell logs check-sta-health fix-dns cleanup \
        setup-fresh fund-wallet restore-prod diagnose-config test-default-mints \
        rescue-router save-upstream restore-upstream \
        test-hostname test-ssl-self-signed test-ssl-remove test-ssl-status test-ssl-full \
        ssl-status ssl-remove-force \
        test-ssl-setup-verify test-ssl-self-signed-yes test-ssl-reapply \
        test-ssl-remove-no-backup test-ssl-verify-cert test-ssl-verify-nds \
         test-ssl-verify-no-dns test-ssl-idempotent \
        test-ssl-comprehensive \
        test-ssl-real-cert test-ssl-real-cert-remove test-ssl-real-cert-full \
        test-ssl-all \
        deploy-develop test-develop-smoke test-develop-smoke-persist test-develop-playwright

DEVELOP_SRC ?= $(HOME)/tollgate-worktrees/develop/src
DEVICE ?= gl-mt3000
RESTART_WAIT ?= 10

hw-deploy: ## Cross-compile and deploy daemon + CLI to router
	$(call require_hardware_lock)
	@$(MAKE) -C mint-health r-deploy ROUTER=$(ROUTER)

deploy-cli: ## Cross-compile and deploy CLI only (no service restart)
	$(call require_hardware_lock)
	@$(MAKE) -C mint-health r-deploy-cli ROUTER=$(ROUTER)

deploy-develop: ## Cross-compile from develop worktree and deploy daemon + CLI to router
	$(call require_hardware_lock)
	@router_host=$$(grep -E "^ROUTER_$$(echo $(ROUTER) | tr '[:lower:]' '[:upper:]')_HOST=" mint-health/routers.env | cut -d= -f2); \
	if [ -z "$$router_host" ]; then echo "$(RED)Unknown router '$(ROUTER)'$(RESET)"; exit 1; fi; \
	echo "$(BOLD)=== Deploying develop branch to $(ROUTER) ($$router_host) [$(DEVICE)] ===$(RESET)"; \
	if [ ! -d "$(DEVELOP_SRC)" ]; then echo "$(RED)Develop worktree not found at $(DEVELOP_SRC)$(RESET)"; exit 1; fi; \
	echo "$(CYAN)1/4 — Building tollgate-wrt...$(RESET)"; \
	if [ "$(DEVICE)" = "gl-ar300" ]; then \
		cd "$(DEVELOP_SRC)" && GOOS=linux GOARCH=mips GOMIPS=softfloat CGO_ENABLED=0 go build -o /tmp/tollgate-wrt-develop -trimpath -ldflags="-s -w"; \
	else \
		cd "$(DEVELOP_SRC)" && GOOS=linux GOARCH=arm64 CGO_ENABLED=0 go build -o /tmp/tollgate-wrt-develop -trimpath -ldflags="-s -w"; \
	fi; \
	echo "$(CYAN)2/4 — Building tollgate CLI...$(RESET)"; \
	if [ "$(DEVICE)" = "gl-ar300" ]; then \
		cd "$(DEVELOP_SRC)/cmd/tollgate-cli" && GOOS=linux GOARCH=mips GOMIPS=softfloat CGO_ENABLED=0 go build -o /tmp/tollgate-cli-develop -trimpath -ldflags="-s -w"; \
	else \
		cd "$(DEVELOP_SRC)/cmd/tollgate-cli" && GOOS=linux GOARCH=arm64 CGO_ENABLED=0 go build -o /tmp/tollgate-cli-develop -trimpath -ldflags="-s -w"; \
	fi; \
	echo "$(CYAN)3/4 — Deploying to router...$(RESET)"; \
	ssh -o ConnectTimeout=10 -o StrictHostKeyChecking=no root@$$router_host "/etc/init.d/tollgate-wrt stop" 2>/dev/null || true; \
	scp -O -o ConnectTimeout=10 -o StrictHostKeyChecking=no /tmp/tollgate-wrt-develop root@$$router_host:/usr/bin/tollgate-wrt; \
	scp -O -o ConnectTimeout=10 -o StrictHostKeyChecking=no /tmp/tollgate-cli-develop root@$$router_host:/usr/bin/tollgate; \
	ssh -o ConnectTimeout=10 -o StrictHostKeyChecking=no root@$$router_host "chmod +x /usr/bin/tollgate-wrt /usr/bin/tollgate && /etc/init.d/tollgate-wrt start"; \
	echo "$(CYAN)4/4 — Verifying...$(RESET)"; \
	sleep 3; \
	ssh -o ConnectTimeout=10 -o StrictHostKeyChecking=no root@$$router_host "tollgate --json config schema" > /dev/null 2>&1 && echo "$(GREEN)Deploy OK — config schema responds$(RESET)" || echo "$(RED)Deploy FAILED — config schema not responding$(RESET)"

test-develop-smoke: ## Run CLI config smoke tests on router with develop binaries
	$(call require_hardware_lock)
	@router_host=$$(grep -E "^ROUTER_$$(echo $(ROUTER) | tr '[:lower:]' '[:upper:]')_HOST=" mint-health/routers.env | cut -d= -f2); \
	if [ -z "$$router_host" ]; then echo "$(RED)Unknown router '$(ROUTER)'$(RESET)"; exit 1; fi; \
	SH="ssh -o ConnectTimeout=10 -o StrictHostKeyChecking=no root@$$router_host"; \
	PASS=0; FAIL=0; \
	echo "$(BOLD)=== Develop Smoke Tests [$(ROUTER)] ===$(RESET)"; \
	echo ""; \
	echo "$(CYAN)--- config schema ---$(RESET)"; \
	$$SH "tollgate --json config schema" > /tmp/smoke-schema.json 2>&1 && grep -q '"json_key"' /tmp/smoke-schema.json && { echo "  $(GREEN)PASS$(RESET): schema returns FieldSchema array"; PASS=$$((PASS+1)); } || { echo "  $(RED)FAIL$(RESET): schema not responding"; FAIL=$$((FAIL+1)); }; \
	echo "$(CYAN)--- config get ---$(RESET)"; \
	$$SH "tollgate --json config get" > /tmp/smoke-config.json 2>&1 && grep -q '"metric"' /tmp/smoke-config.json && { echo "  $(GREEN)PASS$(RESET): config get returns full config"; PASS=$$((PASS+1)); } || { echo "  $(RED)FAIL$(RESET): config get failed"; FAIL=$$((FAIL+1)); }; \
	echo "$(CYAN)--- config set + disk persistence ---$(RESET)"; \
	ORIG_LOGLEVEL=$$($$SH "tollgate --json config get" 2>&1 | grep -oP '"log_level"\s*:\s*"\K[^"]*' | head -1); \
	SET_OUT=$$($$SH "tollgate --json config set log_level warn" 2>&1); \
	echo "$$SET_OUT" | grep -qP '"value"\s*:\s*"warn"' && { echo "  $(GREEN)PASS$(RESET): config set returns new value"; PASS=$$((PASS+1)); } || { echo "  $(RED)FAIL$(RESET): config set failed"; FAIL=$$((FAIL+1)); }; \
	DISK_LEVEL=$$($$SH "grep log_level /etc/tollgate/config.json" 2>&1 | grep -oP '"log_level"\s*:\s*"\K[^"]*'); \
	if [ "$$DISK_LEVEL" = "warn" ]; then echo "  $(GREEN)PASS$(RESET): value persisted to /etc/tollgate/config.json"; PASS=$$((PASS+1)); else echo "  $(RED)FAIL$(RESET): disk has $$DISK_LEVEL (expected warn)"; FAIL=$$((FAIL+1)); fi; \
	$$SH "tollgate --json config set log_level $$ORIG_LOGLEVEL" > /dev/null 2>&1; \
	echo "$(CYAN)--- enum validation ---$(RESET)"; \
	ERR=$$($$SH "tollgate --json config set log_level INVALID" 2>&1); \
	echo "$$ERR" | grep -qi "not in allowed" && { echo "  $(GREEN)PASS$(RESET): rejects invalid log_level enum"; PASS=$$((PASS+1)); } || { echo "  $(RED)FAIL$(RESET): accepted invalid enum (got: $$ERR)"; FAIL=$$((FAIL+1)); }; \
	echo "$(CYAN)--- min/max validation (upper bound) ---$(RESET)"; \
	ERR=$$($$SH "tollgate --json config set margin 5.0" 2>&1); \
	echo "$$ERR" | grep -qi "exceeds maximum" && { echo "  $(GREEN)PASS$(RESET): rejects margin > 1.0"; PASS=$$((PASS+1)); } || { echo "  $(RED)FAIL$(RESET): accepted margin 5.0 (got: $$ERR)"; FAIL=$$((FAIL+1)); }; \
	echo "$(CYAN)--- min/max validation (lower bound) ---$(RESET)"; \
	ERR=$$($$SH "tollgate --json config set margin -- -0.5" 2>&1); \
	echo "$$ERR" | grep -qi "below minimum" && { echo "  $(GREEN)PASS$(RESET): rejects negative margin"; PASS=$$((PASS+1)); } || { echo "  $(RED)FAIL$(RESET): accepted margin -0.5 (got: $$ERR)"; FAIL=$$((FAIL+1)); }; \
	echo "$(CYAN)--- wallet balance ---$(RESET)"; \
	$$SH "tollgate --json wallet balance" > /tmp/smoke-wallet.json 2>&1 && grep -q '"balance_sats"' /tmp/smoke-wallet.json && { echo "  $(GREEN)PASS$(RESET): wallet balance responds"; PASS=$$((PASS+1)); } || { echo "  $(RED)FAIL$(RESET): wallet balance failed"; FAIL=$$((FAIL+1)); }; \
	echo "$(CYAN)--- health ---$(RESET)"; \
	$$SH "tollgate --json health" > /dev/null 2>&1 && { echo "  $(GREEN)PASS$(RESET): health responds"; PASS=$$((PASS+1)); } || { echo "  $(RED)FAIL$(RESET): health failed"; FAIL=$$((FAIL+1)); }; \
	echo "$(CYAN)--- status ---$(RESET)"; \
	$$SH "tollgate --json status" > /dev/null 2>&1 && { echo "  $(GREEN)PASS$(RESET): status responds"; PASS=$$((PASS+1)); } || { echo "  $(RED)FAIL$(RESET): status failed"; FAIL=$$((FAIL+1)); }; \
	echo ""; \
	echo "$(BOLD)=== Results: $(GREEN)$$PASS passed$(RESET), $(RED)$$FAIL failed$(RESET) ==="; \
	if [ "$$FAIL" -gt 0 ]; then exit 1; fi

test-develop-smoke-persist: ## Run set+restart persistence test
	$(call require_hardware_lock)
	@router_host=$$(grep -E "^ROUTER_$$(echo $(ROUTER) | tr '[:lower:]' '[:upper:]')_HOST=" mint-health/routers.env | cut -d= -f2); \
	if [ -z "$$router_host" ]; then echo "$(RED)Unknown router '$(ROUTER)'$(RESET)"; exit 1; fi; \
	echo "$(BOLD)=== Persistence Test [$(ROUTER)] ===$(RESET)"; \
	ORIG=$$(ssh -o ConnectTimeout=10 -o StrictHostKeyChecking=no root@$$router_host "tollgate --json config get" 2>&1 | grep -oP '"log_level"\s*:\s*"\K[^"]*' | head -1); \
	echo "  Original log_level: $$ORIG"; \
	ssh -o ConnectTimeout=10 -o StrictHostKeyChecking=no root@$$router_host "tollgate --json config set log_level error" > /dev/null 2>&1; \
	echo "  Restarting service..."; \
	ssh -o ConnectTimeout=10 -o StrictHostKeyChecking=no root@$$router_host "/etc/init.d/tollgate-wrt restart" 2>&1 || true; \
	echo "  Waiting $(RESTART_WAIT)s for service startup..."; \
	sleep $(RESTART_WAIT); \
	NEW=$$(ssh -o ConnectTimeout=10 -o StrictHostKeyChecking=no root@$$router_host "tollgate --json config get" 2>&1 | grep -oP '"log_level"\s*:\s*"\K[^"]*' | head -1); \
	echo "  After restart log_level: $$NEW"; \
	if [ "$$NEW" = "error" ]; then echo "  $(GREEN)PASS$(RESET): log_level persisted after restart"; \
	else echo "  $(RED)FAIL$(RESET): log_level not persisted (got $$NEW)"; fi; \
	ssh -o ConnectTimeout=10 -o StrictHostKeyChecking=no root@$$router_host "tollgate --json config set log_level $$ORIG" > /dev/null 2>&1

test-develop-playwright: ## Run Playwright tests against router with develop binaries
	$(call require_hardware_lock)
	@if [ ! -d node_modules ]; then echo "$(YELLOW)Run npm install first$(RESET)"; exit 1; fi
	@router_host=$$(grep -E "^ROUTER_$$(echo $(ROUTER) | tr '[:lower:]' '[:upper:]')_HOST=" mint-health/routers.env | cut -d= -f2); \
	if [ -z "$$router_host" ]; then echo "$(RED)Unknown router '$(ROUTER)'$(RESET)"; exit 1; fi; \
	echo "$(BOLD)=== Playwright Tests [$(ROUTER)] ($$router_host) ===$(RESET)"; \
	TOLLGATE_SSH_HOST=$$router_host \
	TOLLGATE_CAPTIVE_PORTAL_HOST=$$router_host \
	npx playwright test tests/tollgate.spec.mjs tests/captive-portal.spec.mjs --project=desktop

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
		owner=$$(grep '^session:' $(HARDWARE_LOCK) | head -1 | sed 's/session: *//' | cut -d@ -f1); \
		if [ "$$owner" = "$$USER" ]; then \
			echo "$(YELLOW)Hardware already locked by this session — refreshing lock$(RESET)"; \
		else \
			echo "$(RED)$(BOLD)Cannot acquire lock — hardware locked by another session:$(RESET)"; \
			echo ""; \
			cat $(HARDWARE_LOCK); \
			echo ""; \
			echo "$(YELLOW)Use 'make force-unlock' to override (with caution).$(RESET)"; \
			exit 1; \
		fi; \
	fi; \
	branch=$$(git branch --show-current 2>/dev/null || echo "unknown"); \
	worktree=$$(pwd); \
	echo "locked: true" > $(HARDWARE_LOCK); \
	echo "branch: $$branch" >> $(HARDWARE_LOCK); \
	echo "worktree: $$worktree" >> $(HARDWARE_LOCK); \
	echo "session: $$USER@$$(hostname)" >> $(HARDWARE_LOCK); \
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
#  HOSTNAME & SSL TESTS (legacy Makefile reference)
#
#  Routine PR validation for the Go SSL CLI has moved to pytest:
#    tests/api/test_ssl_go_cli.py  (PR #123-gated)
#  These targets remain available as manual/reference hardware commands.
# ===========================================================================

test-hostname: ## Verify hostname setup on router (not OpenWrt default)
	$(call require_hardware_lock)
	@$(MAKE) -C mint-health r-test-hostname ROUTER=$(ROUTER)

test-ssl-self-signed: ## Test self-signed SSL apply on router
	$(call require_hardware_lock)
	@$(MAKE) -C mint-health r-test-ssl-self-signed ROUTER=$(ROUTER)

test-ssl-remove: ## Test SSL remove on router
	$(call require_hardware_lock)
	@$(MAKE) -C mint-health r-test-ssl-remove ROUTER=$(ROUTER)

test-ssl-status: ## Test tollgate ssl status command on router
	$(call require_hardware_lock)
	@$(MAKE) -C mint-health r-test-ssl-status ROUTER=$(ROUTER)

test-ssl-full: ## Full SSL lifecycle: apply → verify → remove → verify cleanup
	$(call require_hardware_lock)
	@$(MAKE) -C mint-health r-test-ssl-full ROUTER=$(ROUTER)

ssl-status: ## Show current SSL status on router (read-only, no lock)
	@$(MAKE) -C mint-health r-ssl-status ROUTER=$(ROUTER)

ssl-remove-force: ## Force-remove SSL config on router (cleanup)
	$(call require_hardware_lock)
	@$(MAKE) -C mint-health r-ssl-remove-force ROUTER=$(ROUTER)

test-ssl-setup-verify: ## Verify router is in clean SSL state
	$(call require_hardware_lock)
	@$(MAKE) -C mint-health r-test-ssl-setup-verify ROUTER=$(ROUTER)

test-ssl-self-signed-yes: ## Test self-signed apply with --yes flag
	$(call require_hardware_lock)
	@$(MAKE) -C mint-health r-test-ssl-self-signed-yes ROUTER=$(ROUTER)

test-ssl-reapply: ## Test re-apply with existing backup (overwrite warning)
	$(call require_hardware_lock)
	@$(MAKE) -C mint-health r-test-ssl-reapply ROUTER=$(ROUTER)

test-ssl-remove-no-backup: ## Test remove when no backup exists (error path)
	$(call require_hardware_lock)
	@$(MAKE) -C mint-health r-test-ssl-remove-no-backup ROUTER=$(ROUTER)

test-ssl-verify-cert: ## Deep cert validation: SAN, CN, expiry, permissions
	$(call require_hardware_lock)
	@$(MAKE) -C mint-health r-test-ssl-verify-cert ROUTER=$(ROUTER)

test-ssl-verify-nds: ## Verify nodogsplash allows port 443
	$(call require_hardware_lock)
	@$(MAKE) -C mint-health r-test-ssl-verify-nds ROUTER=$(ROUTER)

test-ssl-verify-no-dns: ## Verify no dnsmasq domain for self-signed
	$(call require_hardware_lock)
	@$(MAKE) -C mint-health r-test-ssl-verify-no-dns ROUTER=$(ROUTER)

test-ssl-idempotent: ## Test apply twice — verify state consistent
	$(call require_hardware_lock)
	@$(MAKE) -C mint-health r-test-ssl-idempotent ROUTER=$(ROUTER)

test-ssl-comprehensive: ## All self-signed SSL tests in sequence (~10 min)
	$(call require_hardware_lock)
	@$(MAKE) -C mint-health r-test-ssl-comprehensive ROUTER=$(ROUTER)

test-ssl-real-cert: ## Test real cert via LE staging + Cloudflare DNS-01
	$(call require_hardware_lock)
	@$(MAKE) -C mint-health r-test-ssl-real-cert ROUTER=$(ROUTER)

test-ssl-real-cert-remove: ## Test real cert removal (dnsmasq + NDS revert)
	$(call require_hardware_lock)
	@$(MAKE) -C mint-health r-test-ssl-real-cert-remove ROUTER=$(ROUTER)

test-ssl-real-cert-full: ## Full real cert lifecycle (~5 min)
	$(call require_hardware_lock)
	@$(MAKE) -C mint-health r-test-ssl-real-cert-full ROUTER=$(ROUTER)

test-ssl-all: ## Run ALL SSL tests: comprehensive + real cert (~20 min)
	$(call require_hardware_lock)
	@$(MAKE) -C mint-health r-test-ssl-all ROUTER=$(ROUTER)

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
	@echo ""
	@echo "$(CYAN)--- ESP32 Local Relay tests ---$(RESET)"
	@echo "  make relay-build                         # build relay firmware (no lock)"
	@echo "  make relay-flash-b                       # flash relay firmware to Board B"
	@echo "  make relay-test-smoke                    # verify relay port 4869"
	@echo "  make relay-test-nip11                    # NIP-11 relay info test"
	@echo "  make relay-test-pubsub                   # WS pub/sub test"
	@echo "  make relay-test-full                     # all relay tests"

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
#  LOCAL RELAY TESTS (Board B, feature/local-relay)
# ===========================================================================

 .PHONY: relay-build relay-flash-b relay-connect-b \
         relay-test-smoke relay-test-nip11 relay-test-pubsub \
         relay-test-sync relay-test-full

relay-build: ## Build relay firmware (no lock)
	@$(MAKE) -C esp32 relay-build

relay-flash-b: ## Flash relay firmware to Board B (requires lock-b)
	@$(MAKE) -C esp32 relay-flash-b

relay-connect-b: ## WiFi connect to Board B AP (requires lock-b)
	@$(MAKE) -C esp32 relay-connect-b

relay-test-smoke: ## Relay smoke test: port 4869 reachable (requires lock-b)
	@$(MAKE) -C esp32 relay-test-smoke

relay-test-nip11: ## NIP-11 relay info document test (requires lock-b)
	@$(MAKE) -C esp32 relay-test-nip11

relay-test-pubsub: ## WebSocket publish + subscribe test (requires lock-b)
	@$(MAKE) -C esp32 relay-test-pubsub

relay-test-sync: ## Verify sync to public relays (requires lock-b)
	@$(MAKE) -C esp32 relay-test-sync

relay-test-full: ## Run all relay tests (~1min, requires lock-b)
	@$(MAKE) -C esp32 relay-test-full

# ===========================================================================
#  ARCH COMPONENT EXTRACTION TESTS (tollgate_core on Board A)
# ===========================================================================

arch-build: ## Build arch (tollgate_core) firmware
	@$(MAKE) -C esp32 arch-build

arch-flash-a: ## Flash arch firmware to Board A (requires lock)
	@$(MAKE) -C esp32 arch-flash-a

arch-generate-spiffs: ## Generate SPIFFS with auto-detected WPA mode
	@$(MAKE) -C esp32 arch-generate-spiffs

arch-flash-spiffs-a: ## Flash SPIFFS to Board A with WPA config (requires lock)
	@$(MAKE) -C esp32 arch-flash-spiffs-a

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

# ===========================================================================
#  PYTEST / CI / REPORT TARGETS (from main branch)
# ===========================================================================

.PHONY: pytest-smoke pytest-critical pytest-extended pytest-api pytest-phone \
        pytest-test \
        pytest-smoke-mac pytest-critical-mac pytest-api-mac pytest-test-mac \
        pytest-smoke-linux pytest-api-linux pytest-test-linux \
        pytest-smoke-rust pytest-api-rust pytest-test-rust pytest-critical-rust \
        luci deploy-ci deploy-ci-rust setup-python \
        run-api run-phone run-luci run-all \
        collect render-report sanitize publish pr-smoke clean

# --- Pytest test tiers (raw pytest, no canonical run dir) ---

pytest-smoke:
	pytest -m smoke

pytest-critical:
	pytest -m critical

pytest-extended:
	pytest -m extended

pytest-api:
	pytest -m api

pytest-phone:
	pytest -m phone --publish

pytest-test:
	pytest

# --- macOS client (no phone) ---

pytest-smoke-mac:
	pytest -m smoke --client=mac

pytest-critical-mac:
	pytest -m critical --client=mac

pytest-api-mac:
	pytest -m api --client=mac

pytest-test-mac:
	pytest --client=mac

# --- Linux client (no phone) ---

pytest-smoke-linux:
	pytest -m smoke --client=linux

pytest-api-linux:
	pytest -m api --client=linux

pytest-test-linux:
	pytest --client=linux

# --- Rust v1 backend ---

pytest-smoke-rust:
	TOLLGATE_BACKEND=rust pytest -m smoke --backend=rust

pytest-api-rust:
	TOLLGATE_BACKEND=rust pytest -m api --backend=rust

pytest-test-rust:
	TOLLGATE_BACKEND=rust pytest --backend=rust

pytest-critical-rust:
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

# --- CI artifact deploy ---

deploy-ci:
	bash scripts/deploy-ci.sh

deploy-ci-rust:
	bash scripts/deploy-rust-ci.sh

# --- Setup (Python venv) ---

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
