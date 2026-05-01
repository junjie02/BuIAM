from __future__ import annotations

import base64
import hashlib
import json

from app.identity.did_resolver import resolve_verification_method
from app.identity.keys import load_mldsa_private_key, load_mldsa_public_key, load_private_key, load_public_key


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
    return _rsa_sign_with_private_key(signing_input, private_key)


def rsa_sign_with_kid(signing_input: str, verification_method_id: str) -> str:
    resolved = resolve_verification_method(verification_method_id)
    private_key = load_private_key(resolved.subject_id)
    return _rsa_sign_with_private_key(signing_input, private_key)


def _rsa_sign_with_private_key(signing_input: str, private_key: dict) -> str:
    digest = hashlib.sha256(signing_input.encode()).digest()
    digest_int = int.from_bytes(digest, "big")
    signature_int = pow(digest_int, int(private_key["d"]), int(private_key["n"]))
    length = (int(private_key["n"]).bit_length() + 7) // 8
    return b64url_encode(signature_int.to_bytes(length, "big"))


def rsa_verify(signing_input: str, signature: str, key_id: str) -> bool:
    public_key = load_public_key(key_id)
    return _rsa_verify_with_public_key(signing_input, signature, public_key)


def rsa_verify_with_kid(signing_input: str, signature: str, verification_method_id: str) -> bool:
    resolved = resolve_verification_method(verification_method_id)
    if resolved.public_key_jwk.get("kty") == "ML-DSA":
        return mldsa_verify_with_kid(signing_input, signature, verification_method_id)
    public_key = {
        "n": str(_base64url_to_int(resolved.public_key_jwk["n"])),
        "e": str(_base64url_to_int(resolved.public_key_jwk["e"])),
    }
    return _rsa_verify_with_public_key(signing_input, signature, public_key)


def _rsa_verify_with_public_key(signing_input: str, signature: str, public_key: dict) -> bool:
    digest_int = int.from_bytes(hashlib.sha256(signing_input.encode()).digest(), "big")
    signature_int = int.from_bytes(b64url_decode(signature), "big")
    verified = pow(signature_int, int(public_key["e"]), int(public_key["n"]))
    return verified == digest_int


def _base64url_to_int(value: str) -> int:
    data = b64url_decode(value)
    return int.from_bytes(data, "big")


def mldsa_sign(signing_input: str, key_id: str) -> str:
    private = load_mldsa_private_key(key_id)
    return _mldsa_sign_with_private(signing_input, private)


def mldsa_sign_with_kid(signing_input: str, verification_method_id: str) -> str:
    resolved = resolve_verification_method(verification_method_id)
    private = load_mldsa_private_key(resolved.subject_id)
    return _mldsa_sign_with_private(signing_input, private)


def _mldsa_sign_with_private(signing_input: str, private_key: dict) -> str:
    from oqs import oqs as oqs_mod  # type: ignore

    sk = base64.b64decode(private_key["sk"])
    alg = str(private_key.get("alg", "ML-DSA-65"))
    with oqs_mod.Signature(alg, secret_key=sk) as signer:
        signature = signer.sign(signing_input.encode())
    return b64url_encode(signature)


def mldsa_verify(signing_input: str, signature: str, key_id: str) -> bool:
    public = load_mldsa_public_key(key_id)
    return _mldsa_verify_with_public(signing_input, signature, public)


def mldsa_verify_with_kid(signing_input: str, signature: str, verification_method_id: str) -> bool:
    resolved = resolve_verification_method(verification_method_id)
    jwk = resolved.public_key_jwk
    public = {"pk": jwk.get("pk", ""), "alg": jwk.get("alg", "ML-DSA-65")}
    return _mldsa_verify_with_public(signing_input, signature, public)


def _mldsa_verify_with_public(signing_input: str, signature: str, public_key: dict) -> bool:
    from oqs import oqs as oqs_mod  # type: ignore

    try:
        pk = base64.b64decode(public_key["pk"])
        sig = b64url_decode(signature)
        alg = str(public_key.get("alg", "ML-DSA-65"))
        with oqs_mod.Signature(alg) as verifier:
            result = verifier.verify(signing_input.encode(), sig, pk)
        return bool(result)
    except Exception:
        return False
