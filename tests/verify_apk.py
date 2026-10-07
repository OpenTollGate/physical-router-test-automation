#!/usr/bin/env python3
"""Prove a shipped OpenWrt package (.apk or .ipk) carries the #680 brand guard.

Usage: verify_apk.py <pkg> [<pkg> ...]

Container formats differ (apk-tools v3 "ADB" for .apk, gzipped tar-of-tars for
the .ipk this feed ships), so instead of parsing containers this decompresses
EVERY gzip stream it can find, recursively, and looks for the byte-exact fix
from PR #680 — `[ -r /etc/tollgate/brand ]` inside the 99-tollgate-setup script.
An artifact predating it (pre23, pinned at cec22228) must come back FAIL; that
is the A/B control.
"""
import sys
import tarfile
import zlib

GUARD = b"[ -r /etc/tollgate/brand ]"
TARGET = "99-tollgate-setup"
MAX_DEPTH = 4


def gunzip_all(blob, depth=MAX_DEPTH):
    """Every gzip member found in blob, recursively, as (offset, bytes)."""
    out, off = [], 0
    while True:
        i = blob.find(b"\x1f\x8b", off)
        if i < 0:
            break
        try:
            d = zlib.decompressobj(31)
            out.append((i, d.decompress(blob[i:]) + d.flush()))
        except Exception:                      # noqa: BLE001 — random magic hits
            pass
        off = i + 2
    if depth:
        for _, data in list(out):
            out += gunzip_all(data, depth - 1)
    return out


def adb_decode(blob):
    """apk-tools v3 "ADB" container: a raw-deflate segment right after the magic.

    The magic is `ADBd` and the payload is NOT a gzip member, so a plain
    gzip-only scan sees nothing (this is why an .apk first reported
    scripts-found=0). Try the few plausible header offsets with wbits=-15.
    """
    if blob[:3] != b"ADB":
        return None
    for off in (4, 8, 12, 16, 20, 24, 32):
        try:
            d = zlib.decompressobj(-15)
            out = d.decompress(blob[off:]) + d.flush()
        except Exception:                      # noqa: BLE001
            continue
        if len(out) > 1024:
            return out
    return None


def main(paths):
    worst = 0
    for path in [p for p in paths if p]:
        blob = open(path, "rb").read()
        decoded = adb_decode(blob)
        print(f"  {path}  ({len(blob)} B)"
              f"{'  [ADB container decoded: ' + str(len(decoded)) + ' B]' if decoded else ''}")
        streams = [(0, blob)] + gunzip_all(blob)
        if decoded:
            streams += [(0, decoded)] + gunzip_all(decoded)
        guard_hits, script_hits = 0, 0
        for _, data in streams:
            if TARGET.encode() in data and GUARD in data:
                guard_hits += 1
                for line in data.decode(errors="replace").splitlines():
                    if "tollgate/brand" in line:
                        print("      " + line.strip())
                        break
            if TARGET.encode() in data:
                script_hits += 1
        # tar members are a second, independent view of the same payload
        for _, data in streams:
            try:
                tf = tarfile.open(fileobj=__import__("io").BytesIO(data))
            except Exception:                  # noqa: BLE001
                continue
            for m in tf.getmembers():
                if m.isfile() and m.name.endswith(TARGET):
                    body = tf.extractfile(m).read()
                    script_hits += 1
                    if GUARD in body:
                        guard_hits += 1
        verdict = "PASS" if guard_hits else "FAIL"
        print(f"    decompressed streams={len(streams)} "
              f"scripts-found={script_hits} with-guard={guard_hits} -> {verdict}")
        if not guard_hits:
            worst = 1
    return worst


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
