#!/usr/bin/env bash
# mint-testnet-token — mint a fresh e-cash token from a TESTNET mint for the
# portal film, so the take uses a real (not placeholder) token.
#
# Usage:  mint-testnet-token.sh <out-file> [amount] [mint-url]
# Requires node + a built @cashu/cashu-ts (set CASHU_TS to its lib dir).
set -euo pipefail
OUT="${1:?usage: mint-testnet-token.sh <out-file> [amount] [mint-url]}"
AMOUNT="${2:-4}"
MINT="${3:-https://testnut.cashu.exchange}"
CASHU_TS="${CASHU_TS:-}"

: "${CASHU_TS:?set CASHU_TS to the built cashu-ts lib dir (…/lib/cashu-ts.es.js)}"

cat > /tmp/mint-testnet-token.mjs <<JS
import { Mint, Wallet, getEncodedToken } from '${CASHU_TS}/cashu-ts.es.js';
const MINT = '${MINT}';
const AMOUNT = Number('${AMOUNT}');
const mint = new Mint(MINT);
const wallet = new Wallet(mint, { unit: 'sat' });
await wallet.loadMint();
const quote = await wallet.createMintQuoteBolt11(BigInt(AMOUNT));
const proofs = await wallet.mintProofsBolt11(BigInt(AMOUNT), quote);
console.log(getEncodedToken({ mint: MINT, proofs, unit: 'sat' }));
JS

node /tmp/mint-testnet-token.mjs > "$OUT"
echo "wrote $(wc -c < "$OUT") bytes -> $OUT"
