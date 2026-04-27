from .doc_agent import AGENT_ID as DOC_AGENT_ID, handle_task as handle_doc_task
from .enterprise_data_agent import (
    AGENT_ID as ENTERPRISE_DATA_AGENT_ID,
    handle_task as handle_enterprise_data_task,
)
from .external_search_agent import (
    AGENT_ID as EXTERNAL_SEARCH_AGENT_ID,
    handle_task as handle_external_search_task,
)

__all__ = [
    "DOC_AGENT_ID",
    "ENTERPRISE_DATA_AGENT_ID",
    "EXTERNAL_SEARCH_AGENT_ID",
    "handle_doc_task",
    "handle_enterprise_data_task",
    "handle_external_search_task",
]
