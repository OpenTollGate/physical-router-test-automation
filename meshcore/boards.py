"""Board definitions for MeshCore LR2021 smoke tests."""

import os
from dataclasses import dataclass


@dataclass
class MeshCoreBoard:
    id: str
    port: str
    serial: str
    role: str

    @property
    def env_port(self) -> str:
        return os.environ.get(f"MESHCORE_BOARD_{self.id.upper()}_PORT", self.port)


BOARDS = {
    "a": MeshCoreBoard(
        id="a",
        port="/dev/ttyACM1",
        serial="B0:A6:04:00:96:DC",
        role="companion",
    ),
    "b": MeshCoreBoard(
        id="b",
        port="/dev/ttyACM2",
        serial="88:56:A6:7B:C6:98",
        role="companion",
    ),
}

# Path to the balloon board mutex lock script
LOCK_SCRIPT = os.path.expanduser(
    "~/repos/balloon-fresh/tools/balloon-board-lock.py"
)

# Path to the MeshCore fork (for building/flashing)
MESHCORE_DIR = os.path.expanduser("~/MeshCore")
