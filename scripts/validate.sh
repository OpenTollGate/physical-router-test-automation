#!/usr/bin/env bash
# validate.sh — one entry point for validating any TollGate build.
#
#   scripts/validate.sh --tag v0.6.0-rc6           # official release tag
#   scripts/validate.sh --pr 123                   # any PR (resolves head SHA)
#   scripts/validate.sh --ref abc1234              # any commit
#   scripts/validate.sh --artifact path/to/ipk     # pre-built artifact
#
# All paths share the same pipeline via lib/venue.py:
#   resolve source → build (if needed) → venue ensure → deploy → gate → suite
#
# Venue selection:
#   --venue virtual (default) | bay --address 192.168.105.1 | island
#
# Session-safe: self-launches under a transient systemd --user unit.
set -uo pipefail

REPO_ROOT=$(git -C "$(dirname -- "$0")" rev-parse --show-toplevel)
cd "$REPO_ROOT"

if [ "${TOLLGATE_VALIDATE_IN_SYSTEMD:-}" != "1" ] && [ -n "${XDG_RUNTIME_DIR:-}" ] \
    && command -v systemd-run >/dev/null 2>&1; then
    exec systemd-run --user --collect --same-dir --pipe --wait \
        --setenv=TOLLGATE_VALIDATE_IN_SYSTEMD=1 \
        --unit="validate-$(date +%s)" "$0" "$@"
fi

# ── Args ───────────────────────────────────────────────────────────────────
TAG="" ; PR="" ; REF="" ; ARTIFACT=""
VENUE_TYPE="virtual" ; VENUE_ADDRESS="" ; VENUE_PASSWORD=""
SKIP_BUILD=false ; SKIP_SUITE=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --tag)          TAG="$2"; shift 2 ;;
        --pr)           PR="$2"; shift 2 ;;
        --ref)          REF="$2"; shift 2 ;;
        --artifact)     ARTIFACT="$2"; shift 2 ;;
        --venue)        VENUE_TYPE="$2"; shift 2 ;;
        --address)      VENUE_ADDRESS="$2"; shift 2 ;;
        --password)     VENUE_PASSWORD="$2"; shift 2 ;;
        --skip-build)   SKIP_BUILD=true; shift ;;
        --skip-suite)   SKIP_SUITE=true; shift ;;
        *) echo "unknown: $1" >&2; exit 1 ;;
    esac
done

# ── Resolve source ─────────────────────────────────────────────────────────
TMBG="${TOLLGATE_TMBG_CHECKOUT:-$HOME/src/tollgate-module-basic-go}"
NODE_BIN="${TOLLGATE_NODE_BIN:-$HOME/laneA/tools/node-v22.17.0-linux-x64/bin}"
GO_PIN="${TOLLGATE_GO_PIN:-go1.26.8}"
FEED_API="https://api.github.com/repos/FreedomTechFeed/packages/releases?per_page=30"
PY="${TOLLGATE_PYTHON:-$(command -v python3)}"

STAMP=$(date -u +%Y%m%dT%H%M%SZ)
OUT="results/validate-$STAMP"
mkdir -p "$OUT"
log() { echo "[validate $(date +%H:%M:%S)] $*" | tee -a "$OUT/validate.log"; }
fail() { echo "[validate] FAIL: $*" | tee -a "$OUT/validate.log" >&2; exit 1; }

if [ -n "$PR" ]; then
    log "resolving PR #$PR → head SHA"
    REF=$(gh pr view "$PR" -R OpenTollGate/tollgate-module-basic-go \
        --json headRefOid --jq .headRefOid 2>/dev/null) \
        || fail "could not resolve PR #$PR"
    TAG="${TAG:-PR-${PR}}"
    log "PR #$PR → commit $REF, label $TAG"
fi

COMMIT="$REF"
if [ -n "$TAG" ] && [ -z "$COMMIT" ]; then
    if [ "$TAG" = "PR-"* ] || [ -z "$TAG" ]; then
        fail "no source to build from"
    fi
    log "resolving source commit from feed tag $TAG"
    AURL=$(curl -fsS --max-time 20 "$FEED_API" | "$PY" -c "
import json,sys
rs=[r for r in json.load(sys.stdin) if r.get('tag_name')=='$TAG']
for r in rs:
    for a in r.get('assets',[]):
        n=a['name']
        if 'aarch64_cortex-a53' in n and n.endswith('.ipk'):
            print(a['browser_download_url']); raise SystemExit
" 2>/dev/null) || true
    [ -n "$AURL" ] || fail "no aarch64 asset for $TAG"
    PROBE=$(mktemp -d)
    curl -fsSL --max-time 60 -o "$PROBE/p.ipk" "$AURL" || fail "feed ipk download failed"
    (cd "$PROBE" && tar xzf p.ipk ./data.tar.gz 2>/dev/null && tar xzf data.tar.gz ./usr/bin/tollgate-wrt 2>/dev/null) || true
    STAMPED=$(strings "$PROBE/usr/bin/tollgate-wrt" 2>/dev/null | grep -m1 -oE 'g[0-9a-f]{7,10}' | tr -d g || true)
    COMMIT=$(printf '%s' "$STAMPED" | grep -oE '[0-9a-f]{7,10}' || true)
    rm -rf "$PROBE"
    [ -n "$COMMIT" ] || fail "could not resolve source commit from feed ipk"
    log "feed stamp → commit $COMMIT"
fi

# ── Build (or use provided artifact) ───────────────────────────────────────
if [ -n "$ARTIFACT" ]; then
    [ -f "$ARTIFACT" ] || fail "no artifact at $ARTIFACT"
    log "using provided artifact: $ARTIFACT"
elif [ "$SKIP_BUILD" = true ]; then
    ARTIFACT=$(ls -t "$TMBG"/packaging/tollgate-wrt_*_x86_64.ipk 2>/dev/null | head -1)
    [ -n "$ARTIFACT" ] || fail "no pre-built x86_64 ipk found"
    log "using latest build: $ARTIFACT"
else
    [ -n "$COMMIT" ] || fail "no source commit to build from"
    case "$VENUE_TYPE" in
        bay|island) BUILD_ARCH=mipsel_24kc ;;
        *) BUILD_ARCH=x86_64 ;;
    esac
    log "building $BUILD_ARCH from $COMMIT"
    git -C "$TMBG" fetch origin main -q || fail "tmbg fetch failed"
    git -C "$TMBG" checkout -q "$COMMIT" || fail "checkout $COMMIT failed"
    (cd "$TMBG" && export PATH="$NODE_BIN:$PATH" \
        && make portal-build \
        && ARCH=$BUILD_ARCH GOTOOLCHAIN="$GO_PIN" PKG_VERSION="${TAG:-dev}" \
           bash packaging/local-build-ipk.sh) >> "$OUT/build.log" 2>&1 \
        || fail "build failed (see $OUT/build.log)"
    if [ "$BUILD_ARCH" = "x86_64" ]; then
        ARTIFACT=$(ls -t "$TMBG"/packaging/tollgate-wrt_*_x86_64.ipk | head -1)
    else
        # mipsel builds via the SDK lane
        EPOCH=$(git -C "$TMBG" log -1 --format=%ct "$COMMIT")
        sudo -n systemd-run --collect --wait --pipe --unit="tg-build-$$" \
            /bin/bash -c "export PATH=/home/user/.local/share/mise/installs/go/1.27.1/bin:/home/user/laneA/tools/node-v22.17.0-linux-x64/bin:\$PATH; export HOME=/root SOURCE_DATE_EPOCH=$EPOCH TG_GIT_COMMIT=${COMMIT:0:7}; cd $TMBG && SDK_TAG=ramips-mt7621-25.12.0 PACKAGE_FORMAT=apk PACKAGE_VERSION=${TAG:-dev} GOTOOLCHAIN=$GO_PIN bash scripts/build-sdk-package.sh" >> "$OUT/build.log" 2>&1 \
            || fail "mipsel build failed (see $OUT/build.log)"
        ARTIFACT=$(sudo -n ls -t /tmp/tollgate-build-artifacts/apk-ramips-mt7621-25.12.0/*.apk 2>/dev/null | head -1)
        [ -n "$ARTIFACT" ] || fail "mipsel artifact not found after build"
    fi
    log "built: $ARTIFACT"
fi

# ── Venue + deploy + suite (via lib/venue.py) ──────────────────────────────
log "venue: $VENUE_TYPE${VENUE_ADDRESS:+ @ $VENUE_ADDRESS}"
VARGS="--type $VENUE_TYPE"
[ -n "$VENUE_ADDRESS" ] && VARGS="$VARGS --address $VENUE_ADDRESS"
[ -n "$VENUE_PASSWORD" ] && VARGS="$VARGS --password $VENUE_PASSWORD"

"$PY" -m lib.venue ensure $VARGS >> "$OUT/venue.log" 2>&1 \
    || fail "venue ensure failed (see $OUT/venue.log)"

"$PY" -m lib.venue deploy $VARGS --artifact "$ARTIFACT" >> "$OUT/deploy.log" 2>&1 \
    || fail "deploy failed (see $OUT/deploy.log)"
log "health gate green"

if [ "$SKIP_SUITE" = true ]; then
    log "VERDICT: deploy-green (suite skipped)"
    exit 0
fi

"$PY" -m lib.venue suite $VARGS --test tests/api/test_payment_rejection_semantics.py \
    >> "$OUT/suite.log" 2>&1
RC=$?
if [ $RC -eq 0 ]; then
    log "VERDICT: GREEN"
else
    log "VERDICT: RED (rc=$RC — see $OUT/suite.log)"
fi
exit $RC
