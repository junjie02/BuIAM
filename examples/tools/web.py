from __future__ import annotations


def search_public_web(query: str) -> list[dict[str, str]]:
    return [
        {
            "title": f"公开网页检索结果：{query}",
            "url": "https://example.com/public-feishu-ai-report",
            "summary": "公开信息显示企业正在加速采用 AI 助手提升协作效率。",
        }
    ]
