"""Todo lists backed by Notion data sources."""

from __future__ import annotations

import logging

from homeassistant.components.todo import (
    TodoItem,
    TodoItemStatus,
    TodoListEntity,
    TodoListEntityFeature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import NotionConfigEntry
from .api import NotionError, truncate
from .const import (
    CONF_ASSIGNEE_PROPERTY,
    CONF_DESCRIPTION_PROPERTY,
    CONF_DUE_PROPERTY,
    CONF_TODO_ENABLED,
    DOMAIN,
)
from .coordinator import NotionDatabaseCoordinator
from .entity import NotionEntity
from .mapping import create_payload, schema_by_id, todo_fields, update_payload

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NotionConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up todo lists for database subentries with todo enabled."""
    for subentry_id, coordinator in entry.runtime_data.coordinators.items():
        if isinstance(coordinator, NotionDatabaseCoordinator) and coordinator.config.get(CONF_TODO_ENABLED):
            async_add_entities(
                [NotionTodoListEntity(coordinator, entry.subentries[subentry_id])],
                config_subentry_id=subentry_id,
            )


class NotionTodoListEntity(NotionEntity[NotionDatabaseCoordinator], TodoListEntity):
    """A Notion data source shown as a todo list."""

    _attr_name = None

    def __init__(self, coordinator: NotionDatabaseCoordinator, subentry) -> None:
        """Initialize the todo list."""
        super().__init__(coordinator, subentry, "todo")
        config = coordinator.config
        features = TodoListEntityFeature.CREATE_TODO_ITEM | TodoListEntityFeature.UPDATE_TODO_ITEM
        if config.get(CONF_DUE_PROPERTY):
            features |= (
                TodoListEntityFeature.SET_DUE_DATE_ON_ITEM | TodoListEntityFeature.SET_DUE_DATETIME_ON_ITEM
            )
        if config.get(CONF_DESCRIPTION_PROPERTY):
            features |= TodoListEntityFeature.SET_DESCRIPTION_ON_ITEM
        self._attr_supported_features = features

    @callback
    def _handle_coordinator_update(self) -> None:
        data = self.coordinator.data
        if data is None:
            self._attr_todo_items = None
        else:
            schema = schema_by_id(data.props)
            tz = dt_util.get_default_time_zone()
            items = []
            for row in data.rows:
                fields = todo_fields(
                    row, self.coordinator.config, tz=tz, user_names=data.user_names, schema=schema
                )
                items.append(
                    TodoItem(
                        uid=fields.uid,
                        summary=fields.summary,
                        status=TodoItemStatus.COMPLETED if fields.completed else TodoItemStatus.NEEDS_ACTION,
                        due=fields.due,
                        description=fields.description,
                    )
                )
            self._attr_todo_items = items
        super()._handle_coordinator_update()

    async def async_added_to_hass(self) -> None:
        """Populate items from the first refresh."""
        await super().async_added_to_hass()
        self._handle_coordinator_update()

    async def async_create_todo_item(self, item: TodoItem) -> None:
        """Create a row in Notion."""
        coordinator = self.coordinator
        if coordinator.data is None:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="not_loaded")
        me_id = coordinator.me_id if coordinator.config.get(CONF_ASSIGNEE_PROPERTY) else None
        payload = create_payload(
            coordinator.config,
            coordinator.data.props,
            summary=item.summary or "",
            completed=item.status == TodoItemStatus.COMPLETED,
            due=item.due,
            description=item.description,
            me_id=me_id,
        )
        _LOGGER.debug("%s: create item %r -> %s", self._subentry.title, item.summary, truncate(payload))
        try:
            page = await coordinator.client.create_page(coordinator.data_source_id, payload)
        except NotionError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="write_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        coordinator.apply_written_page(page)

    async def async_update_todo_item(self, item: TodoItem) -> None:
        """Write changed fields back to Notion."""
        coordinator = self.coordinator
        row = coordinator.row(item.uid or "")
        if row is None or coordinator.data is None:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="item_not_found")
        data = coordinator.data
        old = todo_fields(
            row,
            coordinator.config,
            tz=dt_util.get_default_time_zone(),
            user_names=data.user_names,
            schema=schema_by_id(data.props),
        )
        payload = update_payload(
            coordinator.config,
            data.props,
            old,
            summary=item.summary,
            completed=(item.status == TodoItemStatus.COMPLETED) if item.status else None,
            due=item.due,
            description=item.description,
        )
        _LOGGER.debug(
            "%s: update item %r status=%s due=%s -> %s",
            self._subentry.title,
            item.summary,
            item.status,
            item.due,
            truncate(payload) if payload else "no changes",
        )
        if not payload:
            return
        try:
            page = await coordinator.client.update_page(row["id"], payload)
        except NotionError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="write_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        coordinator.apply_written_page(page)
