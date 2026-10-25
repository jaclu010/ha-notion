"""Tests for the Notion markdown converter."""

from custom_components.notion_workspace.markdown import convert_markdown


def md(text: str) -> str:
    return convert_markdown(text).markdown


def test_plain_paragraphs_and_headings() -> None:
    assert md('# Rubrik {color="blue"}\nFörsta stycket\nAndra stycket') == (
        "# Rubrik\n\nFörsta stycket\n\nAndra stycket"
    )


def test_callout_becomes_blockquote_with_icon() -> None:
    result = md('<callout icon="💡" color="gray_bg">\n\tTips här\n\t- punkt\n</callout>')
    assert result == "> 💡 Tips här\n>\n> - punkt"


def test_callout_notion_icon_is_dropped() -> None:
    assert md('<callout icon="icons/bell_gray">\n\tHej\n</callout>') == "> Hej"


def test_one_line_callout() -> None:
    assert md('<callout icon="⚠️">Varning</callout>') == "> ⚠️ Varning"


def test_details_with_unindented_children() -> None:
    result = md("<details>\n<summary>Mer info</summary>\nDolt innehåll\n</details>")
    assert result == "<details>\n<summary>Mer info</summary>\n\nDolt innehåll\n\n</details>"


def test_toggle_heading() -> None:
    result = md('## Fäll ut {toggle="true"}\n\tBarn')
    assert result == "<details>\n<summary><strong>Fäll ut</strong></summary>\n\nBarn\n\n</details>"


def test_nested_lists_keep_indentation() -> None:
    result = md("- ett\n\t- två\n\tstycke under ett\n- tre")
    assert result == "- ett\n  - två\n\n  stycke under ett\n- tre"


def test_numbered_list_child_indent() -> None:
    assert md("1. först\n\t- under") == "1. först\n   - under"


def test_todo_items() -> None:
    assert md("- [ ] gör\n- [x] klart") == "- [ ] gör\n- [x] klart"


def test_paragraph_children_are_dedented_not_code() -> None:
    assert md("Förälder\n\tBarn") == "Förälder\n\nBarn"


def test_columns_are_unwrapped() -> None:
    text = (
        "<columns>\n\t<column>\n\t\tVänster\n\t</column>\n"
        '\t<column ratio="50">\n\t\tHöger\n\t</column>\n</columns>'
    )
    assert md(text) == "Vänster\n\nHöger"


def test_table_to_gfm() -> None:
    text = (
        '<table header-row="true">\n\t<tr>\n\t\t<td>A</td>\n\t\t<td>B</td>\n\t</tr>\n'
        '\t<tr color="red">\n\t\t<td>1 | 2</td>\n\t\t<td color="blue">**3**</td>\n\t</tr>\n</table>'
    )
    assert md(text) == "| A | B |\n|---|---|\n| 1 \\| 2 | **3** |"


def test_pipe_table_lines_stay_together() -> None:
    assert md("| a | b |\n|---|---|\n| 1 | 2 |") == "| a | b |\n|---|---|\n| 1 | 2 |"


def test_page_database_and_media_links() -> None:
    text = (
        '<page url="https://www.notion.so/abc">Undersida</page>\n'
        '<database url="https://www.notion.so/def" inline="true">Uppgifter</database>\n'
        '<file src="https://example.com/a.pdf">Avtal</file>\n'
        '<video src="https://example.com/v.mp4"/>'
    )
    assert md(text) == (
        "📄 [Undersida](https://www.notion.so/abc)\n\n"
        "🗂️ [Uppgifter](https://www.notion.so/def)\n\n"
        "📎 [Avtal](https://example.com/a.pdf)\n\n"
        "📎 [v.mp4](https://example.com/v.mp4)"
    )


def test_inline_mentions_spans_and_dates() -> None:
    text = (
        'Se <mention-page url="https://www.notion.so/x">Sidan</mention-page>, '
        '<span color="red">rött</span> och <span underline="true">understruket</span> '
        'av <mention-user url="user://123">Jacob</mention-user> '
        '<mention-date start="2026-10-11" startTime="14:00" timeZone="Europe/Stockholm"/>'
    )
    assert md(text) == (
        "Se [Sidan](https://www.notion.so/x), rött och understruket av @Jacob 2026-10-11 14:00"
    )


def test_unknown_blocks_are_dropped_and_reported() -> None:
    result = convert_markdown(
        'Före\n<unknown url="https://x" alt="link_preview"/>\n<table_of_contents/>\n<weird>Text</weird>\nEfter'
    )
    assert result.markdown == "Före\n\nText\n\nEfter"
    assert result.unknown_tags == {"unknown(link_preview)", "weird"}


def test_empty_block() -> None:
    assert md("A\n<empty-block/>\nB") == "A\n\n&nbsp;\n\nB"


def test_code_block_is_verbatim() -> None:
    text = "- lista\n\t```python\n\tif x:\n\t\t<callout>\n\t```"
    assert md(text) == "- lista\n  ```python\n  if x:\n  \t<callout>\n  ```"


def test_equation_block() -> None:
    assert md("$$\nE = mc^2\n$$") == "```\nE = mc^2\n```"


def test_expiring_file_detection() -> None:
    result = convert_markdown(
        "![bild](https://prod-files-secure.s3.us-west-2.amazonaws.com/a.png?X-Amz-Expires=3600)"
    )
    assert result.has_expiring_files
    assert not convert_markdown("![bild](https://example.com/a.png)").has_expiring_files


def test_tabs_become_bold_titles() -> None:
    text = '<tabs>\n\t<tab icon="🍎">\n\t\tFrukt\n\t\tÄpple\n\t</tab>\n</tabs>'
    assert md(text) == "**🍎 Frukt**\n\nÄpple"


def test_meeting_notes_drop_transcript() -> None:
    text = (
        "<meeting-notes>\n\tMöte\n\t<summary>\n\t\tSammanfattning\n\t</summary>\n"
        "\t<transcript>\n\t\tHemligt\n\t</transcript>\n</meeting-notes>"
    )
    assert md(text) == "Möte\n\nSammanfattning"


def test_quote_with_children() -> None:
    assert md("> Citat\n\tFortsättning") == "> Citat\n> Fortsättning"
