import json
import pytest

pytestmark = [pytest.mark.requires_funding]


@pytest.mark.board_c
@pytest.mark.board_a
class TestWalletFunding:
    def test_fund_wallet_from_upstream(self, board_config, board_lock, wifi, http):
        wifi.connect_to_board(board_config)
        import time
        time.sleep(3)

        for _ in range(30):
            mints = http.get_json(f"{board_config.api_url}/mints")
            if mints and any(m.get("reachable") for m in mints):
                break
            time.sleep(2)

        try:
            token = _create_cashu_token(wifi.config.mint_url, wifi.config.fund_amount)
            assert token.startswith("cashuA"), f"Invalid token format: {token[:30]}..."

            rc, body = http.post(f"{board_config.api_url}/", token)
            assert rc == 0, f"POST failed: rc={rc}"

            resp = json.loads(body)
            assert resp.get("kind") == 1022, (
                f"Payment failed (kind={resp.get('kind')}): {resp.get('content', '')}"
            )
        finally:
            pass

    def test_spend_from_funded_wallet(self, funded_board, http, wifi):
        pytest.skip("Wallet receive requires successful keyset load from mint — "
                     "currently fails due to TLS timing during boot. "
                     "See: nucula_wallet keyset load race condition")

        body = http.get(f"{funded_board.api_url}/usage")
        assert body is not None, "/usage unreachable"
        assert body.strip() == "-1/-1", f"Expected no session, got: {body}"

        grant_body = http.get(f"{funded_board.portal_url}/grant_access")
        assert grant_body is not None, "grant_access failed"
        assert "granted" in grant_body

        import time
        time.sleep(2)

        usage_body = http.get(f"{funded_board.api_url}/usage")
        assert usage_body is not None
        parts = usage_body.strip().split("/")
        assert len(parts) == 2, f"Unexpected usage format: {usage_body}"
        remaining = int(parts[0])
        total = int(parts[1])
        assert total > 0, f"Expected positive total allotment, got {total}"

        reset_body = http.get(f"{funded_board.portal_url}/reset_authentication")
        assert reset_body is not None
        assert "reset" in reset_body


def _create_cashu_token(mint_url: str, amount: int) -> str:
    import subprocess
    result = subprocess.run(
        ["cashu", "-h", mint_url, "send", "--legacy", str(amount)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    for line in result.stdout.splitlines():
        if line.startswith("cashuA"):
            return line.strip()
    raise RuntimeError(
        f"No token in cashu output:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
