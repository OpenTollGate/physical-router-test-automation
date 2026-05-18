include boards.env

BOARD_LOCK := esp32.lock

.PHONY: help flash-a flash-b monitor-a monitor-b \
        connect-a connect-b disconnect \
        test-discovery-a test-discovery-b test-mints-a test-mints-b \
        test-multi-mint-a test-multi-mint-b test-all-boards \
        test-cvm-a test-cvm-b test-cvm-mcp-a \
        cvm-pubkey-a cvm-pubkey-b \
        lock unlock force-unlock lock-status \
        build

# --- Mutex: require lock for any target that touches hardware ---
define require_lock
	@if [ ! -f "$(BOARD_LOCK)" ]; then \
		echo "$(RED)$(BOLD)Board not locked — run 'make lock PHASE=\"description\"' first$(RESET)"; \
		echo "$(YELLOW)Other LLM sessions may be using the boards.$(RESET)"; \
		exit 1; \
	fi
endef

# ===========================================================================
#  HELP (no lock needed)
# ===========================================================================

help: ## Show ESP32 board test targets
	@echo "$(BOLD)ESP32 Board Test Targets$(RESET)"
	@echo "$(BOLD)========================$(RESET)"
	@echo ""
	@echo "$(CYAN)--- Flash & Monitor (requires lock) ---$(RESET)"
	@echo "  make flash-a            # Flash Board A ($(BOARD_A_PORT))"
	@echo "  make flash-b            # Flash Board B ($(BOARD_B_PORT))"
	@echo "  make monitor-a          # Serial monitor Board A"
	@echo "  make monitor-b          # Serial monitor Board B"
	@echo ""
	@echo "$(CYAN)--- Network (requires lock) ---$(RESET)"
	@echo "  make connect-a          # Connect laptop to Board A AP"
	@echo "  make connect-b          # Connect laptop to Board B AP"
	@echo "  make disconnect         # Disconnect from board AP"
	@echo ""
	@echo "$(CYAN)--- Tests (requires lock) ---$(RESET)"
	@echo "  make test-discovery-a   # Test multi-mint discovery on Board A"
	@echo "  make test-discovery-b   # Test multi-mint discovery on Board B"
	@echo "  make test-mints-a       # Test /mints endpoint on Board A"
	@echo "  make test-mints-b       # Test /mints endpoint on Board B"
	@echo "  make test-multi-mint-a  # Full multi-mint test suite on Board A"
	@echo "  make test-multi-mint-b  # Full multi-mint test suite on Board B"
	@echo "  make test-all-boards    # Test both boards"
	@echo ""
	@echo "$(CYAN)--- ContextVM tests (requires lock) ---$(RESET)"
	@echo "  make test-cvm-a         # CVM announcement integration test on Board A"
	@echo "  make test-cvm-b         # CVM announcement integration test on Board B"
	@echo "  make test-cvm-mcp-a     # End-to-end MCP tools/call test on Board A"
	@echo "  make cvm-pubkey-a       # Print Board A's CVM npub (no lock)"
	@echo "  make cvm-pubkey-b       # Print Board B's CVM npub (no lock)"
	@echo ""
	@echo "$(CYAN)--- Board mutex ---$(RESET)"
	@echo "  make lock               # Acquire board lock (PHASE='description')"
	@echo "  make unlock             # Release board lock"
	@echo "  make lock-status        # Show current lock status (no lock)"
	@echo "  make force-unlock       # Force-release (caution)"
	@echo ""
	@echo "$(CYAN)--- Build (no lock needed) ---$(RESET)"
	@echo "  make build              # Build firmware (no lock)"

# ===========================================================================
#  BOARD MUTEX
# ===========================================================================

lock: ## Acquire board lock (set PHASE="description")
	@if [ -f "$(BOARD_LOCK)" ]; then \
		echo "$(RED)$(BOLD)Cannot acquire lock — boards already locked:$(RESET)"; \
		echo ""; \
		grep "^branch:" $(BOARD_LOCK) | sed 's/^/  /'; \
		grep "^phase:" $(BOARD_LOCK) | sed 's/^/  /'; \
		grep "^timestamp:" $(BOARD_LOCK) | sed 's/^/  /'; \
		echo ""; \
		echo "$(YELLOW)Use 'make force-unlock' to override (with caution).$(RESET)"; \
		exit 1; \
	fi; \
	branch=$$(git branch --show-current 2>/dev/null || echo "unknown"); \
	echo "locked: true" > $(BOARD_LOCK); \
	echo "branch: $$branch" >> $(BOARD_LOCK); \
	echo "session: $$USER@$$HOSTNAME" >> $(BOARD_LOCK); \
	echo "timestamp: $$(date -u '+%Y-%m-%dT%H:%M:%SZ')" >> $(BOARD_LOCK); \
	echo "phase: $(PHASE)" >> $(BOARD_LOCK); \
	echo "$(GREEN)$(BOLD)Board lock acquired$(RESET)"; \
	cat $(BOARD_LOCK)

unlock: ## Release board lock
	@if [ ! -f "$(BOARD_LOCK)" ]; then \
		echo "$(YELLOW)No lock file found — already unlocked.$(RESET)"; \
		exit 0; \
	fi; \
	echo "$(YELLOW)Releasing board lock...$(RESET)"; \
	rm -f $(BOARD_LOCK); \
	echo "$(GREEN)Board lock released.$(RESET)"

force-unlock: ## Force-release board lock (use with caution)
	@if [ ! -f "$(BOARD_LOCK)" ]; then \
		echo "$(YELLOW)No lock file found — already unlocked.$(RESET)"; \
		exit 0; \
	fi; \
	echo "$(RED)$(BOLD)WARNING: Force-releasing board lock!$(RESET)"; \
	echo "$(RED)Previous holder:$(RESET)"; \
	grep "^branch:" $(BOARD_LOCK) | sed 's/^/  /'; \
	grep "^phase:" $(BOARD_LOCK) | sed 's/^/  /'; \
	grep "^timestamp:" $(BOARD_LOCK) | sed 's/^/  /'; \
	rm -f $(BOARD_LOCK); \
	echo "$(GREEN)Board lock force-released.$(RESET)"

lock-status: ## Show current board lock status (no lock needed)
	@if [ ! -f "$(BOARD_LOCK)" ]; then \
		echo "$(GREEN)Boards are unlocked — available for use.$(RESET)"; \
	else \
		echo "$(YELLOW)$(BOLD)Boards are locked:$(RESET)"; \
		echo ""; \
		cat $(BOARD_LOCK); \
	fi

# ===========================================================================
#  BUILD (no lock needed — doesn't touch hardware)
# ===========================================================================

build: ## Build firmware
	@echo "$(CYAN)Building firmware...$(RESET)"
	@bash -c '$(ESP_IDF_SH) && cd $(FIRMWARE_DIR) && idf.py build 2>&1 | tail -5'
	@echo "$(GREEN)Build complete$(RESET)"

# ===========================================================================
#  FLASH (requires lock)
# ===========================================================================

flash-a: ## Flash Board A
	$(call require_lock)
	@echo "$(CYAN)Flashing Board A ($(BOARD_A_PORT))...$(RESET)"
	@bash -c '$(ESP_IDF_SH) && cd $(FIRMWARE_DIR) && idf.py -p $(BOARD_A_PORT) flash 2>&1 | tail -10'
	@echo "$(GREEN)Board A flashed$(RESET)"

flash-b: ## Flash Board B
	$(call require_lock)
	@echo "$(CYAN)Flashing Board B ($(BOARD_B_PORT))...$(RESET)"
	@bash -c '$(ESP_IDF_SH) && cd $(FIRMWARE_DIR) && idf.py -p $(BOARD_B_PORT) flash 2>&1 | tail -10'
	@echo "$(GREEN)Board B flashed$(RESET)"

# ===========================================================================
#  MONITOR (requires lock — occupies serial port)
# ===========================================================================

monitor-a: ## Serial monitor Board A
	$(call require_lock)
	@bash -c '$(ESP_IDF_SH) && cd $(FIRMWARE_DIR) && idf.py -p $(BOARD_A_PORT) monitor'

monitor-b: ## Serial monitor Board B
	$(call require_lock)
	@bash -c '$(ESP_IDF_SH) && cd $(FIRMWARE_DIR) && idf.py -p $(BOARD_B_PORT) monitor'

# ===========================================================================
#  NETWORK (requires lock — changes host WiFi)
# ===========================================================================

connect-a: ## Connect laptop to Board A AP
	$(call require_lock)
	@echo "$(CYAN)Connecting to $(BOARD_A_SSID)...$(RESET)"
	@nmcli device wifi connect "$(BOARD_A_SSID)" ifname wlan0 2>/dev/null || \
	nmcli connection up "$(BOARD_A_UUID)" 2>/dev/null || \
	echo "$(YELLOW)Failed to connect$(RESET)"
	@sleep 3
	@echo "$(GREEN)Connected — Board A AP IP: $(BOARD_A_IP)$(RESET)"

connect-b: ## Connect laptop to Board B AP
	$(call require_lock)
	@echo "$(CYAN)Connecting to $(BOARD_B_SSID)...$(RESET)"
	@nmcli device wifi connect "$(BOARD_B_SSID)" ifname wlan0 2>/dev/null || \
	nmcli connection up "$(BOARD_B_UUID)" 2>/dev/null || \
	echo "$(YELLOW)Failed to connect$(RESET)"
	@sleep 3
	@echo "$(GREEN)Connected — Board B AP IP: $(BOARD_B_IP)$(RESET)"

disconnect: ## Disconnect from board AP
	@nmcli connection show --active | grep -q TollGate && \
		nmcli connection down $$(nmcli -t -f NAME connection show --active | grep TollGate | head -1) || \
		echo "Not connected to any TollGate AP"
	@echo "$(GREEN)Disconnected$(RESET)"

# ===========================================================================
#  MULTI-MINT TESTS (requires lock via connect-* dependency)
# ===========================================================================

test-discovery-a: connect-a
	@$(MAKE) _test-discovery IP=$(BOARD_A_IP)

test-discovery-b: connect-b
	@$(MAKE) _test-discovery IP=$(BOARD_B_IP)

_test-discovery:
	@echo ""
	@echo "$(BOLD)=== Testing discovery endpoint on $(IP) ===$(RESET)"
	@echo ""
	@echo "$(CYAN)GET http://$(IP):2121/$(RESET)"
	@curl -s --connect-timeout 5 "http://$(IP):2121/" | python3 -m json.tool 2>/dev/null || \
		echo "$(RED)Failed to fetch discovery$(RESET)"
	@echo ""
	@echo "$(CYAN)Checking for multiple price_per_step tags...$(RESET)"
	@PRICE_COUNT=$$(curl -s --connect-timeout 5 "http://$(IP):2121/" | python3 -c \
		"import sys,json; d=json.load(sys.stdin); tags=[t for t in d.get('tags',[]) if t[0]=='price_per_step']; print(len(tags))" 2>/dev/null) && \
		echo "$(GREEN)Found $$PRICE_COUNT price_per_step tag(s)$(RESET)" || \
		echo "$(RED)Failed to count price_per_step tags$(RESET)"

test-mints-a: connect-a
	@$(MAKE) _test-mints IP=$(BOARD_A_IP)

test-mints-b: connect-b
	@$(MAKE) _test-mints IP=$(BOARD_B_IP)

_test-mints:
	@echo ""
	@echo "$(BOLD)=== Testing /mints endpoint on $(IP) ===$(RESET)"
	@echo ""
	@echo "$(CYAN)GET http://$(IP):2121/mints$(RESET)"
	@curl -s --connect-timeout 5 "http://$(IP):2121/mints" | python3 -m json.tool 2>/dev/null || \
		echo "$(RED)Failed to fetch mints$(RESET)"
	@echo ""
	@REACHABLE=$$(curl -s --connect-timeout 5 "http://$(IP):2121/mints" | python3 -c \
		"import sys,json; mints=json.load(sys.stdin); r=[m['url'] for m in mints if m.get('reachable')]; print(len(r), 'reachable:'); print('\\n'.join(['  '+u for u in r]))" 2>/dev/null) && \
		echo "$(GREEN)$$REACHABLE$(RESET)" || \
		echo "$(RED)Failed to parse mint status$(RESET)"

test-multi-mint-a: connect-a
	@$(MAKE) _test-multi-mint IP=$(BOARD_A_IP) BOARD=A

test-multi-mint-b: connect-b
	@$(MAKE) _test-multi-mint IP=$(BOARD_B_IP) BOARD=B

_test-multi-mint:
	@echo ""
	@echo "$(BOLD)=======================================$(RESET)"
	@echo "$(BOLD)  Multi-Mint Test Suite — Board $(BOARD)$(RESET)"
	@echo "$(BOLD)=======================================$(RESET)"
	@echo ""
	@echo "$(CYAN)1/5 — Discovery endpoint...$(RESET)"
	@$(MAKE) _test-discovery IP=$(IP)
	@echo ""
	@echo "$(CYAN)2/5 — Mints endpoint...$(RESET)"
	@$(MAKE) _test-mints IP=$(IP)
	@echo ""
	@echo "$(CYAN)3/5 — Captive portal mint list...$(RESET)"
	@curl -s --connect-timeout 5 "http://$(IP)/" | grep -c "mint-dot" && \
		echo "$(GREEN)Captive portal has mint list$(RESET)" || \
		echo "$(YELLOW)Captive portal may not have mint list (no internet yet?)$(RESET)"
	@echo ""
	@echo "$(CYAN)4/5 — Wallet endpoint...$(RESET)"
	@curl -s --connect-timeout 5 "http://$(IP):2121/wallet" | python3 -m json.tool 2>/dev/null || \
		echo "$(YELLOW)Wallet not initialized (no internet)$(RESET)"
	@echo ""
	@echo "$(CYAN)5/5 — Mint health (wait 30s for probes)...$(RESET)"
	@echo "Waiting for initial health probes to complete..."
	@sleep 30
	@$(MAKE) _test-mints IP=$(IP)
	@echo ""
	@echo "$(BOLD)=======================================$(RESET)"
	@echo "$(GREEN)$(BOLD)  Multi-Mint Test Complete — Board $(BOARD)$(RESET)"
	@echo "$(BOLD)=======================================$(RESET)"

test-all-boards: ## Test both boards sequentially
	@echo "$(BOLD)Testing Board A...$(RESET)"
	@$(MAKE) test-multi-mint-a
	@echo ""
	@echo "$(BOLD)Testing Board B...$(RESET)"
	@$(MAKE) test-multi-mint-b

# ===========================================================================
#  ContextVM (MCP over Nostr) Tests
# ===========================================================================

CVM_NSEC_DEFAULT = a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2
CVM_NSEC_A       = 9af47906b45aca5e238390f3d03c8274e154198e81aa2095065627d1e61ca968
CVM_RELAY        = wss://relay.primal.net

# pubkey targets are read-only — no lock needed
cvm-pubkey-a: ## Print Board A's CVM npub (from board nsec)
	@echo "$(BOLD)=== Board A ContextVM npub ===$(RESET)"
	@NPUB=$$(nak key public $(CVM_NSEC_A)) && \
		echo "  hex:    $$NPUB" && \
		echo "  bech32: $$(nak encode npub $$NPUB)" && \
		echo "" && \
		echo "  Search: https://contextvm.org/servers"

cvm-pubkey-b: ## Print Board B's CVM npub (from default nsec)
	@echo "$(BOLD)=== Board B ContextVM npub ===$(RESET)"
	@NPUB=$$(nak key public $(CVM_NSEC_DEFAULT)) && \
		echo "  hex:    $$NPUB" && \
		echo "  bech32: $$(nak encode npub $$NPUB)" && \
		echo "" && \
		echo "  Search: https://contextvm.org/servers"

# CVM tests need board access — lock via connect-* dependency
test-cvm-a: connect-a ## CVM announcement integration test on Board A
	@$(MAKE) _test-cvm NSEC=$(CVM_NSEC_A) IP=$(BOARD_A_IP)

test-cvm-b: connect-b ## CVM announcement integration test on Board B
	@$(MAKE) _test-cvm NSEC=$(CVM_NSEC_DEFAULT) IP=$(BOARD_B_IP)

_test-cvm:
	@echo ""
	@echo "$(BOLD)=======================================$(RESET)"
	@echo "$(BOLD)  CVM Integration Test$(RESET)"
	@echo "$(BOLD)=======================================$(RESET)"
	@NPUB=$$(nak key public $(NSEC)) && \
		echo "Board npub: $$NPUB" && \
		echo "Relay: $(CVM_RELAY)" && \
		echo ""
	@echo "$(CYAN)1/4 — API reachability...$(RESET)"
	@curl -s --connect-timeout 5 "http://$(IP):2121/usage" | python3 -m json.tool > /dev/null 2>&1 && \
		echo "$(GREEN)  ✓ API reachable$(RESET)" || \
		echo "$(RED)  ✗ API not reachable$(RESET)"
	@echo ""
	@echo "$(CYAN)2/4 — Kind 11316 server announcement...$(RESET)"
	@NPUB=$$(nak key public $(NSEC)) && \
		RESULT=$$(timeout 8 nak req -k 11316 -a $$NPUB -l 1 $(CVM_RELAY) 2>/dev/null) && \
		if echo "$$RESULT" | grep -q '"kind"'; then \
			echo "$(GREEN)  ✓ Kind 11316 found on $(CVM_RELAY)$(RESET)"; \
			echo "$$RESULT" | python3 -c "import sys,json; d=json.loads(sys.stdin.read()); print('    name:', [t[1] for t in d.get('tags',[]) if t[0]=='name'][0])" 2>/dev/null; \
		else \
			echo "$(RED)  ✗ Kind 11316 NOT found$(RESET)"; \
		fi
	@echo ""
	@echo "$(CYAN)3/4 — Kind 11317 tools list...$(RESET)"
	@NPUB=$$(nak key public $(NSEC)) && \
		RESULT=$$(timeout 8 nak req -k 11317 -a $$NPUB -l 1 $(CVM_RELAY) 2>/dev/null) && \
		if echo "$$RESULT" | grep -q '"kind"'; then \
			TOOL_COUNT=$$(echo "$$RESULT" | python3 -c "import sys,json; d=json.loads(sys.stdin.read()); tools=json.loads(d['content'])['tools']; print(len(tools))" 2>/dev/null) && \
			echo "$(GREEN)  ✓ Kind 11317 found — $$TOOL_COUNT tools$(RESET)"; \
		else \
			echo "$(RED)  ✗ Kind 11317 NOT found$(RESET)"; \
		fi
	@echo ""
	@echo "$(CYAN)4/4 — Kind 10002 relay list...$(RESET)"
	@NPUB=$$(nak key public $(NSEC)) && \
		RESULT=$$(timeout 8 nak req -k 10002 -a $$NPUB -l 1 $(CVM_RELAY) 2>/dev/null) && \
		if echo "$$RESULT" | grep -q '"kind"'; then \
			echo "$(GREEN)  ✓ Kind 10002 found on $(CVM_RELAY)$(RESET)"; \
		else \
			echo "$(RED)  ✗ Kind 10002 NOT found$(RESET)"; \
		fi
	@echo ""
	@echo "$(BOLD)=======================================$(RESET)"

test-cvm-mcp-a: connect-a ## End-to-end MCP tools/call via CVM on Board A
	@echo ""
	@echo "$(BOLD)=======================================$(RESET)"
	@echo "$(BOLD)  CVM MCP Request/Reply Test$(RESET)"
	@echo "$(BOLD)=======================================$(RESET)"
	@NPUB=$$(nak key public $(CVM_NSEC_A)) && \
		echo "Board npub: $$NPUB" && \
		CONTENT="$$(echo '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"get_config","arguments":{}}}' | jq -c .)" && \
		echo "" && \
		echo "$(CYAN)Publishing kind 25910 tools/call get_config...$(RESET)" && \
		EVENT=$$(nak event --kind 25910 --tag p=$$NPUB --content "$$CONTENT" $(CVM_RELAY) 2>/dev/null) && \
		echo "  Event: $$(echo $$EVENT | python3 -c 'import sys,json;d=json.loads(sys.stdin.read());print(d["id"][:16]+"...")' 2>/dev/null || echo 'failed')" && \
		echo "" && \
		echo "$(CYAN)Waiting 5s for board to process and respond...$(RESET)" && \
		sleep 5 && \
		echo "" && \
		echo "$(CYAN)Checking for kind 25910 response...$(RESET)" && \
		RESP=$$(timeout 8 nak req -k 25910 -a $$NPUB -l 5 $(CVM_RELAY) 2>/dev/null) && \
		if echo "$$RESP" | grep -q '"kind"'; then \
			echo "$(GREEN)  ✓ Response received!$(RESET)"; \
			echo "$$RESP" | python3 -c "import sys,json; [print('    ',json.dumps(json.loads(l)['content'][:100])) for l in sys.stdin if 'kind' in l]" 2>/dev/null; \
		else \
			echo "$(RED)  ✗ No response received$(RESET)"; \
		fi
	@echo ""
	@echo "$(BOLD)=======================================$(RESET)"
