"""Shared constants for the GCP cloud lab."""

from __future__ import annotations

import os

DEFAULT_ZONE = "europe-west1-b"
DEFAULT_MACHINE_TYPE = "n2-standard-2"
DEFAULT_DISK_SIZE_GB = 50
VM_NAME = "tollgate-test-runner"
SNAPSHOT_NAME = "tollgate-runner-baked-v4"
FIREWALL_RULE_SSH = "tollgate-allow-ssh"
VIRT_LAB_PASSWORD = "tollgate"
OPENWRT_IP = "10.99.99.1"
SELLER_OPENWRT_IP = "10.99.99.11"
SELLER_OPENWRT_MAC = "52:54:00:12:34:57"
DEBIAN_IP = "10.99.99.100"
DEBIAN_MAC = "de:54:4e:91:49:da"
SSH_KEY = os.environ.get("TOLLGATE_GCP_SSH_KEY", os.path.expanduser("~/.ssh/google_compute_engine"))
TEST_DIR = "/opt/tollgate-test"
VIRT_LAB_WORKDIR = "$HOME/tollgate-virtual-lab"
CLOUD_ARCH = "x86_64"
SUITE_REPO = "OpenTollGate/physical-router-test-automation"
SUITE_REPO_URL = "https://github.com/OpenTollGate/physical-router-test-automation.git"
RESULTS_ROOT = "/tmp/tollgate-results"
WORKER_LOG = "/var/log/tollgate-run.log"
STALE_VM_HOURS = 2
