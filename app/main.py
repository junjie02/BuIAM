from __future__ import annotations

from fastapi import FastAPI

from app.gateway.routes import router as gateway_router
from app.identity.routes import router as identity_router
from app.registry.routes import router as registry_router
from app.store.audit import list_logs
from app.store.chain import list_chain
from app.store.schema import init_schema


app = FastAPI(title="BuIAM Agent Security Service")
app.include_router(gateway_router)
app.include_router(identity_router)
app.include_router(registry_router)


@app.on_event("startup")
def on_startup() -> None:
    init_schema()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/audit/logs")
def audit_logs():
    return list_logs()


@app.get("/audit/traces/{trace_id}")
def audit_trace(trace_id: str):
    return {"trace_id": trace_id, "logs": list_logs(trace_id=trace_id), "chain": list_chain(trace_id)}


@app.get("/audit/traces/{trace_id}/chain")
def audit_trace_chain(trace_id: str):
    return {"trace_id": trace_id, "delegation_chain": list_chain(trace_id)}
