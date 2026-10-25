"""Helpers for Home Assistant tests."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigSubentryData
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.notion_workspace.const import (
    AUTH_TOKEN,
    CONF_API_TOKEN,
    CONF_AUTH_TYPE,
    CONF_COLUMNS,
    CONF_DATA_SOURCE_ID,
    CONF_DATABASE_ID,
    CONF_PAGE_ID,
    CONF_PROPERTIES,
    CONF_PROPERTY_META,
    CONF_SCAN_INTERVAL,
    CONF_SHOW_MARKDOWN,
    CONF_USER_ID,
    CONF_VIEW_ID,
    DOMAIN,
    META_CREATED_BY,
    META_LAST_EDITED_TIME,
    NOTION_API_BASE,
    SUBENTRY_DATABASE,
    SUBENTRY_PAGE,
)
from custom_components.notion_workspace.mapping import detect_todo_mapping, schema_properties
from tests.fixtures import (
    DATA_SOURCE,
    DATA_SOURCE_ID,
    DATABASE_ID,
    MARKDOWN,
    PAGE,
    PAGE_ID,
    USER,
    USER_ID,
    row,
)

ME = {"object": "user", "id": USER_ID, "type": "person", "name": "Testa Testsson", "person": {}}


def database_subentry_data(**overrides: Any) -> dict[str, Any]:
    data = {
        CONF_DATA_SOURCE_ID: DATA_SOURCE_ID,
        CONF_DATABASE_ID: DATABASE_ID,
        CONF_VIEW_ID: None,
        CONF_COLUMNS: ["ppl", "tags"],
        CONF_SCAN_INTERVAL: 60,
        **detect_todo_mapping(schema_properties(DATA_SOURCE)),
    }
    data.update(overrides)
    return data


def page_subentry_data() -> dict[str, Any]:
    return {
        CONF_PAGE_ID: PAGE_ID,
        CONF_SHOW_MARKDOWN: True,
        CONF_PROPERTIES: [META_LAST_EDITED_TIME, META_CREATED_BY],
        CONF_PROPERTY_META: {
            META_LAST_EDITED_TIME: {
                "name": "Last edited",
                "type": META_LAST_EDITED_TIME,
                "kind": "timestamp",
            },
            META_CREATED_BY: {"name": "Created by", "type": META_CREATED_BY, "kind": "text"},
        },
        CONF_SCAN_INTERVAL: 120,
    }


def mock_entry(**kwargs: Any) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Testarbetsyta",
        unique_id=f"user-{USER_ID}",
        data={CONF_AUTH_TYPE: AUTH_TOKEN, CONF_API_TOKEN: "ntn_test", CONF_USER_ID: USER_ID},
        options={},
        subentries_data=[
            ConfigSubentryData(
                data=database_subentry_data(),
                subentry_type=SUBENTRY_DATABASE,
                title="Hemmafix",
                unique_id=DATA_SOURCE_ID,
            ),
            ConfigSubentryData(
                data=page_subentry_data(),
                subentry_type=SUBENTRY_PAGE,
                title="Husets manual",
                unique_id=PAGE_ID,
            ),
        ],
        **kwargs,
    )


def mock_notion(aioclient_mock: AiohttpClientMocker, rows: list[dict[str, Any]] | None = None) -> None:
    aioclient_mock.get(f"{NOTION_API_BASE}/users/me", json=ME)
    aioclient_mock.get(f"{NOTION_API_BASE}/users/{USER_ID}", json=USER)
    aioclient_mock.get(f"{NOTION_API_BASE}/data_sources/{DATA_SOURCE_ID}", json=DATA_SOURCE)
    aioclient_mock.post(
        f"{NOTION_API_BASE}/data_sources/{DATA_SOURCE_ID}/query",
        json={"object": "list", "results": rows if rows is not None else [row()], "has_more": False},
    )
    aioclient_mock.get(f"{NOTION_API_BASE}/pages/{PAGE_ID}/markdown", json=MARKDOWN)
    aioclient_mock.get(f"{NOTION_API_BASE}/pages/{PAGE_ID}", json=PAGE)
