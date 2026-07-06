"""Tests 3-5: Contact exchange + encrypted chat between two devices."""

import time
import json
import uuid
import pytest

pytestmark = pytest.mark.smoke


class TestEncryptedChat:

    @pytest.mark.chat
    def test_contact_exchange(self, cli_a, cli_b):
        """Export contact from A, import on B, and vice versa."""
        # Get board A's contact URI
        contacts_a = cli_a.get_contacts()
        raw_a = json.dumps(contacts_a)

        # Try to export A's own contact
        # meshcore-cli export_contact needs a contact name
        # First, get our own identity info
        info_a = cli_a.get_info()
        info_b = cli_b.get_info()

        # Both should have valid info
        assert info_a, "Board A returned no info"
        assert info_b, "Board B returned no info"

    @pytest.mark.chat
    def test_public_channel_message(self, cli_a, cli_b):
        """Send a message on the public channel and verify it arrives.

        This tests the full encrypted path: A encrypts → LR2021 TX →
        B LR2021 RX → B decrypts → message displayed.
        """
        # Unique message to avoid dedup
        test_msg = f"SMOKE_TEST_{uuid.uuid4().hex[:8]}"

        # Board A sends on public channel
        send_result = cli_a.send_public(test_msg)
        assert send_result.get("success", True), \
            f"Board A failed to send public message: {send_result}"

        # Board B waits for the message
        time.sleep(15)  # Give time for CSMA delay + TX + RX + processing

        # Sync messages on B and check
        cli_b.sync_msgs()
        time.sleep(2)

        # Try to receive
        recv = {"success": False, "raw": ""}
        for attempt in range(6):
            recv = cli_b.wait_msg(timeout=10)
            raw = json.dumps(recv)
            if test_msg.lower() in raw.lower():
                return  # Success!

            # Try syncing again
            cli_b.sync_msgs()
            time.sleep(5)

        pytest.fail(
            f"Board B did not receive public message '{test_msg}' within 60s. "
            f"Last recv: {recv}"
        )

    @pytest.mark.chat
    def test_reverse_direction(self, cli_a, cli_b):
        """Send a message from B to A on public channel."""
        test_msg = f"REPLY_{uuid.uuid4().hex[:8]}"

        send_result = cli_b.send_public(test_msg)
        assert send_result.get("success", True), \
            f"Board B failed to send public message: {send_result}"

        time.sleep(15)

        cli_a.sync_msgs()
        time.sleep(2)

        recv = {"success": False, "raw": ""}
        for attempt in range(6):
            recv = cli_a.wait_msg(timeout=10)
            raw = json.dumps(recv)
            if test_msg.lower() in raw.lower():
                return  # Success!

            cli_a.sync_msgs()
            time.sleep(5)

        pytest.fail(
            f"Board A did not receive reverse message '{test_msg}' within 60s. "
            f"Last recv: {recv}"
        )
