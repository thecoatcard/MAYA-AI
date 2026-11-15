"""The safe inline markup parser."""

from __future__ import annotations

import pytest

from app.automation.html_render import inline_html
from app.automation.inline import Span, parse_inline, plain_text, safe_href, xml_safe


def test_plain_text_is_one_span() -> None:
    assert parse_inline("hello world") == [Span("hello world")]


def test_bold_italic_code_and_link() -> None:
    spans = parse_inline("a **b** *c* `d` [e](https://example.com/x?y=1)")
    assert Span("b", bold=True) in spans
    assert Span("c", italic=True) in spans
    assert Span("d", code=True) in spans
    assert Span("e", href="https://example.com/x?y=1") in spans


def test_nested_bold_italic() -> None:
    spans = parse_inline("***both***")
    assert any(s.bold and s.italic and s.text == "both" for s in spans)
    spans = parse_inline("**bold *and italic* end**")
    assert any(s.bold and s.italic and s.text == "and italic" for s in spans)


def test_link_text_can_be_bold() -> None:
    spans = parse_inline("[**go**](https://example.com)")
    assert spans == [Span("go", bold=True, href="https://example.com")]


def test_unmatched_markers_stay_literal() -> None:
    assert plain_text("2 * 3 = 6 and **open") == "2 * 3 = 6 and **open"
    assert plain_text("snake_case and a*b") == "snake_case and a*b"


def test_code_span_protects_markers() -> None:
    assert parse_inline("`**not bold**`") == [Span("**not bold**", code=True)]


def test_script_tag_is_escaped_not_executed() -> None:
    out = inline_html("<script>alert(1)</script> **x**")
    assert "<script>" not in out
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in out
    assert "<strong>x</strong>" in out


def test_attribute_injection_in_link_text_and_url_is_escaped() -> None:
    out = inline_html('[<img src=x onerror=alert(1)>](https://e.com/"onmouseover="x)')
    assert "<img" not in out
    assert 'onmouseover="x' not in out


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "JaVaScRiPt:alert(1)",
        "  javascript:alert(1)",
        "data:text/html;base64,PHNjcmlwdD4=",
        "vbscript:x",
        "file:///etc/passwd",
        "ftp://example.com",
        "//example.com",
        "/relative",
        "java\nscript:alert(1)",
        "",
    ],
)
def test_dangerous_or_unknown_link_targets_are_blocked(url: str) -> None:
    assert safe_href(url) is None


@pytest.mark.parametrize(
    "url", ["https://example.com", "http://example.com/a?b=c#d", "mailto:a@example.com"]
)
def test_allowed_link_targets(url: str) -> None:
    assert safe_href(url) == url


def test_javascript_link_degrades_to_text_without_anchor() -> None:
    out = inline_html("[click](javascript:alert)")
    assert "<a " not in out
    assert out == "click"
    # A target containing parentheses is not even recognised as a link: still no anchor.
    assert "<a " not in inline_html("[click](javascript:alert(1))")


def test_link_renders_with_safe_rel() -> None:
    out = inline_html("[ok](https://example.com)")
    assert '<a href="https://example.com" rel="noopener noreferrer">ok</a>' in out


def test_newlines_become_line_breaks_and_unicode_is_kept() -> None:
    out = inline_html("नमस्ते MAYA\nline two")
    assert "नमस्ते MAYA<br>line two" in out


def test_pathological_input_is_handled_quickly() -> None:
    nasty = "**" * 2000 + "*" * 2000 + "[" * 1000 + "`" * 1000
    assert isinstance(parse_inline(nasty), list)


def test_xml_safe_removes_control_characters() -> None:
    assert xml_safe("a" + chr(0) + "b" + chr(11) + "c\td\n") == "abc\td\n"
