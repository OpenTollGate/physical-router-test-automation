
import sys
sys.path.insert(0, ".")
from lib.claim_handoff import release_verdict

def show(label, **kw):
    v = release_verdict(**kw)
    print(f"{label:34s} -> {v.state.value:16s} {getattr(v,'detail','')[:60]}")

ME = "host/user"
show("never acquired", acquired=False, cleanup_pending=False)
show("released, owner gone", acquired=False, cleanup_pending=False, owner_after="", our_owner=ME, place="p1")
show("released, still ours", acquired=True, cleanup_pending=False, owner_after=ME, our_owner=ME, place="p1")
show("release raised", acquired=True, cleanup_pending=True, error=RuntimeError("boom"), our_owner=ME, place="p1")
show("released, owner unknown", acquired=False, cleanup_pending=False, owner_after=None, our_owner=ME, place="p1")
show("someone else holds it", acquired=False, cleanup_pending=False, owner_after="other/run", our_owner=ME, place="p1")
