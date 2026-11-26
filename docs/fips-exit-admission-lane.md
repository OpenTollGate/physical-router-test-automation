# The paid-exit admission lane

This lane proves the money-side handshake of a paid FIPS exit against the real
daemon and the real kernel, on real packets. It does not prove the exit works
in production; read the limits before quoting a PASS.

## What it runs

One isolated root network namespace (`sudo -n unshare -nm`, private
propagation, a tmpfs mounted over `/run`). Inside it: a bridge `fips0`
carrying `fd0e::1` as the exit, four client namespaces, and an `inet`
responder that answers UDP `:4444`. The real `fips-exitd` runs with an
explicit `--clients` file and `USAGE_REPORT_INTERVAL_MS=200`.

`tools/admissionctl/` is a test shim, not production code. It drives the
module's `fips_exit` admission API (`Grant`, `VerdictFor`, `Apply`,
`Reconcile`, `RenderClientsFile`) and implements the `Store` seam with a real
temp-file-plus-rename write. It exists so the lane can make admission
decisions the same way the TollGate purchase path eventually will, without
this lane pretending to be a payment system.

## What it proves

- (a) **Unpaid is blocked.** A client absent from the clients file gets no
  answer at all: three attempts, all `timeout`, and it never appears in the
  usage report.
- (b) **A grant admits.** After `grant`, the daemon logs
  `client list reloaded, 1 client(s)` on SIGHUP, the same client receives its
  100 bytes, and the usage report carries a row for its address with
  `from_client_bytes = 31`.
- (c) **Revocation stops an existing path.** The verdict is taken one second
  past expiry; the rendered clients file is empty; the daemon logs
  `0 client(s)`; and the *same* client query — the one that just returned 100
  bytes — now times out on the **first** post-revoke attempt, not after a
  retry.
- (d) **An operator's hand-added line is preserved, not adopted.** Reconcile
  keeps it in the file and reports it `needs-grant`; the daemon admits on file
  presence, so the line does grant transit while the module still refuses to
  treat it as an entitlement.
- (e) **Restart neither grants free access nor loses a paid one.** After
  killing and restarting the daemon with the same state and clients file: a
  still-valid grant keeps working (`100`), an expired grant stays blocked,
  a never-granted client stays blocked, and the operator line still gives no
  admission the module recognises.

The meter also survives revocation — the revoked peer's row
(`from_client_bytes = 31, to_client_bytes = 128, packets = 2`) is still there
after the grant is gone. Admission ended; the meter did not forget.

## What it does NOT prove

- **Not a live exit.** Everything above is one namespace on one machine. No
  production `fips-exitd`, no real FIPS mesh, no router.
- **(d) is the least clean result.** The daemon admits any address present in
  the clients file, so an operator line *does* pass traffic. That is the
  daemon's documented behaviour, but it means file presence alone is
  admission. This lane shows the module refusing to call it an entitlement; it
  does not make the daemon enforce entitlement.
- **No payment.** Nothing verifies Cashu, mints, or tokens. The shim grants
  because a test told it to.
- **No authenticated peer.** The module's `claim` layer (challenge, signature,
  binding to the observed source address) is not exercised here.
- **No expiry enforcement on an in-flight flow mid-transfer.** (c) proves a
  blocked *subsequent* query; it does not prove a long-lived TCP transfer is
  torn down the moment a budget is exhausted.
- **No DNS.** The lane does not exercise the daemon's client DNS, and on a
  real router that bind collides with dnsmasq on `:53`.
- **No OpenWrt.** On real hardware the exit interface also needs firewall
  ownership: `fw4`'s `input` chain is `policy drop` and knows nothing about a
  new exit interface, so neighbour solicitation is dropped until that
  interface is given a zone by a sanctioned fragment.

## Reproducing

```sh
cd physical-router-test-automation
sudo -n unshare -nm --propagation private \
  sh -c 'mount -t tmpfs tmpfs /run && exec python3 scripts/fips-exit-admission-lane.py'
```

The lane tears down its own namespaces and children, and asserts that the
host's `/run` carries no artefact and that its mount identity is unchanged.
