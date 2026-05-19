include boards.env

.PHONY: help flash-a flash-b flash-c monitor-a monitor-b monitor-c \
        connect-a connect-b disconnect \
        test-discovery-a test-discovery-b test-mints-a test-mints-b \
        test-multi-mint-a test-multi-mint-b test-all-boards \
        test-cvm-a test-cvm-b test-cvm-mcp-a \
        cvm-pubkey-a cvm-pubkey-b \
        lock-a lock-b lock-c unlock-a unlock-b unlock-c \
        lock-status force-unlock-a force-unlock-b force-unlock-c \
        build detect-wpa-security generate-spiffs \
        flash-spiffs-a flash-spiffs-b flash-spiffs-c \
        arch-build arch-flash arch-generate-spiffs arch-flash-spiffs \
        arch-wait-ready arch-connect arch-disconnect \
        arch-test-smoke arch-test-network arch-test-api arch-test-dns-fw \
        arch-test-reset arch-test-session arch-test-phase2 arch-test-full \
        arch-test-cleanup arch-test-api-quick \
        relay-build relay-flash-b relay-connect-b \
        relay-test-smoke relay-test-nip11 relay-test-pubsub \
        relay-test-sync relay-test-full

define require_lock_a
	@if [ ! -f "$(LOCK_DIR)/board-a.lock" ]; then \
		echo "$(RED)$(BOLD)Board A not locked — run 'make lock-a PHASE=\"description\"' first$(RESET)"; \
		echo "$(YELLOW)Another LLM session may be using Board A.$(RESET)"; \
		exit 1; \
	fi
endef

define require_lock_b
	@if [ ! -f "$(LOCK_DIR)/board-b.lock" ]; then \
		echo "$(RED)$(BOLD)Board B not locked — run 'make lock-b PHASE=\"description\"' first$(RESET)"; \
		echo "$(YELLOW)Another LLM session may be using Board B.$(RESET)"; \
		exit 1; \
	fi
endef

define require_lock_c
	@if [ ! -f "$(LOCK_DIR)/board-c.lock" ]; then \
		echo "$(RED)$(BOLD)Board C not locked — run 'make lock-c PHASE=\"description\"' first$(RESET)"; \
		echo "$(YELLOW)Another LLM session may be using Board C.$(RESET)"; \
		exit 1; \
	fi
endef

define acquire_lock
	@if [ -f "$(LOCK_DIR)/$(1).lock" ]; then \
		owner=$$(grep '^session:' $(LOCK_DIR)/$(1).lock | head -1 | sed 's/session: *//' | cut -d@ -f1); \
		if [ "$$owner" = "$$USER" ]; then \
			echo "$(YELLOW)$(1) already locked by this session — refreshing lock$(RESET)"; \
		else \
			echo "$(RED)$(BOLD)Cannot acquire lock — $(1) locked by another session:$(RESET)"; \
			echo ""; \
			cat $(LOCK_DIR)/$(1).lock | sed 's/^/  /'; \
			echo ""; \
			echo "$(YELLOW)Use 'make force-unlock-$(1)' to override.$(RESET)"; \
			exit 1; \
		fi; \
	fi; \
	branch=$$(git branch --show-current 2>/dev/null || echo "unknown"); \
	worktree=$$(pwd); \
	echo "locked: true" > $(LOCK_DIR)/$(1).lock; \
	echo "board: $(1)" >> $(LOCK_DIR)/$(1).lock; \
	echo "port: $(2)" >> $(LOCK_DIR)/$(1).lock; \
	echo "branch: $$branch" >> $(LOCK_DIR)/$(1).lock; \
	echo "worktree: $$worktree" >> $(LOCK_DIR)/$(1).lock; \
	echo "session: $$USER@$$(hostname)" >> $(LOCK_DIR)/$(1).lock; \
	echo "timestamp: $$(date -u '+%Y-%m-%dT%H:%M:%SZ')" >> $(LOCK_DIR)/$(1).lock; \
	echo "phase: $(PHASE)" >> $(LOCK_DIR)/$(1).lock; \
	echo "$(GREEN)$(BOLD)Board $(1) lock acquired$(RESET)"; \
	cat $(LOCK_DIR)/$(1).lock
endef

# ===========================================================================
#  HELP (no lock needed)
# ===========================================================================

help: ## Show ESP32 board test targets
	@echo "$(BOLD)ESP32 Board Test Targets$(RESET)"
	@echo "$(BOLD)========================$(RESET)"
	@echo ""
	@echo "$(CYAN)--- Per-board locks ---$(RESET)"
	@echo "  make lock-a             # Acquire Board A lock (PHASE='desc')"
	@echo "  make lock-b             # Acquire Board B lock"
	@echo "  make lock-c             # Acquire Board C lock"
	@echo "  make unlock-a           # Release Board A lock"
	@echo "  make unlock-b           # Release Board B lock"
	@echo "  make unlock-c           # Release Board C lock"
	@echo "  make lock-status        # Show all lock statuses"
	@echo ""
	@echo "$(CYAN)--- Flash & Monitor (requires board lock) ---$(RESET)"
	@echo "  make flash-a            # Flash Board A ($(BOARD_A_PORT))"
	@echo "  make flash-b            # Flash Board B ($(BOARD_B_PORT))"
	@echo "  make flash-c            # Flash Board C ($(BOARD_C_PORT))"
	@echo "  make monitor-a          # Serial monitor Board A"
	@echo "  make monitor-b          # Serial monitor Board B"
	@echo "  make monitor-c          # Serial monitor Board C"
	@echo ""
	@echo "$(CYAN)--- Network (requires board lock) ---$(RESET)"
	@echo "  make connect-a          # Connect laptop to Board A AP"
	@echo "  make connect-b          # Connect laptop to Board B AP"
	@echo "  make disconnect         # Disconnect from board AP"
	@echo ""
	@echo "$(CYAN)--- Tests (requires board lock) ---$(RESET)"
	@echo "  make test-discovery-a   # Test multi-mint discovery on Board A"
	@echo "  make test-discovery-b   # Test multi-mint discovery on Board B"
	@echo "  make test-mints-a       # Test /mints endpoint on Board A"
	@echo "  make test-mints-b       # Test /mints endpoint on Board B"
	@echo "  make test-multi-mint-a  # Full multi-mint test suite on Board A"
	@echo "  make test-multi-mint-b  # Full multi-mint test suite on Board B"
	@echo "  make test-all-boards    # Test both boards (needs lock-a + lock-b)"
	@echo ""
	@echo "$(CYAN)--- ContextVM tests (requires board lock) ---$(RESET)"
	@echo "  make test-cvm-a         # CVM announcement test on Board A"
	@echo "  make test-cvm-b         # CVM announcement test on Board B"
	@echo "  make test-cvm-mcp-a     # MCP tools/call test on Board A"
	@echo "  make cvm-pubkey-a       # Print Board A CVM npub (no lock)"
	@echo "  make cvm-pubkey-b       # Print Board B CVM npub (no lock)"
	@echo ""
	@echo "$(CYAN)--- Build (no lock needed) ---$(RESET)"
	@echo "  make build              # Build firmware (no lock)"
	@echo ""
	@echo "$(CYAN)--- SPIFFS + WPA Auto-Detect (no lock for gen, lock for flash) ---$(RESET)"
	@echo "  make detect-wpa-security # Scan WiFi, print WPA2/WPA3 mode"
	@echo "  make generate-spiffs    # Build SPIFFS image with detected WPA mode"
	@echo "  make flash-spiffs-a     # Flash SPIFFS to Board A"
	@echo "  make flash-spiffs-b     # Flash SPIFFS to Board B"
	@echo "  make flash-spiffs-c     # Flash SPIFFS to Board C"
	@echo ""
	@echo "$(CYAN)--- Arch (tollgate_core) tests (requires lock-b) ---$(RESET)"
	@echo "  make arch-build           # Build arch firmware (no lock)"
	@echo "  make arch-flash           # Flash arch firmware + SPIFFS (requires lock-b)"
	@echo "  make arch-wait-ready      # Poll :2121/usage until board ready"
	@echo "  make arch-connect         # WiFi connect to board AP"
	@echo "  make arch-test-api-quick  # Quick 5-endpoint sanity check"
	@echo "  make arch-test-full       # Full E2E test suite (~4min)"
	@echo ""
	@echo "$(CYAN)--- Local Relay tests (Board B, requires lock-b) ---$(RESET)"
	@echo "  make relay-build        # Build relay firmware (no lock)"
	@echo "  make relay-flash-b      # Flash relay firmware to Board B"
	@echo "  make relay-test-smoke   # Verify relay port 4869 reachable"
	@echo "  make relay-test-nip11   # Test NIP-11 info document"
	@echo "  make relay-test-pubsub  # WebSocket publish + subscribe test"
	@echo "  make relay-test-sync    # Verify sync to public relays"
	@echo "  make relay-test-full    # Run all relay tests (~1min)"

# ===========================================================================
#  PER-BOARD LOCKS
# ===========================================================================

lock-a: ## Acquire Board A lock
	$(call acquire_lock,board-a,$(BOARD_A_PORT))

lock-b: ## Acquire Board B lock
	$(call acquire_lock,board-b,$(BOARD_B_PORT))

lock-c: ## Acquire Board C lock
	$(call acquire_lock,board-c,$(BOARD_C_PORT))

unlock-a: ## Release Board A lock
	@if [ ! -f "$(LOCK_DIR)/board-a.lock" ]; then \
		echo "$(YELLOW)Board A not locked — already available.$(RESET)"; \
		exit 0; \
	fi; \
	rm -f $(LOCK_DIR)/board-a.lock; \
	echo "$(GREEN)Board A lock released.$(RESET)"

unlock-b: ## Release Board B lock
	@if [ ! -f "$(LOCK_DIR)/board-b.lock" ]; then \
		echo "$(YELLOW)Board B not locked — already available.$(RESET)"; \
		exit 0; \
	fi; \
	rm -f $(LOCK_DIR)/board-b.lock; \
	echo "$(GREEN)Board B lock released.$(RESET)"

unlock-c: ## Release Board C lock
	@if [ ! -f "$(LOCK_DIR)/board-c.lock" ]; then \
		echo "$(YELLOW)Board C not locked — already available.$(RESET)"; \
		exit 0; \
	fi; \
	rm -f $(LOCK_DIR)/board-c.lock; \
	echo "$(GREEN)Board C lock released.$(RESET)"

force-unlock-a: ## Force-release Board A lock
	@if [ ! -f "$(LOCK_DIR)/board-a.lock" ]; then \
		echo "$(YELLOW)Board A not locked.$(RESET)"; exit 0; \
	fi; \
	echo "$(RED)$(BOLD)WARNING: Force-releasing Board A lock!$(RESET)"; \
	cat $(LOCK_DIR)/board-a.lock | sed 's/^/  /'; \
	rm -f $(LOCK_DIR)/board-a.lock; \
	echo "$(GREEN)Board A lock force-released.$(RESET)"

force-unlock-b: ## Force-release Board B lock
	@if [ ! -f "$(LOCK_DIR)/board-b.lock" ]; then \
		echo "$(YELLOW)Board B not locked.$(RESET)"; exit 0; \
	fi; \
	echo "$(RED)$(BOLD)WARNING: Force-releasing Board B lock!$(RESET)"; \
	cat $(LOCK_DIR)/board-b.lock | sed 's/^/  /'; \
	rm -f $(LOCK_DIR)/board-b.lock; \
	echo "$(GREEN)Board B lock force-released.$(RESET)"

force-unlock-c: ## Force-release Board C lock
	@if [ ! -f "$(LOCK_DIR)/board-c.lock" ]; then \
		echo "$(YELLOW)Board C not locked.$(RESET)"; exit 0; \
	fi; \
	echo "$(RED)$(BOLD)WARNING: Force-releasing Board C lock!$(RESET)"; \
	cat $(LOCK_DIR)/board-c.lock | sed 's/^/  /'; \
	rm -f $(LOCK_DIR)/board-c.lock; \
	echo "$(GREEN)Board C lock force-released.$(RESET)"

lock-status: ## Show all board lock statuses
	@echo "$(BOLD)Board Lock Status$(RESET)"
	@echo "$(BOLD)==================$(RESET)"
	@for board in a b c; do \
		if [ -f "$(LOCK_DIR)/board-$$board.lock" ]; then \
			echo ""; \
			echo "$(YELLOW)Board $$board: LOCKED$(RESET)"; \
			cat $(LOCK_DIR)/board-$$board.lock | sed 's/^/  /'; \
		else \
			echo "Board $$board: $(GREEN)available$(RESET)"; \
		fi; \
	done

# ===========================================================================
#  BUILD (no lock needed)
# ===========================================================================

build: ## Build firmware
	@echo "$(CYAN)Building firmware...$(RESET)"
	@bash -c '$(ESP_IDF_SH) && cd $(FIRMWARE_DIR) && idf.py build 2>&1 | tail -5'
	@echo "$(GREEN)Build complete$(RESET)"

# ===========================================================================
#  WPA AUTO-DETECT + SPIFFS GENERATION (no lock needed)
# ===========================================================================

SPIFFS_DIR     := /tmp/tollgate_spiffs
SPIFFS_BIN     := /tmp/tollgate_spiffs.bin
SPIFFS_SIZE    := 0xF0000
SPIFFS_OFFSET  := 0x410000
WIFI_SSID      ?= $(shell grep -oP 'WIFI_SSID\s*=\s*\K.*' $(FIRMWARE_DIR)/.env 2>/dev/null || echo "EnterSSID-2.4GHz")
WIFI_PASSWORD  ?= $(shell grep -oP 'WIFI_PASSWORD\s*=\s*\K.*' $(FIRMWARE_DIR)/.env 2>/dev/null || echo "c03rad0r123")

detect-wpa-security: ## Scan WiFi and detect WPA security mode for configured SSID
	@echo "$(CYAN)Scanning WiFi networks...$(RESET)"
	@SEC=$$(nmcli -t -f SSID,SECURITY dev wifi list 2>/dev/null | \
		grep "^$(WIFI_SSID):" | head -1 | cut -d: -f2); \
	if [ -z "$$SEC" ] || [ "$$SEC" = "--" ]; then \
		echo "$(YELLOW)SSID '$(WIFI_SSID)' not found or open — defaulting to WPA2$(RESET)"; \
		echo "WPA2"; \
	elif echo "$$SEC" | grep -qi "WPA3"; then \
		echo "$(GREEN)Detected: WPA3 ($$SEC)$(RESET)"; \
		echo "WPA3"; \
	else \
		echo "$(GREEN)Detected: WPA2 ($$SEC)$(RESET)"; \
		echo "WPA2"; \
	fi

generate-spiffs: ## Generate SPIFFS image with auto-detected WPA mode
	@echo "$(CYAN)Generating SPIFFS image...$(RESET)"
	@rm -rf $(SPIFFS_DIR) && mkdir -p $(SPIFFS_DIR)
	@WPA_MODE=$$(nmcli -t -f SSID,SECURITY dev wifi list 2>/dev/null | grep "^$(WIFI_SSID):" | head -1 | cut -d: -f2 | grep -qi "WPA3" && echo "WPA3" || echo "WPA2"); \
	echo "$(CYAN)Detected WPA mode: $$WPA_MODE$(RESET)"; \
	if [ -f "$(FIRMWARE_DIR)/main/config.json" ]; then \
		cp "$(FIRMWARE_DIR)/main/config.json" "$(SPIFFS_DIR)/config.json"; \
	else \
		echo '{}' > "$(SPIFFS_DIR)/config.json"; \
	fi; \
	python3 -c "import json; d=json.load(open('$(SPIFFS_DIR)/config.json')); d['wifi_auth_mode']='$$WPA_MODE'; json.dump(d,open('$(SPIFFS_DIR)/config.json','w'),indent=2)"
	@bash -c '$(ESP_IDF_SH) && python3 $$IDF_PATH/components/spiffs/spiffsgen.py $(SPIFFS_SIZE) $(SPIFFS_DIR) $(SPIFFS_BIN)'
	@echo "$(GREEN)SPIFFS image generated: $(SPIFFS_BIN)$(RESET)"
	@echo "$(CYAN)Contents:$(RESET)"
	@cat $(SPIFFS_DIR)/config.json | python3 -m json.tool 2>/dev/null | head -20

flash-spiffs-a: ## Flash SPIFFS to Board A
	$(call require_lock_a)
	@$(MAKE) generate-spiffs
	@echo "$(CYAN)Flashing SPIFFS to Board A ($(BOARD_A_PORT)) at offset $(SPIFFS_OFFSET)...$(RESET)"
	@bash -c '$(ESP_IDF_SH) && esptool.py --chip esp32s3 -p $(BOARD_A_PORT) -b 115200 --after no_reset write_flash $(SPIFFS_OFFSET) $(SPIFFS_BIN) 2>&1 | tail -5'
	@echo "$(GREEN)SPIFFS flashed to Board A$(RESET)"

flash-spiffs-b: ## Flash SPIFFS to Board B
	$(call require_lock_b)
	@$(MAKE) generate-spiffs
	@echo "$(CYAN)Flashing SPIFFS to Board B ($(BOARD_B_PORT)) at offset $(SPIFFS_OFFSET)...$(RESET)"
	@bash -c '$(ESP_IDF_SH) && esptool.py --chip esp32s3 -p $(BOARD_B_PORT) -b 115200 --after no_reset write_flash $(SPIFFS_OFFSET) $(SPIFFS_BIN) 2>&1 | tail -5'
	@echo "$(GREEN)SPIFFS flashed to Board B$(RESET)"

flash-spiffs-c: ## Flash SPIFFS to Board C
	$(call require_lock_c)
	@$(MAKE) generate-spiffs
	@echo "$(CYAN)Flashing SPIFFS to Board C ($(BOARD_C_PORT)) at offset $(SPIFFS_OFFSET)...$(RESET)"
	@bash -c '$(ESP_IDF_SH) && esptool.py --chip esp32s3 -p $(BOARD_C_PORT) -b 115200 --after no_reset write_flash $(SPIFFS_OFFSET) $(SPIFFS_BIN) 2>&1 | tail -5'
	@echo "$(GREEN)SPIFFS flashed to Board C$(RESET)"

# ===========================================================================
#  FLASH (requires per-board lock)
# ===========================================================================

flash-a: ## Flash Board A
	$(call require_lock_a)
	@echo "$(CYAN)Flashing Board A ($(BOARD_A_PORT))...$(RESET)"
	@bash -c '$(ESP_IDF_SH) && cd $(FIRMWARE_DIR) && idf.py -p $(BOARD_A_PORT) flash 2>&1 | tail -10'
	@echo "$(GREEN)Board A flashed$(RESET)"

flash-b: ## Flash Board B
	$(call require_lock_b)
	@echo "$(CYAN)Flashing Board B ($(BOARD_B_PORT))...$(RESET)"
	@bash -c '$(ESP_IDF_SH) && cd $(FIRMWARE_DIR) && idf.py -p $(BOARD_B_PORT) flash 2>&1 | tail -10'
	@echo "$(GREEN)Board B flashed$(RESET)"

flash-c: ## Flash Board C
	$(call require_lock_c)
	@echo "$(CYAN)Flashing Board C ($(BOARD_C_PORT))...$(RESET)"
	@bash -c '$(ESP_IDF_SH) && cd $(FIRMWARE_DIR) && idf.py -p $(BOARD_C_PORT) flash 2>&1 | tail -10'
	@echo "$(GREEN)Board C flashed$(RESET)"

# ===========================================================================
#  MONITOR (requires per-board lock)
# ===========================================================================

monitor-a: ## Serial monitor Board A
	$(call require_lock_a)
	@bash -c '$(ESP_IDF_SH) && cd $(FIRMWARE_DIR) && idf.py -p $(BOARD_A_PORT) monitor'

monitor-b: ## Serial monitor Board B
	$(call require_lock_b)
	@bash -c '$(ESP_IDF_SH) && cd $(FIRMWARE_DIR) && idf.py -p $(BOARD_B_PORT) monitor'

monitor-c: ## Serial monitor Board C
	$(call require_lock_c)
	@bash -c '$(ESP_IDF_SH) && cd $(FIRMWARE_DIR) && idf.py -p $(BOARD_C_PORT) monitor'

# ===========================================================================
#  NETWORK (requires per-board lock)
# ===========================================================================

WIFI_IFACE := $(shell iw dev | awk '/Interface/{print $$2}' | head -1)

connect-a: ## Connect laptop to Board A AP
	$(call require_lock_a)
	@echo "$(CYAN)Connecting to $(BOARD_A_SSID)...$(RESET)"
	@nmcli device wifi connect "$(BOARD_A_SSID)" ifname $(WIFI_IFACE) 2>/dev/null || \
	nmcli connection up "$(BOARD_A_UUID)" 2>/dev/null || \
	echo "$(YELLOW)Failed to connect$(RESET)"
	@sleep 3
	@echo "$(GREEN)Connected — Board A AP IP: $(BOARD_A_IP)$(RESET)"

connect-b: ## Connect laptop to Board B AP
	$(call require_lock_b)
	@echo "$(CYAN)Connecting to $(BOARD_B_SSID)...$(RESET)"
	@nmcli device wifi connect "$(BOARD_B_SSID)" ifname $(WIFI_IFACE) 2>/dev/null || \
	nmcli connection up "$(BOARD_B_UUID)" 2>/dev/null || \
	echo "$(YELLOW)Failed to connect$(RESET)"
	@sleep 3
	@echo "$(GREEN)Connected — Board B AP IP: $(BOARD_B_IP)$(RESET)"

disconnect: ## Disconnect from board AP
	@nmcli connection show --active | grep -q TollGate && \
		nmcli connection down $$(nmcli -t -f NAME connection show --active | grep TollGate | head -1) || \
		echo "Not connected to any TollGate AP"
	@echo "$(GREEN)Disconnected$(RESET)"

_connect-a-if-needed:
	@if ! ping -c 1 -W 2 $(BOARD_A_IP) >/dev/null 2>&1; then \
		echo "$(CYAN)Not connected to Board A — connecting...$(RESET)"; \
		echo "c03rad0r123" | sudo -S nmcli device wifi connect "$(BOARD_A_SSID)" ifname $(WIFI_IFACE) 2>&1 | grep -v "Password:" || true; \
		sleep 3; \
	fi
	@echo "$(GREEN)Board A reachable at $(BOARD_A_IP)$(RESET)"

_connect-b-if-needed:
	@if ! ping -c 1 -W 2 $(BOARD_B_IP) >/dev/null 2>&1; then \
		echo "$(CYAN)Not connected to Board B — connecting...$(RESET)"; \
		echo "c03rad0r123" | sudo -S nmcli device wifi connect "$(BOARD_B_SSID)" ifname $(WIFI_IFACE) 2>&1 | grep -v "Password:" || true; \
		sleep 3; \
	fi
	@echo "$(GREEN)Board B reachable at $(BOARD_B_IP)$(RESET)"

# ===========================================================================
#  MULTI-MINT TESTS (requires per-board lock)
# ===========================================================================

test-discovery-a: ## Test multi-mint discovery on Board A
	$(call require_lock_a)
	@$(MAKE) _connect-a-if-needed
	@$(MAKE) _test-discovery IP=$(BOARD_A_IP)

test-discovery-b: ## Test multi-mint discovery on Board B
	$(call require_lock_b)
	@$(MAKE) _connect-b-if-needed
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

test-mints-a: ## Test /mints endpoint on Board A
	$(call require_lock_a)
	@$(MAKE) _connect-a-if-needed
	@$(MAKE) _test-mints IP=$(BOARD_A_IP)

test-mints-b: ## Test /mints endpoint on Board B
	$(call require_lock_b)
	@$(MAKE) _connect-b-if-needed
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

test-multi-mint-a: ## Full multi-mint test suite on Board A
	$(call require_lock_a)
	@$(MAKE) _connect-a-if-needed
	@$(MAKE) _test-multi-mint IP=$(BOARD_A_IP) BOARD=A

test-multi-mint-b: ## Full multi-mint test suite on Board B
	$(call require_lock_b)
	@$(MAKE) _connect-b-if-needed
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

test-all-boards: ## Test both boards sequentially (needs lock-a + lock-b)
	$(call require_lock_a)
	$(call require_lock_b)
	@echo "$(BOLD)Testing Board A...$(RESET)"
	@$(MAKE) _connect-a-if-needed
	@$(MAKE) _test-multi-mint IP=$(BOARD_A_IP) BOARD=A
	@echo ""
	@echo "$(BOLD)Testing Board B...$(RESET)"
	@$(MAKE) _connect-b-if-needed
	@$(MAKE) _test-multi-mint IP=$(BOARD_B_IP) BOARD=B

# ===========================================================================
#  ContextVM (MCP over Nostr) Tests
# ===========================================================================

CVM_NSEC_DEFAULT = a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2
CVM_NSEC_A       = 9af47906b45aca5e238390f3d03c8274e154198e81aa2095065627d1e61ca968
CVM_RELAY        = wss://relay.primal.net

cvm-pubkey-a: ## Print Board A's CVM npub (no lock needed)
	@echo "$(BOLD)=== Board A ContextVM npub ===$(RESET)"
	@NPUB=$$(nak key public $(CVM_NSEC_A)) && \
		echo "  hex:    $$NPUB" && \
		echo "  bech32: $$(nak encode npub $$NPUB)" && \
		echo "" && \
		echo "  Search: https://contextvm.org/servers"

cvm-pubkey-b: ## Print Board B's CVM npub (no lock needed)
	@echo "$(BOLD)=== Board B ContextVM npub ===$(RESET)"
	@NPUB=$$(nak key public $(CVM_NSEC_DEFAULT)) && \
		echo "  hex:    $$NPUB" && \
		echo "  bech32: $$(nak encode npub $$NPUB)" && \
		echo "" && \
		echo "  Search: https://contextvm.org/servers"

test-cvm-a: ## CVM announcement integration test on Board A
	$(call require_lock_a)
	@$(MAKE) _connect-a-if-needed
	@$(MAKE) _test-cvm NSEC=$(CVM_NSEC_A) IP=$(BOARD_A_IP)

test-cvm-b: ## CVM announcement integration test on Board B
	$(call require_lock_b)
	@$(MAKE) _connect-b-if-needed
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
	@HTTP_CODE=$$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout 5 "http://$(IP):2121/usage") && \
		if [ "$$HTTP_CODE" = "200" ]; then \
			echo "$(GREEN)  ✓ API reachable (HTTP $$HTTP_CODE)$(RESET)"; \
		else \
			echo "$(YELLOW)  ~ API returned HTTP $$HTTP_CODE$(RESET)"; \
		fi || \
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

test-cvm-mcp-a: ## End-to-end MCP tools/call via CVM on Board A
	$(call require_lock_a)
	@$(MAKE) _connect-a-if-needed
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

# ===========================================================================
#  LOCAL RELAY TESTS (Board B, feature/local-relay)
# ===========================================================================

RELAY_FIRMWARE_DIR = /home/c03rad0r/esp32-tollgate-hardening
RELAY_IP           = $(BOARD_B_IP)
RELAY_PORT_NUM     = 4869
RELAY_SSID         = $(BOARD_B_SSID)
RELAY_TESTS_DIR    = /home/c03rad0r/esp32-tollgate/tests/integration

.PHONY: relay-build relay-flash-b relay-connect-b \
        relay-test-smoke relay-test-nip11 relay-test-pubsub \
        relay-test-full

relay-build: ## Build relay firmware (no lock)
	@echo "$(CYAN)Building relay firmware in $(RELAY_FIRMWARE_DIR)...$(RESET)"
	@bash -c '$(ESP_IDF_SH) && cd $(RELAY_FIRMWARE_DIR) && idf.py build 2>&1 | tail -5'
	@echo "$(GREEN)Build complete$(RESET)"

relay-flash-b: ## Flash relay firmware to Board B (requires lock-b)
	$(call require_lock_b)
	@echo "$(CYAN)Flashing relay firmware to Board B ($(BOARD_B_PORT))...$(RESET)"
	@bash -c '$(ESP_IDF_SH) && cd $(RELAY_FIRMWARE_DIR) && idf.py -p $(BOARD_B_PORT) flash 2>&1 | tail -10'
	@echo "$(GREEN)Board B flashed with relay firmware$(RESET)"

relay-connect-b: ## WiFi connect to Board B AP for relay tests (requires lock-b)
	$(call require_lock_b)
	@$(MAKE) _connect-b-if-needed

relay-test-smoke: ## Smoke test: verify relay port 4869 reachable (requires lock-b)
	$(call require_lock_b)
	@$(MAKE) _connect-b-if-needed
	@echo ""
	@echo "$(BOLD)=== Relay Smoke Test ===$(RESET)"
	@echo "$(CYAN)1/3 — Ping Board B...$(RESET)"
	@ping -c 1 -W 3 $(RELAY_IP) >/dev/null 2>&1 && \
		echo "$(GREEN)  ✓ Board B reachable at $(RELAY_IP)$(RESET)" || \
		echo "$(RED)  ✗ Board B NOT reachable$(RESET)"
	@echo ""
	@echo "$(CYAN)2/3 — HTTP on port $(RELAY_PORT_NUM)...$(RESET)"
	@HTTP_CODE=$$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout 5 "http://$(RELAY_IP):$(RELAY_PORT_NUM)/" 2>/dev/null) && \
		echo "$(GREEN)  ✓ HTTP $${HTTP_CODE} from port $(RELAY_PORT_NUM)$(RESET)" || \
		echo "$(RED)  ✗ Port $(RELAY_PORT_NUM) not responding$(RESET)"
	@echo ""
	@echo "$(CYAN)3/3 — NIP-11 document...$(RESET)"
	@NIP11=$$(curl -s --connect-timeout 5 -H "Accept: application/nostr+json" "http://$(RELAY_IP):$(RELAY_PORT_NUM)/" 2>/dev/null) && \
		echo "$$NIP11" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'  name: {d.get(\"name\",\"?\")}\n  software: {d.get(\"software\",\"?\")}\n  nips: {d.get(\"supported_nips\",[])}')" 2>/dev/null && \
		echo "$(GREEN)  ✓ NIP-11 JSON valid$(RESET)" || \
		echo "$(RED)  ✗ NIP-11 not available$(RESET)"

relay-test-nip11: ## NIP-11 relay info document test (requires lock-b)
	$(call require_lock_b)
	@$(MAKE) _connect-b-if-needed
	@echo ""
	@echo "$(BOLD)=== NIP-11 Relay Info Test ===$(RESET)"
	@cd $(RELAY_TESTS_DIR) && TOLLGATE_IP=$(RELAY_IP) node test-relay-nip11.mjs

relay-test-pubsub: ## WebSocket publish + subscribe test (requires lock-b)
	$(call require_lock_b)
	@$(MAKE) _connect-b-if-needed
	@echo ""
	@echo "$(BOLD)=== Relay Pub/Sub Test ===$(RESET)"
	@cd $(RELAY_TESTS_DIR) && TOLLGATE_IP=$(RELAY_IP) node test-local-relay.mjs

relay-test-sync: ## Verify sync manager pushes events to public relay (requires lock-b)
	$(call require_lock_b)
	@$(MAKE) _connect-b-if-needed
	@echo ""
	@echo "$(BOLD)=== Relay Sync Test ===$(RESET)"
	@echo "$(CYAN)Checking if board has pushed events to public relays...$(RESET)"
	@NPUB=$$(nak key public $(CVM_NSEC_DEFAULT) 2>/dev/null) && \
		echo "Board npub: $$NPUB" && \
		echo "" && \
		echo "$(CYAN)Checking relay.damus.io for kind 38787...$(RESET)" && \
		RESULT=$$(timeout 8 nak req -k 38787 -a $$NPUB -l 1 wss://relay.damus.io 2>/dev/null) && \
		if echo "$$RESULT" | grep -q '"kind"'; then \
			echo "$(GREEN)  ✓ Kind 38787 found on relay.damus.io$(RESET)"; \
		else \
			echo "$(YELLOW)  ~ Kind 38787 not yet synced (sync interval is 30min)$(RESET)"; \
		fi

relay-test-full: ## Run all relay tests sequentially (requires lock-b)
	$(call require_lock_b)
	@echo "$(BOLD)=======================================$(RESET)"
	@echo "$(BOLD)  Full Relay Test Suite (Board B)     $(RESET)"
	@echo "$(BOLD)=======================================$(RESET)"
	@echo ""
	@FAILED=0; \
	echo "$(CYAN)--- 1/5: Smoke Test ---$(RESET)"; \
	$(MAKE) relay-test-smoke || FAILED=$$((FAILED + 1)); echo ""; \
	echo "$(CYAN)--- 2/5: NIP-11 Test ---$(RESET)"; \
	$(MAKE) relay-test-nip11 || FAILED=$$((FAILED + 1)); echo ""; \
	echo "$(CYAN)--- 3/5: Pub/Sub Test ---$(RESET)"; \
	$(MAKE) relay-test-pubsub || FAILED=$$((FAILED + 1)); echo ""; \
	echo "$(CYAN)--- 4/5: Sync Test ---$(RESET)"; \
	$(MAKE) relay-test-sync || FAILED=$$((FAILED + 1)); echo ""; \
	echo "$(CYAN)--- 5/5: API Check ---$(RESET)"; \
	curl -s --connect-timeout 5 "http://$(RELAY_IP):2121/usage" | python3 -m json.tool 2>/dev/null && \
		echo "$(GREEN)  ✓ API reachable$(RESET)" || \
		echo "$(YELLOW)  ~ API not reachable (may need internet for services)$(RESET)"; \
	echo ""; \
	if [ $$FAILED -eq 0 ]; then \
		echo "$(GREEN)=== ALL RELAY TESTS PASSED ===$(RESET)"; \
	else \
		echo "$(RED)=== $$FAILED/4 RELAY TESTS FAILED ===$(RESET)"; \
	fi

# ===========================================================================
#  ARCH COMPONENT EXTRACTION TESTS (tollgate_core)
#  Uses ARCH_BOARD / ARCH_PORT / ARCH_IP from boards.env
# ===========================================================================

ARCH_FIRMWARE_DIR  ?= /home/c03rad0r/esp32-tollgate-arch
ARCH_IP            ?= 10.192.45.1
ARCH_SSID          ?= TollGate-C0E9CA
ARCH_PORT          ?= $(BOARD_C_PORT)
ARCH_BOARD         ?= c
ARCH_WIFI_IFACE    := wlp59s0
ARCH_TESTS_DIR     ?= $(ARCH_FIRMWARE_DIR)/tests/integration
ARCH_SPIFFS_DIR    := /tmp/arch_spiffs
ARCH_SPIFFS_BIN    := /tmp/arch_spiffs.bin
ARCH_SPIFFS_SIZE   := 0xF0000
ARCH_SPIFFS_OFFSET := 0x410000
ARCH_WIFI_SSID     ?= EnterSSID-2.4GHz
ARCH_REQ_DELAY     := 2

define require_arch_lock
	@if [ ! -f "$(LOCK_DIR)/board-$(ARCH_BOARD).lock" ]; then \
		echo "$(RED)$(BOLD)Board $(ARCH_BOARD) not locked — run 'make lock-$(ARCH_BOARD) PHASE=\"description\"' first$(RESET)"; \
		echo "$(YELLOW)Another LLM session may be using this board.$(RESET)"; \
		exit 1; \
	fi
endef

.PHONY: arch-build arch-flash arch-generate-spiffs arch-flash-spiffs \
        arch-wait-ready arch-connect arch-disconnect \
        arch-test-smoke arch-test-network arch-test-api arch-test-dns-fw \
        arch-test-reset arch-test-session arch-test-phase2 arch-test-full \
        arch-test-cleanup arch-test-api-quick

arch-build: ## Build arch (tollgate_core) firmware
	@echo "$(CYAN)Building arch firmware in $(ARCH_FIRMWARE_DIR)...$(RESET)"
	@bash -c 'source /home/c03rad0r/esp/esp-idf/export.sh 2>/dev/null && cd $(ARCH_FIRMWARE_DIR) && idf.py build 2>&1 | tail -5'
	@echo "$(GREEN)Build complete$(RESET)"

arch-generate-spiffs: ## Generate SPIFFS for arch firmware with auto-detected WPA mode
	@echo "$(CYAN)Generating arch SPIFFS image...$(RESET)"
	@rm -rf $(ARCH_SPIFFS_DIR) && mkdir -p $(ARCH_SPIFFS_DIR)
	@WPA_MODE=$$($(MAKE) --no-print-directory detect-wpa-security WIFI_SSID="$(ARCH_WIFI_SSID)"); \
	WPA_MODE=$$(echo "$$WPA_MODE" | tail -1); \
	echo "$(CYAN)Detected WPA mode: $$WPA_MODE$(RESET)"; \
	python3 -c "import json,sys; json.dump({'nsec':'a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2','wifi_networks':[{'ssid':'$(ARCH_WIFI_SSID)','password':'c03rad0r123!'}],'ap_password':'','wifi_auth_mode':sys.argv[1],'mint_url':'https://testnut.cashu.space','price_per_step':21,'step_size_ms':60000}, open('$(ARCH_SPIFFS_DIR)/config.json','w'), indent=2)" "$$WPA_MODE"
	@echo "$(CYAN)Building SPIFFS image ($(ARCH_SPIFFS_SIZE) bytes)...$(RESET)"
	@bash -c 'source /home/c03rad0r/esp/esp-idf/export.sh 2>/dev/null && python3 $$IDF_PATH/components/spiffs/spiffsgen.py $(ARCH_SPIFFS_SIZE) $(ARCH_SPIFFS_DIR) $(ARCH_SPIFFS_BIN) 2>&1'
	@echo "$(GREEN)SPIFFS image: $(ARCH_SPIFFS_BIN)$(RESET)"
	@cat $(ARCH_SPIFFS_DIR)/config.json

arch-flash: ## Flash arch firmware + SPIFFS to arch board (requires lock-$(ARCH_BOARD))
	$(call require_arch_lock)
	@$(MAKE) arch-build
	@$(MAKE) arch-generate-spiffs
	@echo "$(CYAN)Erasing flash on $(ARCH_PORT)...$(RESET)"
	@bash -c 'source /home/c03rad0r/esp/esp-idf/export.sh 2>/dev/null && esptool.py --chip esp32s3 -p $(ARCH_PORT) -b 115200 erase_flash 2>&1 | tail -3'
	@echo "$(CYAN)Flashing firmware + SPIFFS to $(ARCH_PORT)...$(RESET)"
	@bash -c 'source /home/c03rad0r/esp/esp-idf/export.sh 2>/dev/null && esptool.py --chip esp32s3 -p $(ARCH_PORT) -b 115200 --before default_reset --after hard_reset write_flash --flash_mode dio --flash_size 16MB --flash_freq 80m 0x0 $(ARCH_FIRMWARE_DIR)/build/bootloader/bootloader.bin 0x8000 $(ARCH_FIRMWARE_DIR)/build/partition_table/partition-table.bin 0x10000 $(ARCH_FIRMWARE_DIR)/build/esp32-tollgate.bin $(ARCH_SPIFFS_OFFSET) $(ARCH_SPIFFS_BIN) 2>&1 | tail -8'
	@echo "$(GREEN)Firmware + SPIFFS flashed to $(ARCH_PORT)$(RESET)"
	@echo "$(CYAN)Waiting 70s for boot...$(RESET)"
	@sleep 70
	@$(MAKE) arch-wait-ready

arch-flash-spiffs: ## Flash SPIFFS only to arch board (requires lock)
	$(call require_arch_lock)
	@$(MAKE) arch-generate-spiffs
	@echo "$(CYAN)Flashing SPIFFS to $(ARCH_PORT) at offset $(ARCH_SPIFFS_OFFSET)...$(RESET)"
	@bash -c 'source /home/c03rad0r/esp/esp-idf/export.sh 2>/dev/null && esptool.py --chip esp32s3 -p $(ARCH_PORT) -b 115200 --after no_reset write_flash $(ARCH_SPIFFS_OFFSET) $(ARCH_SPIFFS_BIN) 2>&1 | tail -5'
	@echo "$(GREEN)SPIFFS flashed$(RESET)"

arch-wait-ready: ## Wait for arch board HTTP server to be ready (polls :2121/usage)
	@echo "$(CYAN)Waiting for arch board at $(ARCH_IP):2121 to be ready...$(RESET)"
	@for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do \
		if curl -s --connect-timeout 3 --max-time 5 "http://$(ARCH_IP):2121/usage" 2>/dev/null | grep -q "owner"; then \
			echo "$(GREEN)Board ready! (attempt $$i)$(RESET)"; \
			curl -s --max-time 5 "http://$(ARCH_IP):2121/usage" | python3 -m json.tool 2>/dev/null; \
			exit 0; \
		fi; \
		echo "  attempt $$i/15 — not ready, waiting 5s..."; \
		sleep 5; \
	done; \
	echo "$(RED)Board did not become ready after 75s$(RESET)"; \
	exit 1

arch-connect: ## WiFi connect to arch board AP (requires lock)
	$(call require_arch_lock)
	@echo "$(CYAN)Connecting $(ARCH_WIFI_IFACE) to $(ARCH_SSID)...$(RESET)"
	@echo "c03rad0r123" | sudo -S nmcli device wifi connect "$(ARCH_SSID)" ifname $(ARCH_WIFI_IFACE) 2>&1 | grep -v "Password:" || true
	@sleep 3
	@for i in 1 2 3 4 5; do ping -c 1 -W 2 $(ARCH_IP) >/dev/null 2>&1 && break; sleep 2; done
	@echo "$(GREEN)Connected to $(ARCH_SSID) ($(ARCH_IP))$(RESET)"

arch-disconnect: ## Disconnect WiFi from arch board AP
	@echo "c03rad0r123" | sudo -S nmcli device disconnect $(ARCH_WIFI_IFACE) 2>&1 | grep -v "Password:" || true
	@sleep 2
	@echo "$(GREEN)Disconnected from board AP$(RESET)"

arch-test-api-quick: ## Quick API sanity check (requires lock)
	$(call require_arch_lock)
	@echo "$(BOLD)=== Quick API Test ===$(RESET)"
	@echo "$(CYAN)1/5 — /usage$(RESET)"
	@sleep $(ARCH_REQ_DELAY) && curl -s --max-time 10 "http://$(ARCH_IP):2121/usage" | python3 -m json.tool 2>/dev/null || echo "$(RED)  FAILED$(RESET)"
	@echo "$(CYAN)2/5 — /portal-config$(RESET)"
	@sleep $(ARCH_REQ_DELAY) && curl -s --max-time 10 "http://$(ARCH_IP):2121/portal-config" | python3 -m json.tool 2>/dev/null || echo "$(RED)  FAILED$(RESET)"
	@echo "$(CYAN)3/5 — /whoami$(RESET)"
	@sleep $(ARCH_REQ_DELAY) && curl -s --max-time 10 "http://$(ARCH_IP):2121/whoami" 2>/dev/null || echo "$(RED)  FAILED$(RESET)"
	@echo "$(CYAN)4/5 — Portal (port 80)$(RESET)"
	@sleep $(ARCH_REQ_DELAY) && curl -s --max-time 10 -o /dev/null -w "HTTP %{http_code}, %{size_download} bytes" "http://$(ARCH_IP)/" 2>/dev/null && echo "" || echo "$(RED)  FAILED$(RESET)"
	@echo "$(CYAN)5/5 — /usage (confirm still alive)$(RESET)"
	@sleep $(ARCH_REQ_DELAY) && curl -s --max-time 10 "http://$(ARCH_IP):2121/usage" | python3 -m json.tool 2>/dev/null || echo "$(RED)  FAILED$(RESET)"
	@echo "$(GREEN)=== Quick API Test Complete ===$(RESET)"

arch-test-smoke: ## Smoke test: SSID, portal, grant, reset, internet (~30s)
	$(call require_arch_lock)
	@echo "$(BOLD)=== Smoke Test ===$(RESET)"
	@cd $(ARCH_TESTS_DIR) && TOLLGATE_IP=$(ARCH_IP) AP_SSID=$(ARCH_SSID) REQ_DELAY=$(ARCH_REQ_DELAY) node smoke.mjs

arch-test-network: ## Network test: AP scan, DHCP, DNS hijack (~15s)
	$(call require_arch_lock)
	@echo "$(BOLD)=== Network Test ===$(RESET)"
	@cd $(ARCH_TESTS_DIR) && TOLLGATE_IP=$(ARCH_IP) REQ_DELAY=$(ARCH_REQ_DELAY) node network.mjs

arch-test-api: ## API test: portal, captive detect, whoami, usage, DNS (~20s)
	$(call require_arch_lock)
	@echo "$(BOLD)=== API Test ===$(RESET)"
	@cd $(ARCH_TESTS_DIR) && TOLLGATE_IP=$(ARCH_IP) REQ_DELAY=$(ARCH_REQ_DELAY) node api.mjs

arch-test-dns-fw: ## DNS + Firewall test: hijack, NAT, forward after auth (~30s)
	$(call require_arch_lock)
	@echo "$(BOLD)=== DNS + Firewall Test ===$(RESET)"
	@cd $(ARCH_TESTS_DIR) && TOLLGATE_IP=$(ARCH_IP) REQ_DELAY=$(ARCH_REQ_DELAY) node test-dns-firewall.mjs

arch-test-reset: ## Reset auth cycle: reset-pay-verify-reset-repay (~30s)
	$(call require_arch_lock)
	@echo "$(BOLD)=== Reset Auth Test ===$(RESET)"
	@cd $(ARCH_TESTS_DIR) && TOLLGATE_IP=$(ARCH_IP) REQ_DELAY=$(ARCH_REQ_DELAY) node test-reset-auth.mjs

arch-test-session: ## Session expiry: 65s wait, verify blocked, renew (~80s)
	$(call require_arch_lock)
	@echo "$(BOLD)=== Session Expiry Test ===$(RESET)"
	@echo "$(YELLOW)NOTE: This test waits 65s for session expiry$(RESET)"
	@cd $(ARCH_TESTS_DIR) && TOLLGATE_IP=$(ARCH_IP) REQ_DELAY=$(ARCH_REQ_DELAY) node test-session-expiry.mjs

arch-test-phase2: ## Full API: discovery, tokens, spent, expiry, renewal (~90s)
	$(call require_arch_lock)
	@echo "$(BOLD)=== Phase 2 API Test ===$(RESET)"
	@echo "$(YELLOW)NOTE: This test waits 65s for session expiry$(RESET)"
	@cd $(ARCH_TESTS_DIR) && TOLLGATE_IP=$(ARCH_IP) REQ_DELAY=$(ARCH_REQ_DELAY) node phase2.mjs

arch-test-cleanup: ## Disconnect WiFi + reset auth state
	$(call require_arch_lock)
	@sleep $(ARCH_REQ_DELAY) && curl -s --connect-timeout 3 "http://$(ARCH_IP):2121/reset_authentication" 2>/dev/null || true
	@nmcli device disconnect $(ARCH_WIFI_IFACE) 2>/dev/null || true
	@sleep 2
	@echo "$(GREEN)Cleanup complete$(RESET)"

arch-test-full: ## Run all arch E2E tests sequentially (~4min)
	@echo "$(BOLD)=======================================$(RESET)"
	@echo "$(BOLD)  Full Arch E2E Test Suite             $(RESET)"
	@echo "$(BOLD)=======================================$(RESET)"
	@echo ""
	@FAILED=0; \
	echo "$(CYAN)--- 0/7: Wait Ready ---$(RESET)"; \
	$(MAKE) arch-wait-ready || exit 1; echo ""; \
	echo "$(CYAN)--- 1/7: Smoke Test ---$(RESET)"; \
	$(MAKE) arch-test-smoke || FAILED=$$((FAILED + 1)); echo ""; \
	echo "$(CYAN)--- 2/7: Network Test ---$(RESET)"; \
	$(MAKE) arch-test-network || FAILED=$$((FAILED + 1)); echo ""; \
	echo "$(CYAN)--- 3/7: API Test ---$(RESET)"; \
	$(MAKE) arch-test-api || FAILED=$$((FAILED + 1)); echo ""; \
	echo "$(CYAN)--- 4/7: DNS + Firewall Test ---$(RESET)"; \
	$(MAKE) arch-test-dns-fw || FAILED=$$((FAILED + 1)); echo ""; \
	echo "$(CYAN)--- 5/7: Reset Auth Test ---$(RESET)"; \
	$(MAKE) arch-test-reset || FAILED=$$((FAILED + 1)); echo ""; \
	echo "$(CYAN)--- 6/7: Session Expiry Test ---$(RESET)"; \
	$(MAKE) arch-test-session || FAILED=$$((FAILED + 1)); echo ""; \
	echo "$(CYAN)--- 7/7: Phase 2 API Test ---$(RESET)"; \
	$(MAKE) arch-test-phase2 || FAILED=$$((FAILED + 1)); echo ""; \
	echo ""; \
	if [ $$FAILED -eq 0 ]; then \
		echo "$(GREEN)=== ALL 7 ARCH E2E TESTS PASSED ===$(RESET)"; \
	else \
		echo "$(RED)=== $$FAILED/7 ARCH E2E TESTS FAILED ===$(RESET)"; \
	fi
