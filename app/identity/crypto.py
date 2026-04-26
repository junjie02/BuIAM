from __future__ import annotations

import base64
import hashlib
import json

from app.identity.keys import load_private_key, load_public_key


def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def canonical_json(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def sha256_hex(data: str) -> str:
    return hashlib.sha256(data.encode()).hexdigest()


def rsa_sign(signing_input: str, key_id: str) -> str:
    private_key = load_private_key(key_id)
    digest = hashlib.sha256(signing_input.encode()).digest()
    digest_int = int.from_bytes(digest, "big")
    signature_int = pow(digest_int, int(private_key["d"]), int(private_key["n"]))
    length = (int(private_key["n"]).bit_length() + 7) // 8
    return b64url_encode(signature_int.to_bytes(length, "big"))


def rsa_verify(signing_input: str, signature: str, key_id: str) -> bool:
    public_key = load_public_key(key_id)
    digest_int = int.from_bytes(hashlib.sha256(signing_input.encode()).digest(), "big")
    signature_int = int.from_bytes(b64url_decode(signature), "big")
    verified = pow(signature_int, int(public_key["e"]), int(public_key["n"]))
    return verified == digest_int
