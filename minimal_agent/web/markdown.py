"""将模型回复中的常用 Markdown 安全渲染为 HTML。"""

from __future__ import annotations

from html import escape
import re

from markupsafe import Markup


_UNORDERED_LIST_PATTERN = re.compile(r"^[-*+]\s+(.+)$")
_ORDERED_LIST_PATTERN = re.compile(r"^\d+[.)]\s+(.+)$")
_HEADING_PATTERN = re.compile(r"^(#{1,3})\s+(.+)$")
_STRONG_PATTERN = re.compile(r"\*\*(.+?)\*\*")
_EMPHASIS_PATTERN = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)")
_CODE_PATTERN = re.compile(r"`([^`\n]+)`")


def render_markdown(value: object) -> Markup:
    """渲染受限 Markdown，并在任何格式化前转义原始 HTML。"""

    if not isinstance(value, str) or not value:
        return Markup("")

    rendered_blocks: list[str] = []
    paragraph_lines: list[str] = []
    list_items: list[str] = []
    list_tag: str | None = None
    code_lines: list[str] | None = None

    def flush_paragraph() -> None:
        if paragraph_lines:
            rendered_blocks.append(
                "<p>" + "<br>".join(_render_inline(line) for line in paragraph_lines) + "</p>"
            )
            paragraph_lines.clear()

    def flush_list() -> None:
        nonlocal list_tag
        if list_items and list_tag is not None:
            rendered_blocks.append(
                f"<{list_tag}>"
                + "".join(f"<li>{_render_inline(item)}</li>" for item in list_items)
                + f"</{list_tag}>"
            )
            list_items.clear()
            list_tag = None

    for line in value.splitlines():
        if line.strip().startswith("```"):
            flush_paragraph()
            flush_list()
            if code_lines is None:
                code_lines = []
            else:
                rendered_blocks.append(
                    "<pre><code>" + escape("\n".join(code_lines)) + "</code></pre>"
                )
                code_lines = None
            continue
        if code_lines is not None:
            code_lines.append(line)
            continue
        if not line.strip():
            flush_paragraph()
            flush_list()
            continue

        heading = _HEADING_PATTERN.match(line)
        if heading is not None:
            flush_paragraph()
            flush_list()
            level = len(heading.group(1))
            rendered_blocks.append(f"<h{level}>{_render_inline(heading.group(2))}</h{level}>")
            continue

        unordered_item = _UNORDERED_LIST_PATTERN.match(line)
        ordered_item = _ORDERED_LIST_PATTERN.match(line)
        if unordered_item is not None or ordered_item is not None:
            item = (unordered_item or ordered_item).group(1)
            next_list_tag = "ul" if unordered_item is not None else "ol"
            flush_paragraph()
            if list_tag is not None and list_tag != next_list_tag:
                flush_list()
            list_tag = next_list_tag
            list_items.append(item)
            continue

        flush_list()
        paragraph_lines.append(line)

    if code_lines is not None:
        rendered_blocks.append("<pre><code>" + escape("\n".join(code_lines)) + "</code></pre>")
    flush_paragraph()
    flush_list()
    return Markup("".join(rendered_blocks))


def _render_inline(value: str) -> str:
    """只处理常用行内格式，其他内容按转义后的原文展示。"""

    escaped_value = escape(value)
    code_segments: list[str] = []

    def stash_code(match: re.Match[str]) -> str:
        code_segments.append(f"<code>{match.group(1)}</code>")
        return f"\x00{len(code_segments) - 1}\x00"

    rendered_value = _CODE_PATTERN.sub(stash_code, escaped_value)
    rendered_value = _STRONG_PATTERN.sub(r"<strong>\1</strong>", rendered_value)
    rendered_value = _EMPHASIS_PATTERN.sub(r"<em>\1</em>", rendered_value)
    for index, code_segment in enumerate(code_segments):
        rendered_value = rendered_value.replace(f"\x00{index}\x00", code_segment)
    return rendered_value
