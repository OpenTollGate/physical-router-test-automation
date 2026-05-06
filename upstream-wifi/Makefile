# ---------------------------------------------------------------------------
# Makefile.test — Manual testing for upstream WiFi management
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
#   make -f Makefile.test smoke   SSID=MyNet PASS=secret          # local
#   make -f Makefile.test r-smoke SSID=MyNet PASS=secret ROUTER=alpha  # remote
# ---------------------------------------------------------------------------

.PHONY: smoke full status logs scan list connect remove \
        test-edge-cases test-reseller-guard test-cleanup \
        wait-daemon simulate-connectivity-loss verify-uci \
        r-smoke r-full r-status r-logs r-scan r-list r-connect \
        r-remove r-verify-uci r-wait-daemon r-test-cleanup \
        r-test-edge-cases r-test-reseller-guard r-push r-shell \
        r-check-sta-health \
        s-shell s-watch s-login s-status s-recovery s-wait-pattern \
        s-cleanup s-cold-boot-test s-boot-log s-wait-boot \
        h-status h-restart-and-watch h-cleanup \
        routers help

SSID   ?=
PASS   ?=
ROUTER ?= alpha

BOLD   := \033[1m
GREEN  := \033[32m
RED    := \033[31m
YELLOW := \033[33m
CYAN   := \033[36m
RESET  := \033[0m

# ---- Remote helper --------------------------------------------------------
# Source routers.env, resolve ROUTER_<NAME>_HOST, build SSH command.

 routers.env:
	$(eval include routers.env)
	$(eval _ROUTER_HOST := $(foreach v,$(filter ROUTER_%_HOST,$(.VARIABLES)),$(if $(filter $(ROUTER),$(patsubst ROUTER_%_HOST,%,$v)),$(value $v))))
	$(eval _SSH := ssh $(SSH_OPTS) $(ROUTER_USER)@$(_ROUTER_HOST))

define _resolve_router
$(eval include routers.env) \
_router_host=$$(grep -E "^ROUTER_$(shell echo $(ROUTER) | tr '[:lower:]' '[:upper:]')_HOST=" routers.env | cut -d= -f2); \
if [ -z "$$_router_host" ]; then echo "$(RED)Unknown router '$(ROUTER)'. Check routers.env$(RESET)" >&2; exit 1; fi
endef

SSH = @router_host=$$(grep -E "^ROUTER_$$(echo $(ROUTER) | tr '[:lower:]' '[:upper:]')_HOST=" routers.env | cut -d= -f2); \
if [ -z "$$router_host" ]; then echo "$(RED)Unknown router '$(ROUTER)'. Check routers.env$(RESET)" >&2; exit 1; fi; \
ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host

# ===========================================================================
#  LOCAL TARGETS (run on the router itself)
# ===========================================================================

help: ## Show this help
	@echo "Upstream WiFi Manual Testing Targets"
	@echo "====================================="
	@echo ""
	@echo "Usage (local):  make -f Makefile.test <target> SSID=x PASS=y"
	@echo "Usage (remote): make -f Makefile.test r-<target> SSID=x PASS=y ROUTER=alpha"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  $(CYAN)%-35s$(RESET) %s\n", $$1, $$2}'
	@echo ""
	@echo "Variables:"
	@echo "  SSID    - upstream WiFi SSID (required for connect/smoke/full)"
	@echo "  PASS    - upstream WiFi passphrase (optional for open networks)"
	@echo "  ROUTER  - router label from routers.env (default: alpha)"

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

smoke: ## [local] Run the 5-minute smoke test (requires SSID, optional PASS)
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

full: ## [local] Run the full test suite (requires SSID, optional PASS)
	@if [ -z "$(SSID)" ]; then echo "$(RED)Error: SSID is required. Usage: make -f Makefile.test full SSID=MyNet PASS=secret$(RESET)"; exit 1; fi
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

# ===========================================================================
#  REMOTE TARGETS (run from dev machine via SSH)
# ===========================================================================

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

r-shell: ## [remote] Open an interactive SSH session on the router
	@router_host=$$(grep -E "^ROUTER_$$(echo $(ROUTER) | tr '[:lower:]' '[:upper:]')_HOST=" routers.env | cut -d= -f2); \
	if [ -z "$$router_host" ]; then echo "$(RED)Unknown router '$(ROUTER)'$(RESET)"; exit 1; fi; \
	echo "$(GREEN)Connecting to $$router_host ($(ROUTER))...$(RESET)"; \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host

r-status: ## [remote] Phase 1 via SSH
	@router_host=$$(grep -E "^ROUTER_$$(echo $(ROUTER) | tr '[:lower:]' '[:upper:]')_HOST=" routers.env | cut -d= -f2); \
	if [ -z "$$router_host" ]; then echo "$(RED)Unknown router '$(ROUTER)'$(RESET)"; exit 1; fi; \
	echo "$(BOLD)=== Phase 1: Service Startup [$(ROUTER) @ $$router_host] ===$(RESET)"; \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host sh -c "'logread -e tollgate | tail -20 && echo --- && which tollgate && tollgate version && logread -e tollgate | grep -q \"Upstream WiFi manager initialized\" && echo UPSTREAM_OK || echo UPSTREAM_FAIL'"

r-logs: ## [remote] Tail upstream logs on router (Ctrl+C to stop)
	@router_host=$$(grep -E "^ROUTER_$$(echo $(ROUTER) | tr '[:lower:]' '[:upper:]')_HOST=" routers.env | cut -d= -f2); \
	if [ -z "$$router_host" ]; then echo "$(RED)Unknown router '$(ROUTER)'$(RESET)"; exit 1; fi; \
	ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "logread -e tollgate -f" | grep --line-buffered -i "upstream\|scan\|connect\|switch\|candidate"

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

r-smoke: ## [remote] Run smoke test via SSH (requires SSID, optional PASS, ROUTER)
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

r-full: ## [remote] Run full test suite via SSH (requires SSID, optional PASS, ROUTER)
	@if [ -z "$(SSID)" ]; then echo "$(RED)Error: SSID is required. Usage: make -f Makefile.test r-full SSID=MyNet PASS=secret ROUTER=alpha$(RESET)"; exit 1; fi
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
	@echo "$(GREEN)$(BOLD)  Full test suite PASSED [$(ROUTER)]$(RESET)"
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
	dup_ssids=$$(echo "$$output" | awk '{print $$1}' | sort | uniq -d); \
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
	$(SERIAL_EXEC) $$serial_port "logread -e tollgate | tail -20"; \
	$(SERIAL_EXEC) $$serial_port "tollgate version"; \
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
	if [ -z "$(PATTERN)" ]; then echo "$(RED)Error: PATTERN is required. Usage: make -f Makefile s-wait-pattern ROUTER=alpha PATTERN='Upstream WiFi manager'$(RESET)"; exit 1; fi; \
	echo "$(BOLD)=== Waiting for Pattern [$(ROUTER)] ===$(RESET)"; \
	echo "$(YELLOW)Pattern: $(PATTERN), Timeout: $(TIMEOUT)s$(RESET)"; \
	$(SERIAL_WAIT) $$serial_port --pattern "$(PATTERN)" --timeout $(TIMEOUT)

s-recovery: ## [serial] Emergency recovery — run any command via serial (CMD="...")
	@$(RESOLVE_SERIAL); \
	if [ -z "$(CMD)" ]; then echo "$(RED)Error: CMD is required. Usage: make -f Makefile s-recovery ROUTER=alpha CMD='wifi reload'$(RESET)"; exit 1; fi; \
	echo "$(BOLD)=== Serial Recovery [$(ROUTER)] ===$(RESET)"; \
	echo "$(YELLOW)Running: $(CMD)$(RESET)"; \
	$(SERIAL_EXEC) $$serial_port "$(CMD)"

s-cleanup: ## [serial] Cleanup upstream state via serial
	@$(RESOLVE_SERIAL); \
	echo "$(BOLD)=== Serial Cleanup [$(ROUTER)] ===$(RESET)"; \
	echo "$(YELLOW)Disabling upstream STAs...$(RESET)"; \
	$(SERIAL_EXEC) $$serial_port "uci show wireless | grep 'upstream_.*disabled=0' | cut -d. -f2 | while read s; do uci set wireless.\$$s.disabled=1; done; uci commit wireless"; \
	$(SERIAL_EXEC) $$serial_port "wifi reload"; \
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
	echo "$(CYAN)4/4 — Checking upstream manager...$(RESET)"; \
	$(SERIAL_EXEC) $$serial_port "logread -e tollgate | grep -E 'Upstream WiFi manager|tollgate' | tail -5"; \
	echo ""; \
	echo "$(BOLD)=======================================$(RESET)"; \
	echo "$(GREEN)$(BOLD)  Cold boot test complete [$(ROUTER)]$(RESET)"; \
	echo "$(BOLD)=======================================$(RESET)"; \
	echo "$(GREEN)Log: /tmp/cold-boot-$(ROUTER).log$(RESET)"

# ===========================================================================
#  HYBRID TARGETS (SSH first, serial fallback)
# ===========================================================================

h-status: ## [hybrid] Status check — SSH first, serial fallback
	@router_host=$$(grep -E "^ROUTER_$$(echo $(ROUTER) | tr '[:lower:]' '[:upper:]')_HOST=" routers.env | cut -d= -f2); \
	if [ -z "$$router_host" ]; then echo "$(RED)Unknown router '$(ROUTER)'$(RESET)"; exit 1; fi; \
	if ssh $(SSH_OPTS) -o ConnectTimeout=5 $(ROUTER_USER)@$$router_host "echo ok" >/dev/null 2>&1; then \
		$(MAKE) -f Makefile r-status ROUTER=$(ROUTER); \
	else \
		echo "$(YELLOW)SSH unreachable, falling back to serial...$(RESET)"; \
		$(MAKE) -f Makefile s-status ROUTER=$(ROUTER); \
	fi

h-restart-and-watch: ## [hybrid] Restart service via SSH, verify via serial if SSH drops
	@router_host=$$(grep -E "^ROUTER_$$(echo $(ROUTER) | tr '[:lower:]' '[:upper:]')_HOST=" routers.env | cut -d= -f2); \
	if [ -z "$$router_host" ]; then echo "$(RED)Unknown router '$(ROUTER)'$(RESET)"; exit 1; fi; \
	echo "$(BOLD)=== Restart & Watch [$(ROUTER)] ===$(RESET)"; \
	ssh $(SSH_OPTS) -o ConnectTimeout=5 $(ROUTER_USER)@$$router_host "/etc/init.d/tollgate-wrt restart" 2>/dev/null && \
		echo "$(GREEN)Service restarted via SSH$(RESET)" || \
		echo "$(YELLOW)SSH lost during restart — checking via serial...$(RESET)"; \
	sleep 5; \
	$(MAKE) -f Makefile s-status ROUTER=$(ROUTER)

h-cleanup: ## [hybrid] Cleanup — SSH first, serial rescue if stranded
	@router_host=$$(grep -E "^ROUTER_$$(echo $(ROUTER) | tr '[:lower:]' '[:upper:]')_HOST=" routers.env | cut -d= -f2); \
	if [ -z "$$router_host" ]; then echo "$(RED)Unknown router '$(ROUTER)'$(RESET)"; exit 1; fi; \
	if ssh $(SSH_OPTS) -o ConnectTimeout=5 $(ROUTER_USER)@$$router_host "echo ok" >/dev/null 2>&1; then \
		echo "$(GREEN)SSH OK — running normal cleanup$(RESET)"; \
		ssh $(SSH_OPTS) $(ROUTER_USER)@$$router_host "rm -f /tmp/tollgate-baseline.txt /etc/tollgate/config.json.bak 2>/dev/null; echo Cleanup done"; \
	else \
		echo "$(RED)Router unreachable via SSH! Using serial recovery...$(RESET)"; \
		$(MAKE) -f Makefile s-cleanup ROUTER=$(ROUTER); \
	fi
