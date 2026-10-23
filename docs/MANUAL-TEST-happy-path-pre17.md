# Manual happy-path test — tollgate-wrt 0.6.0_alpha4_pre17 on the GL-MT3000

For the operator (c08r4d0r) to run before the review club, and for the club itself.
Everything here is read-only on the router except the payment itself.

Build under test: **`tollgate-wrt-0.6.0_alpha4_pre17-r1`** (`aarch64_cortex-a53`),
module pin `2796d96`, feed release `FreedomTechFeed/packages v0.6.0-alpha4-pre17`.

---

## 0. What you need

| item | why |
|---|---|
| MT3000 with pre17 installed, Ethernet to your workstation | the target |
| a phone or laptop **that has never been on this router's guest SSID** | the real guest vantage; a machine that has already paid or is trusted skips the portal entirely |
| a Cashu token worth **≥ 64 sats** from an advertised mint, **or** a Lightning wallet | 1 sat/step, min 64 sats |
| root password on the router (for the on-box checks) | `test123` on the bench box |

Three scripts do the mechanical parts; all are optional for a manual pass.

```bash
# 1. on-router state — run AFTER the trust cleanup in step 1
ssh root@192.168.1.1 'sh -s' < <(curl -fsSL https://raw.githubusercontent.com/felixfelix-bot/physical-router-test-automation/main/scripts/diag/tg-router-health.sh)

# 2. everything, one command: on-router state + the stranger's walk + the official harness.
#    It asks for the router password ONCE (the connection is then multiplexed) and walks the
#    portal from THIS machine — which is an unauthorised client, so no sudo and no macvlan.
bash <(curl -fsSL https://raw.githubusercontent.com/felixfelix-bot/physical-router-test-automation/main/scripts/test/club-acceptance.sh)

# 3. if the portal renders but nothing can be paid (:2121 silent, no /var/run/tollgate.sock):
ssh root@192.168.1.1 'sh -s' < <(curl -fsSL https://raw.githubusercontent.com/felixfelix-bot/physical-router-test-automation/main/scripts/diag/tg-service-diag.sh)
```

`raw.githubusercontent.com/…/main/…` is CDN-cached and can keep serving the previous revision
for a few minutes after a push. If the behaviour looks older than this document describes, pin
the URL to a commit instead — `…/<full-sha>/scripts/…`.

---

## 1. BEFORE ANYTHING: prove that a stranger still has to pay

This is the step that decides whether the rest of the test means anything. A client whose
MAC is in `nodogsplash.trustedmac` **bypasses the captive portal completely** — no
interception, no detection prompt, no payment, full internet. Bench tooling writes such
entries on purpose (so a test session cannot lock itself out) and they survive reboots.

```bash
ssh root@192.168.1.1
uci get nodogsplash.@nodogsplash[0].trustedmac     # MUST print nothing
ndsctl clients                                     # no client should be "Trusted"
```

If either is non-empty, clear it:

```bash
for m in $(uci get nodogsplash.@nodogsplash[0].trustedmac); do
  uci del_list nodogsplash.@nodogsplash[0].trustedmac="$m"; done
uci commit nodogsplash
/etc/init.d/nodogsplash restart
for m in $(ndsctl clients | sed -n 's/^mac=//p'); do ndsctl deauth "$m"; done
```

**Also make sure the device you will test from is not the one you administer the router
from.** If your laptop must stay online, test the portal from a phone instead.

---

## 2. Which network you test from matters

| vantage | how | what is being tested there |
|---|---|---|
| **guest** | the open SSID `tollgate-XXXX` (no password) or a wired client on br-lan | detection, the portal, payment, access, the :8090 guard |
| **private** | the private SSID `c08r4d0r-XXXX` or the management path | the admin board, LuCI — *not* the customer experience |

A payment test run from the private network proves nothing: there is no captive portal there.

---

## 3. Test 1 — OS captive-portal detection (the headline test)

Connect the fresh device to the **guest** SSID and wait ~10 s. Expect the OS to raise its
own sign-in prompt (Android: "Sign in to network" notification; iOS/iPadOS: a sheet;
Windows: "You need to sign in to this network"; Firefox: an infobar).

If no prompt appears, do not conclude "broken" — check the chain by hand from that device:

```bash
# on the guest device, before paying
curl -s -o /dev/null -w '%{http_code} %{redirect_url}\n' http://connectivitycheck.gstatic.com/generate_204
```

Expected: `307 http://192.168.1.1:2050/splash.html?redir=…` — the same answer for each of
these (they are the four OS probes):

- `http://connectivitycheck.gstatic.com/generate_204` (Android)
- `http://captive.apple.com/hotspot-detect.html` (Apple)
- `http://www.msftconnecttest.com/connecttest.txt` (Windows)
- `http://detectportal.firefox.com/success.txt` (Firefox)

Any `204`/`200` with real content means **the device is being let through** — go back to
step 1 (`trustedmac`, or the device already paid earlier).

**PASS:** all four 307 to `:2050/splash.html?redir=<url-encoded original>`, and the OS
prompt appears.

---

## 4. Test 2 — the portal itself

Follow the redirect (or open `http://192.168.1.1:2051/splash.html`). Expect the TollGate
SPA: logo, price ("1 sat per 21 MiB" style), and the mint list from the advertisement:

```bash
curl -s http://192.168.1.1:2121/ | head -c 400
# kind:10021, tags: metric=bytes, step_size=22020096, price_per_step=cashu 1 sat <mint>
```

Known cosmetic issues you can ignore in this build (they are carded, not blockers):
the balance page renders some **raw i18n keys** (`balance_active_label`, …), and
`/logo192.png` returns **404** on `:2051`.

---

## 5. Test 3 — the admin board must NOT be reachable from the guest

```bash
# from the guest device
curl -s -o /dev/null -w '%{http_code}\n' http://192.168.1.1:8090/     # expect 000
curl -s -o /dev/null -w '%{http_code}\n' http://192.168.1.1/          # expect 307 -> the portal
```

`000` on `:8090` is the guard working (#566). **Known finding, not a blocker, but worth
noting for the club:** `:8080` answers `307 https://192.168.1.1/` and `https://192.168.1.1`
serves **LuCI** (a router admin login, with a self-signed cert warning) — reachable from the
guest network because `users_to_router` allows tcp 8080/443 pre-auth. Carded as
`t_f3bb1f85`; do not paper over it, and do not report it as new.

---

## 6. Test 4 — payment and access (the money path)

**4a — Browser (a real customer's route).** In the portal, choose a purchase → you get a
Lightning invoice (or the Cashu option) → pay it with a Lightning wallet / paste a Cashu
token worth ≥ 64 sats → submit. Expect the confirmation page
("Payment successful! You now have …"), the balance page showing the allotment, and the
device getting internet.

**4b — Headless (reproducible, what the harness does).** From a **pre-auth** client whose
MAC the router can resolve:

```bash
# a free ≥64-sat token: testnut runs a FakeWallet, and the router advertises it
curl -s -o /dev/null -w '%{http_code}\n' http://192.168.1.1:2121/?mac=REPLACE_WITH_THIS_DEVICE_MAC   # before
# POST the token (the harness does exactly this with RHP_CASHU_TOKEN + RHP_SPEND_MAX_SATS=64)
```

Record, before and after:

```bash
curl -s http://192.168.1.1:2121/balance   # expect session_active false  ->  true
curl -s http://192.168.1.1:2121/usage     # -1/-1 before, allotment after
```

**Proven on hardware 2026-09-25** (pre17, testnut 64-sat token): the paid lane went green
for the first time, the merchant wallet on the box moved **2 → 65 sats** (+63: one sat per
step, 63 steps from a 64-sat token), the client's `/balance` went
`{"session_active":true,"allotment":1387266048,"remaining":1387247616}`, that client reached
the real internet (HTTP 200), and a *different* unauthenticated client still got the portal.

**PASS requires all four:**
1. the purchase is accepted (`200`, not a 4xx/5xx);
2. `session_active` flips `false → true` for **that** client;
3. that client can now actually reach the internet (load a page, or `curl -s https://api.ipify.org`);
4. **a different unauthenticated device still gets the portal** — the payment opened
   one client, not the network.

---

## 7. Test 5 — the operator actually got the money

```bash
ssh root@192.168.1.1 'tollgate wallet balance'      # note before + after the purchase
ssh root@192.168.1.1 'cat /etc/tollgate/config.json | head -30'   # accepted mints, min_balance 64
```

Expect the wallet balance to grow by the paid amount (for a Cashu purchase the proof is in
the merchant wallet; for Lightning the invoice settles and the backend reconciles). A
purchase that grants access **without** the wallet increasing is a stop-ship defect.

---

## 8. Test 6 — a fresh install (do this only if the club is also testing the installer)

Install via the installer, then **run the health script BEFORE rebooting**:

```bash
ssh root@192.168.1.1 'sh -s' < <(curl -fsSL …/scripts/diag/tg-router-health.sh)
```

Expected in this build, and **known**: until something reloads the services, the
`nds_enforce_forward` / `admin_board_input_guard` chains are absent, `:8090` can answer to
a guest, and IPv6 RAs may still be live — the feed recipe's postinst restarts only
`tollgate-wrt`. Reboot, re-run, and everything passes. This is what pre18 fixes; report it
as *known*, with the pre-reboot output attached, because that output is the acceptance
evidence for the fix.

---

## 9. Stop-ship criteria

Stop and report immediately if any of these happen:

- a device that is not in `trustedmac` reaches the internet **without** paying;
- the OS prompt never appears **and** the four probes return real content (204/200);
- a purchase grants access but the merchant wallet does not increase;
- `:8090` (or LuCI on `:8080`/`:443`) answers `200` **to a guest**;
- paying for one device opens access for another;
- the portal is unreachable (`:2051` dead) or the API answers non-`kind:10021` on `/`.

---

## 10. Record sheet (fill this in; it is the handover evidence)

```
build      : tollgate-wrt 0.6.0_alpha4_pre17-r1  (module 2796d96)
router     : GL-MT3000 @ 192.168.1.1   uptime/rebooted at: ______
pre-check  : trustedmac empty?  Y/N     ndsctl clients=0?  Y/N
guest dev  : device ______  MAC ______  SSID ______

T1 detection   android/apple/windows/firefox probes: 307? Y/N   OS prompt appeared? Y/N
T2 portal      :2051/splash.html 200? Y/N   price shown? Y/N   mints listed? Y/N
T3 guard       :8090 from guest = 000? Y/N   :80 = 307? Y/N
T4 payment     accepted? Y/N   session_active false->true? Y/N   internet works? Y/N
               second device still portals? Y/N
T5 merchant    wallet before/after: ______ -> ______
T6 fresh inst  pre-reboot health FAILs (expected) Y/N   post-reboot ALL PASS Y/N
known issues   i18n keys Y/N · logo192 404 Y/N · LuCI on 8080/443 from guest Y/N
verdict        PASS / FAIL        notes: ______
```

Attach: the health-script output, the harness transcript, the two wallet readings, and a
screenshot of the OS prompt on the guest device.

---

## 11. Known issues in this build — do not re-report as new

1. Balance page renders raw i18n keys; `/logo192.png` 404s on `:2051` (`t_aa7d5828`).
2. LuCI reachable from the guest network on `:8080`/`:443`; `:8090` guard works (`t_f3bb1f85`).
3. A fresh install needs a service reload or a reboot before the guard chain loads; fixed in
   pre18 (`t_12a2ab24`).
4. `ndsctl status` prints `Preauth: Disabled` — cosmetic on this build; the gate works
   (proven with a fresh MAC).
5. `GET /ln-invoice` with no quote returns `400 {"error":"quote is required"}` — that is the
   status poll, **not** a fault.
6. The harness's paid lane needed a two-character fix (`lib/cashtoken.py` read the version
   character at index 6 instead of 5, so *every* token failed `paid:token-inspected`) —
   carded as `t_56262dc7`, fixed and proven. If you run the harness from a checkout older than
   that fix, the paid lane cannot pass; it is not a build defect.
7. The harness's own guest-vantage quirks (`:8090` expectation, preflight TCP flake) — being
   fixed in `t_ce131bc3`; the wrapper classifies them.

8. **The money path can go silent while the portal looks perfect.** Measured once on a fresh
   install after a reboot: firewall chains, nodogsplash, `:2051`, `:8090`, `:443`, `:8443` all
   healthy and `tollgate-wrt` reported `running` — but no `:2121` and no
   `/var/run/tollgate.sock`, so `tollgate wallet balance` failed and nothing could be bought.
   A service restart brought it back. Under investigation (candidate release blocker): run
   script 3 above and send its output.
9. `club-acceptance.sh` **v1** required the author's password file and root on the tester's own
   machine; **v2** (current) asks for the router password once and uses your machine as the
   unauthorised client. If you are asked for a password file, you have the stale cached copy —
   pin the URL to a commit.
