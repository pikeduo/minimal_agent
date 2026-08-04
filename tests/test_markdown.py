"""助手回复 Markdown 的安全渲染测试。"""

from minimal_agent.web.markdown import render_markdown


def test_markdown_renders_common_assistant_formatting() -> None:
    rendered = render_markdown(
        "你好！我可以帮助你：\n\n- **计算**\n- 搜索\n\n结果是 `2 + 3`，即 **5**。"
    )

    assert str(rendered) == (
        "<p>你好！我可以帮助你：</p>"
        "<ul><li><strong>计算</strong></li><li>搜索</li></ul>"
        "<p>结果是 <code>2 + 3</code>，即 <strong>5</strong>。</p>"
    )


def test_markdown_escapes_html_before_formatting() -> None:
    rendered = str(render_markdown("<script>alert(1)</script> **安全文本**"))

    assert "<script>" not in rendered
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in rendered
    assert "<strong>安全文本</strong>" in rendered
