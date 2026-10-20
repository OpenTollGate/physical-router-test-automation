"""Unit tests for lib/helpers.py — pure-function helpers that need no router.

Tests the data-parsing and decision functions (is_session_event, is_mac_lookup_failure,
parse_json_or_fail, gate_bug_fix, is_full_merchant, is_degraded, assert_internet,
client playwright gate) using mock data. Router-dependent functions (pay_and_wait,
skip_if_no_cli_socket and siblings that need a live router) are excluded — they're
integration-test territory.
"""
from __future__ import annotations

import json
import pytest

from lib.helpers import (
    is_session_event,
    is_mac_lookup_failure,
    is_payment_swap_succeeded,
    parse_json_or_fail,
    gate_bug_fix,
    is_full_merchant,
    is_degraded,
    assert_internet,
)


# --------------------------------------------------------------------------- #
# is_session_event
# --------------------------------------------------------------------------- #


pytestmark = [pytest.mark.story("P11")]

class TestIsSessionEvent:
    def test_kind_1022(self):
        assert is_session_event({"kind": 1022, "tags": []}) is True

    def test_allotment_tag(self):
        assert is_session_event({"kind": 21000, "tags": [["allotment", "1000"]]}) is True

    def test_kind_21000_no_allotment(self):
        assert is_session_event({"kind": 21000, "tags": [["p", "abc"]]}) is False

    def test_non_dict(self):
        assert is_session_event("not a dict") is False
        assert is_session_event(None) is False
        assert is_session_event(42) is False

    def test_empty_dict(self):
        assert is_session_event({}) is False

    def test_tags_not_list(self):
        assert is_session_event({"kind": 1022, "tags": "not_a_list"}) is True

    def test_allotment_tag_wrong_type(self):
        assert is_session_event({"kind": 10021, "tags": ["not_a_list"]}) is False


# --------------------------------------------------------------------------- #
# is_mac_lookup_failure
# --------------------------------------------------------------------------- #


class TestIsMacLookupFailure:
    def test_correct_kind_and_code(self):
        resp = {"kind": 21023, "tags": [["code", "mac-address-lookup-failed"]]}
        assert is_mac_lookup_failure(resp) is True

    def test_wrong_kind(self):
        resp = {"kind": 1022, "tags": [["code", "mac-address-lookup-failed"]]}
        assert is_mac_lookup_failure(resp) is False

    def test_wrong_code(self):
        resp = {"kind": 21023, "tags": [["code", "payment-failed"]]}
        assert is_mac_lookup_failure(resp) is False

    def test_no_tags(self):
        assert is_mac_lookup_failure({"kind": 21023}) is False

    def test_empty_tags(self):
        assert is_mac_lookup_failure({"kind": 21023, "tags": []}) is False

    def test_non_list_tags(self):
        assert is_mac_lookup_failure({"kind": 21023, "tags": "string"}) is False


# --------------------------------------------------------------------------- #
# parse_json_or_fail
# --------------------------------------------------------------------------- #


class TestParseJsonOrFail:
    def test_valid_json(self):
        assert parse_json_or_fail('{"key": "value"}') == {"key": "value"}

    def test_valid_json_array(self):
        assert parse_json_or_fail('[1, 2, 3]') == [1, 2, 3]

    def test_invalid_json_fails(self):
        try:
            parse_json_or_fail("not json")
            assert False, "should have raised"
        except BaseException as e:
            assert "Non-JSON" in str(e)

    def test_invalid_json_skip(self):
        try:
            parse_json_or_fail("not json", skip=True)
            assert False, "should have raised"
        except BaseException as e:
            assert "Non-JSON" in str(e)

    def test_empty_string_fails(self):
        try:
            parse_json_or_fail("")
            assert False, "should have raised"
        except BaseException as e:
            assert "Non-JSON" in str(e)

    def test_with_label(self):
        try:
            parse_json_or_fail("not json", label="balance")
            assert False, "should have raised"
        except BaseException as e:
            assert "balance" in str(e)


# --------------------------------------------------------------------------- #
# gate_bug_fix
# --------------------------------------------------------------------------- #


class TestGateBugFix:
    def test_fix_present_does_nothing(self):
        gate_bug_fix(True, bug_id="test-bug", fix_pr="PR #999")

    def test_fix_absent_xfails(self):
        with pytest.raises(Exception, match="test-bug"):
            gate_bug_fix(False, bug_id="test-bug", fix_pr="PR #999")

    def test_fix_absent_no_pr(self):
        with pytest.raises(Exception, match="test-bug"):
            gate_bug_fix(False, bug_id="test-bug")

    def test_fix_absent_message_contains_pr(self):
        with pytest.raises(Exception, match="PR #999"):
            gate_bug_fix(False, bug_id="bug", fix_pr="PR #999")


# --------------------------------------------------------------------------- #
# is_full_merchant / is_degraded (with mock router)
# --------------------------------------------------------------------------- #


class MockRouter:
    """Minimal router for testing API-parsing helpers."""
    def __init__(self, status_code=200, body=""):
        self._status = status_code
        self._body = body

    def api_status(self, path="/"):
        return self._status

    def api_body(self, path="/"):
        return self._body


class TestIsFullMerchant:
    def test_valid_merchant(self):
        body = json.dumps({"kind": 10021, "tags": [["price_per_step", "cashu", "1", "sat"]]})
        assert is_full_merchant(MockRouter(200, body)) is True

    def test_kind_wrong(self):
        # 21023 with price tags is now VALID (deployed build self-reports
        # 21023 with identical tags — helpers accept both since fac3865);
        # a genuinely wrong kind is something else entirely.
        body = json.dumps({"kind": 5, "tags": [["price_per_step", "cashu", "1", "sat"]]})
        assert is_full_merchant(MockRouter(200, body)) is False

    def test_kind_21023_with_price_accepted(self):
        body = json.dumps({"kind": 21023, "tags": [["price_per_step", "cashu", "1", "sat"]]})
        assert is_full_merchant(MockRouter(200, body)) is True

    def test_no_price_per_step_tag(self):
        body = json.dumps({"kind": 10021, "tags": [["metric", "bytes"]]})
        assert is_full_merchant(MockRouter(200, body)) is False

    def test_non_200(self):
        assert is_full_merchant(MockRouter(500, "")) is False

    def test_invalid_json(self):
        assert is_full_merchant(MockRouter(200, "not json")) is False


class TestIsDegraded:
    def test_degraded(self):
        # Degraded is signaled by the level/code markers, not the kind
        # (kind 21023 is on every healthy ad — fac3865 fixed the race).
        body = json.dumps({"kind": 21023, "tags": [["level", "warning"], ["code", "no-reachable-mints"]]})
        assert is_degraded(MockRouter(200, body)) is True

    def test_plain_ad_not_degraded(self):
        body = json.dumps({"kind": 21023, "tags": []})
        assert is_degraded(MockRouter(200, body)) is False

    def test_not_degraded(self):
        body = json.dumps({"kind": 10021, "tags": []})
        assert is_degraded(MockRouter(200, body)) is False

    def test_invalid_json(self):
        assert is_degraded(MockRouter(200, "not json")) is False


# --------------------------------------------------------------------------- #
# is_payment_swap_succeeded
# --------------------------------------------------------------------------- #


class TestIsPaymentSwapSucceeded:
    def test_session_event_kind_1022(self):
        assert is_payment_swap_succeeded(
            {"kind": 1022, "tags": [["allotment", "66060288"]]}
        ) is True

    def test_session_event_allotment_only(self):
        assert is_payment_swap_succeeded(
            {"kind": 10021, "tags": [["allotment", "100"]]}
        ) is True

    def test_mac_lookup_failure_structural(self):
        assert is_payment_swap_succeeded(
            {"kind": 21023, "tags": [["code", "mac-address-lookup-failed"]]}
        ) is True

    def test_nut02_id_length_invalid(self):
        assert is_payment_swap_succeeded(
            {"kind": 21023, "tags": [["code", "NUT02: ID length invalid"]]}
        ) is False

    def test_invalid_v3_token(self):
        assert is_payment_swap_succeeded(
            {"kind": 21023, "content": "invalid V3 token"}
        ) is False

    def test_token_already_spent(self):
        assert is_payment_swap_succeeded(
            {"kind": 21023, "content": "token_already_spent"}
        ) is False

    def test_failed_to_open_gate(self):
        assert is_payment_swap_succeeded(
            {"kind": 21023, "content": "failed to open gate: exit status 1"}
        ) is True

    def test_gateway_ip_no_false_positive(self):
        assert is_payment_swap_succeeded(
            {"kind": 21023, "tags": [["gateway_ip", "10.99.99.1"]]}
        ) is False

    def test_empty_dict(self):
        assert is_payment_swap_succeeded({}) is False

    def test_non_dict(self):
        assert is_payment_swap_succeeded(None) is False
        assert is_payment_swap_succeeded("not a dict") is False

    def test_unknown_error_returns_false(self):
        assert is_payment_swap_succeeded(
            {"kind": 21023, "tags": [["code", "some-unknown-error"]]}
        ) is False

    def test_token_failure_takes_precedence_over_gate(self):
        body = "NUT02: ID length invalid and failed to open gate"
        assert is_payment_swap_succeeded(
            {"kind": 21023, "content": body}
        ) is False


class TestMintUrlPort:
    """block_mints must block the port a mint actually listens on —
    blocking only 443 was a no-op for plain-http lab mints, so degraded
    mode never triggered locally (2026-09-03 finding)."""

    def test_https_default_port(self):
        from lib.helpers import _mint_url_port
        assert _mint_url_port("https://testnut.cashu.exchange") == 443

    def test_http_default_port(self):
        from lib.helpers import _mint_url_port
        assert _mint_url_port("http://mint.example") == 80

    def test_explicit_port_wins(self):
        from lib.helpers import _mint_url_port
        assert _mint_url_port("http://10.99.99.2:8383") == 8383

    def test_https_explicit_port(self):
        from lib.helpers import _mint_url_port
        assert _mint_url_port("https://mint.example:7443") == 7443

    def test_bare_ip_with_port(self):
        from lib.helpers import _mint_url_port
        assert _mint_url_port("http://10.99.99.2:8385") == 8385


class TestClientPlaywrightProbe:
    """skip_if_no_client_playwright gate (issue #104): probe command shape,
    env override shell-safety, session caching, and skip semantics."""

    def setup_method(self):
        from lib import helpers
        helpers._client_pw_probe_cache.clear()

    def test_probe_cmd_default(self):
        from lib.helpers import _client_pw_probe_cmd, _PW_PROBE_OK_MARKER
        cmd = _client_pw_probe_cmd()
        assert _PW_PROBE_OK_MARKER in cmd
        assert "executable_path" not in cmd
        assert "chromium.launch" in cmd

    def test_probe_cmd_env_override(self, monkeypatch):
        from lib.helpers import _client_pw_probe_cmd
        monkeypatch.setenv("TOLLGATE_PLAYWRIGHT_CHROMIUM_PATH", "/nonexistent/chromium")
        cmd = _client_pw_probe_cmd()
        assert 'executable_path="/nonexistent/chromium"' in cmd

    @pytest.mark.parametrize("evil", ['"/bin/evil"', "a'b", "x\\y"])
    def test_probe_cmd_ignores_unsafe_override(self, monkeypatch, evil):
        from lib.helpers import _client_pw_probe_cmd
        monkeypatch.setenv("TOLLGATE_PLAYWRIGHT_CHROMIUM_PATH", evil)
        assert "executable_path" not in _client_pw_probe_cmd()

    def test_functional_true_on_pw_ok(self):
        from lib.helpers import client_playwright_functional
        calls = []

        class Client:
            _client_ip = "10.99.99.100"

            def exec(self, cmd, timeout=30):
                calls.append(cmd)
                return "PW_OK"

        ok, diag = client_playwright_functional(Client())
        assert ok is True
        assert len(calls) == 1

    def test_traceback_echo_does_not_count_as_ok(self):
        """A failing probe's traceback echoes print("PW_OK") in the source
        line — substring matching would report a browser-less client as
        functional (found while validating issue #104)."""
        from lib.helpers import client_playwright_functional

        class Client:
            _client_ip = "10.99.99.104"

            def exec(self, cmd, timeout=30):
                return (
                    'Traceback (most recent call last):\n'
                    '  File "<string>", line 1, in <module>\n'
                    '    from playwright.sync_api import sync_playwright; '
                    'print("PW_OK")\n'
                    'ModuleNotFoundError: No module named \'playwright\''
                )

        ok, diag = client_playwright_functional(Client())
        assert ok is False
        assert "Traceback" in diag

    def test_pw_ok_with_leading_noise_still_ok(self):
        from lib.helpers import client_playwright_functional

        class Client:
            _client_ip = "10.99.99.105"

            def exec(self, cmd, timeout=30):
                return "playwright deprecation warning\nPW_OK"

        ok, diag = client_playwright_functional(Client())
        assert ok is True
        assert diag == "ok"

    def test_probe_cached_per_client(self):
        from lib.helpers import client_playwright_functional
        calls = []

        class Client:
            _client_ip = "10.99.99.100"

            def exec(self, cmd, timeout=30):
                calls.append(cmd)
                return "ModuleNotFoundError: No module named 'playwright'"

        ok, _ = client_playwright_functional(Client())
        ok2, _ = client_playwright_functional(Client())
        assert ok is False and ok2 is False
        assert len(calls) == 1

    def test_failure_diag_captured(self):
        from lib.helpers import client_playwright_functional

        class Client:
            _client_ip = "10.99.99.101"

            def exec(self, cmd, timeout=30):
                return "Executable doesn't exist at /root/.cache/ms-playwright/..."

        ok, diag = client_playwright_functional(Client())
        assert ok is False
        assert "Executable" in diag

    def test_skip_when_absent(self):
        from lib.helpers import skip_if_no_client_playwright

        class Client:
            _client_ip = "10.99.99.102"

            def exec(self, cmd, timeout=30):
                return "ModuleNotFoundError: No module named 'playwright'"

        with pytest.raises(pytest.skip.Exception, match="Playwright not functional"):
            skip_if_no_client_playwright(Client())

    def test_no_skip_when_functional(self):
        from lib.helpers import skip_if_no_client_playwright

        class Client:
            _client_ip = "10.99.99.103"

            def exec(self, cmd, timeout=30):
                return "PW_OK"

        skip_if_no_client_playwright(Client())

    def test_no_exec_adapter_reports_absent(self):
        from lib.helpers import client_playwright_functional

        ok, diag = client_playwright_functional(object())
        assert ok is False
        assert "no remote exec" in diag
