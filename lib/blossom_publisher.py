#!/usr/bin/env python3
"""Thin shim — delegates to the nostr_publish package.

Install: pip install git+https://github.com/Amperstrand/nostr-publish-file-metadata-action.git
"""
from nostr_publish.blossom import (
    compute_sha256,
    get_blob_url,
    upload_to_blossom,
)
