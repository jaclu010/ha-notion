"""Sensors: database tables, page content and page properties."""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigSubentry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import NotionConfigEntry
from .api import object_icon, object_title
from .const import (
    CONF_COLUMNS,
    CONF_PROPERTIES,
    CONF_PROPERTY_META,
    CONF_SHOW_MARKDOWN,
    CONF_TITLE_PROPERTY,
    META_PREFIX,
)
from .coordinator import NotionDatabaseCoordinator, NotionPageCoordinator
from .entity import NotionEntity
from .mapping import schema_by_id, table_markdown, table_rows
from .properties import (
    KIND_BINARY,
    KIND_COUNT,
    KIND_DATE,
    KIND_ENUM,
    KIND_NUMBER,
    KIND_TIMESTAMP,
    PropertyValue,
    convert,
    convert_meta,
    page_property_by_id,
    parse_notion_date,
)

MAX_STATE_LENGTH = 255


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NotionConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensors for each subentry."""
    for subentry_id, coordinator in entry.runtime_data.coordinators.items():
        subentry = entry.subentries[subentry_id]
        entities: list[SensorEntity] = []
        if isinstance(coordinator, NotionDatabaseCoordinator):
            entities.append(NotionTableSensor(coordinator, subentry))
        else:
            if subentry.data.get(CONF_SHOW_MARKDOWN, True):
                entities.append(NotionPageContentSensor(coordinator, subentry))
            meta = subentry.data.get(CONF_PROPERTY_META) or {}
            for key in subentry.data.get(CONF_PROPERTIES) or []:
                if key in meta and meta[key]["kind"] != KIND_BINARY:
                    entities.append(NotionPagePropertySensor(coordinator, subentry, key, meta[key]))
        async_add_entities(entities, config_subentry_id=subentry_id)


def _jsonable(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    return value


class NotionTableSensor(NotionEntity[NotionDatabaseCoordinator], SensorEntity):
    """Row count, with the selected columns as attributes and a markdown table."""

    _attr_translation_key = "table"
    _unrecorded_attributes = frozenset({"columns", "rows", "markdown"})

    def __init__(self, coordinator: NotionDatabaseCoordinator, subentry: ConfigSubentry) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, subentry, "table")
        self._attrs: dict[str, Any] = {}

    @callback
    def _handle_coordinator_update(self) -> None:
        data = self.coordinator.data
        if data is not None:
            schema = schema_by_id(data.props)
            columns = [c for c in self.coordinator.config.get(CONF_COLUMNS) or [] if c in schema]
            column_names = [schema[c].name for c in columns]
            title_id = self.coordinator.config.get(CONF_TITLE_PROPERTY) or "title"
            title_label = schema[title_id].name if title_id in schema else "Name"
            rows = table_rows(
                data.rows,
                columns,
                tz=dt_util.get_default_time_zone(),
                user_names=data.user_names,
                schema=schema,
            )
            self._attr_native_value = len(rows)
            self._attrs = {
                "title": object_title(data.data_source),
                "icon_emoji": object_icon(data.data_source) or None,
                "columns": [title_label, *column_names],
                "rows": rows,
                "markdown": table_markdown(rows, column_names, title_label),
            }
        super()._handle_coordinator_update()

    async def async_added_to_hass(self) -> None:
        """Populate from the first refresh."""
        await super().async_added_to_hass()
        self._handle_coordinator_update()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return table attributes."""
        return self._attrs


class NotionPageContentSensor(NotionEntity[NotionPageCoordinator], SensorEntity):
    """Last edited time, with the page content as markdown in an attribute."""

    _attr_translation_key = "content"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _unrecorded_attributes = frozenset({"markdown", "title", "url", "truncated", "icon_emoji"})

    def __init__(self, coordinator: NotionPageCoordinator, subentry: ConfigSubentry) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, subentry, "content")

    @property
    def native_value(self) -> datetime | None:
        """Return when the page was last edited."""
        if self.coordinator.data is None:
            return None
        value = parse_notion_date(self.coordinator.data.page.get("last_edited_time"))
        return value if isinstance(value, datetime) else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the markdown and page info."""
        data = self.coordinator.data
        if data is None:
            return {}
        markdown = data.markdown.markdown if data.markdown else ""
        if data.markdown_truncated:
            markdown += "\n\n_… (sidan är för stor och har kortats)_"
        return {
            "markdown": markdown,
            "title": object_title(data.page),
            "icon_emoji": object_icon(data.page) or None,
            "url": data.page.get("url"),
            "truncated": data.markdown_truncated,
        }


class NotionPagePropertySensor(NotionEntity[NotionPageCoordinator], SensorEntity):
    """One property (or metadata field) of a page."""

    def __init__(
        self,
        coordinator: NotionPageCoordinator,
        subentry: ConfigSubentry,
        key: str,
        meta: dict[str, Any],
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, subentry, f"prop_{key}")
        self._key = key
        self._kind = meta["kind"]
        if key.startswith(META_PREFIX):
            self._attr_translation_key = key.removeprefix(META_PREFIX)
        else:
            self._attr_name = meta["name"]
        if self._kind == KIND_TIMESTAMP:
            self._attr_device_class = SensorDeviceClass.TIMESTAMP
        elif self._kind == KIND_DATE:
            self._attr_device_class = SensorDeviceClass.DATE
        elif self._kind == KIND_ENUM:
            self._attr_device_class = SensorDeviceClass.ENUM

    def _value(self) -> PropertyValue | None:
        data = self.coordinator.data
        if data is None:
            return None
        tz = dt_util.get_default_time_zone()
        if self._key.startswith(META_PREFIX):
            return convert_meta(data.page, self._key, tz=tz, user_names=data.user_names)
        found = page_property_by_id(data.page, self._key)
        if found is None:
            return None
        schema_prop = data.schema_by_id.get(self._key)
        return convert(
            found[1],
            tz=tz,
            user_names=data.user_names,
            schema={schema_prop.type: schema_prop.config} if schema_prop else None,
        )

    @property
    def available(self) -> bool:
        """Unavailable when the property no longer exists on the page."""
        return super().available and self._value() is not None

    @property
    def native_value(self) -> Any:
        """Return the value in the shape the device class expects."""
        value = self._value()
        if value is None or value.native is None:
            return None
        native = value.native
        if self._kind == KIND_TIMESTAMP:
            if isinstance(native, datetime):
                return native
            if isinstance(native, date):
                return datetime.combine(native, time(), tzinfo=dt_util.get_default_time_zone())
            return None
        if self._kind == KIND_DATE:
            if isinstance(native, datetime):
                return native.date()
            return native if isinstance(native, date) else None
        if self._kind in (KIND_NUMBER, KIND_COUNT):
            return native if isinstance(native, (int, float)) else None
        if self._kind == KIND_ENUM:
            return native
        text = value.display or None
        return text[:MAX_STATE_LENGTH] if text else None

    @property
    def options(self) -> list[str] | None:
        """Options of a select or status property."""
        if self._kind != KIND_ENUM:
            return None
        data = self.coordinator.data
        names: list[str] = []
        if data is not None:
            schema_prop = data.schema_by_id.get(self._key)
            if schema_prop is not None:
                names = [o.get("name") for o in schema_prop.config.get("options") or [] if o.get("name")]
        value = self._value()
        if value is not None and value.native and value.native not in names:
            names.append(value.native)
        return names

    @property
    def native_unit_of_measurement(self) -> str | None:
        """Percent numbers get a unit."""
        value = self._value()
        if self._kind == KIND_NUMBER and value is not None and value.extra.get("number_format") == "percent":
            return "%"
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Extra details (full text, list items, user ids, ...)."""
        value = self._value()
        if value is None:
            return None
        attrs: dict[str, Any] = {"notion_type": value.type, **_jsonable(value.extra)}
        if len(value.display) > MAX_STATE_LENGTH:
            attrs["full_text"] = value.display
        return attrs
