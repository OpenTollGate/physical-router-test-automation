import json
import subprocess
import os
import base64
import time
import re

from lib.constants import TEST_MINT_URL


class CashuMint:
    def __init__(self, venv_path: str = "/tmp/cashu-venv", mint_url: str = TEST_MINT_URL):
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

    def _find_latest_quote_id(self, url=None):
        mint_url = url or self.mint_url
        r = subprocess.run(
            [self._cashu, "-h", mint_url, "-t", "invoices"],
            capture_output=True, text=True, timeout=15, env=self._env(),
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
            capture_output=True, text=True, timeout=15, env=self._env(),
        )
        return r.stdout.count("Mint quote")

    def _wait_and_claim(self, quote_id, amount, timeout=90):
        for _ in range(timeout // 3):
            time.sleep(3)
            try:
                r = subprocess.run(
                    [self._cashu, "-h", self.mint_url, "-t",
                     "invoice", str(amount), "--id", quote_id],
                    capture_output=True, text=True, timeout=15, env=self._env(),
                )
                if "Invoice paid" in r.stdout:
                    return True
            except Exception:
                pass
        return False

    def _ensure_balance(self, amount):
        r = subprocess.run(
            [self._cashu, "-h", self.mint_url, "-t", "balance"],
            capture_output=True, text=True, timeout=10, env=self._env(),
        )
        match = re.search(r"Balance:\s*(\d+)", r.stdout)
        if match and int(match.group(1)) >= amount:
            return

        before = self._count_invoices()

        proc = subprocess.Popen(
            [self._cashu, "-h", self.mint_url, "-t", "-y", "invoice", str(amount)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self._env(),
        )
        time.sleep(4)
        proc.kill()
        proc.communicate()

        for _ in range(5):
            after = self._count_invoices()
            if after > before:
                break
            time.sleep(2)

        quote_id = self._find_latest_quote_id()
        if not quote_id:
            raise RuntimeError("No quote found after invoice creation")

        if not self._wait_and_claim(quote_id, amount):
            raise RuntimeError(f"Mint claim failed for quote {quote_id}")

    def mint(self, amount: int = 4, legacy: bool = True) -> str:
        if not self.is_available():
            raise RuntimeError(f"cashu venv not found at {self.venv_path}")

        env = self._env()

        self._ensure_balance(amount)

        cmd = [self._cashu, "-h", self.mint_url, "-t", "-y", "send", str(amount)]
        if legacy:
            cmd.append("--legacy")

        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60, env=env)
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
