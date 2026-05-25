#!/usr/bin/env bash
# sanitize-results.sh — Redact sensitive data from test results for public publication.
#
# Usage: sanitize-results.sh <run-dir> [output-dir]
#
# Accepts either a raw results directory OR a canonical run directory:
#   - If <run-dir> contains run.json, it is treated as a canonical run dir
#     and the script sanitizes raw/ and report/ subdirectories.
#   - If no output-dir is given, sanitizes in-place (writes redaction-report.json
#     in the run dir).
#   - Otherwise, copies and sanitizes into output-dir.
#
# Reads sensitive values from .env, environment variables, and run.json (if present).
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

IN_DIR="${1:?Usage: $0 <run-dir> [output-dir]}"
OUT_DIR="${2:-}"

if [ ! -d "$IN_DIR" ]; then
    echo "ERROR: directory not found: $IN_DIR" >&2
    exit 1
fi

# --- Detect canonical vs raw directory ---
CANONICAL=false
RUN_JSON=""
if [ -f "$IN_DIR/run.json" ]; then
    CANONICAL=true
    RUN_JSON="$IN_DIR/run.json"
fi

# --- Determine output dir ---
if [ -z "$OUT_DIR" ]; then
    # In-place: we work on the input dir directly
    OUT_DIR="$IN_DIR"
    INPLACE=true
else
    INPLACE=false
fi

# Determine the actual directory tree to scan for files
if [ "$CANONICAL" = true ]; then
    # For canonical dirs, we sanitize raw/ and report/ subdirs
    SCAN_DIRS=()
    [ -d "$IN_DIR/raw" ] && SCAN_DIRS+=("$IN_DIR/raw")
    [ -d "$IN_DIR/report" ] && SCAN_DIRS+=("$IN_DIR/report")
else
    # For raw dirs, scan everything
    SCAN_DIRS=("$IN_DIR")
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

# --- Gather sensitive values from .env / environment ---
ROUTER_IP="${TOLLGATE_SSH_HOST:-${ROUTER_IP:-}}"
ROUTER_PASSWORD="${TOLLGATE_LUCI_PASSWORD:-${ROUTER_PASSWORD:-}}"
PHONE_SERIAL="${PHONE_SERIAL:-}"
SSID="${TOLLGATE_SSID:-TollGate}"

# --- Read additional values from run.json if present ---
CONTAINER_MODE=false
if [ "$CANONICAL" = true ] && command -v python3 &>/dev/null; then
    # Extract router_ip from run.json lab section
    RUN_IP=$(python3 -c "
import json, sys
try:
    d = json.load(open('$RUN_JSON'))
    lab = d.get('lab', {})
    print(lab.get('router_ip', ''))
except: print('')
" 2>/dev/null || true)
    if [ -n "$RUN_IP" ] && [ -z "$ROUTER_IP" ]; then
        ROUTER_IP="$RUN_IP"
    fi

    # Check container/virtual_lab mode
    CONTAINER_MODE=$(python3 -c "
import json
d = json.load(open('$RUN_JSON'))
lab = d.get('lab', {})
ct = lab.get('client_type', '')
vl = lab.get('virtual_lab', False)
print('true' if ct == 'container' or vl else 'false')
" 2>/dev/null || echo "false")
fi

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

if [[ ${#ARGS[@]} -eq 0 ]]; then
    echo "WARNING: No redaction patterns configured. Output will not be sanitized." >&2
fi

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

# --- Process files ---
mkdir -p "$OUT_DIR"

FILE_COUNT=0
SCREENSHOTS_STRIPPED=0

for metafile in run.json summary.json; do
    if [ -f "$IN_DIR/$metafile" ]; then
        if [ "$INPLACE" = true ]; then
            tmp="$(mktemp)"
            sanitize_text "$IN_DIR/$metafile" "$tmp"
            mv "$tmp" "$OUT_DIR/$metafile"
        else
            sanitize_text "$IN_DIR/$metafile" "$OUT_DIR/$metafile"
        fi
        FILE_COUNT=$((FILE_COUNT + 1))
    fi
done

for SCAN_DIR in "${SCAN_DIRS[@]}"; do
    # Determine the relative prefix from IN_DIR
    while IFS= read -r -d '' file; do
        rel="${file#"$SCAN_DIR"/}"

        if [ "$INPLACE" = true ]; then
            dest="$SCAN_DIR/$rel"
        else
            # Map to output preserving structure
            # e.g. IN_DIR/raw/api/output.log -> OUT_DIR/raw/api/output.log
            subdir="${SCAN_DIR#"$IN_DIR"}"
            dest="$OUT_DIR${subdir}/$rel"
        fi

        mkdir -p "$(dirname "$dest")"

        case "$file" in
            *.html|*.htm|*.xml|*.log|*.txt|*.json)
                if [ "$INPLACE" = true ] && [ "$file" = "$dest" ]; then
                    tmp="$(mktemp)"
                    sanitize_text "$file" "$tmp"
                    mv "$tmp" "$dest"
                else
                    sanitize_text "$file" "$dest"
                fi
                ;;
            *.png|*.jpg|*.jpeg|*.gif|*.webp)
                if [ "$CONTAINER_MODE" = true ]; then
                    # Container/virtual_lab: preserve screenshots with EXIF stripped
                    strip_exif "$file" "$dest"
                else
                    # Phone/non-container: strip screenshots entirely
                    SCREENSHOTS_STRIPPED=$((SCREENSHOTS_STRIPPED + 1))
                fi
                ;;
            *.webm|*.mp4)
                if [ "$CONTAINER_MODE" = true ]; then
                    # Container/virtual_lab: copy videos through (no personal UI data)
                    cp "$file" "$dest"
                else
                    # Phone/non-container: strip videos (may contain personal UI data)
                    SCREENSHOTS_STRIPPED=$((SCREENSHOTS_STRIPPED + 1))
                fi
                ;;
            *)
                cp "$file" "$dest"
                ;;
        esac
        FILE_COUNT=$((FILE_COUNT + 1))
    done < <(find "$SCAN_DIR" -type f -print0)
done

# --- Remove image files from artifacts/ unless container/virtual_lab ---
if [ "$CANONICAL" = true ] && [ -d "$IN_DIR/artifacts" ]; then
    ARTIFACTS_OUT="${OUT_DIR}/artifacts"
    if [ "$CONTAINER_MODE" = true ]; then
        # Preserve all artifacts, strip EXIF on images
        mkdir -p "$ARTIFACTS_OUT"
        while IFS= read -r -d '' afile; do
            arel="${afile#"$IN_DIR/artifacts"/}"
            mkdir -p "$ARTIFACTS_OUT/$(dirname "$arel")"
            case "$afile" in
                *.png|*.jpg|*.jpeg|*.gif|*.webp)
                    strip_exif "$afile" "$ARTIFACTS_OUT/$arel"
                    ;;
                *)
                    cp "$afile" "$ARTIFACTS_OUT/$arel"
                    ;;
            esac
            FILE_COUNT=$((FILE_COUNT + 1))
        done < <(find "$IN_DIR/artifacts" -type f -print0)
    else
        # Copy non-image artifacts only
        mkdir -p "$ARTIFACTS_OUT"
        while IFS= read -r -d '' afile; do
            arel="${afile#"$IN_DIR/artifacts"/}"
            case "$afile" in
                *.png|*.jpg|*.jpeg|*.gif|*.webp)
                    SCREENSHOTS_STRIPPED=$((SCREENSHOTS_STRIPPED + 1))
                    continue
                    ;;
            esac
            mkdir -p "$ARTIFACTS_OUT/$(dirname "$arel")"
            cp "$afile" "$ARTIFACTS_OUT/$arel"
            FILE_COUNT=$((FILE_COUNT + 1))
        done < <(find "$IN_DIR/artifacts" -type f -print0)
    fi
fi

# --- Also strip phone serials from XML dumps in non-container mode ---
if [ "$CONTAINER_MODE" = false ] && [ -n "$PHONE_SERIAL" ]; then
    # Already handled by sed above for all text files
    :
fi

# --- Generate redaction report ---
cat > "$OUT_DIR/redaction-report.json" << EOF
{
  "timestamp": "$(date -u '+%Y-%m-%dT%H:%M:%SZ')",
  "input_dir": "$(cd "$IN_DIR" && pwd | sed -E 's|/Users/[^/]+|<local-path>|g; s|/home/[^/]+|<local-path>|g')",
  "output_dir": "$(cd "$OUT_DIR" && pwd | sed -E 's|/Users/[^/]+|<local-path>|g; s|/home/[^/]+|<local-path>|g')",
  "files_processed": $FILE_COUNT,
  "redactions": {
    "router_ip": $([ -n "$ROUTER_IP" ] && echo "true" || echo "false"),
    "password": $([ -n "$ROUTER_PASSWORD" ] && echo "true" || echo "false"),
    "phone_serial": $([ -n "$PHONE_SERIAL" ] && echo "true" || echo "false"),
    "mac_addresses": true,
    "cashu_tokens": true,
    "local_paths": true,
    "screenshots_stripped": $SCREENSHOTS_STRIPPED
  },
  "container_mode": $CONTAINER_MODE,
  "notes": [
    "Sanitized for public publication",
    "Screenshots: EXIF stripped. Visual content (SSIDs, IPs in UI) NOT auto-redacted.",
    "Review sanitized output before publishing."
  ]
}
EOF

echo "==> Sanitized ${FILE_COUNT} files (${SCREENSHOTS_STRIPPED} screenshots stripped)"
echo "==> Output: ${OUT_DIR}/"
echo ""
echo "==> Review before publishing:"
echo "    open ${OUT_DIR}/report/index.html 2>/dev/null || xdg-open ${OUT_DIR}/report/index.html 2>/dev/null || true"
echo ""
echo "==> To publish: ./scripts/publish-report.sh $OUT_DIR"
