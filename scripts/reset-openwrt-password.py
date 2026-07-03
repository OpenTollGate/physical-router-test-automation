#!/usr/bin/env python3
"""Reset the OpenWrt VM root password via the serial console socket.

Usage: reset-openwrt-password.py <serial_sock> <password>
"""
import socket
import sys
import time


def send_and_wait(sock, data, wait=1.0):
    sock.sendall(data.encode() if isinstance(data, str) else data)
    time.sleep(wait)
    chunks = []
    try:
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
    except socket.timeout:
        pass
    return b"".join(chunks).decode(errors="replace")


def main():
    sock_path = sys.argv[1]
    password = sys.argv[2]
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(3)
    s.connect(sock_path)
    # Wake up the console
    send_and_wait(s, "\n", 1)
    send_and_wait(s, "\n", 1)
    # Set root password using printf piping (BusyBox passwd reads stdin)
    cmd = f"printf '%s\\n%s\\n' '{password}' '{password}' | passwd root\n"
    result = send_and_wait(s, cmd, 2)
    print(result[-300:] if len(result) > 300 else result)
    s.close()
    print("OK: password reset attempted")


if __name__ == "__main__":
    main()
