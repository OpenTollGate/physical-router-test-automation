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

## Domain Setup

**Dashboard is a Cloudflare Worker** deployed via `test-dashboard/wrangler.jsonc`.
Deploys via `.github/workflows/deploy-reader.yml` on push to `docs/**`.

**Worker domain**: `tests.cashu.email` (Cloudflare Worker with `custom_domain: true`)

**Alias domain**: `tests.tollgate.me` — `tollgate.me` is in a separate
Cloudflare account, so it can't use Worker `custom_domain`. Instead, it uses
a Redirect Rule in the `tollgate.me` zone that 301-redirects to
`tests.cashu.email`. To set this up:

**tollgate.me Cloudflare Dashboard → Rules → Redirect Rules → Create**

```
Rule name: tests-redirect
When: http.host eq "tests.tollgate.me"
Then: Static redirect
  URL: https://tests.cashu.email${http.request.uri}
  Status: 301
```

Once configured, `tests.tollgate.me/foo` → `tests.cashu.email/foo` instantly.
