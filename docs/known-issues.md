# Known Issues

## IPv4 Loopback Can Become Stale (Kernel 6.x + Docker)

### Symptom

TCP connections to `127.0.0.1` time out silently. IPv6 (`::1`) works fine.
External connections (to LAN/WAN IPs) are unaffected.

```
curl http://127.0.0.1:3338/   # Times out
curl http://[::1]:3338/        # Works
```

### Root Cause

The Linux loopback interface (`lo`) can enter a stale state where IPv4
packets are silently dropped. The interface shows as `UP` with the correct
address (`127.0.0.1/8`), routing is correct, and no iptables/nftables rules
block the traffic — but IPv4 TCP connections time out.

This has been observed on:
- Kernel 6.17.0-35-generic (Ubuntu)
- Systems running Docker with custom bridge networks (`br-*`, `tg-poc-br`)
- After repeated Docker network create/destroy cycles

The exact kernel-level mechanism is unknown. It is NOT caused by:
- iptables/nftables rules (INPUT/OUTPUT chains are empty, policy ACCEPT)
- `rp_filter` (setting to 0 doesn't fix it)
- AppArmor/SELinux (root also fails)
- seccomp (disabled)
- conntrack table exhaustion (count is low)

### Fix

Restart the loopback interface:

```bash
sudo ip link set lo down
sudo ip link set lo up
```

This re-initializes the kernel's IPv4 loopback path. After restart, both
IPv4 and IPv6 loopback work normally.

### Verification

```bash
python3 -c "
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.settimeout(2)
try:
    s.connect(('127.0.0.1', 1))
except ConnectionRefusedError:
    print('IPv4 loopback: OK')
except socket.timeout:
    print('IPv4 loopback: BROKEN — run: sudo ip link set lo down && sudo ip link set lo up')
s.close()
"
```

### Code Mitigation

The local dry testing code includes automatic detection:

- `lib/mock_mint.py`: `_detect_bind_address()` tries IPv4 first, falls back to IPv6
- `lib/local_process.py`: `detect_loopback()` returns the working address
- `scripts/local-test.sh`: `LOOPBACK` env var (default `127.0.0.1`, override with `TOLLGATE_LOOPBACK=[::1]`)

If IPv4 loopback is broken on your machine and you can't fix it with `ip link`:

```bash
export TOLLGATE_LOOPBACK="[::1]"
./scripts/local-test.sh
```

### Prevention

Avoid creating/destroying Docker bridge networks repeatedly without cleaning
up. If you see this issue after Docker operations, restart `lo` as shown above.
