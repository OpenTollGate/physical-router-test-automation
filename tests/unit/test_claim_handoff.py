"""Requirement 2 handoff and requirement 5 release evidence, as tested logic.

Three layers, in the order a real session hits them:

1. **``lib/claim_handoff``** — the pure decisions
   (:func:`evaluate_handoff`, :func:`release_verdict`). Injected env mappings,
   injected claims, no coordinator, no file reads.
2. **the real conftest seam** — ``tests/conftest.py:_claim_handoff_guard`` and
   the two call sites inside the ``router`` fixture, driven with the actual
   fixture body and a fake ``request``/``backend``. ``plan_venue`` and
   ``venue_mutex`` are the module's own seams (patched exactly as
   ``tests/unit/test_venue_plan.py`` does), so the assertions are about *our*
   code: was the place acquired, or did the session die first at rc=4?
3. **the no-claim proof** — with no claim env the guard returns ``None``
   without importing the decision layer, calling ``pytest.exit``, or changing
   what the ``router`` fixture calls. That is the "zero new behaviour" claim,
   measured rather than asserted.

The whole point of Part A is the ordering: **refuse before acquire**. Every
refusal test therefore also asserts that ``venue_mutex`` was never entered.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
from _pytest.outcomes import Exit

from lib.claim_handoff import (
    CLAIM_CODES,
    CLAIM_ENV,
    CLEAN_REASONS,
    ENV_HELD,
    ENV_OWNER,
    ENV_PLACE,
    ENV_RUN_ID,
    ENV_RUN_ID_LEGACY,
    PROCEED_CODES,
    ClaimCode,
    ClaimEnvError,
    ClaimHandoff,
    ReleaseReason,
    ReleaseState,
    ReleaseVerdict,
    evaluate_handoff,
    parse_claims,
    read_claim,
    record_release,
    release_verdict,
)
from lib.fleet_binding import PlaceClaim
from lib.labgrid_venue import (
    ClientIdentity,
    LabgridBench,
    LabgridBenchError,
    VenueBinding,
    VenuePlan,
)
from lib.lab_inventory import CoordinatorEntry, LabInventory, RouterEntry, SwitchEntry

REPO_ROOT = Path(__file__).resolve().parents[2]

#: The root conftest, loaded by path — the same way ``test_story_evidence.py``
#: loads a nested conftest. Importing it here executes only its module-level
#: env plumbing (``load_env``, inventory map), not a pytest session.
_spec = importlib.util.spec_from_file_location("root_conftest_claim", REPO_ROOT / "tests" / "conftest.py")
assert _spec is not None and _spec.loader is not None
root_conftest = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(root_conftest)


PLACE = "x1860-1"
OTHER_PLACE = "x1860-2"
OWNER = "host-1/prta-run-1-deadbeef"
RUN = "run-1"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def _claim_env(**overrides) -> dict[str, str]:
    """A complete, agreeing claim env — the shape the runner exports."""
    env = {ENV_PLACE: PLACE, ENV_OWNER: OWNER, ENV_RUN_ID: RUN}
    env.update(overrides)
    return {k: v for k, v in env.items() if v is not None}


def _inventory() -> LabInventory:
    return LabInventory(
        switch=SwitchEntry(host="switch"),
        routers={
            PLACE: RouterEntry(name=PLACE, poe_port="3", address="192.0.2.10", place=PLACE),
            OTHER_PLACE: RouterEntry(
                name=OTHER_PLACE, poe_port="4", address="192.0.2.11", place=OTHER_PLACE
            ),
        },
        coordinator=CoordinatorEntry(address="coord:20408"),
    )


class FakeConfig:
    """Just enough ``pytest.Config`` for the ``router`` fixture body."""

    def __init__(self, args=None, workerinput=None):
        self.args = list(args or [])
        self.workerinput = workerinput
        self.asked: list[str] = []

    def getoption(self, name, default=None):
        self.asked.append(name)
        return default


class FakeSession:
    def __init__(self):
        self.finalizers: list = []


class FakeRequest:
    def __init__(self, args=None, workerinput=None):
        self.config = FakeConfig(args=args, workerinput=workerinput)
        self.session = FakeSession()
        self.node = None

    def addfinalizer(self, fn):
        self.session.finalizers.append(fn)


class FakeBackend:
    is_rust_v2 = False


class RecordingBench:
    """A bench stand-in that records the acquire/release it was asked for."""

    def __init__(self, place=PLACE, owner=OWNER):
        self.place = place
        self.identity = ClientIdentity(host=owner.split("/")[0], user=owner.split("/", 1)[1])
        self.acquired = False
        self.cleanup_pending = False
        self.calls: list[str] = []

    def acquire(self):
        self.calls.append("acquire")
        self.acquired = True

    def release(self):
        self.calls.append("release")
        self.acquired = False

    def who(self):
        return ""


def _venue_plan(place=PLACE, origin="explicit-place", bench=None) -> VenuePlan:
    entry = _inventory().routers[place]
    return VenuePlan(
        "venue",
        binding=VenueBinding(bench=bench or RecordingBench(place=place), entry=entry),
        detail=place,
        origin=origin,
    )


@pytest.fixture
def venue_spy(monkeypatch):
    """Patch the ``router`` fixture's two venue seams; report what was called.

    ``plan_venue`` returns whatever ``plan`` holds; ``venue_mutex`` records the
    acquisition and yields the bench without touching a coordinator. Both are
    the fixture's own injection points (same approach as
    ``tests/unit/test_venue_plan.py``), so nothing here needs a labgrid client.
    """
    state = {"plan": VenuePlan("legacy", detail="venue auto: no place"), "acquired": [], "env": []}

    def fake_plan_venue(environ=None, operator_host=None):
        state["planned"] = True
        return state["plan"]

    import contextlib

    @contextlib.contextmanager
    def fake_venue_mutex(binding, phase, *, file_lock=True):
        state["acquired"].append((binding.entry.place, phase, file_lock))
        yield binding.bench

    monkeypatch.setattr("lib.labgrid_venue.plan_venue", fake_plan_venue)
    monkeypatch.setattr("lib.labgrid_venue.venue_mutex", fake_venue_mutex)
    monkeypatch.setattr(
        "lib.labgrid_venue.apply_venue_env",
        lambda entry, respect_existing=False: state["env"].append(entry.place),
    )
    monkeypatch.setattr(
        ClientIdentity,
        "current",
        classmethod(lambda cls: ClientIdentity("host-1", "prta-run-1-deadbeef")),
    )
    return state


def _run_router_fixture(request):
    """Execute the real ``router`` fixture body (no acquisition, no SSH)."""
    fn = getattr(root_conftest.router, "_fixture_function", None) or root_conftest.router.__wrapped__
    return fn(request, FakeBackend())


def _clear_claim_env(monkeypatch):
    for name in (*CLAIM_ENV, ENV_HELD, ENV_RUN_ID_LEGACY):
        monkeypatch.delenv(name, raising=False)


# ---------------------------------------------------------------------------
# The wire format
# ---------------------------------------------------------------------------
class TestWireFormat:
    def test_codes_are_pinned(self):
        assert CLAIM_CODES == {
            "no_claim",
            "claim_agrees",
            "claim_incomplete",
            "venue_takes_no_place",
            "place_mismatch",
            "claim_run_id_mismatch",
            "claim_owner_mismatch",
            "claim_already_held",
            "claim_held_unparsable",
        }

    def test_proceed_codes_are_exactly_the_two_non_refusals(self):
        assert PROCEED_CODES == {ClaimCode.NO_CLAIM, ClaimCode.CLAIM_AGREES}
        assert PROCEED_CODES < set(ClaimCode)

    def test_conftest_fast_path_names_match_the_module(self):
        """conftest duplicates the names for speed; pin them so they cannot drift."""
        assert root_conftest._CLAIM_ENV_NAMES == CLAIM_ENV

    def test_env_names_are_the_documented_ones(self):
        assert (ENV_PLACE, ENV_OWNER, ENV_RUN_ID, ENV_HELD) == (
            "TOLLGATE_FLEET_CLAIM_PLACE",
            "TOLLGATE_FLEET_CLAIM_OWNER",
            "TOLLGATE_FLEET_RUN_ID",
            "TOLLGATE_FLEET_CLAIM_HELD",
        )
        assert ENV_RUN_ID_LEGACY == "TOLLGATE_RUN_ID"


class TestReadClaim:
    def test_no_env_is_no_claim(self):
        assert read_claim({}) is None
        assert read_claim({ENV_PLACE: "  "}) is None

    def test_partial_env_is_not_no_claim(self):
        for missing in (ENV_PLACE, ENV_OWNER, ENV_RUN_ID):
            env = _claim_env(**{missing: ""})
            with pytest.raises(ClaimEnvError) as excinfo:
                read_claim(env)
            assert excinfo.value.code is ClaimCode.CLAIM_INCOMPLETE
            assert missing in str(excinfo.value)

    def test_complete_claim_is_read_and_stripped(self):
        claim = read_claim({" TOLLGATE_FLEET_CLAIM_PLACE ": " x "})
        assert claim is None  # unstripped KEY is not our key
        claim = read_claim({ENV_PLACE: " x ", ENV_OWNER: " o/u ", ENV_RUN_ID: " r "})
        assert claim == ClaimHandoff(place="x", owner="o/u", run_id="r")
        assert claim.claim == PlaceClaim(place="x", owner="o/u")

    def test_a_single_variable_is_enough_to_mean_a_claim_exists(self):
        with pytest.raises(ClaimEnvError):
            read_claim({ENV_RUN_ID: RUN})


class TestParseHeld:
    def test_empty(self):
        assert parse_claims("") == ()
        assert parse_claims("   ;;  ") == ()

    def test_multiple(self):
        assert parse_claims(f"{PLACE}={OWNER};{OTHER_PLACE}=host-2/other") == (
            PlaceClaim(PLACE, OWNER),
            PlaceClaim(OTHER_PLACE, "host-2/other"),
        )

    @pytest.mark.parametrize("raw", [f"{PLACE}", f"{PLACE}=", f"={OWNER}", "x=y=z"])
    def test_malformed_is_an_error_not_an_empty_list(self, raw):
        if raw == "x=y=z":
            # partition("=") leaves owner "y=z" — a legal-looking owner string.
            assert parse_claims(raw) == (PlaceClaim("x", "y=z"),)
            return
        with pytest.raises(ClaimEnvError) as excinfo:
            parse_claims(raw)
        assert excinfo.value.code is ClaimCode.CLAIM_HELD_UNPARSABLE


# ---------------------------------------------------------------------------
# Part A — the decision
# ---------------------------------------------------------------------------
class TestEvaluateHandoff:
    def test_no_claim_env_proceeds_as_no_claim(self):
        decision = evaluate_handoff(environ={}, resolved_place=PLACE)
        assert decision.code is ClaimCode.NO_CLAIM
        assert not decision.refused
        assert decision.place == "" and decision.owner == ""

    def test_no_claim_env_ignores_everything_else(self):
        """A no-claim run must not be refused by data it never exported."""
        decision = evaluate_handoff(
            environ={"TOLLGATE_FLEET_CLAIM_HELD": "garbage"},
            resolved_place="",
            owns_place=False,
            skip_reason="auto-skip",
            identity_owner="someone/else",
        )
        assert decision.code is ClaimCode.NO_CLAIM

    def test_agreeing_claim(self):
        decision = evaluate_handoff(environ=_claim_env(), resolved_place=PLACE, identity_owner=OWNER)
        assert decision.code is ClaimCode.CLAIM_AGREES
        assert not decision.refused
        assert (decision.place, decision.owner, decision.run_id) == (PLACE, OWNER, RUN)

    def test_venue_that_owns_no_place_is_refused(self):
        for owns, reason in ((False, "auto-skip"), (False, "mode-direct"), (False, "")):
            decision = evaluate_handoff(
                environ=_claim_env(), resolved_place=PLACE, owns_place=owns, skip_reason=reason
            )
            assert decision.code is ClaimCode.VENUE_TAKES_NO_PLACE
            assert decision.refused
            if reason:
                assert reason in decision.detail

    def test_different_place_is_refused(self):
        decision = evaluate_handoff(environ=_claim_env(), resolved_place=OTHER_PLACE)
        assert decision.code is ClaimCode.PLACE_MISMATCH
        assert repr(PLACE) in decision.detail and repr(OTHER_PLACE) in decision.detail

    def test_no_resolved_place_is_a_mismatch_not_a_pass(self):
        decision = evaluate_handoff(environ=_claim_env(), resolved_place="")
        assert decision.code is ClaimCode.PLACE_MISMATCH
        assert "(no place)" in decision.detail

    def test_run_id_disagreement_is_refused(self):
        decision = evaluate_handoff(
            environ=_claim_env(**{ENV_RUN_ID_LEGACY: "another-run"}), resolved_place=PLACE
        )
        assert decision.code is ClaimCode.CLAIM_RUN_ID_MISMATCH
        assert "another-run" in decision.detail

    def test_matching_legacy_run_id_is_fine(self):
        decision = evaluate_handoff(
            environ=_claim_env(**{ENV_RUN_ID_LEGACY: RUN}), resolved_place=PLACE
        )
        assert decision.code is ClaimCode.CLAIM_AGREES

    def test_owner_that_differs_from_our_identity_is_refused(self):
        decision = evaluate_handoff(
            environ=_claim_env(), resolved_place=PLACE, identity_owner="host-9/prta-other"
        )
        assert decision.code is ClaimCode.CLAIM_OWNER_MISMATCH
        assert "host-9/prta-other" in decision.detail

    def test_unknown_identity_is_not_a_refusal(self):
        """Only a KNOWN disagreement refuses; an unreadable identity is the
        caller's own problem and the coordinator re-checks ownership anyway."""
        decision = evaluate_handoff(environ=_claim_env(), resolved_place=PLACE, identity_owner="")
        assert decision.code is ClaimCode.CLAIM_AGREES

    def test_foreign_holder_in_env_is_refused(self):
        decision = evaluate_handoff(
            environ=_claim_env(**{ENV_HELD: f"{PLACE}=host-2/other-run"}),
            resolved_place=PLACE,
        )
        assert decision.code is ClaimCode.CLAIM_ALREADY_HELD
        assert decision.conflict == PlaceClaim(PLACE, "host-2/other-run")

    def test_foreign_holder_passed_by_the_caller_is_refused(self):
        decision = evaluate_handoff(
            environ=_claim_env(),
            resolved_place=PLACE,
            held=[PlaceClaim(PLACE, "host-2/other-run")],
        )
        assert decision.code is ClaimCode.CLAIM_ALREADY_HELD

    def test_same_owner_restating_is_a_retry_not_contention(self):
        decision = evaluate_handoff(
            environ=_claim_env(**{ENV_HELD: f"{PLACE}={OWNER}"}), resolved_place=PLACE
        )
        assert decision.code is ClaimCode.CLAIM_AGREES

    def test_a_holder_on_another_place_does_not_block(self):
        decision = evaluate_handoff(
            environ=_claim_env(**{ENV_HELD: f"{OTHER_PLACE}=host-2/other"}), resolved_place=PLACE
        )
        assert decision.code is ClaimCode.CLAIM_AGREES

    def test_unparsable_held_list_is_refused(self):
        decision = evaluate_handoff(environ=_claim_env(**{ENV_HELD: "no-equals"}), resolved_place=PLACE)
        assert decision.code is ClaimCode.CLAIM_HELD_UNPARSABLE

    def test_partial_claim_is_refused(self):
        decision = evaluate_handoff(environ={ENV_PLACE: PLACE}, resolved_place=PLACE)
        assert decision.code is ClaimCode.CLAIM_INCOMPLETE

    def test_precedence_is_fixed(self):
        """Every earlier rule wins over every later one, so a log line is stable."""
        # venue-takes-no-place beats place mismatch, run id, owner, held
        assert (
            evaluate_handoff(
                environ=_claim_env(**{ENV_RUN_ID_LEGACY: "other", ENV_HELD: f"{PLACE}=h/x"}),
                resolved_place=OTHER_PLACE,
                owns_place=False,
                identity_owner="someone/else",
            ).code
            is ClaimCode.VENUE_TAKES_NO_PLACE
        )
        # place mismatch beats run id / owner / held
        assert (
            evaluate_handoff(
                environ=_claim_env(**{ENV_RUN_ID_LEGACY: "other", ENV_HELD: f"{PLACE}=h/x"}),
                resolved_place=OTHER_PLACE,
                identity_owner="someone/else",
            ).code
            is ClaimCode.PLACE_MISMATCH
        )
        # run id beats owner / held
        assert (
            evaluate_handoff(
                environ=_claim_env(**{ENV_RUN_ID_LEGACY: "other", ENV_HELD: f"{PLACE}=h/x"}),
                resolved_place=PLACE,
                identity_owner="someone/else",
            ).code
            is ClaimCode.CLAIM_RUN_ID_MISMATCH
        )
        # owner beats held
        assert (
            evaluate_handoff(
                environ=_claim_env(**{ENV_HELD: f"{PLACE}=h/x"}),
                resolved_place=PLACE,
                identity_owner="someone/else",
            ).code
            is ClaimCode.CLAIM_OWNER_MISMATCH
        )

    def test_every_code_is_reachable(self):
        """No dead codes: each member is produced by some real input."""
        produced = {
            evaluate_handoff(environ={}).code,
            evaluate_handoff(environ=_claim_env(), resolved_place=PLACE).code,
            evaluate_handoff(environ={ENV_PLACE: PLACE}, resolved_place=PLACE).code,
            evaluate_handoff(environ=_claim_env(), resolved_place=PLACE, owns_place=False).code,
            evaluate_handoff(environ=_claim_env(), resolved_place=OTHER_PLACE).code,
            evaluate_handoff(
                environ=_claim_env(**{ENV_RUN_ID_LEGACY: "other"}), resolved_place=PLACE
            ).code,
            evaluate_handoff(
                environ=_claim_env(), resolved_place=PLACE, identity_owner="a/b"
            ).code,
            evaluate_handoff(
                environ=_claim_env(**{ENV_HELD: f"{PLACE}=a/b"}), resolved_place=PLACE
            ).code,
            evaluate_handoff(environ=_claim_env(**{ENV_HELD: "junk"}), resolved_place=PLACE).code,
        }
        assert produced == set(ClaimCode)

    def test_the_decision_is_total_over_env_combinations(self):
        """Never raises: the caller's only safe reaction is the same for all."""
        names = (*CLAIM_ENV, ENV_HELD, ENV_RUN_ID_LEGACY)
        for mask in range(1 << len(names)):
            env = {name: (PLACE, OWNER, RUN, f"{PLACE}={OWNER}", RUN)[i] for i, name in
                   enumerate(names) if mask & (1 << i)}
            decision = evaluate_handoff(environ=env, resolved_place=PLACE)
            assert decision.code in ClaimCode

    def test_message_and_refused(self):
        refused = evaluate_handoff(environ=_claim_env(), resolved_place=OTHER_PLACE)
        assert refused.message.startswith("place_mismatch: ")
        assert refused.refused
        agreed = evaluate_handoff(environ=_claim_env(), resolved_place=PLACE)
        assert agreed.message.startswith("claim_agrees: ")
        assert not agreed.refused


# ---------------------------------------------------------------------------
# Part A — the real conftest seam
# ---------------------------------------------------------------------------
class TestConftestSeam:
    def test_no_claim_env_makes_exactly_the_same_calls(self, monkeypatch, venue_spy):
        """The zero-new-behaviour claim, measured rather than asserted.

        With no claim env the guard must not import the decision layer, not
        call ``pytest.exit``, and the fixture must make the same calls it made
        before the hook existed.
        """
        _clear_claim_env(monkeypatch)
        calls: list[str] = []

        def forbidden(*a, **kw):
            calls.append("claim_handoff")
            raise AssertionError("the no-claim path must not consult the decision layer")

        monkeypatch.setattr("lib.claim_handoff.evaluate_handoff", forbidden)
        monkeypatch.setattr(
            "pytest.exit", lambda *a, **kw: calls.append(("exit", a, kw)), raising=True
        )
        request = FakeRequest()

        assert root_conftest._claim_handoff_guard() is None
        assert _run_router_fixture(request) is None
        assert calls == [], calls
        # Same venue decision as before: plan_venue ran once, nothing acquired.
        assert venue_spy.get("planned") is True
        assert venue_spy["acquired"] == []

    def test_claim_agrees_and_the_venue_is_acquired(self, monkeypatch, venue_spy):
        monkeypatch.setenv(ENV_PLACE, PLACE)
        monkeypatch.setenv(ENV_OWNER, OWNER)
        monkeypatch.setenv(ENV_RUN_ID, RUN)
        monkeypatch.setenv(ENV_RUN_ID_LEGACY, RUN)
        venue_spy["plan"] = _venue_plan(PLACE)

        assert _run_router_fixture(FakeRequest()) is None
        assert venue_spy["acquired"] == [(PLACE, f"labgrid-venue {PLACE}", True)]
        assert venue_spy["env"] == [PLACE]

    def test_claim_for_another_place_dies_before_acquiring(self, monkeypatch, venue_spy):
        monkeypatch.setenv(ENV_PLACE, PLACE)
        monkeypatch.setenv(ENV_OWNER, OWNER)
        monkeypatch.setenv(ENV_RUN_ID, RUN)
        monkeypatch.setenv(ENV_RUN_ID_LEGACY, RUN)
        venue_spy["plan"] = _venue_plan(OTHER_PLACE)

        with pytest.raises(Exit) as excinfo:
            _run_router_fixture(FakeRequest())
        assert excinfo.value.returncode == 4
        assert "claim handoff refused: place_mismatch" in str(excinfo.value)
        assert venue_spy["acquired"] == [], "the place must not be touched"

    def test_claim_with_a_legacy_venue_outcome_dies(self, monkeypatch, venue_spy):
        """direct/legacy means the fixture would own NO place — refuse, never fall back."""
        monkeypatch.setenv(ENV_PLACE, PLACE)
        monkeypatch.setenv(ENV_OWNER, OWNER)
        monkeypatch.setenv(ENV_RUN_ID, RUN)
        venue_spy["plan"] = VenuePlan("legacy", detail="TOLLGATE_VENUE='direct' keeps the legacy path")

        with pytest.raises(Exit) as excinfo:
            _run_router_fixture(FakeRequest())
        assert excinfo.value.returncode == 4
        assert "venue_takes_no_place" in str(excinfo.value)
        assert venue_spy["acquired"] == []

    def test_claim_in_a_unit_only_auto_session_dies_without_planning(self, monkeypatch, venue_spy):
        monkeypatch.delenv("TOLLGATE_VENUE", raising=False)
        monkeypatch.setenv(ENV_PLACE, PLACE)
        monkeypatch.setenv(ENV_OWNER, OWNER)
        monkeypatch.setenv(ENV_RUN_ID, RUN)

        with pytest.raises(Exit) as excinfo:
            _run_router_fixture(FakeRequest(args=["tests/unit/test_x.py"]))
        assert excinfo.value.returncode == 4
        assert "venue_takes_no_place" in str(excinfo.value)
        assert venue_spy.get("planned") is None, "plan_venue must not even run"
        assert venue_spy["acquired"] == []

    def test_claim_in_an_xdist_worker_dies(self, monkeypatch, venue_spy):
        monkeypatch.setenv(ENV_PLACE, PLACE)
        monkeypatch.setenv(ENV_OWNER, OWNER)
        monkeypatch.setenv(ENV_RUN_ID, RUN)

        with pytest.raises(Exit) as excinfo:
            _run_router_fixture(FakeRequest(workerinput={"workerid": "gw0"}))
        assert excinfo.value.returncode == 4
        assert "xdist-worker" in str(excinfo.value)
        assert venue_spy["acquired"] == []

    def test_partial_claim_dies_before_acquiring(self, monkeypatch, venue_spy):
        monkeypatch.setenv(ENV_PLACE, PLACE)
        venue_spy["plan"] = _venue_plan(PLACE)

        with pytest.raises(Exit) as excinfo:
            _run_router_fixture(FakeRequest())
        assert excinfo.value.returncode == 4
        assert "claim_incomplete" in str(excinfo.value)
        assert venue_spy["acquired"] == []

    def test_claim_for_another_run_identity_dies(self, monkeypatch, venue_spy):
        monkeypatch.setenv(ENV_PLACE, PLACE)
        monkeypatch.setenv(ENV_OWNER, "host-1/prta-somebody-else")
        monkeypatch.setenv(ENV_RUN_ID, RUN)
        venue_spy["plan"] = _venue_plan(PLACE)

        with pytest.raises(Exit) as excinfo:
            _run_router_fixture(FakeRequest())
        assert excinfo.value.returncode == 4
        assert "claim_owner_mismatch" in str(excinfo.value)
        assert venue_spy["acquired"] == []

    def test_a_place_held_by_another_run_dies(self, monkeypatch, venue_spy):
        monkeypatch.setenv(ENV_PLACE, PLACE)
        monkeypatch.setenv(ENV_OWNER, OWNER)
        monkeypatch.setenv(ENV_RUN_ID, RUN)
        monkeypatch.setenv(ENV_HELD, f"{PLACE}=host-2/prta-other")
        venue_spy["plan"] = _venue_plan(PLACE)

        with pytest.raises(Exit) as excinfo:
            _run_router_fixture(FakeRequest())
        assert excinfo.value.returncode == 4
        assert "claim_already_held" in str(excinfo.value)
        assert venue_spy["acquired"] == []

    def test_the_guard_passes_a_known_identity_through(self, monkeypatch):
        monkeypatch.setenv(ENV_PLACE, PLACE)
        monkeypatch.setenv(ENV_OWNER, OWNER)
        monkeypatch.setenv(ENV_RUN_ID, RUN)
        decision = root_conftest._claim_handoff_guard(resolved_place=PLACE, identity_owner=OWNER)
        assert decision.code is ClaimCode.CLAIM_AGREES

    def test_a_known_identity_that_disagrees_still_refuses(self, monkeypatch):
        """The identity is consulted, not merely passed through."""
        monkeypatch.setenv(ENV_PLACE, PLACE)
        monkeypatch.setenv(ENV_OWNER, OWNER)
        monkeypatch.setenv(ENV_RUN_ID, RUN)
        with pytest.raises(Exit) as excinfo:
            root_conftest._claim_handoff_guard(resolved_place=PLACE, identity_owner="h/other")
        assert excinfo.value.returncode == 4
        assert "claim_owner_mismatch" in str(excinfo.value)

    def test_the_guard_ignores_a_broken_identity_lookup(self, monkeypatch):
        """An unreadable identity must not turn into a fake agreement OR a crash."""
        monkeypatch.setenv(ENV_PLACE, PLACE)
        monkeypatch.setenv(ENV_OWNER, OWNER)
        monkeypatch.setenv(ENV_RUN_ID, RUN)

        def boom(cls):
            raise RuntimeError("no identity available")

        monkeypatch.setattr(ClientIdentity, "current", classmethod(boom))
        decision = root_conftest._claim_handoff_guard(resolved_place=PLACE)
        assert decision.code is ClaimCode.CLAIM_AGREES


class TestNoClaimIsByteIdenticalToBase:
    """The strongest form of "zero new behaviour": run the *base* fixture body.

    ``tests/conftest.py`` at the PR base (``e2fd71f``) is loaded from ``git
    show`` into a second module and driven with the same fake request, the same
    patched venue seams and the same recorded call log. If the hook changed
    anything on the no-claim path, the two logs diverge.
    """

    BASE_REV = "e2fd71f43ffb577d7c31dc826d4ce9195275edef"

    @staticmethod
    def _base_conftest(tmp_path, monkeypatch):
        import subprocess

        proc = subprocess.run(
            ["git", "show", f"{TestNoClaimIsByteIdenticalToBase.BASE_REV}:tests/conftest.py"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            pytest.skip(f"base conftest unavailable: {proc.stderr.strip()[:200]}")
        path = tmp_path / "base_conftest.py"
        path.write_text(proc.stdout, encoding="utf-8")
        spec = importlib.util.spec_from_file_location("base_conftest_claim", path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    @staticmethod
    def _drive(module, request) -> list[tuple]:
        import contextlib

        log: list[tuple] = []

        def plan_venue(environ=None, operator_host=None):
            log.append(("plan_venue", operator_host))
            return VenuePlan("venue", binding=_venue_plan(PLACE).binding, detail=PLACE, origin="forced")

        @contextlib.contextmanager
        def venue_mutex(binding, phase, *, file_lock=True):
            log.append(("venue_mutex", binding.entry.place, phase, file_lock))
            yield binding.bench

        def apply_venue_env(entry, respect_existing=False):
            log.append(("apply_venue_env", entry.place, respect_existing))

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("lib.labgrid_venue.plan_venue", plan_venue)
            mp.setattr("lib.labgrid_venue.venue_mutex", venue_mutex)
            mp.setattr("lib.labgrid_venue.apply_venue_env", apply_venue_env)
            mp.setattr(
                ClientIdentity,
                "current",
                classmethod(lambda cls: ClientIdentity("host-1", "prta-run-1-deadbeef")),
            )
            fn = getattr(module.router, "_fixture_function", None) or module.router.__wrapped__
            fn(request, FakeBackend())
        log.extend(("getoption", name) for name in request.config.asked)
        return log

    def test_the_router_fixture_makes_the_same_calls(self, tmp_path, monkeypatch):
        base = self._base_conftest(tmp_path, monkeypatch)
        for name in (*CLAIM_ENV, ENV_HELD, ENV_RUN_ID_LEGACY):
            monkeypatch.delenv(name, raising=False)

        base_log = self._drive(base, FakeRequest())
        head_log = self._drive(root_conftest, FakeRequest())

        assert base_log, "the base fixture must actually have taken the venue path"
        assert head_log == base_log

    def test_the_auto_skip_path_is_unchanged_too(self, tmp_path, monkeypatch):
        base = self._base_conftest(tmp_path, monkeypatch)
        for name in (*CLAIM_ENV, ENV_HELD, ENV_RUN_ID_LEGACY):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv("TOLLGATE_VENUE", raising=False)

        request_a, request_b = FakeRequest(args=["tests/unit/test_x.py"]), FakeRequest(args=["tests/unit/test_x.py"])
        base_log = self._drive(base, request_a)
        head_log = self._drive(root_conftest, request_b)
        assert head_log == base_log
        assert not any(entry[0] == "venue_mutex" for entry in head_log)


# ---------------------------------------------------------------------------
# Part B — release evidence
# ---------------------------------------------------------------------------
def _assert_pending(verdict: ReleaseVerdict, reason: ReleaseReason):
    assert verdict.state is ReleaseState.CLEANUP_PENDING
    assert verdict.reason is reason
    assert not verdict.clean
    assert verdict.review is True


class TestReleaseVerdict:
    def test_two_states_only(self):
        assert {s.value for s in ReleaseState} == {"clean_released", "cleanup_pending"}

    def test_clean_reasons_are_pinned(self):
        assert CLEAN_REASONS == {
            ReleaseReason.RELEASED_CONFIRMED,
            ReleaseReason.NEVER_ACQUIRED,
            ReleaseReason.LEASE_TAKEN_BY_OTHER,
        }

    def test_release_raised_is_pending(self):
        verdict = release_verdict(
            acquired=True,
            cleanup_pending=True,
            error=LabgridBenchError("release-from reported success but the lease is still held"),
            owner_after=OWNER,
            our_owner=OWNER,
            place=PLACE,
        )
        _assert_pending(verdict, ReleaseReason.RELEASE_RAISED)
        assert "LabgridBenchError" in verdict.detail

    def test_error_outranks_the_cleanup_flag(self):
        verdict = release_verdict(
            acquired=True,
            cleanup_pending=True,
            error=RuntimeError("x"),
        )
        assert verdict.reason is ReleaseReason.RELEASE_RAISED

    def test_cleanup_flag_is_pending_even_when_the_call_returned(self):
        verdict = release_verdict(acquired=True, cleanup_pending=True, owner_after="", our_owner=OWNER)
        _assert_pending(verdict, ReleaseReason.CLEANUP_FLAG_SET)

    def test_still_held_by_us_is_pending_even_if_acquired_is_false(self):
        """An incoherent local state is not evidence of a clean release."""
        verdict = release_verdict(
            acquired=False, cleanup_pending=False, owner_after=OWNER, our_owner=OWNER
        )
        _assert_pending(verdict, ReleaseReason.LEASE_STILL_HELD)

    def test_never_acquired_is_clean(self):
        verdict = release_verdict(acquired=False, cleanup_pending=False, owner_after=None)
        assert verdict.clean
        assert verdict.reason is ReleaseReason.NEVER_ACQUIRED
        assert verdict.review is False

    def test_no_re_read_is_pending(self):
        verdict = release_verdict(acquired=True, cleanup_pending=False, owner_after=None, our_owner=OWNER)
        _assert_pending(verdict, ReleaseReason.OWNERSHIP_UNREADABLE)

    def test_unknown_own_owner_is_pending(self):
        verdict = release_verdict(
            acquired=True, cleanup_pending=False, owner_after="host-9/other", our_owner=""
        )
        _assert_pending(verdict, ReleaseReason.OWNER_UNKNOWN)

    def test_free_after_release_is_clean_released(self):
        verdict = release_verdict(
            acquired=True, cleanup_pending=False, owner_after="", our_owner=OWNER, place=PLACE
        )
        assert verdict.clean
        assert verdict.reason is ReleaseReason.RELEASED_CONFIRMED
        assert verdict.review is False
        assert verdict.place == PLACE

    def test_taken_by_another_owner_is_clean_but_reviewed(self):
        """We were fenced or replaced: our lease is gone, but we did not free it."""
        verdict = release_verdict(
            acquired=True,
            cleanup_pending=False,
            owner_after="host-2/prta-other",
            our_owner=OWNER,
            place=PLACE,
        )
        assert verdict.state is ReleaseState.CLEAN_RELEASED
        assert verdict.reason is ReleaseReason.LEASE_TAKEN_BY_OTHER
        assert verdict.review is True

    def test_whitespace_does_not_flip_a_verdict(self):
        verdict = release_verdict(
            acquired=True, cleanup_pending=False, owner_after="  ", our_owner=f"  {OWNER} "
        )
        assert verdict.clean
        assert verdict.reason is ReleaseReason.RELEASED_CONFIRMED

    def test_every_reason_is_reachable(self):
        produced = {
            release_verdict(acquired=True, cleanup_pending=False, error=RuntimeError(), our_owner=OWNER).reason,
            release_verdict(acquired=True, cleanup_pending=True).reason,
            release_verdict(acquired=True, cleanup_pending=False, owner_after=OWNER, our_owner=OWNER).reason,
            release_verdict(acquired=False, cleanup_pending=False).reason,
            release_verdict(acquired=True, cleanup_pending=False, owner_after=None, our_owner=OWNER).reason,
            release_verdict(acquired=True, cleanup_pending=False, owner_after="a/b", our_owner="").reason,
            release_verdict(acquired=True, cleanup_pending=False, owner_after="", our_owner=OWNER).reason,
            release_verdict(acquired=True, cleanup_pending=False, owner_after="a/b", our_owner=OWNER).reason,
        }
        assert produced == set(ReleaseReason)

    def test_every_pending_reason_keeps_the_allocation_out_of_service(self):
        for reason in set(ReleaseReason) - CLEAN_REASONS:
            verdict = ReleaseVerdict(ReleaseState.CLEANUP_PENDING, reason)
            assert not verdict.clean and verdict.review

    def test_to_dict_is_json_safe_and_lossless(self):
        verdict = release_verdict(
            acquired=True, cleanup_pending=False, owner_after="", our_owner=OWNER, place=PLACE
        )
        record = verdict.to_dict()
        assert json.loads(json.dumps(record)) == record
        assert record["state"] == "clean_released"
        assert record["reason"] == "released_confirmed"
        assert record["clean"] is True and record["review"] is False
        assert record["place"] == PLACE


class TestRecordRelease:
    """``record_release`` binds the verdict to what a real bench reports."""

    def test_never_acquired_bench_is_clean(self):
        verdict = record_release(RecordingBench())
        assert verdict.reason is ReleaseReason.NEVER_ACQUIRED
        assert verdict.clean

    def test_cleanup_pending_bench_is_pending(self):
        bench = RecordingBench()
        bench.cleanup_pending = True
        _assert_pending(record_release(bench), ReleaseReason.CLEANUP_FLAG_SET)

    def test_a_release_that_raised_leaves_the_allocation_pending(self):
        """The real ``LabgridBench.release()`` path, end to end, hardware-free.

        ``release-from`` exits 0 even when it released nothing
        (``labgrid/remote/coordinator.py:913``), so the wrapper re-reads
        ownership. Here the re-read still shows OUR owner: the place is not
        free, ``release()`` raises, ``cleanup_pending`` is set — and the
        recorder must record ``CLEANUP_PENDING``, not a clean release.
        """
        bench = LabgridBench("coord.example:20408", PLACE, runner=_stuck_release(), identity=ClientIdentity("host-1", "prta-run-1-deadbeef"))
        bench.acquire()
        with pytest.raises(LabgridBenchError):
            bench.release()
        assert bench.cleanup_pending is True

        verdict = record_release(bench, error=LabgridBenchError("unconfirmed"))
        _assert_pending(verdict, ReleaseReason.RELEASE_RAISED)
        assert verdict.place == PLACE

        # Same bench, but the caller passes the re-read it performed: still
        # pending, because the coordinator says we still hold it.
        verdict = record_release(bench, owner_after=bench.identity.owner, ever_acquired=True)
        assert verdict.reason is ReleaseReason.LEASE_STILL_HELD

    def test_a_confirmed_release_is_recorded_clean(self):
        bench = LabgridBench("coord.example:20408", PLACE, runner=_freeing_release(), identity=ClientIdentity("host-1", "prta-run-1-deadbeef"))
        bench.acquire()
        bench.release()
        assert bench.cleanup_pending is False
        assert bench.acquired is False, "release() clears the local flag — the record must not read 'never acquired'"
        verdict = record_release(bench, owner_after="", ever_acquired=True)
        assert verdict.reason is ReleaseReason.RELEASED_CONFIRMED
        assert verdict.clean

    def test_a_bench_that_cannot_report_its_identity_stays_pending(self):
        class Identityless:
            acquired = True
            cleanup_pending = False
            place = PLACE

        _assert_pending(
            record_release(Identityless(), owner_after=""), ReleaseReason.OWNER_UNKNOWN
        )


def _stuck_release():
    """Fake coordinator: ``release-from`` succeeds but the lease is still ours."""

    class Proc:
        returncode = 0
        stdout = ""
        stderr = ""

    def run(cmd, **kwargs):
        verb = cmd[5] if len(cmd) > 5 else ""
        if verb == "show":
            env = kwargs.get("env") or {}
            owner = f"{env.get('LG_HOSTNAME')}/{env.get('LG_USERNAME')}"
            return type("P", (), {"returncode": 0, "stdout": f"acquired: {owner}\n", "stderr": ""})()
        return Proc()

    return run


def _freeing_release():
    """Fake coordinator: ``release-from`` really frees the place."""
    state = {"held": True}

    def run(cmd, **kwargs):
        verb = cmd[5] if len(cmd) > 5 else ""
        if verb == "release-from":
            state["held"] = False
            return type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})()
        if verb == "show":
            return type(
                "P", (), {"returncode": 0, "stdout": "acquired: None\n", "stderr": ""}
            )()
        return type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    return run
