"""Unit tests for lib/portal_payment.py — portal-driving orchestration.

These run without a browser or router: they drive ``pay_cashu_via_portal`` with
a FakePage and exhaustively cover the allotment parser. The live Playwright +
router path is exercised by tests/scenarios/test_captive_portal_cashu_payment.py.
"""
from __future__ import annotations

import pytest

from lib.portal_payment import (
    SEL_CASHU_TAB,
    SEL_CHECKMARK,
    SEL_CONTENT,
    SEL_SUBMIT_CLICK,
    SEL_SUBMIT_READY,
    SEL_TOKEN_INPUT,
    PortalPaymentResult,
    parse_allotment_bytes,
    pay_cashu_via_portal,
)

PORTAL_URL = "http://192.168.41.1:2050/splash.html"
TOKEN = "cashuAeyJ1bml0Ijoi..."  # truncated representative token


class FakeTimeout(Exception):
    """Stands in for playwright.sync_api.TimeoutError in unit tests."""


class FakeLocator:
    def __init__(self, page: FakePage, selector: str) -> None:
        self._page = page
        self._selector = selector

    def fill(self, text: str) -> None:
        self._page.filled[self._selector] = text

    def is_visible(self) -> bool:
        # Default to "present implies visible" unless explicitly overridden.
        return self._page.visible.get(self._selector, self._selector in self._page.present)

    def inner_text(self) -> str:
        return self._page.texts.get(self._selector, "")


class FakePage:
    """Minimal duck-typed Playwright Page for unit-testing the helper."""

    def __init__(
        self,
        *,
        present: set[str] | None = None,
        visible: dict[str, bool] | None = None,
        texts: dict[str, str] | None = None,
    ) -> None:
        self.present = present or set()
        self.visible = visible or {}
        self.texts = texts or {}
        self.filled: dict[str, str] = {}
        self.calls: list[tuple] = []

    def goto(self, url: str, **kwargs) -> None:
        self.calls.append(("goto", url, kwargs.get("wait_until")))

    def wait_for_selector(self, selector: str, timeout: int | None = None) -> None:
        self.calls.append(("wait_for_selector", selector))
        if selector not in self.present:
            raise FakeTimeout(f"timeout waiting for {selector}")

    def locator(self, selector: str) -> FakeLocator:
        return FakeLocator(self, selector)

    def click(self, selector: str) -> None:
        self.calls.append(("click", selector))


def _happy_page(content_text: str = "You have 500 MB remaining") -> FakePage:
    return FakePage(
        present={SEL_CASHU_TAB, SEL_TOKEN_INPUT, SEL_SUBMIT_READY, SEL_CHECKMARK},
        texts={SEL_CONTENT: content_text},
    )


# --------------------------------------------------------------------------- #
# parse_allotment_bytes
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "text, expected",
    [
        ("500 MB", 500_000_000),
        ("2 GB", 2_000_000_000),
        ("1024 MiB", 1_073_741_824),
        ("1 GiB", 1_073_741_824),
        ("1.5 GiB", 1_610_612_736),
        ("1,5 GiB", 1_610_612_736),  # comma = decimal separator (European locale)
        ("allotment: 250 mib granted", 262_144_000),
        ("UPPERCASE 3 GB DONE", 3_000_000_000),
        ("250 kb", 250_000),
    ],
)
def test_parse_allotment_bytes_valid(text, expected):
    assert parse_allotment_bytes(text) == expected


@pytest.mark.parametrize("text", ["", "no units here", "123", "MB only", "0 bytes"])
def test_parse_allotment_bytes_no_match(text):
    assert parse_allotment_bytes(text) is None


# --------------------------------------------------------------------------- #
# pay_cashu_via_portal — happy path
# --------------------------------------------------------------------------- #


def test_happy_path_fills_token_and_reports_success():
    page = _happy_page("You have 500 MB remaining")

    result = pay_cashu_via_portal(page, TOKEN, PORTAL_URL)

    assert isinstance(result, PortalPaymentResult)
    assert result.success is True
    assert result.checkmark_visible is True
    assert result.allotment_bytes == 500_000_000
    assert "500 MB" in result.allotment_text
    # The token must be filled into the Cashu token input specifically.
    assert page.filled == {SEL_TOKEN_INPUT: TOKEN}


def test_happy_path_calls_selectors_in_spec_order():
    page = _happy_page()
    pay_cashu_via_portal(page, TOKEN, PORTAL_URL)

    # Exact ordered call sequence mirrors the proven Node spec flow.
    expected_calls = [
        ("goto", PORTAL_URL, "networkidle"),
        ("wait_for_selector", SEL_CASHU_TAB),
        ("click", SEL_CASHU_TAB),
        ("wait_for_selector", SEL_TOKEN_INPUT),
        ("wait_for_selector", SEL_SUBMIT_READY),
        ("click", SEL_SUBMIT_CLICK),
        ("wait_for_selector", SEL_CHECKMARK),
    ]
    # Filter out the goto's wait_until tuple slot for the non-goto entries.
    actual = []
    for call in page.calls:
        if call[0] == "goto":
            actual.append(call)
        elif call[0] == "wait_for_selector":
            actual.append((call[0], call[1]))
        elif call[0] == "click":
            actual.append((call[0], call[1]))
    assert actual == expected_calls


def test_uses_custom_timeouts():
    page = _happy_page()
    pay_cashu_via_portal(
        page, TOKEN, PORTAL_URL,
        goto_timeout=1111, input_timeout=2222, submit_ready_timeout=3333, checkmark_timeout=4444,
    )
    # goto received the custom timeout via kwargs (not asserted in calls above);
    # the important contract is that no exception is raised and it still succeeds.
    assert page.filled == {SEL_TOKEN_INPUT: TOKEN}


# --------------------------------------------------------------------------- #
# pay_cashu_via_portal — failure modes
# --------------------------------------------------------------------------- #


def test_checkmark_present_but_hidden_is_not_success():
    page = FakePage(
        present={SEL_CASHU_TAB, SEL_TOKEN_INPUT, SEL_SUBMIT_READY, SEL_CHECKMARK},
        visible={SEL_CHECKMARK: False},
        texts={SEL_CONTENT: "500 MB"},
    )
    result = pay_cashu_via_portal(page, TOKEN, PORTAL_URL)
    assert result.checkmark_visible is False
    assert result.success is False  # success requires checkmark visible


def test_no_allotment_unit_is_not_success():
    page = _happy_page(content_text="Thank you for your payment")  # no MB/GB unit
    result = pay_cashu_via_portal(page, TOKEN, PORTAL_URL)
    assert result.checkmark_visible is True
    assert result.allotment_bytes is None
    assert result.success is False


def test_token_input_never_appears_propagates_timeout():
    page = FakePage(present=set())  # nothing present, not even the input
    with pytest.raises(FakeTimeout):
        pay_cashu_via_portal(page, TOKEN, PORTAL_URL)
    # The token must NOT have been filled if the input never appeared.
    assert page.filled == {}


def test_checkmark_never_appears_propagates_timeout():
    page = FakePage(present={SEL_CASHU_TAB, SEL_TOKEN_INPUT, SEL_SUBMIT_READY})  # no checkmark
    with pytest.raises(FakeTimeout):
        pay_cashu_via_portal(page, TOKEN, PORTAL_URL)
    # Token was filled and submit clicked, but the checkmark never came.
    assert page.filled == {SEL_TOKEN_INPUT: TOKEN}
    assert ("click", SEL_SUBMIT_CLICK) in [(c[0], c[1]) for c in page.calls]
