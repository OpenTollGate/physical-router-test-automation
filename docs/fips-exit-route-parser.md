# Offline FIPS exit route parser

`scripts/fips-exit-routes.py` provides `parse_routes(raw, query_table=200)` for the lane-specific IPv4 table-200 inventory. It accepts the unreachable safety default and optional `exit-test` default, rejecting duplicate, missing safety, changed, or unknown route shapes rather than filtering them.

Run the offline regression tests from the repository root:

```sh
python3 scripts/test_fips_exit_routes.py
```

The tests use `docs/evidence/fips-exit-ipip6-down-up.log`. The caller must supply complete output from a successful `ip -j -4 route show table 200` query and trusted query context. The parser cannot establish completeness, namespace identity, freshness, or ownership; it does not collect RecoveryEvidence, apply recovery operations, or prove packet recovery.
