"""Auto-minting Cashu token fixture with setup/teardown.

Provides a pytest fixture (``minted_token``) that mints a Cashu token from the
test mint at setup, yields it to the test, and cleans up at teardown so no
dangling tokens are left behind.

The fixture is *parameterizable* (amount, mint_url) and *resilient*: if the
mint is unreachable it ``pytest.skip``s the test instead of failing.

Why a dedicated fixture
-----------------------
Previously every test that needed a token called ``cashu.mint(N)`` inline.
That spread minting, retry, and skip-on-unavailable logic across many test
files and left no central place to enforce teardown bookkeeping. This module
centralizes that: setup mints (reusing the session-scoped ``cashu`` TokenPool
when the mint URL matches), teardown records whether the token was consumed
and tears down any per-mint local wallet state so nothing leaks.

Usage
-----

Indirect parameterization of the amount::

    @pytest.mark.parametrize("minted_token", [1, 4], indirect=True)
    def test_pay(minted_token, router):
        resp = router.pay_direct(minted_token.token)
        minted_token.mark_consumed()
        assert resp  # ...

Marker-based amount / mint URL (no parametrize needed)::

    @pytest.mark.mint_amount(4)
    @pytest.mark.mint_url("https://testnut.cashu.exchange")
    def test_pay(minted_token, router):
        ...

Plain fixture (defaults: amount=4, mint=TOLLGATE_TEST_MINT_URL)::

    def test_pay(minted_token, router):
        resp = router.pay_direct(minted_token.token)
        minted_token.mark_consumed()

See AGENTS.md "Test Gating Strategies" for the skip / xfail conventions this
fixture follows (it uses ``pytest.skip`` on mint unavailability, like the
session-scoped ``cashu`` fixture and the ``skip_if_no_*`` helpers).
"""
from __future__ import annotations

import logging
import os
import shutil
import time
from dataclasses import dataclass, field
from typing import Any

import pytest

from lib.cashu import (
    CashuMint,
    CdkCliWallet,
    HttpMinter,
    MintUnavailableError,
    TokenPool,
    create_minter,
)
from lib.constants import TEST_MINT_URL

log = logging.getLogger("tollgate.cashu_fixture")

#: Default amount (sats) minted when no override is given. Matches
#: ``lib.constants.TOKEN_DEFAULT``.
DEFAULT_MINT_AMOUNT = 4


@dataclass
class MintedToken:
    """A minted Cashu token with bookkeeping metadata.

    Attributes:
        token: The serialized ``cashuA...`` / ``cashuB...`` token string.
        amount: Amount in sats that was minted.
        mint_url: The mint URL the token was minted from.
        minter: The underlying minter object (for introspection / cleanup).
        minted_at: ``time.monotonic()`` timestamp captured at mint time.
        consumed: Set True by :meth:`mark_consumed` when the test has spent
            the token (e.g. POSTed it to the backend). Drives teardown logic.
    """

    token: str
    amount: int
    mint_url: str
    minter: Any
    minted_at: float = field(default_factory=time.monotonic)
    consumed: bool = False

    def mark_consumed(self) -> None:
        """Flag the token as spent by the test (called after a successful pay)."""
        self.consumed = True


def _resolve_amount(request) -> int:
    """Resolve the mint amount from indirect param, marker, or env/default."""
    # Indirect parametrize: @pytest.mark.parametrize("minted_token", [N], indirect=True)
    indirect = getattr(request, "param", None)
    if isinstance(indirect, int):
        return indirect
    if isinstance(indirect, dict) and "amount" in indirect:
        return int(indirect["amount"])

    marker = request.node.get_closest_marker("mint_amount")
    if marker and marker.args:
        return int(marker.args[0])

    return int(os.environ.get("TOLLGATE_MINT_AMOUNT", str(DEFAULT_MINT_AMOUNT)))


def _resolve_mint_url(request) -> str:
    """Resolve the mint URL from marker, env, or the default test mint."""
    marker = request.node.get_closest_marker("mint_url")
    if marker and marker.args:
        return str(marker.args[0])
    return os.environ.get("TOLLGATE_TEST_MINT_URL", TEST_MINT_URL)


def _resolve_minter(request, mint_url: str) -> Any | None:
    """Reuse the session-scoped ``cashu`` minter when its mint matches.

    Falls back to ``None`` (which makes :func:`mint_token` create a fresh
    minter) when the session minter targets a different mint or is unavailable.
    """
    try:
        session_minter = request.getfixturevalue("cashu")
    except Exception:
        # The session ``cashu`` fixture may skip or otherwise be unavailable.
        return None
    if getattr(session_minter, "mint_url", None) == mint_url:
        return session_minter
    return None


def mint_token(
    amount: int = DEFAULT_MINT_AMOUNT,
    mint_url: str = TEST_MINT_URL,
    minter: Any | None = None,
    timeout: int = 120,
    retries: int = 2,
) -> MintedToken:
    """Mint a single Cashu token, returning a :class:`MintedToken`.

    Skips (via ``pytest.skip``) when the mint is unreachable, so callers can
    use this directly inside a test body without wrapping in try/except.
    """
    if minter is None:
        minter = create_minter(mint_url)

    try:
        minter.ensure_mint_available(timeout=15)
    except MintUnavailableError as exc:
        pytest.skip(f"cashu mint unavailable: {exc}")

    try:
        token = minter.mint(amount=amount, timeout=timeout, retries=retries)
    except MintUnavailableError as exc:
        pytest.skip(f"cashu mint unavailable during mint: {exc}")

    if not token or not token.startswith(("cashuA", "cashuB")):
        pytest.fail(f"mint produced invalid token: {str(token)[:80]}")

    return MintedToken(
        token=token,
        amount=amount,
        mint_url=getattr(minter, "mint_url", mint_url),
        minter=minter,
    )


def cleanup_token(minted: MintedToken) -> None:
    """Best-effort teardown for a minted token (no dangling tokens).

    Cashu ecash tokens are bearer instruments: once spent (e.g. POSTed to the
    backend) they cannot be "unspent". Teardown therefore:

    1. Records whether the token was consumed by the test (audit trail).
    2. For CLI-based minters (``CashuMint`` / ``CdkCliWallet``) removes the
       per-mint ephemeral work directory so no wallet DB or key material leaks
       on disk. ``HttpMinter`` and ``TokenPool`` hold no local state to clean.

    Testnut FakeWallet tokens have no real value, so an unconsumed token is
    logged as a warning rather than treated as a hard failure.
    """
    age = time.monotonic() - minted.minted_at
    if minted.consumed:
        log.info(
            "minted_token teardown: token consumed by test "
            "(amount=%d, mint=%s, age=%.1fs)",
            minted.amount, minted.mint_url, age,
        )
    else:
        log.warning(
            "minted_token teardown: token NOT consumed by test — leaving ecash "
            "unspent (amount=%d, mint=%s, age=%.1fs). Testnut tokens have no "
            "real value.",
            minted.amount, minted.mint_url, age,
        )

    # Unwrap TokenPool to reach the underlying stateful minter.
    minter = minted.minter
    if isinstance(minter, TokenPool):
        minter = getattr(minter, "_minter", minter)

    if isinstance(minter, (CashuMint, CdkCliWallet)):
        work_dir = getattr(minter, "_work_dir", None)
        if work_dir:
            shutil.rmtree(work_dir, ignore_errors=True)
            log.debug("removed minter work dir %s", work_dir)


@pytest.fixture
def minted_token(request):
    """Per-test auto-minted Cashu token with setup/teardown.

    Setup mints a token from the test mint, teardown runs :func:`cleanup_token`
    so no dangling tokens / wallet state are left behind. Reuses the
    session-scoped ``cashu`` TokenPool when the configured mint matches, keeping
    the token pool warm and avoiding redundant minter creation.

    Parameterization:
      * amount — ``@pytest.mark.parametrize("minted_token", [N], indirect=True)``
        or ``@pytest.mark.mint_amount(N)`` or env ``TOLLGATE_MINT_AMOUNT``
        (default 4).
      * mint_url — ``@pytest.mark.mint_url("https://...")`` or env
        ``TOLLGATE_TEST_MINT_URL``.

    Skips the test when the mint is unavailable (does not fail).

    Yields:
        MintedToken
    """
    amount = _resolve_amount(request)
    mint_url = _resolve_mint_url(request)
    minter = _resolve_minter(request, mint_url)

    minted = mint_token(amount=amount, mint_url=mint_url, minter=minter)
    yield minted
    cleanup_token(minted)
