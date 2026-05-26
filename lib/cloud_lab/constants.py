"""Shared constants for the cloud test lab (GCP, VPS, Hetzner, and provider-agnostic)."""

from __future__ import annotations

import os

# ── GCP-specific ──────────────────────────────────────────────────────────────

DEFAULT_ZONE = "europe-west1-b"
DEFAULT_MACHINE_TYPE = "n2-standard-2"
DEFAULT_DISK_SIZE_GB = 50
VM_NAME = "tollgate-test-runner"
SNAPSHOT_NAME = "tollgate-runner-baked-v2"
FIREWALL_RULE_SSH = "tollgate-allow-ssh"
SSH_KEY = os.environ.get("TOLLGATE_GCP_SSH_KEY", os.path.expanduser("~/.ssh/google_compute_engine"))

# ── VPS-specific ──────────────────────────────────────────────────────────────

VPS_HOST = os.environ.get("TOLLGATE_VPS_HOST", "")
VPS_USER = os.environ.get("TOLLGATE_VPS_USER", "root")
VPS_SSH_KEY = os.environ.get("TOLLGATE_VPS_SSH_KEY", os.path.expanduser("~/.ssh/id_ed25519"))
VPS_WORKER_CONFIG = "/tmp/tollgate-worker-config.json"
VPS_RUN_LOCK = "/tmp/tollgate-run.lock"

# ── Hetzner-specific ─────────────────────────────────────────────────────────

HETZNER_API_TOKEN = os.environ.get("HETZNER_API_TOKEN", "")
HETZNER_API_URL = "https://api.hetzner.cloud/v1"
HETZNER_SERVER_TYPE = os.environ.get("HETZNER_SERVER_TYPE", "cx32")
HETZNER_SNAPSHOT_NAME = os.environ.get("HETZNER_SNAPSHOT_NAME", "tollgate-runner-baked")
HETZNER_SSH_KEY_ID = os.environ.get("HETZNER_SSH_KEY_ID", "")
HETZNER_SSH_KEY = os.environ.get("HETZNER_SSH_KEY", os.path.expanduser("~/.ssh/id_ed25519"))
HETZNER_LOCATION = os.environ.get("HETZNER_LOCATION", "fsn1")

# ── Provider-agnostic (virtual lab topology) ──────────────────────────────────

VIRT_LAB_PASSWORD = "tollgate"
OPENWRT_IP = "10.99.99.1"
SELLER_OPENWRT_IP = "10.99.99.11"
SELLER_OPENWRT_MAC = "52:54:00:12:34:57"
DEBIAN_IP = "10.99.99.100"
DEBIAN_MAC = "de:54:4e:91:49:da"
TEST_DIR = "/opt/tollgate-test"
VIRT_LAB_WORKDIR = "$HOME/tollgate-virtual-lab"
CLOUD_ARCH = "x86_64"
SUITE_REPO = "OpenTollGate/physical-router-test-automation"
SUITE_REPO_URL = "https://github.com/OpenTollGate/physical-router-test-automation.git"
RESULTS_ROOT = "/tmp/tollgate-results"
WORKER_LOG = "/var/log/tollgate-run.log"
STALE_VM_HOURS = 2
