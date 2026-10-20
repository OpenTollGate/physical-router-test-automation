# RUNBOOK — serial bench lab (MT3000 / MT6000)

How the physical OpenWrt bench works and how to drive it for the TollGate CI
trust pipeline. The bench is a **single-owner** resource; every router-touching
run takes the shared lock, and **radios are OFF whenever the bench is idle**.

## Topology

```
CI runner (hermes-nvme, self-hosted)      --fips-->  gateway: cobrador
   runs scripts/bench/bench-lanes.sh  ────────────▶  scripts/bench/bench-cycle.sh
                                                        │ SSH over the LAN (USB-eth)
                                                        ▼
                                          bench routers: MT3000 / MT6000
   serial console (passwordless root) ─── emergency path:
      MT3000 → T440 /dev/ttyUSB0          ~/openwrt-serial-lab/router-recover.py
      MT6000 → x280 /dev/ttyUSB0          + mtk_uartboot (BROM, bricked case)
```

- **Gateway:** the `cobrador` host. Reaches the routers over its USB-eth links and
  runs the bench scripts. Also the fips-mesh node the runner dials.
- **Consoles:** FTDI USB-TTL at **115200 8N1, 3.3 V**; MT3000 on **T440**, MT6000
  on **x280**. U-Boot stops autoboot on the literal `gl`; OpenWrt failsafe = `f`+Enter.

## Entry points

```sh
# One lane: lock -> (optional clean flash) -> install pkg -> smoke -> radios OFF
scripts/bench/bench-cycle.sh --router <IP> --lane 24/ipk|25/apk|otg \
    [--image FILE] [--pkg FILE] [--static-ip IP] [--confirm WIPE] [--result out.json]

# All configured lanes, sequentially
scripts/bench/bench-lanes.sh [--only LANE] [--config config/bench-lanes.json]

# Idle safety (also on a 5-min systemd timer on the gateway)
scripts/bench/radios-off.sh <IP>
scripts/bench/bench-wifi-off-watchdog.sh      # BENCH_ROUTERS=... BENCH_LOCK=...
```

- `bench-cycle.sh` sets an **EXIT/INT/TERM trap** that runs `radios-off.sh` — an
  aborted/killed run still drops the APs.
- `bench-cycle.sh --static-ip` re-pins `network.lan.ipaddr` **after** install
  (the TollGate package re-addresses the box on install, which otherwise churns
  the router IP every run).

## Lanes

`config/bench-lanes.json` is the source of truth: `{lane, router, image, pkg,
static_ip}`. `router` is the IP **as seen from the gateway**. Keep it in sync
with `config/routers.json` (the registry).

## The bench image (clean slate + radios-off by default)

Build with the OpenWrt ImageBuilder (`scripts/bench/build-image.sh <LAN_IP>
[profile] [outname] [imagebuilder_dir]`). It bakes:

- a **fixed LAN IP** (uci-defaults) — so the box is reachable at a known address;
- the **documented root password** (`benchpw2026`) — so the SSH cycle needs no console;
- **radios OFF by default** via a late `/etc/rc.local` hook — the uci-defaults run
  *before* the wifi config is generated, so they cannot see the `wifi-device`
  sections; rc.local runs late and does the disable + `wifi down`;
- **`socat`** — the serial-into-Linux fallback's transport.

> ⚠️ Known issue: after a flash the box has been observed answering on a
> *different* `10.17.x` address than the baked `network.lan.ipaddr` (a LAN/WAN/enx
> cabling ambiguity). Until resolved (needs the console boot log), prefer
> `bench-cycle.sh --static-ip` to re-pin, and re-check `config/bench-lanes.json`
> router IPs after each flash.

## Idle WiFi-off (never leave the neighbours a door)

Two layers:
1. **Cycle trap** — every `bench-cycle.sh` exit drops the APs.
2. **Watchdog** — `bench-wifi-off.{service,timer}` on the gateway runs every 5 min;
   if **no run holds the bench lock** it forces radios off on `BENCH_ROUTERS`.
   Covers SIGKILL / reboot / power loss. Env file: `~/.hermes/state/bench-watchdog.env`.

## Recovery

```sh
# Lost password / misconfig / reflash, over the serial console (no network needed)
~/openwrt-serial-lab/router-recover.py --port /dev/ttyUSB0 --action failsafe|firstboot|set-root-password
~/openwrt-serial-lab/router-recover.py --port /dev/ttyUSB0 --action install-remote --url <sysupgrade> --sha256 <hex>

# Bricked bootloader (last resort): BROM UART restore
mtk_uartboot -s /dev/ttyUSB0 -p bl2.bin --aarch64 -f <fip.bin>
```

U-Boot "web failsafe" (network): host static `192.168.1.2`, cable in a **LAN** port,
hold **Reset** on power-up, browse `http://192.168.1.1`, upload the sysupgrade image.

The custom `loady`-enabled U-Boot (built from `gl-inet/mt798x-boot`,
`CONFIG_CMD_LOADB=y`; MT3000 FIP on `mtd3 "FIP"`, MT6000 eMMC `mmcblk0p3`) is the
fully-serial image-ingest path for the unbootable case.

## Failure signatures

| Symptom | Meaning / action |
|---|---|
| `ssh: Permission denied` on a **TollGate-installed** box | the module re-works the admin model; use the **serial console** |
| `Operation not permitted` on a fetch from the box | Linux firewall/policy; use the console, or the U-Boot path |
| Box answers on a different `10.17.x` than configured | LAN/WAN/enx cabling ambiguity — re-pin with `--static-ip`; read the console boot log |
| `iw dev` shows interfaces after a run | radios-on leaked; run `radios-off.sh <IP>` (watchdog should catch it) |

## Reaching a TollGate-installed box (post-install address)

A box that has had the TollGate package installed is **not** reachable at the
address you flashed it with. The module moves the wired LAN onto **br-private**
(a `10.x` subnet) during install, so from the gateway:

- **Do not** target `192.168.1.1`. From `cobrador` that address routes out the
  WAN (`ip route get 192.168.1.1` → `via 192.168.2.1`) and the box's firewall
  drops it — a bare TCP connect can even look "open" for the same reason and
  still be a dead end.
- Reach the box on its **directly-connected management net** — the USB-eth link
  the gateway holds. `ip route get <ip>` must show `dev enx…`, never
  `via 192.168.2.1`.

Discovery, in order:

```sh
# 1. which interface (and therefore which net) actually reaches the box?
ip route get 10.57.113.1       # dev enx00e04c683d2d  -> MT3000 br-private
ip route get 192.168.1.1       # via 192.168.2.1      -> OUT THE WAN, blocked

# 2. the two services a film or probe needs, on the management-net IP
curl -s  -o /dev/null -w '%{http_code}\n' http://<mgmt-ip>:2051/splash.html   # portal -> 200
curl -sk -o /dev/null -w '%{http_code}\n' https://<mgmt-ip>/                  # board  -> 200
```

Verified on the fleet (2026-10-15): from `cobrador`, the MT3000 br-private
`10.57.113.1` serves the portal at `:2051` (**200**; `:2050` → **307**) and the
board at `https://10.57.113.1/` (**200**; `:8080` → **307** → `:443`). The
gateway's USB-eth links are `enx00e04c683d2d` → MT3000 `10.57.113.1` and
`enx6c6e0750c33b` → MT6000 `10.63.101.1` (interface names are examples — treat
`ip route get` as authoritative).

**Rules:**
- Run anything that must reach the router **on a host on the router's
  LAN/management side** (the gateway, or whatever the USB-eth cable terminates
  at). Do **not** SSH-tunnel the router's ports to a WAN-side laptop.
- Do **not** add temporary firewall accepts (`nft insert … accept`) to "open a
  path". The path you want already exists on the management net; a hole is
  invasive, is wiped by the next `fw4 reload` / `network restart`, and hides the
  real topology.
- The portal, the board, and any router menu are reached at the
  **management-net IP** — never the flashed LAN IP.

## Mistakes these docs exist to prevent

Written 2026-10-15 after a session wired up tunnels and firewall holes it did
not need, and aimed at the wrong address. Each row is a real error; the section
above is the answer.

| Mistake | Why it's wrong | Do this instead |
|---|---|---|
| Treated a **synthetic** film as needing the router | the recorders render a fake terminal in HTML; many never touch a router | read the recorder first — no `page.goto(router)` means no router needed |
| **SSH-tunnelled** router ports to a WAN-side host | the browser must run where the router is reachable; a tunnel is a fragile detour | run the recorder **on the LAN-side gateway** |
| Punched a **temporary `nft … accept`** on the router | invasive, ephemeral (wiped by the next reload), and it masks the topology | use the management-net path that already exists |
| Aimed at **`192.168.1.1` from the gateway** | post-install the LAN is on **br-private**; `192.168.1.1` routes out the WAN and is dropped | resolve with `ip route get`; use the `dev enx…` address |
| Assumed a "recording host" without pinning **which** host/interface | the same service listens on several addresses; only one is reachable | pin the host **and** run `ip route get` before recording |
