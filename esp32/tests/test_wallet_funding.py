import json
import pytest

pytestmark = [pytest.mark.requires_funding]


@pytest.mark.board_c
@pytest.mark.board_a
class TestWalletFunding:
    def test_fund_wallet_from_upstream(self, board_config, board_lock, wifi, http):
        wifi.connect_to_upstream()
        import time
        time.sleep(3)

        try:
            result = http.get_json(f"{board_config.api_url}/wallet")
            balance_before = result.get("balance", 0) if result else 0

            token = _create_cashu_token(wifi.config.mint_url, wifi.config.fund_amount)
            assert token.startswith("cashuA"), f"Invalid token format: {token[:30]}..."

            rc, body = http.post(f"{board_config.api_url}/", token)
            assert rc == 0, f"POST failed: rc={rc}"

            resp = json.loads(body)
            assert resp.get("kind") == 1022, (
                f"Payment failed (kind={resp.get('kind')}): {resp.get('content', '')}"
            )

            wallet = http.get_json(f"{board_config.api_url}/wallet")
            assert wallet is not None, "Wallet endpoint failed after funding"
            assert wallet["balance"] >= balance_before + 1, (
                f"Balance didn't increase: was {balance_before}, now {wallet['balance']}"
            )
            print(f"  Wallet funded: {wallet['balance']} sats ({wallet['proof_count']} proofs)")
        finally:
            wifi.connect_to_board(board_config)

    def test_spend_from_funded_wallet(self, funded_board, http, wifi):
        wallet = http.get_json(f"{funded_board.api_url}/wallet")
        assert wallet is not None, "Wallet unreachable"
        assert wallet["balance"] > 0, "Wallet has no balance to spend"

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
