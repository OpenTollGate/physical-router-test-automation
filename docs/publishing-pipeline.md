# Publishing Pipeline

[Test results dashboard](https://tests.tollgate.me/) is a client-side SPA that reads test run summaries from Nostr relays and fetches artifacts from Blossom.

## Current Pipeline (kind 30078)

```
pytest → collect_and_render → publish_to_nostr (Blossom upload + kind 30078 summary)
```

1. **Kind 30078 (parameterized replaceable)** — Run summary with pass/fail counts + Blossom artifact URLs
2. All events tagged with `["t", "<project>"]` for project filtering
3. Dashboard fetches kind 30078 from all runner pubkeys

## Legacy DVM Lifecycle (NIP-90) — DEPRECATED

The old pipeline published DVM job lifecycle events (kinds 5900/7000/6900).
This is deprecated. Use kind 30078 instead. See:
- ADR-007 in hackathon-tooling: NIP-90 DVM replaced by ContextVM
- ContextVM migration guide: hackathon-tooling/patterns/contextvm/migration-from-nip-90.md

## Artifact Storage (Blossom)

Artifacts (screenshots, HTML reports, JSON results) are uploaded to Blossom
via BUD-02 PUT /upload with kind 24242 auth events (BUD-11).

Dashboard fetches artifacts on-demand from blossom.psbt.me.
