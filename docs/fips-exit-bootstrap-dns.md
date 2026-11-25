# Bootstrap DNS: real dnsmasq, isolated namespace

## Exit and loopback split runtime fixtures

```sh
python3 scripts/fips-exit-bootstrap-dns.py --exit-runtime \
  --module-repo "$HOME/worktrees/tollgate-fips-exit-consumer" \
  --go "$HOME/go/pkg/mod/golang.org/toolchain@v0.0.1-go1.26.10.linux-amd64/bin/go"
```

[Runtime evidence](evidence/fips-exit-dns-runtime.log) records unchanged
`RenderExitDNS` output, module source SHA and config SHA256. Real dnsmasq forwards
one guest `public.example` A query to a labelled UDP fixture at derived exit IPv6
port 53; the guest gets NOERROR / 192.0.2.73, and the fixture asserts the observed
source is derived router IPv6 `fd13:2f39:a98c:31ba:addb:a652:5f5d:43f2`.
The fixture handles dnsmasq's appended EDNS OPT without echoing it as an answer
(an initial run rejected that OPT record, then cleaned up successfully).
One unique `split-<random>.fips` A query then receives 192.0.2.74 from the same
UDP fixture bound to the generated loopback endpoint `127.0.0.1:5354`. Its query
count is 1; the still-running exit fixture count stays 1 before/after that query
(including a bounded 200 ms observation after the response).
A subsequent exact `portal.example` A query receives NOERROR / one answer /
10.73.1.1 locally; both live fixture counts remain `[1, 1]` before/after the query
(exit, `.fips` order, including a bounded 200 ms observation).
The successful run exits 0, reaps all children and verifies the private namespace
registry and temporary paths are empty. It reuses the existing Runner and isolated
mount/network harness; host resolver/network and module sources are unchanged.

After those successful probes, only the exit fixture is stopped. One unique
`exit-down-<random>.example.com` A query has a 15-second deadline; the probe
records SERVFAIL separately from timeout and rejects successful answers.
[Exit-down evidence](evidence/fips-exit-dns-exit-down.log) records a timeout.
No local-continuity checks or topology/firewall changes are added.

[WAN positive-control evidence](evidence/fips-exit-dns-wan-control.log) adds one
reused UDP fixture at `10.73.3.2:53`. A direct router-side query returns
NOERROR / one A answer / `192.0.2.75`, incrementing its count from 0 to 1.
The live process handle, count path and control count remain available for later
fallback assertions. This is **positive-control-only, not no-fallback proof**:
no `resolv.conf` changes or new exit-down assertions are included.

This is IPv6 veth plus **synthetic UDP DNS fixtures, not real FIPS**. No
portal HTTP, exhaustive leakage, payment, hardware or production tests run in
this mode. It does not run the bootstrap probes or WAN counter controls below;
**this is not WAN-decoy proof**.


## Exit composer: parse-only check (no service)

```sh
python3 scripts/test_fips_exit_dns_parse.py
python3 scripts/fips-exit-bootstrap-dns.py --parse-only \
  --module-repo "$HOME/worktrees/tollgate-fips-exit-consumer" \
  --go "$HOME/go/pkg/mod/golang.org/toolchain@v0.0.1-go1.26.10.linux-amd64/bin/go"
```

Use an existing **native Go 1.26.10 binary**: an older Go launcher that normally
selects 1.26.10 via `GOTOOLCHAIN` is refused because this helper disables downloads
and toolchain switching. Requires existing dnsmasq, no sudo. Temporary files use
`TMPDIR`; the two explicit `--conf-file` paths prevent default config inheritance.

The exit mode extracts unchanged `bootstrap_dns.go`, `exit_dns.go`,
`upstream_dns.go` and `address.go` from module commit
`3dcbe79462a827731c0e751cc3719d7a3c2e0481` into a standalone temporary Go module.
It calls actual `RenderExitDNS` with public secp256k1 G/2G fixture x-coordinates,
portal 10.73.1.1:5353 and loopback FIPS resolver 127.0.0.1:5354. It writes the
returned config unchanged, then tests a separate copy with one invalid directive.
Both exact argv/status/diagnostics and config SHA256 are logged. A rejected original
is reported as a module defect; it is never patched to get a green parser result.

[Real parser evidence](evidence/fips-exit-dns-parse.log): dnsmasq 2.91 accepted
unchanged output (rc **0**), rejected invalid-directive control (rc **1**), and the
harness exited **0**. Generated SHA256:
`3203c06773cf6d441837961028ba973782050dc08e61b5049a88fbee2262f71d`.
Three offline harness contract tests pass (including failure controls).
[Bounded bootstrap regression](evidence/fips-exit-bootstrap-dns-regression.log)
also exited 0 with the original pinned bootstrap source, expected answers,
unchanged WAN counters and successful cleanup.

**What this does not prove:** parse-only starts no DNS service, creates no packet
topology and tests no FIPS transport, forwarding, source binding, leakage, payment,
or deployment. The separately invoked bootstrap regression remains bootstrap-only.
Full hardware/deployment suites are outside this bounded verification slice.

## Existing bootstrap runtime regression

Run from this checkout (requires existing dnsmasq, ip, nft, ss, sudo/unshare and Go 1.26.10; installs nothing):

```sh
python3 scripts/fips-exit-bootstrap-dns.py \
  --module-repo "$HOME/worktrees/tollgate-fips-exit-consumer" \
  --go "$HOME/.cache/tollgate-tools/go/1.26.10/go/bin/go"
```

The bounded lane reuses `hwsim-netns-poc.py`'s canonical Runner, not its
radio/hardware lifecycle. It compiles the unchanged `bootstrap_dns.go` git object
at `9320319f05da69a2f13e78c8f0d78b16ab3fa376` in a temporary standalone module,
calls `RenderBootstrapDNS`, logs its exact bytes/hash and uses those bytes unchanged.
Go toolchain downloads are disabled. No production module edits occur.

An outer private mount+network namespace and tmpfs `/run/netns` isolate the named
bootstrap/guest/WAN namespaces from host networking and the host namespace registry.
`dnsmasq --test --conf-file=<private-file>` must succeed before a DNS-only process
starts on 10.73.1.1:5353 with an explicit conf-file, nobody/nogroup identity and no
pidfile. No DHCP options, host resolver writes, service restarts or router operations
are involved. Process readiness is bounded and cleanup reaps the child, removes
private namespaces and temporary paths even on assertion failure/SIGTERM.

## Recorded outcome

See [raw evidence](evidence/fips-exit-bootstrap-dns.log). Installed dnsmasq was 2.91;
Go was 1.26.10. Parser rc=0 (`syntax check OK`). From the private guest namespace:

- `portal.example` A: NOERROR, one answer, 10.73.1.1.
- `unrelated.example` A: NXDOMAIN, zero answers.
- `random-9320319f.portal.example` A: NXDOMAIN, zero answers (not wildcard matching).
- WAN-decoy input UDP/TCP port-53 counters: `[1,0]` before and `[1,0]` after probes.
  A deliberately sent UDP positive control created that initial count of 1.
  These are real response codes and observed counters, not timeout-derived safety.

Scope: IPv4 UDP A-query bootstrap behavior with this concrete listen address/port.
The configured default route points at the isolated WAN decoy; it has no host/external
uplink. The unchanged config specifies no upstream (`no-resolv`, `local=/#/`), so
there is no configured-upstream listener to count. WAN counters observe conventional
port-53 traffic only; the TCP counter has no separate positive control. This is not
an exhaustive leakage test, IPv6/TCP DNS coverage, fw4 redirection proof, production
integration, paid DNS or FIPS transport evidence. NXDOMAIN is the measured negative
answer, not DNS RCODE REFUSED. No parser/address/port failure was observed.
