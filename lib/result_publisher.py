"""Thin shim — delegates to the nostr_publish package.

Install: pip install nostr-publish
Source: https://github.com/Amperstrand/nostr-publish-file-metadata-action

This file exists for backward compat with code that does:
  python -m lib.result_publisher
  from lib.result_publisher import publish_results

All logic now lives in nostr_publish.publisher.
"""

__all__ = [
    "publish_results",
    "publish_single_file",
    "main",
    "DEFAULT_MAX_FILE_SIZE",
    "DEFAULT_BLOSSOM_SERVER",
    "DEFAULT_RELAYS",
    "HARD_BLOCKED_NAMES",
    "HARD_BLOCKED_SUFFIXES",
    "logger",
]

from nostr_publish.publisher import (  # noqa: F401
    publish_results,
    publish_single_file,
    main,
    DEFAULT_MAX_FILE_SIZE,
    DEFAULT_BLOSSOM_SERVER,
    DEFAULT_RELAYS,
    HARD_BLOCKED_NAMES,
    HARD_BLOCKED_SUFFIXES,
    logger,
)

if __name__ == "__main__":
    import sys
    sys.exit(main())
