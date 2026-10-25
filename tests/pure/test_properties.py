"""Tests for Notion property conversion."""

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

from custom_components.notion_workspace.const import (
    META_CREATED_BY,
    META_LAST_EDITED_TIME,
)
from custom_components.notion_workspace.properties import (
    KIND_BINARY,
    KIND_COUNT,
    KIND_DATE,
    KIND_ENUM,
    KIND_NUMBER,
    KIND_TEXT,
    KIND_TIMESTAMP,
    convert,
    convert_meta,
    rich_text_payload,
    user_ids,
    write_value,
)
from tests.fixtures import USER_ID, rich, row

TZ = ZoneInfo("Europe/Stockholm")


@pytest.mark.parametrize(
    ("prop", "kind", "display", "native"),
    [
        ({"type": "title", "title": rich("Hej")}, KIND_TEXT, "Hej", "Hej"),
        ({"type": "rich_text", "rich_text": []}, KIND_TEXT, "", None),
        ({"type": "url", "url": "https://a.se"}, KIND_TEXT, "https://a.se", "https://a.se"),
        ({"type": "number", "number": 3.0}, KIND_NUMBER, "3", 3.0),
        ({"type": "number", "number": 1.23456}, KIND_NUMBER, "1.2346", 1.23456),
        ({"type": "select", "select": {"id": "a", "name": "Röd"}}, KIND_ENUM, "Röd", "Röd"),
        ({"type": "select", "select": None}, KIND_ENUM, "", None),
        ({"type": "status", "status": {"id": "s", "name": "Klar"}}, KIND_ENUM, "Klar", "Klar"),
        ({"type": "multi_select", "multi_select": [{"name": "A"}, {"name": "B"}]}, KIND_TEXT, "A, B", "A, B"),
        ({"type": "checkbox", "checkbox": True}, KIND_BINARY, "✅", True),
        ({"type": "checkbox", "checkbox": False}, KIND_BINARY, "⬜", False),
        ({"type": "relation", "relation": [{"id": "x"}, {"id": "y"}]}, KIND_COUNT, "2", 2),
        ({"type": "unique_id", "unique_id": {"prefix": "HEM", "number": 12}}, KIND_TEXT, "HEM-12", "HEM-12"),
        ({"type": "unique_id", "unique_id": {"prefix": None, "number": 7}}, KIND_TEXT, "7", "7"),
        ({"type": "formula", "formula": {"type": "string", "string": "ok"}}, KIND_TEXT, "ok", "ok"),
        ({"type": "formula", "formula": {"type": "number", "number": 2}}, KIND_NUMBER, "2", 2),
        ({"type": "formula", "formula": {"type": "boolean", "boolean": True}}, KIND_BINARY, "✅", True),
        ({"type": "rollup", "rollup": {"type": "number", "number": 5}}, KIND_NUMBER, "5", 5),
    ],
)
def test_convert_simple(prop, kind, display, native) -> None:
    value = convert(prop, tz=TZ)
    assert value is not None
    assert (value.kind, value.display, value.native) == (kind, display, native)


def test_percent_number_uses_schema_format() -> None:
    value = convert({"type": "number", "number": 0.25}, schema={"number": {"format": "percent"}})
    assert value.display == "25 %"
    assert value.native == 25.0


def test_date_and_datetime() -> None:
    value = convert({"type": "date", "date": {"start": "2026-10-20", "end": None}}, tz=TZ)
    assert value.kind == KIND_DATE
    assert value.native == date(2026, 10, 20)
    value = convert(
        {"type": "date", "date": {"start": "2026-10-20T12:00:00.000+00:00", "end": "2026-10-21"}}, tz=TZ
    )
    assert value.kind == KIND_TIMESTAMP
    assert value.native == datetime(2026, 10, 20, 14, 0, tzinfo=TZ)
    assert value.display == "2026-10-20 14:00 → 2026-10-21"


def test_created_time_and_people_lookup() -> None:
    value = convert({"type": "created_time", "created_time": "2026-10-01T10:00:00.000Z"}, tz=TZ)
    assert value.kind == KIND_TIMESTAMP
    assert value.native == datetime(2026, 10, 1, 10, tzinfo=UTC)
    value = convert(
        {"type": "created_by", "created_by": {"object": "user", "id": USER_ID}},
        user_names={USER_ID: "Testa"},
    )
    assert value.display == "Testa"


def test_rollup_array() -> None:
    value = convert(
        {
            "type": "rollup",
            "rollup": {
                "type": "array",
                "array": [
                    {"type": "title", "title": rich("A")},
                    {"type": "title", "title": rich("B")},
                ],
            },
        }
    )
    assert value.display == "A, B"


def test_files() -> None:
    value = convert(
        {
            "type": "files",
            "files": [
                {"name": "a.pdf", "type": "file", "file": {"url": "https://s3/a.pdf"}},
                {"name": "b", "type": "external", "external": {"url": "https://x/b"}},
            ],
        }
    )
    assert value.native == 2
    assert value.extra["urls"] == ["https://s3/a.pdf", "https://x/b"]


def test_unsupported_types() -> None:
    assert convert({"type": "button", "button": {}}) is None
    assert convert({"type": "something_new", "something_new": {}}) is None


def test_meta_and_user_ids() -> None:
    page = row()
    assert convert_meta(page, META_LAST_EDITED_TIME, tz=TZ).native == datetime(2026, 10, 10, 8, tzinfo=UTC)
    assert convert_meta(page, META_CREATED_BY, user_names={USER_ID: "Testa"}).display == "Testa"
    # created_by lacks a name; the people property has one.
    assert user_ids(page) == {USER_ID}
    assert user_ids(page, {"ppl"}) == set()


def test_rich_text_chunks() -> None:
    chunks = rich_text_payload("x" * 4500)
    assert [len(c["text"]["content"]) for c in chunks] == [2000, 2000, 500]
    assert rich_text_payload("") == []


def test_write_values() -> None:
    assert write_value("status", "Klar") == {"status": {"name": "Klar"}}
    assert write_value("select", "") == {"select": None}
    assert write_value("multi_select", "a, b") == {"multi_select": [{"name": "a"}, {"name": "b"}]}
    assert write_value("date", date(2026, 1, 2)) == {"date": {"start": "2026-01-02"}}
    assert write_value("date", None) == {"date": None}
    assert write_value("number", "4") == {"number": 4.0}
    with pytest.raises(ValueError):
        write_value("formula", 1)
