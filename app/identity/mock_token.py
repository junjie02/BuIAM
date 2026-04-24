from __future__ import annotations

import hashlib
from typing import Any

from app.protocol import AuthContext


def _signature_material(actor: str, capabilities: list[str]) -> str:
    return actor + str(capabilities)


def sign_token(token: dict[str, Any]) -> dict[str, Any]:
    actor = str(token.get("actor") or token.get("agent_id") or token.get("sub"))
    capabilities = list(token.get("capabilities", []))
    token["sig"] = hashlib.sha256(
        _signature_material(actor, capabilities).encode()
    ).hexdigest()
    return token


def verify_sig(token: AuthContext) -> bool:
    if token.sig is None:
        return True
    expected = hashlib.sha256(
        _signature_material(token.agent_id, list(token.capabilities)).encode()
    ).hexdigest()
    return token.sig == expected


def verify_token_source(auth_context: AuthContext) -> bool:
    return True
