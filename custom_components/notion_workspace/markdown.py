"""Convert Notion enhanced markdown to markdown Home Assistant can render.

Notion returns markdown with XML-like block tags (callouts, toggles, columns,
tables, mentions) and tab-indented children. Home Assistant's markdown card
renders GFM plus a small set of HTML tags, so block tags are rewritten and
indentation is only kept where markdown needs it (under list items).
No Home Assistant imports, so this module is unit tested on its own.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

ALLOWED_HTML = frozenset(
    {
        "a",
        "b",
        "br",
        "code",
        "del",
        "details",
        "div",
        "em",
        "hr",
        "i",
        "img",
        "ins",
        "kbd",
        "li",
        "mark",
        "ol",
        "p",
        "pre",
        "s",
        "span",
        "strong",
        "sub",
        "summary",
        "sup",
        "table",
        "tbody",
        "td",
        "th",
        "thead",
        "tr",
        "u",
        "ul",
        "ha-alert",
        "ha-icon",
    }
)

# Tags whose content is unwrapped as-is.
UNWRAP_TAGS = frozenset(
    {
        "columns",
        "column",
        "synced_block",
        "synced_block_reference",
        "meeting-notes",
        "notes",
        "summary",
        "tabs",
    }
)
DROP_TAGS = frozenset({"transcript", "colgroup"})
SELF_CLOSING_DROP = frozenset({"table_of_contents", "custom-block", "col"})
MEDIA_TAGS = frozenset({"file", "pdf", "video", "audio", "embed", "image"})
LINK_TAGS = {"page": "📄", "database": "🗂️", "folder": "📁"}

_ATTR_RE = re.compile(r'([\w-]+)\??="([^"]*)"')
_TRAILING_ATTRS_RE = re.compile(r'\s*\{(?:\s*[\w-]+\??="[^"]*")+\s*\}\s*$')
_TOGGLE_RE = re.compile(r'toggle\??="true"')
_OPEN_TAG_RE = re.compile(r'^<([a-z][\w-]*)((?:\s+[\w-]+\??="[^"]*")*)\s*(/?)>(.*)$', re.S)
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_LIST_RE = re.compile(r"^([-*+]|\d+[.)])\s+(.*)$")
_SPAN_RE = re.compile(r"<span\b[^>]*>(.*?)</span>", re.S)
_MENTION_LINK_RE = re.compile(
    r'<mention-(page|database|data-source|agent)\s+url="([^"]*)"[^>]*?>(.*?)</mention-\1>', re.S
)
_MENTION_LINK_SELF_RE = re.compile(r'<mention-(page|database|data-source|agent)\s+url="([^"]*)"[^>]*/>')
_MENTION_USER_RE = re.compile(r"<mention-user\b[^>]*>(.*?)</mention-user>", re.S)
_MENTION_USER_SELF_RE = re.compile(r"<mention-user\b[^>]*/>")
_MENTION_DATE_RE = re.compile(r"<mention-date\b([^>]*)/>")
_CITATION_RE = re.compile(r"\[\^([^\]]+)\]")
_INLINE_MATH_RE = re.compile(r"\$`([^`]*)`\$")
_ANY_TAG_RE = re.compile(r"</?([a-z][\w-]*)\b[^>]*?/?>")
_EXPIRING_RE = re.compile(r"prod-files-secure|secure\.notion-static\.com|X-Amz-|file\.notion\.so", re.I)


@dataclass(slots=True)
class MarkdownResult:
    """Converted markdown plus things worth logging."""

    markdown: str
    unknown_tags: set[str] = field(default_factory=set)
    has_expiring_files: bool = False


@dataclass(slots=True)
class _Node:
    indent: int
    text: str
    children: list[_Node] = field(default_factory=list)
    code: list[str] | None = None


def _attrs(text: str) -> dict[str, str]:
    return dict(_ATTR_RE.findall(text))


def _split_indent(line: str) -> tuple[int, str]:
    level = 0
    i = 0
    while i < len(line):
        if line[i] == "\t":
            level += 1
            i += 1
        elif line.startswith("    ", i):
            level += 1
            i += 4
        else:
            break
    return level, line[i:]


def _strip_indent(line: str, levels: int) -> str:
    for _ in range(levels):
        if line.startswith("\t"):
            line = line[1:]
        elif line.startswith("    "):
            line = line[4:]
    return line


def _parse(text: str) -> list[_Node]:
    root = _Node(-1, "")
    stack = [root]
    lines = text.replace("\r\n", "\n").split("\n")
    i = 0
    while i < len(lines):
        raw = lines[i]
        if not raw.strip():
            i += 1
            continue
        level, content = _split_indent(raw)
        node = _Node(level, content.rstrip())
        stripped = content.strip()
        fence = "```" if stripped.startswith("```") else "$$" if stripped == "$$" else None
        if fence:
            body: list[str] = []
            i += 1
            while i < len(lines):
                inner = _strip_indent(lines[i], level)
                if inner.strip() == fence:
                    break
                body.append(inner)
                i += 1
            node.code = body
        while stack[-1].indent >= level:
            stack.pop()
        stack[-1].children.append(node)
        stack.append(node)
        i += 1
    return root.children


def _flatten(nodes: list[_Node]) -> list[str]:
    out: list[str] = []
    for node in nodes:
        out.append(node.text)
        out.extend(_flatten(node.children))
    return out


def _is_http(url: str) -> bool:
    return url.startswith(("http://", "https://"))


class _Renderer:
    def __init__(self) -> None:
        self.unknown: set[str] = set()
        self.expiring = False

    # Inline

    def inline(self, text: str) -> str:
        if _EXPIRING_RE.search(text):
            self.expiring = True
        text = _TRAILING_ATTRS_RE.sub("", text)
        previous = None
        while previous != text:
            previous = text
            text = _SPAN_RE.sub(r"\1", text)
        text = _MENTION_LINK_RE.sub(
            lambda m: f"[{m.group(3) or m.group(2)}]({m.group(2)})" if _is_http(m.group(2)) else m.group(3),
            text,
        )
        text = _MENTION_LINK_SELF_RE.sub(lambda m: f"[↗]({m.group(2)})" if _is_http(m.group(2)) else "", text)
        text = _MENTION_USER_RE.sub(lambda m: f"@{m.group(1)}", text)
        text = _MENTION_USER_SELF_RE.sub("@", text)
        text = _MENTION_DATE_RE.sub(lambda m: self._date(_attrs(m.group(1))), text)
        text = _CITATION_RE.sub(lambda m: f"[↗]({m.group(1)})" if _is_http(m.group(1)) else "", text)
        text = _INLINE_MATH_RE.sub(r"`\1`", text)
        return _ANY_TAG_RE.sub(self._sweep_tag, text)

    def _sweep_tag(self, match: re.Match[str]) -> str:
        name = match.group(1)
        if name in ALLOWED_HTML:
            return match.group(0)
        self.unknown.add(name)
        return ""

    @staticmethod
    def _date(attrs: dict[str, str]) -> str:
        text = attrs.get("start", "")
        if attrs.get("startTime"):
            text += f" {attrs['startTime']}"
        if attrs.get("end"):
            text += f" → {attrs['end']}"
            if attrs.get("endTime"):
                text += f" {attrs['endTime']}"
        return text

    # Blocks

    def blocks(self, nodes: list[_Node]) -> list[str]:
        out: list[str] = []
        prev_group: str | None = None
        i = 0
        while i < len(nodes):
            consumed, lines, group = self.block(nodes, i)
            i += consumed
            if not lines:
                continue
            if out and not (group is not None and group == prev_group):
                out.append("")
            out.extend(lines)
            prev_group = group
        return out

    def block(self, nodes: list[_Node], i: int) -> tuple[int, list[str], str | None]:
        node = nodes[i]
        text = node.text.strip()

        if node.code is not None:
            if text == "$$":
                return 1, ["```", *node.code, "```"], None
            return 1, [text, *node.code, "```"], None

        tag_match = _OPEN_TAG_RE.match(text)
        if text.startswith("</"):
            return 1, [], None  # stray closing tag
        if tag_match:
            name, attr_text, self_closing, rest = tag_match.groups()
            closes_inline = f"</{name}>" in rest
            if (
                self_closing
                or closes_inline
                or not rest.strip()
                or name in UNWRAP_TAGS | DROP_TAGS | {"callout", "details", "table", "tab"}
            ):
                handled = self.tag_block(
                    nodes, i, name, _attrs(attr_text), bool(self_closing), rest, closes_inline
                )
                if handled is not None:
                    return handled

        heading = _HEADING_RE.match(text)
        if heading:
            hashes, title = heading.groups()
            is_toggle = bool(_TOGGLE_RE.search(title)) and node.children
            title = self.inline(title)
            if is_toggle:
                return 1, self.details(f"<strong>{title}</strong>", node.children), None
            lines = [f"{hashes} {title}"]
            if node.children:
                lines += ["", *self.blocks(node.children)]
            return 1, lines, None

        list_match = _LIST_RE.match(text)
        if list_match:
            marker, rest = list_match.groups()
            prefix = f"{marker} "
            lines = [prefix + self.inline(rest)]
            pad = " " * len(prefix)
            for child_line in self.blocks(node.children):
                lines.append(pad + child_line if child_line else "")
            return 1, lines, "list"

        if text.startswith(">"):
            lines = [f"> {self.inline(text[1:].strip())}"]
            for child_line in self.blocks(node.children):
                lines.append(f"> {child_line}" if child_line else ">")
            return 1, lines, None

        group = "pipe" if text.startswith("|") else None
        lines = [self.inline(text)]
        if node.children:
            lines += ["", *self.blocks(node.children)]
        return 1, lines, group

    def gather(self, nodes: list[_Node], i: int, name: str) -> tuple[int, list[_Node]]:
        """Collect the content of a multi-line block tag up to its closing tag."""
        content = list(nodes[i].children)
        depth = 0
        j = i + 1
        while j < len(nodes):
            sibling = nodes[j].text.strip()
            if sibling == f"</{name}>":
                if depth == 0:
                    return j - i + 1, content
                depth -= 1
            elif re.match(rf"<{re.escape(name)}\b(?![^>]*/>)", sibling) and f"</{name}>" not in sibling:
                depth += 1
            content.append(nodes[j])
            j += 1
        # No closing tag: treat children only as content.
        return 1, list(nodes[i].children)

    def tag_block(
        self,
        nodes: list[_Node],
        i: int,
        name: str,
        attrs: dict[str, str],
        self_closing: bool,
        rest: str,
        closes_inline: bool,
    ) -> tuple[int, list[str], str | None] | None:
        if self_closing:
            if name == "empty-block":
                return 1, ["&nbsp;"], None
            if name in SELF_CLOSING_DROP:
                return 1, [], None
            if name == "unknown":
                self.unknown.add(f"unknown({attrs.get('alt', '?')})")
                return 1, [], None
            if name in MEDIA_TAGS:
                return 1, [self.media(attrs, "")], None
            if name.startswith("mention-"):
                return None  # inline content starting a paragraph
            self.unknown.add(name)
            return 1, [], None

        if closes_inline:
            inner = rest[: rest.index(f"</{name}>")]
            if name in LINK_TAGS:
                url = attrs.get("url", "")
                label = self.inline(inner) or name
                icon = LINK_TAGS[name]
                return 1, [f"{icon} [{label}]({url})" if _is_http(url) else f"{icon} {label}"], None
            if name in MEDIA_TAGS:
                return 1, [self.media(attrs, inner)], None
            if name in DROP_TAGS:
                return 1, [], None
            if name == "summary":
                return 1, [f"**{self.inline(inner)}**"], None
            if name.startswith("mention-") or name in ALLOWED_HTML:
                return None
            if name == "callout":
                return 1, self.callout(attrs, [_Node(0, inner)]), None
            if name in UNWRAP_TAGS or name == "tab":
                return 1, [self.inline(inner)], None
            return None

        if name in {"callout", "details", "table", "tab"} | UNWRAP_TAGS | DROP_TAGS:
            consumed, content = self.gather(nodes, i, name)
            if name in DROP_TAGS:
                return consumed, [], None
            if name == "callout":
                return consumed, self.callout(attrs, content), None
            if name == "details":
                summary = ""
                if content and content[0].text.strip().startswith("<summary>"):
                    first = content.pop(0).text.strip()
                    summary = self.inline(first.removeprefix("<summary>").removesuffix("</summary>"))
                return consumed, self.details(summary, content), None
            if name == "table":
                return consumed, self.table(content), None
            if name == "tab":
                title = content.pop(0).text.strip() if content else ""
                icon = attrs.get("icon", "")
                icon = "" if icon.startswith("icons/") else icon
                heading = f"**{' '.join(p for p in (icon, self.inline(title)) if p)}**"
                body = self.blocks(content)
                return consumed, [heading, *([""] + body if body else [])], None
            return consumed, self.blocks(content), None
        return None

    def media(self, attrs: dict[str, str], caption: str) -> str:
        url = attrs.get("src") or attrs.get("url") or ""
        if _EXPIRING_RE.search(url):
            self.expiring = True
        label = self.inline(caption) or url.rsplit("/", 1)[-1].split("?", 1)[0] or "fil"
        return f"📎 [{label}]({url})" if _is_http(url) else f"📎 {label}"

    def callout(self, attrs: dict[str, str], content: list[_Node]) -> list[str]:
        icon = attrs.get("icon", "")
        if icon.startswith("icons/"):
            icon = ""
        inner = self.blocks(content)
        if icon:
            if inner and not re.match(r"^([#>|<`-]|\d+[.)]\s|```)", inner[0]):
                inner[0] = f"{icon} {inner[0]}"
            else:
                inner.insert(0, icon)
        return [f"> {line}" if line else ">" for line in inner]

    def details(self, summary: str, content: list[_Node]) -> list[str]:
        return ["<details>", f"<summary>{summary}</summary>", "", *self.blocks(content), "", "</details>"]

    def table(self, content: list[_Node]) -> list[str]:
        raw = "".join(_flatten(content))
        rows = [
            [
                self.inline(cell.strip()).replace("|", "\\|")
                for cell in re.findall(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", row, re.S)
            ]
            for row in re.findall(r"<tr\b[^>]*>(.*?)</tr>", raw, re.S)
        ]
        rows = [row for row in rows if row]
        if not rows:
            return []
        width = max(len(row) for row in rows)
        rows = [row + [""] * (width - len(row)) for row in rows]
        lines = ["| " + " | ".join(rows[0]) + " |", "|" + "|".join("---" for _ in range(width)) + "|"]
        lines += ["| " + " | ".join(row) + " |" for row in rows[1:]]
        return lines


def convert_markdown(text: str) -> MarkdownResult:
    """Convert Notion enhanced markdown to Home Assistant friendly markdown."""
    renderer = _Renderer()
    lines = renderer.blocks(_parse(text or ""))
    markdown = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return MarkdownResult(markdown, renderer.unknown, renderer.expiring)
