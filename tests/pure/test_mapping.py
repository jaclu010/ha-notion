"""Tests for row <-> todo mapping, filters and tables."""

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from custom_components.notion_workspace.const import (
    CONF_ASSIGNEE_PROPERTY,
    CONF_COLUMNS,
    CONF_COMPLETED_DAYS,
    CONF_DESCRIPTION_PROPERTY,
    CONF_DONE_PROPERTY,
    CONF_DONE_VALUES,
    CONF_DUE_PROPERTY,
    CONF_OPEN_VALUE,
    CONF_TITLE_PROPERTY,
    CONF_TODO_ENABLED,
)
from custom_components.notion_workspace.mapping import (
    build_filter,
    create_payload,
    default_columns,
    detect_todo_mapping,
    drop_old_completed,
    filter_property_ids,
    schema_by_id,
    schema_properties,
    strip_column_line,
    table_markdown,
    table_rows,
    todo_fields,
    update_payload,
)
from tests.fixtures import (
    DATA_SOURCE,
    OPT_ARCHIVED,
    OPT_DONE,
    OPT_IN_PROGRESS,
    OPT_NOT_STARTED,
    ROW_ID_2,
    USER_ID,
    row,
)

TZ = ZoneInfo("Europe/Stockholm")
PROPS = schema_properties(DATA_SOURCE)
NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)


def config(**overrides):
    cfg = detect_todo_mapping(PROPS)
    cfg[CONF_COLUMNS] = ["ppl", "tags"]
    cfg.update(overrides)
    return cfg


def test_schema_title_first() -> None:
    assert PROPS[0].type == "title"


def test_detect_todo_mapping() -> None:
    mapping = detect_todo_mapping(PROPS)
    assert mapping[CONF_TODO_ENABLED] is True
    assert mapping[CONF_TITLE_PROPERTY] == "title"
    assert mapping[CONF_DONE_PROPERTY] == "st%3A1"
    assert mapping[CONF_DONE_VALUES] == [OPT_DONE, OPT_ARCHIVED]
    assert mapping[CONF_OPEN_VALUE] == OPT_NOT_STARTED
    assert mapping[CONF_DUE_PROPERTY] == "due%3F"
    assert mapping[CONF_DESCRIPTION_PROPERTY] == "note"
    assert mapping[CONF_ASSIGNEE_PROPERTY] == "ppl"


def test_detect_checkbox_and_no_completion() -> None:
    schema = {
        "properties": {
            "Namn": {"id": "title", "type": "title", "title": {}},
            "Köpt": {"id": "cb", "type": "checkbox", "checkbox": {}},
        }
    }
    mapping = detect_todo_mapping(schema_properties(schema))
    assert mapping[CONF_DONE_PROPERTY] == "cb"
    schema = {"properties": {"Namn": {"id": "title", "type": "title", "title": {}}}}
    assert detect_todo_mapping(schema_properties(schema))[CONF_TODO_ENABLED] is False


def test_default_columns_skip_mapped() -> None:
    columns = default_columns(PROPS, detect_todo_mapping(PROPS))
    assert columns == ["tags", "ppl", "cost"]


def test_build_filter_status() -> None:
    assert build_filter(config(), PROPS, NOW) == {
        "or": [
            {
                "and": [
                    {"property": "st%3A1", "status": {"does_not_equal": "Klar"}},
                    {"property": "st%3A1", "status": {"does_not_equal": "Arkiverad"}},
                ]
            },
            {
                "timestamp": "last_edited_time",
                "last_edited_time": {"on_or_after": "2026-10-10T12:00:00+00:00"},
            },
        ]
    }
    assert build_filter(config(**{CONF_COMPLETED_DAYS: 0, CONF_DONE_VALUES: [OPT_DONE]}), PROPS, NOW) == {
        "property": "st%3A1",
        "status": {"does_not_equal": "Klar"},
    }
    assert build_filter(config(**{CONF_TODO_ENABLED: False}), PROPS, NOW) is None


def test_filter_property_ids() -> None:
    assert filter_property_ids(config()) == ["title", "st%3A1", "due%3F", "note", "ppl", "tags"]
    assert filter_property_ids(config(**{CONF_TODO_ENABLED: False})) == ["title", "ppl", "tags"]


def test_todo_fields() -> None:
    fields = todo_fields(row(note="Ta stegen"), config(), tz=TZ, schema=schema_by_id(PROPS))
    assert fields.summary == "Byt lampa"
    assert fields.completed is False
    assert fields.due == date(2026, 10, 20)
    assert fields.column_line == "**Ansvarig:** Testa Testsson · **Taggar:** El"
    assert fields.description == "Ta stegen\n\n**Ansvarig:** Testa Testsson · **Taggar:** El"
    assert todo_fields(row(status=OPT_ARCHIVED), config()).completed is True
    assert todo_fields(row(status=OPT_IN_PROGRESS), config()).completed is False


def test_drop_old_completed() -> None:
    pages = [
        row(status=OPT_DONE, edited="2026-10-11T08:00:00.000Z"),
        row(ROW_ID_2, status=OPT_DONE, edited="2026-10-01T08:00:00.000Z"),
        row(status=OPT_NOT_STARTED, edited="2026-01-01T08:00:00.000Z"),
    ]
    kept = drop_old_completed(pages, config(), NOW)
    assert [p["last_edited_time"][:10] for p in kept] == ["2026-10-11", "2026-01-01"]
    assert len(drop_old_completed(pages, config(**{CONF_COMPLETED_DAYS: 0}), NOW)) == 1


def test_create_payload() -> None:
    payload = create_payload(
        config(), PROPS, summary="Ny", due=date(2026, 11, 1), description="Detaljer", me_id=USER_ID
    )
    assert payload == {
        "title": {"title": [{"type": "text", "text": {"content": "Ny"}}]},
        "st%3A1": {"status": {"id": OPT_NOT_STARTED}},
        "due%3F": {"date": {"start": "2026-11-01"}},
        "note": {"rich_text": [{"type": "text", "text": {"content": "Detaljer"}}]},
        "ppl": {"people": [{"id": USER_ID}]},
    }


def test_update_payload_only_changes() -> None:
    cfg = config()
    old = todo_fields(row(note="Ta stegen"), cfg)
    assert (
        update_payload(
            cfg, PROPS, old, summary=old.summary, completed=False, due=old.due, description=old.description
        )
        == {}
    )
    payload = update_payload(
        cfg, PROPS, old, summary=old.summary, completed=True, due=None, description=old.description
    )
    assert payload == {"st%3A1": {"status": {"id": OPT_DONE}}, "due%3F": {"date": None}}
    payload = update_payload(
        cfg,
        PROPS,
        old,
        summary="Byt två lampor",
        completed=False,
        due=old.due,
        description="Ny text\n\n" + old.column_line,
    )
    assert payload == {
        "title": {"title": [{"type": "text", "text": {"content": "Byt två lampor"}}]},
        "note": {"rich_text": [{"type": "text", "text": {"content": "Ny text"}}]},
    }
    done = todo_fields(row(status=OPT_DONE), cfg)
    assert update_payload(
        cfg, PROPS, done, summary=None, completed=False, due=done.due, description=None
    ) == {"st%3A1": {"status": {"id": OPT_NOT_STARTED}}}


def test_checkbox_completion_payload() -> None:
    schema = {
        "properties": {
            "Namn": {"id": "title", "type": "title", "title": {}},
            "Köpt": {"id": "cb", "type": "checkbox", "checkbox": {}},
        }
    }
    props = schema_properties(schema)
    cfg = detect_todo_mapping(props)
    page = {
        "id": "x",
        "properties": {
            "Namn": {"id": "title", "type": "title", "title": []},
            "Köpt": {"id": "cb", "type": "checkbox", "checkbox": False},
        },
    }
    old = todo_fields(page, cfg)
    assert update_payload(cfg, props, old, summary=None, completed=True, due=None, description=None) == {
        "cb": {"checkbox": True}
    }


def test_strip_column_line() -> None:
    assert strip_column_line("Text\n\n**A:** b", "**A:** b") == "Text"
    assert strip_column_line("**A:** b", "**A:** b") == ""
    assert strip_column_line("Helt ny", "**A:** b") == "Helt ny"


def test_table() -> None:
    rows = table_rows(
        [row(), row(ROW_ID_2, title="Rör | läcker")], ["tags", "cost", "pct"], schema=schema_by_id(PROPS)
    )
    assert rows[0]["cells"] == {"Taggar": "El", "Kostnad": "129.5", "Andel": "25 %"}
    assert rows[0]["values"]["Andel"] == 25.0
    markdown = table_markdown(rows, ["Taggar", "Kostnad"], "Uppgift")
    assert markdown.splitlines()[0] == "| Uppgift | Taggar | Kostnad |"
    assert "[Rör \\| läcker](https://www.notion.so/" in markdown.splitlines()[3]
