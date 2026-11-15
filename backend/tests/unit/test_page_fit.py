"""Page-exact PDF and DOCX: the file's REAL page count is measured and the layout fitted to it.

Known bug reproduced first: a request for 2 pages produced a 1 page file and said nothing. The
fit tests use a stand-in renderer (a PDF whose page count follows the density step) so they do
not depend on a browser or on LibreOffice; the real-browser test is skipped, not passed, when no
browser is installed. No model is called.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from reportlab.pdfgen import canvas

from app.agents.schemas import AgentExecutionStatus
from app.agents.specialized.document import DocumentAgent
from app.automation import page_fit
from app.automation.acceptance import check_acceptance
from app.automation.docx_engine import DocxRenderResult
from app.automation.model import Document
from app.automation.page_fit import (
    PageFit,
    build_paged_file,
    count_pdf_pages,
    estimate_pages,
    fit_to_pages,
    measure_pages,
    within_reach,
)
from app.automation.pdf_engine import PdfRenderResult
from app.services import document_tools
from app.services.document_tools import build_document
from tests.support.documents import requires_browser
from tests.support.longdoc import make_job

DOC = Document.model_validate(
    {
        "theme": "notes",
        "title": "Tea",
        "blocks": [
            {"type": "heading", "level": 1, "text": "Tea"},
            {"type": "paragraph", "text": "Tea is a drink made from leaves. " * 8},
        ],
    }
)


class StepPdf:
    """A renderer whose page count follows the density step: ``pages_at(step)`` pages."""

    def __init__(self, pages_at: Callable[[int], int]) -> None:
        self.pages_at = pages_at
        self.steps: list[int] = []

    def __call__(self, document: Document, output_path: Path | str, **_: Any) -> PdfRenderResult:
        self.steps.append(document.fit_step)
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        pdf = canvas.Canvas(str(path))
        for number in range(self.pages_at(document.fit_step)):
            for line in range(30):  # about 330 words, the density of a real content page
                pdf.drawString(72, 780 - line * 13, f"Page {number + 1} line {line} " + "tea " * 9)
            pdf.showPage()
        pdf.save()
        return PdfRenderResult(path=path, renderer="fake", theme=document.theme)


def build_pdf(tmp_path: Path, renderer: StepPdf, requested: int | None) -> page_fit.PagedBuild:
    return build_paged_file(
        DOC,
        "pdf",
        tmp_path / "out" / "Tea.pdf",
        assets_root=tmp_path,
        requested=requested,
        workdir=tmp_path / "work",
        render_pdf=renderer,
    )


def use_renderer(monkeypatch: pytest.MonkeyPatch, renderer: StepPdf) -> None:
    """Route the chat tool's page-fit through the stand-in renderer."""

    def build(*args: Any, **kwargs: Any) -> page_fit.PagedBuild:
        return build_paged_file(*args, render_pdf=renderer, **kwargs)

    monkeypatch.setattr(document_tools, "build_paged_file", build)


def test_a_two_page_request_that_renders_one_page_is_fitted_to_two(tmp_path: Path) -> None:
    """The reported bug: asked for 2 pages, the first render has 1 and used to be kept."""
    renderer = StepPdf(lambda step: 2 if step >= 2 else 1)
    built = build_pdf(tmp_path, renderer, 2)
    assert built.fit is not None and built.fit.exact
    assert (built.fit.requested, built.fit.measured, built.fit.method) == (2, 2, "pypdf")
    assert count_pdf_pages(built.path) == 2 and built.fit.fit_step == 2
    assert renderer.steps == [0, 1, 2] and built.fit.rounds == 2


def test_a_longer_than_asked_file_is_tightened(tmp_path: Path) -> None:
    built = build_pdf(tmp_path, StepPdf(lambda step: 2 if step <= -1 else 3), 2)
    assert built.fit is not None and built.fit.exact and built.fit.fit_step == -1


def test_a_count_the_density_cannot_reach_is_stated_not_hidden(tmp_path: Path) -> None:
    renderer = StepPdf(lambda step: 3)  # density never changes the count
    built = build_pdf(tmp_path, renderer, 2)
    fit = built.fit
    assert fit is not None and not fit.exact and fit.measured == 3
    assert len(renderer.steps) <= 4  # the first render plus at most three more
    # Wording updated deliberately (count policy): plain "You asked for ...; the file has ...".
    assert "You asked for 2 pages; the file has 3" in fit.note
    assert count_pdf_pages(built.path) == 3


def test_no_request_means_one_render_and_no_fit(tmp_path: Path) -> None:
    renderer = StepPdf(lambda step: 1)
    assert build_pdf(tmp_path, renderer, None).fit is None and renderer.steps == [0]


def test_an_exact_first_render_is_not_rendered_again(tmp_path: Path) -> None:
    renderer = StepPdf(lambda step: 2)
    built = build_pdf(tmp_path, renderer, 2)
    assert renderer.steps == [0] and built.fit is not None and built.fit.fit_step == 0
    assert built.fit.note == "The file has 2 pages, as asked."


def test_fit_to_pages_never_leaves_the_readable_step_range() -> None:
    steps: list[int] = []

    def render(step: int) -> tuple[int, int | None]:
        steps.append(step)
        return step, 5

    fit_to_pages(4, render, max_rounds=10)
    assert steps and all(-3 <= s <= 3 for s in steps) and len(steps) <= 4


def test_a_request_far_out_of_reach_is_not_re_rendered() -> None:
    assert not within_reach(10, 2) and within_reach(2, 1) and within_reach(2, 3)
    calls: list[int] = []

    def render(step: int) -> tuple[int, int | None]:
        calls.append(step)
        return step, 1

    assert fit_to_pages(10, render).measured == 1 and calls == [0]


def test_page_fit_density_stays_inside_the_readable_floor() -> None:
    from app.automation.design import fit_sizes
    from app.automation.themes import get_theme

    base = get_theme("notes").sizes
    densest = fit_sizes(base, -3)
    assert 8.5 <= densest.body_pt < base.body_pt
    assert fit_sizes(base, 0) == base


# ------------------------------------------------------------------------- DOCX


def test_a_docx_without_a_converter_is_never_guessed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(page_fit, "libreoffice_executable", lambda: None)
    built = build_document(
        file_format="docx",
        title="Tea notes",
        spec={"blocks": [{"type": "paragraph", "text": "Tea is a drink. " * 40}]},
        request="make a 2 page docx on tea",
        workdir=tmp_path,
    )
    assert built.pages is None and built.length is not None
    assert built.length.measured is None and built.length.requested == 2
    assert built.length.estimate is not None
    note = built.length.note
    assert "could not be measured" in note and "estimate only" in note
    assert note in built.warnings
    assert measure_pages(tmp_path / "Tea notes.docx", "docx") == (None, "not_measured")


def test_a_docx_with_a_converter_is_fitted_by_its_measured_pages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def render_docx(document: Document, output_path: Path | str, **_: Any) -> DocxRenderResult:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(document.fit_step))  # the stand-in file records its density step
        return DocxRenderResult(path=path, renderer="fake", theme=document.theme)

    monkeypatch.setattr(
        page_fit, "count_docx_pages", lambda p: 2 if int(Path(p).read_text()) >= 1 else 1
    )
    built = build_paged_file(
        DOC,
        "docx",
        tmp_path / "Tea.docx",
        assets_root=tmp_path,
        requested=2,
        workdir=tmp_path / "work",
        render_docx=render_docx,
    )
    assert built.fit is not None and built.fit.exact and built.fit.method == "libreoffice"
    assert "counted with LibreOffice" in built.fit.note


def test_the_estimate_is_labelled_and_the_note_names_a_difference() -> None:
    assert estimate_pages(0) == 1
    fit = PageFit(requested=2, measured=3, method="pypdf", kind="pdf")
    assert fit.as_dict()["measured"] == 3 and fit.as_dict()["requested"] == 2
    assert PageFit(requested=1, measured=1, unit="slides").note.startswith("The file has 1 slide,")


# ---------------------------------------------------------- chat tool and task paths


@requires_browser
def test_chat_tool_two_page_request_gets_two_real_pages(tmp_path: Path) -> None:
    paragraph = "Tea is a drink made from leaves and hot water. " * 8
    blocks = [{"type": "heading", "level": 1, "text": "Tea"}] + [
        {"type": "paragraph", "text": paragraph}
    ] * 9  # about one page at the theme's own density
    built = build_document(
        file_format="pdf",
        title="Tea",
        spec={"blocks": blocks, "theme": "notes"},
        request="make a 2 page pdf on tea",
        workdir=tmp_path,
        pages=2,
    )
    assert built.pages == 2 and count_pdf_pages(tmp_path / "Tea.pdf") == 2
    assert built.length is not None and built.length.exact


def test_chat_tool_states_the_difference_and_never_rejects_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_renderer(monkeypatch, StepPdf(lambda step: 3))
    kwargs: dict[str, Any] = {
        "file_format": "pdf",
        "title": "Tea",
        "spec": {"blocks": [{"type": "paragraph", "text": "Tea is a drink. " * 50}]},
        "request": "a 2 page pdf",
        "workdir": tmp_path,
        "pages": 2,
    }
    built = build_document(**kwargs)
    assert built.pages == 3 and built.length is not None
    assert (built.length.requested, built.length.measured) == (2, 3)
    assert any("You asked for 2 pages; the file has 3" in w for w in built.warnings)
    # Count policy (deliberate update): the first attempt (length_retry=True) used to raise
    # "built with 3 pages but 2 were asked for" and discard the file. It now delivers it too.
    again = build_document(**kwargs, length_retry=True)
    assert again.pages == 3 and again.length is not None and not again.length.exact


async def test_task_path_fits_a_pdf_to_the_stated_pages_and_records_both_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    renderer = StepPdf(lambda step: 2 if step >= 2 else 1)
    monkeypatch.setattr("app.agents.specialized.document.create_pdf_from_document", renderer)
    agent = DocumentAgent(artifacts_base_dir=tmp_path)
    ctx = {
        "filename": "Tea.pdf",
        "file_type": "pdf",
        "title": "Tea",
        "blocks": [{"type": "paragraph", "text": "Tea is a drink. " * 100}],
        "acceptance": {"pages": 2},
    }
    result = await agent.execute_subtask(make_job(), ctx)
    proof = result.verification_proof
    assert proof["total_pages"] == 2
    assert proof["page_fit"]["requested"] == 2 and proof["page_fit"]["measured"] == 2
    assert proof["acceptance"]["met"] is True
    assert result.status == AgentExecutionStatus.SUCCESS


def test_exact_pages_acceptance_rejects_the_band_that_used_to_pass() -> None:
    assert check_acceptance({"pages": 2}, {"pages": 3, "words": 900}).met
    assert not check_acceptance({"pages": 2}, {"pages": 3, "words": 900}, exact_pages=True).met
    assert check_acceptance({"pages": 2}, {"pages": 2, "words": 900}, exact_pages=True).met
