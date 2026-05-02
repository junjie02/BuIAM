#!/usr/bin/env python3
"""Client-side identity generation tool.

Generates a keypair and DID Document locally, then optionally submits
the DID Document to the BuIAM Gateway for registration.

This script runs on the CLIENT side and is NOT part of the Gateway server.
The Gateway only receives and validates DID Documents — it never generates
private keys for external identities.

Usage:
    # Generate keys and DID Document (no submission)
    python examples/generate_identity.py --subject-id user_123

    # Generate and submit to Gateway
    python examples/generate_identity.py --subject-id user_123 --submit

    # With a service endpoint (for agents)
    python examples/generate_identity.py --subject-id doc_agent \\
        --service-endpoint http://127.0.0.1:8011/a2a/tasks --submit

    # Custom Gateway URL
    python examples/generate_identity.py --subject-id user_123 \\
        --gateway http://127.0.0.1:8000 --submit
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate local identity and register DID with Gateway")
    parser.add_argument("--subject-id", required=True, help="Subject identifier (e.g. user_123, doc_agent)")
    parser.add_argument("--service-endpoint", default=None, help="Agent service endpoint URL (optional)")
    parser.add_argument("--gateway", default=None, help="Gateway base URL (default from BUIAM_GATEWAY_URL env or http://127.0.0.1:8000)")
    parser.add_argument("--submit", action="store_true", help="Submit the DID Document to Gateway after generation")
    parser.add_argument("--json", dest="json_output", action="store_true", help="Output only the JSON payload (for scripting)")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    # Load env if .env exists
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    gateway_url = args.gateway or os.getenv("BUIAM_GATEWAY_URL", "http://127.0.0.1:8000")

    from app.identity.did import build_did_document
    from app.identity.did_proof import create_did_proof
    from app.identity.keys import ensure_agent_keypair

    # Step 1: Generate keypair locally
    if not args.json_output:
        print(f"[1/4] Generating keypair for '{args.subject_id}'...")
    ensure_agent_keypair(args.subject_id)
    private_path = Path(os.getenv("BUIAM_KEY_DIR", "data/keys")) / f"{args.subject_id}_private.pem"
    if not args.json_output:
        print(f"      Private key: {private_path}")

    # Step 2: Build DID Document
    if not args.json_output:
        print(f"[2/4] Building DID Document...")
    did_document = build_did_document(args.subject_id, service_endpoint=args.service_endpoint)
    did = did_document["id"]
    if not args.json_output:
        print(f"      DID: {did}")

    # Step 3: Create proof (self-signature)
    if not args.json_output:
        print(f"[3/4] Signing DID Document (proof of key possession)...")
    proof = create_did_proof(did_document, args.subject_id)
    if not args.json_output:
        print(f"      Proof type: {proof['type']}")

    payload = {"did_document": did_document, "proof": proof}

    if args.submit:
        # Step 4: Submit to Gateway
        if not args.json_output:
            print(f"[4/4] Submitting to Gateway ({gateway_url})...")
        register_url = gateway_url.rstrip("/") + "/identity/did-register"
        try:
            response = httpx.post(register_url, json=payload, timeout=10)
        except httpx.HTTPError as exc:
            print(f"Error: Could not connect to Gateway at {gateway_url}: {exc}", file=sys.stderr)
            sys.exit(1)

        if response.status_code == 200:
            if args.json_output:
                print(json.dumps(response.json(), ensure_ascii=False, indent=2))
            else:
                print(f"      SUCCESS: {response.json()['status']}")
        elif response.status_code == 409:
            detail = response.json().get("detail", {})
            error_code = detail.get("error_code", "DID_ALREADY_REGISTERED") if isinstance(detail, dict) else "DID_ALREADY_REGISTERED"
            if args.json_output:
                print(json.dumps({"error": error_code, "did": did}, ensure_ascii=False))
            else:
                print(f"      Already registered: {did}")
        else:
            try:
                detail = response.json().get("detail", "")
            except Exception:
                detail = response.text
            if args.json_output:
                print(json.dumps({"error": str(detail), "status": response.status_code}, ensure_ascii=False))
            else:
                print(f"      FAILED ({response.status_code}): {detail}", file=sys.stderr)
            sys.exit(1)
    else:
        if args.json_output:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(f"\nDID Document ready. Use --submit to register with Gateway.")
            print(f"Gateway: {gateway_url}")
            print(f"DID: {did}")


if __name__ == "__main__":
    main()
