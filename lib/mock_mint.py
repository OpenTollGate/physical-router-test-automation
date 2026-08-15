#!/usr/bin/bin/env python3
"""Mock Cashu mint server for local dry testing.

Implements the minimal Cashu mint API needed for the tollgate backend:
- GET  /v1/info    — mint info
- GET  /v1/keys    — active keyset public keys
- GET  /v1/keysets — keyset list
- POST /v1/swap    — swap proofs (verify + sign)
- POST /v1/checkstate — check proof states

Also provides create_token() to mint valid test tokens.

Crypto matches gonuts-tollgate exactly:
- DomainSeparator: "Secp256k1_HashToCurve_Cashu_"
- Keyset ID V1: "00" + SHA256(sorted_compressed_pubkeys)[:14]
- HashToCurve: SHA256(domain + secret) → counter → force 0x02 prefix
"""

import hashlib
import json
import os
import socket
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn

KEYSET_FILE = os.environ.get("MOCK_MINT_KEYSET_FILE", "/tmp/mock-mint-keyset.json")


def _detect_bind_address() -> str:
    """Detect a working loopback address for binding.

    Tries IPv4 first (127.0.0.1), falls back to IPv6 (::).
    See docs/known-issues.md#ipv4-loopback for why this is needed.
    """
    for family, addr in [(socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::")]:
        try:
            s = socket.socket(family, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((addr, 0))
            s.close()
            return addr
        except OSError:
            continue
    return "::"


_BIND_ADDRESS = _detect_bind_address()


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    address_family = socket.AF_INET if _BIND_ADDRESS != "::" else socket.AF_INET6
    daemon_threads = True
from urllib.parse import urlparse

from coincurve import PublicKey, PrivateKey

DOMAIN_SEPARATOR = b"Secp256k1_HashToCurve_Cashu_"

VERBOSE = os.environ.get("MOCK_MINT_VERBOSE", "1") != "0"

def log(msg: str):
    if VERBOSE:
        sys.stderr.write(f"[mock-mint] {msg}\n")
        sys.stderr.flush()

# ─── Crypto helpers ──────────────────────────────────────────────

def hash_to_curve(secret: bytes) -> bytes:
    """Hash a secret to a secp256k1 curve point. Returns 33-byte compressed key."""
    msg_hash = hashlib.sha256(DOMAIN_SEPARATOR + secret).digest()
    for counter in range(2**16):
        c = counter.to_bytes(4, 'little')
        h = hashlib.sha256(msg_hash + c).digest()
        pk_bytes = b'\x02' + h
        try:
            PublicKey(pk_bytes)
            return pk_bytes
        except Exception:
            continue
    raise ValueError("HashToCurve failed after 2^16 iterations")


def derive_keyset_id_v1(keys: dict[int, str]) -> str:
    """Derive V1 keyset ID from sorted public keys.
    keys = {amount: compressed_pubkey_hex}
    """
    sorted_amounts = sorted(keys.keys())
    concatenated = b''
    for amount in sorted_amounts:
        concatenated += bytes.fromhex(keys[amount])
    h = hashlib.sha256(concatenated).hexdigest()
    return "00" + h[:14]


# ─── Mint state ──────────────────────────────────────────────────

class MockMint:
    def __init__(self):
        self.keypairs: dict[int, dict] = {}
        if os.path.exists(KEYSET_FILE):
            self._load_keyset()
        else:
            self._generate_keyset()
            self._save_keyset()
        self.spent_secrets: set[str] = set()
        self.spent_ys: set[str] = set()
        self.swap_error_count = 0
        self.swap_error_code = 0

    def _generate_keyset(self):
        for i in range(60):
            amount = 2 ** i
            priv = PrivateKey()
            self.keypairs[amount] = {
                "priv": priv,
                "priv_hex": priv.secret.hex(),
                "pub_hex": priv.public_key.format().hex(),
            }
        keys_for_id = {amt: kp["pub_hex"] for amt, kp in self.keypairs.items()}
        self.keyset_id = derive_keyset_id_v1(keys_for_id)

    def _save_keyset(self):
        data = {
            "keyset_id": self.keyset_id,
            "keys": {str(amt): kp["priv_hex"] for amt, kp in self.keypairs.items()},
        }
        with open(KEYSET_FILE, "w") as f:
            json.dump(data, f)

    def _load_keyset(self):
        with open(KEYSET_FILE, "r") as f:
            data = json.load(f)
        self.keyset_id = data["keyset_id"]
        for amt_str, priv_hex in data["keys"].items():
            amount = int(amt_str)
            priv = PrivateKey(bytes.fromhex(priv_hex))
            self.keypairs[amount] = {
                "priv": priv,
                "priv_hex": priv_hex,
                "pub_hex": priv.public_key.format().hex(),
            }

    def public_keys(self) -> dict[str, str]:
        """Return {amount_str: pubkey_hex} for all amounts."""
        return {str(amt): kp["pub_hex"] for amt, kp in sorted(self.keypairs.items())}

    def keysets_response(self) -> list[dict]:
        """Return keysets array for /v1/keysets."""
        return [{
            "id": self.keyset_id,
            "unit": "sat",
            "active": True,
            "input_fee_ppk": 0,
        }]

    def keys_response(self) -> dict:
        """Return full keyset response for /v1/keys."""
        return {
            "keysets": [{
                "id": self.keyset_id,
                "unit": "sat",
                "keys": self.public_keys(),
            }]
        }

    def info_response(self) -> dict:
        """Return mint info for /v1/info."""
        return {
            "name": "Mock Mint (Dry Testing)",
            "pubkey": self.keypairs[1]["pub_hex"],
            "version": "Nutshell/0.20.0",
            "description": "Local mock mint for tollgate dry testing",
            "description_long": "Auto-generated mock mint. No real ecash.",
            "contact": [],
            "nuts": {
                "4": {"methods": [{"method": "bolt11", "unit": "sat"}], "disabled": False},
                "5": {"methods": [{"method": "bolt11", "unit": "sat"}], "disabled": False},
                "7": {"supported": True},
                "8": {"supported": True},
                "9": {"supported": True},
                "11": {"supported": True},
            },
        }

    def create_proof(self, amount: int, secret: str = None) -> dict:
        """Create a valid proof for the given amount."""
        if secret is None:
            import secrets as sec
            secret = sec.token_hex(16)
        Y_bytes = hash_to_curve(secret.encode())
        Y = PublicKey(Y_bytes)
        k_secret = bytes.fromhex(self.keypairs[amount]["priv_hex"])
        C = Y.multiply(k_secret)
        return {
            "amount": amount,
            "id": self.keyset_id,
            "secret": secret,
            "C": C.format().hex(),
        }

    def create_token_v3(self, amount: int) -> str:
        """Create a valid V3 (cashuA) token string."""
        import base64
        proof = self.create_proof(amount)
        loopback = "127.0.0.1" if _BIND_ADDRESS != "::" else "[::1]"
        payload = {
            "token": [{
                "mint": f"http://{loopback}:{MINT_PORT}",
                "proofs": [proof],
            }],
            "unit": "sat",
            "memo": "dry test token",
        }
        json_bytes = json.dumps(payload, separators=(',', ':')).encode()
        b64 = base64.urlsafe_b64encode(json_bytes).decode().rstrip('=')
        return f"cashuA{b64}"

    def verify_proof(self, proof: dict) -> bool:
        """Verify a proof's signature."""
        try:
            amount = proof["amount"]
            secret = proof["secret"]
            C_hex = proof["C"]
            if amount not in self.keypairs:
                return False
            Y_bytes = hash_to_curve(secret.encode())
            Y = PublicKey(Y_bytes)
            k_secret = bytes.fromhex(self.keypairs[amount]["priv_hex"])
            expected_C = Y.multiply(k_secret)
            return expected_C.format().hex() == C_hex
        except Exception:
            return False

    def handle_swap(self, body: dict) -> tuple[dict, int]:
        """Handle POST /v1/swap."""
        if self.swap_error_count > 0:
            self.swap_error_count -= 1
            code = self.swap_error_code or 429
            log(f"SWAP INJECT ERROR remaining={self.swap_error_count} code={code}")
            return {"code": 0, "error": "Simulated mint error"}, code

        inputs = body.get("inputs", [])
        outputs = body.get("outputs", [])
        log(f"SWAP inputs={len(inputs)} outputs={len(outputs)}")
        total_in = 0
        for idx, inp in enumerate(inputs):
            if not self.verify_proof(inp):
                log(f"SWAP REJECT input[{idx}] invalid proof secret={inp.get('secret','?')[:16]}")
                return {"code": 0, "error": "Invalid proof"}, 400
            secret = inp["secret"]
            Y_hex = hash_to_curve(secret.encode()).hex()
            if secret in self.spent_secrets:
                log(f"SWAP REJECT input[{idx}] double-spend secret={secret[:16]} Y={Y_hex[:16]}")
                return {"code": 0, "error": "Token already spent"}, 400
            self.spent_secrets.add(secret)
            self.spent_ys.add(Y_hex)
            log(f"SWAP MARK spent input[{idx}] amount={inp['amount']} secret={secret[:16]} Y={Y_hex[:16]}")
            total_in += inp["amount"]
        # Sign output blinded messages
        total_out = 0
        signatures = []
        for out in outputs:
            amount = out["amount"]
            B_hex = out["B_"]
            B = PublicKey(bytes.fromhex(B_hex))
            k_secret = bytes.fromhex(self.keypairs[amount]["priv_hex"])
            C_ = B.multiply(k_secret)
            signatures.append({
                "amount": amount,
                "C_": C_.format().hex(),
                "id": self.keyset_id,
            })
            total_out += amount
        return {"signatures": signatures}, 200

    def handle_checkstate(self, body: dict) -> dict:
        """Handle POST /v1/checkstate."""
        ys = body.get("Ys", [])
        states = []
        for y in ys:
            is_spent = y in self.spent_ys
            states.append({
                "Y": y,
                "state": "SPENT" if is_spent else "UNSPENT",
                "witness": None,
            })
            log(f"CHECKSTATE Y={y[:16]} → {'SPENT' if is_spent else 'UNSPENT'}")
        return {"states": states}


# ─── HTTP server ─────────────────────────────────────────────────

MINT = None
MINT_PORT = 3338


class MintHandler(BaseHTTPRequestHandler):
    def _json(self, code: int, data: dict):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw)
        except Exception:
            return {}

    def do_GET(self):
        path = urlparse(self.path).path
        parsed = urlparse(self.path)
        path = parsed.path
        query = parsed.query

        log(f"GET {path}" + (f"?{query}" if query else ""))

        if path == "/v1/info":
            self._json(200, MINT.info_response())
        elif path == "/v1/keys":
            self._json(200, MINT.keys_response())
        elif path.startswith("/v1/keys/"):
            self._json(200, MINT.keys_response())
        elif path == "/v1/keysets":
            self._json(200, {"keysets": MINT.keysets_response()})
        elif path == "/test/create-token":
            from urllib.parse import parse_qs
            params = parse_qs(query)
            amount = int(params.get("amount", ["1"])[0])
            token = MINT.create_token_v3(amount)
            log(f"CREATE-TOKEN amount={amount} keyset={MINT.keyset_id}")
            self._json(200, {"token": token, "amount": amount, "keyset_id": MINT.keyset_id})
        elif path == "/test/spent":
            self._json(200, {
                "spent_secrets": list(MINT.spent_secrets),
                "spent_ys": list(MINT.spent_ys),
                "count": len(MINT.spent_secrets),
            })
        elif path == "/test/set-swap-error":
            from urllib.parse import parse_qs
            params = parse_qs(query)
            MINT.swap_error_count = int(params.get("count", ["1"])[0])
            MINT.swap_error_code = int(params.get("code", ["429"])[0])
            log(f"SET SWAP ERROR count={MINT.swap_error_count} code={MINT.swap_error_code}")
            self._json(200, {"ok": True, "count": MINT.swap_error_count, "code": MINT.swap_error_code})
        else:
            log(f"404 NOT FOUND: {path}")
            self._json(404, {"error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        body = self._read_body()
        log(f"POST {path} body_keys={list(body.keys())}")
        if path == "/v1/swap":
            resp, code = MINT.handle_swap(body)
            log(f"SWAP → {code} signatures={len(resp.get('signatures', []))}")
            self._json(code, resp)
        elif path == "/v1/checkstate":
            resp = MINT.handle_checkstate(body)
            self._json(200, resp)
        elif path == "/v1/mint/quote/bolt11":
            # Auto-approve mint quote (FakeWallet behavior)
            import secrets
            quote_id = secrets.token_hex(8)
            self._json(200, {
                "quote": quote_id,
                "request": "lnbc1000n1p3mock25invoice",
                "amount": body.get("amount", 1),
                "unit": "sat",
                "state": "PAID",
                "expiry": 9999999999,
            })
        elif path == "/v1/mint/bolt11":
            # Mint tokens — sign the provided blinded outputs
            outputs = body.get("outputs", [])
            signatures = []
            for out in outputs:
                amount = out.get("amount", 1)
                B_hex = out.get("B_", "")
                try:
                    B = PublicKey(bytes.fromhex(B_hex))
                    k_secret = bytes.fromhex(MINT.keypairs[amount]["priv_hex"])
                    C_ = B.multiply(k_secret)
                    signatures.append({
                        "amount": amount,
                        "C_": C_.format().hex(),
                        "id": MINT.keyset_id,
                    })
                except Exception:
                    pass
            self._json(200, {"signatures": signatures})
        else:
            self._json(404, {"error": "not found"})

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def log_message(self, format, *args):
        log(f"HTTP {self.client_address[0]} {format % args}")


def main():
    global MINT
    port = int(sys.argv[sys.argv.index('--port') + 1]) if '--port' in sys.argv else MINT_PORT
    MINT = MockMint()
    print(f"Mock mint keyset ID: {MINT.keyset_id}")
    print(f"Serving on http://127.0.0.1:{port}")
    server = ThreadingHTTPServer((_BIND_ADDRESS, port), MintHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.shutdown()


if __name__ == "__main__":
    main()
