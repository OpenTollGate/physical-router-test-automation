#!/usr/bin/env python3
"""Publish a film run directory's artifacts to a Blossom server.

Uploads the files a lane produced (the narrated mp4, the reel, captions,
stills) and prints content-addressed URLs, so any test's film can be shared.
Uses ``nak blossom upload`` (BUD-02) with the machine's default key, or an
explicit ``--sec``.

Usage:
  python3 scripts/publish-film.py --run results/films/<id> \
      --server https://blossom1.orangesync.tech
  python3 scripts/publish-film.py --run out/ --glob '*.mp4' --glob '*.vtt'
"""

from __future__ import annotations

import argparse
import glob
import json
import pathlib
import subprocess
import sys

DEFAULT_GLOBS = ["*.mp4", "*.webm", "*.vtt", "index.html", "film-online.html", "*.png"]
DEFAULT_SERVERS = [
    "https://blossom1.orangesync.tech",
    "https://blossom2.orangesync.tech",
]


def upload(path: pathlib.Path, server: str, sec: str | None) -> str:
    cmd = ["nak", "blossom", "upload", "--server", server]
    if sec:
        cmd += ["--sec", sec]
    cmd.append(str(path))
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip()[-200:] or f"rc={proc.returncode}")
    return json.loads(proc.stdout.strip().splitlines()[-1])["url"]


def main() -> int:
    ap = argparse.ArgumentParser(description="Publish film artifacts to Blossom.")
    ap.add_argument("--run", required=True, help="film run directory")
    ap.add_argument("--server", action="append", default=None,
                    help="Blossom server (repeatable; default org servers)")
    ap.add_argument("--glob", action="append", default=None,
                    help="artifact glob, relative to --run (repeatable)")
    ap.add_argument("--sec", default=None, help="signing key (default: nak default key)")
    args = ap.parse_args()

    run = pathlib.Path(args.run).resolve()
    if not run.is_dir():
        print(f"publish-film: no run directory at {run}", file=sys.stderr)
        return 2

    servers = args.server or DEFAULT_SERVERS
    patterns = args.glob or DEFAULT_GLOBS
    files = sorted({pathlib.Path(p) for pattern in patterns for p in glob.glob(str(run / pattern))})
    if not files:
        print(f"publish-film: no artifacts matched {patterns} under {run}", file=sys.stderr)
        return 1

    failures = 0
    successes = 0
    for path in files:
        for server in servers:
            try:
                url = upload(path, server, args.sec)
                successes += 1
                print(f"{path.name}\t{url}")
            except Exception as exc:  # noqa: BLE001 — a mirror failing is a finding, not fatal
                failures += 1
                print(f"[FAIL] {path.name} @ {server}: {exc}")
    if failures:
        print(f"publish-film: {successes} ok, {failures} failed", file=sys.stderr)
    return 0 if successes else 1


if __name__ == "__main__":
    raise SystemExit(main())
