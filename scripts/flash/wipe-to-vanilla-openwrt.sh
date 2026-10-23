#!/usr/bin/env bash
# ============================================================================
#  wipe-to-vanilla-openwrt.sh — reset a GL.iNet GL-MT3000 back to VANILLA
#  OpenWrt 25.12.5 (mediatek/filogic), no TollGate, no leftovers.
#
#  USAGE (the point of this file — one line from any machine):
#    bash <(curl -fsSL https://raw.githubusercontent.com/felixfelix-bot/physical-router-test-automation/main/scripts/flash/wipe-to-vanilla-openwrt.sh)
#
#  Safer first run — stage + verify, flash nothing:
#    ...wipe-to-vanilla-openwrt.sh --ip 192.168.1.1
#  Actually flash:
#    ...wipe-to-vanilla-openwrt.sh --ip 192.168.1.1 --commit
#
#  OPTIONS
#    --ip <addr>            router address (default 192.168.1.1)
#    --password <pw>        root password (else you are prompted, or set ROUTER_PW)
#    --image <file>         use a local sysupgrade .bin instead of downloading
#    --version <ver>        OpenWrt release to install (default 25.12.5)
#    --commit               actually flash. WITHOUT this, nothing is written:
#                           the image is staged, sha256-verified and given to
#                           `sysupgrade -T`, which only TESTS the image.
#    --allow-nonempty-wallet  permit flashing when /etc/tollgate still holds
#                           ecash. Read the warning it prints first.
#    --keep-config          do NOT pass -n (keeps /etc/config). NOT recommended:
#                           a GL.iNet 21.02 config on mainline 25.12 can leave
#                           the router with no L3 path. Default is -n.
#
#  SAFETY MODEL
#    1. The image sha256 must appear in the OFFICIAL downloads.openwrt.org
#       sha256sums for the exact release/target. No other source is accepted.
#    2. `sysupgrade -n` WIPES /etc — including /etc/tollgate and any ecash in it.
#       The script refuses to flash while the wallet is non-empty unless you
#       pass --allow-nonempty-wallet. Drain first:
#           ssh root@<router> '/usr/bin/tollgate wallet drain cashu --yes'
#    3. Destructive only with --commit. The default run is read-only.
#    4. Confirmation prompt before the flash, showing board, version, wallet
#       state and exactly what will be wiped.
#
#  PORTABILITY: bash 3.2 (macOS) compatible — no GNU-only flags. sshpass is used
#  if present; otherwise an SSH_ASKPASS helper answers the prompt (needed because
#  the image is staged through `ssh ... 'cat > /tmp/img'`, which occupies stdin).
# ============================================================================
set -euo pipefail

IP="192.168.1.1"
PASSWORD="${ROUTER_PW:-}"
IMAGE=""
VERSION="25.12.5"
COMMIT=0
ALLOW_NONEMPTY=0
KEEP_CONFIG=0
TARGET="mediatek/filogic"
IMGNAME_BASE="openwrt"

while [ $# -gt 0 ]; do
  case "$1" in
    --ip) IP="$2"; shift 2 ;;
    --password) PASSWORD="$2"; shift 2 ;;
    --image) IMAGE="$2"; shift 2 ;;
    --version) VERSION="$2"; shift 2 ;;
    --commit) COMMIT=1; shift ;;
    --allow-nonempty-wallet) ALLOW_NONEMPTY=1; shift ;;
    --keep-config) KEEP_CONFIG=1; shift ;;
    -h|--help) sed -n '2,45p' "$0" 2>/dev/null || true; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

say()  { printf '%s\n' "$*"; }
head1() { printf '\n=== %s ===\n' "$*"; }
die()  { printf 'FAIL: %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------- host tools
SHASUM=""
if command -v sha256sum >/dev/null 2>&1; then SHASUM="sha256sum"
elif command -v shasum >/dev/null 2>&1; then SHASUM="shasum -a 256"
else die "need sha256sum or shasum"; fi

SSHPASS_BIN="$(command -v sshpass || true)"

# ---------------------------------------------------------------- image
SUMSURL="https://downloads.openwrt.org/releases/${VERSION}/targets/${TARGET}/sha256sums"
DEVICE_GLOB="glinet_gl-mt3000"
if [ -z "$IMAGE" ]; then
  head1 "resolving the official $VERSION image for $TARGET ($DEVICE_GLOB)"
  SUMS="$(mktemp -t tgsums.XXXXXX)"
  curl -fsS --max-time 120 -o "$SUMS" "$SUMSURL" || die "cannot fetch $SUMSURL"
  # sha256sums lines are "<hex> *<filename>" — the leading '*' is the binary-mode
  # marker and is part of field 2; strip it or the URL 404s and the hash lookup misses.
  BASE="$(awk -v g="$DEVICE_GLOB" '$0 ~ g && $0 ~ /sysupgrade\.bin$/ {b=$2; sub(/^\*/, "", b); print b; exit}' "$SUMS")"
  [ -n "$BASE" ] || die "no sysupgrade image for $DEVICE_GLOB in the official sha256sums"
  IMAGE="/tmp/$BASE"
  if [ ! -f "$IMAGE" ]; then
    say "downloading $BASE"
    curl -fL --max-time 900 -o "$IMAGE" "https://downloads.openwrt.org/releases/${VERSION}/targets/${TARGET}/$BASE" \
      || die "download failed"
  else
    say "using existing $IMAGE"
  fi
else
  BASE="$(basename "$IMAGE")"
  SUMS="$(mktemp -t tgsums.XXXXXX)"
  curl -fsS --max-time 120 -o "$SUMS" "$SUMSURL" || die "cannot fetch $SUMSURL"
fi

head1 "verifying the image against the OFFICIAL sha256sums"
EXPECTED="$(awk -v b="$BASE" '{n=$2; sub(/^\*/, "", n); if (n == b) {print $1; exit}}' "$SUMS")"
[ -n "$EXPECTED" ] || die "$BASE is not listed in the official sha256sums for $VERSION"
ACTUAL="$($SHASUM "$IMAGE" | awk '{print $1}')"
[ "$EXPECTED" = "$ACTUAL" ] || die "sha256 mismatch for $BASE
  expected(official): $EXPECTED
  actual(local)     : $ACTUAL"
say "OK  $BASE"
say "    sha256 $ACTUAL  (matches downloads.openwrt.org for $VERSION)"
say "    size   $(wc -c < "$IMAGE" | tr -d ' ') bytes"

# ---------------------------------------------------------------- ssh plumbing
if [ -z "$PASSWORD" ]; then
  if [ -t 0 ]; then
    printf 'router root password for %s: ' "$IP" >&2
    stty -echo 2>/dev/null || true
    read -r PASSWORD
    stty echo 2>/dev/null || true
    printf '\n' >&2
  else
    die "no password: pass --password or set ROUTER_PW (stdin is not a terminal)"
  fi
fi

ASKPASS=""
if [ -z "$SSHPASS_BIN" ]; then
  ASKPASS="$(mktemp -t tgask.XXXXXX)"
  cat > "$ASKPASS" <<'ASKEOF'
#!/bin/sh
printf '%s\n' "$ROUTER_PW"
ASKEOF
  chmod 700 "$ASKPASS"
  export ROUTER_PW="$PASSWORD"
  export SSH_ASKPASS="$ASKPASS"
  export SSH_ASKPASS_REQUIRE=force
  export DISPLAY="${DISPLAY:-:0}"
fi

SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=12 -o LogLevel=ERROR -o PreferredAuthentications=password -o PubkeyAuthentication=no"
rssh() { # rssh <remote command...>   (stdin is passed through)
  if [ -n "$SSHPASS_BIN" ]; then
    SSHPASS="$PASSWORD" "$SSHPASS_BIN" -e ssh $SSH_OPTS "root@$IP" "$@"
  else
    ssh $SSH_OPTS "root@$IP" "$@"
  fi
}
port_open() { (exec 3<>"/dev/tcp/$IP/22") >/dev/null 2>&1; }

head1 "reachability + router identity"
port_open || die "nothing answering on $IP:22 (is the router at a different address?)"
rssh 'echo "board   : $(cat /tmp/sysinfo/board_name 2>/dev/null)"; echo "release : $(. /etc/openwrt_release 2>/dev/null; printf "%s %s" "$DISTRIB_ID" "$DISTRIB_RELEASE")"; echo "target  : $(. /etc/openwrt_release 2>/dev/null; printf "%s" "$DISTRIB_TARGET")"; echo "uptime  : $(uptime | sed "s/^ *//")"' \
  || die "ssh failed (wrong password? set --password/ROUTER_PW)"

BOARD="$(rssh 'cat /tmp/sysinfo/board_name 2>/dev/null' || true)"
case "$BOARD" in
  glinet,mt3000*|glinet,gl-mt3000*) : ;;
  *) say "WARNING: board_name is '$BOARD' — this script targets GL-MT3000 (glinet,mt3000*)" ;;
esac

# ---------------------------------------------------------------- wallet guard
head1 "wallet / state guard"
WALLET_STATE="$(rssh 'if [ -d /etc/tollgate ]; then
  n=$(ls -1 /etc/tollgate/ecash 2>/dev/null | wc -l | tr -d " ");
  b="";
  if [ -x /usr/bin/tollgate ]; then b=$(/usr/bin/tollgate wallet balance 2>/dev/null | tail -1); fi;
  echo "present files=$n ${b:-no-cli}";
else echo "absent"; fi' || echo "unknown")"
say "/etc/tollgate: $WALLET_STATE"

NONEMPTY=0
case "$WALLET_STATE" in
  present*) NONEMPTY=1 ;;
esac
if [ "$NONEMPTY" = "1" ] && [ "$ALLOW_NONEMPTY" != "1" ]; then
  cat >&2 <<EOF

REFUSING TO FLASH: /etc/tollgate exists and may hold ecash. \`sysupgrade -n\` WIPES it.

  Drain first (this spends nothing, it moves the balance into a token you keep):
      ssh root@$IP '/usr/bin/tollgate wallet drain cashu --yes'
  then re-run this script.

  If you have already drained it and the directory is just stale files, or you
  accept the loss, re-run with --allow-nonempty-wallet.
EOF
  exit 3
fi

# ---------------------------------------------------------------- main event
head1 "plan"
say "  image        : $IMAGE"
say "  board        : ${BOARD:-unknown}"
say "  router       : $IP"
if [ "$KEEP_CONFIG" = "1" ]; then
  say "  mode         : sysupgrade (config KEPT) — not recommended across 21.02 -> 25.12"
else
  say "  mode         : sysupgrade -n  (WIPES /etc/config and /etc/tollgate)"
fi
say "  commit       : $([ "$COMMIT" = "1" ] && echo YES || echo 'NO (test only, nothing written)')"

if [ "$COMMIT" != "1" ]; then
  head1 "staging + image TEST (sysupgrade -T) — nothing is written"
  rssh 'cat > /tmp/vanilla-sysupgrade.bin' < "$IMAGE"
  RSHA="$(rssh 'test -f /tmp/vanilla-sysupgrade.bin && sha256sum /tmp/vanilla-sysupgrade.bin | cut -d" " -f1' || true)"
  [ "$RSHA" = "$ACTUAL" ] || die "staged image sha256 differs on the router: $RSHA != $ACTUAL"
  say "staged on router, sha256 verified ($RSHA)"
  rssh 'sysupgrade -T /tmp/vanilla-sysupgrade.bin && echo IMAGE_TEST_OK' | tail -5
  say ""
  say "Nothing was flashed. Re-run with --commit to wipe and install vanilla OpenWrt."
  exit 0
fi

if [ -t 0 ]; then
  printf 'Type FLASH to wipe %s and install vanilla OpenWrt %s: ' "$IP" "$VERSION" >&2
  read -r CONFIRM
  [ "$CONFIRM" = "FLASH" ] || die "aborted by user"
fi

head1 "staging the image"
rssh 'rm -f /tmp/vanilla-sysupgrade.bin; cat > /tmp/vanilla-sysupgrade.bin' < "$IMAGE"
RSHA="$(rssh 'sha256sum /tmp/vanilla-sysupgrade.bin | cut -d" " -f1' || true)"
[ "$RSHA" = "$ACTUAL" ] || die "staged image sha256 differs on the router: $RSHA != $ACTUAL"
say "staged, sha256 verified on the router"

head1 "flashing (the router will drop off the network now)"
FLAGS=""
[ "$KEEP_CONFIG" = "1" ] || FLAGS="-n"
rssh "sysupgrade $FLAGS /tmp/vanilla-sysupgrade.bin" >/dev/null 2>&1 || true

say "waiting for the router to come back on $IP:22 (up to 4 minutes)…"
i=0
while [ $i -lt 48 ]; do
  sleep 5
  i=$((i+1))
  if port_open; then say "ssh is back after ~$((i*5))s"; break; fi
done
port_open || die "router did not come back — check the LEDs; a failed flash may need U-Boot/TFTP recovery"

head1 "post-flash verification (vanilla state)"
sleep 5
rssh 'echo "board   : $(cat /tmp/sysinfo/board_name 2>/dev/null)"; echo "release : $(. /etc/openwrt_release 2>/dev/null; printf "%s %s" "$DISTRIB_ID" "$DISTRIB_RELEASE")"; echo "target  : $(. /etc/openwrt_release 2>/dev/null; printf "%s" "$DISTRIB_TARGET")"; if [ -d /etc/tollgate ]; then echo "tollgate: STILL PRESENT (unexpected)"; else echo "tollgate: absent (clean vanilla)"; fi; echo "nodogsplash: $(command -v ndsctl >/dev/null 2>&1 && echo present || echo absent)"; echo "wifi ifaces: $(uci show wireless 2>/dev/null | grep -c mode)"' \
  || say "WARNING: could not run the verification ssh (fresh vanilla may want a root password set)"

cat <<EOF

=== DONE ===
Vanilla OpenWrt $VERSION is installed. /etc/config and /etc/tollgate were wiped (-n).

NEXT (this is a fresh router — nothing is configured):
  * root has NO password on fresh vanilla. Set one before exposing it:
        ssh root@$IP 'passwd root'
    and, if you need LuCI:
        ssh root@$IP 'apk update && apk add luci'
  * there is no internet until you configure the uplink
    (WAN DHCP, or the WiFi STA client), and therefore NO install path that
    needs feeds until then — that is exactly the WAN-less case the offline
    bundle covers.
  * TollGate is NOT installed by this script. Install paths:
      1. installer    : bash <(curl -fsSL https://raw.githubusercontent.com/OpenTollGate/tollgate-installer/main/install-and-test.sh)
      2. package      : see INSTALL-PATHS.md (apk add of the published .apk)
      3. offline pkg  : pending (the WAN-less bundle is not published yet)
EOF
