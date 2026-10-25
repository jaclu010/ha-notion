# Notion Workspace for Home Assistant

Show Notion in Home Assistant:

- **Databases** as **todo lists with two-way sync** (check an item in HA and the status changes in Notion, and the other way around) and as a **table sensor** with the columns you pick.
- **Pages** as **markdown** (for the Markdown card) and their **properties as sensors** (created by, dates, status, numbers, checkboxes …).
- Everything is picked **in the Home Assistant UI**. With a personal access token you never have to share pages with an integration inside Notion.

> Debug mode is **on by default** in this early version and logs every Notion API call, sync differences and writes. Turn it off under *Settings → Devices & services → Notion Workspace → Configure*.

## Installation (HACS)

1. HACS → ⋮ → *Custom repositories* → add `https://github.com/jaclu010/ha-notion` as type *Integration*.
2. Install **Notion Workspace** and restart Home Assistant.
3. *Settings → Devices & services → Add integration → Notion Workspace*.

Requires Home Assistant 2026.9 or newer.

## Signing in

### Personal access token (recommended)

Create a token at [notion.so/developers/tokens](https://www.notion.so/developers/tokens) (capability *Notion API*). The token acts as you, so every page and database you can open is listed in Home Assistant. Tokens expire (at most after one year). Home Assistant then asks for a new one.

On the Free plan only workspace owners can create tokens. On Business/Enterprise an admin may have to allow it.

### Internal integration secret

A secret from an internal integration (`notion.so/profile/integrations`) also works in the same field. Only pages and databases shared with that integration (*••• → Connections*) are listed.

### OAuth (public integration)

1. Create a **public** integration in Notion with the redirect URI `https://my.home-assistant.io/redirect/oauth`.
2. In Home Assistant: *Settings → Devices & services → ⋮ → Application credentials → Add*, choose Notion Workspace and paste the OAuth client id and secret.
3. Add the integration and pick *Sign in with Notion*. Notion asks which pages to share.

## Setup flow

1. Pick the **databases** to add.
2. Pick **pages**. The list shows recently edited pages. Type a search term and submit to find more, including database rows.
3. Settings per database:
   - **Notion view**: reuse the filter and sort order of a view, for example "My tasks".
   - **Extra columns**: shown under each todo item and in the table sensor.
   - **Todo list**: done property (status or checkbox), which status values mean *done*, the value used when un-checking, due date, description, "assign new items to me", and for how many days done items stay visible.
4. Settings per page: show content as markdown, and which properties become entities.

Add or change databases and pages later with **Add database** / **Add page** and the ⋮ menu on each entry on the integration page.

Deleting items is not supported from Home Assistant on purpose. Items are only created, edited and checked off.

## Entities

| Entity | State | Attributes |
|---|---|---|
| `todo.<database>` | number of open items | items with summary, status, due date and description (+ extra columns) |
| `sensor.<database>_table` | number of rows | `columns`, `rows` (per row: `id`, `url`, `title`, `cells`, `values`), `markdown` (GFM table) |
| `sensor.<page>_content` | last edited time | `markdown`, `title`, `url`, `truncated` |
| `sensor.<page>_<property>` | property value | `notion_type`, plus lists, user ids, end dates, `full_text` for long text |
| `binary_sensor.<page>_<property>` | checkbox | |

Large attributes (`markdown`, `rows`) are not stored in the recorder.

## Dashboard examples

Todo list:

```yaml
type: todo-list
entity: todo.hemmafix
```

Database as a table:

```yaml
type: markdown
content: "{{ state_attr('sensor.hemmafix_table', 'markdown') }}"
```

Page content:

```yaml
type: markdown
title: "{{ state_attr('sensor.husets_manual_content', 'title') }}"
content: "{{ state_attr('sensor.husets_manual_content', 'markdown') }}"
```

Custom rendering of rows:

```yaml
type: markdown
content: >
  {% for row in state_attr('sensor.hemmafix_table', 'rows') %}
  - [{{ row.title }}]({{ row.url }}) – {{ row.cells.get('Status', '') }}
  {% endfor %}
```

## How syncing works

- Databases are polled (default every 60 s). Only the selected properties are downloaded.
- Writes from Home Assistant show up right away. Changes made in Notion show up on the next poll.
- Pages are checked every 2 minutes with a cheap request. The content is only downloaded when the page changed, or when links to files hosted by Notion (valid for one hour) are about to expire.
- Requests are throttled to Notion's limit of about 3 per second, and `429` responses are retried.

## Development

```bash
uv venv .venv --python 3.14
uv pip install --python .venv/bin/python pytest-homeassistant-custom-component
.venv/bin/python -m pytest
```
