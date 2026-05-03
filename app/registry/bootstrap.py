from __future__ import annotations

import logging
import os

from app.delegation.credential_crypto import build_agent_capability_vc
from app.identity.did import build_did, build_did_document
from app.identity.keys import ensure_agent_keypair, ensure_system_keypair
from app.registry.policy import evaluate_capability_request
from app.store.delegation_credentials import upsert_credential
from app.store.did_registry import get_did_document, upsert_did_document
from app.store.registry import upsert_agent

logger = logging.getLogger("buiam.registry.bootstrap")

USER_ID = os.getenv("BUIAM_DEMO_USER_ID", "user_123")

DEMO_AGENTS = [
    {
        "agent_id": "doc_agent",
        "name": "Feishu Doc Agent",
        "agent_type": "doc_agent",
        "description": "Coordinates report generation and writes the final Feishu document.",
        "endpoint_env": "DOC_AGENT_ENDPOINT",
        "default_endpoint": "http://127.0.0.1:8011/a2a/tasks",
        "static_capabilities": [
            "report:write",
            "feishu.doc:write",
            "feishu.contact:read",
            "feishu.calendar:read",
            "feishu.wiki:read",
            "feishu.bitable:read",
            "web.public:read",
        ],
    },
    {
        "agent_id": "enterprise_data_agent",
        "name": "Enterprise Data Agent",
        "agent_type": "enterprise_data_agent",
        "description": "Provides mock enterprise data for the demo flow.",
        "endpoint_env": "ENTERPRISE_DATA_AGENT_ENDPOINT",
        "default_endpoint": "http://127.0.0.1:8012/a2a/tasks",
        "static_capabilities": [
            "feishu.contact:read",
            "feishu.calendar:read",
            "feishu.wiki:read",
            "feishu.bitable:read",
        ],
    },
    {
        "agent_id": "external_search_agent",
        "name": "External Search Agent",
        "agent_type": "external_search_agent",
        "description": "Provides mock public web results and demonstrates denied escalation.",
        "endpoint_env": "EXTERNAL_SEARCH_AGENT_ENDPOINT",
        "default_endpoint": "http://127.0.0.1:8013/a2a/tasks",
        "static_capabilities": ["web.public:read"],
    },
]


def ensure_local_identities() -> None:
    """Generate keypairs and store DID Documents locally (client-side simulation).

    This simulates what a real client does via ``examples/generate_identity.py``.
    Keys are written to ``data/keys/`` and DID Documents are injected directly
    into the local SQLite database — no HTTP API calls needed.

    This function exists for demo / test convenience. In production each entity
    runs its own ``generate_identity.py`` and submits its DID Document via the
    Gateway API.
    """
    ensure_agent_keypair(USER_ID)
    user_doc = build_did_document(USER_ID)
    upsert_did_document(did=build_did(USER_ID), subject_id=USER_ID, document=user_doc)
    for agent in DEMO_AGENTS:
        agent_id = str(agent["agent_id"])
        endpoint = os.getenv(str(agent["endpoint_env"]), str(agent["default_endpoint"]))
        ensure_agent_keypair(agent_id)
        did_doc = build_did_document(agent_id, service_endpoint=endpoint)
        upsert_did_document(did=build_did(agent_id), subject_id=agent_id, document=did_doc)


def register_did_documents() -> int:
    """Store DID Documents for demo identities (direct DB injection, no API).

    Returns the number of DID Documents written.
    """
    count = 0
    user_did = build_did(USER_ID)
    if get_did_document(user_did) is None:
        user_doc = build_did_document(USER_ID)
        upsert_did_document(did=user_did, subject_id=USER_ID, document=user_doc)
        count += 1
    for agent in DEMO_AGENTS:
        agent_id = str(agent["agent_id"])
        agent_did = build_did(agent_id)
        if get_did_document(agent_did) is None:
            endpoint = os.getenv(str(agent["endpoint_env"]), str(agent["default_endpoint"]))
            did_doc = build_did_document(agent_id, service_endpoint=endpoint)
            upsert_did_document(did=agent_did, subject_id=agent_id, document=did_doc)
            count += 1
    return count


def register_agent_metadata() -> int:
    """Register agent metadata in the agents table.

    Requires DID Documents to already exist in ``did_documents`` table.
    Skips agents whose DID is not yet registered and logs a warning.

    Returns the number of agents registered.
    """
    ensure_system_keypair()
    # Ensure the Gateway system identity has a DID document for signing Agent VCs
    from app.delegation.credential_crypto import GATEWAY_SYSTEM_ID
    system_did = build_did(GATEWAY_SYSTEM_ID)
    if get_did_document(system_did) is None:
        system_doc = build_did_document(GATEWAY_SYSTEM_ID)
        upsert_did_document(did=system_did, subject_id=GATEWAY_SYSTEM_ID, document=system_doc)
    registered = 0
    for agent in DEMO_AGENTS:
        agent_id = str(agent["agent_id"])
        if get_did_document(build_did(agent_id)) is None:
            logger.warning(
                "Skipping agent '%s': DID Document not registered. "
                "Run 'python examples/generate_identity.py --subject-id %s --submit' first.",
                agent_id,
                agent_id,
            )
            continue
        endpoint = os.getenv(str(agent["endpoint_env"]), str(agent["default_endpoint"]))
        requested_caps = list(agent["static_capabilities"])
        # Run through capability approval policy
        approval = evaluate_capability_request(
            subject_type="agent",
            agent_type=str(agent["agent_type"]),
            requested=requested_caps,
        )
        if approval["decision"] == "denied":
            logger.warning(
                "Skipping agent '%s': capability request denied — %s", agent_id, approval["reason"]
            )
            continue
        granted = approval["granted"]
        upsert_agent(
            agent_id=agent_id,
            name=str(agent["name"]),
            agent_type=str(agent["agent_type"]),
            description=str(agent["description"]),
            owner_org="demo",
            allowed_resource_domains=["feishu", "public_web"],
            status="active",
            endpoint=endpoint,
            static_capabilities=granted,
        )
        # Issue Agent Capability VC with approved capabilities
        vc = build_agent_capability_vc(
            agent_id=agent_id,
            capabilities=granted,
            endpoint=endpoint,
            agent_type=str(agent["agent_type"]),
        )
        upsert_credential(vc)
        logger.info(
            "Issued Agent Capability VC for %s (%s): %s",
            agent_id, approval["decision"], vc.credential_id,
        )
        registered += 1
    return registered


def bootstrap_demo_identities_locally() -> None:
    """Full local bootstrap for tests and demo scripts.

    Generates keypairs (if missing), writes DID Documents, and registers
    agent metadata — all via direct DB access without HTTP API calls.

    Use this in tests, security scripts, and the demo.py launcher
    where Gateway and clients run on the same host.
    """
    ensure_local_identities()
    register_agent_metadata()


def register_demo_agents() -> None:
    """Gateway startup: register agent metadata for identities whose DID Documents
    are already in the database.

    This function no longer generates keypairs. Entities must generate their
    own keypairs and submit DID Documents before the Gateway can recognise them.

    For demo convenience, the Gateway prints guidance when DID Documents are
    missing so the operator can run ``examples/generate_identity.py``.
    """
    user_did = build_did(USER_ID)
    if get_did_document(user_did) is None:
        logger.warning(
            "Demo user DID Document not found (%s). "
            "Run 'python examples/generate_identity.py --subject-id %s --submit' to register it.",
            user_did,
            USER_ID,
        )
        for agent in DEMO_AGENTS:
            agent_id = str(agent["agent_id"])
            agent_did = build_did(agent_id)
            if get_did_document(agent_did) is None:
                logger.warning(
                    "Agent DID Document not found (%s). "
                    "Run 'python examples/generate_identity.py --subject-id %s --submit' to register it.",
                    agent_did,
                    agent_id,
                )
        return

    registered = register_agent_metadata()
    if registered == 0:
        logger.warning("No agent metadata registered. Ensure DID Documents exist first.")
