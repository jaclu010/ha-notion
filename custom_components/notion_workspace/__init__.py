"""The Notion Workspace integration."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import NotionAuthError, NotionClient, NotionError
from .const import (
    AUTH_OAUTH,
    CONF_API_TOKEN,
    CONF_AUTH_TYPE,
    CONF_DEBUG,
    CONF_USER_ID,
    DEFAULT_DEBUG,
    DOMAIN,
    SUBENTRY_DATABASE,
    SUBENTRY_PAGE,
)
from .coordinator import NotionDatabaseCoordinator, NotionPageCoordinator

_LOGGER = logging.getLogger(__name__)
_PACKAGE_LOGGER = logging.getLogger(__package__)

PLATFORMS = [Platform.BINARY_SENSOR, Platform.SENSOR, Platform.TODO]

type NotionConfigEntry = ConfigEntry[NotionRuntimeData]


@dataclass(slots=True)
class NotionRuntimeData:
    """Objects shared by the platforms of one config entry."""

    client: NotionClient
    me: dict[str, Any]
    coordinators: dict[str, NotionDatabaseCoordinator | NotionPageCoordinator] = field(default_factory=dict)


def _apply_debug(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Turn the package logger up to DEBUG while debug mode is on."""
    store: dict[str, Any] = hass.data.setdefault(DOMAIN, {})
    if entry.options.get(CONF_DEBUG, DEFAULT_DEBUG):
        store.setdefault("original_log_level", _PACKAGE_LOGGER.level)
        _PACKAGE_LOGGER.setLevel(logging.DEBUG)
        _LOGGER.debug("Debug mode on: logging Notion API calls, sync diffs and writes")
    elif "original_log_level" in store:
        _PACKAGE_LOGGER.setLevel(store.pop("original_log_level"))


def _restore_log_level(hass: HomeAssistant, unloading: ConfigEntry) -> None:
    others = [
        entry
        for entry in hass.config_entries.async_loaded_entries(DOMAIN)
        if entry.entry_id != unloading.entry_id and entry.options.get(CONF_DEBUG, DEFAULT_DEBUG)
    ]
    store: dict[str, Any] = hass.data.get(DOMAIN, {})
    if not others and "original_log_level" in store:
        _PACKAGE_LOGGER.setLevel(store.pop("original_log_level"))


async def _token_getter(hass: HomeAssistant, entry: ConfigEntry):
    """Return an async callable giving a valid access token."""
    if entry.data.get(CONF_AUTH_TYPE) == AUTH_OAUTH:
        try:
            implementation = await config_entry_oauth2_flow.async_get_config_entry_implementation(hass, entry)
        except config_entry_oauth2_flow.ImplementationUnavailableError as err:
            raise ConfigEntryNotReady("OAuth credentials are not available") from err
        session = config_entry_oauth2_flow.OAuth2Session(hass, entry, implementation)

        async def oauth_token() -> str:
            await session.async_ensure_token_valid()
            return session.token["access_token"]

        return oauth_token

    token = entry.data[CONF_API_TOKEN]

    async def static_token() -> str:
        return token

    return static_token


async def async_setup_entry(hass: HomeAssistant, entry: NotionConfigEntry) -> bool:
    """Set up Notion Workspace from a config entry."""
    _apply_debug(hass, entry)
    client = NotionClient(async_get_clientsession(hass), await _token_getter(hass, entry))
    try:
        me = await client.users_me()
    except NotionAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except NotionError as err:
        raise ConfigEntryNotReady(str(err)) from err
    client.remember_user(me)
    _LOGGER.debug("Authenticated as %s (%s)", me.get("name"), me.get("type"))

    runtime = NotionRuntimeData(client=client, me=me)
    for subentry_id, subentry in entry.subentries.items():
        if subentry.subentry_type == SUBENTRY_DATABASE:
            runtime.coordinators[subentry_id] = NotionDatabaseCoordinator(
                hass, entry, subentry, client, entry.data.get(CONF_USER_ID)
            )
        elif subentry.subentry_type == SUBENTRY_PAGE:
            runtime.coordinators[subentry_id] = NotionPageCoordinator(hass, entry, subentry, client)

    # A single failing database or page must not block the others, so no
    # first-refresh-or-raise here; failed coordinators show as unavailable.
    await asyncio.gather(*(coordinator.async_refresh() for coordinator in runtime.coordinators.values()))
    for coordinator in runtime.coordinators.values():
        if isinstance(coordinator.last_exception, ConfigEntryAuthFailed):
            raise coordinator.last_exception

    entry.runtime_data = runtime
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: NotionConfigEntry) -> None:
    """Reload when options or subentries change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: NotionConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        _restore_log_level(hass, entry)
    return unloaded
