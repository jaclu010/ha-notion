"""Binary sensors for checkbox (and boolean formula) page properties."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import NotionConfigEntry
from .const import CONF_PROPERTIES, CONF_PROPERTY_META
from .coordinator import NotionPageCoordinator
from .entity import NotionEntity
from .properties import KIND_BINARY, convert, page_property_by_id


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NotionConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up binary sensors for page subentries."""
    for subentry_id, coordinator in entry.runtime_data.coordinators.items():
        if not isinstance(coordinator, NotionPageCoordinator):
            continue
        subentry = entry.subentries[subentry_id]
        meta = subentry.data.get(CONF_PROPERTY_META) or {}
        entities = [
            NotionPagePropertyBinarySensor(coordinator, subentry, key, meta[key])
            for key in subentry.data.get(CONF_PROPERTIES) or []
            if key in meta and meta[key]["kind"] == KIND_BINARY
        ]
        if entities:
            async_add_entities(entities, config_subentry_id=subentry_id)


class NotionPagePropertyBinarySensor(NotionEntity[NotionPageCoordinator], BinarySensorEntity):
    """A checkbox property of a page."""

    def __init__(
        self,
        coordinator: NotionPageCoordinator,
        subentry: ConfigSubentry,
        key: str,
        meta: dict[str, Any],
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator, subentry, f"prop_{key}")
        self._key = key
        self._attr_name = meta["name"]

    @property
    def available(self) -> bool:
        """Unavailable when the property no longer exists on the page."""
        return (
            super().available
            and self.coordinator.data is not None
            and page_property_by_id(self.coordinator.data.page, self._key) is not None
        )

    @property
    def is_on(self) -> bool | None:
        """Return the checkbox value."""
        if self.coordinator.data is None:
            return None
        found = page_property_by_id(self.coordinator.data.page, self._key)
        if found is None:
            return None
        value = convert(found[1])
        return bool(value.native) if value is not None and value.kind == KIND_BINARY else None
