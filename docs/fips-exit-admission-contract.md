# The admission rule exists twice, so it is fenced

The paid-exit admission rule now has two implementations:

- **Go**, in `tollgate-module-basic-go` (`src/fips_exit`: `Challenge.Canonical`,
  `Claim.Verify`, `Derive`, `VerdictFor`, `Redeem`).
- **Rust**, in `fips-exitd`, for the paths that must run without the Go module
  (the exit's own purchase listener, and a router pushing decisions out of
  band).

A rule that lives twice drifts — this repository's own history has the
incidents (`#791` pin, `#746` port table). So neither side is allowed to be the
authority for the other. Both are held to one file:

`contracts/fips-exit-admission-vectors.json`

The vectors are **generated from the Go implementation** by
`tools/conformance/`, reviewed, and then checked in. From that moment the file
is the contract. A change to either implementation that moves a value here is a
contract change, and must show up as a visible diff to that file in the same
commit — not as a silent divergence discovered later on a router.

## What is pinned

| group | cases | what both sides must agree on |
|---|---|---|
| `canonical` | 3 | the exact bytes a buyer signs, and their SHA-256 digest |
| `derive` | 3 | identity key → outer IPv6 address, and the inner IPv4 |
| `verify` | 4 | accept, stolen-npub, not-my-tunnel, tampered signature — with the same sentinel per case |
| `verdicts` | 5 | the decision table, including both boundaries |

The boundary cases are deliberate: `exactly-at-expiry` must be
`revoke-expired` (expiry is inclusive) and `under-budget` must be `keep` while
`exactly-at-budget` must be `revoke-exhausted`. Off-by-one in either direction
is a money bug, and it is the kind of mistake two implementations make
differently.

## Regenerating

```sh
cd tools/conformance
go run . > ../../contracts/fips-exit-admission-vectors.json
```

A regeneration diff is a contract change. Review it, and change both
implementations in the same PR.

## What this does not do

It does not prove either implementation is correct against the real world —
only that the two agree with each other and with the rule as the Go side states
it. Correctness against the kernel lives in
`docs/fips-exit-admission-lane.md`; correctness on a router is still unproven.
