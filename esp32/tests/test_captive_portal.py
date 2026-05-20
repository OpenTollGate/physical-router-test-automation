import re
import pytest


@pytest.mark.board_c
@pytest.mark.board_a
class TestCaptivePortal:
    @pytest.fixture(scope="class", autouse=True)
    def portal_html(self, board_connected, http):
        body = http.get(board_connected.portal_url)
        assert body is not None, "Portal returned no response"
        return body

    def test_portal_has_tollgate_branding(self, portal_html):
        assert "TollGate" in portal_html

    def test_portal_shows_price(self, portal_html):
        match = re.search(r"price-amount[^>]*>(\d+)", portal_html)
        assert match, "No price-amount element found in portal HTML"
        price = int(match.group(1))
        assert price > 0, f"Price should be positive, got {price}"

    def test_portal_has_cashu_input(self, portal_html):
        assert "tokenInput" in portal_html, "No #tokenInput textarea found"
        assert "cashuA" in portal_html, "No 'cashuA' placeholder found"

    def test_portal_has_pay_button(self, portal_html):
        assert "payBtn" in portal_html, "No payBtn element found"
        assert "Pay" in portal_html, "No 'Pay' text found"

    def test_no_unresolved_template_placeholders(self, portal_html):
        for placeholder in ["__AP_IP__", "__MINT_URL__", "__PRICE__"]:
            assert placeholder not in portal_html, f"Unresolved placeholder: {placeholder}"

    def test_mint_url_embedded_no_js_fetch(self, portal_html):
        assert "testnut.cashu.space" in portal_html, "Mint URL not embedded in portal HTML"
        assert "Loading..." not in portal_html, "Portal shows loading state (JS fetch needed?)"
        assert "Error loading" not in portal_html, "Portal shows error loading mint URL"

    def test_price_embedded_no_js_fetch(self, portal_html):
        assert "__PRICE__" not in portal_html, "Price placeholder not resolved"
        assert re.search(r"price-amount[^>]*>\d+", portal_html), "Price not rendered in HTML"
