import os
import subprocess
import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class BoardConfig:
    id: str
    port: str
    mac: str
    ssid: str
    ip: str
    uuid: str
    nsec_env_var: str

    @property
    def api_url(self) -> str:
        return f"http://{self.ip}:2121"

    @property
    def portal_url(self) -> str:
        return f"http://{self.ip}"

    @property
    def lock_name(self) -> str:
        return f"board-{self.id}"


@dataclass
class Config:
    boards: dict[str, BoardConfig] = field(default_factory=dict)
    wifi_iface: str = ""
    upstream_ssid: str = ""
    sudo_pw: str = ""
    lock_dir: str = ""
    mint_url: str = ""
    fund_amount: int = 42
    firmware_dir: str = ""

    @staticmethod
    def load() -> "Config":
        cfg = Config()
        cfg.wifi_iface = _detect_wifi_iface()
        cfg.upstream_ssid = os.environ.get("UPSTREAM_SSID", "EnterSSID-2.4GHz")
        cfg.sudo_pw = os.environ.get("SUDO_PW", "")
        cfg.lock_dir = os.environ.get(
            "LOCK_DIR",
            str(Path(__file__).resolve().parent.parent / "locks"),
        )
        cfg.mint_url = os.environ.get("MINT_URL", "https://testnut-nutshell.mints.orangesync.tech")
        cfg.fund_amount = int(os.environ.get("FUND_AMOUNT", "42"))
        cfg.firmware_dir = os.environ.get(
            "FIRMWARE_DIR", ""
        )

        cfg.boards = {
            "a": BoardConfig(
                id="a",
                port=os.environ.get("BOARD_A_PORT", "/dev/ttyACM0"),
                mac="94:a9:90:2e:37:7c",
                ssid="TollGate-B96D80",
                ip="10.185.47.1",
                uuid="d718c206-d697-4fd7-b902-e7491c34e833",
                nsec_env_var="BOARD_A_NSEC",
            ),
            "b": BoardConfig(
                id="b",
                port=os.environ.get("BOARD_B_PORT", "/dev/ttyACM1"),
                mac="fc:01:2c:c5:50:50",
                ssid="TollGate-C0E9CA",
                ip="10.192.45.1",
                uuid="02ac6725-14e0-4849-9719-9f9cf8d367c7",
                nsec_env_var="BOARD_B_NSEC",
            ),
            "c": BoardConfig(
                id="c",
                port=os.environ.get("BOARD_C_PORT", "/dev/ttyACM2"),
                mac="20:6e:f1:98:d7:08",
                ssid="TollGate-4A2510",
                ip="10.74.63.1",
                uuid="8dfec13d-3b39-4c36-9546-86c8aa5025e3",
                nsec_env_var="BOARD_C_NSEC",
            ),
        }
        return cfg

    def get_board(self, board_id: str) -> BoardConfig:
        return self.boards[board_id]


def _detect_wifi_iface() -> str:
    iface = os.environ.get("WIFI_IFACE", "")
    if iface:
        return iface
    try:
        result = subprocess.run(
            ["iw", "dev"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        for line in result.stdout.splitlines():
            if "Interface" in line:
                return line.split()[-1]
    except Exception:
        pass
    return "wlp59s0"
