# AGENTS.md — Operational Knowledge for physical-router-test-automation

This file contains hard-won operational knowledge for agents and humans working with physical GL.iNet GL-MT3000 routers running OpenWrt. Read this before touching a router.

## Lessons Learned

### `chpasswd` does not exist on OpenWrt BusyBox

OpenWrt's BusyBox does not ship with `chpasswd`. Attempting `echo 'root:pw' | chpasswd` in a uci-defaults script will fail. Use `printf '%s\n%s\n' 'pw' 'pw' | passwd root` instead.

**Impact**: When this failed in our uci-defaults script with `set -eu`, the entire script aborted. The SSH key was written (it came before chpasswd) but the WAN firewall rule never got added. The router was unreachable from WAN until we connected via LAN.

### Never use `set -eu` in uci-defaults scripts

uci-defaults scripts run once on first boot. If you use `set -eu`, any single command failure aborts the entire script. Later commands (like firewall rules) never execute. This can leave the router in an unreachable state.

Instead: let each command run independently. If a command might fail, handle it explicitly. The `exit 0` at the end is what tells OpenWrt to delete the script — it must always be reached.

### SCP requires `-O` flag for OpenWrt

OpenWrt's BusyBox does not include `sftp-server`. Modern OpenSSH defaults to SFTP for SCP transfers, which fails with `ash: /usr/libexec/sftp-server: not found`. Always use `scp -O` (legacy SCP protocol) when copying files to OpenWrt routers.

### The ASU API blocks Python's default User-Agent

The OpenWrt ASU (Attended Sysupgrade) server at `sysupgrade.openwrt.org` returns HTTP 403 for requests with the default `Python-urllib/3.x` User-Agent. Set a custom `User-Agent` header (e.g., `tollgate-build-firmware/1.0`) on all requests to the ASU API.

### Don't accidentally modify the main router

When testing router access from a machine that also has SSH access to the upstream/main router, be extremely careful with IP addresses. Commands like `passwd` or `chpasswd` run without confirmation. Always verify which host you're SSH'd into before running destructive commands.

## Router Access Patterns

### GL-MT3000 Default IPs

| Mode | IP | Access |
|---|---|---|
| OpenWrt factory defaults (LAN) | 192.168.1.1 | SSH (no password), HTTP (LuCI if installed) |
| GL.iNet stock firmware (LAN) | 192.168.8.1 | HTTP admin panel |
| WAN (DHCP from upstream) | Assigned by DHCP | SSH only if WAN firewall rule exists |
| U-Boot recovery | 192.168.1.1 | HTTP only (web UI for firmware upload) |

### SSH Authentication

The test framework uses `sshpass` (password auth) via `TOLLGATE_LUCI_PASSWORD`. Custom firmware images built with `scripts/build-firmware.py` embed both an SSH key and a random password. Both methods work.

### Network Topology

```
Internet → Main Router (192.168.13.1) → Switch → TollGate Router WAN (192.168.13.112)
                                                ↕
                                           Test Machine (192.168.13.244)
```

When connected directly to the TollGate router's LAN port, the test machine gets an IP in 192.168.1.0/24 (en6 on current setup).

## Firmware Build + Flash Workflow

### Build

```bash
scripts/build-firmware.py --router lab-router-a
```

Reads `config/routers.json` for target/profile/version. Auto-detects SSH key from `~/.ssh/`. Generates random password. Builds via ASU API. Downloads sysupgrade image. Saves credentials to `credentials/<router-id>.json`.

### Flash via SSH (LAN or WAN)

```bash
scripts/build-firmware.py --router lab-router-a --flash
```

Or manually:

```bash
scp -O <image.bin> root@<router-ip>:/tmp/
ssh root@<router-ip> "sysupgrade -n /tmp/<image.bin>"
```

`sysupgrade -n` wipes all config. The router reboots. SSH connection dies with exit code 246 (expected).

### Flash via U-Boot (recovery)

For bricked routers that won't boot properly. See the U-Boot section below.

## U-Boot Recovery

### Entering U-Boot Mode (GL-MT3000)

1. Disconnect power from router
2. Connect computer to router's **LAN port** via Ethernet (leave WAN disconnected)
3. Set computer IP to 192.168.1.x (e.g., 192.168.1.2, subnet 255.255.255.0)
4. **Press and hold the Reset button**
5. **While holding Reset, apply power**
6. Watch the LED: blue flashes ~6 times, then turns **solid white**
7. **Release Reset** when LED is solid white
8. U-Boot web UI is now at `http://192.168.1.1`

### Headless Upload via curl

```bash
curl -X POST -F gl_firmware=@<firmware.bin> http://192.168.1.1/index.html
```

The form field name is `gl_firmware`. Wait ~3 minutes. Don't power off. The router reboots automatically.

### Automated Recovery Script

```bash
scripts/uboot-recover.py --image <firmware.bin> [--interface en6]
```

Uses macOS `say` command for voice guidance. Auto-detects U-Boot mode via ping. Uploads firmware via curl. Monitors for reboot completion.

### Browser-Based Recovery (fallback)

Use Chrome or Edge (NOT Firefox — may brick the router). Visit `http://192.168.1.1` in U-Boot mode. Upload firmware via the web form. Wait ~3 minutes.

## Common Recovery Scenarios

### Router boots but no SSH from WAN

Likely: WAN firewall rule missing. Connect via LAN (192.168.1.1) and add the rule:

```bash
uci add firewall rule
uci set firewall.@rule[-1].name='Allow-SSH-WAN'
uci set firewall.@rule[-1].src='wan'
uci set firewall.@rule[-1].dest_port='22'
uci set firewall.@rule[-1].proto='tcp'
uci set firewall.@rule[-1].target='ACCEPT'
uci commit firewall
fw4 restart
```

### uci-defaults script didn't run / partially ran

Check if the script still exists:

```bash
ls /etc/uci-defaults/
```

If `99-asu-defaults` is still there, it failed partway through. Read it, fix the issue, run the remaining commands manually, then delete it.

### Router not getting WAN IP

After `sysupgrade -n`, the WAN port is configured for DHCP by default. Check that the upstream network is providing DHCP. Verify with `ping 192.168.13.1` from the router.

## Security Notes

- Built firmware images contain credentials (SSH key + password) in the uci-defaults script. Treat images as sensitive.
- Credentials are saved to `credentials/` (gitignored, mode 600).
- SSH key comments are stripped before embedding (no user@host in images).
- WAN SSH is acceptable for lab routers. Disable on production.
- The ASU server sees build requests over HTTPS. Acceptable for test firmware.
