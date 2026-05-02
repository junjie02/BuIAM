from __future__ import annotations

import httpx
import pytest

from app.store.did_registry import get_did_document

from tests.security_helpers import (
    GATEWAY_URL,
    USER_ID,
    generate_local_identity,
    register_did_via_api,
    run,
)


class TestDidRegistration:
    def test_register_valid_did_document_succeeds(self):
        did_doc, proof = generate_local_identity("test_valid_subject")
        response = run(register_did_via_api(
            httpx.AsyncClient(base_url=GATEWAY_URL, timeout=10),
            did_doc,
            proof,
        ))
        assert response.status_code == 200
        body = response.json()
        assert body["did"] == "did:buiam:test_valid_subject"
        assert body["status"] == "registered"

        stored = get_did_document("did:buiam:test_valid_subject")
        assert stored is not None
        assert stored["id"] == "did:buiam:test_valid_subject"

    @pytest.mark.parametrize("missing_field,error_code", [
        ("id", "DID_MISSING_ID"),
        ("verificationMethod", "DID_MISSING_VERIFICATION_METHOD"),
    ])
    def test_register_did_with_missing_top_level_fields_is_rejected(self, missing_field, error_code):
        did_doc, proof = generate_local_identity("test_missing_field")
        del did_doc[missing_field]
        response = run(register_did_via_api(
            httpx.AsyncClient(base_url=GATEWAY_URL, timeout=10),
            did_doc,
            proof,
        ))
        assert response.status_code == 400
        detail = response.json()["detail"]
        if isinstance(detail, dict):
            assert detail["error_code"] == error_code

    def test_register_did_with_wrong_did_format_is_rejected(self):
        did_doc, proof = generate_local_identity("test_bad_format")
        did_doc["id"] = "did:other:someone"
        response = run(register_did_via_api(
            httpx.AsyncClient(base_url=GATEWAY_URL, timeout=10),
            did_doc,
            proof,
        ))
        assert response.status_code == 400
        detail = response.json()["detail"]
        if isinstance(detail, dict):
            assert detail["error_code"] == "DID_INVALID_FORMAT"

    def test_register_did_with_missing_jwk_is_rejected(self):
        did_doc, proof = generate_local_identity("test_missing_jwk")
        del did_doc["verificationMethod"][0]["publicKeyJwk"]
        response = run(register_did_via_api(
            httpx.AsyncClient(base_url=GATEWAY_URL, timeout=10),
            did_doc,
            proof,
        ))
        assert response.status_code == 400
        detail = response.json()["detail"]
        if isinstance(detail, dict):
            assert detail["error_code"] == "DID_MISSING_JWK"

    def test_register_did_with_invalid_jwk_is_rejected(self):
        did_doc, proof = generate_local_identity("test_invalid_jwk")
        did_doc["verificationMethod"][0]["publicKeyJwk"] = {"kty": "UNKNOWN"}
        response = run(register_did_via_api(
            httpx.AsyncClient(base_url=GATEWAY_URL, timeout=10),
            did_doc,
            proof,
        ))
        assert response.status_code == 400
        detail = response.json()["detail"]
        if isinstance(detail, dict):
            assert detail["error_code"] == "DID_UNSUPPORTED_KEY_TYPE"

    def test_register_did_with_tampered_proof_is_rejected(self):
        did_doc, proof = generate_local_identity("test_tampered_proof")
        proof["signatureValue"] = "tampered_signature_base64"
        response = run(register_did_via_api(
            httpx.AsyncClient(base_url=GATEWAY_URL, timeout=10),
            did_doc,
            proof,
        ))
        assert response.status_code == 400
        detail = response.json()["detail"]
        if isinstance(detail, dict):
            assert detail["error_code"] == "DID_PROOF_INVALID"

    def test_register_duplicate_did_is_rejected(self):
        did_doc, proof = generate_local_identity("test_duplicate")
        client = httpx.AsyncClient(base_url=GATEWAY_URL, timeout=10)

        async def _register_twice():
            r1 = await register_did_via_api(client, did_doc, proof)
            assert r1.status_code == 200
            r2 = await register_did_via_api(client, did_doc, proof)
            return r2

        response = run(_register_twice())
        assert response.status_code == 409
        detail = response.json()["detail"]
        if isinstance(detail, dict):
            assert detail["error_code"] == "DID_ALREADY_REGISTERED"

    def test_token_issuance_blocked_for_unregistered_did(self):
        async def _try_issue():
            async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=10) as client:
                return await client.post(
                    "/identity/tokens",
                    json={
                        "agent_id": "unregistered_agent_xyz",
                        "delegated_user": USER_ID,
                        "actor_type": "user",
                        "capabilities": ["web.public:read"],
                        "ttl_seconds": 3600,
                    },
                )

        response = run(_try_issue())
        assert response.status_code == 400
        detail = response.json()["detail"]
        if isinstance(detail, dict):
            assert detail["error_code"] == "AGENT_DID_NOT_REGISTERED"

    def test_registered_did_enables_full_token_and_root_task_flow(self):
        did_doc, proof = generate_local_identity("test_e2e_user")

        async def _flow():
            async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=60) as client:
                # 1. Register DID
                r = await register_did_via_api(client, did_doc, proof)
                assert r.status_code == 200

                # 2. Issue token (now possible because DID exists)
                r = await client.post(
                    "/identity/tokens",
                    json={
                        "agent_id": "test_e2e_user",
                        "delegated_user": USER_ID,
                        "actor_type": "user",
                        "capabilities": ["web.public:read"],
                        "ttl_seconds": 3600,
                    },
                )
                assert r.status_code == 200
                token = r.json()["access_token"]

                return token

        token = run(_flow())
        assert token is not None
        assert len(token) > 20
