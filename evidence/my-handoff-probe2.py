
import sys
sys.path.insert(0, "."); sys.path.insert(0, "tests")
import conftest
from _pytest.outcomes import Exit

def check(label, **kw):
    try:
        d = conftest._claim_handoff_guard(**kw)
        print(f"{label}: ALLOWED -> {getattr(d,'detail','')[:70]}")
    except Exit as e:
        print(f"{label}: REFUSED rc={e.returncode} :: {str(e.msg)[:90]}")

BASE = {"TOLLGATE_FLEET_CLAIM_PLACE": "p1",
        "TOLLGATE_FLEET_CLAIM_OWNER": "host/user",
        "TOLLGATE_FLEET_RUN_ID": "run-1"}

check("no-claim        ", environ={})
check("non-owning venue", environ=BASE, owns_place=False, skip_reason="direct")
check("place mismatch  ", environ=BASE, resolved_place="p2", owns_place=True, identity_owner="host/user")
check("agreeing claim  ", environ=BASE, resolved_place="p1", owns_place=True, identity_owner="host/user")
check("foreign holder  ", environ=BASE, resolved_place="p1", owns_place=True, identity_owner="other/run")
check("partial claim   ", environ={"TOLLGATE_FLEET_CLAIM_PLACE": "p1"}, resolved_place="p1", owns_place=True, identity_owner="host/user")
