#!/usr/bin/env bash
# sanitize-results.sh — Redact sensitive data from test results for public publication.
#
# Usage: sanitize-results.sh <raw-dir> <output-dir>
#
# Reads sensitive values from .env or environment variables and replaces them
# with placeholder tokens in all text files. Screenshots are copied as-is
# (EXIF stripped where possible; visual content not auto-redacted).
#
# Sanitizes:
#   - Router IP address and subnet
#   - MAC addresses (any xx:xx:xx:xx:xx:xx)
#   - WiFi SSID
#   - Phone serial number
#   - Cashu token strings (cashuA..., cashuB...)
#   - Local filesystem paths (/Users/..., /home/...)
#   - Router password
#
set -euo pipefail

RAW_DIR="${1:?Usage: $0 <raw-dir> <output-dir>}"
OUT_DIR="${2:?Usage: $0 <raw-dir> <output-dir>}"

if [ ! -d "$RAW_DIR" ]; then
    echo "ERROR: raw directory not found: $RAW_DIR" >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# Load .env if present
if [ -f "$REPO_DIR/.env" ]; then
    set -a
    # shellcheck disable=SC1090
    source "$REPO_DIR/.env"
    set +a
fi

# --- Gather sensitive values ---
ROUTER_IP="${TOLLGATE_SSH_HOST:-${ROUTER_IP:-}}"
ROUTER_PASSWORD="${TOLLGATE_LUCI_PASSWORD:-${ROUTER_PASSWORD:-}}"
PHONE_SERIAL="${PHONE_SERIAL:-}"
SSID="${TOLLGATE_SSID:-TollGate}"

# --- Build sed expressions (all extended regex via -E) ---
# Escape a string for use in sed replacement (handle / & |)
esc() { printf '%s' "$1" | sed 's/[\/&|]/\\&/g'; }

ARGS=()

# Router IP — also matches any host on the same /24 subnet
if [ -n "$ROUTER_IP" ]; then
    E_IP=$(esc "$ROUTER_IP")
    ARGS+=(-e "s|${E_IP}|<router-ip>|g")
    # Subnet: replace first three octets + any fourth octet
    SUBNET=$(printf '%s' "$ROUTER_IP" | sed -E 's/\.[0-9]+$//')
    E_SUB=$(esc "$SUBNET")
    ARGS+=(-e "s|${E_SUB}\.[0-9]+|<client-ip>|g")
fi

# Password
if [ -n "$ROUTER_PASSWORD" ]; then
    E_PW=$(esc "$ROUTER_PASSWORD")
    ARGS+=(-e "s|${E_PW}|<redacted:password>|g")
fi

# Phone serial
if [ -n "$PHONE_SERIAL" ]; then
    E_SN=$(esc "$PHONE_SERIAL")
    ARGS+=(-e "s|${E_SN}|<phone-serial>|g")
fi

# SSID
E_SSID=$(esc "$SSID")
ARGS+=(-e "s|${E_SSID}|<ssid>|g")

# MAC addresses (xx:xx:xx:xx:xx:xx)
ARGS+=(-e 's|[0-9a-fA-F]{2}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}|<mac>|g')

# Cashu tokens (V3 cashuA..., V4 cashuB...)
ARGS+=(-e 's|cashuA[a-zA-Z0-9+/=_-]{20,}|<redacted:token>|g')
ARGS+=(-e 's|cashuB[a-zA-Z0-9+/=_-]{20,}|<redacted:token>|g')

# Local filesystem paths
ARGS+=(-e 's|/Users/[^/ '"'"'	]+|<local-path>|g')
ARGS+=(-e 's|/home/[^/ '"'"'	]+|<local-path>|g')

# --- Helper functions ---
sanitize_text() {
    sed -E "${ARGS[@]}" "$1" > "$2"
}

strip_exif() {
    # macOS: sips can strip metadata from images
    if command -v sips &>/dev/null; then
        cp "$1" "$2"
        sips --setProperty all '' "$2" &>/dev/null || true
    # Linux: ImageMagick convert -strip
    elif command -v convert &>/dev/null; then
        convert "$1" -strip "$2" 2>/dev/null || cp "$1" "$2"
    else
        cp "$1" "$2"
    fi
}

# --- Process all files ---
mkdir -p "$OUT_DIR"

FILE_COUNT=0
while IFS= read -r -d '' file; do
    rel="${file#"$RAW_DIR"/}"
    mkdir -p "$OUT_DIR/$(dirname "$rel")"

    case "$file" in
        *.html|*.htm|*.xml|*.log|*.txt|*.json)
            sanitize_text "$file" "$OUT_DIR/$rel"
            ;;
        *.png|*.jpg|*.jpeg|*.gif|*.webp)
            # Screenshots: strip EXIF metadata. Visual content is NOT auto-redacted.
            strip_exif "$file" "$OUT_DIR/$rel"
            ;;
        *)
            cp "$file" "$OUT_DIR/$rel"
            ;;
    esac
    FILE_COUNT=$((FILE_COUNT + 1))
done < <(find "$RAW_DIR" -type f -print0)

# --- Generate redaction report ---
cat > "$OUT_DIR/redaction-report.json" << EOF
{
  "timestamp": "$(date -u '+%Y-%m-%dT%H:%M:%SZ')",
  "files_processed": $FILE_COUNT,
  "redactions": {
    "router_ip": $([ -n "$ROUTER_IP" ] && echo "true" || echo "false"),
    "password": $([ -n "$ROUTER_PASSWORD" ] && echo "true" || echo "false"),
    "phone_serial": $([ -n "$PHONE_SERIAL" ] && echo "true" || echo "false"),
    "ssid": true,
    "mac_addresses": true,
    "cashu_tokens": true,
    "local_paths": true
  },
  "notes": [
    "Screenshots: EXIF stripped. Visual content (SSIDs, IPs in UI) NOT auto-redacted.",
    "Review sanitized output before publishing."
  ]
}
EOF

echo "==> Sanitized ${FILE_COUNT} files"
echo "==> Output: ${OUT_DIR}/"
echo ""
echo "==> Review before publishing:"
echo "    open ${OUT_DIR}/report.html 2>/dev/null || xdg-open ${OUT_DIR}/report.html"
echo ""
echo "==> To publish: ./scripts/publish-report.sh <commit-hash> ${OUT_DIR}"
