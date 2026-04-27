from __future__ import annotations

from examples.agent.doc_agent import handle_task
from examples.agent.service_factory import create_agent_app


app = create_agent_app(title="doc_agent", handler=handle_task)
