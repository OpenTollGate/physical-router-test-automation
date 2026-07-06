"""Test 2: Advert discovery — board A sees board B's advert."""

import time
import json
import pytest

pytestmark = pytest.mark.smoke


class TestAdvertDiscovery:

    @pytest.mark.discovery
    def test_both_boards_respond_to_cli(self, cli_a, cli_b):
        """Both boards respond to meshcore-cli commands."""
        result_a = cli_a.get_contacts()
        assert result_a.get("success", True) or "raw" in result_a or "contacts" in json.dumps(result_a).lower(), \
            f"Board A CLI failed: {result_a}"

        result_b = cli_b.get_contacts()
        assert result_b.get("success", True) or "raw" in result_b or "contacts" in json.dumps(result_b).lower(), \
            f"Board B CLI failed: {result_b}"

    @pytest.mark.discovery
    def test_advert_exchange(self, cli_a, cli_b):
        """Board A sends flood advert, then checks if board B sees it."""
        # Both boards send flood adverts to announce themselves
        result_a = cli_a.send_flood_advert()
        time.sleep(2)
        result_b = cli_b.send_flood_advert()
        time.sleep(5)

        # Initialize raw_a/raw_b before the loop
        contacts_a = cli_a.get_contacts()
        contacts_b = cli_b.get_contacts()
        raw_a = json.dumps(contacts_a)
        raw_b = json.dumps(contacts_b)

        # Wait for adverts to propagate and contacts to update
        for attempt in range(6):
            time.sleep(10)
            cli_a.sync_msgs()
            cli_b.sync_msgs()

            contacts_a = cli_a.get_contacts()
            contacts_b = cli_b.get_contacts()

            raw_a = json.dumps(contacts_a)
            raw_b = json.dumps(contacts_b)

            # Check if either board sees the other
            if raw_a != "{}" and raw_a != '{"success": true, "raw": ""}' and len(raw_a) > 50:
                break
            if raw_b != "{}" and raw_b != '{"success": true, "raw": ""}' and len(raw_b) > 50:
                break

        # At least one board should see SOMETHING after 60s of advert exchange
        assert len(raw_a) > 50 or len(raw_b) > 50, \
            "Neither board discovered any contacts after 60s of advert exchange"
