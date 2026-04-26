from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any

import httpx


DEFAULT_BASE_URL = "https://open.feishu.cn/open-apis"


class FeishuConfigError(RuntimeError):
    pass


class FeishuAPIError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


@dataclass(frozen=True)
class FeishuOpenAPISettings:
    app_id: str
    app_secret: str
    base_url: str = DEFAULT_BASE_URL
    timeout_seconds: float = 30.0
    contact_department_id: str | None = None
    contact_department_id_type: str = "open_department_id"
    contact_user_id_type: str = "open_id"
    calendar_id: str | None = None
    bitable_app_token: str | None = None
    bitable_table_id: str | None = None
    bitable_view_id: str | None = None
    doc_folder_token: str | None = None

    @classmethod
    def from_env(cls) -> FeishuOpenAPISettings:
        app_id = os.getenv("FEISHU_APP_ID")
        app_secret = os.getenv("FEISHU_APP_SECRET")
        if not app_id or not app_secret:
            raise FeishuConfigError("FEISHU_APP_ID and FEISHU_APP_SECRET are required")
        return cls(
            app_id=app_id,
            app_secret=app_secret,
            base_url=os.getenv("FEISHU_BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
            timeout_seconds=float(os.getenv("FEISHU_TIMEOUT_SECONDS", "30")),
            contact_department_id=os.getenv("FEISHU_CONTACT_DEPARTMENT_ID"),
            contact_department_id_type=os.getenv("FEISHU_CONTACT_DEPARTMENT_ID_TYPE", "open_department_id"),
            contact_user_id_type=os.getenv("FEISHU_CONTACT_USER_ID_TYPE", "open_id"),
            calendar_id=os.getenv("FEISHU_CALENDAR_ID"),
            bitable_app_token=os.getenv("FEISHU_BITABLE_APP_TOKEN"),
            bitable_table_id=os.getenv("FEISHU_BITABLE_TABLE_ID"),
            bitable_view_id=os.getenv("FEISHU_BITABLE_VIEW_ID"),
            doc_folder_token=os.getenv("FEISHU_DOC_FOLDER_TOKEN"),
        )


class FeishuOpenAPIClient:
    def __init__(self, settings: FeishuOpenAPISettings, *, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self._client = client
        self._tenant_token: str | None = None
        self._tenant_token_expire_at: float = 0.0

    async def list_department_users(
        self,
        *,
        department_id: str | None = None,
        page_size: int = 50,
    ) -> list[dict[str, Any]]:
        resolved_department_id = department_id or self.settings.contact_department_id
        if not resolved_department_id:
            raise FeishuConfigError("FEISHU_CONTACT_DEPARTMENT_ID is required for contact queries")
        data = await self._request(
            "GET",
            "/contact/v3/users/find_by_department",
            params={
                "department_id": resolved_department_id,
                "department_id_type": self.settings.contact_department_id_type,
                "user_id_type": self.settings.contact_user_id_type,
                "page_size": page_size,
            },
        )
        items = data.get("items", [])
        if not isinstance(items, list):
            raise FeishuAPIError("unexpected contact payload shape")
        return items

    async def list_calendar_events(
        self,
        *,
        calendar_id: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        page_size: int = 50,
    ) -> list[dict[str, Any]]:
        resolved_calendar_id = calendar_id or self.settings.calendar_id
        if not resolved_calendar_id:
            raise FeishuConfigError("FEISHU_CALENDAR_ID is required for calendar queries")
        params: dict[str, Any] = {"page_size": page_size}
        if start_time:
            params["start_time"] = start_time
        if end_time:
            params["end_time"] = end_time
        data = await self._request(
            "GET",
            f"/calendar/v4/calendars/{resolved_calendar_id}/events",
            params=params,
        )
        items = data.get("items", [])
        if not isinstance(items, list):
            raise FeishuAPIError("unexpected calendar payload shape")
        return items

    async def search_bitable_records(
        self,
        *,
        app_token: str | None = None,
        table_id: str | None = None,
        view_id: str | None = None,
        page_size: int = 50,
    ) -> list[dict[str, Any]]:
        resolved_app_token = app_token or self.settings.bitable_app_token
        resolved_table_id = table_id or self.settings.bitable_table_id
        if not resolved_app_token or not resolved_table_id:
            raise FeishuConfigError("FEISHU_BITABLE_APP_TOKEN and FEISHU_BITABLE_TABLE_ID are required for bitable queries")
        payload: dict[str, Any] = {"page_size": page_size}
        resolved_view_id = view_id or self.settings.bitable_view_id
        if resolved_view_id:
            payload["view_id"] = resolved_view_id
        data = await self._request(
            "POST",
            f"/bitable/v1/apps/{resolved_app_token}/tables/{resolved_table_id}/records/search",
            json_body=payload,
        )
        items = data.get("items", [])
        if not isinstance(items, list):
            raise FeishuAPIError("unexpected bitable payload shape")
        return items

    async def create_docx_document(
        self,
        *,
        title: str,
        folder_token: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"title": title}
        resolved_folder_token = folder_token or self.settings.doc_folder_token
        if resolved_folder_token:
            payload["folder_token"] = resolved_folder_token
        data = await self._request("POST", "/docx/v1/documents", json_body=payload)
        document = data.get("document")
        if not isinstance(document, dict):
            raise FeishuAPIError("unexpected doc create payload shape")
        return document

    async def append_docx_plain_text(
        self,
        *,
        document_id: str,
        content: str,
        root_block_id: str | None = None,
        batch_size: int = 20,
    ) -> dict[str, Any]:
        target_block_id = root_block_id or document_id
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        appended_count = 0
        for start in range(0, len(lines), batch_size):
            children = [_paragraph_block(line) for line in lines[start : start + batch_size]]
            data = await self._request(
                "POST",
                f"/docx/v1/documents/{document_id}/blocks/{target_block_id}/children",
                json_body={"children": children},
            )
            appended = data.get("children", [])
            if isinstance(appended, list):
                appended_count += len(appended)
            else:
                appended_count += len(children)
        return {
            "document_id": document_id,
            "root_block_id": target_block_id,
            "appended_blocks": appended_count,
        }

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        token = await self._tenant_access_token()
        headers = {"Authorization": f"Bearer {token}"}
        response = await self._send(method, path, params=params, json_body=json_body, headers=headers)
        payload = response.json()
        if payload.get("code") != 0:
            raise FeishuAPIError(
                f"Feishu API request failed: {payload.get('msg', 'unknown error')}",
                status_code=response.status_code,
                code=payload.get("code"),
            )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise FeishuAPIError("unexpected Feishu API response: missing data object", status_code=response.status_code)
        return data

    async def _tenant_access_token(self) -> str:
        if self._tenant_token and time.time() < self._tenant_token_expire_at:
            return self._tenant_token
        response = await self._send(
            "POST",
            "/auth/v3/tenant_access_token/internal",
            json_body={"app_id": self.settings.app_id, "app_secret": self.settings.app_secret},
            headers={},
        )
        payload = response.json()
        if payload.get("code") != 0:
            raise FeishuAPIError(
                f"failed to obtain Feishu tenant access token: {payload.get('msg', 'unknown error')}",
                status_code=response.status_code,
                code=payload.get("code"),
            )
        token = payload.get("tenant_access_token")
        expire = payload.get("expire", 0)
        if not token:
            raise FeishuAPIError("Feishu auth response did not include tenant_access_token", status_code=response.status_code)
        self._tenant_token = str(token)
        self._tenant_token_expire_at = time.time() + max(int(expire) - 60, 60)
        return self._tenant_token

    async def _send(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None,
        json_body: dict[str, Any] | None,
        headers: dict[str, str],
    ) -> httpx.Response:
        if self._client is not None:
            response = await self._client.request(
                method,
                f"{self.settings.base_url}{path}",
                params=params,
                json=json_body,
                headers=headers,
            )
            response.raise_for_status()
            return response

        async with httpx.AsyncClient(timeout=self.settings.timeout_seconds) as client:
            response = await client.request(
                method,
                f"{self.settings.base_url}{path}",
                params=params,
                json=json_body,
                headers=headers,
            )
            response.raise_for_status()
            return response


def _paragraph_block(text: str) -> dict[str, Any]:
    return {
        "block_type": 2,
        "paragraph": {
            "elements": [
                {
                    "text_run": {"content": text},
                    "type": "text_run",
                }
            ]
        },
    }
