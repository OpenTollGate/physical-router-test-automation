"""Cloud lab worker — GCP metadata config."""

from __future__ import annotations

import logging
import urllib.request
from dataclasses import dataclass

from lib.cloud_lab.worker.shell import log

METADATA_URL = "http://metadata.google.internal/computeMetadata/v1/instance/attributes"
@dataclass
class WorkerConfig:
    run_id: str
    sut_branch: str
    sut_commit: str
    sut_pr: str
    artifact_run_id: str
    artifact_repo: str
    suite_ref: str
    backend: str
    reseller_scenarios: bool
    two_router: bool
    secondary_router_host: str
    secondary_router_port: str
    keep_vm_on_failure: bool
    publish: bool
    project: str
    zone: str
    vm_name: str
    gh_token: str
    mint: str
    portal: str
    quick: bool
    hwsim_enabled: bool
    vwifi_enabled: bool
    smoke: bool
    wifi_plane: str
def _metadata_get(key: str) -> str:
    req = urllib.request.Request(
        f"{METADATA_URL}/{key}",
        headers={"Metadata-Flavor": "Google"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.read().decode().strip()
def _metadata_get_optional(key: str, default: str = "") -> str:
    try:
        return _metadata_get(key)
    except Exception:
        return default
def load_config_from_metadata() -> WorkerConfig:
    cfg = WorkerConfig(
        run_id=_metadata_get("tollgate-run-id"),
        sut_branch=_metadata_get("tollgate-sut-branch"),
        sut_commit=_metadata_get("tollgate-sut-commit"),
        sut_pr=_metadata_get("tollgate-pr"),
        artifact_run_id=_metadata_get("tollgate-artifact-run-id"),
        artifact_repo=_metadata_get("tollgate-artifact-repo"),
        suite_ref=_metadata_get("tollgate-suite-ref"),
        backend=_metadata_get("tollgate-backend"),
        reseller_scenarios=_metadata_get_optional("tollgate-reseller-scenarios").lower() in ("true", "1", "yes"),
        two_router=_metadata_get_optional("tollgate-two-router").lower() in ("true", "1", "yes"),
        secondary_router_host=_metadata_get_optional("tollgate-secondary-router-host"),
        secondary_router_port=_metadata_get_optional("tollgate-secondary-router-port"),
        keep_vm_on_failure=_metadata_get_optional("tollgate-keep-vm-on-failure").lower() not in ("false", "0", "no"),
        publish=_metadata_get("tollgate-publish").lower() in ("true", "1", "yes"),
        project=_metadata_get("tollgate-project"),
        zone=_metadata_get("tollgate-zone"),
        vm_name=_metadata_get("tollgate-vm-name"),
        gh_token=_metadata_get("tollgate-gh-token"),
        mint=_metadata_get_optional("tollgate-mint", "auto"),
        portal=_metadata_get_optional("tollgate-portal", "builtin"),
        hwsim_enabled=_metadata_get_optional("tollgate-hwsim").lower() in ("true", "1", "yes"),
        vwifi_enabled=_metadata_get_optional("tollgate-vwifi").lower() in ("true", "1", "yes"),
        quick=_metadata_get_optional("tollgate-quick").lower() in ("true", "1", "yes"),
        smoke=_metadata_get_optional("tollgate-smoke").lower() in ("true", "1", "yes"),
        wifi_plane=_metadata_get_optional("tollgate-wifi-plane", "tap"),
    )
    log.info(
        "Config: run=%s branch=%s repo=%s backend=%s pr=%s publish=%s keep_on_fail=%s mint=%s portal=%s hwsim=%s vwifi=%s quick=%s smoke=%s wifi_plane=%s",
        cfg.run_id, cfg.sut_branch, cfg.artifact_repo, cfg.backend,
        cfg.sut_pr or "(none)", cfg.publish, cfg.keep_vm_on_failure, cfg.mint, cfg.portal, cfg.hwsim_enabled, cfg.vwifi_enabled, cfg.quick, cfg.smoke, cfg.wifi_plane,
    )
    log.info(
        "Artifact: run_id=%s suite_ref=%s reseller=%s secondary=%s",
        cfg.artifact_run_id, cfg.suite_ref[:7], cfg.reseller_scenarios, cfg.secondary_router_host or "(none)",
    )
    return cfg
