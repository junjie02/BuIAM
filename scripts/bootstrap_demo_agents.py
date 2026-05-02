from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.registry.bootstrap import bootstrap_demo_identities_locally
from app.store.did_registry import list_did_documents
from app.store.registry import list_agents


def main() -> None:
    bootstrap_demo_identities_locally()
    print(
        json.dumps(
            {
                "agents": [
                    agent.__dict__
                    | {
                        "allowed_resource_domains": sorted(agent.allowed_resource_domains),
                        "static_capabilities": sorted(agent.static_capabilities),
                    }
                    for agent in list_agents()
                ],
                "did_documents": list_did_documents(),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
