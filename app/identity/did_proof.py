from __future__ import annotations

import logging
import os
import time

from app.identity.crypto import (
    _mldsa_verify_with_public,
    _rsa_verify_with_public_key,
    b64url_decode,
    canonical_json,
    mldsa_sign,
    rsa_sign,
)

logger = logging.getLogger("buiam.identity.did_proof")


def create_did_proof(did_document: dict, subject_id: str) -> dict:
    use_mldsa = os.getenv("BUIAM_USE_MLDSA", "false").lower() == "true"
    proof_vm = _extract_first_verification_method(did_document)
    canonical = canonical_json(did_document)
    if use_mldsa:
        proof_type = "BUIAM-MLDSA-65"
        signature_value = mldsa_sign(canonical, subject_id)
    else:
        proof_type = "BUIAM-RS256"
        signature_value = rsa_sign(canonical, subject_id)
    return {
        "type": proof_type,
        "verificationMethod": proof_vm,
        "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "signatureValue": signature_value,
    }


def verify_did_proof(did_document: dict, proof: dict) -> bool:
    vm_id = proof.get("verificationMethod", "")
    jwk = _extract_jwk_by_vm(did_document, vm_id)
    if jwk is None:
        logger.warning("DID proof verification_method %r not found in document", vm_id)
        return False

    kty = str(jwk.get("kty", ""))
    doc_for_signing = _did_document_without_proof(did_document)
    signing_input = canonical_json(doc_for_signing)

    signature_value = proof.get("signatureValue", "")
    if not signature_value:
        return False

    try:
        if kty == "ML-DSA":
            public = {"pk": jwk.get("pk", ""), "alg": jwk.get("alg", "ML-DSA-65")}
            return _mldsa_verify_with_public(signing_input, signature_value, public)
        return _rsa_verify_with_jwk(signing_input, signature_value, jwk)
    except Exception:
        logger.debug("DID proof verification raised exception", exc_info=True)
        return False


def _extract_first_verification_method(did_document: dict) -> str:
    vms = did_document.get("verificationMethod", [])
    if not vms:
        return ""
    return str(vms[0].get("id", ""))


def _extract_jwk_by_vm(did_document: dict, vm_id: str) -> dict | None:
    for vm in did_document.get("verificationMethod", []):
        if vm.get("id") == vm_id:
            jwk = vm.get("publicKeyJwk")
            if isinstance(jwk, dict):
                return jwk
    return None


def _did_document_without_proof(did_document: dict) -> dict:
    return {k: v for k, v in did_document.items() if k != "proof"}


def _rsa_verify_with_jwk(signing_input: str, signature: str, jwk: dict) -> bool:
    n_str = str(_base64url_jwk_param_to_int(jwk.get("n", "")))
    e_str = str(_base64url_jwk_param_to_int(jwk.get("e", "")))
    if not n_str or n_str == "0" or not e_str or e_str == "0":
        return False
    public_key = {"n": n_str, "e": e_str}
    return _rsa_verify_with_public_key(signing_input, signature, public_key)


def _base64url_jwk_param_to_int(value: str) -> int:
    if not value:
        return 0
    return int.from_bytes(b64url_decode(value), "big")
