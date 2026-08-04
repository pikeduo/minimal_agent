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
_TABLE_SEPARATOR_CELL_PATTERN = re.compile(r"^:?-{3,}:?$")


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

    lines = value.splitlines()
    line_index = 0
    while line_index < len(lines):
        line = lines[line_index]
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
            line_index += 1
            continue
        if code_lines is not None:
            code_lines.append(line)
            line_index += 1
            continue
        if not line.strip():
            flush_paragraph()
            flush_list()
            line_index += 1
            continue

        table = _parse_table(lines, line_index)
        if table is not None:
            header, rows, next_line_index = table
            flush_paragraph()
            flush_list()
            rendered_blocks.append(_render_table(header, rows))
            line_index = next_line_index
            continue

        heading = _HEADING_PATTERN.match(line)
        if heading is not None:
            flush_paragraph()
            flush_list()
            level = len(heading.group(1))
            rendered_blocks.append(f"<h{level}>{_render_inline(heading.group(2))}</h{level}>")
            line_index += 1
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
            line_index += 1
            continue

        flush_list()
        paragraph_lines.append(line)
        line_index += 1

    if code_lines is not None:
        rendered_blocks.append("<pre><code>" + escape("\n".join(code_lines)) + "</code></pre>")
    flush_paragraph()
    flush_list()
    return Markup("".join(rendered_blocks))


def _parse_table(
    lines: list[str],
    start_index: int,
) -> tuple[list[str], list[list[str]], int] | None:
    """识别 GitHub 风格表格，列数不一致时回退为普通文本。"""

    if start_index + 1 >= len(lines):
        return None
    header = _split_table_row(lines[start_index])
    separator = _split_table_row(lines[start_index + 1])
    if (
        header is None
        or separator is None
        or len(header) != len(separator)
        or not header
        or not all(_TABLE_SEPARATOR_CELL_PATTERN.fullmatch(cell) for cell in separator)
    ):
        return None

    rows: list[list[str]] = []
    row_index = start_index + 2
    while row_index < len(lines):
        row = _split_table_row(lines[row_index])
        if row is None or len(row) != len(header):
            break
        rows.append(row)
        row_index += 1
    return header, rows, row_index


def _split_table_row(line: str) -> list[str] | None:
    """分割不含转义竖线的最小表格行。"""

    if "|" not in line:
        return None
    stripped_line = line.strip()
    if stripped_line.startswith("|"):
        stripped_line = stripped_line[1:]
    if stripped_line.endswith("|"):
        stripped_line = stripped_line[:-1]
    cells = [cell.strip() for cell in stripped_line.split("|")]
    return cells if all(cells) else None


def _render_table(header: list[str], rows: list[list[str]]) -> str:
    """输出仅包含安全行内格式的语义化 HTML 表格。"""

    header_html = "".join(f"<th>{_render_inline(cell)}</th>" for cell in header)
    rows_html = "".join(
        "<tr>" + "".join(f"<td>{_render_inline(cell)}</td>" for cell in row) + "</tr>"
        for row in rows
    )
    return (
        '<div class="markdown-table-wrap"><table class="markdown-table">'
        f"<thead><tr>{header_html}</tr></thead>"
        f"<tbody>{rows_html}</tbody>"
        "</table></div>"
    )


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
