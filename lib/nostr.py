import json
import hashlib
import time
from coincurve import PrivateKey


def sign_event(tags: list, kind: int = 21000, content: str = "",
               priv_hex: str | None = None) -> dict:
    sk = PrivateKey(bytes.fromhex(priv_hex)) if priv_hex else PrivateKey()
    pubkey = sk.public_key_xonly.format().hex()
    created_at = int(time.time())

    payload = [0, pubkey, created_at, kind, tags, content]
    serialized = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    event_id = hashlib.sha256(serialized).digest()

    sig = sk.sign_schnorr(event_id, aux_randomness=None).hex()

    return {
        "id": event_id.hex(),
        "pubkey": pubkey,
        "created_at": created_at,
        "kind": kind,
        "tags": tags,
        "content": content,
        "sig": sig,
    }


def payment_event(token: str, pubkey: str = "", mac: str = "",
                  priv_hex: str | None = None) -> dict:
    tags = []
    if pubkey:
        tags.append(["p", pubkey])
    if mac:
        tags.append(["device-identifier", "mac", mac])
    tags.append(["payment", token])
    return sign_event(tags=tags, kind=21000, priv_hex=priv_hex)
