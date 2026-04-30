from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.registry.bootstrap import register_demo_agents
from app.store.registry import list_agents


def main() -> None:
    register_demo_agents()
    print(json.dumps([agent.__dict__ | {
        "allowed_resource_domains": sorted(agent.allowed_resource_domains),
        "static_capabilities": sorted(agent.static_capabilities),
    } for agent in list_agents()], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
