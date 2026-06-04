"""Entry point: python -m lib.cloud_lab.worker --from-metadata|--from-env"""

from __future__ import annotations

import sys

from lib.cloud_lab.worker.pipeline import run_worker


def main() -> int:
    if "--from-env" in sys.argv:
        from lib.cloud_lab.worker.config import load_config_from_env
        config = load_config_from_env()
    elif "--from-metadata" in sys.argv:
        from lib.cloud_lab.worker.config import load_config_from_metadata
        config = load_config_from_metadata()
    else:
        print("Usage: python -m lib.cloud_lab.worker --from-metadata|--from-env", file=sys.stderr)
        return 2
    return run_worker(config)


if __name__ == "__main__":
    raise SystemExit(main())
