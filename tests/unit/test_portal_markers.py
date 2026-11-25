"""Portal-render marker contract for phone-tier portal waits.

Pins the dual-generation detection in lib/clients/wifi.py: legacy portals
expose data-sm state attributes; the v0.6 SPA ships none and is detected
via submit copy + tab-state attributes instead. Pins the #302 Cashu-tab
input-readiness gate: on the v0.6 tab contract, token INPUT readiness
requires the Cashu tab to be data-active (the Lightning tab's pay-line
copy also matches PORTAL_INPUT_READY_RE while carrying no token input).
"""
import pytest

from lib.clients.wifi import (
    PORTAL_AUTHED_RE,
    PORTAL_INPUT_READY_RE,
    PORTAL_LOADED_RE,
    WiFi,
    _cashu_tab_state,
)

pytestmark = [pytest.mark.story("G3")]

LEGACY_PORTAL_XML = (
    '<node class="android.webkit.WebView" text="Tollgate Captive Portal">'
    '<node class="android.view.View" content-desc=\'data-sm="portal_ready"\'/>'
    "</node>"
)
LEGACY_AUTHED_XML = '<node content-desc=\'data-sm="authed"\'/>'
LEGACY_COUNTDOWN_XML = '<node content-desc=\'data-sm="countdown"\'/>'

V06_PORTAL_XML = (
    '<node class="android.webkit.WebView">'
    '<node class="android.view.View" content-desc=\'data-active="false" '
    "tollgate-captive-portal-tabs-tab-lightning\" bounds=\"[10,300][520,340]\"/>"
    '<node class="android.view.View" content-desc=\'data-active="true" '
    "tollgate-captive-portal-tabs-tab-cashu\" bounds=\"[540,300][1070,340]\"/>"
    '<node class="android.widget.Button" text="Purchase Internet Access"/>'
    '<node class="android.widget.EditText"/>'
    "</node>"
)
V06_PAYLINE_XML = '<node class="android.widget.TextView" text="Pay 4 to get 150 MiB"/>'
V06_AUTHED_XML = (
    '<node class="android.webkit.WebView">'
    '<node class="android.widget.TextView" text="Data remaining: 120 MiB"/>'
    '<node class="android.widget.TextView" text="Thank you for your purchase"/>'
    "</node>"
)
V06_TAB_HIDDEN_XML = '<node content-desc=\'data-hidden="true" cashu-method\'/>'
V06_TAB_DISABLED_XML = '<node content-desc=\'data-disabled="true"\'/>'

ANDROID_SETTINGS_XML = (
    '<node class="android.widget.ListView">'
    '<node class="android.widget.TextView" text="Connected devices"/>'
    '<node class="android.widget.TextView" text="TollGate-326D, Connected"/>'
    "</node>"
)


class TestPortalLoaded:
    def test_legacy_data_sm_states_match(self):
        for state in ("portal_ready", "token_typing", "authed", "countdown"):
            assert PORTAL_LOADED_RE.search(f'content-desc=\'data-sm="{state}"\'')

    def test_legacy_title_matches(self):
        assert PORTAL_LOADED_RE.search(LEGACY_PORTAL_XML)

    def test_v06_submit_copy_matches(self):
        assert PORTAL_LOADED_RE.search(V06_PORTAL_XML)

    def test_v06_tab_state_attributes_match(self):
        for xml in (V06_TAB_HIDDEN_XML, V06_TAB_DISABLED_XML):
            assert PORTAL_LOADED_RE.search(xml)

    def test_android_settings_chrome_does_not_match(self):
        assert not PORTAL_LOADED_RE.search(ANDROID_SETTINGS_XML)

    def test_unrelated_data_attributes_do_not_match(self):
        assert not PORTAL_LOADED_RE.search('content-desc=\'data-testid="submit"\'')


class TestPortalInputReady:
    def test_legacy_portal_ready_matches(self):
        assert PORTAL_INPUT_READY_RE.search(LEGACY_PORTAL_XML)

    def test_v06_purchase_copy_matches(self):
        assert PORTAL_INPUT_READY_RE.search(V06_PORTAL_XML)

    def test_v06_payline_copy_matches(self):
        assert PORTAL_INPUT_READY_RE.search(V06_PAYLINE_XML)

    def test_authed_state_is_not_input_ready(self):
        assert not PORTAL_INPUT_READY_RE.search(LEGACY_AUTHED_XML)

    def test_v06_authed_dashboard_is_not_input_ready(self):
        assert not PORTAL_INPUT_READY_RE.search(V06_AUTHED_XML)


class TestPortalAuthed:
    def test_legacy_authed_states_match(self):
        assert PORTAL_AUTHED_RE.search(LEGACY_AUTHED_XML)
        assert PORTAL_AUTHED_RE.search(LEGACY_COUNTDOWN_XML)

    def test_v06_remaining_copy_matches(self):
        assert PORTAL_AUTHED_RE.search(V06_AUTHED_XML)

    def test_v06_thank_you_copy_matches(self):
        assert PORTAL_AUTHED_RE.search("text=\"Thank you for your purchase\"")

    def test_v06_session_active_matches(self):
        assert PORTAL_AUTHED_RE.search('text="Session active"')

    def test_preauth_portal_is_not_authed(self):
        assert not PORTAL_AUTHED_RE.search(V06_PORTAL_XML)
        assert not PORTAL_AUTHED_RE.search(V06_PAYLINE_XML)

    def test_legacy_portal_ready_is_not_authed(self):
        assert not PORTAL_AUTHED_RE.search(LEGACY_PORTAL_XML)

    def test_bare_connected_is_not_authed(self):
        assert not PORTAL_AUTHED_RE.search(ANDROID_SETTINGS_XML)

    def test_empty_dump_is_not_authed(self):
        assert not PORTAL_AUTHED_RE.search("")


V06_LIGHTNING_FIRST_XML = (
    '<node class="android.webkit.WebView">'
    '<node class="android.view.View" content-desc=\'data-active="true" '
    "tollgate-captive-portal-tabs-tab-lightning\" bounds=\"[10,300][520,340]\"/>"
    '<node class="android.view.View" content-desc=\'data-active="false" '
    "tollgate-captive-portal-tabs-tab-cashu\" bounds=\"[540,300][1070,340]\"/>"
    '<node class="android.widget.Button" text="Purchase Internet Access"/>'
    '<node class="android.widget.TextView" text="Pay 4 to get 150 MiB"/>'
    "</node>"
)
# Same shape, attribute side double-quoted with escaped inner quotes
# (uiautomator serializes both ways depending on build).
V06_CASHU_ACTIVE_ESCAPED_XML = (
    '<node class="android.view.View" '
    'content-desc="data-active=&quot;true&quot; '
    'tollgate-captive-portal-tabs-tab-cashu" bounds="[540,300][1070,340]"/>'
)


class TestCashuTabGate:
    """#302: input readiness requires the Cashu tab ACTIVE, not just copy."""

    def test_active_cashu_tab_found_with_center(self):
        found = _cashu_tab_state(V06_PORTAL_XML)
        assert found is not None
        cx, cy, active = found
        assert active is True
        # The fixture's cashu tab node carries bounds [0,0][0,0]-style
        # only when absent; here any parse must yield ints.
        assert isinstance(cx, int) and isinstance(cy, int)

    def test_lightning_first_portal_is_not_cashu_active(self):
        found = _cashu_tab_state(V06_LIGHTNING_FIRST_XML)
        assert found is not None
        assert found[2] is False
        # The wrong-tab shape still matches the legacy ready regex —
        # that is exactly the hazard the gate exists for.
        assert PORTAL_INPUT_READY_RE.search(V06_LIGHTNING_FIRST_XML)

    def test_legacy_portal_has_no_tab_contract(self):
        assert _cashu_tab_state(LEGACY_PORTAL_XML) is None
        assert _cashu_tab_state(LEGACY_AUTHED_XML) is None

    def test_escaped_quotes_still_resolve_active(self):
        found = _cashu_tab_state(V06_CASHU_ACTIVE_ESCAPED_XML)
        assert found is not None
        assert found[2] is True

    def test_empty_dump_has_no_tab(self):
        assert _cashu_tab_state("") is None


class FakeAdb:
    """Scripted ui_xml sequence + tap recorder — no hardware."""

    def __init__(self, dumps):
        self._dumps = list(dumps)
        self.taps = []

    def ui_xml(self):
        if len(self._dumps) > 1:
            return self._dumps.pop(0)
        return self._dumps[0]

    def tap(self, x, y):
        self.taps.append((x, y))


class TestWaitPortalInputReady:
    """_wait_portal_input_ready: the #302 Cashu-tab gate + tab switch."""

    STEP = 1     # fake clock seconds per time.time() call
    BASE = 1000.0  # start well above 0 so the >=10s tap gate is live at once

    def _wifi(self, dumps):
        wf = object.__new__(WiFi)
        wf.adb = FakeAdb(dumps)
        return wf

    def _fake_clock(self, monkeypatch):
        clock = {"t": self.BASE}
        monkeypatch.setattr(
            "lib.clients.wifi.time.time",
            lambda: (clock.__setitem__("t", clock["t"] + self.STEP)
                     or clock["t"]),
        )
        monkeypatch.setattr("lib.clients.wifi.time.sleep", lambda *_: None)

    def test_lightning_first_taps_cashu_tab_then_ready(self, monkeypatch):
        wf = self._wifi([
            V06_LIGHTNING_FIRST_XML,   # wrong tab active, ready-copy present
            V06_PORTAL_XML,            # after the tap: cashu tab active
        ])
        self._fake_clock(monkeypatch)
        assert wf._wait_portal_input_ready(timeout=30) is True
        assert len(wf.adb.taps) == 1, "cashu tab must be tapped exactly once"
        # tapped the cashu tab (x in its [540,300][1070,340] bounds), not
        # the lightning tab
        assert 540 <= wf.adb.taps[0][0] <= 1070

    def test_stuck_lightning_times_out_with_bounded_taps(self, monkeypatch):
        wf = self._wifi([V06_LIGHTNING_FIRST_XML])  # never switches
        self._fake_clock(monkeypatch)
        assert wf._wait_portal_input_ready(timeout=30) is False
        # bounded re-tap: one tap per >=10 clock seconds, not per poll
        assert 1 <= len(wf.adb.taps) <= 4

    def test_legacy_marker_gate_untouched(self, monkeypatch):
        wf = self._wifi([LEGACY_PORTAL_XML])
        self._fake_clock(monkeypatch)
        assert wf._wait_portal_input_ready(timeout=30) is True
        assert wf.adb.taps == []

    def test_active_cashu_tab_needs_no_tap(self, monkeypatch):
        wf = self._wifi([V06_PORTAL_XML])
        self._fake_clock(monkeypatch)
        assert wf._wait_portal_input_ready(timeout=30) is True
        assert wf.adb.taps == []
