#!/usr/bin/env python3
"""Unit tests for the lane's console-output parsing.

WHY THIS FILE EXISTS
--------------------
Two bugs in this lane's first protocol run made GREEN code report RED, and both
were parsing bugs, not device bugs:

  1. `last_line()` returned the trailing shell PROMPT, so the sha256 comparison
     compared a hex digest against `root@tollgate-0195:/tmp/x#`.
  2. `if ':2123 ' in out and 'NOLISTEN' not in out` was always false, because
     the console ECHOES the command — so the grep pattern and the `|| echo
     NOLISTEN` fallback both appear in the transcript even on success.

The fixtures below are REAL captured transcripts from tollgate-0195, prompt
lines and line-wrapping included, because a parser tested only against tidy
synthetic input would have passed while the lane failed.

Run: python3 spilman/test_lane_parse.py
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

LANE = Path(__file__).resolve().parent / "spilman_router_lane.py"


def load_lane():
    spec = importlib.util.spec_from_file_location("lane_mod", LANE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


lane = load_lane()

# --- real transcripts (CRs, prompt lines, ANSI, echoed commands) -------------

RAW_SHA = (
    "sha256sum /tmp/spilman-lane-3470542/tollgate-module-basic-rust\r\n"
    "sha256sum /tmp/spilman-lane-347054\r\r\n"
    "2/tollgate-module-basic-rust\r\n"
    "a6962677bf3798036f6da7a592bd11d17ffb702bff13822f24b53143593a4547  "
    "/tmp/spilman-lane-3470542/tollgate-module-basic-rust\r\n"
    "root@tollgate-0195:/tmp/spilman-lane-3470542# \r\n"
)

RAW_NETSTAT = (
    "netstat -ltn 2>/dev/null | grep -E ':2123|:2121' || echo NOLISTEN\r\n"
    "netstat -ltn 2>/dev/null | grep -E\r\r\n"
    " ':2123|:2121' || echo NOLISTEN\r\n"
    "tcp        0      0 127.0.0.1:2123          0.0.0.0:*               LISTEN      \r\n"
    "tcp        0      0 :::2121                 :::*                    LISTEN      \r\n"
    "root@tollgate-0195:/tmp/spilman-lane-3470542# \r\n"
)

RAW_PS = (
    "ps w | grep -F spilman-lane | grep -v grep || echo NOPROC\r\n"
    "ps w | grep -F spilman-lane | grep\r\r\n"
    " -v grep || echo NOPROC\r\n"
    "31501 root     18688 S    /tmp/spilman-lane-3470542/tollgate-module-basic-rust\r\n"
    "root@tollgate-0195:/tmp/spilman-lane-3470542# \r\n"
)

RAW_ANSI = (
    "-rw-r--r--    1 root     root           121 Oct 22 18:54 \x1b[0;0mpay.json\x1b[m\r\n"
    "root@tollgate-0195:/tmp/spilman-cfg# \r\n"
)


def t_clean_drops_prompts_and_echo():
    lines = lane.clean(RAW_SHA)
    ok = not any(l.startswith("root@tollgate-0195") for l in lines)
    print(f"{'PASS' if ok else 'FAIL'}  clean() drops prompt lines")
    return ok


def t_clean_strips_ansi():
    lines = lane.clean(RAW_ANSI)
    ok = any("pay.json" in l for l in lines) and not any("\x1b" in l for l in lines)
    print(f"{'PASS' if ok else 'FAIL'}  clean() strips ANSI escapes")
    return ok


def t_last_line_trap_is_real():
    """Document the old bug so nobody reintroduces it."""
    naive = [l.strip() for l in RAW_SHA.splitlines() if l.strip()][-1]
    ok = naive.startswith("root@")
    print(f"{'PASS' if ok else 'FAIL'}  the old last_line() trap is real "
          f"(returned {naive[:40]!r})")
    return ok


def t_sentinel_beats_echo():
    """The echoed command must never be mistaken for a result.

    The console echoes `echo "<<M<nonce>:$V>>"`. That echoed line MATCHES the
    sentinel pattern with the literal value `$V`. A naive extractor returns
    `$V` — this test failed on exactly that before the `$`-rejection was added.
    """
    nonce = "abcd1234"
    digest = "a6962677bf3798036f6da7a592bd11d17ffb702bff13822f24b53143593a4547"
    transcript = (
        f'V=$(sha256sum /tmp/x | cut -d\\  -f1); echo "<<M{nonce}:$V>>"\r\n'
        f"{digest}\r\n"
        f"<<M{nonce}:{digest}>>\r\n"
        "root@tollgate-0195:/tmp# \r\n"
    )
    got = lane.extract_marked(transcript, nonce)
    ok = got == digest
    print(f"{'PASS' if ok else 'FAIL'}  sentinel extraction ignores the echoed command "
          f"(got {got!r})")
    return ok


def t_sentinel_absent_is_none():
    """No sentinel must mean 'failed', never 'empty string = success'."""
    ok = lane.extract_marked("some unrelated output\r\n", "abcd1234") is None
    print(f"{'PASS' if ok else 'FAIL'}  missing sentinel yields None")
    return ok


def t_sentinel_wrong_nonce_ignored():
    """A stale sentinel from a previous command must not answer this one."""
    stale = "<<Mdeadbeef:200>>\r\n"
    ok = lane.extract_marked(stale, "abcd1234") is None
    print(f"{'PASS' if ok else 'FAIL'}  a sentinel with the wrong nonce is ignored")
    return ok


def t_ps_parse_skips_echoed_grep():
    """The echoed grep line starts with a digit-ish token and holds the marker."""
    fake = ("ps w | grep -F /tmp/spilman-lane-1 | grep -v grep\r\n"
            "31501 root     18688 S    /tmp/spilman-lane-1/tollgate-module-basic-rust\r\n"
            "root@tollgate-0195:/tmp# \r\n")
    import re
    pids = []
    for line in lane.clean(fake):
        m = re.match(r"^\s*(\d+)\s", line)
        if m and "/tmp/spilman-lane-1" in line and "grep" not in line:
            pids.append(int(m.group(1)))
    ok = pids == [31501]
    print(f"{'PASS' if ok else 'FAIL'}  ps parse finds our module pid only (got {pids})")
    return ok


def t_wait_listen_sentinel_shape():
    """wait_listen asks a yes/no question; the answer must be exactly 'Y'."""
    nonce = "feedface"
    transcript = (f"V=$(netstat -ltn 2>/dev/null | grep -qF ':2123 ' && echo Y); "
                  f'echo "<<M{nonce}:$V>>"\r\n'
                  f"Y\r\n<<M{nonce}:Y>>\r\nroot@tollgate-0195:/tmp# \r\n")
    got = lane.extract_marked(transcript, nonce)
    ok = got == "Y"
    print(f"{'PASS' if ok else 'FAIL'}  wait_listen sentinel reads 'Y' (got {got!r})")
    return ok


def t_stop_own_kills_only_recorded_pid():
    """Task C, at unit level: stop_own is pid-based, never name-based."""
    alive = {4242: True, 4343: True}
    killed = []

    def fake_kill(pid, sig):
        killed.append(pid)
        alive[pid] = False

    rc = lane.stop_own("/nonexistent.pid", kill=fake_kill,
                       is_alive=lambda p: alive.get(p, False),
                       read=lambda p: "4242")
    ok = rc == 4242 and killed == [4242] and alive[4343] is True
    print(f"{'PASS' if ok else 'FAIL'}  stop_own kills only its recorded pid "
          f"(killed={killed}, decoy_alive={alive[4343]})")
    return ok


def t_stop_own_ignores_stale_pidfile():
    """A stale pidfile must not kill an unrelated process that reused the pid."""
    calls = []
    rc = lane.stop_own("/nonexistent.pid", kill=lambda p, s: calls.append(p),
                       is_alive=lambda p: False, read=lambda p: "4242")
    ok = rc is None and calls == []
    print(f"{'PASS' if ok else 'FAIL'}  stop_own ignores a stale pidfile (calls={calls})")
    return ok


def t_reserved_ports():
    ok = "2121" in lane.RESERVED_PORTS and "2122" in lane.RESERVED_PORTS
    print(f"{'PASS' if ok else 'FAIL'}  :2121/:2122 are reserved (private-port rule)")
    return ok


TESTS = [
    t_clean_drops_prompts_and_echo,
    t_clean_strips_ansi,
    t_last_line_trap_is_real,
    t_sentinel_beats_echo,
    t_sentinel_absent_is_none,
    t_sentinel_wrong_nonce_ignored,
    t_ps_parse_skips_echoed_grep,
    t_wait_listen_sentinel_shape,
    t_stop_own_kills_only_recorded_pid,
    t_stop_own_ignores_stale_pidfile,
    t_reserved_ports,
]

if __name__ == "__main__":
    results = [fn() for fn in TESTS]
    print()
    ok = all(results)
    print("RESULT:", "parser behaves correctly" if ok
          else f"PARSER DEFECT ({results.count(False)} failed)")
    sys.exit(0 if ok else 1)
