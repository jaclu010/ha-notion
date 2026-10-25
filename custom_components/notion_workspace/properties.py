"""Convert Notion property values to display/native values and back.

No Home Assistant imports, so this module is unit tested on its own.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, tzinfo
from typing import Any

from .api import plain_text
from .const import (
    META_CREATED_BY,
    META_CREATED_TIME,
    META_LAST_EDITED_BY,
    META_LAST_EDITED_TIME,
)

RICH_TEXT_CHUNK = 2000

# Entity kinds a property value can be exposed as.
KIND_TEXT = "text"
KIND_NUMBER = "number"
KIND_ENUM = "enum"
KIND_DATE = "date"
KIND_TIMESTAMP = "timestamp"
KIND_BINARY = "binary"
KIND_COUNT = "count"

UNSUPPORTED_TYPES = frozenset({"button", "verification", "place", "location"})

CHECKED = "✅"
UNCHECKED = "⬜"

META_LABELS = {
    META_CREATED_TIME: "Created",
    META_LAST_EDITED_TIME: "Last edited",
    META_CREATED_BY: "Created by",
    META_LAST_EDITED_BY: "Last edited by",
}


@dataclass(slots=True)
class PropertyValue:
    """A converted Notion property value."""

    type: str
    kind: str
    display: str
    native: Any
    extra: dict[str, Any] = field(default_factory=dict)


def parse_notion_date(value: str | None, tz: tzinfo | None = None) -> date | datetime | None:
    """Parse a Notion date string into a date, or an aware datetime in tz."""
    if not value:
        return None
    if len(value) == 10:
        return date.fromisoformat(value)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(tz) if tz else parsed


def format_date(value: date | datetime | None, tz: tzinfo | None = None) -> str:
    """Format a date or datetime for display."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        if tz:
            value = value.astimezone(tz)
        return value.strftime("%Y-%m-%d %H:%M")
    return value.isoformat()


def format_number(value: float | int | None, number_format: str | None = None) -> str:
    """Format a number for display."""
    if value is None:
        return ""
    if number_format == "percent":
        return f"{_trim(value * 100)} %"
    return _trim(value)


def _trim(value: float | int) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, float):
        return f"{value:.4f}".rstrip("0").rstrip(".")
    return str(value)


def user_name(user: Mapping[str, Any] | None, user_names: Mapping[str, str]) -> str:
    """Return a display name for a user object."""
    if not user:
        return ""
    if user.get("name"):
        return str(user["name"])
    return user_names.get(user.get("id", ""), "")


def user_ids(page: Mapping[str, Any], property_ids: set[str] | None = None) -> set[str]:
    """Collect user ids on a page that lack a name (for lookup)."""
    ids: set[str] = set()

    def add(user: Mapping[str, Any] | None) -> None:
        if user and user.get("id") and not user.get("name"):
            ids.add(user["id"])

    if property_ids is None or META_CREATED_BY in property_ids:
        add(page.get("created_by"))
    if property_ids is None or META_LAST_EDITED_BY in property_ids:
        add(page.get("last_edited_by"))
    for prop in (page.get("properties") or {}).values():
        if property_ids is not None and prop.get("id") not in property_ids:
            continue
        ptype = prop.get("type")
        if ptype == "people":
            for user in prop.get("people") or []:
                add(user)
        elif ptype in ("created_by", "last_edited_by"):
            add(prop.get(ptype))
    return ids


def convert(
    prop: Mapping[str, Any],
    *,
    tz: tzinfo | None = None,
    user_names: Mapping[str, str] | None = None,
    schema: Mapping[str, Any] | None = None,
) -> PropertyValue | None:
    """Convert one page property value. None for unsupported types."""
    names = user_names or {}
    ptype = prop.get("type", "")
    value = prop.get(ptype)
    if ptype in UNSUPPORTED_TYPES:
        return None

    if ptype in ("title", "rich_text"):
        text = plain_text(value)
        return PropertyValue(ptype, KIND_TEXT, text, text or None)
    if ptype in ("url", "email", "phone_number"):
        return PropertyValue(ptype, KIND_TEXT, value or "", value)
    if ptype == "number":
        number_format = ((schema or {}).get("number") or {}).get("format")
        return PropertyValue(
            ptype,
            KIND_NUMBER,
            format_number(value, number_format),
            value * 100 if value is not None and number_format == "percent" else value,
            {"number_format": number_format} if number_format else {},
        )
    if ptype in ("select", "status"):
        name = (value or {}).get("name")
        return PropertyValue(ptype, KIND_ENUM, name or "", name, {"option_id": (value or {}).get("id")})
    if ptype == "multi_select":
        items = [item.get("name", "") for item in value or []]
        return PropertyValue(ptype, KIND_TEXT, ", ".join(items), ", ".join(items) or None, {"items": items})
    if ptype == "checkbox":
        return PropertyValue(ptype, KIND_BINARY, CHECKED if value else UNCHECKED, bool(value))
    if ptype == "date":
        return _convert_date(ptype, value, tz)
    if ptype in ("created_time", "last_edited_time"):
        parsed = parse_notion_date(value, tz)
        return PropertyValue(ptype, KIND_TIMESTAMP, format_date(parsed, tz), parsed)
    if ptype == "people":
        people = [user_name(user, names) for user in value or []]
        people = [p for p in people if p]
        return PropertyValue(
            ptype,
            KIND_TEXT,
            ", ".join(people),
            ", ".join(people) or None,
            {"user_ids": [user.get("id") for user in value or []]},
        )
    if ptype in ("created_by", "last_edited_by"):
        name = user_name(value, names)
        return PropertyValue(ptype, KIND_TEXT, name, name or None, {"user_id": (value or {}).get("id")})
    if ptype == "files":
        files = value or []
        urls = [(f.get(f.get("type", "")) or {}).get("url") for f in files]
        names_ = [f.get("name") or "" for f in files]
        return PropertyValue(
            ptype,
            KIND_COUNT,
            ", ".join(n for n in names_ if n) or str(len(files)),
            len(files),
            {"names": names_, "urls": [u for u in urls if u]},
        )
    if ptype == "relation":
        ids = [item.get("id") for item in value or []]
        return PropertyValue(
            ptype,
            KIND_COUNT,
            str(len(ids)),
            len(ids),
            {"page_ids": ids, "has_more": prop.get("has_more", False)},
        )
    if ptype == "unique_id":
        if not value or value.get("number") is None:
            return PropertyValue(ptype, KIND_TEXT, "", None)
        text = f"{value['prefix']}-{value['number']}" if value.get("prefix") else str(value["number"])
        return PropertyValue(ptype, KIND_TEXT, text, text)
    if ptype == "formula":
        return _convert_formula(value or {}, tz)
    if ptype == "rollup":
        return _convert_rollup(value or {}, tz, names)
    return None


def _convert_date(ptype: str, value: Mapping[str, Any] | None, tz: tzinfo | None) -> PropertyValue:
    if not value:
        return PropertyValue(ptype, KIND_DATE, "", None)
    start = parse_notion_date(value.get("start"), tz)
    end = parse_notion_date(value.get("end"), tz)
    display = format_date(start, tz)
    if end:
        display += f" → {format_date(end, tz)}"
    kind = KIND_TIMESTAMP if isinstance(start, datetime) else KIND_DATE
    return PropertyValue(ptype, kind, display, start, {"end": end.isoformat() if end else None})


def _convert_formula(value: Mapping[str, Any], tz: tzinfo | None) -> PropertyValue:
    rtype = value.get("type")
    result = value.get(rtype) if rtype else None
    if rtype == "number":
        return PropertyValue("formula", KIND_NUMBER, format_number(result), result)
    if rtype == "boolean":
        return PropertyValue("formula", KIND_BINARY, CHECKED if result else UNCHECKED, bool(result))
    if rtype == "date":
        converted = _convert_date("formula", result, tz)
        return converted
    text = "" if result is None else str(result)
    return PropertyValue("formula", KIND_TEXT, text, text or None)


def _convert_rollup(value: Mapping[str, Any], tz: tzinfo | None, names: Mapping[str, str]) -> PropertyValue:
    rtype = value.get("type")
    if rtype == "number":
        number = value.get("number")
        return PropertyValue("rollup", KIND_NUMBER, format_number(number), number)
    if rtype == "date":
        return _convert_date("rollup", value.get("date"), tz)
    if rtype == "array":
        parts = []
        for item in value.get("array") or []:
            converted = convert(item, tz=tz, user_names=names)
            if converted and converted.display:
                parts.append(converted.display)
        text = ", ".join(parts)
        return PropertyValue("rollup", KIND_TEXT, text, text or None, {"items": parts})
    return PropertyValue("rollup", KIND_TEXT, "", None)


def convert_meta(
    page: Mapping[str, Any],
    key: str,
    *,
    tz: tzinfo | None = None,
    user_names: Mapping[str, str] | None = None,
) -> PropertyValue | None:
    """Convert page metadata (created/edited time and user)."""
    names = user_names or {}
    if key in (META_CREATED_TIME, META_LAST_EDITED_TIME):
        raw = page.get(key.removeprefix("meta:"))
        parsed = parse_notion_date(raw, tz)
        return PropertyValue(key, KIND_TIMESTAMP, format_date(parsed, tz), parsed)
    if key in (META_CREATED_BY, META_LAST_EDITED_BY):
        user = page.get(key.removeprefix("meta:"))
        name = user_name(user, names)
        return PropertyValue(key, KIND_TEXT, name, name or None, {"user_id": (user or {}).get("id")})
    return None


def describe_page_properties(
    page: Mapping[str, Any],
    schema: Mapping[str, Mapping[str, Any]] | None,
    meta_labels: Mapping[str, str],
) -> dict[str, dict[str, Any]]:
    """Describe what each page property (and metadata) can be exposed as.

    Returns key -> {"name", "type", "kind"} where key is a property id or a
    meta: key. The kind is taken from the current value, so formula and
    rollup results get a fitting entity type.
    """
    described: dict[str, dict[str, Any]] = {}
    for key in (META_CREATED_TIME, META_LAST_EDITED_TIME, META_CREATED_BY, META_LAST_EDITED_BY):
        value = convert_meta(page, key)
        if value is not None:
            described[key] = {"name": meta_labels.get(key, key), "type": key, "kind": value.kind}
    for name, prop in (page.get("properties") or {}).items():
        ptype = prop.get("type", "")
        if ptype in UNSUPPORTED_TYPES or "id" not in prop:
            continue
        value = convert(prop, schema=(schema or {}).get(prop["id"]))
        if value is None:
            continue
        described[prop["id"]] = {"name": name, "type": ptype, "kind": value.kind}
    return described


def page_property_by_id(page: Mapping[str, Any], prop_id: str) -> tuple[str, Mapping[str, Any]] | None:
    """Return (name, property) for a property id on a page."""
    for name, prop in (page.get("properties") or {}).items():
        if prop.get("id") == prop_id:
            return name, prop
    return None


def property_names(page: Mapping[str, Any]) -> dict[str, str]:
    """Map property id -> current name for a page."""
    return {prop["id"]: name for name, prop in (page.get("properties") or {}).items() if "id" in prop}


# Writing


def rich_text_payload(text: str) -> list[dict[str, Any]]:
    """Build a rich text array, split into Notion's 2000 character chunks."""
    if not text:
        return []
    return [
        {"type": "text", "text": {"content": text[i : i + RICH_TEXT_CHUNK]}}
        for i in range(0, len(text), RICH_TEXT_CHUNK)
    ]


def date_payload(value: date | datetime | None) -> dict[str, Any]:
    """Build a date property value (None clears it)."""
    if value is None:
        return {"date": None}
    return {"date": {"start": value.isoformat()}}


def write_value(ptype: str, value: Any) -> dict[str, Any]:
    """Build a property value payload for writable property types."""
    if ptype == "title":
        return {"title": rich_text_payload(str(value or ""))}
    if ptype == "rich_text":
        return {"rich_text": rich_text_payload(str(value or ""))}
    if ptype == "checkbox":
        return {"checkbox": bool(value)}
    if ptype == "number":
        return {"number": None if value in (None, "") else float(value)}
    if ptype in ("select", "status"):
        return {ptype: None if not value else {"name": str(value)}}
    if ptype == "multi_select":
        items = value if isinstance(value, list) else [v.strip() for v in str(value or "").split(",")]
        return {"multi_select": [{"name": item} for item in items if item]}
    if ptype == "date":
        return date_payload(value)
    if ptype in ("url", "email", "phone_number"):
        return {ptype: value or None}
    if ptype == "people":
        ids = value if isinstance(value, list) else [value]
        return {"people": [{"id": user_id} for user_id in ids if user_id]}
    raise ValueError(f"Property type {ptype} is not writable")
