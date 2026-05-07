#!/usr/bin/env python3
"""U-Boot recovery for GL.iNet GL-MT3000 with voice guidance and pcap capture.

Guides user through physical reset, uploads firmware, monitors reboot.
Captures full pcap for post-analysis of boot sequence signals.

MT3000 U-Boot procedure (validated on hardware, from GL.iNet docs):
  1. Power off, ethernet in LAN port only
  2. Hold RESET button (side, under antenna — no paperclip needed)
  3. Plug in power WHILE holding reset
  4. Blue LED flashes 6 times, then turns SOLID WHITE
  5. Release reset when LED turns solid white
  6. U-Boot HTTP at http://192.168.1.1 (no Server header, rejects HEAD)

MT3000 Hardware:
  - Reset button: side, under antenna. GPIO 1, KEY_RESTART.
  - Mode toggle: side switch (forward/backward). GPIO 0, EV_SW/BTN_0.
    GL.iNet firmware: configurable (VPN/Tor/WiFi). OpenWrt: BTN_0 (no-op).
    NOT used for U-Boot entry.

U-Boot HTTP API (tested on MT3000):
  - GET  /         → 200 OK, HTML firmware upload page
  - HEAD /         → 405 Method Not Allowed
  - POST /upload   → multipart form, field "firmware" → "size md5hash"
  - GET  /flashing.html → triggers flash, returns "Update in progress" page

Network signatures (from tcpdump analysis):
  - OpenWrt running: ICMPv6 Router Advertisements every ~10s from router MAC
  - U-Boot mode: no ICMPv6 RA, HTTP GET on port 80 returns HTML
  - Router off: no traffic from router MAC

Usage:
    scripts/uboot-recover.py --image <firmware.bin> [--interface en6]
    scripts/uboot-recover.py --image <fw.bin> --no-upload   # dry run
    scripts/uboot-recover.py --image <fw.bin> --no-voice    # no audio
    scripts/uboot-recover.py --image <fw.bin> --capture /tmp/boot.pcap
"""

import argparse
import os
import subprocess
import sys
import tempfile
import time


UBOOT_IP = "192.168.1.1"
UBOOT_LED_PATTERN = "blue flashes 6x then solid white"
UBOOT_FLASH_COUNT = 6
UBOOT_FLASH_TIME_SECONDS = 240
UBOOT_REBOOT_TIMEOUT = 360


def say(msg):
    subprocess.run(["say", "-v", "Samantha", msg], check=False, timeout=10)


def log(msg):
    ts = time.strftime("%H:%M:%S")
    print(f"  [{ts}] {msg}")
    sys.stdout.flush()


def get_link_state(interface):
    r = subprocess.run(
        ["ifconfig", interface],
        capture_output=True, text=True, timeout=5, check=False,
    )
    return "status: active" in r.stdout.lower()


def wait_for_link_down(interface, timeout=120):
    log(f"Monitoring {interface} for link down...")
    start = time.time()
    while time.time() - start < timeout:
        if not get_link_state(interface):
            log("Link down detected.")
            return True
        time.sleep(0.3)
    log(f"Link did not go down within {timeout}s.")
    return False


def wait_for_link_up(interface, timeout=60):
    log(f"Waiting for link up on {interface}...")
    start = time.time()
    while time.time() - start < timeout:
        if get_link_state(interface):
            log("Link up detected.")
            return True
        time.sleep(0.3)
    log(f"Link did not come up within {timeout}s.")
    return False


def detect_uboot_http():
    try:
        r = subprocess.run(
            ["curl", "-s", "--max-time", "2", f"http://{UBOOT_IP}/"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        if "FIRMWARE UPDATE" in r.stdout or "firmware" in r.stdout.lower():
            return True, "firmware page"
        if r.stdout.strip().startswith("<!DOCTYPE"):
            return True, "HTML response"
        return False, r.stdout[:100] if r.stdout.strip() else "no response"
    except Exception as e:
        return False, str(e)[:80]


def wait_for_uboot(timeout=90):
    log(f"Probing U-Boot HTTP at {UBOOT_IP}...")
    start = time.time()
    attempts = 0
    while time.time() - start < timeout:
        attempts += 1
        found, detail = detect_uboot_http()
        if found:
            log(f"U-Boot detected on attempt #{attempts}: {detail}")
            return True
        if attempts <= 3 or attempts % 10 == 0:
            log(f"  attempt #{attempts}: {detail}")
        time.sleep(1)
    log(f"U-Boot not detected within {timeout}s ({attempts} attempts).")
    return False


def upload_firmware(image_path, timeout=300):
    size_mb = os.path.getsize(image_path) / 1024 / 1024
    log(f"Uploading {os.path.basename(image_path)} ({size_mb:.1f} MB) to /upload...")
    try:
        r = subprocess.run(
            [
                "curl", "-sk", "--show-error",
                "--max-time", str(timeout),
                "-F", f"firmware=@{image_path};type=application/octet-stream",
                f"http://{UBOOT_IP}/upload",
            ],
            capture_output=True, text=True, timeout=timeout + 30, check=False,
        )
        if r.returncode == 0 and r.stdout.strip():
            parts = r.stdout.strip().split()
            log(f"Upload accepted: size={parts[0]} bytes, md5={parts[1] if len(parts) > 1 else '?'}")
            return True
        log(f"Upload failed (exit {r.returncode}): {r.stderr[:300]}")
    except subprocess.TimeoutExpired:
        log("Upload timed out.")
    except Exception as e:
        log(f"Upload error: {e}")
    return False


def trigger_flash():
    log("Triggering flash via /flashing.html...")
    try:
        r = subprocess.run(
            ["curl", "-s", "--max-time", "5", f"http://{UBOOT_IP}/flashing.html"],
            capture_output=True, text=True, timeout=10, check=False,
        )
        if "Update in progress" in r.stdout:
            log("Flash triggered — 'Update in progress' page returned.")
            return True
        log(f"Unexpected flash response: {r.stdout[:100]}")
    except Exception as e:
        log(f"Flash trigger error: {e}")
    return False


def wait_for_reboot(interface, timeout=UBOOT_REBOOT_TIMEOUT):
    log("Waiting for router to reboot...")
    start = time.time()

    link_went_down = False
    while time.time() - start < timeout:
        state = get_link_state(interface)
        if not state:
            if not link_went_down:
                elapsed = int(time.time() - start)
                log(f"Link down after {elapsed}s — router rebooting...")
                link_went_down = True
        elif link_went_down:
            elapsed = int(time.time() - start)
            log(f"Link back up after {elapsed}s — OpenWrt booting...")
            break

        if not link_went_down and time.time() - start > UBOOT_FLASH_TIME_SECONDS:
            log(f"No link-down detected after {UBOOT_FLASH_TIME_SECONDS}s. Router may have already rebooted.")
            link_went_down = True
        time.sleep(2)

    log("Waiting for SSH...")
    while time.time() - start < timeout:
        try:
            r = subprocess.run(
                ["ssh", "-o", "StrictHostKeyChecking=no",
                 "-o", "UserKnownHostsFile=/dev/null",
                 "-o", "ConnectTimeout=3",
                 "-o", "PasswordAuthentication=no",
                 f"root@{UBOOT_IP}", "echo SSH_OK"],
                capture_output=True, text=True, timeout=10, check=False,
            )
            if "SSH_OK" in r.stdout:
                elapsed = int(time.time() - start)
                log(f"SSH up! Total recovery time: {elapsed}s")
                return True
        except Exception:
            pass
        time.sleep(3)
    log(f"Router did not come back within {timeout}s.")
    return False


def start_pcap_capture(interface, output_path):
    proc = subprocess.Popen(
        ["sudo", "-n", "tcpdump", "-i", interface,
         "-w", output_path, "-n", "-U", "--buffer-size=16384"],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    time.sleep(1)
    if proc.poll() is not None:
        err = proc.stderr.read().decode()
        log(f"tcpdump failed to start: {err}")
        return None
    log(f"pcap capture → {output_path}")
    return proc


def stop_pcap_capture(proc, output_path):
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    if output_path and os.path.isfile(output_path):
        size_kb = os.path.getsize(output_path) / 1024
        log(f"pcap saved: {output_path} ({size_kb:.1f} KB)")


def verify_router():
    log("Verifying router state...")
    checks = []
    try:
        r = subprocess.run(
            ["ssh", "-o", "StrictHostKeyChecking=no",
             "-o", "UserKnownHostsFile=/dev/null",
             "-o", "ConnectTimeout=5",
             "-o", "PasswordAuthentication=no",
             f"root@{UBOOT_IP}",
             "echo hostname=$(cat /proc/sys/kernel/hostname); "
             "echo sshkey=$(wc -c < /etc/dropbear/authorized_keys); "
             "echo wan_ssh=$(uci show firewall | grep Allow-SSH-WAN | wc -l); "
             "echo uci_defaults=$(ls /etc/uci-defaults/ 2>/dev/null | wc -l); "
             "echo kernel=$(uname -r)"],
            capture_output=True, text=True, timeout=15, check=False,
        )
        for line in r.stdout.strip().split('\n'):
            if '=' in line:
                key, val = line.split('=', 1)
                checks.append((key, val))
                log(f"  {key}: {val}")
    except Exception as e:
        log(f"Verification failed: {e}")
    return checks


def main():
    parser = argparse.ArgumentParser(
        description="U-Boot recovery for GL.iNet GL-MT3000 with voice + pcap",
    )
    parser.add_argument("--image", required=True, help="Firmware image to upload")
    parser.add_argument("--interface", default=None,
                        help="Ethernet interface (auto-detected if omitted)")
    parser.add_argument("--no-voice", action="store_true", help="Disable voice guidance")
    parser.add_argument("--no-upload", action="store_true",
                        help="Stop after detecting U-Boot (dry run)")
    parser.add_argument("--capture", default=None,
                        help="Save pcap capture to file (requires sudo)")
    args = parser.parse_args()

    if not os.path.isfile(args.image):
        print(f"ERROR: image not found: {args.image}", file=sys.stderr)
        sys.exit(1)

    _say = (lambda m: None) if args.no_voice else say

    if not args.interface:
        for iface in ["en6", "en7", "en5", "en4", "en3", "en2", "en1", "en0"]:
            r = subprocess.run(
                ["ifconfig", iface], capture_output=True, text=True, check=False,
            )
            if "status: active" in r.stdout.lower() and "192.168.1." in r.stdout:
                args.interface = iface
                break
        if not args.interface:
            print("ERROR: could not auto-detect interface. Use --interface.", file=sys.stderr)
            sys.exit(1)

    pcap_path = args.capture
    pcap_proc = None
    if not pcap_path:
        pcap_path = os.path.join(tempfile.gettempdir(), "uboot-capture.pcap")

    log("GL-MT3000 U-Boot Recovery")
    log(f"Image:      {args.image}")
    log(f"Interface:  {args.interface}")
    log(f"Pcap:       {pcap_path}")
    log(f"LED signal: {UBOOT_LED_PATTERN}")
    print()

    if args.capture:
        pcap_proc = start_pcap_capture(args.interface, pcap_path)

    try:
        rc = _run_recovery(args, _say)
    except KeyboardInterrupt:
        log("Interrupted by user.")
        rc = 1
    finally:
        stop_pcap_capture(pcap_proc, pcap_path)

    return rc


def _run_recovery(args, _say):
    _say("Starting U-Boot recovery for the MT 3000. I will guide you step by step.")
    print()

    link_up = get_link_state(args.interface)
    if link_up:
        _say("Router is on. Unplug the power cable now. Keep the ethernet cable in the LAN port.")
        log("STEP 1: Unplug power (keep ethernet in LAN port)")
        if not wait_for_link_down(args.interface):
            _say("Timed out. Please unplug the power cable from the router.")
            return 1
        _say("Power disconnected. Good.")
    else:
        log("Router already powered off.")
        _say("Router is off. Good.")

    print()
    _say("Press and hold the reset button on the side, under the antenna. No paperclip needed.")
    log("STEP 2: Press and HOLD the reset button (side, under antenna)")
    time.sleep(4)

    _say("While still holding reset, plug in the power cable.")
    log("STEP 3: Plug in power WHILE STILL HOLDING reset")
    time.sleep(2)

    _say(f"Watch the LED. Blue flashes {UBOOT_FLASH_COUNT} times, then solid white. Release reset when it turns white.")
    log(f"Waiting: blue LED {UBOOT_FLASH_COUNT}x → solid white → release reset")
    print()

    if not wait_for_link_up(args.interface, timeout=30):
        _say("Ethernet link did not come up. Check the cable is in the LAN port.")
        return 1

    _say("Link up. Keep holding reset until the LED is solid white.")
    log("Link up — waiting for LED sequence...")
    time.sleep(8)

    _say("Release the reset button now. The LED should be solid white.")
    log("STEP 4: Release reset (LED should be SOLID WHITE)")
    print()

    _say("Scanning for U-Boot HTTP server...")
    if not wait_for_uboot(timeout=90):
        _say("U-Boot not found. Check the LED: solid white means try again with different timing. Flashing blue means it booted normally.")
        log("FAIL: U-Boot not detected. LED states:")
        log("  Solid white = U-Boot (network issue?)")
        log("  Flashing blue = normal boot (wrong timing)")
        log("  Off = not powered")
        return 1

    _say("U-Boot detected. Starting firmware upload.")
    print()

    if args.no_upload:
        _say("Dry run. U-Boot is ready but not uploading.")
        log("DRY RUN: U-Boot ready at http://192.168.1.1")
        log(f"  Upload: curl -F firmware=@{args.image} http://192.168.1.1/upload")
        log(f"  Flash:  curl http://192.168.1.1/flashing.html")
        return 0

    if not upload_firmware(args.image):
        _say("Upload failed. Try Chrome or Edge at http://192.168.1.1 (not Firefox).")
        return 1

    if not trigger_flash():
        log("Flash trigger may have failed. Router may still flash on its own.")

    _say("Firmware flashing. Wait about 4 minutes. Do not unplug.")
    print()

    if wait_for_reboot(args.interface):
        _say("Recovery complete! Router is back online.")
        log("SUCCESS — router recovered.")
        verify_router()
        return 0
    else:
        _say("Router is taking longer than expected. Check SSH in a few minutes.")
        log("TIMEOUT — router may still be booting.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
