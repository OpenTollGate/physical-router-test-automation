# Decisions

## Port Number for NDS Portal
**Decision:** Use port 2050 for the NDS captive portal URL.

**Rationale:**
- Port 2050 is the NDS portal port (React SPA)
- Port 8080 is the CGI port (backend API endpoints)
- Using 8080 for the portal would not work because the portal is a separate service
- Reference: AGENTS.md lesson about NDS portal listening on port 2050

## Portal Host Detection
**Decision:** Use `wifi._get_portal_host()` instead of hardcoded "192.168.1.1".

**Rationale:**
- `wifi._get_portal_host()` reads from br-lan interface, which is more reliable
- Supports routers with custom LAN addressing
- Works regardless of router model or configuration
- Cleaner code that leverages existing WiFi class functionality
