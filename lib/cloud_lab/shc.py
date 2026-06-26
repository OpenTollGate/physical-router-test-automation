"""Sovereign Hybrid Compute (SHC) API client for cloud lab VM lifecycle.

Updated to v2 API with confirmation flow, idempotency, and all fixes
discovered during the shc-toolkit development session.
"""

import json
import os
import time
import uuid
import logging
from typing import Any

import requests

log = logging.getLogger(__name__)

BASE_URL = "https://blesta.sovereignhybridcompute.com/user-api/v2"


class SHCClient:
    """SHC User API v2 client with auto-confirmation."""

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("SHC_API_KEY", "")
        if not self.api_key:
            raise ValueError("SHC_API_KEY not set")
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {self.api_key}"

    def _request(self, method: str, path: str, json_body=None, headers=None) -> Any:
        url = f"{BASE_URL}{path}"
        h = {}
        if json_body is not None:
            h["Content-Type"] = "application/json"
        if headers:
            h.update(headers)
        resp = self.session.request(method, url, json=json_body, headers=h, timeout=60)
        raw = resp.text
        idx = raw.find("{")
        if idx > 0:
            raw = raw[idx:]
        body = json.loads(raw) if raw.strip() else {}
        if not resp.ok:
            err = body.get("error", {})
            exc = SHCError(
                err.get("code", "unknown"),
                err.get("message", resp.text),
                err.get("request_id"),
                err.get("details"),
            )
            conf = body.get("confirmation", {})
            if conf:
                exc.confirmation_id = conf.get("structuredContent", {}).get("confirmation_id")
            raise exc
        return body.get("data", body)

    def _confirmed_request(self, method: str, path: str, json_body=None, headers=None) -> Any:
        try:
            return self._request(method, path, json_body, headers)
        except SHCError as e:
            if e.code != "confirmation_required" or not e.confirmation_id:
                raise
            return self._request(method, path, json_body, {
                **(headers or {}),
                "X-User-Api-Confirm": e.confirmation_id,
            })

    # ── Account ──────────────────────────────────────────────

    def get_balance(self) -> float:
        data = self._request("GET", "/account/balance")
        credits = data.get("credit", [])
        for c in credits:
            if c.get("currency") == "USD":
                return float(c.get("amount", 0))
        return 0.0

    # ── Ordering ─────────────────────────────────────────────

    def submit_order(self, hostname: str, package_id: int = 81, pricing_id: int = 245) -> dict:
        idem = f"order-{uuid.uuid4().hex[:24]}"
        return self._confirmed_request("POST", "/ordering/submit", {
            "hostname": hostname,
            "package_id": package_id,
            "pricing_id": pricing_id,
            "order_form_id": 11,
        }, {"Idempotency-Key": idem})

    def get_catalog(self) -> list[dict]:
        return self._request("GET", "/ordering/catalog").get("items", [])

    # ── VM Lifecycle ─────────────────────────────────────────

    def list_vms(self) -> list[dict]:
        return self._request("GET", "/vm").get("items", [])

    def get_vm(self, service_id: int) -> dict:
        return self._request("GET", f"/vm/{service_id}")

    def get_vm_summary(self, service_id: int) -> dict:
        return self._request("GET", f"/vm/{service_id}/summary")

    def cancel_vm(self, service_id: int, immediate: bool = True) -> dict:
        body = {"immediate": True} if immediate else {}
        return self._confirmed_request("POST", f"/vm/{service_id}/cancel", body)

    # ── SSH Keys ─────────────────────────────────────────────

    def apply_ssh_key_live(self, service_id: int, ssh_key: str) -> dict:
        return self._confirmed_request("POST", f"/vm/{service_id}/ssh-keys/apply-live", {
            "ssh_key": ssh_key,
        })

    # ── Wait helpers ─────────────────────────────────────────

    def wait_for_provisioning(self, service_id: int, timeout: int = 300) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                vm = self.get_vm(service_id)
                state = vm.get("provisioning_state", "unknown")
                if state == "ready":
                    return vm
                if state in ("failed", "error"):
                    raise SHCError("provisioning_failed", f"VM failed: {vm}")
            except SHCError:
                raise
            except Exception:
                pass
            time.sleep(5)
        raise SHCError("timeout", f"VM {service_id} not ready after {timeout}s")


class SHCError(Exception):
    def __init__(self, code: str, message: str, request_id: str | None = None, details: Any = None):
        self.code = code
        self.request_id = request_id
        self.details = details
        self.confirmation_id: str | None = None
        super().__init__(f"[{code}] {message}")
