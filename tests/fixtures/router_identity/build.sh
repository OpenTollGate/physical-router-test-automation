#!/usr/bin/env bash
# Rebuild the golden fixtures for tests/scenarios/test_router_identity_script.py.
#
# Produces two artifacts in this directory:
#   95-router-identity         — the shell script under test (PR #190)
#   identity-ref-linux-amd64   — the Go reference binary (PR #189), cross-compiled
#                                for the SHC VM (linux/amd64, statically linked)
#
# Why both: PR #190 claims to "mirror the Go reference byte-for-byte". This
# build produces both implementations from their PR branches so the test module
# can cross-check them on a real VM and catch any divergence.
#
# Prerequisites: go 1.24+, gh CLI (authenticated), nak (optional — only used to
# regenerate the GOLDEN_KEYS vectors in the test file).
#
# Usage:
#   ./tests/fixtures/router_identity/build.sh
#
# Re-run when PR #189 or PR #190 is updated, or when you regenerate test keys.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

PR189_REPO="c03rad0r/test-stablechannel-tollgate-module-basic-go"
PR189_BRANCH="feat/identity-package"
PR190_REPO="c03rad0r/test-stablechannel-tollgate-module-basic-go"
PR190_BRANCH="feat/router-identity-script"

echo "==> Fetching PR #190 shell script ($PR190_BRANCH)"
gh api "repos/$PR190_REPO/contents/packaging/files/etc/uci-defaults/95-router-identity?ref=$PR190_BRANCH" \
    --jq '.content' | base64 -d > "$SCRIPT_DIR/95-router-identity"
chmod +x "$SCRIPT_DIR/95-router-identity"
echo "    wrote $SCRIPT_DIR/95-router-identity ($(wc -l < "$SCRIPT_DIR/95-router-identity") lines)"

echo "==> Fetching PR #189 Go identity package ($PR189_BRANCH)"
mkdir -p "$TMP_DIR/identity-ref/cmd/ref"
gh api "repos/$PR189_REPO/contents/src/identity/identity.go?ref=$PR189_BRANCH" \
    --jq '.content' | base64 -d > "$TMP_DIR/identity-ref/identity.go"
gh api "repos/$PR189_REPO/contents/src/identity/go.mod?ref=$PR189_BRANCH" \
    --jq '.content' | base64 -d > "$TMP_DIR/identity-ref/go.mod"
gh api "repos/$PR189_REPO/contents/src/identity/go.sum?ref=$PR189_BRANCH" \
    --jq '.content' | base64 -d > "$TMP_DIR/identity-ref/go.sum"

cat > "$TMP_DIR/identity-ref/cmd/ref/main.go" <<'EOF'
package main

import (
	"encoding/hex"
	"fmt"
	"os"

	"github.com/OpenTollGate/tollgate-module-basic-go/src/identity"
	"github.com/nbd-wtf/go-nostr"
)

func main() {
	if len(os.Args) < 2 {
		fmt.Fprintln(os.Stderr, "usage: identity-ref <hex-privkey>")
		os.Exit(2)
	}
	priv := os.Args[1]
	d, err := identity.Derive(priv)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	fmt.Printf("privkey=%s\n", priv)
	fmt.Printf("npub_bech32=%s\n", d.Npub)
	fmt.Printf("ipv4=%s\n", d.IPv4)
	for _, iface := range identity.StandardInterfaces {
		fmt.Printf("mac_%s=%s\n", iface, d.MACs[iface])
	}
	pub, err := nostr.GetPublicKey(priv)
	if err != nil {
		fmt.Fprintln(os.Stderr, "pubkey err:", err)
		return
	}
	b, err := hex.DecodeString(pub)
	if err != nil {
		fmt.Fprintln(os.Stderr, "hex err:", err)
		return
	}
	switch {
	case len(b) == 65:
		fmt.Printf("pubkey_x_hex=%s\n", hex.EncodeToString(b[1:33]))
	case len(b) == 33:
		fmt.Printf("pubkey_x_hex=%s\n", hex.EncodeToString(b[1:33]))
	default:
		fmt.Printf("pubkey_full_hex=%s (len=%d)\n", hex.EncodeToString(b), len(b))
	}
}
EOF

# The identity package uses go-nostr which pulls in many transitive deps.
# go.sum from the PR is authoritative — use it verbatim.
cd "$TMP_DIR/identity-ref"

echo "==> Cross-compiling Go reference for linux/amd64 (static)"
GOOS=linux GOARCH=amd64 CGO_ENABLED=0 go build -o "$SCRIPT_DIR/identity-ref-linux-amd64" ./cmd/ref
chmod +x "$SCRIPT_DIR/identity-ref-linux-amd64"
echo "    wrote $SCRIPT_DIR/identity-ref-linux-amd64"

echo
echo "==> Sanity check (run on linux/amd64 or via qemu-x86_64-static if needed):"
echo "    identity-ref-linux-amd64 <64-char-hex-privkey>"
echo
echo "==> To regenerate GOLDEN_KEYS in the test file, run:"
echo "    for i in 1 2 3; do nak key generate; done"
echo "    then run identity-ref on each key and paste outputs into GOLDEN_KEYS."
