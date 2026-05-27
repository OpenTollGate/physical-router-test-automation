import logging
from unittest.mock import patch, MagicMock

import pytest
import requests

log = logging.getLogger(__name__)


class CashuPayment:
    def __init__(self, mint_url: str, unit: str = "sat"):
        self.mint_url = mint_url.rstrip("/")
        self.unit = unit

    def create_mint_quote(self, amount_sats: int) -> dict:
        url = f"{self.mint_url}/v1/mint/quote/bolt11"
        payload = {"amount": amount_sats * 1000, "unit": self.unit}
        resp = requests.post(url, json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def check_mint_quote(self, quote_id: str) -> dict:
        url = f"{self.mint_url}/v1/mint/quote/bolt11/{quote_id}"
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def is_quote_paid(self, quote_id: str) -> bool:
        data = self.check_mint_quote(quote_id)
        return data.get("state") == "PAID"

    def get_quote_invoice(self, quote_id: str) -> str:
        data = self.check_mint_quote(quote_id)
        return data.get("invoice", "")

    def mint_tokens(self, quote_id: str, proofs_count: int = 1):
        url = f"{self.mint_url}/v1/mint/bolt11"
        payload = {"quote": quote_id, "outputs": []}
        try:
            resp = requests.post(url, json=payload, timeout=30)
            if resp.status_code == 200:
                return resp.json()
            return None
        except requests.RequestException:
            return None


class TestCashuPayment:
    def test_init_strips_trailing_slash(self):
        p = CashuPayment("https://mint.example.com/")
        assert p.mint_url == "https://mint.example.com"

    def test_init_no_trailing_slash(self):
        p = CashuPayment("https://mint.example.com")
        assert p.mint_url == "https://mint.example.com"

    def test_create_mint_quote_success(self):
        p = CashuPayment("https://mint.example.com", "sat")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"quote": "q123", "invoice": "lnbc1000...", "state": "UNPAID"}
        with patch("requests.post", return_value=mock_resp) as mock_post:
            result = p.create_mint_quote(1000)
            assert result["quote"] == "q123"
            mock_post.assert_called_once_with(
                "https://mint.example.com/v1/mint/quote/bolt11",
                json={"amount": 1000000, "unit": "sat"},
                timeout=30,
            )

    def test_create_mint_quote_converts_msats(self):
        p = CashuPayment("https://mint.example.com", "sat")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"quote": "q456", "invoice": "lnbc2000..."}
        with patch("requests.post", return_value=mock_resp) as mock_post:
            result = p.create_mint_quote(2000)
            assert result is not None
            mock_post.assert_called_once()
            call_kwargs = mock_post.call_args
            assert call_kwargs[1]["json"]["amount"] == 2000000

    def test_create_mint_quote_failure(self):
        p = CashuPayment("https://mint.example.com")
        with patch("requests.post", side_effect=requests.RequestException("timeout")):
            with pytest.raises(requests.RequestException):
                p.create_mint_quote(1000)

    def test_check_mint_quote(self):
        p = CashuPayment("https://mint.example.com")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"quote": "q123", "state": "UNPAID"}
        with patch("requests.get", return_value=mock_resp):
            result = p.check_mint_quote("q123")
            assert result["state"] == "UNPAID"

    def test_is_quote_paid_true(self):
        p = CashuPayment("https://mint.example.com")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"quote": "q123", "state": "PAID"}
        with patch("requests.get", return_value=mock_resp):
            assert p.is_quote_paid("q123") is True

    def test_is_quote_paid_false(self):
        p = CashuPayment("https://mint.example.com")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"quote": "q123", "state": "UNPAID"}
        with patch("requests.get", return_value=mock_resp):
            assert p.is_quote_paid("q123") is False

    def test_is_quote_paid_pending(self):
        p = CashuPayment("https://mint.example.com")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"quote": "q123", "state": "PENDING"}
        with patch("requests.get", return_value=mock_resp):
            assert p.is_quote_paid("q123") is False

    def test_mint_tokens_success(self):
        p = CashuPayment("https://mint.example.com")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"signatures": [{"id": "sig1"}]}
        with patch("requests.post", return_value=mock_resp):
            result = p.mint_tokens("q123")
            assert result is not None
            assert "signatures" in result

    def test_mint_tokens_not_paid(self):
        p = CashuPayment("https://mint.example.com")
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.text = "quote not paid"
        with patch("requests.post", return_value=mock_resp):
            result = p.mint_tokens("q123")
            assert result is None

    def test_mint_tokens_network_error(self):
        p = CashuPayment("https://mint.example.com")
        with patch("requests.post", side_effect=requests.RequestException("connection error")):
            result = p.mint_tokens("q123")
            assert result is None
