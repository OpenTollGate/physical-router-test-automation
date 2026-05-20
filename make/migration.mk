# Migration stubs: forward migrated targets to scripts/pymake.py
PYMAKE := $(CURDIR)/scripts/pymake.py

define migrated_target
	@echo "$(YELLOW)>>> This test has moved to pytest — see config/make-pytest-map.yaml$(RESET)"
	@echo "$(YELLOW)>>> Run: ./scripts/pymake.py $(1) --router $(ROUTER)$(RESET)"
	@python3 $(PYMAKE) $(1) --router $(ROUTER) \
		$(if $(SSID),--ssid "$(SSID)",) \
		$(if $(PASS),--password "$(PASS)",) \
		$(if $(MINT),--mint "$(MINT)",) \
		$(if $(CMD),--cmd "$(CMD)",)
endef

define migrated_target_partial
	@echo "$(YELLOW)>>> Partial pytest coverage exists for $(1); running pymake$(RESET)"
	@$(call migrated_target,$(1))
endef
