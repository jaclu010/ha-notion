"""Thin async client for the Notion API (no Home Assistant imports)."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import quote

import aiohttp
from yarl import URL

from .const import MAX_QUERY_ROWS, NOTION_API_BASE, NOTION_VERSION

_LOGGER = logging.getLogger(__name__)

TokenGetter = Callable[[], Awaitable[str]]

LOG_TRUNCATE = 2000
RETRY_STATUSES = {429, 500, 502, 503, 504}


class NotionError(Exception):
    """Base error for Notion API failures."""

    def __init__(self, message: str, *, status: int | None = None, code: str | None = None) -> None:
        """Initialize the error."""
        super().__init__(message)
        self.status = status
        self.code = code


class NotionAuthError(NotionError):
    """Token missing, expired or revoked."""


class NotionNotFound(NotionError):
    """Object does not exist or is not shared with the token."""


class NotionConnectionError(NotionError):
    """Network problem talking to Notion."""


def truncate(value: Any, limit: int = LOG_TRUNCATE) -> str:
    """Render a value for debug logging, capped at limit characters."""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    if len(text) > limit:
        return f"{text[:limit]}… (+{len(text) - limit} chars)"
    return text


def plain_text(rich_text: list[dict[str, Any]] | None) -> str:
    """Join Notion rich text items to a plain string."""
    if not rich_text:
        return ""
    return "".join(item.get("plain_text") or item.get("text", {}).get("content", "") for item in rich_text)


def object_title(obj: dict[str, Any]) -> str:
    """Return the title of a page or data source object."""
    if obj.get("object") == "data_source" or "title" in obj and isinstance(obj["title"], list):
        return plain_text(obj.get("title"))
    for prop in (obj.get("properties") or {}).values():
        if prop.get("type") == "title":
            return plain_text(prop.get("title"))
    return ""


def object_icon(obj: dict[str, Any]) -> str:
    """Return an emoji icon for a page or data source, or an empty string."""
    icon = obj.get("icon") or {}
    if icon.get("type") == "emoji":
        return icon.get("emoji", "")
    return ""


class NotionClient:
    """Minimal Notion REST client with throttling, retries and a call history."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        token_getter: TokenGetter,
        *,
        min_interval: float = 0.34,
        max_retries: int = 3,
        history_size: int = 50,
    ) -> None:
        """Initialize the client."""
        self._session = session
        self._token_getter = token_getter
        self._min_interval = min_interval
        self._max_retries = max_retries
        self._throttle_lock = asyncio.Lock()
        self._last_request = 0.0
        self._user_cache: dict[str, dict[str, Any] | None] = {}
        self.history: deque[dict[str, Any]] = deque(maxlen=history_size)

    async def _throttle(self) -> None:
        """Keep the average request rate under Notion's ~3 requests/second."""
        async with self._throttle_lock:
            wait = self._last_request + self._min_interval - time.monotonic()
            if wait > 0:
                _LOGGER.debug("Throttling %.0f ms", wait * 1000)
                await asyncio.sleep(wait)
            self._last_request = time.monotonic()

    async def request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        query: list[tuple[str, str]] | None = None,
    ) -> dict[str, Any]:
        """Send a request and return the decoded JSON body.

        Query values are sent as given (Notion property ids are already
        URL-encoded and must not be encoded twice).
        """
        url = f"{NOTION_API_BASE}{path}"
        if query:
            url += "?" + "&".join(f"{quote(k, safe='[]')}={v}" for k, v in query)
        if body is not None and method != "GET":
            _LOGGER.debug("%s %s body: %s", method, path, truncate(body))

        attempt = 0
        while True:
            attempt += 1
            await self._throttle()
            headers = {
                "Authorization": f"Bearer {await self._token_getter()}",
                "Notion-Version": NOTION_VERSION,
                "Accept": "application/json",
            }
            started = time.monotonic()
            try:
                async with self._session.request(
                    method,
                    URL(url, encoded=True),
                    json=body,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    status = resp.status
                    retry_after = resp.headers.get("Retry-After")
                    text = await resp.text()
            except (aiohttp.ClientError, TimeoutError) as err:
                self._record(method, path, None, started, attempt, str(err))
                _LOGGER.debug("%s %s failed (attempt %s): %s", method, path, attempt, err)
                if attempt <= self._max_retries:
                    await asyncio.sleep(min(2**attempt, 10))
                    continue
                raise NotionConnectionError(f"Error talking to Notion: {err}") from err

            self._record(method, path, status, started, attempt)
            _LOGGER.debug(
                "%s %s -> %s (%.0f ms, attempt %s)",
                method,
                path,
                status,
                (time.monotonic() - started) * 1000,
                attempt,
            )
            try:
                data = json.loads(text) if text else {}
            except ValueError:
                data = {"message": text}

            if status in RETRY_STATUSES and attempt <= self._max_retries:
                try:
                    delay = float(retry_after) if retry_after else float(2**attempt)
                except ValueError:
                    delay = float(2**attempt)
                _LOGGER.debug(
                    "Notion returned %s, retrying in %.1f s (Retry-After=%s)",
                    status,
                    delay,
                    retry_after,
                )
                await asyncio.sleep(min(delay, 60))
                continue
            if status >= 400:
                raise self._error(status, data)
            return data

    def _record(
        self,
        method: str,
        path: str,
        status: int | None,
        started: float,
        attempt: int,
        error: str | None = None,
    ) -> None:
        self.history.append(
            {
                "time": time.time(),
                "method": method,
                "path": path,
                "status": status,
                "duration_ms": round((time.monotonic() - started) * 1000),
                "attempt": attempt,
                "error": error,
            }
        )

    @staticmethod
    def _error(status: int, data: dict[str, Any]) -> NotionError:
        code = data.get("code")
        message = f"Notion API error {status} ({code}): {data.get('message', '')}"
        _LOGGER.debug("%s", message)
        if status == 401 or code == "unauthorized":
            return NotionAuthError(message, status=status, code=code)
        if status == 404 or code == "object_not_found":
            return NotionNotFound(message, status=status, code=code)
        return NotionError(message, status=status, code=code)

    async def _paginate(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        query: list[tuple[str, str]] | None = None,
        max_results: int | None = None,
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            if method == "GET":
                page_query = list(query or [])
                page_query.append(("page_size", "100"))
                if cursor:
                    page_query.append(("start_cursor", cursor))
                data = await self.request(method, path, query=page_query)
            else:
                page_body = dict(body or {})
                page_body["page_size"] = 100
                if cursor:
                    page_body["start_cursor"] = cursor
                data = await self.request(method, path, body=page_body, query=query)
            results.extend(data.get("results", []))
            if max_results is not None and len(results) >= max_results:
                _LOGGER.debug("%s: stopping at %s results (cap)", path, max_results)
                return results[:max_results]
            if not data.get("has_more") or not data.get("next_cursor"):
                return results
            cursor = data["next_cursor"]

    # Users

    async def users_me(self) -> dict[str, Any]:
        """Return the user or bot the token belongs to."""
        return await self.request("GET", "/users/me")

    async def retrieve_user(self, user_id: str) -> dict[str, Any] | None:
        """Return a user object (cached); None when it cannot be read."""
        if user_id not in self._user_cache:
            try:
                self._user_cache[user_id] = await self.request("GET", f"/users/{user_id}")
            except NotionAuthError:
                raise
            except NotionError as err:
                _LOGGER.debug("Could not resolve user %s: %s", user_id, err)
                self._user_cache[user_id] = None
        return self._user_cache[user_id]

    def remember_user(self, user: dict[str, Any]) -> None:
        """Cache a user object seen in another response."""
        if user.get("id") and user.get("name"):
            self._user_cache[user["id"]] = user

    # Search

    async def search(
        self,
        query: str | None = None,
        *,
        object_type: str | None = None,
        max_results: int | None = None,
    ) -> list[dict[str, Any]]:
        """Search pages or data sources the token can access."""
        body: dict[str, Any] = {"sort": {"direction": "descending", "timestamp": "last_edited_time"}}
        if query:
            body["query"] = query
        if object_type:
            body["filter"] = {"property": "object", "value": object_type}
        return await self._paginate("POST", "/search", body=body, max_results=max_results)

    # Data sources and views

    async def retrieve_data_source(self, data_source_id: str) -> dict[str, Any]:
        """Return a data source including its property schema."""
        return await self.request("GET", f"/data_sources/{data_source_id}")

    async def query_data_source(
        self,
        data_source_id: str,
        *,
        filter_: dict[str, Any] | None = None,
        sorts: list[dict[str, Any]] | None = None,
        filter_properties: list[str] | None = None,
        max_results: int = MAX_QUERY_ROWS,
    ) -> list[dict[str, Any]]:
        """Return the pages of a data source."""
        body: dict[str, Any] = {}
        if filter_:
            body["filter"] = filter_
        if sorts:
            body["sorts"] = sorts
        if filter_ or sorts:
            _LOGGER.debug("Query %s filter=%s sorts=%s", data_source_id, truncate(filter_), truncate(sorts))
        query = [("filter_properties[]", prop) for prop in filter_properties or []]
        return await self._paginate(
            "POST",
            f"/data_sources/{data_source_id}/query",
            body=body,
            query=query,
            max_results=max_results,
        )

    async def list_view_ids(self, *, database_id: str) -> list[str]:
        """Return the ids of the views of a database."""
        results = await self._paginate("GET", "/views", query=[("database_id", database_id)], max_results=100)
        return [view["id"] for view in results if view.get("id")]

    async def retrieve_view(self, view_id: str) -> dict[str, Any]:
        """Return a view with its filter and sorts."""
        return await self.request("GET", f"/views/{view_id}")

    # Pages

    async def retrieve_page(self, page_id: str) -> dict[str, Any]:
        """Return a page with its properties."""
        return await self.request("GET", f"/pages/{page_id}")

    async def create_page(self, data_source_id: str, properties: dict[str, Any]) -> dict[str, Any]:
        """Create a page (row) in a data source."""
        return await self.request(
            "POST",
            "/pages",
            body={
                "parent": {"type": "data_source_id", "data_source_id": data_source_id},
                "properties": properties,
            },
        )

    async def update_page(self, page_id: str, properties: dict[str, Any]) -> dict[str, Any]:
        """Update page properties."""
        return await self.request("PATCH", f"/pages/{page_id}", body={"properties": properties})

    async def get_page_markdown(self, page_id: str) -> dict[str, Any]:
        """Return the page content as Notion enhanced markdown."""
        return await self.request("GET", f"/pages/{page_id}/markdown")
