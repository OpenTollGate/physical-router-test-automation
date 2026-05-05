# ---------------------------------------------------------------------------
# Makefile.test — Manual testing for upstream WiFi management & mint health
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
#   make -f Makefile.test smoke   SSID=MyNet PASS=secret          # local upstream wifi
#   make -f Makefile.test r-smoke SSID=MyNet PASS=secret ROUTER=alpha  # remote upstream wifi
#   make -f Makefile.test r-smoke-offline ROUTER=alpha               # test degraded mode
#   make -f Makefile.test r-smoke-recovery ROUTER=alpha              # test recovery
#   make -f Makefile.test r-smoke-degraded ROUTER=alpha              # full degraded lifecycle (single router)
#   make -f Makefile.test r-smoke-degraded-upstream                  # degraded payment renewal (two routers)
#   make -f Makefile.test r-deploy ROUTER=alpha                      # deploy binaries
#   make -f Makefile.test r-full ROUTER=alpha                        # full mint health suite
#   make -f Makefile.test r-full-upstream SSID=MyNet PASS=secret ROUTER=alpha  # full wifi suite
#   make -f Makefile.test r-lock PHASE="description"                 # acquire router lock
#   make -f Makefile.test r-unlock                                   # release router lock
#
# Router mutex: all r-* targets that modify router state check routers.lock
# first. See docs/router-mutex.md for details.
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
        r-rescue-router r-save-upstream r-restore-upstream \
        r-lock r-unlock force-unlock r-status-lock \
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
if [ -z "$$router_host" ]; then echo "$(RED)Unknown router '$(ROUTER)'. Check routers.env$(RESET)" >&2; exit 1; fi; \
if [ -n "$(ROUTER_USE_LAN)" ]; then \
	router_lan=$$(grep -E "^ROUTER_$$(echo $(ROUTER) | tr '[:lower:]' '[:upper:]')_LAN_HOST=" routers.env | cut -d= -f2); \
	if [ -n "$$router_lan" ]; then router_host="$$router_lan"; fi; \
fi
endef

SSH = @router_host=$$(grep -E "^ROUTER_$$(echo $(ROUTER) | tr '[:lower:]' '[:upper:]')_HOST=" routers.env | cut -d= -f2); \
if [ -z "$$router_host" ]; then echo "$(RED)Unknown router '$(ROUTER)'. Check routers.env$(RESET)" >&2; exit 1; fi; \
ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host

define RESOLVE_ROUTER_LAN
router_lan=$$(grep -E "^ROUTER_$$(echo $(ROUTER) | tr '[:lower:]' '[:upper:]')_LAN_HOST=" routers.env | cut -d= -f2); \
if [ -z "$$router_lan" ]; then echo "$(RED)No LAN_HOST for '$(ROUTER)'. Check routers.env$(RESET)" >&2; exit 1; fi
endef

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
	echo "$(YELLOW)To check status: make -f Makefile.test r-status-lock$(RESET)"; \
	echo "$(YELLOW)To force-release: make -f Makefile.test force-unlock$(RESET)"; \
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
	@echo "Usage (local):  make -f Makefile.test <target> SSID=x PASS=y"
	@echo "Usage (remote): make -f Makefile.test r-<target> SSID=x PASS=y ROUTER=alpha"
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
	@echo "  make -f Makefile.test r-setup-test-mint ROUTER=alpha      # switch to test mint"
	@echo "  make -f Makefile.test r-smoke-degraded ROUTER=alpha       # single-router lifecycle"
	@echo "  make -f Makefile.test r-smoke-degraded-upstream           # two-router payment test"
	@echo "  make -f Makefile.test r-restore-prod-config ROUTER=alpha  # restore production config"

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
	@if [ -z "$(SSID)" ]; then echo "$(RED)Error: SSID is required. Usage: make -f Makefile.test connect SSID=MyNet PASS=secret$(RESET)"; exit 1; fi
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
	@if [ -z "$(SSID)" ]; then echo "$(RED)Error: SSID is required. Usage: make -f Makefile.test remove SSID=MyNet$(RESET)"; exit 1; fi
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
	@if [ -z "$(SSID)" ]; then echo "$(RED)Error: SSID is required. Usage: make -f Makefile.test smoke SSID=MyNet PASS=secret$(RESET)"; exit 1; fi
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
	@if [ -z "$(SSID)" ]; then echo "$(RED)Error: SSID is required. Usage: make -f Makefile.test full-upstream SSID=MyNet PASS=secret$(RESET)"; exit 1; fi
	@echo "$(BOLD)=======================================$(RESET)"
	@echo "$(BOLD)  Upstream WiFi Full Test Suite$(RESET)"
	@echo "$(BOLD)=======================================$(RESET)"
	@echo ""
	@$(MAKE) -f Makefile.test status
	@echo ""
	@$(MAKE) -f Makefile.test scan
	@echo ""
	@$(MAKE) -f Makefile.test connect SSID="$(SSID)" PASS="$(PASS)"
	@echo ""
	@$(MAKE) -f Makefile.test list
	@echo ""
	@$(MAKE) -f Makefile.test verify-uci
	@echo ""
	@$(MAKE) -f Makefile.test test-edge-cases SSID="$(SSID)"
	@echo ""
	@$(MAKE) -f Makefile.test test-cleanup
	@echo ""
	@echo "$(BOLD)=======================================$(RESET)"
	@echo "$(GREEN)$(BOLD)  Full test suite PASSED$(RESET)"
	@echo "$(BOLD)=======================================$(RESET)"
	@echo ""
	@echo "$(YELLOW)Remaining manual steps:$(RESET)"
	@echo "  - Phase 5:  make -f Makefile.test wait-daemon"
	@echo "  - Phase 5.3: make -f Makefile.test simulate-connectivity-loss"
	@echo "  - Phase 6:   make -f Makefile.test remove SSID=<old-ssid> (after connecting to a different one)"
	@echo "  - Phase 7:   make -f Makefile.test test-reseller-guard"

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
	@$(MAKE) -f Makefile.test block-mint MINT="$(MINT)"
	@$(MAKE) -f Makefile.test restart-service
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
	@echo "$(YELLOW)Restore: make -f Makefile.test unblock-mint MINT='$(MINT)' && make -f Makefile.test restart-service$(RESET)"

smoke-recovery: ## [local] Quick recovery test: unblock + wait
	@echo "$(BOLD)=======================================$(RESET)"
	@echo "$(BOLD)  Recovery Smoke Test (~15 min)$(RESET)"
	@echo "$(BOLD)=======================================$(RESET)"
	@echo ""
	@echo "$(CYAN)1/2 — Unblocking mint...$(RESET)"
	@$(MAKE) -f Makefile.test unblock-mint MINT="$(MINT)"
	@echo ""
	@echo "$(CYAN)2/2 — Watching for recovery...$(RESET)"
	@$(MAKE) -f Makefile.test wait-recovery

full: ## [local] Run the full mint health test suite (~20 min)
	@echo "$(BOLD)=======================================$(RESET)"
	@echo "$(BOLD)  Degraded Merchant Full Test Suite$(RESET)"
	@echo "$(BOLD)=======================================$(RESET)"
	@echo ""
	@$(MAKE) -f Makefile.test status
	@echo ""
	@$(MAKE) -f Makefile.test check-merchant
	@echo ""
	@$(MAKE) -f Makefile.test record-baseline
	@echo ""
	@$(MAKE) -f Makefile.test block-mint MINT="$(MINT)"
	@echo ""
	@$(MAKE) -f Makefile.test restart-service
	@echo ""
	@$(MAKE) -f Makefile.test check-degraded
	@echo ""
	@$(MAKE) -f Makefile.test test-offline-ops
	@echo ""
	@$(MAKE) -f Makefile.test unblock-mint MINT="$(MINT)"
	@echo ""
	@$(MAKE) -f Makefile.test wait-recovery
	@echo ""
	@$(MAKE) -f Makefile.test cleanup
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
		echo "$(YELLOW)Use 'make -f Makefile.test force-unlock' to override (with caution).$(RESET)"; \
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
				echo "$(YELLOW)Consider: make -f Makefile.test force-unlock$(RESET)"; \
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
	@if [ -z "$(SSID)" ]; then echo "$(RED)Error: SSID is required. Usage: make -f Makefile.test r-connect SSID=MyNet PASS=secret ROUTER=alpha$(RESET)"; exit 1; fi
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
	@if [ -z "$(SSID)" ]; then echo "$(RED)Error: SSID is required. Usage: make -f Makefile.test r-remove SSID=MyNet ROUTER=alpha$(RESET)"; exit 1; fi
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
	@if [ -z "$(SSID)" ]; then echo "$(RED)Error: SSID is required. Usage: make -f Makefile.test r-smoke SSID=MyNet PASS=secret ROUTER=alpha$(RESET)"; exit 1; fi
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
	@$(MAKE) -f Makefile.test r-record-baseline ROUTER=$(ROUTER)
	@echo ""
	@$(MAKE) -f Makefile.test r-block-mint ROUTER=$(ROUTER) MINT="$(MINT)"
	@echo ""
	@$(MAKE) -f Makefile.test r-restart-service ROUTER=$(ROUTER)
	@echo ""
	@$(MAKE) -f Makefile.test r-check-degraded ROUTER=$(ROUTER)
	@echo ""
	@$(MAKE) -f Makefile.test r-test-offline-ops ROUTER=$(ROUTER)
	@echo ""
	@echo "$(BOLD)=======================================$(RESET)"
	@echo "$(GREEN)$(BOLD)  Offline smoke test complete [$(ROUTER)]$(RESET)"
	@echo "$(BOLD)=======================================$(RESET)"
	@echo ""
	@echo "$(YELLOW)To test recovery:$(RESET)"
	@echo "  make -f Makefile.test r-smoke-recovery ROUTER=$(ROUTER)"
	@echo ""
	@echo "$(YELLOW)To restore immediately:$(RESET)"
	@echo "  make -f Makefile.test r-cleanup ROUTER=$(ROUTER)"

r-smoke-recovery: ## [remote] Quick recovery smoke: unblock + wait
	@$(MAKE) -f Makefile.test r-unblock-mint ROUTER=$(ROUTER) MINT="$(MINT)"
	@echo ""
	@$(MAKE) -f Makefile.test r-wait-recovery ROUTER=$(ROUTER)

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
	@$(MAKE) -f Makefile.test r-setup-test-mint ROUTER=$(ROUTER)
	@echo ""
	@$(MAKE) -f Makefile.test r-fund-wallet ROUTER=$(ROUTER)
	@echo ""
	@$(MAKE) -f Makefile.test r-check-merchant ROUTER=$(ROUTER)
	@echo ""
	@$(MAKE) -f Makefile.test r-record-baseline ROUTER=$(ROUTER)
	@echo ""
	@$(MAKE) -f Makefile.test r-block-mint ROUTER=$(ROUTER) MINT="$(MINT)"
	@echo ""
	@$(MAKE) -f Makefile.test r-restart-service ROUTER=$(ROUTER)
	@echo ""
	@$(MAKE) -f Makefile.test r-check-degraded ROUTER=$(ROUTER)
	@echo ""
	@$(MAKE) -f Makefile.test r-test-offline-ops ROUTER=$(ROUTER)
	@echo ""
	@$(MAKE) -f Makefile.test r-unblock-mint ROUTER=$(ROUTER) MINT="$(MINT)"
	@echo ""
	@$(MAKE) -f Makefile.test r-restart-service ROUTER=$(ROUTER)
	@echo ""
	@$(MAKE) -f Makefile.test r-check-merchant ROUTER=$(ROUTER)
	@echo ""
	@$(MAKE) -f Makefile.test r-restore-prod-config ROUTER=$(ROUTER)
	@echo ""
	@echo "$(BOLD)=======================================$(RESET)"
	@echo "$(GREEN)$(BOLD)  Degraded mode smoke test PASSED [$(ROUTER)]$(RESET)"
	@echo "$(BOLD)=======================================$(RESET)"

r-rescue-router: ## [remote] Rescue a router with dead NetBird via another router's LAN. Usage: r-rescue-router ROUTER=beta VIA=alpha
	@if [ -z "$(VIA)" ]; then echo "$(RED)Error: VIA is required. Usage: make -f Makefile.test r-rescue-router ROUTER=beta VIA=alpha$(RESET)"; exit 1; fi
	@target_upper=$$(echo $(ROUTER) | tr '[:lower:]' '[:upper:]'); \
	via_upper=$$(echo $(VIA) | tr '[:lower:]' '[:upper:]'); \
	via_lan=$$(grep -E "^ROUTER_$${via_upper}_LAN_HOST=" routers.env | cut -d= -f2); \
	target_lan=$$(grep -E "^ROUTER_$${target_upper}_LAN_HOST=" routers.env | cut -d= -f2); \
	target_ssid=$$(grep -E "^ROUTER_$${target_upper}_SSID=" routers.env | cut -d= -f2); \
	target_netbird=$$(grep -E "^ROUTER_$${target_upper}_HOST=" routers.env | cut -d= -f2); \
	if [ -z "$$via_lan" ]; then echo "$(RED)No LAN_HOST for $(VIA). Check routers.env$(RESET)"; exit 1; fi; \
	if [ -z "$$target_lan" ]; then echo "$(RED)No LAN_HOST for $(ROUTER). Check routers.env$(RESET)"; exit 1; fi; \
	if [ -z "$$target_ssid" ]; then echo "$(RED)No SSID for $(ROUTER). Check routers.env$(RESET)"; exit 1; fi; \
	echo "$(BOLD)=== Rescue $(ROUTER) via $(VIA) ===$(RESET)"; \
	echo "Connecting $(VIA) ($$via_lan) to $(ROUTER)'s open AP ($$target_ssid)..."; \
	sta_name=$$(echo "$$target_ssid" | tr '[:upper:][:space:]' '[:lower:]_' | sed 's/[^a-z0-9_]//g'); \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$via_lan "\
		if uci get wireless.upstream_$$sta_name >/dev/null 2>&1; then \
			uci set wireless.upstream_$$sta_name.disabled='0'; \
		else \
			uci set wireless.upstream_$$sta_name=wifi-iface; \
			uci set wireless.upstream_$$sta_name.device='radio1'; \
			uci set wireless.upstream_$$sta_name.mode='sta'; \
			uci set wireless.upstream_$$sta_name.network='wwan'; \
			uci set wireless.upstream_$$sta_name.ssid='$$target_ssid'; \
			uci set wireless.upstream_$$sta_name.encryption='none'; \
			uci set wireless.upstream_$$sta_name.disabled='0'; \
		fi; \
		uci commit wireless; \
		wifi reload radio1; \
		echo 'Waiting for DHCP on radio1...'; \
		for i in 1 2 3 4 5 6 7 8 9 10 11 12; do \
			sleep 5; \
			if ifconfig phy1-sta0 2>/dev/null | grep -q 'inet addr'; then \
				echo 'Got DHCP on phy1-sta0'; \
				break; \
			fi; \
			echo \"  ... \$\$((i*5))s\"; \
			if [ \"\$$i\" = '12' ]; then \
				echo 'TIMEOUT: no DHCP on radio1'; \
				uci set wireless.upstream_$$sta_name.disabled='1'; \
				uci commit wireless; \
				wifi reload radio1; \
				exit 1; \
			fi; \
		done; \
		echo 'SSHing to $(ROUTER) at $$target_lan to restart NetBird...'; \
		ssh -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new $(ROUTER_USER)@$$target_lan '/etc/init.d/netbird restart'; \
		sleep 5; \
		echo 'Disabling rescue STA...'; \
		uci set wireless.upstream_$$sta_name.disabled='1'; \
		uci commit wireless; \
		wifi reload radio1; \
		echo 'Rescue complete'; \
	"; \
	echo ""; \
	echo "$(YELLOW)Waiting for $(ROUTER) NetBird ($$target_netbird) to come up...$(RESET)"; \
	for i in 1 2 3 4 5 6 7 8 9 10 11 12; do \
		sleep 10; \
		if ssh $(SSH_OPTS) $(ROUTER_USER)@$$target_netbird "echo netbird-ok" 2>/dev/null | grep -q netbird-ok; then \
			echo "$(GREEN)$(ROUTER) NetBird recovered after $$((i*10))s!$(RESET)"; \
			exit 0; \
		fi; \
		echo "  ... $$((i*10))s"; \
	done; \
	echo "$(RED)$(ROUTER) NetBird not reachable after 120s$(RESET)"; \
	exit 1

r-save-upstream: ## [remote] Save current active upstream SSID for later restore
	@$(RESOLVE_ROUTER_LAN); \
	echo "$(BOLD)=== Save Upstream [$(ROUTER)] ===$(RESET)"; \
	active_ssid=$$(ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_lan "tollgate upstream list 2>/dev/null" | grep ACTIVE | awk '{print $$1}'); \
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
		ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_lan "tollgate upstream connect '$$saved_ssid' '$$stargate_pass' 2>&1 || tollgate upstream connect '$$saved_ssid' 2>&1"; \
	else \
		ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_lan "tollgate upstream connect '$$saved_ssid' 2>&1"; \
	fi; \
	echo "$(GREEN)Restored upstream to $$saved_ssid$(RESET)"

r-smoke-degraded-upstream: ## [remote] Two-router: connect online, degrade, verify offline renewal (uses LAN for alpha)
	@echo "$(BOLD)=======================================$(RESET)"
	@echo "$(BOLD)  Two-Router Degraded Upstream Test$(RESET)"
	@echo "$(BOLD)=======================================$(RESET)"
	@echo ""
	@alpha_lan=$$(grep -E "^ROUTER_ALPHA_LAN_HOST=" routers.env | cut -d= -f2); \
	beta_host=$$(grep -E "^ROUTER_BETA_HOST=" routers.env | cut -d= -f2); \
	beta_ssid=$$(grep -E "^ROUTER_BETA_SSID=" routers.env | cut -d= -f2); \
	stargate_pass=$$(grep -E "^STARGATE_PASS=" routers.env | cut -d= -f2); \
	if [ -z "$$alpha_lan" ]; then echo "$(RED)No ROUTER_ALPHA_LAN_HOST in routers.env$(RESET)"; exit 1; fi; \
	if [ -z "$$beta_host" ]; then echo "$(RED)No ROUTER_BETA_HOST in routers.env$(RESET)"; exit 1; fi; \
	\
	echo "$(CYAN)Step 0: Pre-flight — verify connectivity$(RESET)"; \
	echo "  Alpha (LAN $$alpha_lan)..."; \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$alpha_lan "echo alpha-lan-ok" 2>/dev/null | grep -q alpha-lan-ok || { echo "$(RED)Alpha unreachable via LAN ($$alpha_lan). Connect Ethernet.$(RESET)"; exit 1; }; \
	echo "  Beta (NetBird $$beta_host)..."; \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$beta_host "echo beta-ok" 2>/dev/null | grep -q beta-ok || { echo "$(RED)Beta unreachable via NetBird ($$beta_host). Run r-rescue-router first.$(RESET)"; exit 1; }; \
	echo "$(GREEN)Both routers accessible$(RESET)"; \
	\
	echo ""; \
	echo "$(CYAN)Step 1: Save alpha's current upstream$(RESET)"; \
	active_ssid=$$(ssh $(SSH_OPTS) $(ROUTER_USER)@$$alpha_lan "tollgate upstream list 2>/dev/null" | grep ACTIVE | awk '{print $$1}'); \
	if [ -z "$$active_ssid" ]; then echo "$(RED)No active upstream on alpha$(RESET)"; exit 1; fi; \
	echo "$$active_ssid" > /tmp/tollgate-upstream-alpha.txt; \
	echo "$(GREEN)Saved: $$active_ssid$(RESET)"; \
	\
	echo ""; \
	echo "$(CYAN)Step 2: Setup test mint on both routers$(RESET)"; \
	$(MAKE) -f Makefile.test r-setup-test-mint ROUTER=alpha ROUTER_USE_LAN=1; \
	echo ""; \
	$(MAKE) -f Makefile.test r-setup-test-mint ROUTER=beta; \
	\
	echo ""; \
	echo "$(CYAN)Step 3: Fund both wallets$(RESET)"; \
	$(MAKE) -f Makefile.test r-fund-wallet ROUTER=alpha ROUTER_USE_LAN=1; \
	echo ""; \
	$(MAKE) -f Makefile.test r-fund-wallet ROUTER=beta; \
	\
	echo ""; \
	echo "$(CYAN)Step 4: Verify both routers are merchant-ready$(RESET)"; \
	echo "  Alpha:"; \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$alpha_lan "tollgate wallet balance"; \
	echo "  Beta:"; \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$beta_host "tollgate wallet balance"; \
	\
	echo ""; \
	echo "$(CYAN)Step 5: Connect alpha to beta's open AP ($$beta_ssid) via radio1$(RESET)"; \
	echo "$(YELLOW)Using LAN SSH — NetBird disruption on alpha is OK.$(RESET)"; \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$alpha_lan "tollgate upstream connect '$$beta_ssid' 2>&1"; \
	ssh_rc=$$?; \
	echo "Connect exit code: $$ssh_rc"; \
	\
	echo ""; \
	echo "$(CYAN)Step 6: Wait for alpha to get DHCP on beta's LAN (up to 60s)$(RESET)"; \
	for i in 1 2 3 4 5 6 7 8 9 10 11 12; do \
		sleep 5; \
		if ssh $(SSH_OPTS) $(ROUTER_USER)@$$alpha_lan "ifconfig phy1-sta0 2>/dev/null | grep -q 'inet addr'" 2>/dev/null; then \
			echo "$(GREEN)Alpha connected to beta's AP after $$((i*5))s$(RESET)"; \
			break; \
		fi; \
		if [ "$$i" = "12" ]; then \
			echo "$(RED)Alpha failed to connect to beta's AP$(RESET)"; \
			echo "$(YELLOW)Restoring alpha's upstream...$(RESET)"; \
			ssh $(SSH_OPTS) $(ROUTER_USER)@$$alpha_lan "tollgate upstream connect '$$active_ssid' '$$stargate_pass'" 2>/dev/null; \
			exit 1; \
		fi; \
		echo "  ... $$((i*5))s"; \
	done; \
	\
	echo ""; \
	echo "$(CYAN)Step 7: Verify alpha sees beta as TollGate gateway (up to 30s)$(RESET)"; \
	for i in 1 2 3 4 5 6; do \
		sleep 5; \
		gw_found=$$(ssh $(SSH_OPTS) $(ROUTER_USER)@$$alpha_lan "logread -e tollgate 2>/dev/null | grep -i 'gateway.*$$(echo $$beta_ssid | sed 's/TollGate-//')' | tail -1" 2>/dev/null); \
		if [ -n "$$gw_found" ]; then \
			echo "$(GREEN)Alpha found beta as gateway: $$gw_found$(RESET)"; \
			break; \
		fi; \
		if [ "$$i" = "6" ]; then \
			echo "$(YELLOW)No gateway log found, checking upstream list...$(RESET)"; \
			ssh $(SSH_OPTS) $(ROUTER_USER)@$$alpha_lan "tollgate upstream list"; \
		fi; \
		echo "  ... $$((i*5))s"; \
	done; \
	\
	echo ""; \
	echo "$(CYAN)Step 8: Wait for initial payment and session (up to 30s)$(RESET)"; \
	echo "$(YELLOW)Watching for payment logs on alpha...$(RESET)"; \
	timeout 30 ssh $(SSH_OPTS) $(ROUTER_USER)@$$alpha_lan "logread -e tollgate -f" 2>/dev/null | grep --line-buffered -i "payment\|session\|allotment\|purchase" | head -5; \
	\
	echo ""; \
	echo "$(CYAN)Step 9: Block mint on alpha (degrade)$(RESET)"; \
	$(MAKE) -f Makefile.test r-block-mint ROUTER=alpha MINT="$(MINT)" ROUTER_USE_LAN=1; \
	\
	echo ""; \
	echo "$(CYAN)Step 10: Wait for session renewal with offline wallet (up to 60s)$(RESET)"; \
	echo "$(YELLOW)With step_size=4096 and 8KB allotment, renewal should trigger within seconds.$(RESET)"; \
	timeout 60 ssh $(SSH_OPTS) $(ROUTER_USER)@$$alpha_lan "logread -e tollgate -f" 2>/dev/null | grep --line-buffered -i "payment\|renewal\|allotment\|degraded\|offline\|SendWith" | head -10; \
	\
	echo ""; \
	echo "$(CYAN)Step 11: Restore alpha's upstream to $$active_ssid$(RESET)"; \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$alpha_lan "tollgate upstream connect '$$active_ssid' '$$stargate_pass'" 2>&1 || \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$alpha_lan "tollgate upstream connect '$$active_ssid'" 2>&1; \
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
	$(MAKE) -f Makefile.test r-restore-prod-config ROUTER=alpha; \
	echo ""; \
	$(MAKE) -f Makefile.test r-restore-prod-config ROUTER=beta; \
	\
	echo ""; \
	echo "$(BOLD)=======================================$(RESET)"
	@echo "$(GREEN)$(BOLD)  Two-router degraded upstream test PASSED$(RESET)"
	@echo "$(BOLD)=======================================$(RESET)"

r-smoke-degraded-connect: ## [remote] WARNING: may strand router. Connect to upstream while already degraded.
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
	@$(MAKE) -f Makefile.test r-setup-test-mint ROUTER=alpha ROUTER_USE_LAN=1
	@echo ""
	@$(MAKE) -f Makefile.test r-setup-test-mint ROUTER=beta
	@echo ""
	@echo "$(CYAN)Step 1b: Fund both wallets$(RESET)"
	@$(MAKE) -f Makefile.test r-fund-wallet ROUTER=alpha ROUTER_USE_LAN=1
	@echo ""
	@$(MAKE) -f Makefile.test r-fund-wallet ROUTER=beta
	@echo ""
	@echo "$(CYAN)Step 2: Degrade alpha FIRST$(RESET)"
	@$(MAKE) -f Makefile.test r-block-mint ROUTER=alpha MINT="$(MINT)" ROUTER_USE_LAN=1
	@echo ""
	@$(MAKE) -f Makefile.test r-restart-service ROUTER=alpha ROUTER_USE_LAN=1
	@echo ""
	@$(MAKE) -f Makefile.test r-check-degraded ROUTER=alpha ROUTER_USE_LAN=1
	@echo ""
	@echo "$(CYAN)Step 3: Connect alpha to beta while degraded$(RESET)"
	@echo "$(YELLOW)If degraded payment fails, alpha may become unreachable.$(RESET)"
	@alpha_lan=$$(grep -E "^ROUTER_ALPHA_LAN_HOST=" routers.env | cut -d= -f2); \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$alpha_lan "tollgate upstream connect 'TollGate-D1C6' 2>&1"; \
	echo ""; \
	echo "$(CYAN)Step 4: Check if alpha survived$(RESET)"; \
	if ssh -o ConnectTimeout=10 $(SSH_OPTS) $(ROUTER_USER)@$$alpha_lan "true" 2>/dev/null; then \
		echo "$(GREEN)Alpha survived! Checking connectivity...$(RESET)"; \
		ssh $(SSH_OPTS) $(ROUTER_USER)@$$alpha_lan "ping -c 2 -W 5 9.9.9.9" && echo "$(GREEN)ONLINE$(RESET)" || echo "$(RED)No internet$(RESET)"; \
		echo ""; \
		echo "$(CYAN)Step 5: Restore both routers$(RESET)"; \
	$(MAKE) -f Makefile.test r-restore-prod-config ROUTER=alpha ROUTER_USE_LAN=1; \
		echo ""; \
		$(MAKE) -f Makefile.test r-restore-prod-config ROUTER=beta; \
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
	@$(MAKE) -f Makefile.test r-deploy ROUTER=$(ROUTER)
	@echo ""
	@$(MAKE) -f Makefile.test r-status ROUTER=$(ROUTER)
	@echo ""
	@$(MAKE) -f Makefile.test r-check-merchant ROUTER=$(ROUTER)
	@echo ""
	@$(MAKE) -f Makefile.test r-record-baseline ROUTER=$(ROUTER)
	@echo ""
	@$(MAKE) -f Makefile.test r-block-mint ROUTER=$(ROUTER) MINT="$(MINT)"
	@echo ""
	@$(MAKE) -f Makefile.test r-restart-service ROUTER=$(ROUTER)
	@echo ""
	@$(MAKE) -f Makefile.test r-check-degraded ROUTER=$(ROUTER)
	@echo ""
	@$(MAKE) -f Makefile.test r-test-offline-ops ROUTER=$(ROUTER)
	@echo ""
	@$(MAKE) -f Makefile.test r-unblock-mint ROUTER=$(ROUTER) MINT="$(MINT)"
	@echo ""
	@$(MAKE) -f Makefile.test r-wait-recovery ROUTER=$(ROUTER)
	@echo ""
	@$(MAKE) -f Makefile.test r-cleanup ROUTER=$(ROUTER)
	@echo ""
	@echo "$(BOLD)=======================================$(RESET)"
	@echo "$(GREEN)$(BOLD)  Full test suite complete [$(ROUTER)]$(RESET)"
	@echo "$(BOLD)=======================================$(RESET)"
	@echo ""
	@echo "$(YELLOW)Remaining manual steps (remote):$(RESET)"
	@echo "  - Phase 6: make -f Makefile.test r-test-first-boot-offline ROUTER=beta"
	@echo "  - Phase 7: make -f Makefile.test r-test-no-mints ROUTER=$(ROUTER)"

r-full-upstream: ## [remote] Run full upstream WiFi test suite via SSH (requires SSID, optional PASS, ROUTER)
	@if [ -z "$(SSID)" ]; then echo "$(RED)Error: SSID is required. Usage: make -f Makefile.test r-full-upstream SSID=MyNet PASS=secret ROUTER=alpha$(RESET)"; exit 1; fi
	@$(MAKE) -f Makefile.test r-status ROUTER=$(ROUTER)
	@echo ""
	@$(MAKE) -f Makefile.test r-scan ROUTER=$(ROUTER)
	@echo ""
	@$(MAKE) -f Makefile.test r-connect SSID="$(SSID)" PASS="$(PASS)" ROUTER=$(ROUTER)
	@echo ""
	@$(MAKE) -f Makefile.test r-list ROUTER=$(ROUTER)
	@echo ""
	@$(MAKE) -f Makefile.test r-verify-uci ROUTER=$(ROUTER)
	@echo ""
	@$(MAKE) -f Makefile.test r-test-edge-cases ROUTER=$(ROUTER)
	@echo ""
	@$(MAKE) -f Makefile.test r-test-cleanup ROUTER=$(ROUTER)
	@echo ""
	@echo "$(BOLD)=======================================$(RESET)"
	@echo "$(GREEN)$(BOLD)  Full upstream test suite PASSED [$(ROUTER)]$(RESET)"
	@echo "$(BOLD)=======================================$(RESET)"
	@echo ""
	@echo "$(YELLOW)Remaining manual steps (remote):$(RESET)"
	@echo "  - Phase 5:   make -f Makefile.test r-wait-daemon ROUTER=$(ROUTER)"
	@echo "  - Phase 6:   make -f Makefile.test r-remove SSID=<old-ssid> ROUTER=$(ROUTER)"
	@echo "  - Phase 7:   make -f Makefile.test r-test-reseller-guard ROUTER=$(ROUTER)"

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
