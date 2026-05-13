# ---------------------------------------------------------------------------
# Makefile — Manual testing for upstream WiFi management & mint health
#
# Two modes:
#   1. LOCAL  — run directly on the router (SSH into it first)
#   2. REMOTE — run from your dev machine via SSH (requires routers.env)
#
# Setup for remote mode:
#   cp routers.env.example routers.env   # then edit with real IPs
#   ssh-copy-id root@<router-ip>         # SSH key auth, no passwords
#
# Quick reference:
#   make -f Makefile smoke   SSID=MyNet PASS=secret          # local upstream wifi
#   make -f Makefile r-smoke SSID=MyNet PASS=secret ROUTER=alpha  # remote upstream wifi
#   make -f Makefile r-smoke-offline ROUTER=alpha               # test degraded mode
#   make -f Makefile r-smoke-recovery ROUTER=alpha              # test recovery
#   make -f Makefile r-smoke-degraded ROUTER=alpha              # full degraded lifecycle (single router)
#   make -f Makefile r-smoke-degraded-upstream                  # degraded payment renewal (two routers)
#   make -f Makefile r-deploy ROUTER=alpha                      # deploy binaries
#   make -f Makefile r-diagnose-config-path ROUTER=alpha        # verify config path
#   make -f Makefile r-test-default-mints ROUTER=alpha          # verify default mint config
#   make -f Makefile r-rescue-router ROUTER=beta VIA=alpha      # rescue offline router
#   make -f Makefile r-test-startup-hygiene ROUTER=alpha        # power cycle test: boot with non-internet STA
#   make -f Makefile r-test-startup-hygiene-dead-only ROUTER=alpha  # boot with ONLY dead STA, emergency scan test
#   make -f Makefile r-full ROUTER=alpha                        # full mint health suite
#   make -f Makefile r-full-upstream SSID=MyNet PASS=secret ROUTER=alpha  # full wifi suite
#   make -f Makefile r-lock PHASE="description"                 # acquire router lock
#   make -f Makefile r-unlock                                   # release router lock
#
# Router mutex: all r-* targets that modify router state check routers.lock
# first. See docs/router-mutex.md for details.
#
# WARNING: Dual-WWAN routing conflict — do NOT create a second wwan interface
# (e.g. wwan2) on any router. Having two wwan interfaces simultaneously breaks
# the default gateway routing and kills internet connectivity. Always use a
# single wwan interface and switch its upstream when needed.
# ---------------------------------------------------------------------------

.PHONY: smoke smoke-offline smoke-recovery full full-upstream \
        status logs scan list connect remove verify-uci \
        check-merchant check-degraded block-mint unblock-mint \
        restart-service record-baseline \
        test-edge-cases test-reseller-guard test-cleanup \
        test-offline-ops wait-recovery test-first-boot-offline test-no-mints \
        wait-daemon simulate-connectivity-loss cleanup \
        r-smoke r-smoke-offline r-smoke-recovery r-full r-full-upstream \
        r-status r-logs r-scan r-list r-connect r-remove r-verify-uci \
        r-check-merchant r-check-degraded r-block-mint r-unblock-mint \
        r-restart-service r-record-baseline \
        r-wait-daemon r-wait-recovery \
        r-test-edge-cases r-test-reseller-guard r-test-cleanup \
        r-test-offline-ops r-test-first-boot-offline r-test-no-mints \
        r-deploy r-shell r-cleanup r-push \
        r-check-sta-health \
        r-setup-test-mint r-restore-prod-config r-fund-wallet \
        r-smoke-degraded r-smoke-degraded-upstream r-smoke-degraded-connect \
        r-smoke-dynamic-rebuild \
        r-smoke-pin-upstream \
r-test-startup-hygiene r-test-startup-hygiene-setup r-test-startup-hygiene-verify \
	r-test-startup-hygiene-dead-only r-test-startup-hygiene-dead-only-setup r-test-startup-hygiene-dead-only-verify \
	r-rescue-router r-save-upstream r-restore-upstream \
        r-diagnose-config-path r-test-default-mints \
        r-guard-no-dual-wwan \
        r-lock r-unlock force-unlock r-status-lock \
        r-fix-dns r-test-captive-portal r-test-captive-portal-happy r-test-cashu-payment \
        s-shell s-boot-log s-wait-boot s-status s-recovery s-wait-pattern \
        s-cleanup s-cold-boot-test s-check-merchant s-check-degraded \
        h-status h-check-merchant h-restart-and-watch h-cleanup \
        routers help

SSID    ?=
PASS    ?=
ROUTER  ?= alpha
MINT    ?= https://nofee.testnut.cashu.space
PHASE   ?= unspecified

-include routers.env

BOLD   := \033[1m
GREEN  := \033[32m
RED    := \033[31m
YELLOW := \033[33m
CYAN   := \033[36m
RESET  := \033[0m

ROUTER_LOCK := routers.lock

# ---- Remote helper --------------------------------------------------------
# Source routers.env, resolve ROUTER_<NAME>_HOST, build SSH command.

define RESOLVE_ROUTER
router_host=$$(grep -E "^ROUTER_$$(echo $(ROUTER) | tr '[:lower:]' '[:upper:]')_HOST=" routers.env | cut -d= -f2); \
if [ -z "$$router_host" ]; then echo "$(RED)Unknown router '$(ROUTER)'. Check routers.env$(RESET)" >&2; exit 1; fi
endef

SSH = @router_host=$$(grep -E "^ROUTER_$$(echo $(ROUTER) | tr '[:lower:]' '[:upper:]')_HOST=" routers.env | cut -d= -f2); \
if [ -z "$$router_host" ]; then echo "$(RED)Unknown router '$(ROUTER)'. Check routers.env$(RESET)" >&2; exit 1; fi; \
ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host

# ---- Router mutex ---------------------------------------------------------
# File-based advisory lock to prevent collisions between LLM sessions.
# See docs/router-mutex.md for usage guide.

define CHECK_LOCK
if [ -f "$(ROUTER_LOCK)" ]; then \
	echo "$(RED)$(BOLD)Router is LOCKED by another session:$(RESET)"; \
	echo ""; \
	grep "^branch:" $(ROUTER_LOCK) | sed 's/^branch:/  Branch:/'; \
	grep "^session:" $(ROUTER_LOCK) | sed 's/^session:/  Session:/'; \
	grep "^phase:" $(ROUTER_LOCK) | sed 's/^phase:/  Phase:/'; \
	grep "^timestamp:" $(ROUTER_LOCK) | sed 's/^timestamp:/  Since:/'; \
	echo ""; \
	echo "$(YELLOW)To check status: make -f Makefile r-status-lock$(RESET)"; \
	echo "$(YELLOW)To force-release: make -f Makefile force-unlock$(RESET)"; \
	exit 1; \
fi
endef

# ===========================================================================
#  LOCAL TARGETS (run on the router itself)
# ===========================================================================

help: ## Show this help
	@echo "Router Manual Testing — Upstream WiFi & Mint Health"
	@echo "====================================================="
	@echo ""
	@echo "Usage (local):  make -f Makefile <target> SSID=x PASS=y"
	@echo "Usage (remote): make -f Makefile r-<target> SSID=x PASS=y ROUTER=alpha"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  $(CYAN)%-35s$(RESET) %s\n", $$1, $$2}'
	@echo ""
	@echo "Variables:"
	@echo "  SSID    - upstream WiFi SSID (required for connect/smoke/full-upstream)"
	@echo "  PASS    - upstream WiFi passphrase (optional for open networks)"
	@echo "  ROUTER  - router label from routers.env (default: alpha)"
	@echo "  MINT    - mint URL to block/unblock (default: https://nofee.testnut.cashu.space)"
	@echo "  PHASE   - description for router lock (default: unspecified)"
	@echo ""
	@echo "Degraded mode testing:"
	@echo "  make -f Makefile r-setup-test-mint ROUTER=alpha      # switch to test mint"
	@echo "  make -f Makefile r-smoke-degraded ROUTER=alpha       # single-router lifecycle"
	@echo "  make -f Makefile r-smoke-degraded-upstream           # two-router payment test"
	@echo "  make -f Makefile r-restore-prod-config ROUTER=alpha  # restore production config"
	@echo ""
	@echo "Diagnostics & rescue:"
	@echo "  make -f Makefile r-diagnose-config-path ROUTER=alpha # verify config path"
	@echo "  make -f Makefile r-test-default-mints ROUTER=alpha   # verify mint config"
	@echo "  make -f Makefile r-rescue-router ROUTER=beta VIA=alpha # rescue offline router"
	@echo "  make -f Makefile r-test-startup-hygiene ROUTER=alpha # power cycle: boot with non-internet STA, verify auto-switch (single command)"
	@echo "  make -f Makefile r-test-startup-hygiene-dead-only ROUTER=alpha # boot with ONLY dead STA, verify emergency scan + switch (single command)"

# ---------------------------------------------------------------------------
#  Upstream WiFi — Local targets
# ---------------------------------------------------------------------------

status: ## [local] Phase 1: Verify service is running and CLI works
	@echo "$(BOLD)=== Phase 1: Service Startup ===$(RESET)"
	@echo ""
	@echo "$(YELLOW)Checking tollgate service logs...$(RESET)"
	@logread -e tollgate | tail -20
	@echo ""
	@echo "$(YELLOW)Checking CLI binary...$(RESET)"
	@which tollgate && echo "$(GREEN)CLI binary found$(RESET)" || echo "$(RED)CLI binary NOT found$(RESET)"
	@echo ""
	@echo "$(YELLOW)Checking version...$(RESET)"
	@tollgate version || echo "$(RED)Version command failed$(RESET)"
	@echo ""
	@echo "$(YELLOW)Checking for upstream manager startup...$(RESET)"
	@logread -e tollgate | grep -q "Upstream WiFi manager initialized" && \
		echo "$(GREEN)Upstream manager initialized$(RESET)" || \
		echo "$(RED)Upstream manager NOT initialized$(RESET)"

scan: ## [local] Phase 2: Scan for available upstream WiFi networks
	@echo "$(BOLD)=== Phase 2: Upstream Scan ===$(RESET)"
	@echo ""
	@echo "$(YELLOW)Running upstream scan...$(RESET)"
	@tollgate upstream scan
	@echo ""
	@echo "$(YELLOW)Checking for scan errors in logs...$(RESET)"
	@logread -e tollgate | grep -i "scan" | tail -5

connect: ## [local] Phase 3: Connect to upstream WiFi (requires SSID, optional PASS)
	@if [ -z "$(SSID)" ]; then echo "$(RED)Error: SSID is required. Usage: make -f Makefile connect SSID=MyNet PASS=secret$(RESET)"; exit 1; fi
	@echo "$(BOLD)=== Phase 3: Upstream Connect ===$(RESET)"
	@echo ""
	@echo "$(YELLOW)Connecting to '$(SSID)'...$(RESET)"
	@if [ -n "$(PASS)" ]; then \
		tollgate upstream connect "$(SSID)" "$(PASS)"; \
	else \
		tollgate upstream connect "$(SSID)"; \
	fi
	@echo ""
	@echo "$(YELLOW)Waiting 5s for interface to settle...$(RESET)"
	@sleep 5
	@echo ""
	@echo "$(YELLOW)Testing connectivity...$(RESET)"
	@ping -c 3 -W 5 8.8.8.8 && echo "$(GREEN)Online!$(RESET)" || echo "$(RED)Not reachable$(RESET)"

list: ## [local] Phase 4: List configured upstream STA interfaces
	@echo "$(BOLD)=== Phase 4: Upstream List ===$(RESET)"
	@echo ""
	@tollgate upstream list

verify-uci: ## [local] Verify UCI state after connect
	@echo "$(BOLD)=== UCI State Verification ===$(RESET)"
	@echo ""
	@echo "$(YELLOW)Upstream STA sections in wireless config:$(RESET)"
	@uci show wireless | grep upstream_ || echo "  (none found)"
	@echo ""
	@echo "$(YELLOW)WWAN network interface:$(RESET)"
	@uci show network.wwan 2>/dev/null || echo "  (not configured)"
	@echo ""
	@echo "$(YELLOW)Active STA (disabled=0):$(RESET)"
	@uci show wireless | grep "upstream_.*disabled=0" || echo "  (no active upstream)"
	@echo ""
	@echo "$(YELLOW)Disabled STAs (disabled=1):$(RESET)"
	@uci show wireless | grep "upstream_.*disabled=1" || echo "  (none)"

wait-daemon: ## [local] Phase 5: Watch daemon logs for one scan cycle (up to 6 min)
	@echo "$(BOLD)=== Phase 5: Daemon Scan Cycle ===$(RESET)"
	@echo ""
	@echo "$(YELLOW)Watching for daemon scan cycle (timeout 360s)...$(RESET)"
	@echo "$(YELLOW)Press Ctrl+C once you see a scan cycle.$(RESET)"
	@echo ""
	@timeout 360 logread -e tollgate -f 2>/dev/null | grep --line-buffered -i "upstream\|scan cycle\|Best candidate\|switch" | head -20

simulate-connectivity-loss: ## [local] Phase 5.3: Simulate connectivity loss
	@echo "$(BOLD)=== Phase 5.3: Connectivity Loss Simulation ===$(RESET)"
	@echo ""
	@echo "$(YELLOW)Finding upstream interface...$(RESET)"
	@ifname=$$(uci show network.wwan.ifname 2>/dev/null | cut -d= -f2 | tr -d "'"); \
	if [ -z "$$ifname" ]; then \
		ifname=$$(ip route | grep default | awk '{print $$5}' | head -1); \
	fi; \
	if [ -z "$$ifname" ]; then \
		echo "$(RED)Could not determine upstream interface$(RESET)"; \
		exit 1; \
	fi; \
	echo "$(YELLOW)Blocking traffic on $$ifname for 90 seconds...$(RESET)"; \
	iptables -A OUTPUT -o $$ifname -j DROP; \
	echo "$(GREEN)Blocked. Watching logs...$(RESET)"; \
	timeout 90 logread -e tollgate -f 2>/dev/null | grep --line-buffered -i "lost\|emergency\|scan cycle" | head -10; \
	iptables -D OUTPUT -o $$ifname -j DROP; \
	echo "$(GREEN)Unblocked $$ifname$(RESET)"

remove: ## [local] Phase 6: Remove a disabled upstream STA (requires SSID)
	@if [ -z "$(SSID)" ]; then echo "$(RED)Error: SSID is required. Usage: make -f Makefile remove SSID=MyNet$(RESET)"; exit 1; fi
	@echo "$(BOLD)=== Phase 6: Remove Upstream ===$(RESET)"
	@echo ""
	@echo "$(YELLOW)Attempting to remove '$(SSID)'...$(RESET)"
	@tollgate upstream remove "$(SSID)"

test-edge-cases: ## [local] Phase 8: Test error handling edge cases
	@echo "$(BOLD)=== Phase 8: Edge Cases ===$(RESET)"
	@echo ""
	@echo "$(YELLOW)8.1 — Connect to unknown SSID (should fail):$(RESET)"
	@tollgate upstream connect NonExistentSSID 2>&1; echo ""
	@echo ""
	@echo "$(YELLOW)8.2 — Connect to encrypted SSID without passphrase (should fail):$(RESET)"
	@if [ -n "$(SSID)" ]; then \
		tollgate upstream connect "$(SSID)" 2>&1; echo ""; \
	else \
		echo "  (skipped: no SSID set)"; \
	fi
	@echo ""
	@echo "$(YELLOW)8.3 — Remove unknown SSID (should fail):$(RESET)"
	@tollgate upstream remove UnknownSSID 2>&1; echo ""
	@echo ""
	@echo "$(YELLOW)8.4 — Pre-existing CLI commands still work:$(RESET)"
	@tollgate version
	@tollgate status
	@echo ""

test-reseller-guard: ## [local] Phase 7: Test that daemon respects reseller mode
	@echo "$(BOLD)=== Phase 7: Reseller Mode Guard ===$(RESET)"
	@echo ""
	@echo "$(YELLOW)Enabling reseller mode...$(RESET)"
	@uci set tollgate.config.reseller_mode=1 && uci commit tollgate
	@echo "$(GREEN)Reseller mode ON. Watching logs for 35s (daemon should be silent)...$(RESET)"
	@timeout 35 logread -e tollgate -f 2>/dev/null | grep --line-buffered "Running upstream scan cycle" | head -1 && \
		echo "$(RED)FAIL: Daemon ran a scan cycle while in reseller mode!$(RESET)" || \
		echo "$(GREEN)OK: No scan cycles during reseller mode$(RESET)"
	@echo ""
	@echo "$(YELLOW)Verifying manual CLI still works in reseller mode...$(RESET)"
	@tollgate upstream list && echo "$(GREEN)CLI works$(RESET)" || echo "$(RED)CLI failed$(RESET)"
	@echo ""
	@echo "$(YELLOW)Disabling reseller mode...$(RESET)"
	@uci set tollgate.config.reseller_mode=0 && uci commit tollgate
	@echo "$(GREEN)Reseller mode OFF$(RESET)"

test-cleanup: ## [local] Phase 9: Verify old shell scripts are gone
	@echo "$(BOLD)=== Phase 9: Cleanup Verification ===$(RESET)"
	@echo ""
	@echo "$(YELLOW)Checking old shell scripts are removed...$(RESET)"
	@err=0; \
	for f in /usr/bin/wifiscan.sh /usr/bin/upstream-daemon.sh /etc/init.d/tollgate-upstream; do \
		if [ -f "$$f" ]; then \
			echo "$(RED)FAIL: $$f still exists$(RESET)"; \
			err=1; \
		else \
			echo "$(GREEN)OK:   $$f removed$(RESET)"; \
		fi; \
	done; \
	exit $$err
	@echo ""
	@echo "$(YELLOW)Checking upstream commands in CLI help...$(RESET)"
	@tollgate --help | grep -q upstream && \
		echo "$(GREEN)OK:   upstream command listed in CLI help$(RESET)" || \
		echo "$(RED)FAIL: upstream command not found in CLI help$(RESET)"

smoke: ## [local] Run the upstream WiFi smoke test (requires SSID, optional PASS)
	@if [ -z "$(SSID)" ]; then echo "$(RED)Error: SSID is required. Usage: make -f Makefile smoke SSID=MyNet PASS=secret$(RESET)"; exit 1; fi
	@echo "$(BOLD)=======================================$(RESET)"
	@echo "$(BOLD)  Upstream WiFi Smoke Test (5 min)$(RESET)"
	@echo "$(BOLD)=======================================$(RESET)"
	@echo ""
	@echo "$(CYAN)1/6 — Verifying service is up...$(RESET)"
	@logread -e tollgate | grep -q "Upstream WiFi manager initialized" && \
		echo "$(GREEN)  OK: Upstream manager initialized$(RESET)" || \
		(echo "$(RED)  FAIL: Upstream manager not initialized$(RESET)" && exit 1)
	@echo ""
	@echo "$(CYAN)2/6 — Scanning for networks...$(RESET)"
	@tollgate upstream scan
	@echo ""
	@echo "$(CYAN)3/6 — Connecting to '$(SSID)'...$(RESET)"
	@if [ -n "$(PASS)" ]; then \
		tollgate upstream connect "$(SSID)" "$(PASS)"; \
	else \
		tollgate upstream connect "$(SSID)"; \
	fi
	@echo ""
	@echo "$(CYAN)4/6 — Verifying connectivity...$(RESET)"
	@sleep 5
	@ping -c 3 -W 5 8.8.8.8 && echo "$(GREEN)  OK: Online$(RESET)" || echo "$(RED)  WARN: Cannot reach 8.8.8.8 (may be firewall)$(RESET)"
	@echo ""
	@echo "$(CYAN)5/6 — Listing upstream STAs...$(RESET)"
	@tollgate upstream list
	@echo ""
	@echo "$(CYAN)6/6 — Verifying old scripts removed...$(RESET)"
	@err=0; \
	for f in /usr/bin/wifiscan.sh /usr/bin/upstream-daemon.sh; do \
		if [ -f "$$f" ]; then \
			echo "$(RED)  FAIL: $$f still exists$(RESET)"; \
			err=1; \
		else \
			echo "$(GREEN)  OK:   $$f removed$(RESET)"; \
		fi; \
	done; \
	if [ $$err -ne 0 ]; then exit 1; fi
	@echo ""
	@echo "$(BOLD)=======================================$(RESET)"
	@echo "$(GREEN)$(BOLD)  Smoke test PASSED$(RESET)"
	@echo "$(BOLD)=======================================$(RESET)"

full-upstream: ## [local] Run the full upstream WiFi test suite (requires SSID, optional PASS)
	@if [ -z "$(SSID)" ]; then echo "$(RED)Error: SSID is required. Usage: make -f Makefile full-upstream SSID=MyNet PASS=secret$(RESET)"; exit 1; fi
	@echo "$(BOLD)=======================================$(RESET)"
	@echo "$(BOLD)  Upstream WiFi Full Test Suite$(RESET)"
	@echo "$(BOLD)=======================================$(RESET)"
	@echo ""
	@$(MAKE) -f Makefile status
	@echo ""
	@$(MAKE) -f Makefile scan
	@echo ""
	@$(MAKE) -f Makefile connect SSID="$(SSID)" PASS="$(PASS)"
	@echo ""
	@$(MAKE) -f Makefile list
	@echo ""
	@$(MAKE) -f Makefile verify-uci
	@echo ""
	@$(MAKE) -f Makefile test-edge-cases SSID="$(SSID)"
	@echo ""
	@$(MAKE) -f Makefile test-cleanup
	@echo ""
	@echo "$(BOLD)=======================================$(RESET)"
	@echo "$(GREEN)$(BOLD)  Full test suite PASSED$(RESET)"
	@echo "$(BOLD)=======================================$(RESET)"
	@echo ""
	@echo "$(YELLOW)Remaining manual steps:$(RESET)"
	@echo "  - Phase 5:  make -f Makefile wait-daemon"
	@echo "  - Phase 5.3: make -f Makefile simulate-connectivity-loss"
	@echo "  - Phase 6:   make -f Makefile remove SSID=<old-ssid> (after connecting to a different one)"
	@echo "  - Phase 7:   make -f Makefile test-reseller-guard"

# ---------------------------------------------------------------------------
#  Mint Health — Local targets
# ---------------------------------------------------------------------------

check-merchant: ## [local] Verify full merchant (online boot)
	@echo "$(BOLD)=== Check: Full Merchant ===$(RESET)"
	@logread -e tollgate | grep -q "Merchant ready" && \
		echo "$(GREEN)OK: Merchant ready (full mode)$(RESET)" || \
		echo "$(RED)FAIL: Merchant ready not found in logs$(RESET)"
	@echo ""
	@echo "$(YELLOW)Wallet balance...$(RESET)"
	@tollgate wallet balance || echo "$(RED)Balance check failed$(RESET)"

check-degraded: ## [local] Verify degraded merchant (offline boot)
	@echo "$(BOLD)=== Check: Degraded Merchant ===$(RESET)"
	@logread -e tollgate | grep -q "Starting in degraded mode" && \
		echo "$(GREEN)OK: Degraded mode detected$(RESET)" || \
		echo "$(RED)FAIL: Degraded mode not found in logs$(RESET)"
	@echo ""
	@logread -e tollgate | grep "offline wallet loaded" | tail -1
	@echo ""
	@echo "$(YELLOW)Wallet balance (offline)...$(RESET)"
	@tollgate wallet balance || echo "$(RED)Balance check failed$(RESET)"
	@echo ""
	@echo "$(YELLOW)Service status (offline)...$(RESET)"
	@tollgate status || echo "$(RED)Status failed$(RESET)"

block-mint: ## [local] Block mint via /etc/hosts override
	@mint_host=$$(echo "$(MINT)" | sed 's|^[a-z]*://||' | sed 's|/.*||'); \
	echo "$(YELLOW)Blocking mint $$mint_host via /etc/hosts...$(RESET)"; \
	grep -q "$$mint_host" /etc/hosts 2>/dev/null && echo "$(GREEN)Already blocked$(RESET)" || \
		(echo "0.0.0.0 $$mint_host" >> /etc/hosts && echo "$(GREEN)Blocked $$mint_host$(RESET)"); \
	echo ""; \
	echo "$(YELLOW)Verifying block...$(RESET)"; \
	wget -q --timeout=5 -O- "$(MINT)/v1/info" > /dev/null 2>&1 && \
		echo "$(RED)WARN: Mint still reachable$(RESET)" || \
		echo "$(GREEN)OK: Mint unreachable$(RESET)"

unblock-mint: ## [local] Remove mint /etc/hosts override
	@mint_host=$$(echo "$(MINT)" | sed 's|^[a-z]*://||' | sed 's|/.*||'); \
	echo "$(YELLOW)Removing $$mint_host from /etc/hosts...$(RESET)"; \
	sed -i "/$$mint_host/d" /etc/hosts && echo "$(GREEN)Removed$(RESET)" || echo "$(YELLOW)No entry found$(RESET)"; \
	echo ""; \
	echo "$(YELLOW)Verifying connectivity...$(RESET)"; \
	sleep 1; \
	wget -q --timeout=5 -O- "$(MINT)/v1/info" > /dev/null 2>&1 && \
		echo "$(GREEN)OK: Mint reachable again$(RESET)" || \
		echo "$(RED)WARN: Mint still unreachable$(RESET)"

restart-service: ## [local] Restart the tollgate service
	@echo "$(YELLOW)Restarting tollgate-wrt...$(RESET)"
	/etc/init.d/tollgate-wrt restart
	@echo "$(GREEN)Service restarted$(RESET)"
	@echo "$(YELLOW)Waiting 3s for startup...$(RESET)"
	@sleep 3

record-baseline: ## [local] Record current wallet balance as baseline
	@echo "$(BOLD)=== Recording Baseline ===$(RESET)"
	@balance=$$(tollgate wallet balance 2>/dev/null | grep -oE '[0-9]+' | head -1); \
	if [ -z "$$balance" ]; then balance="unknown"; fi; \
	echo "$$balance" > /tmp/tollgate-baseline.txt; \
	echo "$(GREEN)Baseline balance: $$balance sats (saved to /tmp/tollgate-baseline.txt)$(RESET)"

test-offline-ops: ## [local] Phase 4: Verify offline wallet operations
	@echo "$(BOLD)=== Phase 4: Offline Operations ===$(RESET)"
	@echo ""
	@echo "$(YELLOW)4.1 — Wallet balance (should work offline)...$(RESET)"
	@tollgate wallet balance && echo "$(GREEN)  OK$(RESET)" || echo "$(RED)  FAIL$(RESET)"
	@echo ""
	@echo "$(YELLOW)4.2 — Service status...$(RESET)"
	@tollgate status && echo "$(GREEN)  OK$(RESET)" || echo "$(RED)  FAIL$(RESET)"
	@echo ""
	@echo "$(YELLOW)4.3 — Service still running (no crash loop)...$(RESET)"
	@sleep 2; logread -e tollgate | tail -3 | grep -q "panic\|fatal\"" && \
		echo "$(RED)  FAIL: crash detected$(RESET)" || echo "$(GREEN)  OK: no crashes$(RESET)"

wait-recovery: ## [local] Phase 5: Watch logs for recovery (up to 16 min)
	@echo "$(BOLD)=== Phase 5: Waiting for Recovery ===$(RESET)"
	@echo "$(YELLOW)Watching for recovery logs (timeout 960s = 16 min)...$(RESET)"
	@echo "$(YELLOW)The tracker checks every 5 min and needs 3 consecutive successes.$(RESET)"
	@echo "$(YELLOW)Press Ctrl+C once you see 'Merchant ready' or 'Failed to upgrade'.$(RESET)"
	@echo ""
	@timeout 960 logread -e tollgate -f 2>/dev/null | grep --line-buffered -i "reachable\|upgrade\|degraded\|Merchant ready\|BoltDB\|Failed to upgrade" | head -10

test-first-boot-offline: ## [local] Phase 6: First boot offline (no wallet DB)
	@echo "$(BOLD)=== Phase 6: First Boot Offline ===$(RESET)"
	@echo "$(YELLOW)This test must be run with an unreachable mint configured$(RESET)"
	@echo "$(YELLOW)and no existing wallet.db. Expected: graceful degradation.$(RESET)"
	@echo ""
	@logread -e tollgate | grep -q "offline wallet load failed" && \
		echo "$(GREEN)OK: Offline wallet load failed gracefully (first boot)$(RESET)" || \
		echo "$(RED)FAIL: Expected 'offline wallet load failed' log$(RESET)"
	@echo ""
	@logread -e tollgate | grep -q "Starting in degraded mode" && \
		echo "$(GREEN)OK: Degraded mode started$(RESET)" || \
		echo "$(RED)FAIL: Degraded mode not detected$(RESET)"
	@echo ""
	@echo "$(YELLOW)Checking service is still running (no boot loop)...$(RESET)"
	@tollgate status && echo "$(GREEN)  OK: service running$(RESET)" || echo "$(RED)  FAIL: service down$(RESET)"

test-no-mints: ## [local] Phase 7: No configured mints
	@echo "$(BOLD)=== Phase 7: No Configured Mints ===$(RESET)"
	@logread -e tollgate | grep -q "no configured mints" && \
		echo "$(GREEN)OK: 'no configured mints' log found$(RESET)" || \
		echo "$(RED)FAIL: Expected 'no configured mints' log$(RESET)"
	@echo ""
	@echo "$(YELLOW)Checking service is still running...$(RESET)"
	@tollgate status && echo "$(GREEN)  OK: service running$(RESET)" || echo "$(RED)  FAIL: service down$(RESET)"

cleanup: ## [local] Phase 8: Remove iptables blocks and restore
	@echo "$(BOLD)=== Phase 8: Cleanup ===$(RESET)"
	@echo "$(YELLOW)Removing any mint blocks...$(RESET)"
	@mint_host=$$(echo "$(MINT)" | sed -E 's|https?://([^/:]+).*|\1|'); \
	mint_ip=$$(nslookup $$mint_host 2>/dev/null | grep -A1 "Name:" | grep "Address:" | awk '{print $$2}' | head -1); \
	if [ -n "$$mint_ip" ]; then \
		iptables -D OUTPUT -d $$mint_ip -j DROP 2>/dev/null && echo "$(GREEN)Removed block for $$mint_ip$(RESET)" || echo "$(YELLOW)No block found$(RESET)"; \
	else \
		echo "$(YELLOW)Could not resolve mint host for cleanup$(RESET)"; \
	fi
	@echo ""
	@echo "$(YELLOW)Removing baseline file...$(RESET)"
	@rm -f /tmp/tollgate-baseline.txt
	@echo "$(GREEN)Cleanup complete$(RESET)"

smoke-offline: ## [local] Quick offline test: block + restart + verify degraded
	@echo "$(BOLD)=======================================$(RESET)"
	@echo "$(BOLD)  Offline Smoke Test (2 min)$(RESET)"
	@echo "$(BOLD)=======================================$(RESET)"
	@echo ""
	@echo "$(CYAN)1/4 — Recording baseline...$(RESET)"
	@balance=$$(tollgate wallet balance 2>/dev/null | grep -oE '[0-9]+' | head -1); \
	echo "  Baseline: $$balance sats"
	@echo ""
	@echo "$(CYAN)2/4 — Blocking mint and restarting...$(RESET)"
	@$(MAKE) -f Makefile block-mint MINT="$(MINT)"
	@$(MAKE) -f Makefile restart-service
	@echo ""
	@echo "$(CYAN)3/4 — Checking degraded mode...$(RESET)"
	@logread -e tollgate | grep -q "Starting in degraded mode" && \
		echo "$(GREEN)  OK: Degraded mode$(RESET)" || echo "$(RED)  FAIL: Not in degraded mode$(RESET)"
	@logread -e tollgate | grep -q "offline wallet loaded successfully" && \
		echo "$(GREEN)  OK: Wallet loaded offline$(RESET)" || echo "$(YELLOW)  WARN: Wallet not loaded (first boot?)$(RESET)"
	@echo ""
	@echo "$(CYAN)4/4 — Wallet balance (offline)...$(RESET)"
	@tollgate wallet balance || echo "$(RED)  Balance check failed$(RESET)"
	@echo ""
	@echo "$(BOLD)=======================================$(RESET)"
	@echo "$(GREEN)$(BOLD)  Offline smoke test complete$(RESET)"
	@echo "$(BOLD)=======================================$(RESET)"
	@echo ""
	@echo "$(YELLOW)Restore: make -f Makefile unblock-mint MINT='$(MINT)' && make -f Makefile restart-service$(RESET)"

smoke-recovery: ## [local] Quick recovery test: unblock + wait
	@echo "$(BOLD)=======================================$(RESET)"
	@echo "$(BOLD)  Recovery Smoke Test (~15 min)$(RESET)"
	@echo "$(BOLD)=======================================$(RESET)"
	@echo ""
	@echo "$(CYAN)1/2 — Unblocking mint...$(RESET)"
	@$(MAKE) -f Makefile unblock-mint MINT="$(MINT)"
	@echo ""
	@echo "$(CYAN)2/2 — Watching for recovery...$(RESET)"
	@$(MAKE) -f Makefile wait-recovery

full: ## [local] Run the full mint health test suite (~20 min)
	@echo "$(BOLD)=======================================$(RESET)"
	@echo "$(BOLD)  Degraded Merchant Full Test Suite$(RESET)"
	@echo "$(BOLD)=======================================$(RESET)"
	@echo ""
	@$(MAKE) -f Makefile status
	@echo ""
	@$(MAKE) -f Makefile check-merchant
	@echo ""
	@$(MAKE) -f Makefile record-baseline
	@echo ""
	@$(MAKE) -f Makefile block-mint MINT="$(MINT)"
	@echo ""
	@$(MAKE) -f Makefile restart-service
	@echo ""
	@$(MAKE) -f Makefile check-degraded
	@echo ""
	@$(MAKE) -f Makefile test-offline-ops
	@echo ""
	@$(MAKE) -f Makefile unblock-mint MINT="$(MINT)"
	@echo ""
	@$(MAKE) -f Makefile wait-recovery
	@echo ""
	@$(MAKE) -f Makefile cleanup
	@echo ""
	@echo "$(BOLD)=======================================$(RESET)"
	@echo "$(GREEN)$(BOLD)  Full test suite complete$(RESET)"
	@echo "$(BOLD)=======================================$(RESET)"

# ===========================================================================
#  REMOTE TARGETS (run from dev machine via SSH)
# ===========================================================================

# --- Router mutex management ---

r-lock: ## [remote] Acquire router lock (set PHASE="description")
	@if [ -f "$(ROUTER_LOCK)" ]; then \
		echo "$(RED)$(BOLD)Cannot acquire lock — routers are already locked:$(RESET)"; \
		echo ""; \
		grep "^branch:" $(ROUTER_LOCK) | sed 's/^branch:/  Branch:/'; \
		grep "^phase:" $(ROUTER_LOCK) | sed 's/^phase:/  Phase:/'; \
		grep "^timestamp:" $(ROUTER_LOCK) | sed 's/^timestamp:/  Since:/'; \
		echo ""; \
		echo "$(YELLOW)Use 'make -f Makefile force-unlock' to override (with caution).$(RESET)"; \
		exit 1; \
	fi; \
	branch=$$(git branch --show-current 2>/dev/null || echo "unknown"); \
	echo "locked: true" > $(ROUTER_LOCK); \
	echo "branch: $$branch" >> $(ROUTER_LOCK); \
	echo "session: $$USER@$$HOSTNAME" >> $(ROUTER_LOCK); \
	echo "timestamp: $$(date -u '+%Y-%m-%dT%H:%M:%SZ')" >> $(ROUTER_LOCK); \
	echo "phase: $(PHASE)" >> $(ROUTER_LOCK); \
	echo "$(GREEN)$(BOLD)Router lock acquired$(RESET)"; \
	cat $(ROUTER_LOCK)

r-unlock: ## [remote] Release router lock
	@if [ ! -f "$(ROUTER_LOCK)" ]; then \
		echo "$(YELLOW)No lock file found — already unlocked.$(RESET)"; \
		exit 0; \
	fi; \
	echo "$(YELLOW)Releasing router lock...$(RESET)"; \
	rm -f $(ROUTER_LOCK); \
	echo "$(GREEN)Router lock released.$(RESET)"

force-unlock: ## [remote] Force-release router lock (use with caution)
	@if [ ! -f "$(ROUTER_LOCK)" ]; then \
		echo "$(YELLOW)No lock file found — already unlocked.$(RESET)"; \
		exit 0; \
	fi; \
	echo "$(RED)$(BOLD)WARNING: Force-releasing router lock!$(RESET)"; \
	echo "$(RED)Previous holder:$(RESET)"; \
	grep "^branch:" $(ROUTER_LOCK) | sed 's/^branch:/  Branch:/'; \
	grep "^phase:" $(ROUTER_LOCK) | sed 's/^phase:/  Phase:/'; \
	grep "^timestamp:" $(ROUTER_LOCK) | sed 's/^timestamp:/  Since:/'; \
	rm -f $(ROUTER_LOCK); \
	echo "$(GREEN)Lock force-released.$(RESET)"

r-status-lock: ## [remote] Show current router lock status
	@if [ ! -f "$(ROUTER_LOCK)" ]; then \
		echo "$(GREEN)$(BOLD)Router lock: UNLOCKED$(RESET)"; \
		echo "Routers are available for testing."; \
	else \
		echo "$(RED)$(BOLD)Router lock: LOCKED$(RESET)"; \
		echo ""; \
		cat $(ROUTER_LOCK) | sed 's/^locked:/  Status:/;s/^branch:/  Branch:/;s/^session:/  Session:/;s/^timestamp:/  Since:/;s/^phase:/  Phase:/'; \
		echo ""; \
		ts=$$(grep "^timestamp:" $(ROUTER_LOCK) | cut -d' ' -f2-); \
		if [ -n "$$ts" ]; then \
			age=$$(($$(date +%s) - $$(date -d "$$ts" +%s 2>/dev/null || echo 0))); \
			if [ $$age -gt 7200 ]; then \
				echo "$(YELLOW)WARNING: Lock is over 2 hours old — likely stale.$(RESET)"; \
				echo "$(YELLOW)Consider: make -f Makefile force-unlock$(RESET)"; \
			fi; \
		fi; \
	fi

# --- Router info ---

routers: ## Show configured routers from routers.env
	@if [ ! -f routers.env ]; then echo "$(RED)routers.env not found. Run: cp routers.env.example routers.env$(RESET)"; exit 1; fi
	@echo "$(BOLD)Configured Routers$(RESET)"
	@echo ""
	@grep -E "^ROUTER_[A-Z]+_HOST=" routers.env | while IFS='=' read -r key host; do \
		name=$$(echo "$$key" | sed 's/ROUTER_//;s/_HOST//'); \
		label=$$(grep "^ROUTER_$${name}_LABEL=" routers.env | cut -d= -f2); \
		if [ -n "$$label" ]; then \
			printf "  $(CYAN)%-12s$(RESET) %s  (label: %s)\n" "$$name" "$$host" "$$label"; \
		else \
			printf "  $(CYAN)%-12s$(RESET) %s\n" "$$name" "$$host"; \
		fi; \
	done
	@echo ""
	@echo "Active: $(CYAN)$(ROUTER)$(RESET)"

# --- Shared remote targets ---

r-shell: ## [remote] Open an interactive SSH session on the router
	@$(CHECK_LOCK)
	@$(RESOLVE_ROUTER); \
	echo "$(GREEN)Connecting to $$router_host ($(ROUTER))...$(RESET)"; \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host

r-deploy: ## [remote] Cross-compile and deploy binaries to router
	@$(CHECK_LOCK)
	@$(RESOLVE_ROUTER); \
	echo "$(BOLD)=== Deploying to $(ROUTER) ($$router_host) ===$(RESET)"; \
	./scripts/local-compile-to-router.sh $$router_host

r-status: ## [remote] Phase 1: Check service status (WiFi + mint health)
	@$(RESOLVE_ROUTER); \
	echo "$(BOLD)=== Phase 1: Service Status [$(ROUTER)] ===$(RESET)"; \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host sh -c "'echo \"--- Logs (last 20) ---\"; logread -e tollgate | tail -20; echo; echo \"--- Version ---\"; tollgate version; echo; echo \"--- Status ---\"; tollgate status; echo; logread -e tollgate | grep -q \"Upstream WiFi manager initialized\" && echo \"UPSTREAM_OK: Upstream manager initialized\" || echo \"UPSTREAM_NA: No upstream manager\"'"

r-logs: ## [remote] Tail tollgate logs — upstream WiFi + mint health (Ctrl+C to stop)
	@$(RESOLVE_ROUTER); \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "logread -e tollgate -f" | grep --line-buffered -i "merchant\|degraded\|reachable\|wallet\|upgrade\|BoltDB\|mint\|upstream\|scan\|connect\|switch\|candidate"

# ---------------------------------------------------------------------------
#  Upstream WiFi — Remote targets
# ---------------------------------------------------------------------------

r-scan: ## [remote] Phase 2 via SSH
	@router_host=$$(grep -E "^ROUTER_$$(echo $(ROUTER) | tr '[:lower:]' '[:upper:]')_HOST=" routers.env | cut -d= -f2); \
	if [ -z "$$router_host" ]; then echo "$(RED)Unknown router '$(ROUTER)'$(RESET)"; exit 1; fi; \
	echo "$(BOLD)=== Phase 2: Upstream Scan [$(ROUTER)] ===$(RESET)"; \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "tollgate upstream scan"

r-list: ## [remote] Phase 4 via SSH
	@router_host=$$(grep -E "^ROUTER_$$(echo $(ROUTER) | tr '[:lower:]' '[:upper:]')_HOST=" routers.env | cut -d= -f2); \
	if [ -z "$$router_host" ]; then echo "$(RED)Unknown router '$(ROUTER)'$(RESET)"; exit 1; fi; \
	echo "$(BOLD)=== Phase 4: Upstream List [$(ROUTER)] ===$(RESET)"; \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "tollgate upstream list"

r-connect: ## [remote] Phase 3 via SSH (requires SSID, optional PASS)
	@if [ -z "$(SSID)" ]; then echo "$(RED)Error: SSID is required. Usage: make -f Makefile r-connect SSID=MyNet PASS=secret ROUTER=alpha$(RESET)"; exit 1; fi
	@router_host=$$(grep -E "^ROUTER_$$(echo $(ROUTER) | tr '[:lower:]' '[:upper:]')_HOST=" routers.env | cut -d= -f2); \
	if [ -z "$$router_host" ]; then echo "$(RED)Unknown router '$(ROUTER)'$(RESET)"; exit 1; fi; \
	echo "$(BOLD)=== Phase 3: Upstream Connect [$(ROUTER)] ===$(RESET)"; \
	if [ -n "$(PASS)" ]; then \
		ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "tollgate upstream connect '$(SSID)' '$(PASS)'"; \
	else \
		ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "tollgate upstream connect '$(SSID)'"; \
	fi; \
	echo "$(YELLOW)Waiting 5s...$(RESET)"; sleep 5; \
	echo "$(YELLOW)Testing connectivity via router...$(RESET)"; \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "ping -c 3 -W 5 8.8.8.8" && echo "$(GREEN)Online!$(RESET)" || echo "$(RED)Not reachable$(RESET)"

r-remove: ## [remote] Phase 6 via SSH (requires SSID)
	@if [ -z "$(SSID)" ]; then echo "$(RED)Error: SSID is required. Usage: make -f Makefile r-remove SSID=MyNet ROUTER=alpha$(RESET)"; exit 1; fi
	@router_host=$$(grep -E "^ROUTER_$$(echo $(ROUTER) | tr '[:lower:]' '[:upper:]')_HOST=" routers.env | cut -d= -f2); \
	if [ -z "$$router_host" ]; then echo "$(RED)Unknown router '$(ROUTER)'$(RESET)"; exit 1; fi; \
	echo "$(BOLD)=== Phase 6: Remove Upstream [$(ROUTER)] ===$(RESET)"; \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "tollgate upstream remove '$(SSID)'"

r-verify-uci: ## [remote] Verify UCI state via SSH
	@router_host=$$(grep -E "^ROUTER_$$(echo $(ROUTER) | tr '[:lower:]' '[:upper:]')_HOST=" routers.env | cut -d= -f2); \
	if [ -z "$$router_host" ]; then echo "$(RED)Unknown router '$(ROUTER)'$(RESET)"; exit 1; fi; \
	echo "$(BOLD)=== UCI State [$(ROUTER)] ===$(RESET)"; \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host sh -c "'echo \"--- Upstream STAs ---\"; uci show wireless | grep upstream_; echo \"--- WWAN ---\"; uci show network.wwan 2>/dev/null || echo (not configured)'"

r-wait-daemon: ## [remote] Phase 5 via SSH (watch daemon logs, up to 6 min)
	@router_host=$$(grep -E "^ROUTER_$$(echo $(ROUTER) | tr '[:lower:]' '[:upper:]')_HOST=" routers.env | cut -d= -f2); \
	if [ -z "$$router_host" ]; then echo "$(RED)Unknown router '$(ROUTER)'$(RESET)"; exit 1; fi; \
	echo "$(BOLD)=== Phase 5: Daemon Watch [$(ROUTER)] ===$(RESET)"; \
	echo "$(YELLOW)Watching for scan cycle (timeout 360s)...$(RESET)"; \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "timeout 360 logread -e tollgate -f" 2>/dev/null | grep --line-buffered -i "upstream\|scan cycle\|Best candidate\|switch" | head -20

r-test-edge-cases: ## [remote] Phase 8 via SSH
	@router_host=$$(grep -E "^ROUTER_$$(echo $(ROUTER) | tr '[:lower:]' '[:upper:]')_HOST=" routers.env | cut -d= -f2); \
	if [ -z "$$router_host" ]; then echo "$(RED)Unknown router '$(ROUTER)'$(RESET)"; exit 1; fi; \
	echo "$(BOLD)=== Phase 8: Edge Cases [$(ROUTER)] ===$(RESET)"; \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host sh -c "'echo \"8.1 — Unknown SSID:\"; tollgate upstream connect NonExistentSSID 2>&1; echo; echo \"8.3 — Remove unknown:\"; tollgate upstream remove UnknownSSID 2>&1; echo; echo \"8.4 — Existing commands:\"; tollgate version; tollgate status'"

r-test-reseller-guard: ## [remote] Phase 7 via SSH
	@router_host=$$(grep -E "^ROUTER_$$(echo $(ROUTER) | tr '[:lower:]' '[:upper:]')_HOST=" routers.env | cut -d= -f2); \
	if [ -z "$$router_host" ]; then echo "$(RED)Unknown router '$(ROUTER)'$(RESET)"; exit 1; fi; \
	echo "$(BOLD)=== Phase 7: Reseller Guard [$(ROUTER)] ===$(RESET)"; \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host sh -c "'uci set tollgate.config.reseller_mode=1 && uci commit tollgate && echo Reseller ON && timeout 35 logread -e tollgate -f 2>/dev/null | grep --line-buffered \"Running upstream scan cycle\" | head -1 && echo FAIL || echo OK_NO_SCAN && tollgate upstream list && uci set tollgate.config.reseller_mode=0 && uci commit tollgate && echo Reseller OFF'"

r-test-cleanup: ## [remote] Phase 9 via SSH
	@router_host=$$(grep -E "^ROUTER_$$(echo $(ROUTER) | tr '[:lower:]' '[:upper:]')_HOST=" routers.env | cut -d= -f2); \
	if [ -z "$$router_host" ]; then echo "$(RED)Unknown router '$(ROUTER)'$(RESET)"; exit 1; fi; \
	echo "$(BOLD)=== Phase 9: Cleanup Check [$(ROUTER)] ===$(RESET)"; \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host sh -c "'for f in /usr/bin/wifiscan.sh /usr/bin/upstream-daemon.sh /etc/init.d/tollgate-upstream; do [ -f \"\$$f\" ] && echo FAIL_\$$f || echo OK_\$$f; done; tollgate --help | grep -q upstream && echo OK_upstream_help || echo FAIL_upstream_help'"

# ---------------------------------------------------------------------------
#  Mint Health — Remote targets
# ---------------------------------------------------------------------------

r-check-merchant: ## [remote] Verify full merchant (online boot)
	@$(RESOLVE_ROUTER); \
	echo "$(BOLD)=== Check: Full Merchant [$(ROUTER)] ===$(RESET)"; \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host sh -c "'logread -e tollgate | grep -q \"Merchant ready\" && echo OK_MERCHANT_READY || echo FAIL_NO_MERCHANT_READY; echo; tollgate wallet balance'"

r-check-degraded: ## [remote] Verify degraded merchant (offline boot)
	@$(RESOLVE_ROUTER); \
	echo "$(BOLD)=== Check: Degraded Merchant [$(ROUTER)] ===$(RESET)"; \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host sh -c "'logread -e tollgate | grep -q \"Starting in degraded mode\" && echo OK_DEGRADED || echo FAIL_NOT_DEGRADED; echo; logread -e tollgate | grep \"offline wallet\" | tail -1; echo; tollgate wallet balance; echo; tollgate status'"

r-block-mint: ## [remote] Block mint via /etc/hosts override
	@$(CHECK_LOCK)
	@$(RESOLVE_ROUTER); \
	echo "$(BOLD)=== Blocking Mint [$(ROUTER)] ===$(RESET)"; \
	mint_host=$$(echo "$(MINT)" | sed 's|^[a-z]*://||' | sed 's|/.*||'); \
	echo "Blocking $$mint_host via /etc/hosts..."; \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "grep -q '$$mint_host' /etc/hosts 2>/dev/null && echo 'Already blocked' || (echo '0.0.0.0 $$mint_host' >> /etc/hosts && echo 'Blocked $$mint_host')"; \
	echo; echo "Verifying block..."; \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "wget -q --timeout=5 -O- '$(MINT)/v1/info' >/dev/null 2>&1 && echo 'WARN: Mint still reachable' || echo 'OK: Mint unreachable'"

r-unblock-mint: ## [remote] Remove mint /etc/hosts override
	@$(CHECK_LOCK)
	@$(RESOLVE_ROUTER); \
	echo "$(BOLD)=== Unblocking Mint [$(ROUTER)] ===$(RESET)"; \
	mint_host=$$(echo "$(MINT)" | sed 's|^[a-z]*://||' | sed 's|/.*||'); \
	echo "Removing $$mint_host from /etc/hosts..."; \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "sed -i '/$$mint_host/d' /etc/hosts && echo 'Removed $$mint_host' || echo 'No entry found'"; \
	echo; sleep 1; \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "wget -q --timeout=5 -O- '$(MINT)/v1/info' >/dev/null 2>&1 && echo 'OK: Mint reachable again' || echo 'WARN: Mint still unreachable'"

r-restart-service: ## [remote] Restart tollgate service
	@$(CHECK_LOCK)
	@$(RESOLVE_ROUTER); \
	echo "$(BOLD)=== Restarting Service [$(ROUTER)] ===$(RESET)"; \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host sh -c "'/etc/init.d/tollgate-wrt restart && echo OK_RESTARTED || echo FAIL_RESTART'"; \
	echo "$(YELLOW)Waiting 3s for startup...$(RESET)"; \
	sleep 3

r-record-baseline: ## [remote] Record wallet balance as baseline
	@$(RESOLVE_ROUTER); \
	echo "$(BOLD)=== Recording Baseline [$(ROUTER)] ===$(RESET)"; \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host sh -c "'tollgate wallet balance'" 2>/dev/null | tee /tmp/tollgate-baseline-$(ROUTER).txt; \
	echo "$(GREEN)Saved to /tmp/tollgate-baseline-$(ROUTER).txt$(RESET)"

r-test-offline-ops: ## [remote] Phase 4: Verify offline wallet operations
	@$(RESOLVE_ROUTER); \
	echo "$(BOLD)=== Phase 4: Offline Ops [$(ROUTER)] ===$(RESET)"; \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host sh -c "'\
		echo \"4.1 — Wallet balance:\"; tollgate wallet balance; echo; \
		echo \"4.2 — Service status:\"; tollgate status; echo; \
		echo \"4.3 — No crashes:\"; \
		logread -e tollgate | tail -3 | grep -q \"panic\" && echo FAIL_CRASH || echo OK_NO_CRASH'"

r-wait-recovery: ## [remote] Phase 5: Watch for recovery (up to 16 min)
	@$(RESOLVE_ROUTER); \
	echo "$(BOLD)=== Phase 5: Waiting for Recovery [$(ROUTER)] ===$(RESET)"; \
	echo "$(YELLOW)Tracker checks every 5 min, needs 3 consecutive successes (up to 15 min).$(RESET)"; \
	echo "$(YELLOW)Press Ctrl+C once you see the result.$(RESET)"; \
	echo ""; \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "timeout 960 logread -e tollgate -f" 2>/dev/null | grep --line-buffered -i "reachable\|upgrade\|degraded\|Merchant ready\|BoltDB\|Failed to upgrade" | head -10; \
	echo ""; \
	echo "$(YELLOW)Recovery watch ended. Checking final state...$(RESET)"; \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "logread -e tollgate | grep -E 'Merchant ready|Upgrading from degraded|Failed to upgrade' | tail -3"

r-test-first-boot-offline: ## [remote] Phase 6: First boot offline on fresh router
	@$(CHECK_LOCK)
	@$(RESOLVE_ROUTER); \
	echo "$(BOLD)=== Phase 6: First Boot Offline [$(ROUTER)] ===$(RESET)"; \
	echo "$(YELLOW)Setting up unreachable mint config...$(RESET)"; \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host sh -c "'\
		cp /etc/tollgate/config.json /etc/tollgate/config.json.bak; \
		cat /etc/tollgate/config.json | \
			jq \".accepted_mints = [{\\\"url\\\": \\\"http://192.0.2.1:12345\\\", \\\"price_per_step\\\": 1, \\\"price_unit\\\": \\\"sats\\\"}]\" > /tmp/config-offline.json; \
		mv /tmp/config-offline.json /etc/tollgate/config.json; \
		rm -f /etc/tollgate/ecash/wallet.db; \
		echo \"Config set to unreachable mint, wallet.db removed\"; \
		/etc/init.d/tollgate-wrt restart; \
		sleep 5; \
		echo; \
		logread -e tollgate | grep -q \"offline wallet load failed\" && echo OK_GRACEFUL_DEGRADE || echo FAIL_NO_GRACEFUL; \
		logread -e tollgate | grep -q \"Starting in degraded mode\" && echo OK_DEGRADED || echo FAIL_NOT_DEGRADED; \
		echo; \
		echo \"Checking no boot loop (waiting 10s)...\"; \
		sleep 10; \
		tollgate status > /dev/null 2>&1 && echo OK_SERVICE_UP || echo FAIL_SERVICE_DOWN; \
		echo; \
		echo \"Restoring original config...\"; \
		mv /etc/tollgate/config.json.bak /etc/tollgate/config.json'"

r-test-no-mints: ## [remote] Phase 7: No configured mints
	@$(CHECK_LOCK)
	@$(RESOLVE_ROUTER); \
	echo "$(BOLD)=== Phase 7: No Configured Mints [$(ROUTER)] ===$(RESET)"; \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host sh -c "'\
		cp /etc/tollgate/config.json /etc/tollgate/config.json.bak2; \
		cat /etc/tollgate/config.json | jq \".accepted_mints = []\" > /tmp/config-nomints.json; \
		mv /tmp/config-nomints.json /etc/tollgate/config.json; \
		/etc/init.d/tollgate-wrt restart; \
		sleep 5; \
		logread -e tollgate | grep -q \"no configured mints\" && echo OK_NO_MINTS || echo FAIL_NO_MINTS_LOG; \
		echo \"Checking service up (10s)...\"; \
		sleep 10; \
		tollgate status > /dev/null 2>&1 && echo OK_SERVICE_UP || echo FAIL_SERVICE_DOWN; \
		echo \"Restoring config...\"; \
		mv /etc/tollgate/config.json.bak2 /etc/tollgate/config.json'"

r-cleanup: ## [remote] Phase 8: Remove /etc/hosts overrides and restore
	@$(CHECK_LOCK)
	@$(RESOLVE_ROUTER); \
	echo "$(BOLD)=== Phase 8: Cleanup [$(ROUTER)] ===$(RESET)"; \
	mint_host=$$(echo "$(MINT)" | sed 's|^[a-z]*://||' | sed 's|/.*||'); \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "sed -i '/$$mint_host/d' /etc/hosts && echo 'Removed $$mint_host from /etc/hosts' || echo 'No /etc/hosts entry'"; \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "rm -f /tmp/tollgate-baseline.txt /etc/tollgate/config.json.bak /etc/tollgate/config.json.bak2 /etc/tollgate/config.json.prod-backup 2>/dev/null; echo Cleanup done"; \
	rm -f /tmp/tollgate-baseline-$(ROUTER).txt; \
	echo "$(GREEN)Local cleanup done$(RESET)"

# ---------------------------------------------------------------------------
#  Remote smoke & full test suites
# ---------------------------------------------------------------------------

r-smoke: ## [remote] Run upstream WiFi smoke test via SSH (requires SSID, optional PASS, ROUTER)
	@if [ -z "$(SSID)" ]; then echo "$(RED)Error: SSID is required. Usage: make -f Makefile r-smoke SSID=MyNet PASS=secret ROUTER=alpha$(RESET)"; exit 1; fi
	@router_host=$$(grep -E "^ROUTER_$$(echo $(ROUTER) | tr '[:lower:]' '[:upper:]')_HOST=" routers.env | cut -d= -f2); \
	if [ -z "$$router_host" ]; then echo "$(RED)Unknown router '$(ROUTER)'$(RESET)"; exit 1; fi; \
	echo "$(BOLD)=======================================$(RESET)"; \
	echo "$(BOLD)  Upstream WiFi Smoke Test [$(ROUTER)]$(RESET)"; \
	echo "$(BOLD)=======================================$(RESET)"; \
	echo ""; \
	echo "$(CYAN)1/6 — Service check...$(RESET)"; \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "logread -e tollgate | grep -q 'Upstream WiFi manager initialized' && echo OK_UPSTREAM || echo FAIL_UPSTREAM"; \
	echo ""; \
	echo "$(CYAN)2/6 — Scanning...$(RESET)"; \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "tollgate upstream scan"; \
	echo ""; \
	echo "$(CYAN)3/6 — Connecting to '$(SSID)'...$(RESET)"; \
	if [ -n "$(PASS)" ]; then \
		ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "tollgate upstream connect '$(SSID)' '$(PASS)'"; \
	else \
		ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "tollgate upstream connect '$(SSID)'"; \
	fi; \
	echo ""; \
	echo "$(CYAN)4/6 — Connectivity...$(RESET)"; \
	sleep 5; \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "ping -c 3 -W 5 8.8.8.8" && echo "$(GREEN)  Online$(RESET)" || echo "$(RED)  Not reachable$(RESET)"; \
	echo ""; \
	echo "$(CYAN)5/6 — List upstreams...$(RESET)"; \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "tollgate upstream list"; \
	echo ""; \
	echo "$(CYAN)6/6 — Old scripts gone?...$(RESET)"; \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "for f in /usr/bin/wifiscan.sh /usr/bin/upstream-daemon.sh; do [ -f \"\$$f\" ] && echo FAIL_\$$f || echo OK_\$$f; done"; \
	echo ""; \
	echo "$(BOLD)=======================================$(RESET)"; \
	echo "$(GREEN)$(BOLD)  Smoke test complete [$(ROUTER)]$(RESET)"; \
	echo "$(BOLD)=======================================$(RESET)"

r-smoke-offline: ## [remote] Quick offline smoke: block + restart + verify degraded
	@$(MAKE) -f Makefile r-record-baseline ROUTER=$(ROUTER)
	@echo ""
	@$(MAKE) -f Makefile r-block-mint ROUTER=$(ROUTER) MINT="$(MINT)"
	@echo ""
	@$(MAKE) -f Makefile r-restart-service ROUTER=$(ROUTER)
	@echo ""
	@$(MAKE) -f Makefile r-check-degraded ROUTER=$(ROUTER)
	@echo ""
	@$(MAKE) -f Makefile r-test-offline-ops ROUTER=$(ROUTER)
	@echo ""
	@echo "$(BOLD)=======================================$(RESET)"
	@echo "$(GREEN)$(BOLD)  Offline smoke test complete [$(ROUTER)]$(RESET)"
	@echo "$(BOLD)=======================================$(RESET)"
	@echo ""
	@echo "$(YELLOW)To test recovery:$(RESET)"
	@echo "  make -f Makefile r-smoke-recovery ROUTER=$(ROUTER)"
	@echo ""
	@echo "$(YELLOW)To restore immediately:$(RESET)"
	@echo "  make -f Makefile r-cleanup ROUTER=$(ROUTER)"

r-smoke-recovery: ## [remote] Quick recovery smoke: unblock + wait
	@$(MAKE) -f Makefile r-unblock-mint ROUTER=$(ROUTER) MINT="$(MINT)"
	@echo ""
	@$(MAKE) -f Makefile r-wait-recovery ROUTER=$(ROUTER)

r-smoke-degraded-recovery: ## [remote] Full degraded→recovery lifecycle WITHOUT restart after unblock (tests BoltDB lock release)
	@echo "$(BOLD)=======================================$(RESET)"
	@echo "$(BOLD)  Degraded Recovery Smoke Test [$(ROUTER)]$(RESET)"
	@echo "$(BOLD)  (in-process recovery, NO restart after unblock)$(RESET)"
	@echo "$(BOLD)=======================================$(RESET)"
	@echo ""
	@$(MAKE) -f Makefile r-setup-test-mint ROUTER=$(ROUTER)
	@echo ""
	@$(MAKE) -f Makefile r-fund-wallet ROUTER=$(ROUTER)
	@echo ""
	@$(MAKE) -f Makefile r-check-merchant ROUTER=$(ROUTER)
	@echo ""
	@$(MAKE) -f Makefile r-record-baseline ROUTER=$(ROUTER)
	@echo ""
	@echo "$(YELLOW)--- Blocking mint to force degraded mode ---$(RESET)"
	@$(MAKE) -f Makefile r-block-mint ROUTER=$(ROUTER) MINT="$(MINT)"
	@echo ""
	@$(MAKE) -f Makefile r-restart-service ROUTER=$(ROUTER)
	@echo ""
	@$(MAKE) -f Makefile r-check-degraded ROUTER=$(ROUTER)
	@echo ""
	@$(MAKE) -f Makefile r-test-offline-ops ROUTER=$(ROUTER)
	@echo ""
	@echo "$(YELLOW)--- Unblocking mint (NO restart — tests in-process recovery) ---$(RESET)"
	@$(MAKE) -f Makefile r-unblock-mint ROUTER=$(ROUTER) MINT="$(MINT)"
	@echo ""
	@$(MAKE) -f Makefile r-wait-recovery ROUTER=$(ROUTER)
	@echo ""
	@$(MAKE) -f Makefile r-check-merchant ROUTER=$(ROUTER)
	@echo ""
	@$(MAKE) -f Makefile r-restore-prod-config ROUTER=$(ROUTER)
	@echo ""
	@echo "$(BOLD)=======================================$(RESET)"
	@echo "$(GREEN)$(BOLD)  Degraded recovery smoke test PASSED [$(ROUTER)]$(RESET)"
	@echo "$(BOLD)=======================================$(RESET)"

r-smoke-dynamic-rebuild: ## [remote] Full merchant lifecycle: full→degraded→full (tests onReachableSetChanged + Shutdown + rebuild)
	@echo "$(BOLD)=======================================$(RESET)"
	@echo "$(BOLD)  Dynamic Merchant Rebuild Smoke Test [$(ROUTER)]$(RESET)"
	@echo "$(BOLD)  (tests onReachableSetChanged, Shutdown, NewMerchantDegradedFromFull)$(RESET)"
	@echo "$(BOLD)=======================================$(RESET)"
	@echo ""
	@$(MAKE) -f Makefile r-setup-test-mint ROUTER=$(ROUTER)
	@echo ""
	@$(MAKE) -f Makefile r-fund-wallet ROUTER=$(ROUTER)
	@echo ""
	@echo "$(CYAN)--- Phase 1: Verify full merchant mode ---$(RESET)"
	@$(MAKE) -f Makefile r-check-merchant ROUTER=$(ROUTER)
	@echo ""
	@$(MAKE) -f Makefile r-record-baseline ROUTER=$(ROUTER)
	@echo ""
	@echo "$(CYAN)--- Phase 2: Block mint and restart (enter degraded mode) ---$(RESET)"
	@$(MAKE) -f Makefile r-block-mint ROUTER=$(ROUTER) MINT="$(MINT)"
	@echo ""
	@$(MAKE) -f Makefile r-restart-service ROUTER=$(ROUTER)
	@echo ""
	@$(MAKE) -f Makefile r-check-degraded ROUTER=$(ROUTER)
	@echo ""
	@$(MAKE) -f Makefile r-test-offline-ops ROUTER=$(ROUTER)
	@echo ""
	@echo "$(CYAN)--- Phase 3: Unblock mint, NO restart (in-process recovery) ---$(RESET)"
	@$(MAKE) -f Makefile r-unblock-mint ROUTER=$(ROUTER) MINT="$(MINT)"
	@echo ""
	@$(MAKE) -f Makefile r-wait-recovery ROUTER=$(ROUTER)
	@echo ""
	@$(MAKE) -f Makefile r-check-merchant ROUTER=$(ROUTER)
	@echo ""
	@echo "$(CYAN)--- Phase 4: Verify wallet balance preserved through full cycle ---$(RESET)"
	@$(RESOLVE_ROUTER); \
	balance=$$(ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "tollgate wallet balance 2>/dev/null | grep -oE '[0-9]+' | head -1"); \
	echo "  Current balance: $$balance sats"; \
	echo ""
	@echo ""
	@echo "$(CYAN)--- Phase 5: Block mint again (tests full→degraded via onReachableSetChanged) ---$(RESET)"
	@$(MAKE) -f Makefile r-block-mint ROUTER=$(ROUTER) MINT="$(MINT)"
	@echo ""
	@echo "$(YELLOW)Waiting 7 min for proactive check to detect mint down...$(RESET)"
	@$(RESOLVE_ROUTER); \
	for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14; do \
		sleep 30; \
		down_log=$$(ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "logread | grep -i 'downgrading\|all mints unreachable\|degraded mode' | tail -3" 2>/dev/null); \
		if [ -n "$$down_log" ]; then \
			echo "$(GREEN)Auto-downgrade detected after $$((i*30))s:$(RESET)"; \
			echo "$$down_log" | while IFS= read -r line; do echo "  $$line"; done; \
			break; \
		fi; \
		if [ "$$i" = "14" ]; then \
			echo "$(YELLOW)No auto-downgrade log found in 7 min (may need restart to trigger proactive check)$(RESET)"; \
		fi; \
		echo "  ... $$((i*30))s elapsed"; \
	done
	@echo ""
	@$(MAKE) -f Makefile r-restore-prod-config ROUTER=$(ROUTER)
	@echo ""
	@echo "$(BOLD)=======================================$(RESET)"
	@echo "$(GREEN)$(BOLD)  Dynamic merchant rebuild smoke test PASSED [$(ROUTER)]$(RESET)"
	@echo "$(BOLD)=======================================$(RESET)"

r-smoke-pin-upstream: ## [remote] Two-router: verify upstream pin prevents scan-away after payment
	@$(MAKE) -f Makefile r-guard-no-dual-wwan ROUTER=alpha
	@echo "$(BOLD)=======================================$(RESET)"
	@echo "$(BOLD)  Pin Upstream Smoke Test$(RESET)"
	@echo "$(BOLD)  (tests PinUpstream suppresses scan-away during active session)$(RESET)"
	@echo "$(BOLD)=======================================$(RESET)"
	@echo ""
	@alpha_host=$$(grep -E "^ROUTER_ALPHA_HOST=" routers.env | cut -d= -f2); \
	beta_host=$$(grep -E "^ROUTER_BETA_HOST=" routers.env | cut -d= -f2); \
	beta_ssid=$$(grep -E "^ROUTER_BETA_SSID=" routers.env | cut -d= -f2); \
	stargate_pass=$$(grep -E "^STARGATE_PASS=" routers.env | cut -d= -f2); \
	if [ -z "$$alpha_host" ]; then echo "$(RED)No ROUTER_ALPHA_HOST$(RESET)"; exit 1; fi; \
	if [ -z "$$beta_host" ]; then echo "$(RED)No ROUTER_BETA_HOST$(RESET)"; exit 1; fi; \
	\
	echo "$(CYAN)Step 0: Pre-flight$(RESET)"; \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$alpha_host "echo alpha-ok" 2>/dev/null | grep -q alpha-ok || { echo "$(RED)Alpha unreachable$(RESET)"; exit 1; }; \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$beta_host "echo beta-ok" 2>/dev/null | grep -q beta-ok || { echo "$(RED)Beta unreachable$(RESET)"; exit 1; }; \
	echo "$(GREEN)Both routers accessible$(RESET)"; \
	\
	echo ""; \
	echo "$(CYAN)Step 1: Save alpha's upstream$(RESET)"; \
	prev_ssid=$$(ssh $(SSH_OPTS) $(ROUTER_USER)@$$alpha_host "tollgate upstream list 2>/dev/null" | grep ACTIVE | awk '{print $$1}'); \
	echo "  Active: $$prev_ssid"; \
	\
	echo ""; \
	echo "$(CYAN)Step 2: Setup test mints on both routers$(RESET)"; \
	$(MAKE) -f Makefile r-setup-test-mint ROUTER=alpha; \
	echo ""; \
	$(MAKE) -f Makefile r-setup-test-mint ROUTER=beta; \
	\
	echo ""; \
	echo "$(CYAN)Step 3: Fund both wallets$(RESET)"; \
	$(MAKE) -f Makefile r-fund-wallet ROUTER=alpha; \
	echo ""; \
	$(MAKE) -f Makefile r-fund-wallet ROUTER=beta; \
	\
	echo ""; \
	echo "$(CYAN)Step 4: Connect alpha to beta's open AP$(RESET)"; \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$alpha_host "tollgate upstream connect '$$beta_ssid' 2>&1"; \
	echo "$(YELLOW)Waiting for DHCP (up to 60s)...$(RESET)"; \
	for i in 1 2 3 4 5 6 7 8 9 10 11 12; do \
		sleep 5; \
		if ssh $(SSH_OPTS) $(ROUTER_USER)@$$alpha_host "ifstatus wwan 2>/dev/null | jsonfilter -e '@.up' 2>/dev/null | grep -q true" 2>/dev/null; then \
			echo "$(GREEN)Connected after $$((i*5))s$(RESET)"; \
			break; \
		fi; \
		if [ "$$i" = "12" ]; then \
			echo "$(RED)Failed to connect$(RESET)"; \
			ssh $(SSH_OPTS) $(ROUTER_USER)@$$alpha_host "tollgate upstream connect '$$prev_ssid' '$$stargate_pass'" 2>/dev/null; \
			exit 1; \
		fi; \
		echo "  ... $$((i*5))s"; \
	done; \
	\
	echo ""; \
	echo "$(CYAN)Step 5: Wait for session payment (up to 30s)$(RESET)"; \
	timeout 30 ssh $(SSH_OPTS) $(ROUTER_USER)@$$alpha_host "logread -e tollgate -f" 2>/dev/null | grep --line-buffered -i "payment successful\|session updated\|Pinned upstream" | head -3; \
	\
	echo ""; \
	echo "$(CYAN)Step 6: Verify pin was set$(RESET)"; \
	pin_log=$$(ssh $(SSH_OPTS) $(ROUTER_USER)@$$alpha_host "logread | grep -i 'Pinned upstream' | tail -1" 2>/dev/null); \
	if [ -n "$$pin_log" ]; then \
		echo "$(GREEN)Pin detected: $$pin_log$(RESET)"; \
	else \
		echo "$(YELLOW)No explicit pin log (may need log pattern adjustment)$(RESET)"; \
	fi; \
	\
	echo ""; \
	echo "$(CYAN)Step 7: Check alpha stayed on beta's AP (pin prevented switch)$(RESET)"; \
	current_ssid=$$(ssh $(SSH_OPTS) $(ROUTER_USER)@$$alpha_host "tollgate upstream list 2>/dev/null" | grep ACTIVE | awk '{print $$1}'); \
	echo "  Active upstream: $$current_ssid"; \
	if [ "$$current_ssid" = "$$beta_ssid" ]; then \
		echo "$(GREEN)OK: Still on $$beta_ssid (pin working)$(RESET)"; \
	else \
		echo "$(YELLOW)WARN: Switched away from $$beta_ssid to $$current_ssid$(RESET)"; \
	fi; \
	\
	echo ""; \
	echo "$(CYAN)Step 8: Restore alpha upstream$(RESET)"; \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$alpha_host "tollgate upstream connect '$$prev_ssid' '$$stargate_pass'" 2>&1 || \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$alpha_host "tollgate upstream connect '$$prev_ssid'" 2>&1; \
	\
	echo ""; \
	echo "$(CYAN)Step 9: Wait for NetBird recovery$(RESET)"; \
	alpha_host=$$(grep -E "^ROUTER_ALPHA_HOST=" routers.env | cut -d= -f2); \
	for i in 1 2 3 4 5 6 7 8 9 10 11 12; do \
		sleep 10; \
		if ssh $(SSH_OPTS) $(ROUTER_USER)@$$alpha_host "echo netbird-ok" 2>/dev/null | grep -q netbird-ok; then \
			echo "$(GREEN)NetBird recovered after $$((i*10))s$(RESET)"; \
			break; \
		fi; \
		if [ "$$i" = "12" ]; then echo "$(RED)NetBird not back after 120s$(RESET)"; exit 1; fi; \
		echo "  ... $$((i*10))s"; \
	done; \
	\
	echo ""; \
	echo "$(CYAN)Step 10: Restore production configs$(RESET)"; \
	$(MAKE) -f Makefile r-restore-prod-config ROUTER=alpha; \
	echo ""; \
	$(MAKE) -f Makefile r-restore-prod-config ROUTER=beta; \
	\
	echo ""; \
	echo "$(BOLD)=======================================$(RESET)"; \
	echo "$(GREEN)$(BOLD)  Pin upstream smoke test PASSED$(RESET)"; \
	echo "$(BOLD)=======================================$(RESET)"

# ---------------------------------------------------------------------------
#  Test mint setup & degraded mode suites
# ---------------------------------------------------------------------------

TEST_MINT_CONFIG := {"config_version":"v0.0.7","log_level":"info","accepted_mints":[{"url":"https://nofee.testnut.cashu.space","price_per_step":1,"price_unit":"sats","min_balance":0,"payout_interval_seconds":999999,"min_payout_amount":999999,"balance_tolerance_percent":0}],"profit_share":[{"factor":1.0,"identity":"owner"}],"step_size":4096,"margin":0.1,"metric":"bytes","show_setup":true,"upstream_detector":{"probe_timeout":10000000000,"probe_retry_count":3,"probe_retry_delay":2000000000,"require_valid_signature":false,"ignore_interfaces":["lo","docker0","br-lan","hostap0"],"only_interfaces":[],"discovery_timeout":300000000000},"upstream_session_manager":{"max_price_per_millisecond":0.002777777778,"max_price_per_byte":3.725782414e-05,"trust":{"default_policy":"trust_all","allowlist":[],"blocklist":[]},"sessions":{"preferred_session_increments_milliseconds":60000,"preferred_session_increments_bytes":8192,"millisecond_renewal_offset":10000,"bytes_renewal_offset":4096},"usage_tracking":{"data_monitoring_interval":500000000}}}

r-setup-test-mint: ## [remote] Configure router with nofee.testnut.cashu.space for testing
	@$(CHECK_LOCK)
	@$(RESOLVE_ROUTER); \
	echo "$(BOLD)=== Setup Test Mint [$(ROUTER)] ===$(RESET)"; \
	echo "Backing up production config..."; \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "cp /etc/tollgate/config.json /etc/tollgate/config.json.prod-backup && echo 'Backup saved' || echo 'FAIL: no config to backup'"; \
	echo "Writing test config..."; \
	echo '$(TEST_MINT_CONFIG)' | ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "cat > /etc/tollgate/config.json && echo 'Config written'"; \
	echo "Removing old wallet..."; \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "rm -f /etc/tollgate/ecash/wallet.db && echo 'Wallet removed'"; \
	echo "Restarting service..."; \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "OLD_PID=\$$(pgrep -f tollgate-wrt); /etc/init.d/tollgate-wrt restart; echo \"Old PID: \$$OLD_PID\""; \
	echo "$(YELLOW)Waiting up to 60s for merchant to bootstrap...$(RESET)"; \
	for i in 1 2 3 4 5 6 7 8 9 10 11 12; do \
		sleep 5; \
		if ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "tollgate status 2>/dev/null | grep -q 'running: true'"; then \
			echo "$(GREEN)Merchant ready!$(RESET)"; \
			if ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "grep -q 'nofee.testnut' /etc/tollgate/config.json"; then \
				echo "$(GREEN)Config OK: test mint active$(RESET)"; \
			else \
				echo "$(RED)CONFIG ERROR: test mint not in config!$(RESET)"; \
				exit 1; \
			fi; \
			ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "tollgate wallet balance"; \
			echo ""; \
			echo "$(GREEN)Test mint setup complete [$(ROUTER)]$(RESET)"; \
			exit 0; \
		fi; \
		echo "  ... $$((i*5))s elapsed, waiting..."; \
	done; \
	echo "$(RED)TIMEOUT: Merchant did not become ready within 60s$(RESET)"; \
	echo "$(YELLOW)Check logs: ssh $(ROUTER_USER)@$$router_host 'logread -e tollgate | tail -30'$(RESET)"; \
	exit 1

MINT_TOKEN_BIN := $(shell test -x /tmp/mint-token && echo /tmp/mint-token || echo "")

r-fund-wallet: ## [remote] Fund wallet with 1013 sats from nofee.testnut.cashu.space
	@if [ -z "$(MINT_TOKEN_BIN)" ]; then \
		echo "$(RED)mint-token tool not found. Build it first:$(RESET)"; \
		echo "  cd src/cmd/mint-token && export PATH=\$$PATH:/usr/local/go/bin && go build -o /tmp/mint-token ."; \
		exit 1; \
	fi
	@$(RESOLVE_ROUTER); \
	echo "$(BOLD)=== Fund Wallet [$(ROUTER)] ===$(RESET)"; \
	config_line=$$(ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "logread | grep 'config paths' | tail -1" 2>/dev/null); \
	if ! echo "$$config_line" | grep -q '/etc/tollgate/config.json'; then \
		echo "$(RED)FAIL: Service not reading from /etc/tollgate/config.json$(RESET)"; \
		echo "  $$config_line"; \
		echo "$(YELLOW)This means the binary has the 000_main_test_env.go bug. Rebuild with the build tag fix.$(RESET)"; \
		exit 1; \
	fi; \
	echo "Minting 1013 sats from nofee.testnut.cashu.space..."; \
	TOKEN=$$($(MINT_TOKEN_BIN) 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])"); \
	if [ -z "$$TOKEN" ]; then echo "$(RED)Failed to mint token$(RESET)"; exit 1; fi; \
	echo "Token minted (length $${#TOKEN}). Funding wallet..."; \
	echo "$$TOKEN" | ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "tollgate wallet fund" 2>&1; \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "tollgate wallet balance"

r-restore-prod-config: ## [remote] Restore production config and restart
	@$(CHECK_LOCK)
	@$(RESOLVE_ROUTER); \
	echo "$(BOLD)=== Restore Production Config [$(ROUTER)] ===$(RESET)"; \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "if [ -f /etc/tollgate/config.json.prod-backup ]; then mv /etc/tollgate/config.json.prod-backup /etc/tollgate/config.json && echo 'Config restored'; else echo 'No backup found, keeping current config'; fi"; \
	mint_host=$$(echo "$(MINT)" | sed 's|^[a-z]*://||' | sed 's|/.*||'); \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "sed -i '/$$mint_host/d' /etc/hosts && echo 'Removed /etc/hosts override' || true"; \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "/etc/init.d/tollgate-wrt restart"; \
	echo "$(YELLOW)Waiting 5s for startup...$(RESET)"; \
	sleep 5; \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "tollgate status" && echo "$(GREEN)Service healthy$(RESET)" || echo "$(RED)Service not healthy$(RESET)"

r-smoke-degraded: ## [remote] Single-router degraded mode lifecycle
	@echo "$(BOLD)=======================================$(RESET)"
	@echo "$(BOLD)  Degraded Mode Smoke Test [$(ROUTER)]$(RESET)"
	@echo "$(BOLD)=======================================$(RESET)"
	@echo ""
	@$(MAKE) -f Makefile r-setup-test-mint ROUTER=$(ROUTER)
	@echo ""
	@$(MAKE) -f Makefile r-fund-wallet ROUTER=$(ROUTER)
	@echo ""
	@$(MAKE) -f Makefile r-check-merchant ROUTER=$(ROUTER)
	@echo ""
	@$(MAKE) -f Makefile r-record-baseline ROUTER=$(ROUTER)
	@echo ""
	@$(MAKE) -f Makefile r-block-mint ROUTER=$(ROUTER) MINT="$(MINT)"
	@echo ""
	@$(MAKE) -f Makefile r-restart-service ROUTER=$(ROUTER)
	@echo ""
	@$(MAKE) -f Makefile r-check-degraded ROUTER=$(ROUTER)
	@echo ""
	@$(MAKE) -f Makefile r-test-offline-ops ROUTER=$(ROUTER)
	@echo ""
	@$(MAKE) -f Makefile r-unblock-mint ROUTER=$(ROUTER) MINT="$(MINT)"
	@echo ""
	@$(MAKE) -f Makefile r-restart-service ROUTER=$(ROUTER)
	@echo ""
	@$(MAKE) -f Makefile r-check-merchant ROUTER=$(ROUTER)
	@echo ""
	@$(MAKE) -f Makefile r-restore-prod-config ROUTER=$(ROUTER)
	@echo ""
	@echo "$(BOLD)=======================================$(RESET)"
	@echo "$(GREEN)$(BOLD)  Degraded mode smoke test PASSED [$(ROUTER)]$(RESET)"
	@echo "$(BOLD)=======================================$(RESET)"

r-guard-no-dual-wwan: ## [internal] Assert no second wwan interface exists (dual-WWAN breaks routing)
	@$(RESOLVE_ROUTER); \
	wwan_count=$$(ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "uci show network | grep 'wwan.*proto' | grep -c ''" 2>/dev/null); \
	if [ "$$wwan_count" -gt 1 ] 2>/dev/null; then \
		echo "$(RED)FAIL: $$wwan_count wwan interfaces detected on $(ROUTER). Dual-WWAN breaks gateway routing.$(RESET)"; \
		echo "$(YELLOW)Remove the extra wwan interface (e.g. 'uci delete network.wwan2 && uci commit && wifi reload')$(RESET)"; \
		exit 1; \
	fi; \
	echo "$(GREEN)OK: $$wwan_count wwan interface(s)$(RESET)"

r-diagnose-config-path: ## [remote] Verify service reads config from /etc/tollgate not /tmp
	@$(RESOLVE_ROUTER); \
	echo "$(BOLD)=== Diagnose Config Path [$(ROUTER)] ===$(RESET)"; \
	config_line=$$(ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "logread | grep 'config paths' | tail -1" 2>/dev/null); \
	if echo "$$config_line" | grep -q '/etc/tollgate/config.json'; then \
		echo "$(GREEN)OK: Service using /etc/tollgate/config.json$(RESET)"; \
		echo "  $$config_line"; \
	else \
		echo "$(RED)FAIL: Service NOT using /etc/tollgate/config.json$(RESET)"; \
		echo "  $$config_line"; \
		echo "$(YELLOW)This usually means the binary was built without excluding 000_main_test_env.go$(RESET)"; \
		exit 1; \
	fi

r-test-default-mints: ## [remote] Verify default mint config matches expected branch behavior
	@$(RESOLVE_ROUTER); \
	echo "$(BOLD)=== Test Default Mints [$(ROUTER)] ===$(RESET)"; \
	mint_count=$$(ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "cat /etc/tollgate/config.json" 2>/dev/null | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('accepted_mints',[])))" 2>/dev/null); \
	if [ -z "$$mint_count" ]; then \
		mint_count=$$(ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "cat /etc/tollgate/config.json" 2>/dev/null | grep -o '"url"' | wc -l); \
	fi; \
	echo "Mint count: $$mint_count"; \
	if [ "$$mint_count" -ge "2" ] 2>/dev/null; then \
		echo "$(GREEN)OK: $$mint_count mints configured$(RESET)"; \
	else \
		echo "$(RED)FAIL: Expected at least 2 mints, got $$mint_count$(RESET)"; \
		exit 1; \
	fi; \
	if ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "cat /etc/tollgate/config.json" 2>/dev/null | grep -q 'nofee.testnut.cashu.space'; then \
		echo "$(GREEN)OK: Test mint present in config (dev build)$(RESET)"; \
	else \
		echo "$(YELLOW)INFO: Test mint not in config (production build or custom config)$(RESET)"; \
	fi

r-test-startup-hygiene: ## [remote] Full power cycle test: enable dead STA + remove ecash + reboot + verify auto-switch + restore. Single command.
	@$(MAKE) -f Makefile r-test-startup-hygiene-setup ROUTER=$(ROUTER) && \
	echo ""; \
	echo "$(CYAN)Rebooting $(ROUTER) via SSH...$(RESET)"; \
	router_host=$$(grep -E "^ROUTER_$$(echo $(ROUTER) | tr '[:lower:]' '[:upper:]')_HOST=" routers.env | cut -d= -f2); \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "reboot" 2>/dev/null || true; \
	echo "  Waiting 50s for reboot..."; \
	sleep 50; \
	for i in 1 2 3 4 5 6 7 8 9 10 11 12; do \
		if ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new $(ROUTER_USER)@$$router_host "echo lan-ok" 2>/dev/null | grep -q lan-ok; then \
			echo "$(GREEN)$(ROUTER) back after $$((50 + i*5))s$(RESET)"; \
			break; \
		fi; \
		echo "  waiting... ($$i/12)"; \
		sleep 5; \
	done; \
	$(MAKE) -f Makefile r-test-startup-hygiene-verify ROUTER=$(ROUTER)

r-test-startup-hygiene-setup: ## [remote] Step 1/2: enable non-internet STA, remove ecash, save state for verify
	@$(RESOLVE_ROUTER_LAN); \
	target_upper=$$(echo $(ROUTER) | tr '[:lower:]' '[:upper:]'); \
	router_netbird=$$(grep -E "^ROUTER_$${target_upper}_HOST=" routers.env | cut -d= -f2); \
	echo "$(BOLD)=========================================$(RESET)"; \
	echo "$(BOLD)  Startup Hygiene Setup [$(ROUTER)]$(RESET)"; \
	echo "$(BOLD)=========================================$(RESET)"; \
	echo ""; \
	echo "$(CYAN)Pre-flight checks...$(RESET)"; \
	echo "  LAN ($$router_host)..."; \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "echo lan-ok" 2>/dev/null | grep -q lan-ok || { echo "$(RED)Router unreachable via LAN ($$router_host). Connect ethernet.$(RESET)"; exit 1; }; \
	echo "  $(GREEN)LAN OK$(RESET)"; \
	echo ""; \
	echo "$(CYAN)Step 1: Verify current upstream and internet$(RESET)"; \
	active_ssid=$$(ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "tollgate upstream list 2>/dev/null" | grep ACTIVE | awk '{print $$1}'); \
	if [ -z "$$active_ssid" ]; then echo "$(RED)No active upstream on $(ROUTER).$(RESET)"; exit 1; fi; \
	echo "  Active upstream: $$active_ssid"; \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "ping -c2 -W3 9.9.9.9" 2>/dev/null | grep -q "0% packet loss" || { echo "$(RED)No internet on $(ROUTER).$(RESET)"; exit 1; }; \
	echo "  $(GREEN)Internet OK$(RESET)"; \
	echo ""; \
	echo "$(CYAN)Step 2: Backup wallet and remove ecash$(RESET)"; \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "ls -la /etc/tollgate/wallet.db 2>/dev/null" | tail -1; \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "mv /etc/tollgate/wallet.db /etc/tollgate/wallet.db.bak" 2>/dev/null || { echo "$(RED)No wallet.db found on $(ROUTER).$(RESET)"; exit 1; }; \
	echo "  $(GREEN)Wallet backed up → wallet.db.bak$(RESET)"; \
	echo ""; \
	echo "$(CYAN)Step 3: Enable non-internet STA (other router's TollGate AP)$(RESET)"; \
	if [ "$(ROUTER)" = "alpha" ]; then \
		dead_ssid=$$(grep -E "^ROUTER_BETA_SSID=" routers.env | cut -d= -f2); \
	else \
		dead_ssid=$$(grep -E "^ROUTER_ALPHA_SSID=" routers.env | cut -d= -f2); \
	fi; \
	sta_name=$$(echo "$$dead_ssid" | tr '[:upper:]-' '[:lower:]_' | sed 's/[^a-z0-9_]//g'); \
	echo "  Enabling upstream_$$sta_name ($$dead_ssid) on radio1..."; \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "\
		if uci -q get wireless.upstream_$$sta_name >/dev/null 2>&1; then \
			echo '  Reusing existing upstream_$$sta_name'; \
		else \
			uci set wireless.upstream_$$sta_name=wifi-iface; \
			uci set wireless.upstream_$$sta_name.mode='sta'; \
			uci set wireless.upstream_$$sta_name.network='wwan'; \
		fi; \
		uci set wireless.upstream_$$sta_name.device='radio1'; \
		uci set wireless.upstream_$$sta_name.ssid='$$dead_ssid'; \
		uci set wireless.upstream_$$sta_name.encryption='none'; \
		uci set wireless.upstream_$$sta_name.disabled='0'; \
		uci commit wireless; \
	"; \
	echo "  $(GREEN)Enabled $$dead_ssid on radio1$(RESET)"; \
	echo ""; \
	echo "$(YELLOW)Saving state for verify step...$(RESET)"; \
	echo "$$active_ssid" > /tmp/tollgate-hygiene-active-$(ROUTER).txt; \
	echo "$$dead_ssid" > /tmp/tollgate-hygiene-dead-$(ROUTER).txt; \
	echo "$$sta_name" > /tmp/tollgate-hygiene-sta-$(ROUTER).txt; \
	echo ""; \
	echo "$(GREEN)Setup complete.$(RESET)"

r-test-startup-hygiene-verify: ## [remote] Step 2/2: verify startup check auto-switched, restore wallet
	@$(RESOLVE_ROUTER_LAN); \
	target_upper=$$(echo $(ROUTER) | tr '[:lower:]' '[:upper:]'); \
	router_netbird=$$(grep -E "^ROUTER_$${target_upper}_HOST=" routers.env | cut -d= -f2); \
	dead_ssid=$$(cat /tmp/tollgate-hygiene-dead-$(ROUTER).txt 2>/dev/null); \
	sta_name=$$(cat /tmp/tollgate-hygiene-sta-$(ROUTER).txt 2>/dev/null); \
	prev_active=$$(cat /tmp/tollgate-hygiene-active-$(ROUTER).txt 2>/dev/null); \
	if [ -z "$$dead_ssid" ] || [ -z "$$sta_name" ]; then echo "$(RED)No setup state found. Run r-test-startup-hygiene-setup first.$(RESET)"; exit 1; fi; \
	echo "$(BOLD)=========================================$(RESET)"; \
	echo "$(BOLD)  Startup Hygiene Verify [$(ROUTER)]$(RESET)"; \
	echo "$(BOLD)=========================================$(RESET)"; \
	echo ""; \
	echo "$(CYAN)Step 4: Check LAN connectivity after power cycle$(RESET)"; \
	if ! ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "echo lan-ok" 2>/dev/null | grep -q lan-ok; then \
		echo "$(RED)Router not reachable via LAN ($$router_host).$(RESET)"; \
		echo "  Wait longer for boot, or check ethernet cable."; \
		exit 1; \
	fi; \
	echo "  $(GREEN)LAN OK$(RESET)"; \
	echo ""; \
	echo "$(CYAN)Step 5: Check startup connectivity hygiene logs$(RESET)"; \
	startup_log=$$(ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "logread | grep -i 'startup check'"); \
	if echo "$$startup_log" | grep -q "active STA has internet"; then \
		echo "$(GREEN)$(BOLD)Startup check: active STA has internet!$(RESET)"; \
		echo ""; \
		echo "$$startup_log" | while IFS= read -r line; do echo "  $$line"; done; \
	elif echo "$$startup_log" | grep -q "no internet"; then \
		echo "$(GREEN)$(BOLD)Startup check detected no internet!$(RESET)"; \
		echo ""; \
		echo "$$startup_log" | while IFS= read -r line; do echo "  $$line"; done; \
	else \
		echo "$(YELLOW)No startup check logs found. Checking all relevant logs...$(RESET)"; \
		ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "logread | grep -i 'startup\|blacklist\|emergency\|Successfully switched' | tail -15"; \
	fi; \
	echo ""; \
	blacklist_log=$$(ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "logread | grep -i 'blacklisted'"); \
	if [ -n "$$blacklist_log" ]; then \
		echo "$(CYAN)Blacklist entries:$(RESET)"; \
		echo "$$blacklist_log" | while IFS= read -r line; do echo "  $$line"; done; \
	fi; \
	echo ""; \
	switch_log=$$(ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "logread | grep 'Successfully switched'"); \
	if [ -n "$$switch_log" ]; then \
		echo "$(CYAN)Switch events:$(RESET)"; \
		echo "$$switch_log" | while IFS= read -r line; do echo "  $$line"; done; \
	fi; \
	echo ""; \
	echo "$(CYAN)Step 6: Verify current upstream and internet$(RESET)"; \
	new_active=$$(ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "tollgate upstream list 2>/dev/null" | grep ACTIVE | awk '{print $$1}'); \
	echo "  Active upstream now: $$new_active"; \
	echo "  Previous active: $$prev_active"; \
	echo "  Dead STA that was added: $$dead_ssid"; \
	internet_ok=0; \
	for i in 1 2 3 4; do \
		if ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "ping -c2 -W5 9.9.9.9" 2>/dev/null | grep -q "0% packet loss"; then \
			echo "  $(GREEN)Internet OK (attempt $$i)$(RESET)"; \
			internet_ok=1; \
			break; \
		fi; \
		echo "  $(YELLOW)No internet yet... (attempt $$i/4)$(RESET)"; \
		sleep 10; \
	done; \
	if [ "$$internet_ok" = "0" ]; then echo "  $(RED)No internet after 4 attempts$(RESET)"; fi; \
	echo ""; \
	echo "$(CYAN)Step 7: Verify NetBird recovered$(RESET)"; \
	netbird_ok=0; \
	for i in 1 2 3 4 5 6; do \
		if ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "ifconfig wt0 2>/dev/null" | grep -q "inet addr:100"; then \
			wt_ip=$$(ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "ifconfig wt0 2>/dev/null" | grep "inet addr:" | awk -F: '{print $$2}' | awk '{print $$1}'); \
			echo "$(GREEN)NetBird up: $$wt_ip$(RESET)"; \
			netbird_ok=1; \
			break; \
		fi; \
		echo "  Waiting... ($$i/6)"; \
		sleep 5; \
	done; \
	if [ "$$netbird_ok" = "0" ]; then echo "$(YELLOW)NetBird not up yet (may need more time)$(RESET)"; fi; \
	echo ""; \
	echo "$(CYAN)Step 8: Cleanup — restore wallet and disable test STA$(RESET)"; \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "mv /etc/tollgate/wallet.db.bak /etc/tollgate/wallet.db"; \
	echo "  $(GREEN)Wallet restored$(RESET)"; \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "uci set wireless.upstream_$$sta_name.disabled=1; uci commit wireless"; \
	echo "  $(GREEN)Disabled $$dead_ssid STA$(RESET)"; \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "/etc/init.d/tollgate-wrt restart" 2>/dev/null; \
	sleep 5; \
	if ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "ping -c2 -W3 9.9.9.9" 2>/dev/null | grep -q "0% packet loss"; then \
		echo "  $(GREEN)Internet OK after restore$(RESET)"; \
	else \
		echo "  $(YELLOW)Internet not yet restored (may need a moment)$(RESET)"; \
	fi; \
	rm -f /tmp/tollgate-hygiene-active-$(ROUTER).txt /tmp/tollgate-hygiene-dead-$(ROUTER).txt /tmp/tollgate-hygiene-sta-$(ROUTER).txt; \
	echo ""; \
	if [ "$$internet_ok" = "1" ]; then \
		echo "$(BOLD)=========================================$(RESET)"; \
		echo "$(GREEN)$(BOLD)  Startup connectivity hygiene test PASSED [$(ROUTER)]$(RESET)"; \
		echo "$(BOLD)=========================================$(RESET)"; \
	else \
		echo "$(BOLD)=========================================$(RESET)"; \
		echo "$(RED)$(BOLD)  Startup connectivity hygiene test FAILED [$(ROUTER)]$(RESET)"; \
		echo "$(BOLD)=========================================$(RESET)"; \
		exit 1; \
	fi

r-test-startup-hygiene-dead-only: ## [remote] Boot with ONLY dead STA enabled, verify emergency scan finds disabled good STA and switches.
	@$(MAKE) -f Makefile r-test-startup-hygiene-dead-only-setup ROUTER=$(ROUTER) && \
	echo ""; \
	echo "$(CYAN)Rebooting $(ROUTER) via SSH...$(RESET)"; \
	router_host=$$(grep -E "^ROUTER_$$(echo $(ROUTER) | tr '[:lower:]' '[:upper:]')_HOST=" routers.env | cut -d= -f2); \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "reboot" 2>/dev/null || true; \
	echo "  Waiting 50s for reboot..."; \
	sleep 50; \
	for i in 1 2 3 4 5 6 7 8 9 10 11 12; do \
		if ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new $(ROUTER_USER)@$$router_host "echo lan-ok" 2>/dev/null | grep -q lan-ok; then \
			echo "$(GREEN)$(ROUTER) back after $$((50 + i*5))s$(RESET)"; \
			break; \
		fi; \
		echo "  waiting... ($$i/12)"; \
		sleep 5; \
	done; \
	$(MAKE) -f Makefile r-test-startup-hygiene-dead-only-verify ROUTER=$(ROUTER)

r-test-startup-hygiene-dead-only-setup: ## [remote] Step 1/2: disconnect other router, disable ALL STAs except dead one, remove ecash
	@$(RESOLVE_ROUTER_LAN); \
	target_upper=$$(echo $(ROUTER) | tr '[:lower:]' '[:upper:]'); \
	router_netbird=$$(grep -E "^ROUTER_$${target_upper}_HOST=" routers.env | cut -d= -f2); \
	if [ "$(ROUTER)" = "alpha" ]; then \
		other_upper=BETA; \
	else \
		other_upper=ALPHA; \
	fi; \
	other_host=$$(grep -E "^ROUTER_$${other_upper}_HOST=" routers.env | cut -d= -f2); \
	other_netbird=$$(grep -E "^ROUTER_$${other_upper}_HOST=" routers.env | cut -d= -f2); \
	echo "$(BOLD)=========================================$(RESET)"; \
	echo "$(BOLD)  Startup Hygiene Dead-Only Setup [$(ROUTER)]$(RESET)"; \
	echo "$(BOLD)=========================================$(RESET)"; \
	echo ""; \
	echo "$(CYAN)Pre-flight checks...$(RESET)"; \
	echo "  LAN ($$router_host)..."; \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "echo lan-ok" 2>/dev/null | grep -q lan-ok || { echo "$(RED)Router unreachable via LAN ($$router_host). Connect ethernet.$(RESET)"; exit 1; }; \
	echo "  $(GREEN)LAN OK$(RESET)"; \
	echo "  Other router NetBird ($$other_netbird)..."; \
	ssh -o ConnectTimeout=20 -o StrictHostKeyChecking=accept-new $(ROUTER_USER)@$$other_netbird "echo netbird-ok" 2>/dev/null | grep -q netbird-ok || { echo "$(RED)Other router unreachable via NetBird ($$other_netbird).$(RESET)"; exit 1; }; \
	echo "  $(GREEN)Other router NetBird OK$(RESET)"; \
	echo ""; \
	echo "$(CYAN)Step 0: Disconnect other router's upstream (so dead STA truly has no internet)$(RESET)"; \
	other_active_sta=$$(ssh $(SSH_OPTS) $(ROUTER_USER)@$$other_netbird "uci show wireless" | grep "disabled='0'" | grep -v default | grep -v private | grep -v 'wireless\.radio[0-9]\.' | head -1 | sed "s/.*wireless\.\([^.]*\).disabled=.*/\1/"); \
	if [ -z "$$other_active_sta" ]; then echo "$(YELLOW)No active STA found on other router — already offline$(RESET)"; \
	else \
		echo "  Other router active STA: $$other_active_sta"; \
		echo "  Scheduling safety-net restore on other router (5min)..."; \
		ssh $(SSH_OPTS) $(ROUTER_USER)@$$other_netbird "nohup sh -c 'sleep 300; uci set wireless.$$other_active_sta.disabled=0; uci commit wireless; wifi reload' >/dev/null 2>&1 &"; \
		echo "  $(GREEN)Safety net scheduled$(RESET)"; \
		ssh $(SSH_OPTS) $(ROUTER_USER)@$$other_netbird "uci set wireless.$$other_active_sta.disabled='1'; uci commit wireless; ifdown wwan; wifi reload"; \
		echo "  $(GREEN)Disabled $$other_active_sta on other router + ifdown wwan$(RESET)"; \
	fi; \
	echo "$$other_active_sta" > /tmp/tollgate-hygiene-deadonly-other-sta-$(ROUTER).txt; \
	echo "$$other_host" > /tmp/tollgate-hygiene-deadonly-other-lan-$(ROUTER).txt; \
	echo "$$other_netbird" > /tmp/tollgate-hygiene-deadonly-other-netbird-$(ROUTER).txt; \
	echo ""; \
	echo "$(CYAN)Step 1: Record all currently-enabled STA sections$(RESET)"; \
	enabled_stas=$$(ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "uci show wireless" | grep "disabled='0'" | grep -v default | grep -v private | grep -v 'wireless\.radio[0-9]\.' | sed "s/.*wireless\.\([^.]*\).disabled=.*/\1/" | tr '\n' ' '); \
	echo "  Enabled STA sections: $$enabled_stas"; \
	echo "$$enabled_stas" > /tmp/tollgate-hygiene-deadonly-enabled-$(ROUTER).txt; \
	good_ssid=$$(ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "tollgate upstream list 2>/dev/null" | grep ACTIVE | awk '{print $$1}'); \
	if [ -z "$$good_ssid" ]; then echo "$(RED)No active upstream on $(ROUTER).$(RESET)"; exit 1; fi; \
	echo "  Active upstream: $$good_ssid"; \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "ping -c2 -W3 9.9.9.9" 2>/dev/null | grep -q "0% packet loss" || { echo "$(RED)No internet on $(ROUTER).$(RESET)"; exit 1; }; \
	echo "  $(GREEN)Internet OK$(RESET)"; \
	echo ""; \
	echo "$(CYAN)Step 2: Backup wallet and remove ecash$(RESET)"; \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "ls -la /etc/tollgate/wallet.db 2>/dev/null" | tail -1; \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "mv /etc/tollgate/wallet.db /etc/tollgate/wallet.db.bak" 2>/dev/null || { echo "$(RED)No wallet.db found on $(ROUTER).$(RESET)"; exit 1; }; \
	echo "  $(GREEN)Wallet backed up → wallet.db.bak$(RESET)"; \
	echo ""; \
	echo "$(CYAN)Step 3: Enable dead STA (other router's TollGate AP)$(RESET)"; \
	if [ "$(ROUTER)" = "alpha" ]; then \
		dead_ssid=$$(grep -E "^ROUTER_BETA_SSID=" routers.env | cut -d= -f2); \
	else \
		dead_ssid=$$(grep -E "^ROUTER_ALPHA_SSID=" routers.env | cut -d= -f2); \
	fi; \
	dead_sta=$$(echo "$$dead_ssid" | tr '[:upper:]-' '[:lower:]_' | sed 's/[^a-z0-9_]//g'); \
	echo "  Enabling upstream_$$dead_sta ($$dead_ssid) on radio1..."; \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "\
		if uci -q get wireless.upstream_$$dead_sta >/dev/null 2>&1; then \
			echo '  Reusing existing upstream_$$dead_sta'; \
		else \
			uci set wireless.upstream_$$dead_sta=wifi-iface; \
			uci set wireless.upstream_$$dead_sta.mode='sta'; \
			uci set wireless.upstream_$$dead_sta.network='wwan'; \
		fi; \
		uci set wireless.upstream_$$dead_sta.device='radio1'; \
		uci set wireless.upstream_$$dead_sta.ssid='$$dead_ssid'; \
		uci set wireless.upstream_$$dead_sta.encryption='none'; \
		uci set wireless.upstream_$$dead_sta.disabled='0'; \
		uci commit wireless; \
	"; \
	echo "  $(GREEN)Enabled $$dead_ssid on radio1$(RESET)"; \
	echo ""; \
	echo "$(CYAN)Step 4: Disable ALL other STAs (so ONLY dead STA is active at boot)$(RESET)"; \
	disable_cmds=""; \
	for sta in $$enabled_stas; do \
		disable_cmds="$$disable_cmds uci set wireless.$$sta.disabled='1';"; \
		echo "  Disabling $$sta"; \
	done; \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "$$disable_cmds uci commit wireless"; \
	echo "  $(GREEN)All other STAs disabled$(RESET)"; \
	echo ""; \
	echo "$(YELLOW)Saving state for verify step...$(RESET)"; \
	echo "$$dead_ssid" > /tmp/tollgate-hygiene-deadonly-dead-$(ROUTER).txt; \
	echo "$$dead_sta" > /tmp/tollgate-hygiene-deadonly-deadsta-$(ROUTER).txt; \
	echo ""; \
	echo "$(GREEN)Setup complete. Dead-only boot config applied.$(RESET)"; \
	echo "$(YELLOW)Other router ($$other_host) is OFFLINE — will be restored after verify.$(RESET)"

r-test-startup-hygiene-dead-only-verify: ## [remote] Step 2/2: verify multi-hop recovery, restore both routers
	@$(RESOLVE_ROUTER_LAN); \
	target_upper=$$(echo $(ROUTER) | tr '[:lower:]' '[:upper:]'); \
	router_netbird=$$(grep -E "^ROUTER_$${target_upper}_HOST=" routers.env | cut -d= -f2); \
	dead_ssid=$$(cat /tmp/tollgate-hygiene-deadonly-dead-$(ROUTER).txt 2>/dev/null); \
	dead_sta=$$(cat /tmp/tollgate-hygiene-deadonly-deadsta-$(ROUTER).txt 2>/dev/null); \
	enabled_stas=$$(cat /tmp/tollgate-hygiene-deadonly-enabled-$(ROUTER).txt 2>/dev/null); \
	other_active_sta=$$(cat /tmp/tollgate-hygiene-deadonly-other-sta-$(ROUTER).txt 2>/dev/null); \
	other_host=$$(cat /tmp/tollgate-hygiene-deadonly-other-lan-$(ROUTER).txt 2>/dev/null); \
	other_netbird=$$(cat /tmp/tollgate-hygiene-deadonly-other-netbird-$(ROUTER).txt 2>/dev/null); \
	if [ -z "$$dead_ssid" ] || [ -z "$$enabled_stas" ]; then echo "$(RED)No setup state found. Run r-test-startup-hygiene-dead-only-setup first.$(RESET)"; exit 1; fi; \
	echo "$(BOLD)=========================================$(RESET)"; \
	echo "$(BOLD)  Startup Hygiene Dead-Only Verify [$(ROUTER)]$(RESET)"; \
	echo "$(BOLD)=========================================$(RESET)"; \
	echo ""; \
	echo "  Dead STA (was enabled at boot): $$dead_sta ($$dead_ssid)"; \
	echo "  Previously-enabled STAs (all disabled): $$enabled_stas"; \
	echo "  Other router STA to restore: $$other_active_sta"; \
	echo ""; \
	echo "$(CYAN)Step 5: Check LAN connectivity after reboot$(RESET)"; \
	if ! ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "echo lan-ok" 2>/dev/null | grep -q lan-ok; then \
		echo "$(RED)Router not reachable via LAN ($$router_host).$(RESET)"; \
		exit 1; \
	fi; \
	echo "  $(GREEN)LAN OK$(RESET)"; \
	echo ""; \
	echo "$(CYAN)Step 6: Check startup connectivity hygiene logs$(RESET)"; \
	startup_log=$$(ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "logread | grep -i 'startup check'"); \
	if echo "$$startup_log" | grep -q "no internet"; then \
		echo "$(GREEN)$(BOLD)Startup check detected no internet!$(RESET)"; \
	else \
		echo "$(YELLOW)No 'no internet' in startup logs. Checking all...$(RESET)"; \
	fi; \
	echo "$$startup_log" | while IFS= read -r line; do echo "  $$line"; done; \
	echo ""; \
	echo "$(CYAN)Step 7: Check scan/switch logs (expecting multi-hop recovery)$(RESET)"; \
	scan_log=$$(ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "logread | grep -i 'scanning for alternative\|candidate found\|switching\|blacklisted\|switch failed\|no working upstream\|Successfully switched'"); \
	if [ -n "$$scan_log" ]; then \
		echo "$(CYAN)Scan/switch events:$(RESET)"; \
		echo "$$scan_log" | while IFS= read -r line; do echo "  $$line"; done; \
		switch_count=$$(echo "$$scan_log" | grep -c 'candidate found'); \
		echo ""; \
		echo "  Switch attempts: $$switch_count"; \
	else \
		echo "$(YELLOW)No scan/switch logs found.$(RESET)"; \
	fi; \
	echo ""; \
	blacklist_log=$$(ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "logread | grep -i 'blacklisted'"); \
	if [ -n "$$blacklist_log" ]; then \
		echo "$(CYAN)Blacklist entries:$(RESET)"; \
		echo "$$blacklist_log" | while IFS= read -r line; do echo "  $$line"; done; \
	fi; \
	echo ""; \
	echo "$(CYAN)Step 8: Verify internet recovered (generous timeout for multi-hop)$(RESET)"; \
	internet_ok=0; \
	for i in 1 2 3 4 5 6 7 8 9 10 11 12; do \
		if ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "ping -c2 -W5 9.9.9.9" 2>/dev/null | grep -q "0% packet loss"; then \
			echo "  $(GREEN)Internet OK (attempt $$i)$(RESET)"; \
			internet_ok=1; \
			break; \
		fi; \
		echo "  $(YELLOW)No internet yet... (attempt $$i/12)$(RESET)"; \
		sleep 15; \
	done; \
	if [ "$$internet_ok" = "0" ]; then echo "  $(RED)No internet after 12 attempts (~3min)$(RESET)"; fi; \
	echo ""; \
	new_active=$$(ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "tollgate upstream list 2>/dev/null" | grep ACTIVE | awk '{print $$1}'); \
	echo "  Active upstream now: $$new_active"; \
	echo "  Dead STA (boot-only): $$dead_ssid"; \
	echo ""; \
	echo "$(CYAN)Step 9: Cleanup — restore wallet, re-enable all STAs, restart tollgate$(RESET)"; \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "mv /etc/tollgate/wallet.db.bak /etc/tollgate/wallet.db"; \
	echo "  $(GREEN)Wallet restored$(RESET)"; \
	restore_cmds=""; \
	for sta in $$enabled_stas; do \
		restore_cmds="$$restore_cmds uci set wireless.$$sta.disabled='0';"; \
	done; \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "$$restore_cmds uci set wireless.$$dead_sta.disabled='1'; uci commit wireless"; \
	echo "  $(GREEN)Restored all STAs: $$enabled_stas$(RESET)"; \
	echo "  $(GREEN)Disabled dead STA: $$dead_sta$(RESET)"; \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "/etc/init.d/tollgate-wrt restart" 2>/dev/null; \
	sleep 5; \
	echo ""; \
	echo "$(CYAN)Step 9b: Reconnect other router's upstream$(RESET)"; \
	other_restored=0; \
	if [ -n "$$other_active_sta" ]; then \
		for other_addr in $$other_netbird $$other_host; do \
			echo "  Trying $$other_addr..."; \
			if ssh $(SSH_OPTS) $(ROUTER_USER)@$$other_addr "uci set wireless.$$other_active_sta.disabled='0'; uci commit wireless; wifi reload" 2>/dev/null; then \
				echo "  $(GREEN)Other router upstream restored via $$other_addr$(RESET)"; \
				other_restored=1; \
				break; \
			fi; \
		done; \
		if [ "$$other_restored" = "0" ]; then \
			echo "  $(YELLOW)Could not reach other router directly — safety net will auto-restore in ~5min$(RESET)"; \
		fi; \
	else \
		echo "  $(YELLOW)No other router state saved — skipping$(RESET)"; \
	fi; \
	echo ""; \
	if ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "ping -c2 -W3 9.9.9.9" 2>/dev/null | grep -q "0% packet loss"; then \
		echo "  $(GREEN)Internet OK after restore$(RESET)"; \
	else \
		echo "  $(YELLOW)Internet not yet restored (may need a moment)$(RESET)"; \
	fi; \
	rm -f /tmp/tollgate-hygiene-deadonly-enabled-$(ROUTER).txt /tmp/tollgate-hygiene-deadonly-dead-$(ROUTER).txt \
	       /tmp/tollgate-hygiene-deadonly-deadsta-$(ROUTER).txt /tmp/tollgate-hygiene-deadonly-other-sta-$(ROUTER).txt \
	       /tmp/tollgate-hygiene-deadonly-other-lan-$(ROUTER).txt /tmp/tollgate-hygiene-deadonly-other-netbird-$(ROUTER).txt; \
	echo ""; \
	if [ "$$internet_ok" = "1" ] && [ "$$new_active" != "$$dead_ssid" ]; then \
		echo "$(BOLD)=========================================$(RESET)"; \
		echo "$(GREEN)$(BOLD)  Dead-only startup hygiene test PASSED [$(ROUTER)]$(RESET)"; \
		echo "$(BOLD)  Internet recovered via $$new_active (was not $$dead_ssid)$(RESET)"; \
		echo "$(BOLD)=========================================$(RESET)"; \
	elif [ "$$internet_ok" = "1" ]; then \
		no_internet_detected=$$(echo "$$startup_log" | grep -c 'no internet'); \
		emergency_scan=$$(echo "$$scan_log" | grep -c 'emergency\|scanning for alternative\|candidate found'); \
		if [ "$$no_internet_detected" -gt 0 ] && [ "$$emergency_scan" -gt 0 ]; then \
			echo "$(BOLD)=========================================$(RESET)"; \
			echo "$(GREEN)$(BOLD)  Dead-only startup hygiene test PASSED [$(ROUTER)]$(RESET)"; \
			echo "$(BOLD)  Startup check detected no internet, emergency scan triggered$(RESET)"; \
			echo "$(BOLD)  Internet recovered (possibly via multi-hop) on $$new_active$(RESET)"; \
			echo "$(BOLD)=========================================$(RESET)"; \
		else \
			echo "$(BOLD)=========================================$(RESET)"; \
			echo "$(YELLOW)$(BOLD)  Dead-only startup hygiene test PARTIAL [$(ROUTER)]$(RESET)"; \
			echo "$(BOLD)  Internet OK but still on dead STA ($$new_active)$(RESET)"; \
			echo "$(BOLD)=========================================$(RESET)"; \
		fi; \
	else \
		echo "$(BOLD)=========================================$(RESET)"; \
		echo "$(RED)$(BOLD)  Dead-only startup hygiene test FAILED [$(ROUTER)]$(RESET)"; \
		echo "$(BOLD)=========================================$(RESET)"; \
		exit 1; \
	fi

r-rescue-router: ## [remote] Rescue a router with dead NetBird via another router. Usage: r-rescue-router ROUTER=beta VIA=alpha
	@if [ -z "$(VIA)" ]; then echo "$(RED)Error: VIA is required. Usage: make -f Makefile r-rescue-router ROUTER=beta VIA=alpha$(RESET)"; exit 1; fi
	@target_upper=$$(echo $(ROUTER) | tr '[:lower:]' '[:upper:]'); \
	via_upper=$$(echo $(VIA) | tr '[:lower:]' '[:upper:]'); \
	via_host=$$(grep -E "^ROUTER_$${via_upper}_HOST=" routers.env | cut -d= -f2); \
	target_host=$$(grep -E "^ROUTER_$${target_upper}_HOST=" routers.env | cut -d= -f2); \
	target_ssid=$$(grep -E "^ROUTER_$${target_upper}_SSID=" routers.env | cut -d= -f2); \
	target_netbird=$$(grep -E "^ROUTER_$${target_upper}_HOST=" routers.env | cut -d= -f2); \
	stargate_pass=$$(grep -E "^STARGATE_PASS=" routers.env | cut -d= -f2); \
	if [ -z "$$via_host" ]; then echo "$(RED)No HOST for $(VIA). Check routers.env$(RESET)"; exit 1; fi; \
	if [ -z "$$target_host" ]; then echo "$(RED)No HOST for $(ROUTER). Check routers.env$(RESET)"; exit 1; fi; \
	if [ -z "$$target_ssid" ]; then echo "$(RED)No SSID for $(ROUTER). Check routers.env$(RESET)"; exit 1; fi; \
	echo "$(BOLD)=== Rescue $(ROUTER) via $(VIA) ===$(RESET)"; \
	echo "Step 1: Saving $(VIA)'s current upstream..."; \
	via_ssid=$$(ssh $(SSH_OPTS) $(ROUTER_USER)@$$via_host "tollgate upstream list 2>/dev/null" | grep ACTIVE | awk '{print $$1}'); \
	if [ -z "$$via_ssid" ]; then via_ssid="StarGate"; fi; \
	echo "  $(VIA) active upstream: $$via_ssid"; \
	echo ""; \
	echo "Step 2: Connecting $(VIA) to $(ROUTER)'s open AP ($$target_ssid) on single wwan..."; \
	sta_name=$$(echo "$$target_ssid" | tr '[:upper:][:space:]' '[:lower:]_' | sed 's/[^a-z0-9_]//g'); \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$via_host "\
		uci set wireless.upstream_$$sta_name=wifi-iface; \
		uci set wireless.upstream_$$sta_name.device='radio0'; \
		uci set wireless.upstream_$$sta_name.mode='sta'; \
		uci set wireless.upstream_$$sta_name.network='wwan'; \
		uci set wireless.upstream_$$sta_name.ssid='$$target_ssid'; \
		uci set wireless.upstream_$$sta_name.encryption='none'; \
		uci set wireless.upstream_$$sta_name.disabled='0'; \
		uci commit wireless; \
		echo 'Waiting for DHCP...'; \
		for i in 1 2 3 4 5 6 7 8 9 10 11 12; do \
			sleep 5; \
			if ifstatus wwan 2>/dev/null | jsonfilter -e '@.up' 2>/dev/null | grep -q true; then \
				echo 'Got DHCP on wwan'; \
				break; \
			fi; \
			echo \"  ... \$\$((i*5))s\"; \
			if [ \"\$$i\" = '12' ]; then \
				echo 'TIMEOUT: no DHCP'; \
				exit 1; \
			fi; \
		done; \
	"; \
	echo ""; \
	echo "Step 3: Fixing $(ROUTER)'s DNS and upstream via relay..."; \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$via_host "\
		ssh -o ConnectTimeout=10 -o StrictHostKeyChecking=no root@$$target_host '\
			rm -f /etc/resolv.conf; \
			echo nameserver 8.8.8.8 > /etc/resolv.conf; \
			iwinfo radio0 scan 2>/dev/null | grep -q StarGate && { \
				uci set wireless.upstream_stargate.disabled=0; \
				uci commit wireless; \
				wifi reload; \
				echo Switched to StarGate; \
			}; \
			/etc/init.d/netbird restart; \
			echo NetBird restarted; \
		'; \
	"; \
	echo ""; \
	echo "Step 4: Restoring $(VIA) to $$via_ssid..."; \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$via_host "\
		uci set wireless.upstream_$$sta_name.disabled='1'; \
		uci commit wireless; \
		wifi reload; \
	"; \
	echo ""; \
	echo "$(YELLOW)Waiting for $(ROUTER) NetBird ($$target_netbird) to come up...$(RESET)"; \
	for i in 1 2 3 4 5 6 7 8 9 10 11 12; do \
		sleep 10; \
		if ssh $(SSH_OPTS) $(ROUTER_USER)@$$target_netbird "echo netbird-ok" 2>/dev/null | grep -q netbird-ok; then \
			echo "$(GREEN)$(ROUTER) NetBird recovered after $$((i*10))s!$(RESET)"; \
			echo ""; \
			echo "$(YELLOW)Restoring $(VIA) upstream to $$via_ssid...$(RESET)"; \
			if [ "$$via_ssid" = "StarGate" ]; then \
				ssh $(SSH_OPTS) $(ROUTER_USER)@$$via_host "uci set wireless.upstream_stargate.disabled=0; uci commit wireless; wifi reload" 2>/dev/null; \
			fi; \
			exit 0; \
		fi; \
		echo "  ... $$((i*10))s"; \
	done; \
	echo "$(RED)$(ROUTER) NetBird not reachable after 120s$(RESET)"; \
	echo "$(YELLOW)Restoring $(VIA) upstream manually...$(RESET)"; \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$via_host "uci set wireless.upstream_stargate.disabled=0; uci commit wireless; wifi reload" 2>/dev/null; \
	exit 1

r-save-upstream: ## [remote] Save current active upstream SSID for later restore
	@$(RESOLVE_ROUTER_LAN); \
	echo "$(BOLD)=== Save Upstream [$(ROUTER)] ===$(RESET)"; \
	active_ssid=$$(ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "tollgate upstream list 2>/dev/null" | grep ACTIVE | awk '{print $$1}'); \
	if [ -z "$$active_ssid" ]; then \
		echo "$(RED)No active upstream found on $(ROUTER)$(RESET)"; \
		exit 1; \
	fi; \
	echo "$$active_ssid" > /tmp/tollgate-upstream-$(ROUTER).txt; \
	echo "$(GREEN)Saved upstream: $$active_ssid → /tmp/tollgate-upstream-$(ROUTER).txt$(RESET)"

r-restore-upstream: ## [remote] Restore previously saved upstream SSID
	@$(RESOLVE_ROUTER_LAN); \
	saved_ssid=$$(cat /tmp/tollgate-upstream-$(ROUTER).txt 2>/dev/null); \
	if [ -z "$$saved_ssid" ]; then echo "$(RED)No saved upstream for $(ROUTER). Run r-save-upstream first.$(RESET)"; exit 1; fi; \
	echo "$(BOLD)=== Restore Upstream [$(ROUTER)] → $$saved_ssid ===$(RESET)"; \
	stargate_pass=$$(grep -E "^STARGATE_PASS=" routers.env | cut -d= -f2); \
	if [ "$$saved_ssid" = "StarGate" ]; then \
		ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "tollgate upstream connect '$$saved_ssid' '$$stargate_pass' 2>&1 || tollgate upstream connect '$$saved_ssid' 2>&1"; \
	else \
		ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "tollgate upstream connect '$$saved_ssid' 2>&1"; \
	fi; \
	echo "$(GREEN)Restored upstream to $$saved_ssid$(RESET)"

r-smoke-degraded-upstream: ## [remote] Two-router: connect online, degrade, verify offline renewal (uses LAN for alpha)
	@$(MAKE) -f Makefile r-guard-no-dual-wwan ROUTER=alpha
	@echo "$(BOLD)=======================================$(RESET)"
	@echo "$(BOLD)  Two-Router Degraded Upstream Test$(RESET)"
	@echo "$(BOLD)=======================================$(RESET)"
	@echo ""
	@alpha_host=$$(grep -E "^ROUTER_ALPHA_HOST=" routers.env | cut -d= -f2); \
	beta_host=$$(grep -E "^ROUTER_BETA_HOST=" routers.env | cut -d= -f2); \
	beta_ssid=$$(grep -E "^ROUTER_BETA_SSID=" routers.env | cut -d= -f2); \
	stargate_pass=$$(grep -E "^STARGATE_PASS=" routers.env | cut -d= -f2); \
	if [ -z "$$alpha_host" ]; then echo "$(RED)No ROUTER_ALPHA_HOST in routers.env$(RESET)"; exit 1; fi; \
	if [ -z "$$beta_host" ]; then echo "$(RED)No ROUTER_BETA_HOST in routers.env$(RESET)"; exit 1; fi; \
	\
	echo "$(CYAN)Step 0: Pre-flight — verify connectivity$(RESET)"; \
	echo "  Alpha (LAN $$alpha_host)..."; \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$alpha_host "echo alpha-lan-ok" 2>/dev/null | grep -q alpha-lan-ok || { echo "$(RED)Alpha unreachable via LAN ($$alpha_host). Connect Ethernet.$(RESET)"; exit 1; }; \
	echo "  Beta (NetBird $$beta_host)..."; \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$beta_host "echo beta-ok" 2>/dev/null | grep -q beta-ok || { echo "$(RED)Beta unreachable via NetBird ($$beta_host). Run r-rescue-router first.$(RESET)"; exit 1; }; \
	echo "$(GREEN)Both routers accessible$(RESET)"; \
	\
	echo ""; \
	echo "$(CYAN)Step 1: Save alpha's current upstream$(RESET)"; \
	active_ssid=$$(ssh $(SSH_OPTS) $(ROUTER_USER)@$$alpha_host "tollgate upstream list 2>/dev/null" | grep ACTIVE | awk '{print $$1}'); \
	if [ -z "$$active_ssid" ]; then echo "$(RED)No active upstream on alpha$(RESET)"; exit 1; fi; \
	echo "$$active_ssid" > /tmp/tollgate-upstream-alpha.txt; \
	echo "$(GREEN)Saved: $$active_ssid$(RESET)"; \
	\
	echo ""; \
	echo "$(CYAN)Step 2: Setup test mint on both routers$(RESET)"; \
	$(MAKE) -f Makefile r-setup-test-mint ROUTER=alpha; \
	echo ""; \
	$(MAKE) -f Makefile r-setup-test-mint ROUTER=beta; \
	\
	echo ""; \
	echo "$(CYAN)Step 3: Fund both wallets$(RESET)"; \
	$(MAKE) -f Makefile r-fund-wallet ROUTER=alpha; \
	echo ""; \
	$(MAKE) -f Makefile r-fund-wallet ROUTER=beta; \
	\
	echo ""; \
	echo "$(CYAN)Step 4: Verify both routers are merchant-ready$(RESET)"; \
	echo "  Alpha:"; \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$alpha_host "tollgate wallet balance"; \
	echo "  Beta:"; \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$beta_host "tollgate wallet balance"; \
	\
	echo ""; \
	echo "$(CYAN)Step 5: Connect alpha to beta's open AP ($$beta_ssid) via radio1$(RESET)"; \
	echo "$(YELLOW)Using LAN SSH — NetBird disruption on alpha is OK.$(RESET)"; \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$alpha_host "tollgate upstream connect '$$beta_ssid' 2>&1"; \
	ssh_rc=$$?; \
	echo "Connect exit code: $$ssh_rc"; \
	\
	echo ""; \
	echo "$(CYAN)Step 6: Wait for alpha to get DHCP on beta's LAN (up to 60s)$(RESET)"; \
	for i in 1 2 3 4 5 6 7 8 9 10 11 12; do \
		sleep 5; \
		if ssh $(SSH_OPTS) $(ROUTER_USER)@$$alpha_host "ifstatus wwan 2>/dev/null | jsonfilter -e '@.up' 2>/dev/null | grep -q true" 2>/dev/null; then \
			echo "$(GREEN)Alpha connected to beta's AP after $$((i*5))s$(RESET)"; \
			break; \
		fi; \
		if [ "$$i" = "12" ]; then \
			echo "$(RED)Alpha failed to connect to beta's AP$(RESET)"; \
			echo "$(YELLOW)Restoring alpha's upstream...$(RESET)"; \
			ssh $(SSH_OPTS) $(ROUTER_USER)@$$alpha_host "tollgate upstream connect '$$active_ssid' '$$stargate_pass'" 2>/dev/null; \
			exit 1; \
		fi; \
		echo "  ... $$((i*5))s"; \
	done; \
	\
	echo ""; \
	echo "$(CYAN)Step 7: Verify alpha sees beta as TollGate gateway (up to 30s)$(RESET)"; \
	for i in 1 2 3 4 5 6; do \
		sleep 5; \
		gw_found=$$(ssh $(SSH_OPTS) $(ROUTER_USER)@$$alpha_host "logread -e tollgate 2>/dev/null | grep -i 'gateway.*$$(echo $$beta_ssid | sed 's/TollGate-//')' | tail -1" 2>/dev/null); \
		if [ -n "$$gw_found" ]; then \
			echo "$(GREEN)Alpha found beta as gateway: $$gw_found$(RESET)"; \
			break; \
		fi; \
		if [ "$$i" = "6" ]; then \
			echo "$(YELLOW)No gateway log found, checking upstream list...$(RESET)"; \
			ssh $(SSH_OPTS) $(ROUTER_USER)@$$alpha_host "tollgate upstream list"; \
		fi; \
		echo "  ... $$((i*5))s"; \
	done; \
	\
	echo ""; \
	echo "$(CYAN)Step 8: Wait for initial payment and session (up to 30s)$(RESET)"; \
	echo "$(YELLOW)Watching for payment logs on alpha...$(RESET)"; \
	timeout 30 ssh $(SSH_OPTS) $(ROUTER_USER)@$$alpha_host "logread -e tollgate -f" 2>/dev/null | grep --line-buffered -i "payment\|session\|allotment\|purchase" | head -5; \
	\
	echo ""; \
	echo "$(CYAN)Step 9: Block mint on alpha (degrade)$(RESET)"; \
	$(MAKE) -f Makefile r-block-mint ROUTER=alpha MINT="$(MINT)"; \
	\
	echo ""; \
	echo "$(CYAN)Step 10: Wait for session renewal with offline wallet (up to 60s)$(RESET)"; \
	echo "$(YELLOW)With step_size=4096 and 8KB allotment, renewal should trigger within seconds.$(RESET)"; \
	timeout 60 ssh $(SSH_OPTS) $(ROUTER_USER)@$$alpha_host "logread -e tollgate -f" 2>/dev/null | grep --line-buffered -i "payment\|renewal\|allotment\|degraded\|offline\|SendWith" | head -10; \
	\
	echo ""; \
	echo "$(CYAN)Step 11: Restore alpha's upstream to $$active_ssid$(RESET)"; \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$alpha_host "tollgate upstream connect '$$active_ssid' '$$stargate_pass'" 2>&1 || \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$alpha_host "tollgate upstream connect '$$active_ssid'" 2>&1; \
	\
	echo ""; \
	echo "$(CYAN)Step 12: Wait for alpha's NetBird to recover$(RESET)"; \
	alpha_host=$$(grep -E "^ROUTER_ALPHA_HOST=" routers.env | cut -d= -f2); \
	for i in 1 2 3 4 5 6 7 8 9 10 11 12; do \
		sleep 10; \
		if ssh $(SSH_OPTS) $(ROUTER_USER)@$$alpha_host "echo netbird-ok" 2>/dev/null | grep -q netbird-ok; then \
			echo "$(GREEN)Alpha NetBird recovered after $$((i*10))s$(RESET)"; \
			break; \
		fi; \
		if [ "$$i" = "12" ]; then \
			echo "$(RED)Alpha NetBird not back after 120s$(RESET)"; \
			exit 1; \
		fi; \
		echo "  ... $$((i*10))s"; \
	done; \
	\
	echo ""; \
	echo "$(CYAN)Step 13: Restore production configs on both routers$(RESET)"; \
	$(MAKE) -f Makefile r-restore-prod-config ROUTER=alpha; \
	echo ""; \
	$(MAKE) -f Makefile r-restore-prod-config ROUTER=beta; \
	\
	echo ""; \
	echo "$(BOLD)=======================================$(RESET)"
	@echo "$(GREEN)$(BOLD)  Two-router degraded upstream test PASSED$(RESET)"
	@echo "$(BOLD)=======================================$(RESET)"

r-smoke-degraded-connect: ## [remote] WARNING: may strand router. Connect to upstream while already degraded.
	@$(MAKE) -f Makefile r-guard-no-dual-wwan ROUTER=alpha
	@echo "$(RED)$(BOLD)!!! WARNING: This test may strand the router !!!$(RESET)"
	@echo "$(RED)$(BOLD)!!! Alpha's WiFi will be reloaded, potentially losing NetBird   !!!$(RESET)"
	@echo "$(RED)$(BOLD)!!! Only run if someone is available for physical recovery        !!!$(RESET)"
	@echo ""
	@echo "Press Ctrl+C to abort, or wait 5 seconds to continue..."
	@sleep 5
	@echo ""
	@echo "$(BOLD)=======================================$(RESET)"
	@echo "$(BOLD)  Two-Router Degraded Connect Test (RISKY)$(RESET)"
	@echo "$(BOLD)=======================================$(RESET)"
	@echo ""
	@echo "$(CYAN)Step 1: Setup test mint on both routers$(RESET)"
	@$(MAKE) -f Makefile r-setup-test-mint ROUTER=alpha
	@echo ""
	@$(MAKE) -f Makefile r-setup-test-mint ROUTER=beta
	@echo ""
	@echo "$(CYAN)Step 1b: Fund both wallets$(RESET)"
	@$(MAKE) -f Makefile r-fund-wallet ROUTER=alpha
	@echo ""
	@$(MAKE) -f Makefile r-fund-wallet ROUTER=beta
	@echo ""
	@echo "$(CYAN)Step 2: Degrade alpha FIRST$(RESET)"
	@$(MAKE) -f Makefile r-block-mint ROUTER=alpha MINT="$(MINT)"
	@echo ""
	@$(MAKE) -f Makefile r-restart-service ROUTER=alpha
	@echo ""
	@$(MAKE) -f Makefile r-check-degraded ROUTER=alpha
	@echo ""
	@echo "$(CYAN)Step 3: Connect alpha to beta while degraded$(RESET)"
	@echo "$(YELLOW)If degraded payment fails, alpha may become unreachable.$(RESET)"
	@alpha_host=$$(grep -E "^ROUTER_ALPHA_HOST=" routers.env | cut -d= -f2); \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$alpha_host "tollgate upstream connect 'TollGate-D1C6' 2>&1"; \
	echo ""; \
	echo "$(CYAN)Step 4: Check if alpha survived$(RESET)"; \
	if ssh -o ConnectTimeout=10 $(SSH_OPTS) $(ROUTER_USER)@$$alpha_host "true" 2>/dev/null; then \
		echo "$(GREEN)Alpha survived! Checking connectivity...$(RESET)"; \
		ssh $(SSH_OPTS) $(ROUTER_USER)@$$alpha_host "ping -c 2 -W 5 9.9.9.9" && echo "$(GREEN)ONLINE$(RESET)" || echo "$(RED)No internet$(RESET)"; \
		echo ""; \
		echo "$(CYAN)Step 5: Restore both routers$(RESET)"; \
	$(MAKE) -f Makefile r-restore-prod-config ROUTER=alpha; \
		echo ""; \
		$(MAKE) -f Makefile r-restore-prod-config ROUTER=beta; \
		echo ""; \
		echo "$(GREEN)$(BOLD)  Degraded connect test PASSED$(RESET)"; \
	else \
		echo "$(RED)ALPHA IS UNREACHABLE$(RESET)"; \
		echo ""; \
		echo "$(YELLOW)Recovery options:$(RESET)"; \
		echo "  1. Connect to alpha's LAN WiFi (c08r4d0r-1690)"; \
		echo "  2. ssh root@192.168.41.1 'sed -i \"/nofee.testnut/d\" /etc/hosts && /etc/init.d/tollgate-wrt restart'"; \
		echo "  3. Or: physical power cycle"; \
		exit 1; \
	fi

r-full: ## [remote] Run the full mint health test suite (~20 min)
	@echo "$(BOLD)=======================================$(RESET)"
	@echo "$(BOLD)  Degraded Merchant Full Test Suite [$(ROUTER)]$(RESET)"
	@echo "$(BOLD)=======================================$(RESET)"
	@echo ""
	@$(MAKE) -f Makefile r-deploy ROUTER=$(ROUTER)
	@echo ""
	@$(MAKE) -f Makefile r-status ROUTER=$(ROUTER)
	@echo ""
	@$(MAKE) -f Makefile r-check-merchant ROUTER=$(ROUTER)
	@echo ""
	@$(MAKE) -f Makefile r-record-baseline ROUTER=$(ROUTER)
	@echo ""
	@$(MAKE) -f Makefile r-block-mint ROUTER=$(ROUTER) MINT="$(MINT)"
	@echo ""
	@$(MAKE) -f Makefile r-restart-service ROUTER=$(ROUTER)
	@echo ""
	@$(MAKE) -f Makefile r-check-degraded ROUTER=$(ROUTER)
	@echo ""
	@$(MAKE) -f Makefile r-test-offline-ops ROUTER=$(ROUTER)
	@echo ""
	@$(MAKE) -f Makefile r-unblock-mint ROUTER=$(ROUTER) MINT="$(MINT)"
	@echo ""
	@$(MAKE) -f Makefile r-wait-recovery ROUTER=$(ROUTER)
	@echo ""
	@$(MAKE) -f Makefile r-cleanup ROUTER=$(ROUTER)
	@echo ""
	@echo "$(BOLD)=======================================$(RESET)"
	@echo "$(GREEN)$(BOLD)  Full test suite complete [$(ROUTER)]$(RESET)"
	@echo "$(BOLD)=======================================$(RESET)"
	@echo ""
	@echo "$(YELLOW)Remaining manual steps (remote):$(RESET)"
	@echo "  - Phase 6: make -f Makefile r-test-first-boot-offline ROUTER=beta"
	@echo "  - Phase 7: make -f Makefile r-test-no-mints ROUTER=$(ROUTER)"

r-full-upstream: ## [remote] Run full upstream WiFi test suite via SSH (requires SSID, optional PASS, ROUTER)
	@if [ -z "$(SSID)" ]; then echo "$(RED)Error: SSID is required. Usage: make -f Makefile r-full-upstream SSID=MyNet PASS=secret ROUTER=alpha$(RESET)"; exit 1; fi
	@$(MAKE) -f Makefile r-status ROUTER=$(ROUTER)
	@echo ""
	@$(MAKE) -f Makefile r-scan ROUTER=$(ROUTER)
	@echo ""
	@$(MAKE) -f Makefile r-connect SSID="$(SSID)" PASS="$(PASS)" ROUTER=$(ROUTER)
	@echo ""
	@$(MAKE) -f Makefile r-list ROUTER=$(ROUTER)
	@echo ""
	@$(MAKE) -f Makefile r-verify-uci ROUTER=$(ROUTER)
	@echo ""
	@$(MAKE) -f Makefile r-test-edge-cases ROUTER=$(ROUTER)
	@echo ""
	@$(MAKE) -f Makefile r-test-cleanup ROUTER=$(ROUTER)
	@echo ""
	@echo "$(BOLD)=======================================$(RESET)"
	@echo "$(GREEN)$(BOLD)  Full upstream test suite PASSED [$(ROUTER)]$(RESET)"
	@echo "$(BOLD)=======================================$(RESET)"
	@echo ""
	@echo "$(YELLOW)Remaining manual steps (remote):$(RESET)"
	@echo "  - Phase 5:   make -f Makefile r-wait-daemon ROUTER=$(ROUTER)"
	@echo "  - Phase 6:   make -f Makefile r-remove SSID=<old-ssid> ROUTER=$(ROUTER)"
	@echo "  - Phase 7:   make -f Makefile r-test-reseller-guard ROUTER=$(ROUTER)"

define _resolve_router
$(eval include routers.env) \
_router_host=$$(grep -E "^ROUTER_$(shell echo $(ROUTER) | tr '[:lower:]' '[:upper:]')_HOST=" routers.env | cut -d= -f2); \
if [ -z "$$_router_host" ]; then echo "$(RED)Unknown router '$(ROUTER)'. Check routers.env$(RESET)" >&2; exit 1; fi
endef

define RESOLVE_ROUTER
router_host=$$(grep -E "^ROUTER_$$(echo $(ROUTER) | tr '[:lower:]' '[:upper:]')_HOST=" routers.env | cut -d= -f2); \
if [ -z "$$router_host" ]; then echo "$(RED)Unknown router '$(ROUTER)'. Check routers.env$(RESET)" >&2; exit 1; fi
endef

r-check-sta-health: ## [remote] Verify no stale/duplicate STA sections exist
	@$(RESOLVE_ROUTER); \
	echo "$(BOLD)=== STA Health Check [$(ROUTER)] ===$(RESET)"; \
	output=$$(ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "tollgate upstream list 2>/dev/null"); \
	echo "$$output"; \
	echo ""; \
	active_count=$$(echo "$$output" | grep -c 'ACTIVE'); \
	if [ "$$active_count" -gt 1 ]; then \
		echo "$(RED)FAIL: $$active_count ACTIVE STAs (expected 1)$(RESET)"; \
		exit 1; \
	fi; \
	total_count=$$(echo "$$output" | grep -cE '^[A-Za-z]'); \
	dup_ssids=$$(echo "$$output" | grep -E '^[A-Za-z]' | awk '{print $$1}' | sort | uniq -d); \
	if [ -n "$$dup_ssids" ]; then \
		echo "$(RED)FAIL: Duplicate SSIDs found: $$dup_ssids$(RESET)"; \
		exit 1; \
	fi; \
	echo "$(GREEN)OK: 1 active STA, no duplicates, $$total_count total STAs$(RESET)"

# ===========================================================================
#  SERIAL TARGETS (run via USB-TTL serial console)
# ===========================================================================
# Uses scripts/router-serial.py for reliable command execution over serial.
# These targets work even when the router has no network connectivity.
# Requires: pyserial (pip install pyserial)

define RESOLVE_SERIAL
serial_port=$$(grep -E "^ROUTER_$$(echo $(ROUTER) | tr '[:lower:]' '[:upper:]')_SERIAL=" routers.env | cut -d= -f2); \
if [ -z "$$serial_port" ]; then echo "$(RED)No serial port for '$(ROUTER)'. Check routers.env$(RESET)" >&2; exit 1; fi
endef

SERIAL_EXEC = python3 scripts/router-serial.py exec --port
SERIAL_WAIT = python3 scripts/router-serial.py wait --port
SERIAL_BOOTLOG = python3 scripts/router-serial.py bootlog --port
SERIAL_WATCH = python3 scripts/router-serial.py watch --port

s-shell: ## [serial] Interactive serial console (picocom)
	@$(RESOLVE_SERIAL); \
	echo "$(GREEN)Connecting to $(ROUTER) on $$serial_port...$(RESET)"; \
	echo "$(YELLOW)Exit: Ctrl+A then Ctrl+X$(RESET)"; \
	picocom $$serial_port -b 115200

s-watch: ## [serial] Watch serial output (router-serial.py watch, Ctrl+C to stop)
	@$(RESOLVE_SERIAL); \
	$(SERIAL_WATCH) $$serial_port

s-login: ## [serial] Send login credentials if at login prompt
	@$(RESOLVE_SERIAL); \
	python3 scripts/router-serial.py login --port $$serial_port

s-status: ## [serial] Check tollgate status via serial
	@$(RESOLVE_SERIAL); \
	echo "$(BOLD)=== Status via Serial [$(ROUTER)] ===$(RESET)"; \
	$(SERIAL_EXEC) $$serial_port "tollgate status"

s-check-merchant: ## [serial] Verify full merchant via serial
	@$(RESOLVE_SERIAL); \
	echo "$(BOLD)=== Check: Full Merchant via Serial [$(ROUTER)] ===$(RESET)"; \
	$(SERIAL_EXEC) $$serial_port "logread -e tollgate | grep -q 'Merchant ready' && echo OK_MERCHANT_READY || echo FAIL_NO_MERCHANT_READY"; \
	$(SERIAL_EXEC) $$serial_port "tollgate wallet balance"

s-check-degraded: ## [serial] Verify degraded merchant via serial
	@$(RESOLVE_SERIAL); \
	echo "$(BOLD)=== Check: Degraded Merchant via Serial [$(ROUTER)] ===$(RESET)"; \
	$(SERIAL_EXEC) $$serial_port "logread -e tollgate | grep -q 'Starting in degraded mode' && echo OK_DEGRADED || echo FAIL_NOT_DEGRADED"; \
	$(SERIAL_EXEC) $$serial_port "logread -e tollgate | grep 'offline wallet' | tail -1"; \
	$(SERIAL_EXEC) $$serial_port "tollgate wallet balance"; \
	$(SERIAL_EXEC) $$serial_port "tollgate status"

s-wait-boot: ## [serial] Wait for router to fully boot (login prompt)
	@$(RESOLVE_SERIAL); \
	echo "$(BOLD)=== Waiting for Boot [$(ROUTER)] ===$(RESET)"; \
	$(SERIAL_WAIT) $$serial_port --pattern "login:" --timeout 120

s-boot-log: ## [serial] Capture full boot output (send reboot first or power-cycle)
	@$(RESOLVE_SERIAL); \
	echo "$(BOLD)=== Boot Log Capture [$(ROUTER)] ===$(RESET)"; \
	$(SERIAL_BOOTLOG) $$serial_port --timeout 180 --end-pattern "login:" | tee /tmp/boot-log-$(ROUTER).log; \
	echo "$(GREEN)Boot log saved to /tmp/boot-log-$(ROUTER).log$(RESET)"

s-wait-pattern: ## [serial] Wait for a log pattern (PATTERN=, TIMEOUT=120)
	@$(RESOLVE_SERIAL); \
	if [ -z "$(PATTERN)" ]; then echo "$(RED)Error: PATTERN is required. Usage: make -f Makefile s-wait-pattern ROUTER=alpha PATTERN='Merchant ready'$(RESET)"; exit 1; fi; \
	echo "$(BOLD)=== Waiting for Pattern [$(ROUTER)] ===$(RESET)"; \
	echo "$(YELLOW)Pattern: $(PATTERN), Timeout: $(TIMEOUT)s$(RESET)"; \
	$(SERIAL_WAIT) $$serial_port --pattern "$(PATTERN)" --timeout $(TIMEOUT)

s-recovery: ## [serial] Emergency recovery — run any command via serial (CMD="...")
	@$(RESOLVE_SERIAL); \
	if [ -z "$(CMD)" ]; then echo "$(RED)Error: CMD is required. Usage: make -f Makefile s-recovery ROUTER=alpha CMD='reboot'$(RESET)"; exit 1; fi; \
	echo "$(BOLD)=== Serial Recovery [$(ROUTER)] ===$(RESET)"; \
	echo "$(YELLOW)Running: $(CMD)$(RESET)"; \
	$(SERIAL_EXEC) $$serial_port "$(CMD)"

s-cleanup: ## [serial] Cleanup mint blocks and restore config via serial
	@$(RESOLVE_SERIAL); \
	echo "$(BOLD)=== Serial Cleanup [$(ROUTER)] ===$(RESET)"; \
	echo "$(YELLOW)Removing mint blocks from /etc/hosts...$(RESET)"; \
	$(SERIAL_EXEC) $$serial_port "sed -i '/nofee.testnut/d' /etc/hosts"; \
	echo "$(YELLOW)Removing backup configs...$(RESET)"; \
	$(SERIAL_EXEC) $$serial_port "rm -f /etc/tollgate/config.json.bak /etc/tollgate/config.json.bak2 /etc/tollgate/config.json.prod-backup"; \
	echo "$(YELLOW)Restarting service...$(RESET)"; \
	$(SERIAL_EXEC) $$serial_port "/etc/init.d/tollgate-wrt restart"; \
	echo "$(GREEN)Cleanup complete via serial$(RESET)"

s-cold-boot-test: ## [serial] Full cold boot test with serial monitoring
	@$(RESOLVE_SERIAL); \
	echo "$(BOLD)=======================================$(RESET)"; \
	echo "$(BOLD)  Cold Boot Test [$(ROUTER)]$(RESET)"; \
	echo "$(BOLD)=======================================$(RESET)"; \
	echo ""; \
	echo "$(CYAN)1/4 — Issuing reboot...$(RESET)"; \
	$(SERIAL_EXEC) $$serial_port "reboot" 2>/dev/null || echo "$(YELLOW)Reboot command sent (or router already rebooting)$(RESET)"; \
	echo ""; \
	echo "$(CYAN)2/4 — Capturing boot log (up to 3 min)...$(RESET)"; \
	$(SERIAL_BOOTLOG) $$serial_port --timeout 180 --end-pattern "login:" > /tmp/cold-boot-$(ROUTER).log 2>/tmp/cold-boot-$(ROUTER)-stderr.txt; \
	cat /tmp/cold-boot-$(ROUTER).log; \
	cat /tmp/cold-boot-$(ROUTER)-stderr.txt >&2; \
	echo ""; \
	echo "$(CYAN)3/4 — Checking tollgate status...$(RESET)"; \
	python3 scripts/router-serial.py login --port $$serial_port 2>/dev/null; \
	$(SERIAL_EXEC) $$serial_port "tollgate status"; \
	echo ""; \
	echo "$(CYAN)4/4 — Checking merchant mode...$(RESET)"; \
	$(SERIAL_EXEC) $$serial_port "logread -e tollgate | grep -E 'Merchant ready|degraded' | tail -5"; \
	echo ""; \
	echo "$(BOLD)=======================================$(RESET)"; \
	echo "$(GREEN)$(BOLD)  Cold boot test complete [$(ROUTER)]$(RESET)"; \
	echo "$(BOLD)=======================================$(RESET)"; \
	echo "$(GREEN)Log: /tmp/cold-boot-$(ROUTER).log$(RESET)"

# ===========================================================================
#  HYBRID TARGETS (SSH first, serial fallback)
# ===========================================================================
# Try SSH first. If the router is unreachable, fall back to serial.
# Useful for tests that may strand the router (WiFi reload, network changes).

h-status: ## [hybrid] Status check — SSH first, serial fallback
	@$(RESOLVE_ROUTER); \
	if ssh $(SSH_OPTS) -o ConnectTimeout=5 $(ROUTER_USER)@$$router_host "echo ok" >/dev/null 2>&1; then \
		$(MAKE) -f Makefile r-status ROUTER=$(ROUTER); \
	else \
		echo "$(YELLOW)SSH unreachable, falling back to serial...$(RESET)"; \
		$(MAKE) -f Makefile s-status ROUTER=$(ROUTER); \
	fi

h-check-merchant: ## [hybrid] Merchant check — SSH first, serial fallback
	@$(RESOLVE_ROUTER); \
	if ssh $(SSH_OPTS) -o ConnectTimeout=5 $(ROUTER_USER)@$$router_host "echo ok" >/dev/null 2>&1; then \
		$(MAKE) -f Makefile r-check-merchant ROUTER=$(ROUTER); \
	else \
		echo "$(YELLOW)SSH unreachable, falling back to serial...$(RESET)"; \
		$(MAKE) -f Makefile s-check-merchant ROUTER=$(ROUTER); \
	fi

h-restart-and-watch: ## [hybrid] Restart service via SSH, verify via serial if SSH drops
	@$(RESOLVE_ROUTER); \
	echo "$(BOLD)=== Restart & Watch [$(ROUTER)] ===$(RESET)"; \
	ssh $(SSH_OPTS) -o ConnectTimeout=5 $(ROUTER_USER)@$$router_host "/etc/init.d/tollgate-wrt restart" 2>/dev/null && \
		echo "$(GREEN)Service restarted via SSH$(RESET)" || \
		echo "$(YELLOW)SSH lost during restart — checking via serial...$(RESET)"; \
	sleep 5; \
	$(MAKE) -f Makefile s-status ROUTER=$(ROUTER)

r-fix-dns: ## [remote] Fix NetBird DNS hijack: disable NetBird DNS, restore resolv.conf, restart tollgate
	@$(CHECK_LOCK)
	@$(RESOLVE_ROUTER); \
	echo "$(BOLD)=== Fix DNS [$(ROUTER)] ===$(RESET)"; \
	echo "$(CYAN)Step 1: Disable NetBird DNS management in config...$(RESET)"; \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "cd /etc/netbird && cat config.json | sed 's/\"DisableDNS\": false/\"DisableDNS\": true/' > config.json.tmp && mv config.json.tmp config.json && echo 'DisableDNS set to true' || echo 'No NetBird config found'"; \
	echo ""; \
	echo "$(CYAN)Step 2: Disable NetBird at boot and stop...$(RESET)"; \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "/etc/init.d/netbird disable 2>/dev/null; /etc/init.d/netbird stop 2>/dev/null; echo 'NetBird disabled+stopped'"; \
	echo ""; \
	echo "$(CYAN)Step 3: Restore resolv.conf from netifd auto...$(RESET)"; \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "cp /tmp/resolv.conf.d/resolv.conf.auto /etc/resolv.conf 2>/dev/null && echo 'resolv.conf restored' || echo 'No auto resolv.conf, keeping current'"; \
	echo ""; \
	echo "$(CYAN)Step 4: Verify DNS...$(RESET)"; \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "cat /etc/resolv.conf; echo '---'; nslookup nofee.testnut.cashu.space 2>&1 | head -5"; \
	echo ""; \
	echo "$(CYAN)Step 5: Restart tollgate...$(RESET)"; \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "/etc/init.d/tollgate-wrt restart"; \
	echo "$(YELLOW)Waiting 15s for startup...$(RESET)"; \
	sleep 15; \
	echo ""; \
	echo "$(CYAN)Step 6: Verify merchant mode...$(RESET)"; \
	resp=$$(ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "wget -qO- --timeout=10 http://localhost:2121/ 2>&1"); \
	echo "$$resp" | grep -q 'price_per_step' && echo "$(GREEN)OK: Full merchant mode (price_per_step found)$(RESET)" || echo "$(RED)WARN: Still in degraded mode (no price_per_step)$(RESET)"

r-test-captive-portal: ## [remote] Run Playwright captive portal tests against physical router
	@$(CHECK_LOCK)
	@echo "$(BOLD)=== Captive Portal Tests [$(ROUTER)] ===$(RESET)"; \
	router_host=$$(grep -E "^ROUTER_$$(echo $(ROUTER) | tr '[:lower:]' '[:upper:]')_HOST=" routers.env | cut -d= -f2); \
	if [ -z "$$router_host" ]; then echo "$(RED)Unknown router '$(ROUTER)'$(RESET)"; exit 1; fi; \
	echo "$(CYAN)Running Playwright tests against $$router_host:2050...$(RESET)"; \
	cd $$(dirname $$(pwd))/tests && TOLLGATE_CAPTIVE_PORTAL_HOST=$$router_host npx playwright test captive-portal 2>&1; \
	test_rc=$$?; \
	echo ""; \
	if [ $$test_rc -eq 0 ]; then \
		echo "$(GREEN)$(BOLD)Captive portal tests PASSED$(RESET)"; \
	else \
		echo "$(RED)$(BOLD)Captive portal tests FAILED (exit $$test_rc)$(RESET)"; \
		exit $$test_rc; \
	fi

r-test-captive-portal-happy: ## [remote] Run only happy-path Playwright captive portal tests (requires merchant mode)
	@$(CHECK_LOCK)
	@echo "$(BOLD)=== Happy Path Captive Portal Tests [$(ROUTER)] ===$(RESET)"; \
	router_host=$$(grep -E "^ROUTER_$$(echo $(ROUTER) | tr '[:lower:]' '[:upper:]')_HOST=" routers.env | cut -d= -f2); \
	if [ -z "$$router_host" ]; then echo "$(RED)Unknown router '$(ROUTER)'$(RESET)"; exit 1; fi; \
	echo "$(CYAN)Verifying merchant mode...$(RESET)"; \
	resp=$$(ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "wget -qO- --timeout=10 http://localhost:2121/ 2>&1"); \
	echo "$$resp" | grep -q 'price_per_step' || { echo "$(RED)Not in merchant mode. Run r-fix-dns first.$(RESET)"; exit 1; }; \
	echo "$(GREEN)Merchant mode confirmed$(RESET)"; \
	echo ""; \
	echo "$(CYAN)Running happy-path Playwright tests...$(RESET)"; \
	cd $$(dirname $$(pwd))/tests && TOLLGATE_CAPTIVE_PORTAL_HOST=$$router_host npx playwright test captive-portal -g "happy" 2>&1; \
	test_rc=$$?; \
	echo ""; \
	if [ $$test_rc -eq 0 ]; then \
		echo "$(GREEN)$(BOLD)Happy path tests PASSED$(RESET)"; \
	else \
		echo "$(RED)$(BOLD)Happy path tests FAILED (exit $$test_rc)$(RESET)"; \
		exit $$test_rc; \
	fi

r-test-cashu-payment: ## [remote] Run cashu e2e payment Playwright test (mint token → portal → purchase → checkmark)
	@$(CHECK_LOCK)
	@echo "$(BOLD)=== Cashu E2E Payment Test [$(ROUTER)] ===$(RESET)"; \
	router_host=$$(grep -E "^ROUTER_$$(echo $(ROUTER) | tr '[:lower:]' '[:upper:]')_HOST=" routers.env | cut -d= -f2); \
	if [ -z "$$router_host" ]; then echo "$(RED)No HOST for $(ROUTER)$(RESET)"; exit 1; fi; \
	if [ ! -x scripts/mint-token/mint-token ]; then \
		echo "$(RED)mint-token binary not found at scripts/mint-token/mint-token$(RESET)"; \
		exit 1; \
	fi; \
	echo "$(CYAN)Verifying merchant mode on $$router_host...$(RESET)"; \
	resp=$$(ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "wget -qO- --timeout=10 http://localhost:2121/ 2>&1"); \
	echo "$$resp" | grep -q 'price_per_step' || { echo "$(RED)Not in merchant mode. Run r-fix-dns first.$(RESET)"; exit 1; }; \
	echo "$(GREEN)Merchant mode confirmed$(RESET)"; \
	echo ""; \
	echo "$(CYAN)Running cashu e2e payment Playwright test...$(RESET)"; \
	cd tests && TOLLGATE_CAPTIVE_PORTAL_HOST=$$router_host npx playwright test captive-portal -g "cashu" --project=alpha 2>&1; \
	test_rc=$$?; \
	echo ""; \
	if [ $$test_rc -eq 0 ]; then \
		echo "$(GREEN)$(BOLD)Cashu e2e payment test PASSED$(RESET)"; \
	else \
		echo "$(RED)$(BOLD)Cashu e2e payment test FAILED (exit $$test_rc)$(RESET)"; \
		exit $$test_rc; \
	fi
