"""Setup, todo two-way sync and sensors."""

from __future__ import annotations

import logging

from homeassistant.components.todo import DOMAIN as TODO_DOMAIN
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.notion_workspace.const import CONF_DEBUG, NOTION_API_BASE
from tests.fixtures import OPT_DONE, OPT_NOT_STARTED, ROW_ID_1, USER_ID, row
from tests.ha.common import mock_entry, mock_notion


async def _setup(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, **kwargs):
    entry = mock_entry(**kwargs)
    entry.add_to_hass(hass)
    await hass.config.async_set_time_zone("Europe/Stockholm")
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_setup_creates_entities(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    mock_notion(aioclient_mock)
    entry = await _setup(hass, aioclient_mock)
    assert entry.state is ConfigEntryState.LOADED

    entity_ids = {e.entity_id for e in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)}
    assert entity_ids == {
        "todo.hemmafix",
        "sensor.hemmafix_table",
        "sensor.husets_manual_content",
        "sensor.husets_manual_last_edited",
        "sensor.husets_manual_created_by",
    }

    todo = hass.states.get("todo.hemmafix")
    assert todo.state == "1"

    table = hass.states.get("sensor.hemmafix_table")
    assert table.state == "1"
    assert table.attributes["columns"] == ["Uppgift", "Ansvarig", "Taggar"]
    assert table.attributes["rows"][0]["cells"] == {"Ansvarig": "Testa Testsson", "Taggar": "El"}
    assert table.attributes["markdown"].startswith("| Uppgift | Ansvarig | Taggar |")

    content = hass.states.get("sensor.husets_manual_content")
    assert content.state == "2026-10-11T09:15:00+00:00"
    assert content.attributes["markdown"] == "# Manual\n\n> 💡 Stäng av huvudkranen först"
    assert content.attributes["title"] == "Husets manual"

    assert hass.states.get("sensor.husets_manual_created_by").state == "Testa Testsson"


async def test_todo_items_and_update(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    mock_notion(aioclient_mock)
    await _setup(hass, aioclient_mock)

    result = await hass.services.async_call(
        TODO_DOMAIN, "get_items", {"entity_id": "todo.hemmafix"}, blocking=True, return_response=True
    )
    items = result["todo.hemmafix"]["items"]
    assert items == [
        {
            "uid": ROW_ID_1,
            "summary": "Byt lampa",
            "status": "needs_action",
            "due": "2026-10-20",
            "description": "**Ansvarig:** Testa Testsson · **Taggar:** El",
        }
    ]

    aioclient_mock.patch(
        f"{NOTION_API_BASE}/pages/{ROW_ID_1}", json=row(status=OPT_DONE, edited="2026-10-11T12:00:00.000Z")
    )
    await hass.services.async_call(
        TODO_DOMAIN,
        "update_item",
        {"entity_id": "todo.hemmafix", "item": ROW_ID_1, "status": "completed"},
        blocking=True,
    )
    patch_calls = [c for c in aioclient_mock.mock_calls if c[0] == "PATCH"]
    assert patch_calls[-1][2] == {"properties": {"st%3A1": {"status": {"id": OPT_DONE}}}}
    assert hass.states.get("todo.hemmafix").state == "0"


async def test_todo_create(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    mock_notion(aioclient_mock)
    await _setup(hass, aioclient_mock)
    created = row("44444444-4444-4444-8444-444444444449", title="Köp glödlampor", due=None)
    aioclient_mock.post(f"{NOTION_API_BASE}/pages", json=created)
    await hass.services.async_call(
        TODO_DOMAIN, "add_item", {"entity_id": "todo.hemmafix", "item": "Köp glödlampor"}, blocking=True
    )
    post = [c for c in aioclient_mock.mock_calls if c[0] == "POST" and str(c[1]).endswith("/pages")][-1]
    assert post[2]["parent"] == {
        "type": "data_source_id",
        "data_source_id": created["parent"]["data_source_id"],
    }
    assert post[2]["properties"]["st%3A1"] == {"status": {"id": OPT_NOT_STARTED}}
    assert post[2]["properties"]["ppl"] == {"people": [{"id": USER_ID}]}
    assert hass.states.get("todo.hemmafix").state == "2"


async def test_todo_has_no_delete(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    mock_notion(aioclient_mock)
    await _setup(hass, aioclient_mock)
    features = hass.states.get("todo.hemmafix").attributes["supported_features"]
    assert not features & 2  # DELETE_TODO_ITEM


async def test_auth_failure_starts_reauth(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.get(f"{NOTION_API_BASE}/users/me", status=401, json={"code": "unauthorized"})
    entry = mock_entry()
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert any(flow["context"]["source"] == "reauth" for flow in hass.config_entries.flow.async_progress())


async def test_debug_mode_sets_log_level(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    logger = logging.getLogger("custom_components.notion_workspace")
    logger.setLevel(logging.NOTSET)
    mock_notion(aioclient_mock)
    entry = await _setup(hass, aioclient_mock)
    assert logger.level == logging.DEBUG

    hass.config_entries.async_update_entry(entry, options={CONF_DEBUG: False})
    await hass.async_block_till_done()
    assert logger.level == logging.NOTSET


async def test_failing_page_does_not_block_database(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_notion(aioclient_mock)
    aioclient_mock.clear_requests()
    from tests.fixtures import DATA_SOURCE, DATA_SOURCE_ID, PAGE_ID
    from tests.ha.common import ME

    aioclient_mock.get(f"{NOTION_API_BASE}/users/me", json=ME)
    aioclient_mock.get(f"{NOTION_API_BASE}/data_sources/{DATA_SOURCE_ID}", json=DATA_SOURCE)
    aioclient_mock.post(
        f"{NOTION_API_BASE}/data_sources/{DATA_SOURCE_ID}/query",
        json={"results": [row()], "has_more": False},
    )
    aioclient_mock.get(f"{NOTION_API_BASE}/users/{USER_ID}", json={"id": USER_ID, "name": "Testa"})
    aioclient_mock.get(f"{NOTION_API_BASE}/pages/{PAGE_ID}", status=404, json={"code": "object_not_found"})
    entry = await _setup(hass, aioclient_mock)
    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get("todo.hemmafix").state == "1"
    assert hass.states.get("sensor.husets_manual_content").state == "unavailable"
