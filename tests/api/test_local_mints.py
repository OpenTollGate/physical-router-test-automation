"""Health checks for local mints in cloud lab / virtual lab environments."""

from __future__ import annotations

import json
import os
from urllib import request

import pytest

pytestmark = [pytest.mark.api, pytest.mark.virtual_lab]


def _skip_if_no_local_mints():
    if not os.environ.get("TOLLGATE_VIRTUAL_LAB"):
        pytest.skip("Local mints only available in virtual lab")


def test_cdk_v2_mint_healthy():
    _skip_if_no_local_mints()
    url = os.environ.get("TOLLGATE_CDK_MINT_URL", "http://10.99.99.2:8383")
    resp = request.urlopen(f"{url}/v1/keys", timeout=10)
    assert resp.status == 200
    data = json.loads(resp.read())
    keysets = data.get("keysets", [])
    assert len(keysets) > 0
    keyset_ids = [k.get("id", "") for k in keysets]
    assert any(k.startswith("01") for k in keyset_ids), f"CDK should have V2 keyset, got: {keyset_ids}"


def test_nutshell_v2_mint_healthy():
    _skip_if_no_local_mints()
    url = os.environ.get("TOLLGATE_NUTSHELL_V2_MINT_URL", "http://10.99.99.2:8384")
    resp = request.urlopen(f"{url}/v1/keys", timeout=10)
    assert resp.status == 200


def test_nutshell_v1_mint_healthy():
    _skip_if_no_local_mints()
    url = os.environ.get("TOLLGATE_NUTSHELL_V1_MINT_URL", "http://10.99.99.2:8385")
    resp = request.urlopen(f"{url}/v1/keys", timeout=10)
    assert resp.status == 200


def test_cdk_v2_mint_info():
    _skip_if_no_local_mints()
    url = os.environ.get("TOLLGATE_CDK_MINT_URL", "http://10.99.99.2:8383")
    resp = request.urlopen(f"{url}/v1/info", timeout=10)
    assert resp.status == 200
    data = json.loads(resp.read())
    assert data.get("name")


def test_local_mints_reachable_from_openwrt(router):
    _skip_if_no_local_mints()
    result = router.ssh("curl -sf http://10.99.99.2:8383/v1/keys | head -c 100")
    assert "keysets" in result, f"OpenWrt should reach CDK mint, got: {result}"
