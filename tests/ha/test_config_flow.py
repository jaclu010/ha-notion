"""Config flow, subentry flows, options and reauth."""

from __future__ import annotations

from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.notion_workspace.const import (
    AUTH_TOKEN,
    CONF_API_TOKEN,
    CONF_AUTH_TYPE,
    CONF_COLUMNS,
    CONF_DEBUG,
    CONF_DONE_PROPERTY,
    CONF_DONE_VALUES,
    CONF_OPEN_VALUE,
    CONF_PROPERTIES,
    CONF_TODO_ENABLED,
    CONF_USER_ID,
    CONF_VIEW_ID,
    DOMAIN,
    META_CREATED_TIME,
    NONE_VALUE,
    NOTION_API_BASE,
    SUBENTRY_DATABASE,
    SUBENTRY_PAGE,
)
from tests.fixtures import (
    DATA_SOURCE,
    DATA_SOURCE_ID,
    DATABASE_ID,
    OPT_ARCHIVED,
    OPT_DONE,
    OPT_NOT_STARTED,
    PAGE,
    PAGE_ID,
    ROW_ID_1,
    USER_ID,
    VIEW,
    VIEW_ID,
    clone,
    row,
)
from tests.ha.common import ME, mock_entry, mock_notion

SECOND_DS = "77777777-7777-4777-8777-777777777777"


def mock_search(aioclient_mock: AiohttpClientMocker) -> None:
    other = clone(DATA_SOURCE)
    other["id"] = SECOND_DS
    other["title"] = [{"plain_text": "Läslista"}]

    async def search(method, url, data):
        from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMockResponse

        if data["filter"]["value"] == "data_source":
            results = [DATA_SOURCE, other]
        elif data.get("query"):
            results = [row()]  # search hits include database rows
        else:
            results = [PAGE, row()]
        return AiohttpClientMockResponse(method, url, json={"results": results, "has_more": False})

    aioclient_mock.post(f"{NOTION_API_BASE}/search", side_effect=search)
    aioclient_mock.get(
        f"{NOTION_API_BASE}/views?database_id={DATABASE_ID}&page_size=100",
        json={"results": [{"object": "view", "id": VIEW_ID}], "has_more": False},
    )
    aioclient_mock.get(f"{NOTION_API_BASE}/views/{VIEW_ID}", json=VIEW)
    aioclient_mock.get(f"{NOTION_API_BASE}/pages/{ROW_ID_1}", json=row())


async def test_token_flow_creates_entry_with_subentries(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_notion(aioclient_mock)
    mock_search(aioclient_mock)

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.MENU
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "token"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_TOKEN: " ntn_abc "})
    assert result["step_id"] == "databases"
    assert result["description_placeholders"]["count"] == "2"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"databases": [DATA_SOURCE_ID]}
    )
    assert result["step_id"] == "pages"
    # Without search only standalone pages are listed (no database rows).
    options = result["data_schema"].schema["pages"].config["options"]
    assert [o["value"] for o in options] == [PAGE_ID]

    # Searching keeps the selection and lists database rows too.
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"pages": [PAGE_ID], "search": "lampa"}
    )
    assert result["step_id"] == "pages"
    options = result["data_schema"].schema["pages"].config["options"]
    assert {o["value"] for o in options} == {ROW_ID_1, PAGE_ID}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"pages": [PAGE_ID, ROW_ID_1], "search": "lampa"}
    )
    assert result["step_id"] == "database_settings"
    schema = result["data_schema"].schema
    assert [o["label"] for o in schema[CONF_VIEW_ID].config["options"]] == ["—", "Mina uppgifter"]

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "name": "Hemma",
            CONF_VIEW_ID: VIEW_ID,
            CONF_COLUMNS: ["tags"],
            "todo": {
                CONF_TODO_ENABLED: True,
                CONF_DONE_PROPERTY: "st%3A1",
                CONF_DONE_VALUES: [f"st%3A1|{OPT_DONE}"],
                CONF_OPEN_VALUE: NONE_VALUE,
                "due_property": "due%3F",
                "description_property": NONE_VALUE,
                "assignee_property": "ppl",
                "completed_days": 2,
            },
            "scan_interval": 90,
        },
    )
    assert result["step_id"] == "page_settings"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"name": "Manual", "show_markdown": True, CONF_PROPERTIES: [META_CREATED_TIME], "scan_interval": 300},
    )
    assert result["step_id"] == "page_settings"
    defaults = {
        str(k): k.default()
        for k in result["data_schema"].schema
        if hasattr(k, "default") and callable(k.default)
    }
    # Database rows get their properties preselected (not the title).
    assert "st%3A1" in defaults[CONF_PROPERTIES]
    assert "title" not in defaults[CONF_PROPERTIES]
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "name": "Byt lampa",
            "show_markdown": False,
            CONF_PROPERTIES: ["st%3A1", "cost"],
            "scan_interval": 120,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    entry = result["result"]
    assert entry.data == {
        CONF_AUTH_TYPE: AUTH_TOKEN,
        CONF_API_TOKEN: "ntn_abc",
        CONF_USER_ID: USER_ID,
        "workspace_name": None,
    }
    assert entry.options == {CONF_DEBUG: True}
    subentries = sorted(entry.subentries.values(), key=lambda s: s.title)
    assert [(s.subentry_type, s.title) for s in subentries] == [
        (SUBENTRY_PAGE, "Byt lampa"),
        (SUBENTRY_DATABASE, "Hemma"),
        (SUBENTRY_PAGE, "Manual"),
    ]
    database = subentries[1].data
    assert database[CONF_VIEW_ID] == VIEW_ID
    assert database[CONF_DONE_VALUES] == [OPT_DONE]
    assert database[CONF_OPEN_VALUE] == OPT_NOT_STARTED  # filled from the To-do group
    assert database["description_property"] is None
    row_page = subentries[0].data
    assert row_page["property_meta"]["st%3A1"]["kind"] == "enum"
    assert row_page["property_meta"]["cost"]["kind"] == "number"


async def test_token_errors(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.get(f"{NOTION_API_BASE}/users/me", status=401, json={"code": "unauthorized"})
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "token"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_TOKEN: "bad"})
    assert result["errors"] == {"base": "invalid_auth"}


async def test_status_without_done_values_is_rejected(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_notion(aioclient_mock)
    mock_search(aioclient_mock)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "token"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_TOKEN: "ntn"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"databases": [DATA_SOURCE_ID]}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"pages": []})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "name": "X",
            CONF_COLUMNS: [],
            "todo": {
                CONF_TODO_ENABLED: True,
                CONF_DONE_PROPERTY: "st%3A1",
                CONF_DONE_VALUES: [],
                "completed_days": 1,
            },
            "scan_interval": 60,
        },
    )
    assert result["errors"] == {"base": "done_values_required"}


async def test_duplicate_workspace_aborts(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    mock_notion(aioclient_mock)
    mock_entry().add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "token"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_TOKEN: "ntn"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def _loaded_entry(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker):
    mock_notion(aioclient_mock)
    mock_search(aioclient_mock)
    entry = mock_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_add_database_subentry(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    entry = await _loaded_entry(hass, aioclient_mock)
    second = clone(DATA_SOURCE)
    second["id"] = SECOND_DS
    aioclient_mock.get(f"{NOTION_API_BASE}/data_sources/{SECOND_DS}", json=second)

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_DATABASE), context={"source": SOURCE_USER}
    )
    assert result["step_id"] == "user"
    # Already added databases are not offered again.
    options = result["data_schema"].schema["data_source_id"].config["options"]
    assert [o["value"] for o in options] == [SECOND_DS]
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"data_source_id": SECOND_DS}
    )
    assert result["step_id"] == "settings"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            "name": "Läslista",
            CONF_COLUMNS: [],
            "todo": {CONF_TODO_ENABLED: False, "completed_days": 1},
            "scan_interval": 60,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert any(s.title == "Läslista" for s in entry.subentries.values())
    assert entry.state is ConfigEntryState.LOADED


async def test_reconfigure_database_subentry(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    entry = await _loaded_entry(hass, aioclient_mock)
    subentry = next(s for s in entry.subentries.values() if s.subentry_type == SUBENTRY_DATABASE)
    result = await entry.start_subentry_reconfigure_flow(hass, subentry.subentry_id)
    assert result["step_id"] == "settings"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            "name": "Hemmafix",
            CONF_COLUMNS: ["cost"],
            "todo": {
                CONF_TODO_ENABLED: True,
                CONF_DONE_PROPERTY: "st%3A1",
                CONF_DONE_VALUES: [f"st%3A1|{OPT_DONE}", f"st%3A1|{OPT_ARCHIVED}"],
                CONF_OPEN_VALUE: f"st%3A1|{OPT_NOT_STARTED}",
                "completed_days": 0,
            },
            "scan_interval": 30,
        },
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    assert entry.subentries[subentry.subentry_id].data[CONF_COLUMNS] == ["cost"]
    assert entry.state is ConfigEntryState.LOADED


async def test_add_page_subentry_with_search(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    entry = await _loaded_entry(hass, aioclient_mock)
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_PAGE), context={"source": SOURCE_USER}
    )
    # The configured page is hidden; search to find the database row.
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"page": NONE_VALUE, "search": "lampa"}
    )
    assert result["step_id"] == "user"
    options = result["data_schema"].schema["page"].config["options"]
    assert [o["value"] for o in options] == [NONE_VALUE, ROW_ID_1]
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"page": ROW_ID_1, "search": "lampa"}
    )
    assert result["step_id"] == "settings"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {"name": "Lampan", "show_markdown": False, CONF_PROPERTIES: ["cost"], "scan_interval": 120},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert hass.states.get("sensor.lampan_kostnad").state == "129.5"


async def test_options_debug(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    entry = await _loaded_entry(hass, aioclient_mock)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {CONF_DEBUG: False})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {CONF_DEBUG: False}


async def test_reauth_token(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    entry = await _loaded_entry(hass, aioclient_mock)
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_TOKEN: "ntn_new"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_API_TOKEN] == "ntn_new"


async def test_reauth_wrong_account(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    entry = await _loaded_entry(hass, aioclient_mock)
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{NOTION_API_BASE}/users/me", json={**ME, "id": "someone-else"})
    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_TOKEN: "ntn_other"})
    assert result["reason"] == "wrong_account"


async def test_oauth_flow(
    hass: HomeAssistant,
    hass_client_no_auth,
    aioclient_mock: AiohttpClientMocker,
    current_request_with_host,
) -> None:
    from homeassistant.components.application_credentials import (
        ClientCredential,
        async_import_client_credential,
    )
    from homeassistant.helpers import config_entry_oauth2_flow
    from homeassistant.setup import async_setup_component

    from custom_components.notion_workspace.const import OAUTH_TOKEN_URL

    await async_setup_component(hass, "application_credentials", {})
    await async_import_client_credential(hass, DOMAIN, ClientCredential("cid", "csecret"), "Notion")
    mock_notion(aioclient_mock)
    mock_search(aioclient_mock)
    aioclient_mock.post(
        OAUTH_TOKEN_URL,
        json={
            "access_token": "oauth-at",
            "refresh_token": "oauth-rt",
            "token_type": "bearer",
            "bot_id": "bot-1",
            "workspace_id": "ws-1",
            "workspace_name": "Hemma",
            "owner": {"type": "user", "user": {"object": "user", "id": USER_ID}},
        },
    )

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "pick_implementation"}
    )
    assert result["type"] is FlowResultType.EXTERNAL_STEP
    assert "owner=user" in result["url"]
    assert result["url"].startswith("https://api.notion.com/v1/oauth/authorize?")
    state = config_entry_oauth2_flow._encode_jwt(
        hass, {"flow_id": result["flow_id"], "redirect_uri": "https://example.com/auth/external/callback"}
    )
    client = await hass_client_no_auth()
    resp = await client.get(f"/auth/external/callback?code=abcd&state={state}")
    assert resp.status == 200

    result = await hass.config_entries.flow.async_configure(result["flow_id"])
    assert result["step_id"] == "databases"
    token_call = next(c for c in aioclient_mock.mock_calls if str(c[1]) == OAUTH_TOKEN_URL)
    assert token_call[2]["grant_type"] == "authorization_code"
    assert "client_id" not in token_call[2]

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"databases": []})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"pages": []})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    entry = result["result"]
    assert entry.unique_id == "ws-1"
    assert entry.title == "Hemma"
    assert entry.data[CONF_AUTH_TYPE] == "oauth"
    assert entry.data[CONF_USER_ID] == USER_ID
    assert entry.data["token"]["access_token"] == "oauth-at"
    assert entry.data["token"]["expires_in"] > 0
