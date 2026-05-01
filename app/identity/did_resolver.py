from __future__ import annotations

from dataclasses import dataclass

from app.store.did_registry import get_did_document


class DidResolutionError(Exception):
    pass


@dataclass(frozen=True)
class ResolvedVerificationMethod:
    did: str
    verification_method_id: str
    subject_id: str
    public_key_jwk: dict[str, str]


def resolve_did_document(did: str) -> dict:
    document = get_did_document(did)
    if document is None:
        raise DidResolutionError(f"DID not found: {did}")
    return document


def resolve_verification_method(verification_method_id: str) -> ResolvedVerificationMethod:
    if "#" not in verification_method_id:
        raise DidResolutionError("verification method id must be did#fragment")
    did, _, fragment = verification_method_id.partition("#")
    if not did or not fragment:
        raise DidResolutionError("invalid verification method id")

    document = resolve_did_document(did)
    methods = document.get("verificationMethod") or []
    for method in methods:
        if str(method.get("id")) == verification_method_id:
            jwk = method.get("publicKeyJwk")
            if not isinstance(jwk, dict):
                raise DidResolutionError("verification method missing publicKeyJwk")
            kty = str(jwk.get("kty", ""))
            if kty == "ML-DSA":
                if "pk" not in jwk:
                    raise DidResolutionError("ML-DSA verification method missing pk")
                normalized = {"kty": "ML-DSA", "pk": str(jwk["pk"]), "alg": str(jwk.get("alg", "ML-DSA-65"))}
            else:
                if "n" not in jwk or "e" not in jwk:
                    raise DidResolutionError("RSA verification method missing n/e")
                normalized = {"kty": str(jwk.get("kty", "RSA")), "n": str(jwk["n"]), "e": str(jwk["e"])}
            subject_id = str((document.get("metadata") or {}).get("subject_id") or "")
            if not subject_id:
                raise DidResolutionError("DID document missing metadata.subject_id")
            return ResolvedVerificationMethod(
                did=did,
                verification_method_id=verification_method_id,
                subject_id=subject_id,
                public_key_jwk=normalized,
            )
    raise DidResolutionError(f"verification method not found: {verification_method_id}")
