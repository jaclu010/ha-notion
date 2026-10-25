"""Constants for the Notion Workspace integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "notion_workspace"

NOTION_API_BASE: Final = "https://api.notion.com/v1"
NOTION_VERSION: Final = "2026-03-11"
OAUTH_AUTHORIZE_URL: Final = "https://api.notion.com/v1/oauth/authorize"
OAUTH_TOKEN_URL: Final = "https://api.notion.com/v1/oauth/token"

# Main entry
CONF_AUTH_TYPE: Final = "auth_type"
AUTH_TOKEN: Final = "token"
AUTH_OAUTH: Final = "oauth"
CONF_API_TOKEN: Final = "api_token"
CONF_WORKSPACE_NAME: Final = "workspace_name"
CONF_USER_ID: Final = "user_id"

# Debug mode: on by default for now, flip to False in a later release.
CONF_DEBUG: Final = "debug"
DEFAULT_DEBUG: Final = True

# Subentry types
SUBENTRY_DATABASE: Final = "database"
SUBENTRY_PAGE: Final = "page"

# Shared subentry keys
CONF_SCAN_INTERVAL: Final = "scan_interval"

# Database subentry
CONF_DATA_SOURCE_ID: Final = "data_source_id"
CONF_DATABASE_ID: Final = "database_id"
CONF_VIEW_ID: Final = "view_id"
CONF_COLUMNS: Final = "columns"
CONF_TODO_ENABLED: Final = "todo_enabled"
CONF_TITLE_PROPERTY: Final = "title_property"
CONF_DONE_PROPERTY: Final = "done_property"
CONF_DONE_VALUES: Final = "done_values"
CONF_OPEN_VALUE: Final = "open_value"
CONF_DUE_PROPERTY: Final = "due_property"
CONF_DESCRIPTION_PROPERTY: Final = "description_property"
CONF_ASSIGNEE_PROPERTY: Final = "assignee_property"
CONF_COMPLETED_DAYS: Final = "completed_days"

DEFAULT_DATABASE_SCAN_INTERVAL: Final = 60
DEFAULT_COMPLETED_DAYS: Final = 1
MIN_SCAN_INTERVAL: Final = 15

# Page subentry
CONF_PAGE_ID: Final = "page_id"
CONF_SHOW_MARKDOWN: Final = "show_markdown"
CONF_PROPERTIES: Final = "properties"
CONF_PROPERTY_META: Final = "property_meta"

DEFAULT_PAGE_SCAN_INTERVAL: Final = 120

# Page metadata exposed as pseudo-properties (not real Notion property ids).
META_PREFIX: Final = "meta:"
META_CREATED_TIME: Final = "meta:created_time"
META_LAST_EDITED_TIME: Final = "meta:last_edited_time"
META_CREATED_BY: Final = "meta:created_by"
META_LAST_EDITED_BY: Final = "meta:last_edited_by"
META_PROPERTIES: Final = (
    META_CREATED_TIME,
    META_LAST_EDITED_TIME,
    META_CREATED_BY,
    META_LAST_EDITED_BY,
)

# Selector value used for "no property" in optional dropdowns.
NONE_VALUE: Final = "__none__"

# How often cached schemas/views are refreshed.
SCHEMA_REFRESH_SECONDS: Final = 3600
# Notion-hosted file URLs expire after an hour, refetch markdown before that.
MARKDOWN_FILE_REFRESH_SECONDS: Final = 45 * 60
# last_edited_time is rounded to the minute, so recent edits may hide more edits.
MARKDOWN_EDIT_GRACE_SECONDS: Final = 120

MAX_QUERY_ROWS: Final = 500
