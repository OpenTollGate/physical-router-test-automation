# Bootstrap DNS: real dnsmasq, isolated namespace

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
