# The extensive test — runbook

One command, three maps, every path class. This is the entry point for
the extensive-test effort; the behavioral ground truth it should cover
lives in **`docs/user-journeys.md`** (realistic persona journeys ×
happy/failure/regression paths) and the generated
**`docs/user-stories.generated.md`** (story × test map —
`python3 scripts/story-coverage.py` to regenerate).

## Quick start

```bash
./scripts/extensive-test.sh                        # virtual venue, full tier sweep
STORIES="JG1-mapped G2,G4" ./scripts/extensive-test.sh   # scoped below
PATHS="failure" ./scripts/extensive-test.sh        # failure paths only
TOLLGATE_VENUE=physical TOLLGATE_SSH_HOST=192.168.1.1 \
  TOLLGATE_IPK=packaging/tollgate-wrt_vX_x86_64.ipk ./scripts/extensive-test.sh
```

Artifacts land in `results/extensive-<UTC stamp>/`: per-tier junit +
logs, plus copies of both maps. Exit code = worst tier.

## Venue readiness (refreshed 2026-10-15 — live-probed; see docs/testing-cycles.md for rail cycle times)

| Venue | State | Bring-up |
|---|---|---|
| Virtual rail (br-laneA venue, ai-legion-small) | **UP** — one-command via `scripts/release-validate.sh` (tag→build→deploy→suite, ~15 min) | none; image persists |
| Virtual lab (ai-legion poc QEMUs) | host-memory dependent — #241's preflight fails fast when starved | clear host memory or use the br-laneA venue |
| TollGate-326D (live MT3000) | **MIA** per owner | locate, cable to bench-owrt lan3 (VLAN 1003) |
| ALPHA/BRAVO (x1860 labgrid) | dark — awaiting bench cabling (seats lan5/lan4 exist; lan6/7 free for more) | owner cabling + PSUs |
| NR7101 (.12.124) | **reachable from ai-legion-small (root key-auth), running tollgate-wrt rc5 mipsel**, WiFi AP `TollGate-0805` broadcasting — but client-initiated TCP to the gateway never arrives (portal unusable; tmbg#946) | discriminator test or fix per #946 |
| Phone | attached on ai-legion (`numo-phone` place — **shared resource**), adb authorized; **browser suspended by a supervision profile** — UX lane blocked, adb-shell lane works | owner lifts supervision |

## Scoping knobs

- `TIERS` — pytest tier markers, default `smoke critical extended`
  (hierarchical: smoke ⊂ critical ⊂ extended).
- `STORIES` — story ids from `docs/story-catalog.yml`, comma/space list
  (`--story` is OR-within; `pytest --story G2 --tip TIP-02` is AND-across).
- `PATHS` — `happy`, `failure`, `regression` path classes (new tests tag
  them; legacy files are classified per-journey in `docs/user-journeys.md`).
- `TIPS` — TIP conformance items (`TIP-01..03` currently exercised).

## What "done" looks like for the extensive test

Per `docs/user-journeys.md`: every journey's happy row green against the
release-candidate build; every failure row green (bounded, loud, honest
failures — not passes, *correct failure behavior*); every regression row
green; and each **GAP** row either gains a test or an explicit
venue-blocked note. Known chases while doing it: **issue #10** (deployed
Go build never recovered from degraded when the mint returned — the
mint-returns assertion is mandatory against the rc build) and the G7
portal-copy UI remainder.
