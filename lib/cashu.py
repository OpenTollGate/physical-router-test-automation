from __future__ import annotations

import hashlib
import json
import logging
import secrets as _secrets_mod
import shutil
import signal
import socket
import subprocess
import tempfile
import os
import base64
import time
import re
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING

from urllib import error, request
from urllib.parse import urlparse

from lib.constants import TEST_MINT_URL



class MintUnavailableError(Exception):
    pass


class _MintTimeoutError(Exception):
    pass


def mint_reachable(mint_url: str, timeout: float = 3.0) -> bool:
    """TCP-probe a mint URL. The cashu CLI can block for minutes against an
    absent mint, so tests should skip via this probe before driving the CLI."""
    try:
        parsed = urlparse(mint_url)
        host = parsed.hostname or mint_url
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


class CashuMint:
    def __init__(self, venv_path: str | None = None, mint_url: str = TEST_MINT_URL):
        venv_path = venv_path or os.environ.get("TOLLGATE_CASHU_VENV", "/opt/cashu-venv")
        self.venv_path = venv_path
        self.mint_url = mint_url
        self._python = os.path.join(venv_path, "bin", "python")
        self._cashu = os.path.join(venv_path, "bin", "cashu")

    def is_available(self) -> bool:
        return os.path.isfile(self._cashu)

    def _env(self):
        env = os.environ.copy()
        env["VIRTUAL_ENV"] = self.venv_path
        env["PATH"] = f"{self.venv_path}/bin:{env.get('PATH', '')}"
        return env

    def ensure_mint_available(self, timeout: int = 15):
        keys_url = f"{self.mint_url.rstrip('/')}/v1/keys"
        req = request.Request(keys_url, headers={"User-Agent": "tollgate-test/1.0"})
        try:
            with request.urlopen(req, timeout=timeout) as response:
                if response.status != 200:
                    raise MintUnavailableError(
                        f"Mint health check failed with HTTP {response.status}"
                    )
        except MintUnavailableError:
            raise
        except (error.URLError, TimeoutError) as exc:
            raise MintUnavailableError(f"cashu mint unavailable: {exc}") from exc
        except Exception as exc:
            raise MintUnavailableError(f"cashu mint unexpected error: {exc}") from exc

    def _find_latest_quote_id(self, url=None):
        mint_url = url or self.mint_url
        r = subprocess.run(
            [self._cashu, "-h", mint_url, "-t", "invoices"],
            capture_output=True, text=True, timeout=30, env=self._env(),
        )
        entries = self._parse_invoices(r.stdout)
        if not entries:
            return None
        return entries[-1].get("id", "").strip()

    @staticmethod
    def _parse_invoices(output):
        entries = []
        current = {}
        for line in output.split("\n"):
            line = line.strip()
            if line.startswith("#"):
                if current:
                    entries.append(current)
                current = {}
            elif ":" in line and current is not None:
                k, v = line.split(":", 1)
                current[k.strip().lower()] = v.strip()
        if current:
            entries.append(current)
        return entries

    def _count_invoices(self):
        r = subprocess.run(
            [self._cashu, "-h", self.mint_url, "-t", "invoices"],
            capture_output=True, text=True, timeout=30, env=self._env(),
        )
        return r.stdout.count("Mint quote")

    def _wait_and_claim(self, quote_id, amount, timeout=30):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(2, remaining))
            try:
                r = subprocess.run(
                    [self._cashu, "-h", self.mint_url, "-t",
                     "invoice", str(amount), "--id", quote_id],
                    capture_output=True, text=True,
                    timeout=min(15, max(5, remaining)),
                    env=self._env(),
                )
                if "Invoice paid" in r.stdout:
                    return True
            except subprocess.TimeoutExpired:
                if time.monotonic() >= deadline:
                    raise MintUnavailableError(
                        f"cashu claim timed out after {timeout}s"
                    )
            except Exception:
                pass
        return False

    def _ensure_balance(self, amount):
        r = subprocess.run(
            [self._cashu, "-h", self.mint_url, "-t", "balance"],
            capture_output=True, text=True, timeout=30, env=self._env(),
        )
        match = re.search(r"Balance:\s*(\d+)", r.stdout)
        if match and int(match.group(1)) >= amount:
            return

        before = self._count_invoices()

        proc = subprocess.Popen(
            [self._cashu, "-h", self.mint_url, "-t", "-y", "invoice", str(amount)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self._env(),
        )
        time.sleep(3)
        proc.kill()
        proc.communicate()

        created = False
        for attempt in range(10):
            after = self._count_invoices()
            if after > before:
                created = True
                break
            time.sleep(2)

        if not created:
            raise RuntimeError(
                f"Invoice not created after 25s (before={before}, after={after}, mint={self.mint_url})"
            )

        quote_id = self._find_latest_quote_id()
        if not quote_id:
            raise RuntimeError("No quote found after invoice creation")

        if not self._wait_and_claim(quote_id, amount, timeout=60):
            raise RuntimeError(f"Mint claim failed for quote {quote_id}")

    def _timeout_handler(self, signum, frame):
        raise _MintTimeoutError()

    def warmup(self, timeout: int = 60) -> None:
        """Pre-initialize the cashu wallet DB and fetch keysets.

        The first ``cashu`` CLI call is slow (Python startup + wallet DB
        creation + keyset fetch ≈ 10-15 s on a cold GCP VM).  Calling this
        once during fixture setup prevents the first real ``mint()`` from
        blowing past the SIGALRM deadline.
        """
        if not self.is_available():
            return
        try:
            subprocess.run(
                [self._cashu, "-h", self.mint_url, "-t", "balance"],
                capture_output=True, text=True, timeout=timeout, env=self._env(),
            )
        except (subprocess.TimeoutExpired, Exception):
            pass  # non-fatal — the real mint() will retry

    def mint(self, amount: int = 4, legacy: bool = True, timeout: int = 120, retries: int = 2) -> str:
        if not self.is_available():
            raise RuntimeError(f"cashu venv not found at {self.venv_path}")

        old_handler = signal.signal(signal.SIGALRM, self._timeout_handler)
        last_err: RuntimeError = RuntimeError("mint failed with no specific error")
        for attempt in range(1 + retries):
            signal.alarm(timeout)
            try:
                return self._mint_inner(amount, legacy)
            except _MintTimeoutError:
                raise MintUnavailableError(
                    f"mint() timed out after {timeout}s"
                )
            except RuntimeError as exc:
                last_err = exc
                if attempt < retries:
                    time.sleep(5 * (attempt + 1))
            finally:
                signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)
        raise last_err  # type: ignore[misc]

    def _mint_inner(self, amount: int, legacy: bool = True) -> str:
        env = self._env()

        self.ensure_mint_available()
        self._ensure_balance(amount)

        cmd = [self._cashu, "-h", self.mint_url, "-t", "-y", "send", str(amount)]
        if legacy:
            cmd.append("--legacy")

        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120, env=env)
        token = r.stdout.strip().split("\n")[0]
        if not token.startswith(("cashuA", "cashuB")):
            raise RuntimeError(f"Send failed: {r.stdout[-200:]}")
        return token

    def mint_from_wrong_mint(self, amount: int = 4, timeout: int = 90) -> str:
        return self.synthetic_wrong_mint_token()

    @staticmethod
    def synthetic_wrong_mint_token() -> str:
        payload = [{"mint": "https://wrong-mint.example.com",
                     "proofs": [{"amount": 4, "secret": "fake", "C": "fake"}]}]
        return "cashuA" + base64.b64encode(json.dumps(payload).encode()).decode()


class MockCashuMinter:
    """Token fabricator for TOLLGATE_MOCK=1 runs (no mint, no router).

    Produces structurally valid V3 tokens (V1 keyset ids — ``00``-prefix,
    16 hex chars) that the mock backend in lib/mock_router.py accepts: the
    mock's payment handler validates shape, mint URL against its accepted
    mints, and double-spends, so the payment-tier api tests exercise their
    full assert paths mock-side. Tokens carry no real value — they only
    round-trip against the mock backend, never a real one.
    """

    def __init__(self, mint_url: str = "https://testnut.cashu.exchange"):
        self.mint_url = mint_url
        # One deterministic V1-shaped keyset id per mint URL, published via
        # keyset_ids() — mock-mode keyset tests validate minted proofs
        # against exactly this set (parity with a real mint's /v1/keys).
        self.keyset_id = "00" + hashlib.sha256(mint_url.encode()).hexdigest()[:14]

    def keyset_ids(self) -> set[str]:
        return {self.keyset_id}

    def keyset_keys(self) -> dict[str, str]:
        """NUT-02 amount→pubkey map for the deterministic keyset (standard
        power-of-2 amounts, hex pubkeys — the shape keyset tests assert)."""
        return {
            str(1 << i): "02" + hashlib.sha256(f"{self.mint_url}:{1 << i}".encode()).hexdigest()
            for i in range(11)  # 1 .. 1024
        }

    def is_available(self) -> bool:
        return True

    def ensure_mint_available(self, timeout: int = 15):
        return None

    def warmup(self, timeout: int = 60) -> None:
        return None

    def _fabricate(self, amount: int, mint_url: str) -> str:
        payload = {
            "token": [{
                "mint": mint_url,
                "proofs": [{
                    "amount": amount,
                    "id": self.keyset_id,
                    "secret": _secrets_mod.token_hex(16),
                    "C": "02" + _secrets_mod.token_hex(32),
                }],
            }],
            "unit": "sat",
            "memo": None,
        }
        return "cashuA" + base64.b64encode(json.dumps(payload).encode()).decode()

    def mint(self, amount: int = 4, legacy: bool = True, timeout: int = 120,
             retries: int = 2) -> str:
        return self._fabricate(amount, self.mint_url)

    def mint_from_wrong_mint(self, amount: int = 4, timeout: int = 90) -> str:
        return self.synthetic_wrong_mint_token()

    @staticmethod
    def synthetic_wrong_mint_token() -> str:
        payload = [{"mint": "https://wrong-mint.example.com",
                     "proofs": [{"amount": 4, "secret": "fake", "C": "fake"}]}]
        return "cashuA" + base64.b64encode(json.dumps(payload).encode()).decode()


_CDK_CLI_PATHS = [
    "/opt/cdk-mintd/cdk-cli",
    "/usr/local/bin/cdk-cli",
]


class CdkCliWallet:
    def __init__(self, mint_url: str, cdk_cli_path: str | None = None):
        self.mint_url = mint_url
        if cdk_cli_path:
            self._cli = cdk_cli_path
        else:
            self._cli = self._find_cli()
        self._work_dir: str | None = None

    def _find_cli(self) -> str:
        for path in _CDK_CLI_PATHS:
            if os.path.isfile(path) and os.access(path, os.X_OK):
                return path
        for dir_entry in os.environ.get("PATH", "").split(os.pathsep):
            candidate = os.path.join(dir_entry, "cdk-cli")
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
        return _CDK_CLI_PATHS[0]

    def is_available(self) -> bool:
        return os.path.isfile(self._cli) and os.access(self._cli, os.X_OK)

    def ensure_mint_available(self, timeout: int = 15):
        keys_url = f"{self.mint_url.rstrip('/')}/v1/keys"
        req = request.Request(keys_url, headers={"User-Agent": "tollgate-test/1.0"})
        try:
            with request.urlopen(req, timeout=timeout) as response:
                if response.status != 200:
                    raise MintUnavailableError(
                        f"Mint health check failed with HTTP {response.status}"
                    )
        except MintUnavailableError:
            raise
        except (error.URLError, TimeoutError) as exc:
            raise MintUnavailableError(f"cashu mint unavailable: {exc}") from exc
        except Exception as exc:
            raise MintUnavailableError(f"cashu mint unexpected error: {exc}") from exc

    def _get_work_dir(self) -> str:
        if self._work_dir and os.path.isdir(self._work_dir):
            return self._work_dir
        self._work_dir = tempfile.mkdtemp(prefix="cdk-cli-session-")
        return self._work_dir

    def _mint_token(self, amount: int, timeout: int) -> str:
        work_dir = self._get_work_dir()
        mint_r = subprocess.run(
            [self._cli, "-w", work_dir, "mint", self.mint_url, str(amount)],
            capture_output=True, text=True, timeout=timeout,
        )
        if mint_r.returncode != 0:
            err = mint_r.stderr[-300:]
            if "already exists" in err or "pending" in err.lower():
                shutil.rmtree(work_dir, ignore_errors=True)
                self._work_dir = None
                work_dir = self._get_work_dir()
                mint_r = subprocess.run(
                    [self._cli, "-w", work_dir, "mint", self.mint_url, str(amount)],
                    capture_output=True, text=True, timeout=timeout,
                )
            if mint_r.returncode != 0:
                raise RuntimeError(
                    f"cdk-cli mint failed (exit {mint_r.returncode}): "
                    f"{mint_r.stderr[-300:]}"
                )

        send_r = subprocess.run(
            [self._cli, "-w", work_dir, "send", "--mint-url", self.mint_url, "--v3"],
            input=f"{amount}\n",
            capture_output=True, text=True, timeout=timeout,
        )
        if send_r.returncode != 0:
            raise RuntimeError(
                f"cdk-cli send failed (exit {send_r.returncode}): "
                f"stdout={send_r.stdout[-200:]} stderr={send_r.stderr[-200:]}"
            )

        for line in send_r.stdout.strip().splitlines():
            line = line.strip()
            if line.startswith(("cashuA", "cashuB")):
                return line

        raise RuntimeError(
            f"cdk-cli send produced no token: {send_r.stdout[-300:]}"
        )

    def warmup(self, timeout: int = 60) -> None:
        if not self.is_available():
            return
        work_dir = self._get_work_dir()
        try:
            subprocess.run(
                [self._cli, "-w", work_dir, "balance"],
                capture_output=True, text=True, timeout=timeout,
            )
        except (subprocess.TimeoutExpired, Exception):
            pass  # non-fatal

    def mint(self, amount: int = 4, legacy: bool = False, timeout: int = 90,
             retries: int = 2) -> str:
        if not self.is_available():
            raise RuntimeError(f"cdk-cli not found at {self._cli}")

        last_err: RuntimeError = RuntimeError("mint failed with no specific error")
        for attempt in range(1 + retries):
            try:
                self.ensure_mint_available()
                return self._mint_token(amount, timeout)
            except subprocess.TimeoutExpired:
                raise MintUnavailableError(
                    f"cdk-cli mint() timed out after {timeout}s"
                )
            except RuntimeError as exc:
                last_err = exc
                if attempt < retries:
                    time.sleep(5 * (attempt + 1))
        raise last_err

    @staticmethod
    def synthetic_wrong_mint_token() -> str:
        return CashuMint.synthetic_wrong_mint_token()

    def mint_from_wrong_mint(self, amount: int = 4, timeout: int = 90) -> str:
        return self.synthetic_wrong_mint_token()


def create_minter(
    mint_url: str = TEST_MINT_URL,
    venv_path: str | None = None,
) -> CdkCliWallet | CashuMint:
    """Reference-first minter selection (#268): cdk-cli is the CDK
    reference; the nutshell cashu CLI is the Python-reference fallback.
    The hand-rolled HttpMinter was removed — it drifted from the spec
    (v1-truncated keyset ids) and co-masked the portal defect in #247.
    """
    cdk = CdkCliWallet(mint_url)
    if cdk.is_available():
        try:
            cdk.ensure_mint_available(timeout=10)
            log.info("create_minter: selected CdkCliWallet (mint=%s)", mint_url)
            return cdk
        except Exception as exc:
            log.warning(
                "create_minter: cdk-cli present but mint unavailable (mint=%s, error=%s); falling back",
                mint_url, exc,
            )

    log.info("create_minter: selected CashuMint (mint=%s, venv=%s)", mint_url, venv_path)
    return CashuMint(venv_path=venv_path, mint_url=mint_url)


log = logging.getLogger("tollgate.token_pool")


class TokenPool:
    _POOL_AMOUNT = 4

    def __init__(self, minter: CashuMint | CdkCliWallet, pool_size: int = 10):
        self._minter = minter
        self._pool_size = pool_size
        self._queue: deque[str] = deque()
        self._lock = threading.Lock()
        self._minter_url = getattr(minter, "mint_url", "<unknown>")
        self._prefill()

    def _prefill(self):
        t0 = time.monotonic()
        successes = 0
        with ThreadPoolExecutor(max_workers=min(self._pool_size, 5)) as pool:
            futures = {
                pool.submit(self._minter.mint, self._POOL_AMOUNT): i
                for i in range(self._pool_size)
            }
            for future in as_completed(futures):
                try:
                    token = future.result()
                    with self._lock:
                        self._queue.append(token)
                    successes += 1
                except Exception as exc:
                    log.warning("TokenPool: prefill mint failed: %s", exc)
        elapsed = time.monotonic() - t0
        log.info(
            "TokenPool: prefilled %d/%d tokens (%.1fs, mint=%s)",
            successes, self._pool_size, elapsed, self._minter_url,
        )

    def _replenish(self):
        try:
            token = self._minter.mint(self._POOL_AMOUNT)
            with self._lock:
                self._queue.append(token)
        except Exception as exc:
            log.warning("TokenPool: replenish failed: %s", exc)

    # -- public API (same interface as CashuMint / CdkCliWallet) --

    def mint(self, amount: int = 4, legacy: bool = True, timeout: int = 120, retries: int = 2) -> str:
        if amount == self._POOL_AMOUNT:
            with self._lock:
                if self._queue:
                    token = self._queue.popleft()
                    if len(self._queue) < 3:
                        threading.Thread(target=self._replenish, daemon=True).start()
                    return token
        return self._minter.mint(amount, legacy=legacy, timeout=timeout, retries=retries)

    def mint_fresh(self, amount: int = 4, legacy: bool = True, timeout: int = 120, retries: int = 2) -> str:
        # #247: bypass the pool for payment-critical tokens. A venue reset
        # re-inits the fakewallet mint and rotates its keyset generation —
        # pooled tokens minted before the rotation then verify as CU109
        # (outdated mint key) and the payment silently never lands.
        return self._minter.mint(amount, legacy=legacy, timeout=timeout, retries=retries)

    def drain(self) -> int:
        """Drop queued tokens (e.g. after a venue reset rotated the mint)."""
        with self._lock:
            n = len(self._queue)
            self._queue.clear()
        if n:
            log.info("TokenPool: drained %d pooled tokens", n)
        return n

    @property
    def mint_url(self) -> str:
        return self._minter_url

    def ensure_mint_available(self, timeout: int = 15):
        return self._minter.ensure_mint_available(timeout=timeout)

    def warmup(self, timeout: int = 60):
        return self._minter.warmup(timeout=timeout)

    def is_available(self) -> bool:
        return self._minter.is_available()

    def mint_from_wrong_mint(self, amount: int = 4, timeout: int = 90) -> str:
        return self._minter.mint_from_wrong_mint(amount=amount, timeout=timeout)

    @staticmethod
    def synthetic_wrong_mint_token() -> str:
        return CashuMint.synthetic_wrong_mint_token()
