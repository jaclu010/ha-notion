"""Data update coordinators for Notion databases and pages."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import NotionAuthError, NotionClient, NotionError, NotionNotFound, object_title, truncate
from .const import (
    CONF_COLUMNS,
    CONF_DATA_SOURCE_ID,
    CONF_PAGE_ID,
    CONF_PROPERTIES,
    CONF_SCAN_INTERVAL,
    CONF_SHOW_MARKDOWN,
    CONF_VIEW_ID,
    DEFAULT_DATABASE_SCAN_INTERVAL,
    DEFAULT_PAGE_SCAN_INTERVAL,
    DOMAIN,
    MARKDOWN_EDIT_GRACE_SECONDS,
    MARKDOWN_FILE_REFRESH_SECONDS,
    SCHEMA_REFRESH_SECONDS,
)
from .mapping import (
    DEFAULT_SORTS,
    SchemaProperty,
    build_filter,
    drop_old_completed,
    filter_property_ids,
    schema_properties,
)
from .markdown import MarkdownResult, convert_markdown
from .properties import parse_notion_date, user_ids

if TYPE_CHECKING:
    from . import NotionConfigEntry

_LOGGER = logging.getLogger(__name__)

# Rows written from HA are kept for a short while in case Notion's query
# results lag behind the write.
RECENT_WRITE_SECONDS = 30


async def resolve_user_names(client: NotionClient, ids: set[str]) -> dict[str, str]:
    """Look up names for user ids (cached in the client)."""
    names: dict[str, str] = {}
    for user_id in ids:
        user = await client.retrieve_user(user_id)
        if user and user.get("name"):
            names[user_id] = user["name"]
    return names


class _NotionCoordinator[T](DataUpdateCoordinator[T]):
    """Shared error handling."""

    config_entry: NotionConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: NotionConfigEntry,
        subentry: ConfigSubentry,
        client: NotionClient,
        default_interval: int,
    ) -> None:
        self.subentry = subentry
        self.config: dict[str, Any] = dict(subentry.data)
        self.client = client
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {subentry.title}",
            update_interval=timedelta(seconds=self.config.get(CONF_SCAN_INTERVAL, default_interval)),
        )

    async def _async_update_data(self) -> T:
        try:
            return await self._fetch()
        except NotionAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except NotionNotFound as err:
            raise UpdateFailed(f"{self.subentry.title}: not found in Notion or no access ({err})") from err
        except NotionError as err:
            raise UpdateFailed(f"{self.subentry.title}: {err}") from err

    async def _fetch(self) -> T:
        raise NotImplementedError


@dataclass(slots=True)
class DatabaseData:
    """Rows and schema of a data source."""

    data_source: dict[str, Any]
    rows: list[dict[str, Any]]
    user_names: dict[str, str] = field(default_factory=dict)

    @property
    def props(self) -> list[SchemaProperty]:
        """Schema properties."""
        return schema_properties(self.data_source)


class NotionDatabaseCoordinator(_NotionCoordinator[DatabaseData]):
    """Polls a data source (optionally through one of its views)."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: NotionConfigEntry,
        subentry: ConfigSubentry,
        client: NotionClient,
        me_id: str | None,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(hass, entry, subentry, client, DEFAULT_DATABASE_SCAN_INTERVAL)
        self.me_id = me_id
        self.data_source_id: str = self.config[CONF_DATA_SOURCE_ID]
        self._data_source: dict[str, Any] | None = None
        self._data_source_at = 0.0
        self._view: dict[str, Any] | None = None
        self._view_at = 0.0
        self._previous: dict[str, dict[str, str]] = {}
        self._recent_writes: dict[str, tuple[dict[str, Any], float]] = {}

    async def _fetch(self) -> DatabaseData:
        monotonic = time.monotonic()
        if self._data_source is None or monotonic - self._data_source_at > SCHEMA_REFRESH_SECONDS:
            self._data_source = await self.client.retrieve_data_source(self.data_source_id)
            self._data_source_at = monotonic
            _LOGGER.debug(
                "%s: schema loaded (%s properties)",
                self.subentry.title,
                len(self._data_source.get("properties") or {}),
            )
        props = schema_properties(self._data_source)

        now = dt_util.utcnow()
        view_id = self.config.get(CONF_VIEW_ID)
        if view_id:
            if self._view is None or monotonic - self._view_at > SCHEMA_REFRESH_SECONDS:
                self._view = await self.client.retrieve_view(view_id)
                self._view_at = monotonic
                _LOGGER.debug(
                    "%s: view '%s' loaded, filter=%s sorts=%s",
                    self.subentry.title,
                    self._view.get("name"),
                    truncate(self._view.get("filter")),
                    truncate(self._view.get("sorts")),
                )
            filter_ = self._view.get("filter")
            sorts = self._view.get("sorts") or DEFAULT_SORTS
        else:
            filter_ = build_filter(self.config, props, now)
            sorts = DEFAULT_SORTS

        rows = await self.client.query_data_source(
            self.data_source_id,
            filter_=filter_,
            sorts=sorts,
            filter_properties=filter_property_ids(self.config),
        )
        rows = self._merge_recent_writes(rows)
        visible = drop_old_completed(rows, self.config, now)
        _LOGGER.debug(
            "%s: %s rows fetched, %s visible (%s old completed hidden)",
            self.subentry.title,
            len(rows),
            len(visible),
            len(rows) - len(visible),
        )

        columns = set(self.config.get(CONF_COLUMNS) or [])
        missing: set[str] = set()
        for row in visible:
            missing |= user_ids(row, columns)
        user_names = await resolve_user_names(self.client, missing)

        self._log_diff(visible)
        return DatabaseData(self._data_source, visible, user_names)

    def _merge_recent_writes(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Prefer rows written from HA when the query has not caught up yet."""
        if not self._recent_writes:
            return rows
        cutoff = time.monotonic() - RECENT_WRITE_SECONDS
        self._recent_writes = {k: v for k, v in self._recent_writes.items() if v[1] >= cutoff}
        by_id = {row["id"]: index for index, row in enumerate(rows)}
        for page_id, (page, _) in self._recent_writes.items():
            if page_id not in by_id:
                _LOGGER.debug(
                    "%s: keeping just written row %s not yet in query", self.subentry.title, page_id
                )
                rows.append(page)
                continue
            current = rows[by_id[page_id]]
            if (current.get("last_edited_time") or "") < (page.get("last_edited_time") or ""):
                _LOGGER.debug(
                    "%s: query returned stale row %s, using written copy", self.subentry.title, page_id
                )
                rows[by_id[page_id]] = page
        return rows

    def _log_diff(self, rows: list[dict[str, Any]]) -> None:
        """Log added, removed and changed rows (debug only)."""
        if not _LOGGER.isEnabledFor(logging.DEBUG):
            return
        current = {
            row["id"]: {
                name: json.dumps(prop.get(prop.get("type", "")), sort_keys=True, ensure_ascii=False)
                for name, prop in (row.get("properties") or {}).items()
            }
            for row in rows
        }
        if self._previous:
            added = [object_title(r) for r in rows if r["id"] not in self._previous]
            removed = [row_id for row_id in self._previous if row_id not in current]
            changed = {}
            for row_id, props in current.items():
                before = self._previous.get(row_id)
                if before is None:
                    continue
                # Write responses carry all properties, queries only the selected ones.
                diff = [name for name, value in props.items() if name in before and before[name] != value]
                if diff:
                    changed[row_id] = diff
            if added or removed or changed:
                _LOGGER.debug(
                    "%s: diff added=%s removed=%s changed=%s",
                    self.subentry.title,
                    added,
                    removed,
                    truncate(changed),
                )
        self._previous = current

    def apply_written_page(self, page: dict[str, Any]) -> None:
        """Use a create/update response right away (optimistic update)."""
        self._recent_writes[page["id"]] = (page, time.monotonic())
        if self.data is None:
            return
        rows = list(self.data.rows)
        for index, row in enumerate(rows):
            if row["id"] == page["id"]:
                rows[index] = page
                break
        else:
            rows.append(page)
        self.async_set_updated_data(replace(self.data, rows=rows))

    def row(self, page_id: str) -> dict[str, Any] | None:
        """Return a row by id."""
        if self.data is None:
            return None
        return next((row for row in self.data.rows if row["id"] == page_id), None)


@dataclass(slots=True)
class PageData:
    """A page, its parent schema and converted markdown."""

    page: dict[str, Any]
    data_source: dict[str, Any] | None
    markdown: MarkdownResult | None
    markdown_truncated: bool
    markdown_fetched: datetime | None
    user_names: dict[str, str] = field(default_factory=dict)

    @property
    def schema_by_id(self) -> dict[str, SchemaProperty]:
        """Parent data source schema by property id."""
        if not self.data_source:
            return {}
        return {prop.id: prop for prop in schema_properties(self.data_source)}


class NotionPageCoordinator(_NotionCoordinator[PageData]):
    """Polls a page; fetches markdown only when it may have changed."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: NotionConfigEntry,
        subentry: ConfigSubentry,
        client: NotionClient,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(hass, entry, subentry, client, DEFAULT_PAGE_SCAN_INTERVAL)
        self.page_id: str = self.config[CONF_PAGE_ID]
        self._data_source: dict[str, Any] | None = None
        self._data_source_at = 0.0
        self._markdown: MarkdownResult | None = None
        self._markdown_truncated = False
        self._markdown_fetched: datetime | None = None
        self._markdown_edited: str | None = None

    async def _fetch(self) -> PageData:
        page = await self.client.retrieve_page(self.page_id)
        parent = page.get("parent") or {}
        monotonic = time.monotonic()
        if parent.get("type") == "data_source_id" and (
            self._data_source is None or monotonic - self._data_source_at > SCHEMA_REFRESH_SECONDS
        ):
            try:
                self._data_source = await self.client.retrieve_data_source(parent["data_source_id"])
            except NotionAuthError:
                raise
            except NotionError as err:
                _LOGGER.debug("%s: parent schema not readable: %s", self.subentry.title, err)
                self._data_source = None
            self._data_source_at = monotonic

        selected = set(self.config.get(CONF_PROPERTIES) or [])
        user_names = await resolve_user_names(self.client, user_ids(page, selected))

        if self.config.get(CONF_SHOW_MARKDOWN, True):
            reason = self._markdown_reason(page)
            if reason:
                response = await self.client.get_page_markdown(self.page_id)
                self._markdown = convert_markdown(response.get("markdown") or "")
                self._markdown_truncated = bool(response.get("truncated"))
                self._markdown_fetched = dt_util.utcnow()
                self._markdown_edited = page.get("last_edited_time")
                _LOGGER.debug(
                    "%s: markdown fetched (%s): %s chars in, %s chars out, truncated=%s, expiring files=%s, unknown tags=%s",
                    self.subentry.title,
                    reason,
                    len(response.get("markdown") or ""),
                    len(self._markdown.markdown),
                    self._markdown_truncated,
                    self._markdown.has_expiring_files,
                    sorted(self._markdown.unknown_tags) or "none",
                )
            else:
                _LOGGER.debug("%s: page unchanged, markdown not refetched", self.subentry.title)

        return PageData(
            page=page,
            data_source=self._data_source,
            markdown=self._markdown,
            markdown_truncated=self._markdown_truncated,
            markdown_fetched=self._markdown_fetched,
            user_names=user_names,
        )

    def _markdown_reason(self, page: dict[str, Any]) -> str | None:
        """Return why markdown should be fetched, or None to skip."""
        if self._markdown is None or self._markdown_fetched is None:
            return "first fetch"
        edited_raw = page.get("last_edited_time")
        if edited_raw != self._markdown_edited:
            return "last_edited_time changed"
        edited = parse_notion_date(edited_raw)
        if isinstance(edited, datetime) and edited >= self._markdown_fetched - timedelta(
            seconds=MARKDOWN_EDIT_GRACE_SECONDS
        ):
            return "edited close to last fetch"
        age = (dt_util.utcnow() - self._markdown_fetched).total_seconds()
        if self._markdown.has_expiring_files and age > MARKDOWN_FILE_REFRESH_SECONDS:
            return "file links about to expire"
        return None
