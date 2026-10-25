"""OAuth for Notion public integrations via Application Credentials."""

from __future__ import annotations

import base64
import logging
from typing import Any, cast

from homeassistant.components.application_credentials import (
    AuthImplementation,
    AuthorizationServer,
    ClientCredential,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import OAUTH_AUTHORIZE_URL, OAUTH_TOKEN_URL

_LOGGER = logging.getLogger(__name__)

# Notion does not say when access tokens expire. Refresh weekly so HA keeps
# a working refresh token (Notion rotates it on every refresh).
DEFAULT_EXPIRES_IN = 7 * 24 * 3600


async def async_get_authorization_server(hass: HomeAssistant) -> AuthorizationServer:
    """Return Notion's OAuth endpoints."""
    return AuthorizationServer(authorize_url=OAUTH_AUTHORIZE_URL, token_url=OAUTH_TOKEN_URL)


async def async_get_auth_implementation(
    hass: HomeAssistant, auth_domain: str, credential: ClientCredential
) -> config_entry_oauth2_flow.AbstractOAuth2Implementation:
    """Return the Notion OAuth implementation."""
    return NotionOAuth2Implementation(
        hass, auth_domain, credential, await async_get_authorization_server(hass)
    )


class NotionOAuth2Implementation(AuthImplementation):
    """Notion wants owner=user, HTTP Basic client auth and a JSON body."""

    @property
    def extra_authorize_data(self) -> dict[str, Any]:
        """Ask for a user (not workspace) authorization."""
        return {"owner": "user"}

    async def _token_request(self, data: dict) -> dict:
        """Exchange a code or refresh token for tokens."""
        data.pop("client_id", None)
        session = async_get_clientsession(self.hass)
        _LOGGER.debug("Notion token request (%s)", data.get("grant_type"))
        # aiohttp errors propagate; Home Assistant maps them to OAuth errors.
        credentials = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
        resp = await session.post(
            self.token_url,
            json=data,
            headers={"Authorization": f"Basic {credentials}", "Accept": "application/json"},
        )
        if resp.status >= 400:
            _LOGGER.debug("Notion token request failed (%s): %s", resp.status, (await resp.text())[:200])
        resp.raise_for_status()
        token = cast(dict, await resp.json())
        token.setdefault("expires_in", DEFAULT_EXPIRES_IN)
        return token
