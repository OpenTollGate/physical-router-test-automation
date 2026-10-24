"""api_status shim fallback regression tests (nr7101 gate bug).

The session gate read HTTP 0 in the fixture while standalone curl got 200:
nr7101 ships an nc-based /usr/bin/curl shim without -w support, so the
ssh-path probe returned non-digit output. api_status must fall back to
busybox wget -S status-line parsing.
"""
from unittest.mock import patch

from lib.router import Router


class _StubRouter(Router):
    """Router with ssh() stubbed to scripted outputs."""

    def __init__(self, ssh_outputs):  # noqa: D107 - test stub
        # bypass Router.__init__ (no device needed); set only the fields
        # api_status touches.
        self.jump_host = "stub"
        self.host = "192.0.2.10"
        self._ssh_outputs = list(ssh_outputs)

    def ssh(self, cmd, timeout=30):
        return self._ssh_outputs.pop(0)

    def backend_url(self, path="/"):
        return f"http://127.0.0.1:2121{path}"


def test_real_curl_still_wins():
    r = _StubRouter(["200"])
    assert r.api_status("/session") == 200


def test_curl_shim_falls_back_to_wget():
    # nc shim prints junk for -w; wget -S first line carries the status.
    r = _StubRouter(["", "  HTTP/1.1 200 OK"])
    assert r.api_status("/session") == 200


def test_wget_404_is_reported_not_zero():
    r = _StubRouter(["", "HTTP/1.1 404 Not Found"])
    assert r.api_status("/nope") == 404


def test_all_probes_dead_is_zero():
    r = _StubRouter(["", ""])
    assert r.api_status("/session") == 0


def test_ssh_exception_falls_through_to_wget():
    class _Boom(_StubRouter):
        def ssh(self, cmd, timeout=30):
            if "curl" in cmd:
                raise RuntimeError("ssh died")
            return "  HTTP/1.1 200 OK"

    assert _Boom([]).api_status("/session") == 200
