"""Map Notion data source rows to todo items and tables (no HA imports)."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, tzinfo
from typing import Any

from .api import plain_text
from .const import (
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
    DEFAULT_COMPLETED_DAYS,
)
from .properties import (
    PropertyValue,
    convert,
    date_payload,
    parse_notion_date,
    rich_text_payload,
)

DONE_GROUP_NAMES = {"complete", "completed", "done", "klar", "klart", "avklarad", "färdig"}
TODO_GROUP_NAMES = {"to-do", "to do", "todo", "not started", "att göra", "ej påbörjad"}

DONE_NAME_RE = re.compile(r"done|klar|complete|finished|färdig|checked|avbockad", re.I)
STATUS_NAME_RE = re.compile(r"status|state|tillstånd", re.I)
DUE_NAME_RE = re.compile(r"due|deadline|förfall|slutdatum|senast", re.I)
DATE_NAME_RE = re.compile(r"date|datum|när|when", re.I)
DATE_EXCLUDE_RE = re.compile(r"complet|klar|created|skapad|start", re.I)
DESCRIPTION_NAME_RE = re.compile(r"desc|beskriv|note|anteckn|kommentar|comment|detalj", re.I)
ASSIGNEE_NAME_RE = re.compile(r"assign|responsible|ansvarig|owner|ägare|tilldelad", re.I)

COLUMN_TYPES = (
    "status",
    "select",
    "multi_select",
    "people",
    "date",
    "number",
    "checkbox",
    "formula",
    "unique_id",
)
DEFAULT_SORTS = [{"timestamp": "created_time", "direction": "ascending"}]


@dataclass(frozen=True, slots=True)
class SchemaProperty:
    """One property in a data source schema."""

    id: str
    name: str
    type: str
    config: Mapping[str, Any]


def schema_properties(data_source: Mapping[str, Any]) -> list[SchemaProperty]:
    """Return the schema properties, title first."""
    props = [
        SchemaProperty(prop["id"], name, prop["type"], prop.get(prop["type"]) or {})
        for name, prop in (data_source.get("properties") or {}).items()
    ]
    return sorted(props, key=lambda p: p.type != "title")


def schema_by_id(props: Sequence[SchemaProperty]) -> dict[str, SchemaProperty]:
    """Index schema properties by id."""
    return {p.id: p for p in props}


def _first(
    props: Sequence[SchemaProperty],
    ptype: str,
    pattern: re.Pattern[str] | None = None,
    exclude: re.Pattern[str] | None = None,
) -> SchemaProperty | None:
    for prop in props:
        if prop.type != ptype:
            continue
        if pattern and not pattern.search(prop.name):
            continue
        if exclude and exclude.search(prop.name):
            continue
        return prop
    return None


def status_groups(prop: SchemaProperty) -> list[Mapping[str, Any]]:
    """Return the groups of a status property."""
    return list(prop.config.get("groups") or [])


def status_options(prop: SchemaProperty) -> list[Mapping[str, Any]]:
    """Return the options of a status or select property."""
    return list(prop.config.get("options") or [])


def option_name(prop: SchemaProperty, option_id: str) -> str | None:
    """Return the current name of an option id."""
    for option in status_options(prop):
        if option.get("id") == option_id:
            return option.get("name")
    return None


def default_done_values(prop: SchemaProperty) -> list[str]:
    """Option ids that mean "completed": the Complete group, else the last group."""
    groups = status_groups(prop)
    for group in groups:
        if str(group.get("name", "")).strip().lower() in DONE_GROUP_NAMES:
            return list(group.get("option_ids") or [])
    if groups:
        return list(groups[-1].get("option_ids") or [])
    options = status_options(prop)
    return [options[-1]["id"]] if options else []


def default_open_value(prop: SchemaProperty) -> str | None:
    """Option id used when un-completing: first option of the To-do group."""
    groups = status_groups(prop)
    for group in groups:
        if str(group.get("name", "")).strip().lower() in TODO_GROUP_NAMES and group.get("option_ids"):
            return group["option_ids"][0]
    if groups and groups[0].get("option_ids"):
        return groups[0]["option_ids"][0]
    options = status_options(prop)
    return options[0]["id"] if options else None


def detect_todo_mapping(props: Sequence[SchemaProperty]) -> dict[str, Any]:
    """Guess a todo mapping for a schema."""
    title = _first(props, "title")
    done = (
        _first(props, "status", STATUS_NAME_RE)
        or _first(props, "status")
        or _first(props, "checkbox", DONE_NAME_RE)
        or _first(props, "checkbox")
    )
    due = _first(props, "date", DUE_NAME_RE) or _first(props, "date", DATE_NAME_RE, DATE_EXCLUDE_RE)
    description = _first(props, "rich_text", DESCRIPTION_NAME_RE)
    assignee = _first(props, "people", ASSIGNEE_NAME_RE)
    mapping: dict[str, Any] = {
        CONF_TODO_ENABLED: done is not None,
        CONF_TITLE_PROPERTY: title.id if title else None,
        CONF_DONE_PROPERTY: done.id if done else None,
        CONF_DONE_VALUES: [],
        CONF_OPEN_VALUE: None,
        CONF_DUE_PROPERTY: due.id if due else None,
        CONF_DESCRIPTION_PROPERTY: description.id if description else None,
        CONF_ASSIGNEE_PROPERTY: assignee.id if assignee else None,
        CONF_COMPLETED_DAYS: DEFAULT_COMPLETED_DAYS,
    }
    if done is not None and done.type == "status":
        mapping[CONF_DONE_VALUES] = default_done_values(done)
        mapping[CONF_OPEN_VALUE] = default_open_value(done)
    return mapping


def default_columns(props: Sequence[SchemaProperty], mapping: Mapping[str, Any], limit: int = 3) -> list[str]:
    """Pick a few short, informative columns not already used by the todo mapping."""
    used = {
        mapping.get(CONF_TITLE_PROPERTY),
        mapping.get(CONF_DUE_PROPERTY),
        mapping.get(CONF_DESCRIPTION_PROPERTY),
    }
    if mapping.get(CONF_TODO_ENABLED):
        used.add(mapping.get(CONF_DONE_PROPERTY))
    columns: list[str] = []
    for ptype in COLUMN_TYPES:
        for prop in props:
            if prop.type == ptype and prop.id not in used and prop.id not in columns:
                columns.append(prop.id)
                if len(columns) >= limit:
                    return columns
    return columns


# Querying


def build_filter(
    config: Mapping[str, Any], props: Sequence[SchemaProperty], now: datetime
) -> dict[str, Any] | None:
    """Server side filter for a todo list without a view: open, or recently edited."""
    if not config.get(CONF_TODO_ENABLED):
        return None
    done = schema_by_id(props).get(config.get(CONF_DONE_PROPERTY) or "")
    if done is None:
        return None
    if done.type == "checkbox":
        open_cond: dict[str, Any] = {"property": done.id, "checkbox": {"equals": False}}
    elif done.type == "status":
        names = [n for n in (option_name(done, oid) for oid in config.get(CONF_DONE_VALUES) or []) if n]
        if not names:
            return None
        conds = [{"property": done.id, "status": {"does_not_equal": name}} for name in names]
        open_cond = conds[0] if len(conds) == 1 else {"and": conds}
    else:
        return None
    days = int(config.get(CONF_COMPLETED_DAYS, DEFAULT_COMPLETED_DAYS))
    if days <= 0:
        return open_cond
    since = (now - timedelta(days=days)).replace(microsecond=0).isoformat()
    return {
        "or": [
            open_cond,
            {"timestamp": "last_edited_time", "last_edited_time": {"on_or_after": since}},
        ]
    }


def filter_property_ids(config: Mapping[str, Any]) -> list[str]:
    """Property ids the rows must include."""
    ids: list[str] = []
    keys = [CONF_TITLE_PROPERTY]
    if config.get(CONF_TODO_ENABLED):
        keys += [CONF_DONE_PROPERTY, CONF_DUE_PROPERTY, CONF_DESCRIPTION_PROPERTY]
    for key in keys:
        value = config.get(key)
        if value and value not in ids:
            ids.append(value)
    for column in config.get(CONF_COLUMNS) or []:
        if column not in ids:
            ids.append(column)
    return ids


def _page_prop(page: Mapping[str, Any], prop_id: str | None) -> Mapping[str, Any] | None:
    if not prop_id:
        return None
    for prop in (page.get("properties") or {}).values():
        if prop.get("id") == prop_id:
            return prop
    return None


def is_completed(page: Mapping[str, Any], config: Mapping[str, Any]) -> bool:
    """Return True if a row counts as completed."""
    prop = _page_prop(page, config.get(CONF_DONE_PROPERTY))
    if prop is None:
        return False
    if prop.get("type") == "checkbox":
        return bool(prop.get("checkbox"))
    if prop.get("type") == "status":
        option = prop.get("status") or {}
        return option.get("id") in (config.get(CONF_DONE_VALUES) or [])
    return False


def drop_old_completed(
    pages: list[dict[str, Any]], config: Mapping[str, Any], now: datetime
) -> list[dict[str, Any]]:
    """Remove completed rows not edited within the configured number of days."""
    if not config.get(CONF_TODO_ENABLED):
        return pages
    days = int(config.get(CONF_COMPLETED_DAYS, DEFAULT_COMPLETED_DAYS))
    since = now - timedelta(days=days)
    kept = []
    for page in pages:
        if is_completed(page, config):
            edited = parse_notion_date(page.get("last_edited_time"))
            if days <= 0 or not isinstance(edited, datetime) or edited < since:
                continue
        kept.append(page)
    return kept


# Todo fields


@dataclass(slots=True)
class TodoFields:
    """Todo item fields extracted from a row."""

    uid: str
    summary: str
    completed: bool
    due: date | datetime | None
    description_text: str | None
    column_line: str

    @property
    def description(self) -> str | None:
        """Description shown in HA: description text plus the column line."""
        return compose_description(self.description_text, self.column_line)


def column_values(
    page: Mapping[str, Any],
    columns: Sequence[str],
    *,
    tz: tzinfo | None = None,
    user_names: Mapping[str, str] | None = None,
    schema: Mapping[str, SchemaProperty] | None = None,
) -> list[tuple[str, PropertyValue]]:
    """Return (name, value) for the configured columns present on a page."""
    by_id = {prop.get("id"): (name, prop) for name, prop in (page.get("properties") or {}).items()}
    values: list[tuple[str, PropertyValue]] = []
    for column in columns:
        if column not in by_id:
            continue
        name, prop = by_id[column]
        schema_prop = (schema or {}).get(column)
        converted = convert(
            prop,
            tz=tz,
            user_names=user_names,
            schema={schema_prop.type: schema_prop.config} if schema_prop else None,
        )
        if converted is not None:
            values.append((name, converted))
    return values


def column_line(values: Sequence[tuple[str, PropertyValue]]) -> str:
    """Render column values as one markdown line."""
    return " · ".join(f"**{name}:** {value.display}" for name, value in values if value.display)


def compose_description(text: str | None, line: str) -> str | None:
    """Join the description text and the generated column line."""
    parts = [part for part in (text, line) if part]
    return "\n\n".join(parts) or None


def strip_column_line(description: str | None, line: str) -> str:
    """Remove the generated column line from a description edited in HA."""
    text = description or ""
    if line and text.rstrip().endswith(line):
        text = text.rstrip()[: -len(line)]
    return text.strip()


def todo_fields(
    page: Mapping[str, Any],
    config: Mapping[str, Any],
    *,
    tz: tzinfo | None = None,
    user_names: Mapping[str, str] | None = None,
    schema: Mapping[str, SchemaProperty] | None = None,
) -> TodoFields:
    """Extract todo fields from a row."""
    title_prop = _page_prop(page, config.get(CONF_TITLE_PROPERTY) or "title")
    if title_prop is None:
        title_prop = next(
            (p for p in (page.get("properties") or {}).values() if p.get("type") == "title"), None
        )
    summary = plain_text((title_prop or {}).get("title"))

    due = None
    due_prop = _page_prop(page, config.get(CONF_DUE_PROPERTY))
    if due_prop and due_prop.get("type") == "date" and due_prop.get("date"):
        due = parse_notion_date(due_prop["date"].get("start"), tz)

    description_text = None
    desc_prop = _page_prop(page, config.get(CONF_DESCRIPTION_PROPERTY))
    if desc_prop and desc_prop.get("type") == "rich_text":
        description_text = plain_text(desc_prop.get("rich_text")) or None

    line = column_line(
        column_values(page, config.get(CONF_COLUMNS) or [], tz=tz, user_names=user_names, schema=schema)
    )
    return TodoFields(
        uid=page["id"],
        summary=summary,
        completed=is_completed(page, config),
        due=due,
        description_text=description_text,
        column_line=line,
    )


def _completion_payload(
    done: SchemaProperty, config: Mapping[str, Any], completed: bool
) -> dict[str, Any] | None:
    if done.type == "checkbox":
        return {"checkbox": completed}
    if done.type == "status":
        if completed:
            values = config.get(CONF_DONE_VALUES) or []
            return {"status": {"id": values[0]}} if values else None
        open_value = config.get(CONF_OPEN_VALUE)
        return {"status": {"id": open_value}} if open_value else None
    return None


def create_payload(
    config: Mapping[str, Any],
    props: Sequence[SchemaProperty],
    *,
    summary: str,
    completed: bool = False,
    due: date | datetime | None = None,
    description: str | None = None,
    me_id: str | None = None,
) -> dict[str, Any]:
    """Properties for a new row."""
    by_id = schema_by_id(props)
    title_id = config.get(CONF_TITLE_PROPERTY) or "title"
    payload: dict[str, Any] = {title_id: {"title": rich_text_payload(summary)}}
    done = by_id.get(config.get(CONF_DONE_PROPERTY) or "")
    if done is not None:
        completion = _completion_payload(done, config, completed)
        if completion:
            payload[done.id] = completion
    if due is not None and config.get(CONF_DUE_PROPERTY):
        payload[config[CONF_DUE_PROPERTY]] = date_payload(due)
    if description and config.get(CONF_DESCRIPTION_PROPERTY):
        payload[config[CONF_DESCRIPTION_PROPERTY]] = {"rich_text": rich_text_payload(description)}
    if me_id and config.get(CONF_ASSIGNEE_PROPERTY):
        payload[config[CONF_ASSIGNEE_PROPERTY]] = {"people": [{"id": me_id}]}
    return payload


def update_payload(
    config: Mapping[str, Any],
    props: Sequence[SchemaProperty],
    old: TodoFields,
    *,
    summary: str | None,
    completed: bool | None,
    due: date | datetime | None,
    description: str | None,
) -> dict[str, Any]:
    """Properties that changed between the current row and the item from HA."""
    by_id = schema_by_id(props)
    payload: dict[str, Any] = {}
    if summary is not None and summary != old.summary:
        payload[config.get(CONF_TITLE_PROPERTY) or "title"] = {"title": rich_text_payload(summary)}
    done = by_id.get(config.get(CONF_DONE_PROPERTY) or "")
    if done is not None and completed is not None and completed != old.completed:
        completion = _completion_payload(done, config, completed)
        if completion:
            payload[done.id] = completion
    if config.get(CONF_DUE_PROPERTY) and due != old.due:
        payload[config[CONF_DUE_PROPERTY]] = date_payload(due)
    if config.get(CONF_DESCRIPTION_PROPERTY):
        new_text = strip_column_line(description, old.column_line)
        if new_text != (old.description_text or ""):
            payload[config[CONF_DESCRIPTION_PROPERTY]] = {"rich_text": rich_text_payload(new_text)}
    return payload


# Tables


def _jsonable(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def table_rows(
    pages: Sequence[Mapping[str, Any]],
    columns: Sequence[str],
    *,
    tz: tzinfo | None = None,
    user_names: Mapping[str, str] | None = None,
    schema: Mapping[str, SchemaProperty] | None = None,
) -> list[dict[str, Any]]:
    """Rows for the table sensor attribute."""
    rows = []
    for page in pages:
        title = ""
        for prop in (page.get("properties") or {}).values():
            if prop.get("type") == "title":
                title = plain_text(prop.get("title"))
                break
        values = column_values(page, columns, tz=tz, user_names=user_names, schema=schema)
        rows.append(
            {
                "id": page.get("id"),
                "url": page.get("url"),
                "title": title,
                "cells": {name: value.display for name, value in values},
                "values": {name: _jsonable(value.native) for name, value in values},
            }
        )
    return rows


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\r", "").replace("\n", "<br>")


def table_markdown(
    rows: Sequence[Mapping[str, Any]], column_names: Sequence[str], title_label: str = "Name"
) -> str:
    """Render rows as a GFM table; the title links to Notion."""
    header = [title_label, *column_names]
    lines = [
        "| " + " | ".join(_cell(h) for h in header) + " |",
        "|" + "|".join("---" for _ in header) + "|",
    ]
    for row in rows:
        title = _cell(row.get("title") or "–")
        if row.get("url"):
            title = f"[{title}]({row['url']})"
        cells = [title] + [_cell(str(row["cells"].get(name, ""))) for name in column_names]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)
