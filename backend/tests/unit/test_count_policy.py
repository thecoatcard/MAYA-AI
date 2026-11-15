"""Count policy: a page, slide or row count that differs from the request NEVER rejects a file.

Owner complaint: "If the user asks for any PDF, PPT or anything of X pages or slides and the AI
generates more or fewer than that, it gets REJECTED. That should not happen." The golden cases
below pin the new behaviour: the deck or document is fitted in code to the stated number, a count
that cannot be reached is delivered with the true numbers (a warning, not a failure), and a range
("8-10 pages", "about 10 slides") accepts any value inside it. Empty, invalid or unverifiable files
are still rejected (last test). Offline: stand-in PDF renderers, the real pptx and xlsx engines, no
model call.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from pptx import Presentation
from reportlab.pdfgen import canvas

from app.agents.schemas import AgentExecutionStatus
from app.agents.specialized.document import DocumentAgent
from app.automation import page_fit
from app.automation.acceptance import approx_range, check_acceptance, extract_ranges
from app.automation.count_fit import fit_slides, slide_count_note
from app.automation.model import Document
from app.automation.pdf_engine import PdfRenderResult
from app.automation.xlsx_engine import build_spreadsheet
from app.services import task_result
from app.services.document_tools import build_document
from tests.support.longdoc import make_job

# --------------------------------------------------------------------------------- fixtures


def deck_slides(total: int) -> list[dict[str, Any]]:
    """A cover, an agenda, ``total - 3`` content slides and a closing slide."""
    content: list[dict[str, Any]] = [
        {"layout": "bullets", "title": f"Topic {i}", "bullets": [f"Point {i}a", f"Point {i}b"]}
        for i in range(1, total - 2)
    ]
    slides: list[dict[str, Any]] = [
        {"layout": "cover", "title": "Solar deck", "subtitle": "An overview"},
        {"layout": "bullets", "title": "Agenda", "bullets": ["Why", "How", "Next"]},
        *content[: total - 3],
        {"layout": "closing", "title": "Thank you"},
    ]
    return slides[:total]


def slide_texts(path: str | Path) -> list[str]:
    out: list[str] = []
    for slide in Presentation(str(path)).slides:
        out.append(
            " ".join(s.text_frame.text for s in slide.shapes if getattr(s, "has_text_frame", False))
        )
    return out


async def run_deck(
    tmp_path: Path,
    slides: list[dict[str, Any]],
    asked: int,
    *,
    request: str | None = None,
) -> Any:
    agent = DocumentAgent(artifacts_base_dir=tmp_path)
    ctx: dict[str, Any] = {
        "filename": "deck.pptx",
        "file_type": "pptx",
        "title": "Solar deck",
        "slides": slides,
        "acceptance": {"slides": asked},
    }
    if request:
        ctx["requirements"] = {"user_request": request}
    return await agent.execute_subtask(make_job(), ctx)


class BlockPdf:
    """A stand-in PDF renderer whose page count follows the CONTENT (``pages_of``)."""

    def __init__(self, pages_of: Callable[[Document], int]) -> None:
        self.pages_of = pages_of
        self.calls = 0

    def __call__(self, document: Document, output_path: Path | str, **_: Any) -> PdfRenderResult:
        self.calls += 1
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        pdf = canvas.Canvas(str(path))
        for number in range(self.pages_of(document)):
            for line in range(30):
                pdf.drawString(72, 780 - line * 13, f"Page {number + 1} line {line} " + "tea " * 9)
            pdf.showPage()
        pdf.save()
        return PdfRenderResult(path=path, renderer="fake", theme=document.theme)


def breaks(document: Document) -> int:
    return sum(1 for block in document.blocks if block.type == "page_break")


PAGE_BLOCKS: list[dict[str, Any]] = [
    {"type": "heading", "level": 1, "text": "Tea"},
    {"type": "paragraph", "text": "Tea is a drink made from leaves. " * 20},
    {"type": "page_break"},
    {"type": "heading", "level": 2, "text": "Brewing"},
    {"type": "paragraph", "text": "Use hot water. " * 20},
]


async def run_pdf(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    renderer: Callable[..., Any],
    blocks: list[dict[str, Any]],
    asked: int,
    *,
    request: str | None = None,
    extra: dict[str, Any] | None = None,
) -> Any:
    monkeypatch.setattr("app.agents.specialized.document.create_pdf_from_document", renderer)
    ctx: dict[str, Any] = {
        "filename": "Tea.pdf",
        "file_type": "pdf",
        "title": "Tea",
        "blocks": blocks,
        "acceptance": {"pages": asked},
        **(extra or {}),
    }
    if request:
        ctx["requirements"] = {"user_request": request}
    return await DocumentAgent(artifacts_base_dir=tmp_path).execute_subtask(make_job(), ctx)


# --------------------------------------------------------------------- slides: the pure fit
def test_fit_slides_trims_14_to_10_keeping_the_cover_the_agenda_and_the_closing() -> None:
    fitted = fit_slides(deck_slides(14), 10)
    titles = [s["title"] for s in fitted.slides]
    assert len(fitted.slides) == 10 and fitted.changed
    assert titles[0] == "Solar deck" and titles[1] == "Agenda" and titles[-1] == "Thank you"
    assert any("merged" in action for action in fitted.actions)


def test_fit_slides_drops_the_thinnest_content_when_nothing_can_be_merged() -> None:
    slides: list[dict[str, Any]] = [
        {"layout": "cover", "title": "Deck"},
        {"layout": "quote", "title": "Big idea", "quote": "Long quote text " * 8},
        {"layout": "quote", "title": "Tiny", "quote": "Hi"},
        {"layout": "quote", "title": "Another", "quote": "Medium quote text " * 3},
        {"layout": "closing", "title": "Thank you"},
    ]
    fitted = fit_slides(slides, 4)
    titles = [s["title"] for s in fitted.slides]
    assert titles == ["Deck", "Big idea", "Another", "Thank you"]
    # The removed slide's text is kept in the speaker notes of the slide before it.
    assert "Tiny" in fitted.slides[1].get("notes", "")


def test_fit_slides_expands_7_to_10_by_splitting_then_labelled_appendix_slides() -> None:
    slides = deck_slides(7)
    slides[2]["bullets"] = ["a", "b", "c", "d", "e", "f"]  # one overfull slide can be split
    fitted = fit_slides(slides, 10)
    titles = [s["title"] for s in fitted.slides]
    assert len(fitted.slides) == 10 and titles[-1] == "Thank you"
    assert any(t.endswith("(cont.)") for t in titles)
    appendix = [t for t in titles if t.startswith("Appendix")]
    assert appendix and all("Notes" in t for t in appendix)  # clearly labelled, never disguised
    assert titles.index(appendix[0]) < len(titles) - 1  # before the closing


def test_fit_slides_is_exact_for_any_target_and_never_drops_the_last_slide() -> None:
    for target in (1, 2, 3, 5, 9, 12, 20):
        fitted = fit_slides(deck_slides(9), target)
        assert len(fitted.slides) == target
        if target >= 2:
            assert fitted.slides[-1]["title"] == "Thank you"


def test_slide_count_note_says_the_true_number_plainly() -> None:
    assert slide_count_note(10, 9) == "You asked for 10 slides; the deck has 9 slides."
    assert slide_count_note(1, 1).startswith("The deck has 1 slide, as you asked.")


# ----------------------------------------------------------- slides: task path, end to end
async def test_task_10_slides_requested_model_returned_14_is_merged_to_exactly_10(
    tmp_path: Path,
) -> None:
    result = await run_deck(tmp_path, deck_slides(14), 10)
    proof = result.verification_proof
    assert result.status == AgentExecutionStatus.SUCCESS, result.summary
    assert proof["status"] == "verified" and proof["total_slides"] == 10
    assert proof["acceptance"]["status"] == "met" and proof["acceptance"]["actual"] == {
        "slides": 10
    }
    assert any(h["attempt"] == "fit_slide_count" for h in proof["healing"])
    texts = slide_texts(result.artifact_paths[0])
    assert len(texts) == 10 and "Thank you" in texts[-1] and "Solar deck" in texts[0]
    assert "The deck has 10 slides, as you asked" in result.summary  # the plain result line


async def test_task_10_slides_requested_model_returned_7_is_expanded_to_exactly_10(
    tmp_path: Path,
) -> None:
    result = await run_deck(tmp_path, deck_slides(7), 10)
    proof = result.verification_proof
    assert result.status == AgentExecutionStatus.SUCCESS, result.summary
    assert proof["total_slides"] == 10 and proof["acceptance"]["met"] is True
    texts = slide_texts(result.artifact_paths[0])
    assert any("Appendix: Notes" in t for t in texts) and "Thank you" in texts[-1]


async def test_task_about_10_slides_accepts_11_without_touching_the_deck(tmp_path: Path) -> None:
    result = await run_deck(tmp_path, deck_slides(11), 10, request="make an about 10 slide deck")
    proof = result.verification_proof
    assert result.status == AgentExecutionStatus.SUCCESS
    assert proof["total_slides"] == 11 and proof["acceptance"]["met"] is True
    assert not any(h["attempt"] == "fit_slide_count" for h in proof.get("healing", []))
    # "about 10" is plus or minus 15 percent: 12 is outside it and is fitted back to 10.
    outside = await run_deck(tmp_path, deck_slides(12), 10, request="about 10 slides please")
    assert outside.verification_proof["total_slides"] == 10


async def test_task_an_explicit_slide_range_accepts_any_count_inside_it(tmp_path: Path) -> None:
    result = await run_deck(tmp_path, deck_slides(9), 9, request="an 8-10 slide deck on solar")
    assert result.verification_proof["total_slides"] == 9
    assert result.verification_proof["acceptance"]["met"] is True


async def test_task_a_model_expansion_round_runs_once_for_too_few_slides(tmp_path: Path) -> None:
    calls: list[str] = []

    async def healer(*args: Any) -> dict[str, Any] | None:
        calls.append("healed")
        return {"slides": deck_slides(10), "healing": [{"attempt": "regenerate_for_length"}]}

    agent = DocumentAgent(artifacts_base_dir=tmp_path)
    result = await agent.execute_subtask(
        make_job(),
        {
            "filename": "deck.pptx",
            "file_type": "pptx",
            "slides": deck_slides(7),
            "acceptance": {"slides": 10},
            "acceptance_healer": healer,
        },
    )
    assert calls == ["healed"] and result.verification_proof["total_slides"] == 10
    # Too many slides never asks the model: the deck is merged and trimmed in code.
    calls.clear()
    over = await agent.execute_subtask(
        make_job(),
        {
            "filename": "deck2.pptx",
            "file_type": "pptx",
            "slides": deck_slides(14),
            "acceptance": {"slides": 10},
            "acceptance_healer": healer,
        },
    )
    assert calls == [] and over.verification_proof["total_slides"] == 10


# --------------------------------------------------------------------- pages: task path
async def test_task_5_pages_requested_6_rendered_is_tightened_to_5(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Dense(BlockPdf):
        def __call__(self, document: Document, output_path: Path | str, **_: Any) -> Any:
            self.pages_of = lambda doc: 5 if doc.fit_step <= -1 else 6
            return super().__call__(document, output_path, **_)

    result = await run_pdf(tmp_path, monkeypatch, Dense(lambda d: 6), PAGE_BLOCKS, 5)
    proof = result.verification_proof
    assert result.status == AgentExecutionStatus.SUCCESS
    assert proof["total_pages"] == 5 and proof["acceptance"]["status"] == "met"
    assert proof["page_fit"]["exact"] is True and proof["page_fit"]["fit_step"] == -1


async def test_task_5_pages_requested_4_rendered_is_loosened_to_5(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Loose(BlockPdf):
        def __call__(self, document: Document, output_path: Path | str, **_: Any) -> Any:
            self.pages_of = lambda doc: 5 if doc.fit_step >= 1 else 4
            return super().__call__(document, output_path, **_)

    result = await run_pdf(tmp_path, monkeypatch, Loose(lambda d: 4), PAGE_BLOCKS, 5)
    assert result.status == AgentExecutionStatus.SUCCESS
    assert result.verification_proof["total_pages"] == 5
    assert result.verification_proof["acceptance"]["met"] is True


async def test_task_one_page_short_gets_a_labelled_notes_page_not_a_rejection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The layout cannot move the count (density never changes it); content decides: 1 + breaks.
    renderer = BlockPdf(lambda d: 1 + breaks(d))
    result = await run_pdf(tmp_path, monkeypatch, renderer, PAGE_BLOCKS, 3)  # 2 pages built
    proof = result.verification_proof
    assert result.status == AgentExecutionStatus.SUCCESS
    assert proof["total_pages"] == 3 and proof["acceptance"]["met"] is True
    assert "Notes page was added" in proof["page_fit"]["note"]


async def test_task_one_page_over_loses_only_the_trailing_appendix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    blocks = [
        *PAGE_BLOCKS,
        {"type": "page_break"},
        {"type": "heading", "level": 2, "text": "Appendix"},
        {"type": "paragraph", "text": "Extra material. " * 20},
    ]
    renderer = BlockPdf(lambda d: 1 + breaks(d))  # 3 pages built, 2 asked for
    result = await run_pdf(tmp_path, monkeypatch, renderer, blocks, 2)
    proof = result.verification_proof
    assert result.status == AgentExecutionStatus.SUCCESS
    assert proof["total_pages"] == 2 and proof["acceptance"]["status"] == "met"
    assert "Appendix" in proof["page_fit"]["note"] and "removed" in proof["page_fit"]["note"]


async def test_task_a_gap_that_cannot_be_fixed_is_delivered_with_a_note_not_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    renderer = BlockPdf(lambda d: 9)  # density and content never move the count
    result = await run_pdf(tmp_path, monkeypatch, renderer, PAGE_BLOCKS, 10)
    proof = result.verification_proof
    assert result.status == AgentExecutionStatus.SUCCESS  # never FAILED
    assert result.artifact_paths  # the file is delivered; its verifier read it from disk
    assert proof["status"] == "verified" and proof["total_pages"] == 9
    assert proof["acceptance"]["met"] is False and proof["acceptance"]["status"] == "warning"
    assert proof["acceptance"]["note"].startswith("You asked for 10 pages; the file has 9 pages")
    assert "You asked for 10 pages; the file has 9" in result.summary
    assert "failed" not in result.summary.lower() and "NOT meet" not in result.summary


async def test_task_8_10_pages_range_accepts_9_and_about_100_is_plus_minus_15_percent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    renderer = BlockPdf(lambda d: 9)
    result = await run_pdf(
        tmp_path, monkeypatch, renderer, PAGE_BLOCKS, 9, request="an 8-10 pages PDF on tea"
    )
    assert result.verification_proof["acceptance"]["status"] == "met"
    assert result.verification_proof["page_fit"]["accepted"] == [8, 10]
    assert renderer.calls == 1  # inside the range: no fitting render at all
    assert approx_range(100) == (85, 115)


async def test_task_long_document_outside_its_band_is_fitted_once_and_never_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Long(BlockPdf):
        def __call__(self, document: Document, output_path: Path | str, **_: Any) -> Any:
            self.pages_of = lambda doc: 20 if doc.fit_step <= -1 else 28
            return super().__call__(document, output_path, **_)

    result = await run_pdf(
        tmp_path,
        monkeypatch,
        Long(lambda d: 28),
        PAGE_BLOCKS,
        20,
        extra={"assembly": object()},  # a long document carries its assembly
    )
    proof = result.verification_proof
    assert result.status == AgentExecutionStatus.SUCCESS
    assert proof["total_pages"] == 20 and proof["acceptance"]["met"] is True
    assert any(h["attempt"] == "fit_pages" for h in proof["healing"])


# ------------------------------------------------------------------- chat tool path
def chat_pdf(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, renderer: Any, request: str) -> Any:
    monkeypatch.setattr(
        "app.services.document_tools.build_paged_file",
        lambda *a, **k: page_fit.build_paged_file(*a, render_pdf=renderer, **k),
    )
    return build_document(
        file_format="pdf",
        title="Tea",
        spec={"theme": "notes", "blocks": PAGE_BLOCKS},
        request=request,
        workdir=tmp_path,
        length_retry=True,  # the first attempt used to raise on a miss: it must deliver now
    )


@pytest.mark.parametrize(("rendered", "expected_note"), [(6, "has 6"), (4, "has 4")])
def test_chat_5_pages_requested_6_or_4_rendered_is_delivered_never_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rendered: int, expected_note: str
) -> None:
    built = chat_pdf(tmp_path, monkeypatch, BlockPdf(lambda d: rendered), "a 5 page pdf on tea")
    assert built.pages == rendered and built.data  # a file, not an exception
    assert built.length is not None and not built.length.exact
    assert f"You asked for 5 pages; the file has {rendered}" in built.length.note
    assert expected_note in built.length.note


def test_chat_pages_are_fitted_when_the_layout_or_one_page_of_content_can_reach_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fits = chat_pdf(
        tmp_path,
        monkeypatch,
        BlockPdf(lambda d: 5 if d.fit_step <= -1 else 6),
        "a 5 page pdf on tea",
    )
    assert fits.pages == 5 and fits.length is not None and fits.length.exact
    notes = chat_pdf(
        tmp_path, monkeypatch, BlockPdf(lambda d: 1 + breaks(d)), "a 3 page pdf on tea"
    )
    assert notes.pages == 3 and notes.length is not None and "Notes page" in notes.length.note


def test_chat_about_10_pages_accepts_11_and_an_8_10_range_accepts_9(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    about = chat_pdf(tmp_path, monkeypatch, BlockPdf(lambda d: 11), "about 10 pages on tea")
    assert about.length is not None and about.length.exact and about.pages == 11
    ranged = chat_pdf(tmp_path, monkeypatch, BlockPdf(lambda d: 9), "an 8-10 page pdf on tea")
    assert ranged.length is not None and ranged.length.exact and ranged.pages == 9


@pytest.mark.parametrize(("model_slides", "label"), [(14, "trimmed"), (7, "expanded")])
def test_chat_10_slides_requested_is_fitted_to_exactly_10(
    tmp_path: Path, model_slides: int, label: str
) -> None:
    built = build_document(
        file_format="pptx",
        title="Solar deck",
        spec={"theme": "midnight", "slides": deck_slides(model_slides)},
        request="a 10 slide deck on solar power",
        workdir=tmp_path,
        length_retry=True,
    )
    assert built.pages == 10, label
    assert built.length is not None and built.length.exact
    assert built.length.adjustment  # what was merged / split / added, in plain words
    assert built.effective_spec is not None and len(built.effective_spec["slides"]) == 10
    assert built.effective_spec["slides"][-1]["title"] == "Thank you"


def test_chat_about_10_slides_accepts_11(tmp_path: Path) -> None:
    built = build_document(
        file_format="pptx",
        title="Solar deck",
        spec={"theme": "midnight", "slides": deck_slides(11)},
        request="about 10 slides on solar",
        workdir=tmp_path,
    )
    assert built.pages == 11 and built.length is not None and built.length.exact
    assert not built.length.adjustment


# ------------------------------------------------------------------------------- rows
def rows_sheet(count: int, *, generated: bool = False) -> dict[str, Any]:
    sheet: dict[str, Any] = {"title": "Data", "headers": ["ID", "Qty"]}
    if generated:
        sheet["row_count"] = count
        sheet["columns"] = [
            {"header": "ID", "generate": {"kind": "sequence", "start": 1}},
            {"header": "Qty", "generate": {"kind": "range_int", "min": 1, "max": 9}},
        ]
    else:
        sheet["rows"] = [[f"R{i}", str(i)] for i in range(1, count + 1)]
    return sheet


def test_20_rows_requested_25_written_are_cut_to_exactly_20(tmp_path: Path) -> None:
    built = build_spreadsheet([rows_sheet(25)], tmp_path / "a.xlsx", requested_rows=20)
    assert built.file_rows == 20 and not built.shortfall
    assert any("reduced to the 20 rows" in note for note in built.notes)


def test_20_rows_requested_18_generated_are_extended_to_exactly_20(tmp_path: Path) -> None:
    built = build_spreadsheet(
        [rows_sheet(18, generated=True)], tmp_path / "b.xlsx", requested_rows=20
    )
    assert built.file_rows == 20 and not built.shortfall


def test_20_rows_requested_a_rows_range_accepts_any_count_inside_it(tmp_path: Path) -> None:
    kept = build_spreadsheet(
        [rows_sheet(25)], tmp_path / "c.xlsx", requested_rows=20, rows_range=(18, 25)
    )
    assert kept.file_rows == 25  # inside "18-25 rows": nothing is cut
    assert extract_ranges("a sheet with 18-25 rows") == {"rows": (18, 25)}


def test_a_summary_sheet_is_never_cut_by_the_row_count(tmp_path: Path) -> None:
    small = {"title": "Summary", "headers": ["Month", "Total"], "rows": [["Jan", "1"]] * 12}
    built = build_spreadsheet(
        [small, rows_sheet(30, generated=True)], tmp_path / "d.xlsx", requested_rows=20
    )
    sizes = {s.title: s.data_rows for s in built.sheets}
    assert sizes["Data"] == 20 and sizes["Summary"] == 12


async def test_task_20_rows_requested_18_hand_written_is_delivered_with_a_note(
    tmp_path: Path,
) -> None:
    agent = DocumentAgent(artifacts_base_dir=tmp_path)
    result = await agent.execute_subtask(
        make_job(),
        {
            "filename": "rows.xlsx",
            "file_type": "xlsx",
            "title": "Rows",
            "sheets": [rows_sheet(18)],
            "acceptance": {"rows": 20},
        },
    )
    proof = result.verification_proof
    assert result.status == AgentExecutionStatus.SUCCESS and proof["status"] == "verified"
    assert proof["acceptance"]["status"] == "warning"
    assert "You asked for 20 rows; the file has 18 rows" in result.summary
    over = await agent.execute_subtask(
        make_job(),
        {
            "filename": "rows2.xlsx",
            "file_type": "xlsx",
            "title": "Rows",
            "sheets": [rows_sheet(25)],
            "acceptance": {"rows": 20},
        },
    )
    assert over.verification_proof["acceptance"]["status"] == "met"
    assert over.verification_proof["acceptance"]["actual"] == {"rows": 20}


def test_chat_20_rows_requested_25_and_18_written(tmp_path: Path) -> None:
    over = build_document(
        file_format="xlsx",
        title="Rows",
        spec={"sheets": [rows_sheet(25)]},
        request="an excel sheet with 20 rows",
        workdir=tmp_path,
        length_retry=True,
    )
    assert (
        over.sheet is not None
        and over.sheet["rows"] == 20
        and over.sheet["delivery"] == ("complete")
    )
    short = build_document(
        file_format="xlsx",
        title="Rows",
        spec={"sheets": [rows_sheet(18)]},
        request="an excel sheet with 20 rows",
        workdir=tmp_path,
        length_retry=True,  # used to raise a repairable error and discard the sheet
    )
    assert short.sheet is not None and short.sheet["delivery"] == "partial"
    assert "You asked for 20 rows; the file has 18" in short.sheet["message"]


# -------------------------------------------------------- acceptance: warning, not failure
def test_acceptance_ranges_and_the_warning_verdict() -> None:
    assert extract_ranges("about 10 slides") == {"slides": (9, 11)}
    assert extract_ranges("8-10 pages") == {"pages": (8, 10)}
    assert extract_ranges("exactly 10 slides") == {}  # a plain count has no range: it is exact
    assert extract_ranges("around 100 pages") == {"pages": (85, 115)}
    assert check_acceptance({"slides": 10}, {"slides": 11}, ranges={"slides": (9, 11)}).met
    assert check_acceptance(
        {"pages": 9}, {"pages": 10}, exact_pages=True, ranges={"pages": (8, 10)}
    ).met
    missed = check_acceptance({"slides": 10}, {"slides": 3})
    # Far under half of the request: still only a WARNING (severe is information, not a failure).
    assert missed.severe and missed.status == "warning" and not missed.met
    assert missed.as_proof()["status"] == "warning"
    assert missed.note == "You asked for 10 slides; the file has 3 slides."
    assert check_acceptance({"slides": 10}, {"slides": 10}).status == "met"


# --------------------------------------------------------- task status and result text
def view_with_note(note: str, *, status: str = task_result.DONE) -> task_result.TaskView:
    proof = {
        "report": {
            "acceptance": {
                "met": False,
                "status": "warning",
                "requested": {"pages": 10},
                "actual": {"pages": 9},
                "message": "Requested 10 pages, produced 9",
                "note": note,
            }
        }
    }
    return task_result.TaskView(
        task_id="t1",
        title="Tea guide",
        instruction="make a 10 page PDF on tea",
        status=status,
        steps=(
            task_result.StepView(
                description="Write the PDF",
                agent="document",
                status="completed",
                proof=proof,
            ),
        ),
        artifacts=(task_result.ArtifactView("a1", "Tea.pdf", "pdf"),),
        warnings=(note,),
    )


def test_task_result_says_the_true_count_and_never_calls_the_file_failed() -> None:
    note = "You asked for 10 pages; the file has 9 pages."
    # A delivered file with a count warning is DONE (no failed step, so never "partial").
    assert task_result.status_of("completed", {"warnings": [note]}) == task_result.DONE
    text = task_result.deterministic_result(view_with_note(note))
    assert "You asked for 10 pages; the file has 9 pages." in text.markdown
    assert "Size: 9 pages" in text.markdown and "The full file is in Artifacts." in text.markdown
    low = (text.markdown + text.spoken).lower()
    assert "failed" not in low and "did not finish" not in low and "with problems" not in low
    assert text.status == task_result.DONE
    assert "You asked for 10 pages" in text.spoken
    assert text.markdown.count("You asked for 10 pages") == 1  # the note is not repeated
    assert task_result.count_notes(view_with_note(note).steps) == [note]


# --------------------------------------------------------- real rejections stay rejections
async def test_empty_or_invalid_files_are_still_rejected_even_with_a_stated_count(
    tmp_path: Path,
) -> None:
    agent = DocumentAgent(artifacts_base_dir=tmp_path)
    empty_deck = await agent.execute_subtask(
        make_job(),
        {"filename": "e.pptx", "file_type": "pptx", "slides": [], "acceptance": {"slides": 5}},
    )
    assert empty_deck.status == AgentExecutionStatus.FAILED
    no_sheets = await agent.execute_subtask(
        make_job(),
        {"filename": "e.xlsx", "file_type": "xlsx", "sheets": [], "acceptance": {"rows": 5}},
    )
    assert no_sheets.status == AgentExecutionStatus.FAILED
    empty_text = await agent.execute_subtask(
        make_job(), {"filename": "e.txt", "content": "   ", "acceptance": {"words": 100}}
    )
    assert empty_text.status == AgentExecutionStatus.FAILED
