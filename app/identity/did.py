from __future__ import annotations

import hashlib
import os
from typing import Any

from app.identity.keys import load_mldsa_public_key, load_public_key


def build_did(subject_id: str) -> str:
    return f"did:buiam:{subject_id}"


def build_verification_method_id(did: str, key_label: str = "key-1") -> str:
    return f"{did}#{key_label}"


def build_did_document(subject_id: str, *, key_label: str = "key-1", service_endpoint: str | None = None) -> dict[str, Any]:
    did = build_did(subject_id)
    vm_id = build_verification_method_id(did, key_label)
    use_mldsa = os.getenv("BUIAM_USE_MLDSA", "false").lower() == "true"
    if use_mldsa:
        mldsa = load_mldsa_public_key(subject_id)
        jwk = {
            "kty": "ML-DSA",
            "alg": str(mldsa.get("alg", "ML-DSA-65")),
            "pk": str(mldsa.get("pk", "")),
        }
    else:
        public_key = load_public_key(subject_id)
        jwk = {
            "kty": "RSA",
            "n": _int_str_to_base64url(public_key["n"]),
            "e": _int_str_to_base64url(public_key["e"]),
        }
    service: list[dict[str, str]] = []
    if service_endpoint:
        service.append({
            "id": f"{did}#a2a-service",
            "type": "A2A-Service",
            "serviceEndpoint": service_endpoint,
        })
    return {
        "@context": ["https://www.w3.org/ns/did/v1"],
        "id": did,
        "verificationMethod": [{"id": vm_id, "type": "JsonWebKey2020", "controller": did, "publicKeyJwk": jwk}],
        "authentication": [vm_id],
        "assertionMethod": [vm_id],
        "capabilityDelegation": [vm_id],
        "keyAgreement": [],
        "service": service,
        "metadata": {"subject_id": subject_id, "fingerprint": _jwk_fingerprint(jwk)},
    }


def _jwk_fingerprint(jwk: dict[str, str]) -> str:
    canonical = "|".join(f"{k}:{jwk[k]}" for k in sorted(jwk.keys()))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _int_str_to_base64url(value: str) -> str:
    number = int(value)
    length = max(1, (number.bit_length() + 7) // 8)
    data = number.to_bytes(length, "big")
    import base64

    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()
