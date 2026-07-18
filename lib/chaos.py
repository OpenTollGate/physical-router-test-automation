"""Chaos engineering controller for cloud-lab mint connectivity.

Wraps the Toxiproxy REST API to let tests toggle mint reachability
without SSH or iptables. Toxiproxy sits between the router and the
CDK V2 mint: router → port 8383 (Toxiproxy) → port 18383 (actual mint).

Usage in tests::

    from lib.chaos import MintChaosController

    chaos = MintChaosController()  # connects to Toxiproxy on localhost:8474
    chaos.go_offline(30)           # block mint for 30 seconds
    # ... monitor logs backoff behavior ...
    chaos.reset()                  # restore connectivity
"""

from __future__ import annotations

import os
import time
import logging

import requests

log = logging.getLogger(__name__)

TOXIPROXY_HOST = os.environ.get("TOXIPROXY_HOST", "http://127.0.0.1")
TOXIPROXY_MGMT_PORT = int(os.environ.get("TOXIPROXY_MGMT_PORT", "8474"))
CDK_PROXY_NAME = "cdk_mint"


class MintChaosController:
    def __init__(self, base_url: str | None = None, proxy_name: str = CDK_PROXY_NAME):
        self.base_url = base_url or f"{TOXIPROXY_HOST}:{TOXIPROXY_MGMT_PORT}"
        self.proxy_name = proxy_name

    def go_offline(self, duration_s: int = 30) -> None:
        """Block all traffic to the mint for duration_s seconds, then restore."""
        self._disable()
        if duration_s > 0:
            log.info("Mint offline for %ds", duration_s)
            time.sleep(duration_s)
            self._enable()

    def block_until_reset(self) -> None:
        """Block mint traffic indefinitely (until reset() or come_online() is called)."""
        self._disable()

    def come_online(self) -> None:
        """Restore mint connectivity immediately."""
        self._enable()

    def add_latency(self, ms: int, jitter: int = 0, direction: str = "downstream") -> None:
        toxic_name = f"latency_{direction}"
        self._delete_toxic(toxic_name)
        self._create_toxic({
            "name": toxic_name,
            "type": "latency",
            "stream": direction,
            "attributes": {"latency": ms, "jitter": jitter},
        })

    def add_packet_loss(self, percent: float, direction: str = "downstream") -> None:
        toxic_name = f"loss_{direction}"
        self._delete_toxic(toxic_name)
        self._create_toxic({
            "name": toxic_name,
            "type": "loss",
            "stream": direction,
            "attributes": {"probability": percent / 100.0},
        })

    def reset(self) -> None:
        """Remove all toxics and re-enable the proxy."""
        self._enable()
        toxics = self._get_toxics()
        for toxic in toxics:
            self._delete_toxic(toxic["name"])

    def is_reachable(self) -> bool:
        try:
            resp = requests.get(f"{self.base_url}/proxies/{self.proxy_name}", timeout=3)
            data = resp.json()
            return data.get("enabled", True)
        except Exception:
            return False

    def _disable(self) -> None:
        resp = requests.post(
            f"{self.base_url}/proxies/{self.proxy_name}",
            json={"enabled": False},
            timeout=5,
        )
        resp.raise_for_status()

    def _enable(self) -> None:
        resp = requests.post(
            f"{self.base_url}/proxies/{self.proxy_name}",
            json={"enabled": True},
            timeout=5,
        )
        resp.raise_for_status()

    def _create_toxic(self, toxic: dict) -> None:
        resp = requests.post(
            f"{self.base_url}/proxies/{self.proxy_name}/toxics",
            json=toxic,
            timeout=5,
        )
        resp.raise_for_status()

    def _delete_toxic(self, name: str) -> None:
        requests.delete(
            f"{self.base_url}/proxies/{self.proxy_name}/toxics/{name}",
            timeout=5,
        )

    def _get_toxics(self) -> list[dict]:
        try:
            resp = requests.get(
                f"{self.base_url}/proxies/{self.proxy_name}/toxics",
                timeout=5,
            )
            return resp.json() if resp.status_code == 200 else []
        except Exception:
            return []
