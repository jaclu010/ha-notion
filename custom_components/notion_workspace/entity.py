"""Base entity for Notion Workspace."""

from __future__ import annotations

from homeassistant.config_entries import ConfigSubentry
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_DATABASE_ID, CONF_PAGE_ID, DOMAIN, SUBENTRY_DATABASE
from .coordinator import NotionDatabaseCoordinator, NotionPageCoordinator


def notion_url(object_id: str | None) -> str | None:
    """Return the web URL of a Notion page or database id."""
    return f"https://www.notion.so/{object_id.replace('-', '')}" if object_id else None


class NotionEntity[C: (NotionDatabaseCoordinator, NotionPageCoordinator)](CoordinatorEntity[C]):
    """An entity belonging to one database or page subentry (one device each)."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: C, subentry: ConfigSubentry, key: str) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self._subentry = subentry
        self._attr_unique_id = f"{subentry.subentry_id}_{key}"
        is_database = subentry.subentry_type == SUBENTRY_DATABASE
        object_id = subentry.data.get(CONF_DATABASE_ID if is_database else CONF_PAGE_ID)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, subentry.subentry_id)},
            name=subentry.title,
            manufacturer="Notion",
            model="Database" if is_database else "Page",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=notion_url(object_id),
        )
