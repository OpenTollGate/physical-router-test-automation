#!/usr/bin/env python3
"""Counting HTTP proxy for the Spilman lane's "zero mint calls" assertion.

WHY A PROXY AND NOT A LOG SCRAPE
--------------------------------
The goal's central claim is *"between open and settle the payer makes ZERO calls
to the mint"*. That is a claim about **network behaviour**, so it has to be
MEASURED, not inferred. Reading the payer's log and counting the lines it chose
to print is an inference: a call the payer forgot to log is invisible, and the
assertion could never fail.

So the lane puts this proxy in front of the test mint and points BOTH the payer
driver and the router module at the proxy's address. Every HTTP request the
proxy receives is counted. The count is then a fact about bytes on the wire.

The proxy is *armed* only for the window the lane wants to measure (open is
excluded, because open legitimately spends one funding swap at the mint):

    /__lane/reset   -> zero the counter and forget records
    /__lane/arm     -> start counting
    /__lane/disarm  -> stop counting
    /__lane/count   -> {"count": N, "armed": bool}
    /__lane/records -> the (method, path) pairs seen while armed

Control paths are intercepted and never forwarded to the mint.

THREADED, NOT ASYNCIO
---------------------
The lane already owns the serial port in a blocking loop; a background thread
that the lane can query synchronously is simpler and has no event-loop coupling.

Standalone self-test (no bench needed):

    python3 spilman/mint_proxy.py --selftest
"""
from __future__ import annotations

import argparse
import http.client
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

CONTROL_PREFIX = "/__lane/"


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    # Silence the default stderr spam; the lane reports failures itself.
    def log_message(self, *args):  # noqa: D102
        pass

    def _control(self) -> bool:
        proxy: "CountingMintProxy" = self.server.proxy  # type: ignore[attr-defined]
        tail = self.path[len(CONTROL_PREFIX):].split("?")[0]
        if tail == "reset":
            proxy.reset()
            payload = {"ok": True}
        elif tail == "arm":
            proxy.arm()
            payload = {"ok": True, "armed": True}
        elif tail == "disarm":
            proxy.disarm()
            payload = {"ok": True, "armed": False}
        elif tail == "count":
            payload = {"count": proxy.count(), "armed": proxy.armed}
        elif tail == "records":
            payload = {"count": proxy.count(), "armed": proxy.armed,
                       "records": proxy.records()}
        else:
            payload = {"ok": False, "error": f"unknown control path {tail!r}"}
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        return True

    def _forward(self, method: str) -> None:
        if self.path.startswith(CONTROL_PREFIX):
            self._control()
            return
        proxy: "CountingMintProxy" = self.server.proxy  # type: ignore[attr-defined]
        proxy.note(method, self.path)
        length = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(length) if length else None
        headers = {k: v for k, v in self.headers.items()
                   if k.lower() not in ("host", "content-length", "connection")}
        try:
            conn = http.client.HTTPConnection(proxy.up_host, proxy.up_port,
                                              timeout=proxy.timeout)
            conn.request(method, self.path, body=body, headers=headers)
            resp = conn.getresponse()
            data = resp.read()
            self.send_response(resp.status)
            for k, v in resp.getheaders():
                if k.lower() in ("transfer-encoding", "connection", "content-length"):
                    continue
                self.send_header(k, v)
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            conn.close()
        except Exception as exc:  # noqa: BLE001 — surface as 502, never crash the lane
            msg = json.dumps({"error": f"proxy upstream failure: {exc}"}).encode()
            self.send_response(502)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(msg)))
            self.end_headers()
            self.wfile.write(msg)

    def do_GET(self):  # noqa: N802
        self._forward("GET")

    def do_POST(self):  # noqa: N802
        self._forward("POST")

    def do_HEAD(self):  # noqa: N802
        self._forward("HEAD")


class CountingMintProxy:
    """A threaded HTTP proxy that counts the requests it forwards.

    `upstream` is the real mint URL (e.g. http://192.168.2.18:3338). The proxy
    binds `bind_host:bind_port`; the router and the payer driver are pointed here.
    """

    def __init__(self, upstream: str, bind_host: str = "0.0.0.0",
                 bind_port: int = 3339, timeout: float = 15.0):
        parts = urlsplit(upstream)
        if parts.scheme not in ("http", ""):
            raise ValueError(f"only http upstreams are supported, got {upstream!r}")
        self.up_host = parts.hostname or "127.0.0.1"
        self.up_port = parts.port or 80
        self.bind_host, self.bind_port = bind_host, bind_port
        self.timeout = timeout
        self._lock = threading.Lock()
        self._armed = False
        self._count = 0
        self._records: list[tuple[float, str, str]] = []
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    # ---- lifecycle --------------------------------------------------------
    def start(self) -> "CountingMintProxy":
        self._httpd = ThreadingHTTPServer((self.bind_host, self.bind_port), _Handler)
        self._httpd.daemon_threads = True
        self._httpd.proxy = self  # type: ignore[attr-defined]
        self._thread = threading.Thread(target=self._httpd.serve_forever,
                                        name="mint-proxy", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    # ---- counting ---------------------------------------------------------
    def reset(self) -> None:
        with self._lock:
            self._count = 0
            self._records.clear()

    def arm(self) -> None:
        with self._lock:
            self._armed = True

    def disarm(self) -> None:
        with self._lock:
            self._armed = False

    @property
    def armed(self) -> bool:
        with self._lock:
            return self._armed

    def note(self, method: str, path: str) -> None:
        with self._lock:
            if self._armed:
                self._count += 1
                self._records.append((time.monotonic(), method, path))

    def count(self) -> int:
        with self._lock:
            return self._count

    def records(self) -> list[list[str]]:
        with self._lock:
            return [[f"{m} {p}"] for _, m, p in self._records]

    # ---- control via HTTP (used when the lane drives the proxy remotely) ---
    def base_url(self) -> str:
        host = self.bind_host if self.bind_host != "0.0.0.0" else "127.0.0.1"
        return f"http://{host}:{self.bind_port}"

    def upstream_url(self) -> str:
        return f"http://{self.up_host}:{self.up_port}"


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _selftest() -> int:
    """Prove the proxy counts, forwards, and gates on arm/disarm — for real."""
    import urllib.request

    hits: list[str] = []

    class _Upstream(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):  # noqa: N802
            hits.append(self.path)
            body = b'{"ok":true}'
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):  # noqa: N802
            n = int(self.headers.get("content-length") or 0)
            self.rfile.read(n)
            hits.append("POST " + self.path)
            body = b'{"posted":true}'
            self.send_response(200)
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    up_port = _free_port()
    up = ThreadingHTTPServer(("127.0.0.1", up_port), _Upstream)
    up.daemon_threads = True
    threading.Thread(target=up.serve_forever, daemon=True).start()

    proxy_port = _free_port()
    proxy = CountingMintProxy(f"http://127.0.0.1:{up_port}",
                              bind_host="127.0.0.1", bind_port=proxy_port).start()
    ok = True
    try:
        base = f"http://127.0.0.1:{proxy_port}"

        def get(path):
            with urllib.request.urlopen(base + path, timeout=5) as r:
                return r.read()

        # 1. control endpoints are intercepted, never forwarded
        get(CONTROL_PREFIX + "reset")
        assert hits == [], f"control path leaked upstream: {hits}"

        # 2. a request while DISARMED is forwarded but NOT counted
        get("/v1/info")
        assert hits == ["/v1/info"], hits
        assert proxy.count() == 0, f"disarmed request counted: {proxy.count()}"
        print("selftest: disarmed request forwarded, count stays 0  OK")

        # 3. arm, then a request is forwarded AND counted
        get(CONTROL_PREFIX + "arm")
        get("/v1/swap")
        assert proxy.count() == 1, f"armed request not counted: {proxy.count()}"
        print("selftest: armed request counted = 1  OK")

        # 4. POST with a body round-trips and is counted
        req = urllib.request.Request(base + "/v1/swap", data=b'{"a":1}',
                                     headers={"content-type": "application/json"},
                                     method="POST")
        with urllib.request.urlopen(req, timeout=5) as r:
            assert json.loads(r.read())["posted"] is True
        assert proxy.count() == 2, proxy.count()
        assert hits[-1] == "POST /v1/swap", hits
        print("selftest: armed POST counted and round-tripped  OK")

        # 5. disarm stops counting; reset zeroes
        get(CONTROL_PREFIX + "disarm")
        get("/v1/info")
        assert proxy.count() == 2, proxy.count()
        get(CONTROL_PREFIX + "reset")
        assert proxy.count() == 0 and proxy.records() == []
        print("selftest: disarm stops counting, reset zeroes  OK")

        # 6. the count endpoint reflects state over HTTP
        payload = json.loads(get(CONTROL_PREFIX + "count"))
        assert payload == {"count": 0, "armed": False}, payload
        print("selftest: /__lane/count reflects state  OK")
    except AssertionError as exc:
        ok = False
        print(f"selftest FAIL: {exc}")
    finally:
        proxy.stop()
        up.shutdown()
        up.server_close()
    print("selftest:", "ALL OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--upstream")
    ap.add_argument("--bind-host", default="0.0.0.0")
    ap.add_argument("--bind-port", type=int, default=3339)
    args = ap.parse_args()
    if args.selftest:
        raise SystemExit(_selftest())
    if not args.upstream:
        ap.error("--upstream is required unless --selftest")
    p = CountingMintProxy(args.upstream, args.bind_host, args.bind_port).start()
    print(f"counting proxy on {p.base_url()} -> {p.upstream_url()}")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        p.stop()
