# net4sats Dual-Router Test Report
# Date: 2026-07-03 16:45 IST
# Test Machine: T470 (CobradorWave) connected to both routers via ethernet

## Routers Tested

| Property | GL-MT6000 (Flint 3) | GL-MT3000 (Beryl AX) |
|---|---|---|
| IP | 192.168.1.1 | 10.47.41.1 |
| Hostname | TollGate.lan | TollGate |
| Chip | MT7986 (Filogic) | MT7981 (Filogic) |
| OpenWrt | 25.12.0 (apk) | 24.10.4 (opkg) |
| Kernel | 6.12.71 | 6.6.110 |
| tollgate-wrt | v0.5.0-alpha3 | v0.5.0-alpha3 |
| Disk | 437M/7.2G (6%) | 21M/200M (11%) |
| WiFi APs | 4 (2.4+5GHz x open+WPA) | 4 (2.4+5GHz x open+WPA) |
| WiFi STA | 0 | 1 (upstream "EnterSSID-2.4GHz") |
| SSL certs | No | Yes (/etc/tollgate/ssl/) |
| net4sats portal | Yes (/www/net4sats/) | Yes (/www/net4sats/) |

## API Tests (curl/SSH)

| Test | MT6000 | MT3000 |
|---|---|---|
| SSH root access | PASS | PASS |
| tollgate-wrt process | PASS | PASS |
| API :2121 health | PASS | PASS |
| API :2121 /balance | PASS | PASS |
| API :2121 /usage | PASS | PASS |
| HTTP :80 responds | PASS (307) | PASS (307) |
| net4sats portal dir | PASS | PASS |
| net4sats HTTP loads | PASS (307) | PASS (307) |
| WiFi AP active | PASS (4 APs) | PASS (4 APs) |
| WiFi STA upstream | SKIP (none) | PASS (1 STA) |
| SSL certificates | SKIP (none) | PASS (certs present) |
| Disk healthy (<90%) | PASS (6%) | PASS (11%) |

**API Score: MT6000 10/12 pass (2 skip), MT3000 12/12 pass**

## Playwright Browser Tests

| Test | MT6000 | MT3000 |
|---|---|---|
| net4sats UX walkthrough | PASS (28s) | PASS (33s) |
| Portal loads with branding | PASS | PASS |
| Payment form element | FAIL (SPA timeout) | PASS |
| Connect/pay button | FAIL (SPA timeout) | FAIL (SPA timeout) |
| Desktop screenshot | PASS | PASS |
| Mobile screenshot | PASS | PASS |
| NDS redirect intercept | FAIL (LAN side) | FAIL (LAN side) |

**Playwright Score: MT6000 4/7 pass, MT3000 5/7 pass**

## Failure Analysis

### 1. "Payment form element" — FAIL on MT6000, PASS on MT3000
- **Root cause:** Both routers serve React SPAs. The MT6000 runs the net4sats-branded SPA (title: "net4sats"), the MT3000 runs the TollGate-branded SPA (title: "Tollgate Captive Portal"). The MT6000 SPA takes >15s to hydrate and render form elements.
- **Fix:** Increase `waitForFunction` timeout from 15000ms to 30000ms.

### 2. "Connect/pay button" — FAIL on both
- **Root cause:** Neither SPA renders a traditional `<button>` with text "connect", "pay", "submit", or "go" within 15s. The portals may use icon buttons, different text, or SVG-based buttons that the DOM query doesn't match.
- **Fix:** Update the button text search to include portal-specific text ("Buy", "Purchase", "Get Internet", or icon class names).

### 3. "NDS redirect intercept" — FAIL on both (EXPECTED)
- **Root cause:** The test machine (T470) is connected via ethernet to the router LAN port. NDS only intercepts traffic from WiFi clients on the open AP. From the trusted LAN side, HTTP requests to example.com go straight through — no redirect.
- **This is correct behavior.** This test can only pass when run from a device connected to the open WiFi SSID (TollGate-F794 / TollGate-D213), not from ethernet.

## WiFi STA Verification (MT3000 only)
The MT3000 has an active upstream WiFi STA connection:
- Interface: phy0-sta0
- SSID: "EnterSSID-2.4GHz"
- Signal: -33 dBm (excellent)
- Channel: 6 (2.4GHz)
- Bit Rate: 270 MBit/s (HT40)

## Portal Differences
- **MT6000 splash:** net4sats branded (`splash-DeiT_wzP.js`, title "net4sats")
- **MT3000 splash:** TollGate branded (`portal-BQ7wV0jU.js`, title "Tollgate Captive Portal")
- Both are React SPAs with client-side rendering
- Both redirect port 80 → port 2050 (NDS splash)
