#!/usr/bin/env bash
# FLEET-CAP-3 qualification runner — the one command that decides this slice.
#
# Exit codes are the contract:
#   0  QUALIFIED   every gate ran and passed
#   1  NOT QUALIFIED at least one gate ran and failed
#   2  BLOCKED     an environmental prerequisite is missing (labgrid, KVM, …)
#   3  REFUSED     the host has no measured safe headroom for the benchmark
#
# Gates:
#   A identity + ownership semantics      tests/unit/test_labgrid_venue_ownership.py
#   B real pinned coordinator integration tests/integration/test_labgrid_coordinator_real.py
#   C aggregate host admission (OWED)     tests/unit/test_fleet_authority_aggregate.py --runxfail
#   D benchmark admission gate + contract tests/unit/test_fleet_bench_gate.py
#   E benchmark measurement               scripts/fleet-bench.py (3 configurations)
#
# Gate C is run with --runxfail ON PURPOSE. Its xfail markers document an owed
# capability; pytest would exit 0 on expected failures, which must never be
# reported as a pass. With --runxfail the missing host pool is a hard failure.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

PYTHON="${TOLLGATE_QUALIFY_PYTHON:-python3}"
OUT="${TOLLGATE_QUALIFY_OUT:-results/fleet-cap-3}"
mkdir -p "$OUT"

fail=0
refused=0
record() { printf '%-10s %s\n' "$1" "$2" | tee -a "$OUT/qualification.txt"; }

echo "== FLEET-CAP-3 qualification $(date -Is)" | tee "$OUT/qualification.txt"
echo "   repo    $REPO_ROOT" | tee -a "$OUT/qualification.txt"
echo "   head    $(git rev-parse HEAD 2>/dev/null || echo unknown)" | tee -a "$OUT/qualification.txt"
"$PYTHON" - "$OUT" <<'PY' | tee -a "$OUT/qualification.txt"
import json, subprocess, sys, os
out = sys.argv[1]
try:
    import labgrid
    version, path = getattr(labgrid, "__version__", "?"), labgrid.__file__
except Exception as exc:                                  # noqa: BLE001
    version, path = f"absent ({exc.__class__.__name__})", ""
print("   labgrid", version, path)
try:
    sha = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
except OSError:
    sha = "unknown"
json.dump({"python": sys.version, "labgrid": version, "labgrid_path": path, "head": sha},
          open(os.path.join(out, "toolchain.json"), "w"), indent=2)
PY

# --- gate A -----------------------------------------------------------------
if "$PYTHON" -m pytest tests/unit/test_labgrid_venue_ownership.py -q -p no:cacheprovider \
        >"$OUT/gate-A.log" 2>&1; then
    record "gate A" "PASS  per-run identity + owned release (unit, doubles)"
else
    record "gate A" "FAIL  see $OUT/gate-A.log"; fail=1
fi

# --- gate B -----------------------------------------------------------------
# Never skip-green: if labgrid is absent this exits 1 with "not installed".
if "$PYTHON" -m pytest tests/integration/test_labgrid_coordinator_real.py -q -p no:cacheprovider \
        >"$OUT/gate-B.log" 2>&1; then
    record "gate B" "PASS  real pinned coordinator (no doubles)"
else
    record "gate B" "FAIL  see $OUT/gate-B.log"; fail=1
fi

# --- gate C -----------------------------------------------------------------
if "$PYTHON" -m pytest tests/unit/test_fleet_authority_aggregate.py -q -p no:cacheprovider --runxfail \
        >"$OUT/gate-C.log" 2>&1; then
    record "gate C" "PASS  aggregate host admission"
else
    record "gate C" "NOT QUALIFIED  aggregate host admission missing (owed) — see $OUT/gate-C.log"; fail=1
fi

# --- gate D -----------------------------------------------------------------
if "$PYTHON" -m pytest tests/unit/test_fleet_bench_gate.py -q -p no:cacheprovider \
        >"$OUT/gate-D.log" 2>&1; then
    record "gate D" "PASS  benchmark admission gate + lane isolation contract"
else
    record "gate D" "FAIL  see $OUT/gate-D.log"; fail=1
fi

# --- gate E -----------------------------------------------------------------
# The benchmark decides for itself: 3 = refused for lack of measured headroom.
# A refusal does NOT mask a failed gate A-D: the exit code is 1 if anything else
# failed, and it is 3 only when the benchmark is the sole outstanding item.
rc=0
for cfg in serial parallel oversubscribed; do
    rc=0
    "$PYTHON" scripts/fleet-bench.py --config "$cfg" --jobs "${TOLLGATE_BENCH_JOBS:-2}" \
        --out "$OUT" >>"$OUT/gate-E.log" 2>&1 || rc=$?
    if [ "$rc" -eq 3 ]; then
        record "gate E" "REFUSED  $cfg — $("$PYTHON" -c "
import json
try: print(json.load(open('$OUT/admission-$cfg.json'))['reason'][:300])
except Exception: print('see gate-E.log')")"
        refused=1
        break
    elif [ "$rc" -ne 0 ]; then
        record "gate E" "FAIL  $cfg rc=$rc — see $OUT/gate-E.log"; fail=1
        break
    fi
done
if [ "$fail" -eq 0 ] && [ "$refused" -eq 0 ]; then
    record "gate E" "PASS  3 configurations measured — see $OUT/{serial,parallel,oversubscribed}.json"
elif [ "$refused" -eq 1 ]; then
    record "gate E" "REFUSED (not run) — no measured safe host headroom"
fi

if [ "$fail" -ne 0 ]; then
    echo "RESULT: NOT QUALIFIED (see gates above)" | tee -a "$OUT/qualification.txt"
    [ "$refused" -eq 1 ] && echo "NOTE: benchmark additionally REFUSED for lack of safe host headroom" | tee -a "$OUT/qualification.txt"
    exit 1
elif [ "$refused" -eq 1 ]; then
    echo "RESULT: REFUSED — gates A-D pass; benchmark not run (no measured safe host headroom)" | tee -a "$OUT/qualification.txt"
    exit 3
fi
echo "RESULT: QUALIFIED (QEMU only; physical hardware unproven)" | tee -a "$OUT/qualification.txt"
exit 0
