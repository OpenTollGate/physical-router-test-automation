
import sys
sys.path.insert(0, ".")
from lib.claim_handoff import release_verdict
import inspect
print("signature:", inspect.signature(release_verdict))

# Fail-closed default: unknown/absent evidence must never yield clean.
cases = [
    ("no release attempted", dict()),
]
for label, kw in cases:
    try:
        v = release_verdict(**kw)
        print(f"{label}: {v.state.value if hasattr(v.state,'value') else v.state} :: {getattr(v,'detail','')[:80]}")
    except TypeError as e:
        print(f"{label}: needs args -> {e}")
