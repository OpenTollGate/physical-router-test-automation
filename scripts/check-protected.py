#!/usr/bin/env python3
"""CLI: fail closed if a target is a protected production/control device."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.protected_devices import (  # noqa: E402
    ProtectedDeviceError,
    match_protected,
)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--board", default=None, help="ubus board_name")
    ap.add_argument("--model", default=None, help="inventory model (e.g. GL-E750)")
    ap.add_argument("--mac", action="append", default=[], help="br-lan or other MAC")
    ap.add_argument("--hostname", default=None)
    ap.add_argument("--address", action="append", default=[], help="SSH/management address")
    args = ap.parse_args()

    try:
        row = match_protected(
            board_name=args.board,
            model=args.model,
            macs=args.mac,
            hostname=args.hostname,
            addresses=args.address,
        )
    except ProtectedDeviceError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if row:
        print(f"PROTECTED {row.get('id', '?')}: {row.get('reason', '')}")
        print("REFUSED: this device is not a test target.")
        return 1
    print("NOT_PROTECTED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
