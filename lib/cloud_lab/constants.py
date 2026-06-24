"""Shared constants for the GCP cloud lab."""

from __future__ import annotations

import os

DEFAULT_ZONE = "us-central1-a"
DEFAULT_MACHINE_TYPE = "n1-standard-2"
DEFAULT_DISK_SIZE_GB = 50
VM_NAME = "tollgate-test-runner"
SNAPSHOT_NAME = "tollgate-runner-baked-v15"
FIREWALL_RULE_SSH = "tollgate-allow-ssh"
VIRT_LAB_PASSWORD = "tollgate"
OPENWRT_IP = "10.99.99.1"
SELLER_OPENWRT_IP = "10.99.99.11"
SELLER_OPENWRT_MAC = "52:54:00:12:34:57"
DEBIAN_IP = "10.99.99.100"
DEBIAN_MAC = "de:54:4e:91:49:da"
SSH_KEY = os.environ.get("TOLLGATE_GCP_SSH_KEY", os.path.expanduser("~/.ssh/google_compute_engine"))
TEST_DIR = os.environ.get("GITHUB_WORKSPACE", "/opt/tollgate-test")
VIRT_LAB_WORKDIR = "$HOME/tollgate-virtual-lab"
CLOUD_ARCH = "x86_64"
SUITE_REPO = "OpenTollGate/physical-router-test-automation"
SUITE_REPO_URL = "https://github.com/OpenTollGate/physical-router-test-automation.git"
RESULTS_ROOT = "/tmp/tollgate-results"
WORKER_LOG = "/var/log/tollgate-run.log"
STALE_VM_HOURS = 2

# Management bridge — separate from test network so SSH survives network changes
MGMT_BRIDGE = "mgmt-br"
MGMT_HOST_IP = "10.99.97.2"
MGMT_SUBNET = "10.99.97.0/24"
MGMT_ALPHA_IP = "10.99.97.1"
MGMT_DEBIAN_IP = "10.99.97.100"
MGMT_BETA_IP = "10.99.97.11"
MGMT_TAP_ALPHA = "mgmt-tap"
MGMT_TAP_DEBIAN = "mgmt-tap2"
MGMT_TAP_BETA = "mgmt-tap3"
MGMT_ALPHA_MAC = "52:54:00:c0:01:01"
MGMT_DEBIAN_MAC = "52:54:00:c0:02:64"
MGMT_BETA_MAC = "52:54:00:c0:03:0b"

# Two-router upstream bridge
UPSTREAM_BRIDGE = "tg-upstream-br"
UPSTREAM_TAP_ALPHA = "tg-upst-tap-a"
UPSTREAM_TAP_BETA = "tg-upst-tap-b"
BETA_WAN_IP = "10.99.98.1"
ALPHA_WAN_MAC = "52:54:00:aa:bb:02"
BETA_WAN_MAC = "52:54:00:aa:bb:01"

# Local mint configuration
LOCAL_MINT_HOST = "10.99.99.2"
CDK_MINT_PORT = 8383
NUTSHELL_V2_MINT_PORT = 8384
NUTSHELL_V1_MINT_PORT = 8385
CDK_VERSION = "0.16.0"
CDK_MINT_DIR = "/opt/cdk-mintd"

# IP-based URLs — used for health checks before /etc/hosts is configured
CDK_MINT_URL = f"http://{LOCAL_MINT_HOST}:{CDK_MINT_PORT}"
NUTSHELL_V2_MINT_URL = f"http://{LOCAL_MINT_HOST}:{NUTSHELL_V2_MINT_PORT}"
NUTSHELL_V1_MINT_URL = f"http://{LOCAL_MINT_HOST}:{NUTSHELL_V1_MINT_PORT}"

# LAN mint URLs — IP-based (OpenWrt dnsmasq does not resolve /etc/hosts entries)
V1_TESTNUT_NUTSHELL_LAN = f"http://{LOCAL_MINT_HOST}:{NUTSHELL_V1_MINT_PORT}"
V2_TESTNUT_CDK_LAN = f"http://{LOCAL_MINT_HOST}:{CDK_MINT_PORT}"
V2_TESTNUT_NUTSHELL_LAN = f"http://{LOCAL_MINT_HOST}:{NUTSHELL_V2_MINT_PORT}"

# Beta isolated LAN bridge — separate from tg-poc-br so Beta has its own subnet
BETA_BRIDGE = "tg-beta-br"
BETA_TAP = "tg-beta-tap"
BETA_LAN_IP = "10.99.96.11"
BETA_LAN_HOST_IP = "10.99.96.2"
BETA_LAN_SUBNET = "10.99.96.0/24"

# Backwards-compatible alias
NUTSHELL_V1_MINT_LAN = V1_TESTNUT_NUTSHELL_LAN
