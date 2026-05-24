# Learnings

## 2026-05-13 Session Start
- NDS 5.0.2 returns 503 for requests FROM the router (no ARP entry for own IP). This is expected — NOT a bug.
- Android puts captive portal notification in notification shade, NOT WiFi Settings
- Phone has subnet route to 192.168.1.0/24 via wlan0 even when mobile data is primary — browser CAN reach portal
- Cashu tokens are URL-safe base64 (no spaces) — ADB `input text` should work
- `--quick-phone` flag skips `_open_portal_on_phone()` entirely
- `pay_direct()` tests never call `_open_portal_on_phone()` — they work fine
- Portal is React SPA at `/etc/tollgate/tollgate-captive-portal-site/splash.html`
- Portal state machine uses `data-sm` HTML attribute: portal_ready, token_typing, authed, countdown
- D-Link COVR-X1860 uses anonymous wifi-iface sections — TollGate uci-defaults bug
