"""Independent integration checks for reservation-time authorization."""
from lib.fleet_authority import Demand, LaneSpec
from lib.run_admission import AdmissionRefused
from test_runner_manifest_authorization import setup, runner
import pytest


def test_identity_is_checked_after_admission_construction(tmp_path):
    args, auth, budget = setup(tmp_path)
    admission = runner.admission_from_args(args, "identity-changed")
    auth.register_lane(LaneSpec(lane_id="lane-a", fleet_id="other", resource_id="other", **budget))
    with pytest.raises(AdmissionRefused, match="lane_identity_mismatch"):
        admission.acquire()
    assert auth.allocations() == ()


@pytest.mark.parametrize("expected", [{"expected_fleet_id": "other"}, {"expected_resource_id": "other"}])
def test_replay_cannot_skip_identity_check(tmp_path, expected):
    args, auth, _ = setup(tmp_path)
    admission = runner.admission_from_args(args, "replay")
    allocation = admission.acquire()
    decision = auth.try_reserve(lane_id="lane-a", run_id="replay", demand=allocation.demand,
                                domains=allocation.domains, **expected)
    assert not decision.granted
    assert decision.reason == "lane_identity_mismatch"
    assert len(auth.allocations()) == 1
    admission.release()
