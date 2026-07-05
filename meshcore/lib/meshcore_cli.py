"""Python wrapper around meshcore-cli for serial communication with MeshCore nodes."""

import json
import os
import re
import subprocess
import time
from typing import Optional


class MeshCoreCLI:
    """Control a MeshCore node via meshcore-cli over serial."""

    def __init__(self, port: str, timeout: int = 30):
        self.port = port
        self.timeout = timeout
        self.binary = os.path.expanduser("~/.local/bin/meshcore-cli")

    def _run(self, *args, timeout: Optional[int] = None) -> dict:
        """Run meshcore-cli with JSON output. Returns parsed JSON or raw output."""
        cmd = [
            self.binary,
            "-s", self.port,
            "-j",  # JSON output
            "-q",  # quiet (errors only)
        ] + list(args)

        t = timeout or self.timeout
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=t,
        )

        if result.returncode != 0:
            return {
                "success": False,
                "error": result.stderr.strip() or result.stdout.strip(),
                "returncode": result.returncode,
            }

        # Try to parse JSON output
        stdout = result.stdout.strip()
        try:
            return json.loads(stdout)
        except json.JSONDecodeError:
            return {"success": True, "raw": stdout}

    def get_contacts(self) -> dict:
        """List contacts on this node."""
        return self._run("contacts")

    def get_advert(self) -> dict:
        """Send an advert."""
        return self._run("advert")

    def send_flood_advert(self) -> dict:
        """Send a flood advert to discover nearby nodes."""
        return self._run("floodadv")

    def send_message(self, contact_name: str, message: str) -> dict:
        """Send a direct encrypted message to a contact."""
        return self._run("msg", contact_name, message)

    def send_public(self, message: str) -> dict:
        """Send a message to the public channel."""
        return self._run("public", message)

    def wait_msg(self, timeout: int = 30) -> dict:
        """Wait for an incoming message."""
        return self._run("wait_msg", timeout=timeout + 5)

    def recv(self) -> dict:
        """Read next message (non-blocking)."""
        return self._run("recv")

    def sync_msgs(self) -> dict:
        """Sync all unread messages."""
        return self._run("sync_msgs")

    def export_contact(self, contact_name: str) -> dict:
        """Export a contact's URI."""
        return self._run("export_contact", contact_name)

    def import_contact(self, uri: str) -> dict:
        """Import a contact from URI."""
        return self._run("import_contact", uri)

    def get_info(self) -> dict:
        """Get node info."""
        return self._run("get", "help")

    def wait_for_contact(self, name_pattern: str, max_wait: int = 60) -> bool:
        """Poll contacts list until a matching contact appears."""
        deadline = time.time() + max_wait
        while time.time() < deadline:
            result = self.get_contacts()
            raw = json.dumps(result)
            if name_pattern.lower() in raw.lower():
                return True
            time.sleep(5)
        return False

    def wait_for_message(self, pattern: str, max_wait: int = 60) -> Optional[str]:
        """Poll for messages until one matches the pattern."""
        deadline = time.time() + max_wait
        while time.time() < deadline:
            result = self.wait_msg(timeout=10)
            raw = json.dumps(result)
            if pattern.lower() in raw.lower():
                return raw
            time.sleep(2)
        return None
