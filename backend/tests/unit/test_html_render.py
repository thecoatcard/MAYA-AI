"""The pure HTML renderer: escaping, theme CSS, print CSS, Devanagari, preview."""

from __future__ import annotations

import re
from typing import Any

import pytest

from app.automation.html_render import (
    css_string,
    document_preview_html,
    render_document_html,
)
from app.automation.model import Document
from app.automation.themes import THEME_NAMES, get_theme
from tests.support.documents import HINGLISH, TINY_PNG, sample_document


def _doc(blocks: list[dict[str, Any]], **extra: Any) -> Document:
    return Document.model_validate({"title": "T", "blocks": blocks, **extra})


def test_output_is_one_self_contained_page_without_script_or_external_urls() -> None:
    html = render_document_html(sample_document())
    assert html.startswith("<!doctype html>")
    assert "<script" not in html.lower()
    assert "<link" not in html.lower()
    assert "@import" not in html
    # Only the user-provided link may contain an absolute URL.
    urls = re.findall(r"https?://[^\s\"')]+", html)
    assert set(urls) == {"https://example.com"}


def test_content_security_policy_forbids_script_and_network() -> None:
    html = render_document_html(sample_document())
    csp = re.search(r'http-equiv="Content-Security-Policy" content="([^"]+)"', html)
    assert csp is not None
    policy = csp.group(1)
    assert "default-src &#x27;none&#x27;" in policy
    assert "script-src" not in policy  # covered by default-src 'none'
    assert "img-src data:" in policy


def test_malicious_strings_are_escaped_everywhere() -> None:
    evil = '<script>alert("x")</script><img src=x onerror=alert(1)>'
    short = "<script>x</script>"
    doc = _doc(
        [
            {"type": "heading", "level": 1, "text": evil},
            {"type": "paragraph", "text": evil},
            {"type": "bullets", "items": [evil]},
            {"type": "numbered", "items": [evil]},
            {"type": "table", "headers": [evil], "rows": [[evil]], "caption": evil},
            {"type": "callout", "kind": "info", "text": evil},
            {"type": "metrics", "items": [{"value": short, "label": evil}]},
            {"type": "quote", "text": evil, "by": evil},
            {"type": "image", "src": TINY_PNG, "alt": evil, "caption": evil},
            {"type": "code", "language": "js", "text": evil},
        ],
        title=evil,
        subtitle=evil,
        author=evil,
        date=short,
    )
    html = render_document_html(doc)
    assert "<script" not in html.lower()
    assert "<img src=x" not in html
    assert "onerror=alert(1)>" not in html
    assert html.count("&lt;script&gt;") >= 12


def test_dangerous_link_is_not_rendered_as_anchor() -> None:
    html = render_document_html(
        _doc([{"type": "paragraph", "text": "[bad](javascript:alert) [good](https://ok.example)"}])
    )
    assert 'href="javascript' not in html
    assert 'href="https://ok.example"' in html


def test_devanagari_text_is_preserved_verbatim() -> None:
    html = render_document_html(sample_document())
    assert html.count(HINGLISH) >= 2  # subtitle + paragraph
    assert '<meta charset="utf-8">' in html


@pytest.mark.parametrize("name", THEME_NAMES)
def test_theme_colors_and_fonts_are_inlined_as_css(name: str) -> None:
    theme = get_theme(name)
    html = render_document_html(sample_document(name))
    assert f'data-theme="{name}"' in html
    assert f"--page-bg: {theme.colors.page_bg}" in html
    assert f"--accent: {theme.colors.accent}" in html
    assert theme.fonts.body_stack in html
    assert "Nirmala UI" in html


def test_themes_produce_different_css() -> None:
    pages = {name: render_document_html(sample_document(name)) for name in THEME_NAMES}
    assert len(set(pages.values())) == len(THEME_NAMES)


def test_print_css_has_page_size_margins_header_footer_and_page_numbers() -> None:
    html = render_document_html(sample_document())
    assert "size: A4" in html
    assert "@page {" in html and "margin: 20mm" in html
    assert "@top-left" in html and "@bottom-right" in html
    assert 'counter(page) " of " counter(pages)' in html
    assert '"Quarterly Operations Report"' in html  # running header carries the title
    assert "@page :first" in html


def test_print_css_keeps_blocks_together_and_repeats_table_headers() -> None:
    html = render_document_html(sample_document())
    assert "break-inside: avoid" in html
    assert "thead { display: table-header-group; }" in html
    for selector in ("table.keep {", ".callout {", "blockquote {", "figure {"):
        rule = html[html.index(selector) :].split("}")[0]
        assert "break-inside: avoid" in rule, selector
    # Code blocks were kept whole before; they now split across pages (no big gaps), with
    # orphan/widow control so a short block still stays together.
    pre_rule = html[html.index("pre {") :].split("}")[0]
    assert "break-inside: auto" in pre_rule and "orphans: 3" in pre_rule


def test_running_header_cannot_break_out_of_the_style_element() -> None:
    title = 'x"; } </style><script>alert(1)</script> \\'
    html = render_document_html(_doc([{"type": "paragraph", "text": "p"}], title=title))
    assert html.count("</style>") == 1
    assert "<script" not in html.lower()


def test_css_string_escapes_quotes_backslashes_angles_and_truncates() -> None:
    literal = css_string('a"b' + chr(92) + "c<d>e" + chr(10) + "f")
    assert (
        literal
        == '"a' + chr(92) + "22 b" + chr(92) + "5C c" + chr(92) + "3C d" + chr(92) + '3E e f"'
    )
    assert len(css_string("x" * 500)) < 100
    assert css_string("नमस्ते") == '"नमस्ते"'


def test_leading_trailing_and_repeated_page_breaks_are_dropped() -> None:
    blocks: list[dict[str, Any]] = [
        {"type": "page_break"},
        {"type": "paragraph", "text": "a"},
        {"type": "page_break"},
        {"type": "page_break"},
        {"type": "paragraph", "text": "b"},
        {"type": "page_break"},
    ]
    html = render_document_html(_doc(blocks))
    assert html.count('<div class="page-break"></div>') == 1


def test_unresolvable_image_path_renders_a_visible_placeholder() -> None:
    doc = _doc([{"type": "image", "src": "images/chart.png", "alt": "Revenue chart"}])
    assert "Image unavailable: Revenue chart" in render_document_html(doc)
    resolved = render_document_html(doc, resolve_image=lambda _p: TINY_PNG)
    assert f'<img src="{TINY_PNG}"' in resolved


def test_resolver_cannot_inject_a_non_data_uri() -> None:
    doc = _doc([{"type": "image", "src": "images/chart.png", "alt": "x"}])
    html = render_document_html(doc, resolve_image=lambda _p: "https://evil.example/x.png")
    assert "evil.example" not in html


def test_callout_kinds_and_metric_cards_are_rendered() -> None:
    html = render_document_html(sample_document())
    assert 'class="callout warning"' in html
    assert 'class="metric"' in html and ">98%<" in html


def test_preview_function_accepts_model_or_dict_and_is_pure() -> None:
    doc = sample_document("minimal")
    assert document_preview_html(doc) == render_document_html(doc)
    from_dict = document_preview_html(
        {"theme": "midnight", "title": "P", "blocks": [{"type": "paragraph", "text": "hi"}]}
    )
    assert 'data-theme="midnight"' in from_dict and "<p>hi</p>" in from_dict


def test_preview_function_validates_dicts() -> None:
    with pytest.raises(ValueError, match="theme"):
        document_preview_html(
            {"theme": "nope", "title": "P", "blocks": [{"type": "paragraph", "text": "x"}]}
        )
