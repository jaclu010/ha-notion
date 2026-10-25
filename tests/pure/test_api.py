"""Tests for the Notion API client."""

import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.notion_workspace.api import (
    NotionAuthError,
    NotionClient,
    NotionError,
    NotionNotFound,
    object_icon,
    object_title,
)
from custom_components.notion_workspace.const import NOTION_API_BASE
from tests.fixtures import DATA_SOURCE, DATA_SOURCE_ID, PAGE, row


async def _token() -> str:
    return "secret-token"


@pytest.fixture
async def client_factory():
    mocker = AiohttpClientMocker()
    clients = []

    def make(**kwargs) -> NotionClient:
        session = mocker.create_session(None)
        client = NotionClient(session, _token, min_interval=0, **kwargs)
        clients.append(session)
        return client

    yield mocker, make
    for session in clients:
        await session.close()


async def test_headers_and_history(client_factory) -> None:
    mocker, make = client_factory
    mocker.get(f"{NOTION_API_BASE}/users/me", json={"object": "user", "id": "u"})
    client = make()
    assert (await client.users_me())["id"] == "u"
    _, url, _, headers = mocker.mock_calls[0]
    assert headers["Authorization"] == "Bearer secret-token"
    assert headers["Notion-Version"] == "2026-03-11"
    assert client.history[-1]["status"] == 200
    assert "secret" not in str(client.history)


async def test_errors(client_factory) -> None:
    mocker, make = client_factory
    mocker.get(f"{NOTION_API_BASE}/users/me", status=401, json={"code": "unauthorized", "message": "nope"})
    mocker.get(f"{NOTION_API_BASE}/pages/x", status=404, json={"code": "object_not_found", "message": "?"})
    mocker.get(f"{NOTION_API_BASE}/pages/y", status=400, json={"code": "validation_error", "message": "bad"})
    client = make()
    with pytest.raises(NotionAuthError):
        await client.users_me()
    with pytest.raises(NotionNotFound):
        await client.retrieve_page("x")
    with pytest.raises(NotionError) as err:
        await client.retrieve_page("y")
    assert err.value.code == "validation_error"


async def test_retry_on_429(client_factory, monkeypatch) -> None:
    mocker, make = client_factory
    sleeps = []

    async def fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr("custom_components.notion_workspace.api.asyncio.sleep", fake_sleep)
    responses = iter([(429, {"code": "rate_limited"}), (200, {"object": "user", "id": "u"})])

    async def side_effect(method, url, data):
        from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMockResponse

        status, body = next(responses)
        return AiohttpClientMockResponse(method, url, status=status, json=body, headers={"Retry-After": "2"})

    mocker.get(f"{NOTION_API_BASE}/users/me", side_effect=side_effect)
    client = make()
    assert (await client.users_me())["id"] == "u"
    assert sleeps == [2.0]


async def test_query_paginates_and_keeps_encoded_ids(client_factory) -> None:
    mocker, make = client_factory
    url = f"{NOTION_API_BASE}/data_sources/{DATA_SOURCE_ID}/query"
    pages = iter(
        [
            {"results": [row()], "has_more": True, "next_cursor": "c1"},
            {"results": [row()], "has_more": False, "next_cursor": None},
        ]
    )

    async def side_effect(method, url_, data):
        from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMockResponse

        return AiohttpClientMockResponse(method, url_, json=next(pages))

    mocker.post(url, side_effect=side_effect)
    client = make()
    rows = await client.query_data_source(DATA_SOURCE_ID, filter_properties=["title", "st%3A1"])
    assert len(rows) == 2
    called_url = mocker.mock_calls[0][1]
    assert "filter_properties[]=st%3A1" in str(called_url)
    assert mocker.mock_calls[1][2]["start_cursor"] == "c1"


def test_titles_and_icons() -> None:
    assert object_title(DATA_SOURCE) == "Hemmafix"
    assert object_title(PAGE) == "Husets manual"
    assert object_icon(PAGE) == "🏡"
    assert object_icon(row()) == ""
