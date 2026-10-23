#!/usr/bin/env bash
# tag-pre18.sh — tag the next tollgate-wrt pre-release from the FEED repo, but only
# when the tree is actually ready to be tested by a human.
#
#   bash <(curl -fsSL https://raw.githubusercontent.com/felixfelix-bot/physical-router-test-automation/main/scripts/release/tag-pre18.sh)
#   bash <(curl -fsSL .../tag-pre18.sh) --tag          # actually create the tag
#
# WHY GATES: tagging master as it stands produces a build byte-equivalent in
# BEHAVIOUR to the one that just failed on the operator's router — the feed
# postinst still restarts only tollgate-wrt, so the install path leaves the
# firewall guard, nodogsplash, uhttpd and dnsmasq unapplied. A tag is a promise
# that the artifact is worth a human's afternoon; this script refuses to make
# that promise on an unready tree, and tells you the exact missing item instead.
#
# Default mode is CHECK (no writes). --tag creates the tag only if every gate passes.
#
# Maintainer action — requires `gh` authenticated with push access to the feed repo.

set -uo pipefail

REPO="FreedomTechFeed/packages"
MODULE_REPO="OpenTollGate/tollgate-module-basic-go"
BRANCH="master"
VERSION="0.6.0_alpha4_pre18"
TAG="v0.6.0-alpha4-pre18"
MAKEFILE="net/tollgate-wrt/Makefile"

MODE="check"
for a in "$@"; do
  case "$a" in
    --tag) MODE="tag" ;;
    --check) MODE="check" ;;
    --repo) shift; REPO="${1:-$REPO}" ;;
    --version) shift; VERSION="${1:-$VERSION}"; TAG="v${VERSION//_/-}"; TAG="${TAG/_alpha4_pre18/-alpha4-pre18}" ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "unknown argument: $a" >&2; exit 2 ;;
  esac
done

fail=0
pass() { printf '  PASS  %s\n' "$*"; }
bad()  { printf '  FAIL  %s\n' "$*"; fail=1; }
info() { printf '  ----  %s\n' "$*"; }

echo "target : $REPO@$BRANCH  ->  tag $TAG"
echo "mode   : $MODE"
echo

# ---- G1: gh + auth + repo reachable ----------------------------------------
if ! command -v gh >/dev/null 2>&1; then bad "gh not installed"; else
  if gh auth status >/dev/null 2>&1; then
    if gh api "repos/$REPO" >/dev/null 2>&1; then pass "gh authenticated, $REPO reachable"; else bad "cannot read $REPO with the current gh token"; fi
  else bad "gh is not authenticated (run: gh auth login)"; fi
fi
[ "$fail" -eq 0 ] || { echo; echo "=== refusing: fix the above first ==="; exit 1; }

# ---- G2: tag does not already exist ----------------------------------------
if gh api "repos/$REPO/git/ref/tags/$TAG" >/dev/null 2>&1; then
  bad "tag $TAG already exists — nothing to do"
else
  pass "tag $TAG does not exist yet"
fi

# ---- fetch the tree we would tag -------------------------------------------
MK=$(gh api "repos/$REPO/contents/$MAKEFILE?ref=$BRANCH" --jq .content 2>/dev/null | base64 -d 2>/dev/null)
HEAD_SHA=$(gh api "repos/$REPO/commits/$BRANCH" --jq .sha 2>/dev/null)
if [ -z "$MK" ] || [ -z "$HEAD_SHA" ]; then
  bad "could not read $MAKEFILE or $BRANCH head from $REPO"
  echo; echo "=== refusing: no tree to inspect ==="; exit 1
fi
info "would tag $BRANCH head: $HEAD_SHA"

# ---- G3: the release version is the pre18 one ------------------------------
CURV=$(printf '%s\n' "$MK" | sed -n 's/^PKG_VERSION:=\(.*\)$/\1/p' | head -1)
if [ "$CURV" = "$VERSION" ]; then
  pass "PKG_VERSION is $VERSION"
else
  bad "PKG_VERSION is '$CURV' — expected '$VERSION' (the cut commit is not on $BRANCH)"
  info "bump it in $MAKEFILE as part of the pre18 cut (one commit, with PKG_HASH recomputed)"
fi

# ---- G4: the install-path fix is present in the shipped postinst -----------
POSTINST=$(printf '%s\n' "$MK" | sed -n '/define Package\/tollgate-wrt\/postinst/,/^endef/p')
[ -n "$POSTINST" ] || POSTINST=$(printf '%s\n' "$MK" | sed -n '/define Package\/.*\/postinst/,/^endef/p')
NEEDED="/etc/init.d/firewall reload:/etc/init.d/nodogsplash restart:/etc/init.d/uhttpd restart:/etc/init.d/dnsmasq restart"
MISSING=""
IFS=':' read -r -a REQS <<< "$NEEDED"
for r in "${REQS[@]}"; do
  printf '%s' "$POSTINST" | grep -qF "$r" || MISSING="$MISSING\n        - $r"
done
if [ -z "$MISSING" ]; then
  pass "postinst restarts firewall + nodogsplash + uhttpd + dnsmasq (install path fixed)"
else
  bad "postinst is missing the install-path service restarts:$(printf "$MISSING")"
  info "the correct sequence already exists in the MODULE's packaging/Makefile (present"
  info "since the pre17 pin, lines ~108-117): network restart + wait, wifi reload,"
  info "firewall reload, dnsmasq restart, uhttpd restart, nodogsplash restart, tollgate-wrt."
  info "Port it verbatim into the feed recipe (card t_12a2ab24)."
fi

# ---- G5: the module repin is the current main tip -------------------------
PIN=$(printf '%s\n' "$MK" | sed -n 's/^PKG_SOURCE_VERSION:=\(.*\)$/\1/p' | head -1)
TIPSHA=$(gh api "repos/$MODULE_REPO/commits/main" --jq .sha 2>/dev/null)
if [ -n "$PIN" ] && [ -n "$TIPSHA" ] && [ "$PIN" = "$TIPSHA" ]; then
  pass "module pin == $MODULE_REPO main tip (${TIPSHA:0:7})"
elif [ -z "$TIPSHA" ]; then
  bad "could not read $MODULE_REPO main tip"
else
  bad "module pin ${PIN:0:7} != main tip ${TIPSHA:0:7} — repin to the tip (brings #579 etc.)"
fi

# ---- verdict ---------------------------------------------------------------
echo
if [ "$fail" -ne 0 ]; then
  echo "=== VERDICT: NOT READY — refusing to tag $TAG ==="
  echo "Every FAIL above is a reason a tester would waste an afternoon on this build."
  echo "Re-run this command after the cut commit lands on $BRANCH; it flips to READY by itself."
  exit 1
fi

echo "=== VERDICT: READY ==="
if [ "$MODE" = "check" ]; then
  echo "Re-run with --tag to create $TAG on $HEAD_SHA."
  exit 0
fi

echo "creating lightweight tag $TAG -> $HEAD_SHA ..."
if ! gh api -X POST "repos/$REPO/git/refs" -f "ref=refs/tags/$TAG" -f "sha=$HEAD_SHA" >/dev/null 2>&1; then
  echo "FAIL: tag creation rejected by the API"; exit 1
fi
OUT=$(gh api "repos/$REPO/git/ref/tags/$TAG" --jq '{ref: .ref, sha: .object.sha}' 2>/dev/null)
echo "created: $OUT"
case "$OUT" in *"$HEAD_SHA"*) echo "  PASS  read-back confirms the tag points at $HEAD_SHA";; *) echo "  FAIL  read-back mismatch — check the tag by hand"; exit 1;; esac
echo
echo "Next:"
echo "  * the release workflow builds 7 arches x {apk master, ipk openwrt-24.10} + the"
echo "    per-arch offline bundles, then signs SHA256SUMS — watch it:"
echo "      gh run list -R $REPO --workflow release-publish.yml --limit 3"
echo "  * when it is green, verify the artifact the way the operator does (download the"
echo "    aarch64_cortex-a53 apk, match its sha256 against the signed SHA256SUMS, extract"
echo "    and diff /etc/uci-defaults + the postinst against this tree)."
echo "  * acceptance BEFORE a reboot: scripts/diag/tg-router-health.sh must PASS all six"
echo "    sections on the freshly installed router."
