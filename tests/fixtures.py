"""Synthetic Notion API objects for tests (hand written, no real workspace data)."""

from __future__ import annotations

import copy
from typing import Any

DATA_SOURCE_ID = "11111111-1111-4111-8111-111111111111"
DATABASE_ID = "22222222-2222-4222-8222-222222222222"
PAGE_ID = "33333333-3333-4333-8333-333333333333"
ROW_ID_1 = "44444444-4444-4444-8444-444444444441"
ROW_ID_2 = "44444444-4444-4444-8444-444444444442"
USER_ID = "55555555-5555-4555-8555-555555555555"
VIEW_ID = "66666666-6666-4666-8666-666666666666"

OPT_NOT_STARTED = "opt-not-started"
OPT_IN_PROGRESS = "opt-in-progress"
OPT_DONE = "opt-done"
OPT_ARCHIVED = "opt-archived"


def rich(text: str) -> list[dict[str, Any]]:
    return [{"type": "text", "text": {"content": text}, "plain_text": text}]


DATA_SOURCE: dict[str, Any] = {
    "object": "data_source",
    "id": DATA_SOURCE_ID,
    "title": rich("Hemmafix"),
    "icon": {"type": "emoji", "emoji": "🔨"},
    "parent": {"type": "database_id", "database_id": DATABASE_ID},
    "properties": {
        "Status": {
            "id": "st%3A1",
            "name": "Status",
            "type": "status",
            "status": {
                "options": [
                    {"id": OPT_NOT_STARTED, "name": "Inte påbörjad", "color": "default"},
                    {"id": OPT_IN_PROGRESS, "name": "Pågår", "color": "blue"},
                    {"id": OPT_DONE, "name": "Klar", "color": "green"},
                    {"id": OPT_ARCHIVED, "name": "Arkiverad", "color": "gray"},
                ],
                "groups": [
                    {"id": "g1", "name": "To-do", "option_ids": [OPT_NOT_STARTED]},
                    {"id": "g2", "name": "In progress", "option_ids": [OPT_IN_PROGRESS]},
                    {"id": "g3", "name": "Complete", "option_ids": [OPT_DONE, OPT_ARCHIVED]},
                ],
            },
        },
        "Uppgift": {"id": "title", "name": "Uppgift", "type": "title", "title": {}},
        "Förfaller": {"id": "due%3F", "name": "Förfaller", "type": "date", "date": {}},
        "Anteckningar": {"id": "note", "name": "Anteckningar", "type": "rich_text", "rich_text": {}},
        "Ansvarig": {"id": "ppl", "name": "Ansvarig", "type": "people", "people": {}},
        "Taggar": {
            "id": "tags",
            "name": "Taggar",
            "type": "multi_select",
            "multi_select": {"options": [{"id": "t1", "name": "El"}, {"id": "t2", "name": "VVS"}]},
        },
        "Kostnad": {"id": "cost", "name": "Kostnad", "type": "number", "number": {"format": "number"}},
        "Andel": {"id": "pct", "name": "Andel", "type": "number", "number": {"format": "percent"}},
        "Skapad": {"id": "ct", "name": "Skapad", "type": "created_time", "created_time": {}},
        "Knapp": {"id": "btn", "name": "Knapp", "type": "button", "button": {}},
    },
}


def row(
    page_id: str = ROW_ID_1,
    *,
    title: str = "Byt lampa",
    status: str | None = OPT_NOT_STARTED,
    due: str | None = "2026-10-20",
    note: str = "",
    edited: str = "2026-10-10T08:00:00.000Z",
) -> dict[str, Any]:
    status_obj = None
    if status:
        names = {o["id"]: o["name"] for o in DATA_SOURCE["properties"]["Status"]["status"]["options"]}
        status_obj = {"id": status, "name": names[status], "color": "default"}
    return {
        "object": "page",
        "id": page_id,
        "created_time": "2026-10-01T10:00:00.000Z",
        "last_edited_time": edited,
        "created_by": {"object": "user", "id": USER_ID},
        "last_edited_by": {"object": "user", "id": USER_ID},
        "parent": {"type": "data_source_id", "data_source_id": DATA_SOURCE_ID, "database_id": DATABASE_ID},
        "in_trash": False,
        "url": f"https://www.notion.so/{page_id.replace('-', '')}",
        "icon": None,
        "properties": {
            "Uppgift": {"id": "title", "type": "title", "title": rich(title)},
            "Status": {"id": "st%3A1", "type": "status", "status": status_obj},
            "Förfaller": {
                "id": "due%3F",
                "type": "date",
                "date": {"start": due, "end": None, "time_zone": None} if due else None,
            },
            "Anteckningar": {"id": "note", "type": "rich_text", "rich_text": rich(note) if note else []},
            "Ansvarig": {
                "id": "ppl",
                "type": "people",
                "people": [{"object": "user", "id": USER_ID, "name": "Testa Testsson"}],
            },
            "Taggar": {"id": "tags", "type": "multi_select", "multi_select": [{"id": "t1", "name": "El"}]},
            "Kostnad": {"id": "cost", "type": "number", "number": 129.5},
            "Andel": {"id": "pct", "type": "number", "number": 0.25},
            "Skapad": {"id": "ct", "type": "created_time", "created_time": "2026-10-01T10:00:00.000Z"},
            "Knapp": {"id": "btn", "type": "button", "button": {}},
        },
    }


PAGE: dict[str, Any] = {
    "object": "page",
    "id": PAGE_ID,
    "created_time": "2026-09-01T07:30:00.000Z",
    "last_edited_time": "2026-10-11T09:15:00.000Z",
    "created_by": {"object": "user", "id": USER_ID},
    "last_edited_by": {"object": "user", "id": USER_ID},
    "parent": {"type": "workspace", "workspace": True},
    "in_trash": False,
    "url": f"https://www.notion.so/{PAGE_ID.replace('-', '')}",
    "icon": {"type": "emoji", "emoji": "🏡"},
    "properties": {"title": {"id": "title", "type": "title", "title": rich("Husets manual")}},
}

USER = {"object": "user", "id": USER_ID, "type": "person", "name": "Testa Testsson", "person": {}}

VIEW = {
    "object": "view",
    "id": VIEW_ID,
    "name": "Mina uppgifter",
    "type": "table",
    "data_source_id": DATA_SOURCE_ID,
    "filter": {"property": "ppl", "people": {"contains": "me"}},
    "sorts": [{"property": "due%3F", "direction": "ascending"}],
}

MARKDOWN = {
    "object": "page_markdown",
    "markdown": '# Manual\n<callout icon="💡">\n\tStäng av huvudkranen först\n</callout>',
    "truncated": False,
    "unknown_block_ids": [],
}


def clone(obj: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(obj)
