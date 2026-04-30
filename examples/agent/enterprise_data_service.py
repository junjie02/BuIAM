from __future__ import annotations

from examples.agent.enterprise_data_agent import handle_task
from examples.agent.service_factory import create_agent_app


app = create_agent_app(title="enterprise_data_agent", handler=handle_task)
