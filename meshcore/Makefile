# MeshCore LR2021 2-Device Smoke Tests
#
# Prerequisites:
#   - 2x ESP32-C3 + LR2021 boards connected
#   - MeshCore fork built at ~/MeshCore
#   - meshcore-cli installed at ~/.local/bin/meshcore-cli
#   - balloon-board-lock.py at ~/repos/balloon-fresh/tools/
#
# Usage:
#   make test           # Run full smoke test suite
#   make test-no-flash  # Skip flashing, use existing firmware
#   make flash          # Just flash both boards
#   make status         # Check board + lock status

MESHCORE_DIR ?= $(HOME)/MeshCore
LOCK_SCRIPT  ?= $(HOME)/repos/balloon-fresh/tools/balloon-board-lock.py
BOARD_A      ?= /dev/ttyACM1
BOARD_B      ?= /dev/ttyACM2

.PHONY: test test-no-flash flash status clean

test:
	pytest tests/ -v \
		--board-a $(BOARD_A) \
		--board-b $(BOARD_B) \
		--tb=short \
		-s

test-no-flash:
	pytest tests/ -v \
		--board-a $(BOARD_A) \
		--board-b $(BOARD_B) \
		--no-flash \
		--tb=short \
		-s

flash:
	cd $(MESHCORE_DIR) && \
	pio run -t upload -e LR2021_companion_radio_usb --upload-port $(BOARD_A) && \
	pio run -t upload -e LR2021_companion_radio_usb --upload-port $(BOARD_B)

status:
	python3 $(LOCK_SCRIPT) status

clean:
	rm -rf results/
