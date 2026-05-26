import json

import pytest


@pytest.mark.board_a
@pytest.mark.board_c
class TestNip11:
    def test_nip11_json(self, board_connected, relay):
        doc = relay.nip11()
        assert doc is not None, "NIP-11 should return valid JSON"

    def test_required_fields(self, board_connected, relay):
        doc = relay.nip11()
        assert doc is not None
        assert isinstance(doc.get("name"), str) and len(doc["name"]) > 0
        assert isinstance(doc.get("description"), str) and len(doc["description"]) > 0
        assert isinstance(doc.get("software"), str)
        assert isinstance(doc.get("version"), str)

    def test_nip_support(self, board_connected, relay):
        doc = relay.nip11()
        assert doc is not None
        nips = doc.get("supported_nips", [])
        assert isinstance(nips, list)
        assert 1 in nips, "NIP-01 should be supported"
        assert 9 in nips, "NIP-09 should be supported"
        assert 11 in nips, "NIP-11 should be supported"

    def test_name_mentions_tollgate(self, board_connected, relay):
        doc = relay.nip11()
        assert doc is not None
        name = doc.get("name", "")
        assert "TollGate" in name or "4869" in name, f"Name: {name}"
