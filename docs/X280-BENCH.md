# x280 bench manager

x280 is the **only** host that runs tests against the lab routers. T14 is a
personal control console and does not run pytest/Playwright or hold router key
material.

## Checkout

```bash
git clone https://github.com/OpenTollGate/physical-router-test-automation.git \
  /home/c08r4d0r/src/prta-main
cd /home/c08r4d0r/src/prta-main
python3 -m venv /home/c08r4d0r/.tollgate-test-venv
/home/c08r4d0r/.tollgate-test-venv/bin/pip install -r requirements.txt
```

The live x280 uses `origin/main` at `3c4a3cc` (or newer) and the pre-created
venv `/home/c08r4d0r/.tollgate-test-venv`.

## OpenBao AppRole

T14 runs the OpenBao store (loopback + FIPS ULA). x280 is a read-only client.

On T14, with an admin token:

```bash
cd /home/c08rad0r/physical-router-test-automation
BAO_TOKEN=... scripts/openbao-approle-apply.sh \
  --role-id-file /tmp/prta-role-id
```

Copy the role ID to x280 and create a SecretID once:

```bash
# on T14 (no value printed)
bao write -f -field=secret_id \
  auth/approle/role/physical-router-automation/secret-id \
  > /tmp/prta-secret-id
scp /tmp/prta-role-id x280:~/.config/prta/role-id
scp /tmp/prta-secret-id x280:~/.config/prta/secret-id
rm -f /tmp/prta-role-id /tmp/prta-secret-id
```

On x280:

```bash
chmod 700 ~/.config/prta
chmod 600 ~/.config/prta/role-id ~/.config/prta/secret-id
printf '%s\n' 'http://[fd79:f451:67b1:8084:2b2a:5b1e:9110:26d0]:8200' \
  > ~/.config/prta/bao-addr
cp ~/.local/bin/bao ~/.local/bin/bao   # install the OpenBao CLI
```

The policy (`config/openbao-policy.hcl`) can read
`secret/data/fleet/test-routers-*` and nothing under `mt6000-*`.

## Bench key

The public half is committed under `bench` in `config/fleet_ssh_keys.yaml`
(`ssh-ed25519 ... x280-bench`). The private half is in OpenBao
`secret/fleet/test-routers-bench:ssh_private_key`; it is not on disk.

`prta-bench-run.sh` fetches it only inside the lease, writes a 0600 temp file,
exports `TOLLGATE_SSH_KEY`, and deletes it after quiesce. x280's general
`id_fleet`/`id_ed25519` are deliberately not authorized on test routers.

To rotate it, generate a new ed25519 key on x280, replace the `bench` public key
in git, install the key set on each router inside the lease, and store the new
private half in OpenBao.

## Bench links

```text
E750    enx9c69d3ca3cfe  192.168.8.244/24  -> 192.168.8.1
MT3000  enx6c6e0750c33b  10.110.223.2/24   -> 10.110.223.1 (br-private/eth1)
COVR    WAN 192.168.50.218                    x280-only firewall rule
```

MT3000's `192.168.1.1` LAN bridge has only AP members; the physical 1G port is
`eth1`, assigned to `br-private` (`10.110.223.1`). Use the private link for
management.

COVR's physical Ethernet port is `eth1` in `br-private`
(`192.168.78.1`). Until a cable is run to a spare x280 adapter, management is
WAN-only with a source-IP firewall rule. The x280 host route pins the source:

```bash
sudo ip route replace 192.168.50.218/32 dev enx6c6e0750cd79 src 192.168.50.10
```

## Install the routes and quiesce watchdog

```bash
cd /home/c08r4d0r/src/prta-main
sudo PRTA_ROOT=$PWD ./deploy/x280/install-bench.sh
```

This installs:

- `prta-bench-routes.service` — restores the E750/MT3000 addresses and the
  COVR `/32` route at boot;
- `prta-quiesce.service` + `prta-quiesce.timer` — every 2 minutes, if the
  bench lease is free, disable all APs;
- `/etc/prta/bench.env` — non-secret interface/address config.

## Private inventory

Copy `config/routers.fleet.example.json` to `config/routers.json` (gitignored)
and keep secrets out of it. The live inventory has `e750-lab`,
`tollgate-af92`, `tollgate-9bd4`, and `comfast-b3ba` (pending reset).

## Run a test

```bash
cd /home/c08r4d0r/src/prta-main
scripts/hw-bench-lease exec --purpose "test-pr 42" -- \
  scripts/prta-bench-run.sh -- ./scripts/test-pr.sh --pr 42
```

`prta-bench-run.sh` refuses without the lease, fetches the bench key from
OpenBao, exports `TOLLGATE_SSH_KEY`, and quiesces before removing the key. The
2-minute watchdog also fetches the bench key for cleanup when the lease is free.

## Verification

```bash
python3 scripts/check-protected.py --board glinet,gl-mt6000   # REFUSED
python3 scripts/check-protected.py --model GL-E750            # NOT_PROTECTED
python3 scripts/quiesce-router.py --all --allow-unlocked      # QUIESCE_DONE
scripts/hw-bench-lease status
```

## Troubleshooting

- **COVR SSH refused**: source is `192.168.50.180`; pin the host route or add
  `sshBind: 192.168.50.10`.
- **MT3000 unreachable**: the adapter must be up with `10.110.223.2/24`; use
  `prta-bench-routes.service` or `ip addr replace`.
- **E750 missing fw_env**: create
  `/etc/fw_env.config` = `/dev/mtd1 0x0 0x10000 0x10000`.
- **E750 serial TX garbles**: use SSH/LuCI; serial is for U-Boot recovery.
- **A router cannot be quiesced**: mark it broken, cut power/switch port if
  possible, and do not run further tests against it.
