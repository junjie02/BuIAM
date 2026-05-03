from __future__ import annotations

import pytest

from tests.security_helpers import build_server_handles, reset_runtime_db


def pytest_addoption(parser):
    parser.addoption("--json", action="store_true", default=False, help="Print full VC presentation JSON")


@pytest.fixture(scope="session", autouse=True)
def servers():
    reset_runtime_db()
    handles = build_server_handles()
    for handle in handles:
        handle.start()
    yield
    for handle in reversed(handles):
        handle.stop()
