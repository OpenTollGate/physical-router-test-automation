import subprocess
import logging

log = logging.getLogger("tollgate.container_client")


class ContainerClient:
    """Client that executes commands inside a Docker container on a remote host.

    Designed for virtual lab setups where the "phone" is a Debian container
    (tg-poc-client) reachable via SSH through a jump host. All commands are
    routed as ``ssh <host> docker exec tg-poc-client <cmd>``.

    The adapter exposes the same interface as ADBDevice so existing tests can
    use it as a drop-in replacement for the ``adb`` fixture.
    """

    is_desktop = True
    is_container = True

    def __init__(self, host: str, container: str = "tg-poc-client"):
        self._host = host
        self._container = container

    # -- internal helpers ------------------------------------------------

    def _ssh_cmd(self, cmd: str, timeout: int = 30) -> list[str]:
        return ["ssh", self._host, f"docker exec {self._container} {cmd}"]

    def _exec(self, cmd: str, timeout: int = 30) -> str:
        full = self._ssh_cmd(cmd, timeout)
        log.debug("container exec: %s", " ".join(full))
        r = subprocess.run(full, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip()

    # -- ADBDevice-compatible interface ----------------------------------

    def wifi_mac(self) -> str:
        return "02:00:00:00:00:01"

    def wifi_ip(self) -> str:
        return "192.168.1.100"

    def shell(self, cmd: str, timeout: int = 30) -> str:
        """Execute a shell command inside the container.

        Only non-interactive commands are forwarded; Android-specific
        commands (dumpsys, am, svc, settings, cmd) return empty strings.
        """
        if cmd.startswith("dumpsys") or cmd.startswith("am ") or cmd.startswith("svc "):
            return ""
        if cmd.startswith("settings "):
            return ""
        if cmd.startswith("cmd "):
            return ""
        return self._exec(cmd, timeout)

    def exec(self, cmd: str, timeout: int = 30) -> str:
        return self._exec(cmd, timeout)

    def curl(self, url: str, timeout: int = 10, **kwargs) -> str:
        flags = []
        for k, v in kwargs.items():
            if len(k) == 1:
                flags.append(f"-{k}")
                if v is not True:
                    flags.append(str(v))
            else:
                k_dashed = k.replace("_", "-")
                if v is True:
                    flags.append(f"--{k_dashed}")
                elif v is False:
                    continue
                else:
                    flags.append(f"--{k_dashed}={v}")
        flag_str = " ".join(flags)
        cmd = f"curl --connect-timeout {timeout} --max-time {timeout + 5} {flag_str} '{url}'".strip()
        return self._exec(cmd, timeout=timeout + 10)

    def ping(self, host: str = "1.1.1.1", count: int = 2, timeout: int = 3,
             interface: str = None) -> bool:
        iface_opt = f" -I {interface}" if interface else ""
        out = self._exec(f"ping{iface_opt} -c {count} -W {timeout} {host}",
                         timeout=timeout * count + 10)
        result = "0% packet loss" in out
        if not result:
            log.debug("ping %s failed: %s", host, out[:100])
        return result

    def is_connected(self) -> bool:
        return self.ping("192.168.1.1", count=1, timeout=3)

    def screenshot(self, path: str) -> bool:
        return False

    def screenshot_portal(self, path: str, report_dir: str = None) -> bool:
        return False

    def ui_xml(self) -> str:
        return ""

    def tap(self, x: int, y: int):
        pass

    def tap_bounds(self, bounds_str: str):
        pass

    def input_text(self, text: str):
        pass

    def press_key(self, key: str):
        pass

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration: int = 300):
        pass

    def wake_and_unlock(self):
        return True

    def force_stop(self, package: str):
        pass

    def start_activity(self, action: str = None, data_uri: str = None,
                       component: str = None):
        pass

    def open_url(self, url: str) -> bool:
        return True

    def connect_wifi(self, ssid: str) -> bool:
        return True

    def restore_wifi(self) -> bool:
        return True

    def is_wifi_connected(self, ssid: str) -> bool:
        return True
