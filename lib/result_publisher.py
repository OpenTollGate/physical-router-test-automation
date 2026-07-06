"""Thin shim — delegates to the nostr_publish package.

Install: pip install nostr-publish
Source: https://github.com/Amperstrand/nostr-publish-file-metadata-action

This file exists for backward compat with code that does:
  python -m lib.result_publisher
  from lib.result_publisher import publish_results

All logic now lives in nostr_publish.publisher.
"""

from nostr_publish.publisher import (  # noqa: F401
    publish_results,
    publish_single_file,
    main,
    _build_parser,
    _upload_one,
    _publish_file_event,
    _generate_run_id,
    _guess_mime_type,
    _is_hard_blocked,
    _is_probably_binary,
    _hard_filter,
)
from nostr_publish.publisher import (  # noqa: F401
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
