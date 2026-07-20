from __future__ import annotations

"""Serial console — delegates to tollgate_lab when available.

Canonical: tollgate_lab/drivers/serial_console.py
"""

try:
    from tollgate_lab.drivers.serial_console import *
except ImportError:
    pass  # Fall through to local implementation below

import os
import subprocess
import sys
from pathlib import Path

# DEPRECATED: This module is a backward-compat shim.
# All consumers should import from tollgate_lab.drivers.serial_console directly.
# This file will be removed once all external consumers are migrated.
