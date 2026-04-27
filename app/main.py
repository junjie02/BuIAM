from __future__ import annotations
import time
from dotenv import load_dotenv
from fastapi import FastAPI

load_dotenv()

from app.gateway.routes import router as gateway_router
from app.identity.routes import router as identity_router
from app.registry.routes import router as registry_router
from app.store.audit import list_logs, cleanup_expired_audit_logs
from app.store.auth_events import list_auth_events
from app.store.chain import list_chain
from app.store.delegation_credentials import list_credentials
from app.store.intent_tree import get_intent_node, list_intent_tree
from app.store.schema import init_schema
from app.store.tokens import cleanup_expired_tokens

# 定时任务功能（可选，需要安装apscheduler后启用）
# from apscheduler.schedulers.background import BackgroundScheduler
# scheduler = BackgroundScheduler()


app = FastAPI(title="BuIAM Agent Security Service")
app.include_router(gateway_router)
app.include_router(identity_router)
app.include_router(registry_router)


# def run_cleanup_jobs() -> None:
#     """执行所有过期数据清理任务"""
#     token_cleaned = cleanup_expired_tokens()
#     audit_cleaned = cleanup_expired_audit_logs()
#     print(f"[定时清理任务] 清理过期令牌: {token_cleaned}条，清理过期审计日志: {audit_cleaned}条")


@app.on_event("startup")
def on_startup() -> None:
    init_schema()
    
    # 定时任务功能需要安装apscheduler后启用
    # # 每天凌晨2点执行一次过期数据清理
    # scheduler.add_job(
    #     run_cleanup_jobs,
    #     "cron",
    #     hour=2,
    #     minute=0,
    #     id="daily_cleanup_job",
    #     replace_existing=True
    # )
    # scheduler.start()
    # print("[定时任务] 已启动每日过期数据清理任务，执行时间：每天凌晨2点")


# @app.on_event("shutdown")
# def on_shutdown() -> None:
#     """应用关闭时停止定时任务"""
#     scheduler.shutdown()
#     print("[定时任务] 已停止所有定时任务")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/audit/logs")
def audit_logs():
    return list_logs()


@app.get("/audit/auth-events")
def audit_auth_events(
    trace_id: str | None = None,
    request_id: str | None = None,
    jti: str | None = None,
    agent_id: str | None = None,
    decision: str | None = None,
):
    return list_auth_events(
        trace_id=trace_id,
        request_id=request_id,
        jti=jti,
        agent_id=agent_id,
        decision=decision,
    )


@app.get("/audit/traces/{trace_id}")
def audit_trace(trace_id: str):
    return {
        "trace_id": trace_id,
        "logs": list_logs(trace_id=trace_id),
        "chain": list_chain(trace_id),
        "delegation_credentials": [
            credential.model_dump() for credential in list_credentials(trace_id=trace_id)
        ],
        "auth_events": list_auth_events(trace_id=trace_id),
        "intent_tree": list_intent_tree(trace_id),
    }


@app.get("/audit/traces/{trace_id}/chain")
def audit_trace_chain(trace_id: str):
    return {"trace_id": trace_id, "delegation_chain": list_chain(trace_id)}


@app.get("/audit/traces/{trace_id}/credentials")
def audit_trace_credentials(trace_id: str):
    return {
        "trace_id": trace_id,
        "delegation_credentials": [
            credential.model_dump() for credential in list_credentials(trace_id=trace_id)
        ],
    }


@app.get("/audit/traces/{trace_id}/intent-tree")
def audit_trace_intent_tree(trace_id: str):
    return {"trace_id": trace_id, "intent_tree": list_intent_tree(trace_id)}


@app.get("/audit/intent-nodes/{node_id}")
def audit_intent_node(node_id: str):
    return get_intent_node(node_id) or {"error_code": "INTENT_NODE_NOT_FOUND", "node_id": node_id}
