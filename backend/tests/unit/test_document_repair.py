"""Table rows in any shape, never-silent block repair, the long-document route and the fill check.

Reproduces the live failure ("make a 10page java stream notes in a pdf"): the model sent table rows
as strings and three create_document attempts failed with ``rows.0: Input should be a valid list``.
The real-browser tests are skipped without Edge or Chrome.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from pypdf import PdfReader

from app.automation.normalize import coerce_rows, describe_validation_problems, repair_spec_blocks
from app.automation.page_fit import fill_report, page_word_counts
from app.services.document_tools import (
    DocumentToolError,
    build_document,
    long_route,
)
from app.tasks.dispatch import BlockSpec, DocumentSpec
from tests.support.documents import requires_browser

HEADERS = ["Operation", "Kind", "Purpose"]


@pytest.mark.parametrize(
    "rows",
    [
        ["map | intermediate | transforms", "filter | intermediate | keeps"],
        "| map | intermediate | transforms |\n|---|---|---|\n| filter | intermediate | keeps |",
        [
            {"Operation": "map", "Kind": "intermediate", "Purpose": "transforms"},
            {"Operation": "filter", "Kind": "intermediate", "Purpose": "keeps"},
        ],
        ["map", "intermediate", "transforms", "filter", "intermediate", "keeps"],
        [["map", "intermediate", "transforms"], ["filter", "intermediate"]],
    ],
)
def test_table_rows_in_every_shape_become_lists_of_lists(rows: Any) -> None:
    headers, out, _ = coerce_rows(rows, list(HEADERS))
    assert headers == HEADERS and len(out) == 2
    assert all(isinstance(r, list) and len(r) == 3 for r in out)
    assert out[0][0] == "map" and out[1][0] == "filter"


def test_an_overlong_row_keeps_its_text_in_the_last_cell() -> None:
    _, out, changed = coerce_rows([["a", "b", "c", "d"]], ["x", "y", "z"])
    assert out == [["a", "b", "c | d"]] and changed


def test_the_exact_live_failure_is_reproduced_by_the_flat_schema_and_named_precisely() -> None:
    raw = {"type": "table", "headers": HEADERS, "rows": ["a | b | c", "d | e | f"]}
    spec = {
        "file_type": "pdf",
        "filename": "x.pdf",
        "title": "T",
        "blocks": [{"type": "paragraph", "text": "p"}] * 6 + [raw],
    }
    with pytest.raises(ValidationError) as caught:
        DocumentSpec.model_validate(spec)
    text = "; ".join(describe_validation_problems(caught.value, blocks=spec["blocks"]))
    assert "block 7 table" in text and "list of lists of strings" in text
    assert '[["a","b"],["c","d"]]' in text
    assert "rows.0" not in text  # one line per field, not one per row


def test_repair_never_drops_a_block_it_cannot_fix() -> None:
    blocks = [
        {"type": "heading", "level": 1, "text": "Intro"},
        {"type": "table", "headers": ["A"], "rows": 12},
        {"type": "table", "headers": HEADERS, "rows": ["x | y | z"]},
        {"type": "metrics", "items": [{"value": "1", "label": "one"}]},
        {"type": "callout", "kind": "tip", "text": "Use streams."},
    ]
    out, notes = repair_spec_blocks(blocks, BlockSpec.model_validate)
    assert [b["type"] for b in out] == ["heading", "table", "table", "metrics", "callout"]
    assert any("block 3 (table)" in n for n in notes)
    assert out[2]["rows"] == [["x", "y", "z"]]


def test_a_block_that_stays_invalid_is_kept_as_a_paragraph_and_named() -> None:
    def refuse(block: dict[str, Any]) -> None:
        if block.get("type") == "table":
            raise ValueError("bad")

    out, notes = repair_spec_blocks(
        [{"type": "table", "headers": ["A", "B"], "rows": [["1", "2"]], "caption": "Costs"}],
        refuse,
    )
    assert out[0]["type"] == "paragraph" and "Costs" in out[0]["text"]
    assert "could not be repaired" in notes[0] and "paragraph" in notes[0]


@requires_browser
def test_a_document_with_string_rows_builds_and_loses_no_section() -> None:
    headings = [f"Section {i}" for i in range(1, 7)]
    blocks: list[dict[str, Any]] = []
    for title in headings:
        blocks.append({"type": "heading", "level": 1, "text": title})
        blocks.append({"type": "paragraph", "text": f"Body of {title}."})
        blocks.append(
            {"type": "table", "headers": HEADERS, "rows": [f"{title} | k | p", "b | c | d"]}
        )
    with tempfile.TemporaryDirectory() as work:
        built = build_document(
            file_format="pdf",
            title="Java Stream Notes",
            spec={"theme": "notes", "blocks": blocks},
            request="java notes in a pdf",
            workdir=Path(work),
        )
    text = " ".join(p.extract_text() for p in PdfReader(_BytesPath(built.data)).pages)
    for title in headings:
        assert title in text
    assert any("table" in w and "reshaped" in w for w in built.warnings)


def _BytesPath(data: bytes) -> Any:
    import io

    return io.BytesIO(data)


def test_a_bad_spec_error_is_specific_enough_to_fix_in_one_retry() -> None:
    with tempfile.TemporaryDirectory() as work, pytest.raises(DocumentToolError) as caught:
        build_document(
            file_format="pdf",
            title="T",
            spec={"theme": "no-such-theme", "blocks": [{"type": "paragraph", "text": "x"}]},
            request="a pdf",
            workdir=Path(work),
        )
    assert "no-such-theme" in str(caught.value)


def test_a_ten_page_pdf_request_goes_to_the_long_document_job() -> None:
    route = long_route("pdf", None, "make a 10page java stream notes in a pdf")
    assert route is not None and route.pages == 10 and route.chapters >= 3
    assert "10" in route.instruction
    # Changed on purpose: this used to assert that the model's own `pages` argument (12) started
    # a background job. A model-planned count is not a requirement; only the count the user
    # stated routes.
    assert long_route("pdf", 12, "java stream notes") is None


@pytest.mark.parametrize(
    ("fmt", "pages", "message", "edits"),
    [
        # Changed on purpose: 6 pages used to be one call; 6 and above are a job now.
        ("pdf", None, "a 5 page pdf on java", None),
        ("pdf", 8, "java notes", None),
        ("pptx", 20, "20 slides on java", None),
        ("pdf", 12, "shorten this", "file-1"),
        ("pdf", None, "java notes", None),
    ],
)
def test_short_requests_edits_and_decks_stay_in_the_single_call(
    fmt: str, pages: Any, message: str, edits: str | None
) -> None:
    assert long_route(fmt, pages, message, edits) is None


def test_fill_report_flags_thin_interior_pages_only() -> None:
    report = fill_report([40, 300, 90, 310, 20], theme="notes", leading_pages=1)
    assert report.thin == (3,) and not report.ok and "Page 3" in report.note
    assert fill_report([300, 310, 305], theme="notes").ok
    # a forced page break may end one page early
    assert fill_report([300, 90, 310, 300], theme="notes", page_breaks=1).ok


def test_page_word_counts_ignore_the_running_header_and_footer(tmp_path: Path) -> None:
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(595, 842)
    path = tmp_path / "blank.pdf"
    with path.open("wb") as handle:
        writer.write(handle)
    assert page_word_counts(path) == [0]


@requires_browser
def test_the_cover_brand_extracts_as_plain_text_and_the_first_page_has_no_running_title() -> None:
    blocks = [{"type": "heading", "level": 1, "text": "Intro"}, {"type": "paragraph", "text": "x"}]
    with tempfile.TemporaryDirectory() as work:
        built = build_document(
            file_format="pdf",
            title="Cover Title",
            spec={"theme": "notes", "cover_page": True, "blocks": blocks},
            request="a pdf",
            workdir=Path(work),
        )
    first = PdfReader(_BytesPath(built.data)).pages[0].extract_text()
    assert first.split("\n")[0] == "MAYA AI"  # not letter-spaced into "M AYA  A I"
    assert first.count("Cover Title") == 1 and "Page 1 of" not in first
