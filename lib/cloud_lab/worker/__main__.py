"""Entry point: python -m lib.cloud_lab.worker --from-metadata"""

from __future__ import annotations

import sys

from lib.cloud_lab.worker.config import load_config_from_metadata
from lib.cloud_lab.worker.pipeline import run_worker


def main() -> int:
    if "--from-metadata" not in sys.argv:
        print("Usage: python -m lib.cloud_lab.worker --from-metadata", file=sys.stderr)
        return 2
    config = load_config_from_metadata()
    return run_worker(config)


if __name__ == "__main__":
    raise SystemExit(main())
