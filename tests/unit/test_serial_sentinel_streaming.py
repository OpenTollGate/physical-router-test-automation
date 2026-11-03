"""The serial sentinel reader must consume lines, not re-parse the whole buffer.

``RouterSerial.exec_command`` wraps a command in start/end markers and reads
until the end marker appears. The original implementation appended each read to
``buf`` and then iterated ``buf.split("\\n")`` **from the beginning every time**,
which has two consequences:

* every line seen so far is re-processed on each read, so output accumulates
  duplicated lines;
* the last element of ``split`` is an *incomplete* line unless the read happened
  to end on a newline — and it was treated as complete.

Observed on a real GL-MT3000: a compound command was truncated mid-line
(``o '#SEC:board'; cat /tmp/sysinfo/model...``), and a one-line answer came back
wrapped in the wrapper's own sentinel text. The board's real answer to
``cat /tmp/sysinfo/model`` is exactly one line, so the truncation was the
difference between "identity verified" and "identity mismatch".

These tests drive the reader with a fake serial port that hands out data in
awkward chunks (mid-line splits, one byte at a time, marker split across reads).
They are written to fail against the buffer-resplitting implementation.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("router_serial", ROOT / "scripts/router-serial.py")
router_serial = importlib.util.module_from_spec(spec)
spec.loader.exec_module(router_serial)


class FakeSerial:
    """A serial port that yields pre-scripted chunks, then goes quiet."""

    def __init__(self, chunks: list[bytes]):
        self.chunks = list(chunks)
        self.written: list[str] = []
        self.is_open = True

    def read(self, size: int = 1) -> bytes:  # noqa: ARG002 - size ignored on purpose
        if self.chunks:
            return self.chunks.pop(0)
        return b""

    def write(self, data: bytes) -> None:
        self.written.append(data.decode("utf-8", errors="replace"))

    def flush(self) -> None:
        pass

    def reset_input_buffer(self) -> None:
        pass

    def close(self) -> None:
        self.is_open = False


def _session(chunks: list[bytes]):
    conn = router_serial.SerialConnection.__new__(router_serial.SerialConnection)
    conn.ser = FakeSerial(chunks)
    # The fixture scripts raw command output only; login probing would consume
    # those bytes and make the test measure the login path instead.
    conn._ensure_logged_in = lambda: None
    return conn


START = router_serial.START_MARKER
END = router_serial.END_MARKER_PREFIX


def _framed(output: str, rc: int = 0) -> bytes:
    """The byte stream a board produces for one wrapped command."""
    return f"{START}\r\n{output}\r\n{END}{rc}\r\n".encode()


def test_a_single_line_answer_is_not_duplicated():
    """Re-parsing the whole buffer duplicates every line already seen."""
    conn = _session([_framed("GL.iNet GL-MT3000")])
    out, rc = conn.exec_command("cat /tmp/sysinfo/model", timeout=2)
    assert rc == 0
    assert out == "GL.iNet GL-MT3000", f"expected one clean line, got {out!r}"


def test_output_split_across_reads_is_reassembled():
    """A line arriving in two chunks must be joined, not emitted twice."""
    payload = _framed("GL.iNet GL-MT3000")
    mid = len(payload) // 2
    conn = _session([payload[:mid], payload[mid:]])
    out, rc = conn.exec_command("cat /tmp/sysinfo/model", timeout=2)
    assert rc == 0
    assert out == "GL.iNet GL-MT3000", f"chunked read corrupted the answer: {out!r}"


def test_byte_at_a_time_read_does_not_lose_or_duplicate():
    payload = _framed("alpha\nbeta")
    conn = _session([payload[i : i + 1] for i in range(len(payload))])
    out, rc = conn.exec_command("printf 'alpha\\nbeta'", timeout=3)
    assert rc == 0
    assert out == "alpha\nbeta", f"byte-wise read produced {out!r}"


def test_marker_split_across_reads_is_recognised():
    """The end marker arriving in pieces must still end the command."""
    payload = _framed("ok", rc=7)
    idx = payload.index(END.encode()) + 4
    conn = _session([payload[:idx], payload[idx:]])
    out, rc = conn.exec_command("false", timeout=2)
    assert out == "ok", f"got {out!r}"
    assert rc == 7, f"exit code from a split marker was {rc}"


def test_a_trailing_partial_line_is_not_treated_as_output():
    """A chunk ending mid-line is incomplete; emitting it is the truncation bug."""
    # First read: start marker + half a line. Second read: the rest + end marker.
    conn = _session(
        [
            f"{START}\r\nGL.iNet GL-MT".encode(),
            b"3000\r\n" + f"{END}0\r\n".encode(),
        ]
    )
    out, rc = conn.exec_command("cat /tmp/sysinfo/model", timeout=2)
    assert rc == 0
    assert out == "GL.iNet GL-MT3000", f"partial line leaked into output: {out!r}"


def test_multiple_lines_preserve_order_and_count():
    conn = _session([_framed("one\ntwo\nthree")])
    out, rc = conn.exec_command("print-three", timeout=2)
    assert rc == 0
    assert out.split("\n") == ["one", "two", "three"]


def test_wait_pattern_sees_a_line_delivered_in_two_chunks():
    """Same defect class in wait_pattern: a split line must still match."""
    conn = _session([b"boot: GL.iNet GL-MT", b"3000 ready\n"])
    line, _elapsed = conn.wait_pattern(r"GL-MT3000", timeout=2)
    assert line is not None, "a line split across reads was never matched"


def test_exec_command_returns_minus_one_when_the_sentinel_never_arrives():
    """A silent board is a timeout, not an empty success."""
    conn = _session([f"{START}\r\n{END}0\r\n".encode()[:6]])
    out, rc = conn.exec_command("cat /tmp/sysinfo/model", timeout=0.5)
    assert rc == -1, f"missing sentinel reported rc={rc}, which reads as success"
    assert out == ""
