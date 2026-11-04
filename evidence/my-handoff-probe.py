
import sys, os, inspect
sys.path.insert(0, ".")
import lib.claim_handoff as ch

print("CLAIM_ENV:", getattr(ch, "CLAIM_ENV", "MISSING"))

# 1. No claim env -> guard must return None and import nothing new
import importlib
for k in list(sys.modules):
    if k.startswith("lib.claim_handoff"): del sys.modules[k]
sys.path.insert(0, "tests")
import conftest
res = conftest._claim_handoff_guard(environ={})
print("NO-CLAIM GUARD RESULT:", res, "| module imported?", "lib.claim_handoff" in sys.modules)

# 2. Claim present but venue owns no place -> must refuse
try:
    conftest._claim_handoff_guard(
        environ={"TOLLGATE_FLEET_CLAIM_PLACE": "p1",
                 "TOLLGATE_FLEET_CLAIM_OWNER": "host/user",
                 "TOLLGATE_FLEET_RUN_ID": "run-1"},
        owns_place=False, skip_reason="direct")
    print("!! NO REFUSAL on non-owning venue")
except SystemExit as e:
    print("NON-OWNING VENUE REFUSED rc=%s" % e.code)

# 3. Claim present, place mismatch -> refuse
try:
    conftest._claim_handoff_guard(
        environ={"TOLLGATE_FLEET_CLAIM_PLACE": "p1",
                 "TOLLGATE_FLEET_CLAIM_OWNER": "host/user",
                 "TOLLGATE_FLEET_RUN_ID": "run-1"},
        resolved_place="p2", owns_place=True, identity_owner="host/user")
    print("!! NO REFUSAL on place mismatch")
except SystemExit as e:
    print("PLACE MISMATCH REFUSED rc=%s" % e.code)

# 4. Agreeing claim -> allowed
d = conftest._claim_handoff_guard(
    environ={"TOLLGATE_FLEET_CLAIM_PLACE": "p1",
             "TOLLGATE_FLEET_CLAIM_OWNER": "host/user",
             "TOLLGATE_FLEET_RUN_ID": "run-1"},
    resolved_place="p1", owns_place=True, identity_owner="host/user")
print("AGREEING CLAIM ALLOWED:", not d.refused, "|", d.detail[:60])
