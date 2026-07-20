"""Compatibility layer for physical-router-test-automation.

Re-exports tollgate-lab modules for backward compatibility while
the codebase migrates to direct tollgate-lab imports.
"""

# These modules have been extracted to tollgate-lab.
# Tests should gradually switch to importing from tollgate_lab directly.

try:
    from tollgate_lab.hardware.lock import (
        acquire_hardware_lock,
        release_hardware_lock,
        is_hardware_locked,
        require_hardware_lock,
        read_hardware_lock,
    )
except ImportError:
    # Fallback to local copy if tollgate-lab not installed
    from tollgate_lab.hardware.lock import (
        acquire_hardware_lock,
        release_hardware_lock,
        is_hardware_locked,
        require_hardware_lock,
        read_hardware_lock,
    )

try:
    from tollgate_lab.drivers.ssh import _ssh_run, SSHAdapter
except ImportError:
    pass

__all__ = [
    "acquire_hardware_lock",
    "release_hardware_lock",
    "is_hardware_locked",
    "require_hardware_lock",
    "read_hardware_lock",
]
