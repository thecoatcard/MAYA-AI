"""Render a :class:`Document` to one self-contained HTML string (inline CSS, no scripts).

This is a pure function: no I/O, no browser, no network. The same HTML is printed to PDF by the
browser renderer and can be shown as a UI preview. All dynamic text goes through
``html.escape``; the only attribute values built from content are escaped ``href``/``src``
values that already passed the allow-lists in ``inline`` and ``model``. A Content-Security-Policy
meta tag forbids scripts and any network fetch even if a mistake ever let markup through.
"""

from __future__ import annotations

import html
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from app.automation.design import fit_theme
from app.automation.inline import Span, parse_inline
from app.automation.model import (
    BulletsBlock,
    CalloutBlock,
    CodeBlock,
    Document,
    HeadingBlock,
    ImageBlock,
    MetricsBlock,
    NumberedBlock,
    PageBreakBlock,
    ParagraphBlock,
    QuoteBlock,
    TableBlock,
    document_from_dict,
)
from app.automation.themes import Theme, compact_sizes, themed

ImageResolver = Callable[[str], str | None]
"""Maps a workspace-relative image path to a ``data:`` URI, or ``None`` if unavailable."""

CSP = (
    "default-src 'none'; img-src data:; style-src 'unsafe-inline'; "
    "base-uri 'none'; form-action 'none'"
)
_MAX_HEADER_CHARS = 90
# A table of at most this many rows stays on one page; a longer one flows and repeats its header
# row. Kept small: a big table that must stay whole leaves a large gap and adds pages.
MAX_KEEP_TOGETHER_ROWS = 6
# Chapter layout rule (also used by the DOCX engine): every level-1 heading starts a new page when
# the document has at least this many level-1 sections AND is at least this many pages long. A
# 1-3 page report therefore never gets a page break before each h1.
CHAPTER_MIN_H1 = 3
CHAPTER_MIN_PAGES = 6
TOC_MAX_LEVEL = 2
_CALLOUT_LABEL = {"info": "Note", "success": "Tip", "warning": "Warning"}


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def css_string(value: str, max_chars: int = _MAX_HEADER_CHARS) -> str:
    """A safe CSS string literal (with quotes) for use in a ``content:`` declaration.

    Backslash, quote, angle brackets and control characters are hex-escaped so the text can
    never close the string, the rule or the surrounding ``<style>`` element.
    """
    text = " ".join(value.split())
    if len(text) > max_chars:
        text = text[: max_chars - 1].rstrip() + "…"
    out: list[str] = []
    for ch in text:
        if ch in "\\\"<>&'" or ord(ch) < 0x20 or ch in "\u2028\u2029":
            out.append(f"\\{ord(ch):X} ")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def spans_to_html(spans: list[Span]) -> str:
    parts: list[str] = []
    for span in spans:
        piece = esc(span.text)
        if span.code:
            piece = f"<code>{piece}</code>"
        if span.bold:
            piece = f"<strong>{piece}</strong>"
        if span.italic:
            piece = f"<em>{piece}</em>"
        if span.href:
            piece = f'<a href="{esc(span.href)}" rel="noopener noreferrer">{piece}</a>'
        parts.append(piece)
    return "".join(parts)


def inline_html(text: str) -> str:
    """Inline markup (bold/italic/code/links) to escaped HTML; newlines become ``<br>``."""
    return spans_to_html(parse_inline(text)).replace("\n", "<br>")


def h1_count(document: Document) -> int:
    return sum(1 for b in document.blocks if isinstance(b, HeadingBlock) and b.level == 1)


def wants_chapter_breaks(document: Document, pages: int) -> bool:
    """Whether level-1 headings start a new page: >= 3 h1 sections and >= 6 pages (measured by a
    first print without the breaks, so the rule is about the document's own length)."""
    return h1_count(document) >= CHAPTER_MIN_H1 and pages >= CHAPTER_MIN_PAGES


def toc_entries(document: Document) -> list[tuple[int, int, str]]:
    """``(block index, level, text)`` of the headings the contents list shows (levels 1 and 2)."""
    return [
        (index, block.level, block.text)
        for index, block in enumerate(document.blocks)
        if isinstance(block, HeadingBlock) and block.level <= TOC_MAX_LEVEL
    ]


def _toc_html(document: Document, toc_pages: dict[int, int] | None) -> str:
    """The contents list. The page-number column is always reserved so adding the numbers in a
    second pass cannot change the layout; an entry without a measured page shows no number."""
    items: list[str] = []
    for index, level, text in toc_entries(document):
        number = (toc_pages or {}).get(index)
        shown = str(number) if number is not None else ""
        items.append(
            f'<li class="lv{level}"><a href="#h{index}"><span class="t">{inline_html(text)}</span>'
            f'<span class="dots"></span><span class="n">{esc(shown)}</span></a></li>'
        )
    return f'<nav class="toc"><div class="toc-title">Contents</div><ol>{"".join(items)}</ol></nav>'


def _css(
    theme: Theme,
    title: str,
    *,
    cover_page: bool = False,
    chapter_breaks: bool = False,
    footer_label: str = "MAYA AI",
) -> str:
    c, f, s = theme.colors, theme.fonts, theme.sizes
    # Vertical rhythm of lists, tables, callouts and code scales with the theme's block gap (9 pt
    # is 1.0), so a dense theme (notes) is dense everywhere, not only between paragraphs.
    d = s.block_gap_pt / 9.0
    li_gap, cell_pad = round(3 * d, 2), round(5 * d, 2)
    callout_pad, pre_pad = round(8 * d, 2), round(9 * d, 2)
    header = css_string(title)
    footer = css_string(footer_label, 40)
    # Each chapter (h1) starts a fresh page; its top margin is dropped because it is at the page
    # top. The last page of a chapter may be short. An h1 that already follows a page break (the
    # book form) is marked "after-break" so two breaks never stack.
    chapter_css = (
        "h1.h { break-before: page; margin-top: 0; }\nh1.h.after-break { break-before: auto; }"
        if chapter_breaks
        else ""
    )
    first_page = (
        "@page :first { @top-left { content: none; } @bottom-left { content: none; } "
        "@bottom-right { content: none; } }"
        if cover_page
        else "@page :first { @top-left { content: none; } }"
    )
    return f"""
:root {{
  --page-bg: {c.page_bg}; --text: {c.text}; --muted: {c.muted}; --heading: {c.heading};
  --primary: {c.primary}; --accent: {c.accent}; --border: {c.border}; --surface: {c.surface};
  --th-bg: {c.table_header_bg}; --th-text: {c.table_header_text}; --stripe: {c.table_stripe};
  --code-bg: {c.code_bg}; --code-text: {c.code_text}; --link: {c.link};
}}
@page {{
  size: A4;
  margin: {s.page_margin_mm}mm {s.page_margin_mm - 2}mm {s.page_margin_mm + 2}mm;
  @top-left {{ content: {header}; font: 8pt {f.body_stack}; color: {c.muted}; }}
  @bottom-left {{ content: {footer}; font: 8pt {f.body_stack}; color: {c.muted}; }}
  @bottom-right {{
    content: "Page " counter(page) " of " counter(pages);
    font: 8pt {f.body_stack}; color: {c.muted};
  }}
}}
{first_page}
* {{ box-sizing: border-box; }}
html {{ background: var(--page-bg); -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
body {{
  margin: 0; background: var(--page-bg); color: var(--text);
  font-family: {f.body_stack}; font-size: {s.body_pt}pt; line-height: {s.line_height};
  overflow-wrap: anywhere; word-break: normal;
}}
main.doc {{ max-width: 100%; }}
header.cover {{ margin: 0 0 {s.block_gap_pt * 2}pt; padding-bottom: 12pt;
  border-bottom: 2.5pt solid var(--accent); }}
header.cover .brand {{ font-size: {s.small_pt}pt; letter-spacing: 0; text-transform: uppercase;
  color: var(--accent); font-weight: 700; margin-bottom: 8pt; }}
header.cover h1.title {{ font-family: {f.heading_stack}; font-size: {s.title_pt}pt;
  line-height: 1.15;
  margin: 0 0 6pt; color: var(--heading); font-weight: 700; }}
header.cover .subtitle {{ font-size: {s.subtitle_pt}pt; color: var(--muted); margin: 0 0 6pt; }}
header.cover .meta {{ font-size: {s.small_pt + 0.5}pt; color: var(--muted); }}
h1, h2, h3 {{ font-family: {f.heading_stack}; color: var(--heading); break-after: avoid;
  line-height: 1.25; margin: {s.block_gap_pt * 1.8}pt 0 {s.block_gap_pt * 0.6}pt; }}
h1.h {{ font-size: {s.h1_pt}pt; padding-bottom: 4pt; border-bottom: 0.75pt solid var(--border); }}
h2.h {{ font-size: {s.h2_pt}pt; }}
h3.h {{ font-size: {s.h3_pt}pt; color: var(--primary); }}
p {{ margin: 0 0 {s.block_gap_pt}pt; orphans: 3; widows: 3; }}
a {{ color: var(--link); text-decoration: underline; }}
strong {{ font-weight: 700; }}
code {{ font-family: {f.mono_stack}; font-size: 0.92em; background: var(--code-bg);
  padding: 0 3pt; line-height: 1; border-radius: 2pt; color: var(--code-text);
  -webkit-box-decoration-break: clone; box-decoration-break: clone; }}
ul, ol {{ margin: 0 0 {s.block_gap_pt}pt; padding-left: 18pt; list-style-position: outside;
  orphans: 2; widows: 2; }}
li {{ margin: 0 0 {li_gap}pt; padding-left: 2pt; }}
li::marker {{ color: var(--accent); }}
table {{ width: 100%; border-collapse: collapse; margin: 0 0 {s.block_gap_pt}pt;
  font-size: {s.body_pt - 0.5}pt; }}
table.keep {{ break-inside: avoid; }}
caption {{ caption-side: bottom; text-align: left; font-size: {s.small_pt}pt; color: var(--muted);
  padding-top: 4pt; }}
thead {{ display: table-header-group; }}
tr {{ break-inside: avoid; }}
th {{ background: var(--th-bg); color: var(--th-text); text-align: left; font-weight: 700;
  padding: {cell_pad}pt 7pt; border: 0.5pt solid var(--th-bg); }}
td {{ padding: {cell_pad}pt 7pt; border: 0.5pt solid var(--border); vertical-align: top; }}
tbody tr:nth-child(even) td {{ background: var(--stripe); }}
.callout {{ break-inside: avoid; margin: 0 0 {s.block_gap_pt}pt; padding: {callout_pad}pt 11pt;
  border-left: 3.5pt solid; border-radius: 3pt; }}
.callout {{ orphans: 3; widows: 3; }}
.callout .label {{ display: block; font-size: {s.small_pt}pt; font-weight: 700;
  text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 2pt; }}
.callout {{ color: var(--text); }}
.callout.info {{ background: {c.info_bg}; border-color: {c.info_fg}; }}
.callout.warning {{ background: {c.warning_bg}; border-color: {c.warning_fg}; }}
.callout.success {{ background: {c.success_bg}; border-color: {c.success_fg}; }}
.callout.info .label {{ color: {c.info_fg}; }}
.callout.warning .label {{ color: {c.warning_fg}; }}
.callout.success .label {{ color: {c.success_fg}; }}
.metrics {{ display: grid;
  grid-template-columns: repeat(auto-fit, minmax(110pt, 1fr));
  gap: 8pt; margin: 0 0 {s.block_gap_pt}pt; break-inside: avoid; }}
.metric {{ background: var(--surface); border: 0.5pt solid var(--border);
  border-top: 3pt solid var(--accent);
  border-radius: 4pt; padding: 9pt 10pt; }}
.metric .value {{ font-family: {f.heading_stack}; font-size: {s.h1_pt + 3}pt; font-weight: 700;
  color: var(--heading); line-height: 1.1; }}
.metric .label {{ font-size: {s.small_pt + 0.5}pt; color: var(--muted); margin-top: 3pt; }}
blockquote {{ break-inside: avoid; margin: 0 0 {s.block_gap_pt}pt; padding: 4pt 0 4pt 12pt;
  border-left: 3pt solid var(--accent); font-family: {f.heading_stack}; font-style: italic;
  font-size: {s.body_pt + 1.5}pt; color: var(--heading); }}
blockquote .by {{ display: block; margin-top: 4pt; font-family: {f.body_stack}; font-style: normal;
  font-size: {s.small_pt + 0.5}pt; color: var(--muted); }}
figure {{ break-inside: avoid; margin: 0 0 {s.block_gap_pt}pt; text-align: center; }}
figure img {{ max-width: 100%; max-height: 220mm; height: auto; border-radius: 3pt; }}
figcaption {{ font-size: {s.small_pt}pt; color: var(--muted); margin-top: 4pt; }}
.img-missing {{ border: 0.75pt dashed var(--border); padding: 14pt; color: var(--muted);
  font-size: {s.small_pt + 0.5}pt; text-align: center; }}
pre {{ break-inside: auto; orphans: 3; widows: 3; margin: 0 0 {s.block_gap_pt}pt;
  padding: {pre_pad}pt 11pt; background: var(--code-bg);
  border: 0.5pt solid var(--border); border-radius: 3pt; white-space: pre-wrap;
  overflow-wrap: anywhere; }}
pre code {{ background: none; padding: 0; font-size: {s.code_pt}pt; line-height: 1.45; }}
.code-lang {{ break-after: avoid; font-size: {s.small_pt - 0.5}pt; text-transform: uppercase;
  letter-spacing: 0.1em; color: var(--muted); margin-bottom: 3pt; }}
.page-break {{ break-after: page; height: 0; }}
header.cover.page {{ min-height: 225mm; display: flex; flex-direction: column;
  justify-content: center; break-after: page; border-bottom: none; padding-bottom: 0;
  border-left: 5pt solid var(--accent); padding-left: 18pt; }}
header.cover.page h1.title {{ font-size: {s.title_pt + 10}pt; }}
header.cover.page .subtitle {{ font-size: {s.subtitle_pt + 3}pt; margin-top: 8pt; }}
header.cover.page .meta {{ margin-top: 28pt; }}
nav.toc {{ {"break-after: page;" if (cover_page or chapter_breaks) else "margin: 0 0 16pt;"} }}
{chapter_css}
nav.toc .toc-title {{ font-family: {f.heading_stack}; font-size: {s.h1_pt}pt; font-weight: 700;
  color: var(--heading); border-bottom: 0.75pt solid var(--border); padding-bottom: 4pt;
  margin: 0 0 {s.block_gap_pt * 1.5}pt; }}
nav.toc ol {{ list-style: none; margin: 0; padding: 0; }}
nav.toc li {{ margin: 0; padding: 0; break-inside: avoid; }}
nav.toc li a {{ display: flex; align-items: baseline; color: var(--text); text-decoration: none; }}
nav.toc li.lv1 {{ margin-top: {s.block_gap_pt * 0.8}pt; font-weight: 700; color: var(--heading); }}
nav.toc li.lv1 a {{ color: var(--heading); }}
nav.toc li.lv2 {{ padding-left: 14pt; font-size: {s.body_pt - 0.5}pt; }}
nav.toc .dots {{ flex: 1; border-bottom: 1pt dotted var(--border); margin: 0 5pt 3pt; }}
nav.toc .n {{ min-width: 2.4em; text-align: right; font-variant-numeric: tabular-nums; }}
{_style_css(theme, cover_page)}
"""


def _style_css(theme: Theme, cover_page: bool) -> str:
    """Treatments a designed theme chose (cover, headings, tables, callouts). The presets use the
    defaults, which add nothing, so their print is unchanged."""
    st, c = theme.style, theme.colors
    rules: list[str] = []
    if st.cover == "plain":
        rules.append("header.cover { border-bottom: none; }")
        rules.append("header.cover.page { border-left: none; padding-left: 0; }")
    elif st.cover == "bold":
        rules.append(
            "header.cover { background: var(--primary); padding: 16pt 18pt; border: none; "
            f"border-radius: 4pt; }} header.cover .brand, header.cover .meta, "
            f"header.cover .subtitle {{ color: {c.slide_title_text}; }} "
            f"header.cover h1.title {{ color: {c.slide_title_text}; }}"
        )
        rules.append("header.cover.page { border-left: none; padding: 40pt 30pt; }")
    elif st.cover == "sidebar":
        rules.append(
            "header.cover { border-bottom: none; border-left: 8pt solid var(--accent); "
            "padding-left: 14pt; }"
        )
    if st.heading == "rule":
        rules.append("h2.h { padding-bottom: 3pt; border-bottom: 0.75pt solid var(--accent); }")
    elif st.heading == "bar":
        rules.append(
            "h1.h, h2.h { border-left: 5pt solid var(--accent); padding-left: 8pt; "
            "border-bottom: none; }"
        )
    elif st.heading == "caps":
        rules.append("h1.h, h2.h { text-transform: uppercase; letter-spacing: 0.06em; }")
    if st.table == "lined":
        rules.append(
            "th { background: transparent; color: var(--heading); border: none; "
            "border-bottom: 1.5pt solid var(--primary); } "
            "td { border: none; border-bottom: 0.5pt solid var(--border); } "
            "tbody tr:nth-child(even) td { background: transparent; }"
        )
    elif st.table == "striped":
        rules.append(
            "td { border: none; } tbody tr:nth-child(even) td { background: var(--stripe); }"
        )
    elif st.table == "minimal":
        rules.append(
            "th { background: transparent; color: var(--text); border: none; "
            "border-bottom: 1pt solid var(--text); } td { border: none; } "
            "tbody tr:nth-child(even) td { background: transparent; }"
        )
    if st.callout == "outlined":
        rules.append(
            ".callout { background: transparent !important; border: 1pt solid; "
            "border-left-width: 1pt; }"
        )
    elif st.callout == "bar":
        rules.append(".callout { background: transparent !important; border-radius: 0; }")
    return "\n".join(rules)


def _block_html(
    block: object,
    resolve_image: ImageResolver | None,
    index: int = 0,
    anchors: bool = False,
    after_break: bool = False,
) -> str:
    if isinstance(block, HeadingBlock):
        anchor = f' id="h{index}"' if anchors else ""
        cls = "h after-break" if after_break and block.level == 1 else "h"
        return f'<h{block.level} class="{cls}"{anchor}>{inline_html(block.text)}</h{block.level}>'
    if isinstance(block, ParagraphBlock):
        return f"<p>{inline_html(block.text)}</p>"
    if isinstance(block, BulletsBlock | NumberedBlock):
        tag = "ul" if isinstance(block, BulletsBlock) else "ol"
        items = "".join(f"<li>{inline_html(i)}</li>" for i in block.items)
        return f"<{tag}>{items}</{tag}>"
    if isinstance(block, TableBlock):
        head = "".join(f'<th scope="col">{inline_html(h)}</th>' for h in block.headers)
        body = "".join(
            "<tr>" + "".join(f"<td>{inline_html(c)}</td>" for c in row) + "</tr>"
            for row in block.rows
        )
        caption = f"<caption>{inline_html(block.caption)}</caption>" if block.caption else ""
        # Short tables stay on one page; long ones flow and repeat their header row.
        keep = ' class="keep"' if len(block.rows) <= MAX_KEEP_TOGETHER_ROWS else ""
        return f"<table{keep}>{caption}<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"
    if isinstance(block, CalloutBlock):
        return (
            f'<div class="callout {block.kind}">'
            f'<span class="label">{esc(_CALLOUT_LABEL.get(block.kind, block.kind))}</span>'
            f"{inline_html(block.text)}</div>"
        )
    if isinstance(block, MetricsBlock):
        cells = "".join(
            f'<div class="metric"><div class="value">{inline_html(m.value)}</div>'
            f'<div class="label">{inline_html(m.label)}</div></div>'
            for m in block.items
        )
        return f'<div class="metrics">{cells}</div>'
    if isinstance(block, QuoteBlock):
        by = f'<span class="by">— {inline_html(block.by)}</span>' if block.by else ""
        return f"<blockquote>{inline_html(block.text)}{by}</blockquote>"
    if isinstance(block, ImageBlock):
        src = (
            block.src
            if block.src.startswith("data:")
            else (resolve_image(block.src) if resolve_image else None)
        )
        caption = f"<figcaption>{inline_html(block.caption)}</figcaption>" if block.caption else ""
        if not src or not src.startswith("data:image/"):
            return (
                f'<figure><div class="img-missing">Image unavailable: {esc(block.alt)}</div>'
                f"{caption}</figure>"
            )
        return f'<figure><img src="{esc(src)}" alt="{esc(block.alt)}">{caption}</figure>'
    if isinstance(block, CodeBlock):
        label = f'<div class="code-lang">{esc(block.language)}</div>' if block.language else ""
        return f"<pre>{label}<code>{esc(block.text)}</code></pre>"
    if isinstance(block, PageBreakBlock):
        return '<div class="page-break"></div>'
    raise TypeError(f"Unsupported block type: {type(block).__name__}")


def _trim_page_breaks(blocks: list[Any]) -> list[Any]:
    """Drop leading, trailing and repeated page breaks (they would print blank pages)."""
    out: list[Any] = []
    for block in blocks:
        if isinstance(block, PageBreakBlock) and (not out or isinstance(out[-1], PageBreakBlock)):
            continue
        out.append(block)
    while out and isinstance(out[-1], PageBreakBlock):
        out.pop()
    return out


def render_document_html(
    document: Document,
    *,
    resolve_image: ImageResolver | None = None,
    toc_pages: dict[int, int] | None = None,
    chapter_breaks: bool = False,
) -> str:
    """The complete HTML page for ``document``, styled by its theme.

    ``chapter_breaks`` starts every level-1 heading on a new page (decided by the caller with
    :func:`wants_chapter_breaks`; the contents list then also gets a page of its own).

    ``toc_pages`` maps a heading's block index to its measured page number (see
    :func:`toc_entries`); it only matters for a document with ``toc`` set.
    """
    theme = themed(document.theme, document.accent_color, document.design)
    if document.density == "compact":
        theme = replace(theme, sizes=compact_sizes(theme.sizes))
    theme = fit_theme(theme, document.fit_step)
    meta_parts = [p for p in (document.author, document.date) if p]
    meta = f'<div class="meta">{" · ".join(esc(p) for p in meta_parts)}</div>' if meta_parts else ""
    subtitle = (
        f'<div class="subtitle">{inline_html(document.subtitle)}</div>' if document.subtitle else ""
    )
    cover_class = "cover page" if document.cover_page else "cover"
    cover = (
        f'<header class="{cover_class}"><div class="brand">MAYA AI</div>'
        f'<h1 class="title">{esc(document.title)}</h1>{subtitle}{meta}</header>'
    )
    toc = _toc_html(document, toc_pages) if document.toc else ""
    # Heading anchors and the contents list use positions in ``document.blocks``, so the blocks
    # that print are chosen by position instead of being removed from the list first.
    kept = {id(b) for b in _trim_page_breaks(document.blocks)}
    parts: list[str] = []
    previous: object | None = None
    for index, b in enumerate(document.blocks):
        if id(b) not in kept:
            continue
        after_break = isinstance(previous, PageBreakBlock)
        parts.append(_block_html(b, resolve_image, index, document.toc, after_break))
        previous = b
    body = "\n".join(parts)
    footer_label = (document.author or "MAYA AI").strip() or "MAYA AI"
    css = _css(
        theme,
        document.title,
        cover_page=document.cover_page,
        chapter_breaks=chapter_breaks,
        footer_label=footer_label,
    )
    return (
        "<!doctype html>\n"
        f'<html lang="en" data-theme="{esc(theme.name)}"><head><meta charset="utf-8">'
        f'<meta http-equiv="Content-Security-Policy" content="{esc(CSP)}">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{esc(document.title)}</title>"
        f"<style>{css}</style></head>"
        f'<body><main class="doc">{cover}\n{toc}\n{body}</main></body></html>'
    )


def document_preview_html(
    document: Document | dict[str, Any],
    *,
    resolve_image: ImageResolver | None = None,
) -> str:
    """HTML preview for a document or a plain dict (validated first). Pure; no I/O.

    Intended for a later UI preview: the returned string is a full page that is safe to show in
    a sandboxed iframe (``srcdoc``) because it contains no script and a deny-by-default CSP.
    """
    doc = document if isinstance(document, Document) else document_from_dict(document)
    return render_document_html(doc, resolve_image=resolve_image)
