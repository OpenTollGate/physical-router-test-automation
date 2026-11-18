#!/usr/bin/env python3
"""adversarial-mint.py — the NUT-13 keyset-collision fixture (matrix scenario
`keyset-id-collision-quarantine`; PRTA #348's pending-venue enabler).

Serves a minimal Cashu mint surface whose KEYSET is chosen to collide with
an honest keyset under NUT-13's legacy derivation, so a wallet with the
honest keyset registered must refuse to register this mint (the fork's
`refuseCollidingKeyset` / `ErrCollidingKeysetId` quarantine).

Two attack shapes, selected by argv[1] (default: duplicate):

  duplicate  Announce the HONEST mint's public keys verbatim as our own.
             The keyset ID verifies (ID = hash(keys)), so the ID and its
             residue trivially collide cross-mint — the exact-duplicate
             path of the quarantine. Needs no grinding; the honest keys
             are passed via --honest-keys (a JSON file served by the lane,
             or fetched from the honest mint's /v1/keys by the fixture).

  residue    Announce ground keys whose v1 keyset ID shares the honest
             keyset's residue mod 2^31-1 without being the same ID. The
             pair comes from grind-keyset-collision.py (offline, run once)
             via --collision-file; the fixture refuses to start without it
             rather than silently weakening the shape.

The mint answers /v1/keys, /v1/keysets (keyset announcement), /v1/info
(minimal) and 501s everything money-moving: the scenario's assertion is
the REGISTRATION refusal, which happens before any token handling.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

RESIDUE_MOD = 2**31 - 1


def keyset_id_residue(keyset_id: str) -> int:
    return int(keyset_id, 16) % RESIDUE_MOD if all(c in "0123456789abcdefABCDEF" for c in keyset_id) \
        else int.from_bytes(keyset_id.encode(), "big") % RESIDUE_MOD


def load_keys(source: str) -> dict:
    if source.startswith("http"):
        with urllib.request.urlopen(source, timeout=10) as r:
            return json.load(r)
    with open(source) as f:
        return json.load(f)


class Handler(BaseHTTPRequestHandler):
    keys_doc: dict = {}
    mode: str = "duplicate"
    honest_id: str = ""

    def do_GET(self):  # noqa: N802
        if self.path.startswith("/v1/keys") or self.path.startswith("/v1/keysets"):
            body = json.dumps(self.keys_doc).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path.startswith("/v1/info"):
            body = json.dumps({"name": "adversarial-nut13-fixture", "nuts": {}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(501)
            self.end_headers()

    def do_POST(self):  # noqa: N802
        # Registration is a GET-side fact; any money-moving POST is refused.
        self.send_response(501)
        self.end_headers()

    def log_message(self, fmt, *args):
        sys.stderr.write(f"[adversarial-mint:{self.mode}] {fmt % args}\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", nargs="?", choices=["duplicate", "residue"], default="duplicate")
    ap.add_argument("--listen", default="0.0.0.0:9099")
    ap.add_argument("--honest-keys", required=True,
                    help="URL or file of the honest mint's /v1/keys document")
    ap.add_argument("--collision-file", default=None,
                    help="residue mode: the ground pair from grind-keyset-collision.py")
    args = ap.parse_args()

    honest = load_keys(args.honest_keys)
    honest_id = honest.get("keysets", [{}])[0].get("id", "") if "keysets" in honest else honest.get("id", "")

    if args.mode == "duplicate":
        # Announce the honest keys as ours: ID verifies, residue trivially equal.
        keys_doc = dict(honest)
    else:
        if not args.collision_file:
            print("residue mode requires --collision-file (run grind-keyset-collision.py)",
                  file=sys.stderr)
            return 2
        with open(args.collision_file) as f:
            pair = json.load(f)
        keys_doc = pair["adversarial_keys"]
        honest_id = pair["honest_id"]

    Handler.keys_doc = keys_doc
    Handler.mode = args.mode
    Handler.honest_id = honest_id
    host, port = args.listen.rsplit(":", 1)
    print(f"adversarial-mint[{args.mode}] on {args.listen}; honest id {honest_id[:16]}… "
          f"(residue {keyset_id_residue(honest_id) if honest_id else '?'})", file=sys.stderr)
    ThreadingHTTPServer((host, int(port)), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
