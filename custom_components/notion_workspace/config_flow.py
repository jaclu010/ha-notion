"""Config flow for Notion Workspace: auth, then pick databases and pages in HA."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import probatio as vol
from homeassistant.config_entries import (
    SOURCE_REAUTH,
    SOURCE_RECONFIGURE,
    ConfigEntry,
    ConfigFlowResult,
    ConfigSubentryData,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import (
    NotionAuthError,
    NotionClient,
    NotionError,
    object_icon,
    object_title,
)
from .const import (
    AUTH_OAUTH,
    AUTH_TOKEN,
    CONF_API_TOKEN,
    CONF_ASSIGNEE_PROPERTY,
    CONF_AUTH_TYPE,
    CONF_COLUMNS,
    CONF_COMPLETED_DAYS,
    CONF_DATA_SOURCE_ID,
    CONF_DATABASE_ID,
    CONF_DEBUG,
    CONF_DESCRIPTION_PROPERTY,
    CONF_DONE_PROPERTY,
    CONF_DONE_VALUES,
    CONF_DUE_PROPERTY,
    CONF_OPEN_VALUE,
    CONF_PAGE_ID,
    CONF_PROPERTIES,
    CONF_PROPERTY_META,
    CONF_SCAN_INTERVAL,
    CONF_SHOW_MARKDOWN,
    CONF_TITLE_PROPERTY,
    CONF_TODO_ENABLED,
    CONF_USER_ID,
    CONF_VIEW_ID,
    CONF_WORKSPACE_NAME,
    DEFAULT_COMPLETED_DAYS,
    DEFAULT_DATABASE_SCAN_INTERVAL,
    DEFAULT_DEBUG,
    DEFAULT_PAGE_SCAN_INTERVAL,
    DOMAIN,
    META_CREATED_BY,
    META_CREATED_TIME,
    META_LAST_EDITED_BY,
    META_LAST_EDITED_TIME,
    META_PREFIX,
    MIN_SCAN_INTERVAL,
    NONE_VALUE,
    SUBENTRY_DATABASE,
    SUBENTRY_PAGE,
)
from .mapping import (
    SchemaProperty,
    default_columns,
    default_open_value,
    detect_todo_mapping,
    schema_by_id,
    schema_properties,
    status_options,
)
from .properties import UNSUPPORTED_TYPES, describe_page_properties

_LOGGER = logging.getLogger(__name__)

CONF_DATABASES = "databases"
CONF_PAGES = "pages"
CONF_PAGE = "page"
CONF_SEARCH = "search"
SECTION_TODO = "todo"

MAX_DATA_SOURCES = 300
MAX_PAGES = 100
MAX_VIEWS = 25
OPTION_SEPARATOR = "|"

META_LABELS = {
    "sv": {
        META_CREATED_TIME: "Skapad",
        META_LAST_EDITED_TIME: "Senast ändrad",
        META_CREATED_BY: "Skapad av",
        META_LAST_EDITED_BY: "Ändrad av",
    },
    "en": {
        META_CREATED_TIME: "Created",
        META_LAST_EDITED_TIME: "Last edited",
        META_CREATED_BY: "Created by",
        META_LAST_EDITED_BY: "Last edited by",
    },
}


def _meta_labels(hass: HomeAssistant) -> dict[str, str]:
    return META_LABELS["sv"] if (hass.config.language or "").startswith("sv") else META_LABELS["en"]


def _static_client(hass: HomeAssistant, token: str) -> NotionClient:
    async def getter() -> str:
        return token

    return NotionClient(async_get_clientsession(hass), getter)


def _label(obj: Mapping[str, Any]) -> str:
    title = object_title(obj) or "Untitled"
    icon = object_icon(obj)
    label = f"{icon} {title}" if icon else title
    if (obj.get("parent") or {}).get("type") in ("data_source_id", "database_id"):
        label = f"↳ {label}"
    return label


def _is_row(page: Mapping[str, Any]) -> bool:
    return (page.get("parent") or {}).get("type") in ("data_source_id", "database_id")


def _select(options: list[SelectOptionDict], *, multiple: bool = False) -> SelectSelector:
    return SelectSelector(
        SelectSelectorConfig(options=options, multiple=multiple, mode=SelectSelectorMode.DROPDOWN)
    )


def _seconds(minimum: int, maximum: int, step: int) -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(
            min=minimum, max=maximum, step=step, unit_of_measurement="s", mode=NumberSelectorMode.BOX
        )
    )


def _opt(value: Any) -> Any:
    return None if value in (None, "", NONE_VALUE) else value


def _encode(prop_id: str, option_id: str) -> str:
    return f"{prop_id}{OPTION_SEPARATOR}{option_id}"


def _decode(value: str) -> tuple[str, str]:
    prop_id, _, option_id = value.rpartition(OPTION_SEPARATOR)
    return prop_id, option_id


# Database settings (shared by the main flow and the database subentry flow)


@dataclass(slots=True)
class DatabaseContext:
    """Everything needed to render the database settings form."""

    data_source: dict[str, Any]
    props: list[SchemaProperty]
    views: list[tuple[str, str]]

    @property
    def title(self) -> str:
        """Data source title."""
        return object_title(self.data_source) or "Notion"


async def load_database_context(client: NotionClient, data_source_id: str) -> DatabaseContext:
    """Fetch schema and views of a data source."""
    data_source = await client.retrieve_data_source(data_source_id)
    views: list[tuple[str, str]] = []
    database_id = (data_source.get("parent") or {}).get("database_id")
    if database_id:
        try:
            for view_id in (await client.list_view_ids(database_id=database_id))[:MAX_VIEWS]:
                view = await client.retrieve_view(view_id)
                views.append((view_id, view.get("name") or view_id))
        except NotionAuthError:
            raise
        except NotionError as err:
            _LOGGER.debug("Views for %s not available: %s", data_source_id, err)
    context = DatabaseContext(data_source, schema_properties(data_source), views)
    _LOGGER.debug("Database %s: %s properties, %s views", context.title, len(context.props), len(views))
    return context


def database_defaults(context: DatabaseContext, existing: Mapping[str, Any] | None) -> dict[str, Any]:
    """Defaults for the form: existing settings, else auto-detected ones."""
    if existing:
        return {CONF_NAME: existing.get(CONF_NAME, context.title), **existing}
    detected = detect_todo_mapping(context.props)
    _LOGGER.debug("Auto-detected mapping for %s: %s", context.title, detected)
    return {
        CONF_NAME: context.title,
        CONF_VIEW_ID: None,
        CONF_COLUMNS: default_columns(context.props, detected),
        CONF_SCAN_INTERVAL: DEFAULT_DATABASE_SCAN_INTERVAL,
        **detected,
    }


def database_schema(context: DatabaseContext, defaults: Mapping[str, Any]) -> vol.Schema:
    """Settings form for one database."""
    props = context.props

    def prop_options(types: tuple[str, ...]) -> list[SelectOptionDict]:
        return [SelectOptionDict(value=NONE_VALUE, label="—")] + [
            SelectOptionDict(value=p.id, label=p.name) for p in props if p.type in types
        ]

    status_choices = [
        SelectOptionDict(value=_encode(p.id, o["id"]), label=f"{p.name}: {o.get('name', '')}")
        for p in props
        if p.type == "status"
        for o in status_options(p)
    ]
    done_prop = defaults.get(CONF_DONE_PROPERTY)
    todo: dict[Any, Any] = {
        vol.Required(CONF_TODO_ENABLED, default=bool(defaults.get(CONF_TODO_ENABLED))): BooleanSelector(),
        vol.Optional(CONF_DONE_PROPERTY, default=done_prop or NONE_VALUE): _select(
            prop_options(("status", "checkbox"))
        ),
    }
    if status_choices:
        todo[
            vol.Optional(
                CONF_DONE_VALUES,
                default=[
                    _encode(done_prop, oid) for oid in defaults.get(CONF_DONE_VALUES) or [] if done_prop
                ],
            )
        ] = _select(status_choices, multiple=True)
        open_value = defaults.get(CONF_OPEN_VALUE)
        todo[
            vol.Optional(
                CONF_OPEN_VALUE,
                default=_encode(done_prop, open_value) if done_prop and open_value else NONE_VALUE,
            )
        ] = _select([SelectOptionDict(value=NONE_VALUE, label="—"), *status_choices])
    todo.update(
        {
            vol.Optional(CONF_DUE_PROPERTY, default=defaults.get(CONF_DUE_PROPERTY) or NONE_VALUE): _select(
                prop_options(("date",))
            ),
            vol.Optional(
                CONF_DESCRIPTION_PROPERTY, default=defaults.get(CONF_DESCRIPTION_PROPERTY) or NONE_VALUE
            ): _select(prop_options(("rich_text",))),
            vol.Optional(
                CONF_ASSIGNEE_PROPERTY, default=defaults.get(CONF_ASSIGNEE_PROPERTY) or NONE_VALUE
            ): _select(prop_options(("people",))),
            vol.Required(
                CONF_COMPLETED_DAYS, default=defaults.get(CONF_COMPLETED_DAYS, DEFAULT_COMPLETED_DAYS)
            ): NumberSelector(NumberSelectorConfig(min=0, max=365, step=1, mode=NumberSelectorMode.BOX)),
        }
    )
    column_choices = [
        SelectOptionDict(value=p.id, label=f"{p.name} ({p.type})")
        for p in props
        if p.type not in UNSUPPORTED_TYPES and p.type != "title"
    ]
    view_choices = [SelectOptionDict(value=NONE_VALUE, label="—")] + [
        SelectOptionDict(value=view_id, label=name) for view_id, name in context.views
    ]
    known_columns = {c["value"] for c in column_choices}
    return vol.Schema(
        {
            vol.Required(CONF_NAME, default=defaults.get(CONF_NAME, context.title)): TextSelector(),
            vol.Optional(CONF_VIEW_ID, default=defaults.get(CONF_VIEW_ID) or NONE_VALUE): _select(
                view_choices
            ),
            vol.Optional(
                CONF_COLUMNS, default=[c for c in defaults.get(CONF_COLUMNS) or [] if c in known_columns]
            ): _select(column_choices, multiple=True),
            vol.Required(SECTION_TODO): section(vol.Schema(todo), {"collapsed": False}),
            vol.Required(
                CONF_SCAN_INTERVAL, default=defaults.get(CONF_SCAN_INTERVAL, DEFAULT_DATABASE_SCAN_INTERVAL)
            ): _seconds(MIN_SCAN_INTERVAL, 3600, 5),
        }
    )


def database_data(
    context: DatabaseContext, user_input: Mapping[str, Any]
) -> tuple[str, dict[str, Any], dict[str, str]]:
    """Turn form input into (title, subentry data, errors)."""
    todo = dict(user_input.get(SECTION_TODO) or {})
    by_id = schema_by_id(context.props)
    enabled = bool(todo.get(CONF_TODO_ENABLED))
    done_prop_id = _opt(todo.get(CONF_DONE_PROPERTY))
    done_prop = by_id.get(done_prop_id or "")
    done_values = [oid for pid, oid in map(_decode, todo.get(CONF_DONE_VALUES) or []) if pid == done_prop_id]
    open_value = None
    if _opt(todo.get(CONF_OPEN_VALUE)):
        pid, oid = _decode(todo[CONF_OPEN_VALUE])
        open_value = oid if pid == done_prop_id else None

    errors: dict[str, str] = {}
    if enabled:
        if done_prop is None:
            errors["base"] = "done_property_required"
        elif done_prop.type == "status":
            if not done_values:
                errors["base"] = "done_values_required"
            open_value = open_value or default_open_value(done_prop)
    if done_prop is None or done_prop.type != "status":
        done_values, open_value = [], None

    title_prop = next((p for p in context.props if p.type == "title"), None)
    data = {
        CONF_DATA_SOURCE_ID: context.data_source["id"],
        CONF_DATABASE_ID: (context.data_source.get("parent") or {}).get("database_id"),
        CONF_VIEW_ID: _opt(user_input.get(CONF_VIEW_ID)),
        CONF_COLUMNS: list(user_input.get(CONF_COLUMNS) or []),
        CONF_TODO_ENABLED: enabled,
        CONF_TITLE_PROPERTY: title_prop.id if title_prop else "title",
        CONF_DONE_PROPERTY: done_prop_id,
        CONF_DONE_VALUES: done_values,
        CONF_OPEN_VALUE: open_value,
        CONF_DUE_PROPERTY: _opt(todo.get(CONF_DUE_PROPERTY)),
        CONF_DESCRIPTION_PROPERTY: _opt(todo.get(CONF_DESCRIPTION_PROPERTY)),
        CONF_ASSIGNEE_PROPERTY: _opt(todo.get(CONF_ASSIGNEE_PROPERTY)),
        CONF_COMPLETED_DAYS: int(todo.get(CONF_COMPLETED_DAYS, DEFAULT_COMPLETED_DAYS)),
        CONF_SCAN_INTERVAL: int(user_input.get(CONF_SCAN_INTERVAL, DEFAULT_DATABASE_SCAN_INTERVAL)),
    }
    title = str(user_input.get(CONF_NAME) or "").strip() or context.title
    if not errors:
        _LOGGER.debug("Database settings for %s: %s", title, data)
    return title, data, errors


# Page settings


@dataclass(slots=True)
class PageContext:
    """Everything needed to render the page settings form."""

    page: dict[str, Any]
    described: dict[str, dict[str, Any]]

    @property
    def title(self) -> str:
        """Page title."""
        return object_title(self.page) or "Notion"


async def load_page_context(client: NotionClient, page_id: str, labels: Mapping[str, str]) -> PageContext:
    """Fetch a page and describe its properties."""
    page = await client.retrieve_page(page_id)
    schema = None
    parent = page.get("parent") or {}
    if parent.get("type") == "data_source_id":
        try:
            data_source = await client.retrieve_data_source(parent["data_source_id"])
        except NotionAuthError:
            raise
        except NotionError as err:
            _LOGGER.debug("Parent schema of %s not readable: %s", page_id, err)
        else:
            schema = {p.id: {p.type: p.config} for p in schema_properties(data_source)}
    described = describe_page_properties(page, schema, labels)
    _LOGGER.debug("Page %s: properties %s", object_title(page), {k: v["kind"] for k, v in described.items()})
    return PageContext(page, described)


def page_defaults(context: PageContext, existing: Mapping[str, Any] | None) -> dict[str, Any]:
    """Defaults: metadata, plus all properties of database rows (except the title)."""
    if existing:
        return {CONF_NAME: existing.get(CONF_NAME, context.title), **existing}
    keys = [k for k in context.described if k.startswith(META_PREFIX)]
    if _is_row(context.page):
        keys += [
            k for k, d in context.described.items() if not k.startswith(META_PREFIX) and d["type"] != "title"
        ]
    return {
        CONF_NAME: context.title,
        CONF_SHOW_MARKDOWN: True,
        CONF_PROPERTIES: keys,
        CONF_SCAN_INTERVAL: DEFAULT_PAGE_SCAN_INTERVAL,
    }


def page_schema(context: PageContext, defaults: Mapping[str, Any]) -> vol.Schema:
    """Settings form for one page."""
    choices = [
        SelectOptionDict(
            value=key,
            label=d["name"] if key.startswith(META_PREFIX) else f"{d['name']} ({d['type']})",
        )
        for key, d in context.described.items()
    ]
    return vol.Schema(
        {
            vol.Required(CONF_NAME, default=defaults.get(CONF_NAME, context.title)): TextSelector(),
            vol.Required(
                CONF_SHOW_MARKDOWN, default=defaults.get(CONF_SHOW_MARKDOWN, True)
            ): BooleanSelector(),
            vol.Optional(
                CONF_PROPERTIES,
                default=[k for k in defaults.get(CONF_PROPERTIES) or [] if k in context.described],
            ): _select(choices, multiple=True),
            vol.Required(
                CONF_SCAN_INTERVAL, default=defaults.get(CONF_SCAN_INTERVAL, DEFAULT_PAGE_SCAN_INTERVAL)
            ): _seconds(30, 86400, 30),
        }
    )


def page_data(context: PageContext, user_input: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    """Turn form input into (title, subentry data)."""
    selected = [k for k in user_input.get(CONF_PROPERTIES) or [] if k in context.described]
    data = {
        CONF_PAGE_ID: context.page["id"],
        CONF_SHOW_MARKDOWN: bool(user_input.get(CONF_SHOW_MARKDOWN, True)),
        CONF_PROPERTIES: selected,
        CONF_PROPERTY_META: {k: context.described[k] for k in selected},
        CONF_SCAN_INTERVAL: int(user_input.get(CONF_SCAN_INTERVAL, DEFAULT_PAGE_SCAN_INTERVAL)),
    }
    title = str(user_input.get(CONF_NAME) or "").strip() or context.title
    _LOGGER.debug("Page settings for %s: %s", title, data)
    return title, data


# Pickers


async def search_data_sources(client: NotionClient) -> list[dict[str, Any]]:
    """All data sources the token can see."""
    sources = await client.search(object_type="data_source", max_results=MAX_DATA_SOURCES)
    _LOGGER.debug("Search found %s data sources", len(sources))
    return sources


async def search_pages(client: NotionClient, query: str | None) -> list[dict[str, Any]]:
    """Pages for the picker: recent standalone pages, or search hits incl. rows."""
    pages = await client.search(query or None, object_type="page", max_results=MAX_PAGES * 3)
    if not query:
        pages = [p for p in pages if not _is_row(p)]
    pages = pages[:MAX_PAGES]
    _LOGGER.debug("Page search %r: %s results", query, len(pages))
    return pages


class NotionConfigFlow(config_entry_oauth2_flow.AbstractOAuth2FlowHandler, domain=DOMAIN):
    """Set up a Notion workspace."""

    DOMAIN = DOMAIN
    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        super().__init__()
        self._client: NotionClient | None = None
        self._entry_data: dict[str, Any] = {}
        self._title = "Notion"
        self._sources: dict[str, dict[str, Any]] = {}
        self._pages: dict[str, dict[str, Any]] = {}
        self._pending_sources: list[str] = []
        self._pending_pages: list[str] = []
        self._selected_pages: list[str] = []
        self._last_query = ""
        self._subentries: list[ConfigSubentryData] = []
        self._database_context: DatabaseContext | None = None
        self._page_context: PageContext | None = None

    @property
    def logger(self) -> logging.Logger:
        """Return the logger used by the OAuth helper."""
        return _LOGGER

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Databases and pages are subentries."""
        return {SUBENTRY_DATABASE: DatabaseSubentryFlow, SUBENTRY_PAGE: PageSubentryFlow}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return NotionOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Choose how to sign in."""
        return self.async_show_menu(step_id="user", menu_options=["token", "pick_implementation"])

    async def async_step_token(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Sign in with a personal access token or internal integration secret."""
        errors: dict[str, str] = {}
        if user_input is not None:
            token = user_input[CONF_API_TOKEN].strip()
            client = _static_client(self.hass, token)
            try:
                me = await client.users_me()
            except NotionAuthError:
                errors["base"] = "invalid_auth"
            except NotionError:
                errors["base"] = "cannot_connect"
            else:
                bot = me.get("bot") or {}
                user_id = (
                    me["id"]
                    if me.get("type") == "person"
                    else ((bot.get("owner") or {}).get("user") or {}).get("id")
                )
                return await self._async_authenticated(
                    client,
                    me,
                    {CONF_AUTH_TYPE: AUTH_TOKEN, CONF_API_TOKEN: token},
                    workspace_id=bot.get("workspace_id") or me.get("workspace_id"),
                    workspace_name=bot.get("workspace_name") or me.get("workspace_name"),
                    user_id=user_id,
                )
        step_id = "reauth_confirm" if self.source == SOURCE_REAUTH else "token"
        return self.async_show_form(
            step_id=step_id,
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_API_TOKEN): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    )
                }
            ),
            errors=errors,
        )

    async def async_oauth_create_entry(self, data: dict[str, Any]) -> ConfigFlowResult:
        """OAuth finished: continue with picking databases and pages."""
        token = data["token"]
        client = _static_client(self.hass, token["access_token"])
        try:
            me = await client.users_me()
        except NotionError:
            return self.async_abort(reason="cannot_connect")
        owner = (token.get("owner") or {}).get("user") or {}
        return await self._async_authenticated(
            client,
            me,
            {CONF_AUTH_TYPE: AUTH_OAUTH, **data},
            workspace_id=token.get("workspace_id"),
            workspace_name=token.get("workspace_name"),
            user_id=owner.get("id"),
        )

    async def _async_authenticated(
        self,
        client: NotionClient,
        me: dict[str, Any],
        auth_data: dict[str, Any],
        *,
        workspace_id: str | None,
        workspace_name: str | None,
        user_id: str | None,
    ) -> ConfigFlowResult:
        unique_id = workspace_id or f"user-{me['id']}"
        _LOGGER.debug(
            "Authenticated as %s (%s), workspace=%s, unique_id=%s",
            me.get("name"),
            me.get("type"),
            workspace_name,
            unique_id,
        )
        await self.async_set_unique_id(unique_id)
        if self.source == SOURCE_REAUTH:
            self._abort_if_unique_id_mismatch(reason="wrong_account")
            entry = self._get_reauth_entry()
            if entry.update_listeners:
                # Loaded entry: the update listener reloads it.
                return self.async_update_and_abort(entry, data_updates=auth_data)
            return self.async_update_reload_and_abort(entry, data_updates=auth_data)
        self._abort_if_unique_id_configured()
        self._client = client
        self._title = workspace_name or me.get("name") or "Notion"
        self._entry_data = {**auth_data, CONF_USER_ID: user_id, CONF_WORKSPACE_NAME: workspace_name}
        return await self.async_step_databases()

    async def async_step_databases(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Pick the databases (data sources) to add."""
        assert self._client is not None
        if user_input is not None:
            self._pending_sources = list(user_input.get(CONF_DATABASES) or [])
            return await self.async_step_pages()
        try:
            sources = await search_data_sources(self._client)
        except NotionError:
            return self.async_abort(reason="cannot_connect")
        self._sources = {s["id"]: s for s in sources}
        options = [SelectOptionDict(value=s["id"], label=_label(s)) for s in sources]
        return self.async_show_form(
            step_id="databases",
            data_schema=vol.Schema(
                {vol.Optional(CONF_DATABASES, default=[]): _select(options, multiple=True)} if options else {}
            ),
            description_placeholders={"count": str(len(options))},
        )

    async def async_step_pages(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Pick pages; a search term re-shows the list with matches."""
        assert self._client is not None
        query = ""
        if user_input is not None:
            self._selected_pages = list(user_input.get(CONF_PAGES) or [])
            query = str(user_input.get(CONF_SEARCH) or "").strip()
            if not query or query == self._last_query:
                self._pending_pages = list(self._selected_pages)
                return await self._async_next_settings_step()
            self._last_query = query
        try:
            results = await search_pages(self._client, query)
        except NotionError:
            return self.async_abort(reason="cannot_connect")
        self._pages.update({p["id"]: p for p in results})
        shown = [p["id"] for p in results]
        shown += [pid for pid in self._selected_pages if pid not in shown and pid in self._pages]
        options = [SelectOptionDict(value=pid, label=_label(self._pages[pid])) for pid in shown]
        return self.async_show_form(
            step_id="pages",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_PAGES, default=self._selected_pages): _select(options, multiple=True),
                    vol.Optional(CONF_SEARCH, default=""): TextSelector(),
                }
            ),
            description_placeholders={"count": str(len(results)), "query": query or "–"},
        )

    async def _async_next_settings_step(self) -> ConfigFlowResult:
        if self._pending_sources:
            return await self.async_step_database_settings()
        if self._pending_pages:
            return await self.async_step_page_settings()
        return self.async_create_entry(
            title=self._title,
            data=self._entry_data,
            options={CONF_DEBUG: DEFAULT_DEBUG},
            subentries=self._subentries,
        )

    async def async_step_database_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Settings for the next selected database."""
        assert self._client is not None
        errors: dict[str, str] = {}
        if self._database_context is None:
            try:
                self._database_context = await load_database_context(self._client, self._pending_sources[0])
            except NotionError:
                return self.async_abort(reason="cannot_connect")
        context = self._database_context
        defaults = database_defaults(context, None)
        if user_input is not None:
            title, data, errors = database_data(context, user_input)
            if not errors:
                self._subentries.append(
                    ConfigSubentryData(
                        data=data,
                        subentry_type=SUBENTRY_DATABASE,
                        title=title,
                        unique_id=data[CONF_DATA_SOURCE_ID],
                    )
                )
                self._pending_sources.pop(0)
                self._database_context = None
                return await self._async_next_settings_step()
            defaults = {**defaults, **data, CONF_NAME: title}
        return self.async_show_form(
            step_id="database_settings",
            data_schema=database_schema(context, defaults),
            errors=errors,
            description_placeholders={"database": context.title},
        )

    async def async_step_page_settings(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Settings for the next selected page."""
        assert self._client is not None
        if self._page_context is None:
            try:
                self._page_context = await load_page_context(
                    self._client, self._pending_pages[0], _meta_labels(self.hass)
                )
            except NotionError:
                return self.async_abort(reason="cannot_connect")
        context = self._page_context
        if user_input is not None:
            title, data = page_data(context, user_input)
            self._subentries.append(
                ConfigSubentryData(
                    data=data, subentry_type=SUBENTRY_PAGE, title=title, unique_id=data[CONF_PAGE_ID]
                )
            )
            self._pending_pages.pop(0)
            self._page_context = None
            return await self._async_next_settings_step()
        return self.async_show_form(
            step_id="page_settings",
            data_schema=page_schema(context, page_defaults(context, None)),
            description_placeholders={"page": context.title},
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Token expired or revoked."""
        if entry_data.get(CONF_AUTH_TYPE) == AUTH_OAUTH:
            return await self.async_step_reauth_oauth()
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_oauth(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Confirm signing in with Notion again."""
        if user_input is None:
            return self.async_show_form(step_id="reauth_oauth")
        return await self.async_step_pick_implementation()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for a new token."""
        return await self.async_step_token(user_input)


class NotionOptionsFlow(OptionsFlow):
    """Integration-wide options."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Debug mode."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_DEBUG, default=self.config_entry.options.get(CONF_DEBUG, DEFAULT_DEBUG)
                    ): BooleanSelector()
                }
            ),
        )


class _NotionSubentryFlow(ConfigSubentryFlow):
    """Common helpers for subentry flows."""

    def _client(self) -> NotionClient | None:
        entry = self._get_entry()
        runtime = getattr(entry, "runtime_data", None)
        return runtime.client if runtime is not None else None

    def _configured(self, key: str) -> set[str]:
        return {s.data.get(key) for s in self._get_entry().subentries.values()}


class DatabaseSubentryFlow(_NotionSubentryFlow):
    """Add or reconfigure a database."""

    def __init__(self) -> None:
        """Initialize the flow."""
        self._context: DatabaseContext | None = None
        self._data_source_id: str | None = None
        self._existing: dict[str, Any] | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Pick a database."""
        client = self._client()
        if client is None:
            return self.async_abort(reason="entry_not_loaded")
        if user_input is not None:
            self._data_source_id = user_input[CONF_DATA_SOURCE_ID]
            return await self.async_step_settings()
        try:
            sources = await search_data_sources(client)
        except NotionError:
            return self.async_abort(reason="cannot_connect")
        configured = self._configured(CONF_DATA_SOURCE_ID)
        options = [
            SelectOptionDict(value=s["id"], label=_label(s)) for s in sources if s["id"] not in configured
        ]
        if not options:
            return self.async_abort(reason="no_databases")
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_DATA_SOURCE_ID): _select(options)}),
        )

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Change the settings of a database."""
        subentry = self._get_reconfigure_subentry()
        self._data_source_id = subentry.data[CONF_DATA_SOURCE_ID]
        self._existing = {**subentry.data, CONF_NAME: subentry.title}
        return await self.async_step_settings()

    async def async_step_settings(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Database settings."""
        client = self._client()
        if client is None:
            return self.async_abort(reason="entry_not_loaded")
        assert self._data_source_id is not None
        if self._context is None:
            try:
                self._context = await load_database_context(client, self._data_source_id)
            except NotionError:
                return self.async_abort(reason="cannot_connect")
        defaults = database_defaults(self._context, self._existing)
        errors: dict[str, str] = {}
        if user_input is not None:
            title, data, errors = database_data(self._context, user_input)
            if not errors:
                if self.source == SOURCE_RECONFIGURE:
                    return self.async_update_and_abort(
                        self._get_entry(), self._get_reconfigure_subentry(), title=title, data=data
                    )
                return self.async_create_entry(title=title, data=data, unique_id=data[CONF_DATA_SOURCE_ID])
            defaults = {**defaults, **data, CONF_NAME: title}
        return self.async_show_form(
            step_id="settings",
            data_schema=database_schema(self._context, defaults),
            errors=errors,
            description_placeholders={"database": self._context.title},
        )


class PageSubentryFlow(_NotionSubentryFlow):
    """Add or reconfigure a page."""

    def __init__(self) -> None:
        """Initialize the flow."""
        self._context: PageContext | None = None
        self._page_id: str | None = None
        self._existing: dict[str, Any] | None = None
        self._pages: dict[str, dict[str, Any]] = {}
        self._last_query = ""

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Search and pick a page."""
        client = self._client()
        if client is None:
            return self.async_abort(reason="entry_not_loaded")
        query = ""
        if user_input is not None:
            query = str(user_input.get(CONF_SEARCH) or "").strip()
            selected = user_input.get(CONF_PAGE)
            if selected and selected != NONE_VALUE and (not query or query == self._last_query):
                self._page_id = selected
                return await self.async_step_settings()
            self._last_query = query
        try:
            results = await search_pages(client, query)
        except NotionError:
            return self.async_abort(reason="cannot_connect")
        configured = self._configured(CONF_PAGE_ID)
        self._pages = {p["id"]: p for p in results if p["id"] not in configured}
        options = [SelectOptionDict(value=NONE_VALUE, label="—")] + [
            SelectOptionDict(value=pid, label=_label(page)) for pid, page in self._pages.items()
        ]
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_PAGE, default=NONE_VALUE): _select(options),
                    vol.Optional(CONF_SEARCH, default=""): TextSelector(),
                }
            ),
            description_placeholders={"count": str(len(self._pages)), "query": query or "–"},
        )

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Change the settings of a page."""
        subentry = self._get_reconfigure_subentry()
        self._page_id = subentry.data[CONF_PAGE_ID]
        self._existing = {**subentry.data, CONF_NAME: subentry.title}
        return await self.async_step_settings()

    async def async_step_settings(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Page settings."""
        client = self._client()
        if client is None:
            return self.async_abort(reason="entry_not_loaded")
        assert self._page_id is not None
        if self._context is None:
            try:
                self._context = await load_page_context(client, self._page_id, _meta_labels(self.hass))
            except NotionError:
                return self.async_abort(reason="cannot_connect")
        if user_input is not None:
            title, data = page_data(self._context, user_input)
            if self.source == SOURCE_RECONFIGURE:
                return self.async_update_and_abort(
                    self._get_entry(), self._get_reconfigure_subentry(), title=title, data=data
                )
            return self.async_create_entry(title=title, data=data, unique_id=data[CONF_PAGE_ID])
        return self.async_show_form(
            step_id="settings",
            data_schema=page_schema(self._context, page_defaults(self._context, self._existing)),
            description_placeholders={"page": self._context.title},
        )
