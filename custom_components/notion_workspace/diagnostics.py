"""Diagnostics for Notion Workspace."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import NotionConfigEntry
from .const import CONF_API_TOKEN, CONF_DEBUG, DEFAULT_DEBUG
from .coordinator import NotionDatabaseCoordinator

TO_REDACT = {CONF_API_TOKEN, "token", "access_token", "refresh_token", "email"}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: NotionConfigEntry) -> dict[str, Any]:
    """Return diagnostics; the API call history is included in debug mode."""
    runtime = entry.runtime_data
    subentries = {}
    for subentry_id, subentry in entry.subentries.items():
        coordinator = runtime.coordinators.get(subentry_id)
        info: dict[str, Any] = {
            "type": subentry.subentry_type,
            "title": subentry.title,
            "data": dict(subentry.data),
        }
        if coordinator is not None:
            info["last_update_success"] = coordinator.last_update_success
            info["last_exception"] = repr(coordinator.last_exception) if coordinator.last_exception else None
            if isinstance(coordinator, NotionDatabaseCoordinator) and coordinator.data is not None:
                info["rows"] = len(coordinator.data.rows)
        subentries[subentry_id] = info
    diagnostics: dict[str, Any] = {
        "entry": {"data": async_redact_data(dict(entry.data), TO_REDACT), "options": dict(entry.options)},
        "me": async_redact_data(runtime.me, TO_REDACT),
        "subentries": subentries,
    }
    if entry.options.get(CONF_DEBUG, DEFAULT_DEBUG):
        diagnostics["api_calls"] = list(runtime.client.history)
    return diagnostics
