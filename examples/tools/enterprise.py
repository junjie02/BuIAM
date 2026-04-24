from __future__ import annotations


def read_contacts() -> list[dict[str, str]]:
    return [
        {"name": "王敏", "department": "产品部", "role": "项目负责人"},
        {"name": "李雷", "department": "数据平台", "role": "数据接口人"},
    ]


def read_wiki() -> list[dict[str, str]]:
    return [
        {"title": "Q2 飞书增长复盘", "summary": "知识库记录显示协作效率提升 18%。"},
        {"title": "客户反馈整理", "summary": "多维表格和知识库打通是高频需求。"},
    ]


def read_bitable() -> list[dict[str, int | str]]:
    return [
        {"metric": "活跃项目数", "value": 32, "period": "2026-Q2"},
        {"metric": "自动化报告数", "value": 128, "period": "2026-Q2"},
    ]
