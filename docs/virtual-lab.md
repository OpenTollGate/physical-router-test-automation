# Virtual Lab on Ubuntu host `218`

This document describes the first target architecture for running TollGate tests
without physical routers. The initial target is the local Ubuntu machine reachable
as `218`; cloud providers can come later once the local lab is stable.

## Why local Ubuntu first

Host `218` is suitable for the first implementation:

- Ubuntu 24.04 on x86_64
- AMD Ryzen 7 5700G, 16 logical CPUs
- 31 GiB RAM
- `/dev/kvm` is present, so OpenWrt x86 VMs can run with hardware acceleration

The host is currently memory pressured, so initial VM sizing should be modest:
256-512 MiB RAM per OpenWrt VM and one vCPU per VM.

## Current proof-of-concept topology

The first implemented proof of concept is intentionally small:

```text
Ubuntu host 218
├── OpenWrt VM: tollgate-poc
│   ├── x86_64 OpenWrt 24.10.1 image
│   ├── LAN interface on bridge tg-poc-br
│   └── expected gateway IP 192.168.1.1
└── Linux client network namespace: tg-poc-client
    ├── veth attached to tg-poc-br
    ├── static IP 192.168.1.50/24
    └── default route via 192.168.1.1
```

This proves the fundamental building block: a Linux client can sit behind a
virtual OpenWrt router and reach its LAN gateway without any physical router.

## Full target topology

```text
Ubuntu host 218
├── test runner
│   └── this repository, pytest, Playwright, gh, cashu tooling
├── OpenWrt VM: seller
│   ├── x86_64 OpenWrt image
│   ├── SSH exposed to host on 127.0.0.1:2201
│   ├── TollGate backend on 2121
│   ├── LuCI/uhttpd on 80/8080
│   └── LAN bridge to reseller WAN
├── OpenWrt VM: reseller
│   ├── x86_64 OpenWrt image
│   ├── SSH exposed to host on 127.0.0.1:2202
│   ├── TollGate backend on 2121
│   ├── nodogsplash on client-facing LAN
│   └── WAN bridge to seller LAN
└── Linux client namespace/container
    ├── attached to reseller LAN
    ├── curl/ping for connectivity checks
    └── Chromium + Playwright for captive portal browser flow
```

The important point is that the client is not the host's default network. It
must sit behind the reseller's LAN so nodogsplash/firewall behavior is real.

## What this can validate

The virtual lab should cover:

- API tests that only need SSH + HTTP
- LuCI Playwright tests
- router-to-router / multihop behavior
- reseller session resume behavior
- the upstream nil-pointer panic documented in the Amperstrand fork
- captive portal browser flow from a Linux client
- token typing/paste behavior in the web portal

## What still requires physical hardware

The virtual lab is not Android-equivalent. Physical phone/router testing remains
necessary for:

- Android captive portal notification behavior
- ADB UI automation specifics
- mobile-data fallback behavior
- real WiFi association/deauth quirks
- device-specific OpenWrt target/kernel/package issues

## Existing framework integration points

The current framework is already mostly virtual-lab friendly:

- `lib.router.Router` talks to routers via SSH and HTTP.
- `lib.deploy` deploys packages via SSH/SCP and `opkg`.
- `tests/conftest.py` supports router inventory through `TOLLGATE_ROUTER_ID` and
  `TOLLGATE_ROUTER_INVENTORY`.
- `--client=linux` exists, but it assumes real NetworkManager WiFi via `nmcli`.

For the virtual client, we should add a new client mode later, likely
`--client=netns` or `--client=container`, that runs `curl`, `ping`, and browser
automation inside the client namespace/container instead of using `nmcli`.

## Planned files

- `scripts/virtual-lab.py` — bootstrap and diagnostic entry point for host `218`.
- `config/routers.virtual.example.json` — inventory template for the virtual
  seller/reseller routers.
- Future: `lib/clients/netns.py` — client adapter that executes commands inside
  the virtual client namespace/container.

## POC commands

Run the POC from this Mac against host `218`:

```bash
python3 scripts/virtual-lab.py doctor --host 218
python3 scripts/virtual-lab.py install-deps --host 218
python3 scripts/virtual-lab.py prepare-image --host 218
python3 scripts/virtual-lab.py poc --host 218
```

Inspect or clean up:

```bash
python3 scripts/virtual-lab.py status-poc --host 218
python3 scripts/virtual-lab.py smoke-poc --host 218
python3 scripts/virtual-lab.py stop-poc --host 218
```

Run the pytest POC directly on `218` after checking out this repository there:

```bash
./scripts/setup-python.sh
source ~/.tollgate-test-venv/bin/activate
python3 scripts/virtual-lab.py poc --host local
TOLLGATE_VIRTUAL_LAB=1 pytest tests/api/test_virtual_lab_poc.py -m virtual_lab
```

If you are doing a minimal ad-hoc checkout without the Python test dependencies,
the pytest-timeout options from `pytest.ini` will fail. Install the repo
requirements first; for a one-off proof only, you can bypass repo addopts:

```bash
TOLLGATE_VIRTUAL_LAB=1 python3 -m pytest -o addopts='' tests/api/test_virtual_lab_poc.py -m virtual_lab -q
```

The pytest POC intentionally does not require TollGate yet. It only proves that
the Linux client namespace can reach the virtual OpenWrt router. Once this is
stable, the next step is exposing SSH to the OpenWrt VM and installing TollGate.

## Implementation phases

### Phase 1 — host bootstrap and diagnostics

Verify/install host requirements:

- `qemu-system-x86_64`
- `qemu-img`
- `iproute2`
- `dnsmasq` or equivalent DHCP support for virtual networks
- `curl`
- `python3`
- optional: Docker for the Linux client container

### Phase 2 — OpenWrt image management

Download OpenWrt x86_64 combined ext4 image, convert it to qcow2, and create
per-router overlay disks:

- base image: OpenWrt x86/64, matching the release under test where practical
- seller overlay: `.tmp/virtual-lab/seller.qcow2`
- reseller overlay: `.tmp/virtual-lab/reseller.qcow2`

### Phase 3 — virtual networking

Create Linux bridges or tap-backed namespaces:

- `tg-wan` — upstream/internet-facing test network
- `tg-backhaul` — seller LAN to reseller WAN
- `tg-client` — reseller LAN to client namespace/container

Prefer host-only bridges/taps so we can model traffic precisely and avoid
accidentally changing the host's real uplink.

### Phase 4 — VM boot and provisioning

Boot each OpenWrt VM with QEMU/KVM, expose SSH to localhost ports, and configure:

- root password or SSH key
- LAN/WAN interfaces
- `opkg update`
- test dependencies (`curl`, `socat`, `nodogsplash`, `jq`, `luci`, `px5g-mbedtls`)
- TollGate package/binary

### Phase 5 — client namespace/container

Create a Debian/Ubuntu client attached to reseller LAN with:

- `curl`, `iputils-ping`, `iproute2`
- Chromium + Playwright
- a stable MAC/IP surfaced to the pytest fixtures

This gives us browser-level captive portal coverage without pretending to be an
Android phone.

### Phase 6 — pytest integration

Generate an inventory similar to:

```json
{
  "default": "virtual-reseller",
  "routers": {
    "virtual-seller": {
      "model": "openwrt-x86_64-qemu",
      "luciUrl": "http://127.0.0.1:8081",
      "sshHost": "127.0.0.1",
      "sshUser": "root",
      "sshPort": 2201,
      "arch": "x86_64",
      "tollgateSsidPrefix": "TollGate-",
      "openwrtVersion": "24.10.1",
      "openwrtTarget": "x86/64",
      "openwrtProfile": "generic"
    },
    "virtual-reseller": {
      "model": "openwrt-x86_64-qemu",
      "luciUrl": "http://127.0.0.1:8082",
      "sshHost": "127.0.0.1",
      "sshUser": "root",
      "sshPort": 2202,
      "arch": "x86_64",
      "tollgateSsidPrefix": "TollGate-",
      "openwrtVersion": "24.10.1",
      "openwrtTarget": "x86/64",
      "openwrtProfile": "generic"
    }
  }
}
```

`Router` does not currently accept a port. That needs a small follow-up change
before this inventory can be used directly.

## Immediate next step

Run diagnostics on `218`:

```bash
python3 scripts/virtual-lab.py doctor --host 218
```

If QEMU tools are missing, install the printed package list on `218`, then rerun
the doctor command.

Prepare OpenWrt x86 images and seller/reseller qcow2 overlays:

```bash
python3 scripts/virtual-lab.py prepare-image --host 218
```
