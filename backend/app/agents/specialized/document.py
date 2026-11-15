"""Document Specialist Agent for Artifact Generation and Checksum Verification (Phase 09/15)."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.agents.base import BaseSpecialistAgent
from app.agents.schemas import (
    AgentExecutionStatus,
    SpecialistAgentResult,
    SpecialistAgentRole,
)
from app.automation.acceptance import (
    AcceptanceResult,
    check_acceptance,
    extract_ranges,
    normalize_acceptance,
    requested_pages,
    scaled_document_timeout,
)
from app.automation.count_fit import fit_slides, slide_count_note
from app.automation.docx_engine import create_document_from_model
from app.automation.long_document import word_count
from app.automation.model import (
    Document,
    document_from_dict,
    document_from_legacy,
    format_validation_error,
)
from app.automation.normalize import describe_validation_problems
from app.automation.page_fit import build_paged_file
from app.automation.paths import ArtifactPathTooLongError, fit_filename
from app.automation.pdf_engine import create_pdf_from_document
from app.automation.pptx_engine import create_deck, deck_from_legacy_slides
from app.automation.themes import DEFAULT_THEME
from app.automation.verifiers.docx_verifier import verify_docx
from app.automation.verifiers.pdf_verifier import verify_pdf
from app.automation.verifiers.pptx_verifier import verify_pptx
from app.automation.verifiers.xlsx_verifier import verify_xlsx
from app.automation.xlsx_engine import build_spreadsheet
from app.decisions.doc_quality import flatten, review_document
from app.mcp.client_manager import MCPClientManager
from app.mcp.connectors.filesystem import FilesystemConnector
from app.mcp.permissions import ToolPermissionRegistry, ToolTier
from app.tasks.queue import SubtaskJob

logger = logging.getLogger(__name__)

AcceptanceHealer = Callable[
    [SubtaskJob, dict[str, Any], AcceptanceResult, dict[str, int | None]],
    Awaitable[dict[str, Any] | None],
]
"""``FleetSupport.heal_acceptance``: context fields (with a ``healing`` trail) or ``None``."""

_DECK_FIT_ROUNDS = 4
"""Rebuilds the deck fit may make (the engine splitting a slide can move the count each time)."""

# One built file: its verification proof and what could be measured for the acceptance check.
_Built = tuple[dict[str, Any], dict[str, int | None]]


def _document_from_context(ctx: dict[str, Any]) -> Document:
    """Build the validated document model from new-schema ``blocks`` or legacy ``sections``."""
    theme = str(ctx.get("theme") or DEFAULT_THEME)
    title = str(ctx.get("title") or "Document Report")
    subtitle = str(ctx["subtitle"]) if ctx.get("subtitle") else None
    if ctx.get("blocks"):
        return document_from_dict(
            {
                "theme": theme,
                "title": title,
                "subtitle": subtitle,
                "author": ctx.get("author") or None,
                "date": ctx.get("date") or None,
                "cover_page": bool(ctx.get("cover_page")),
                "toc": bool(ctx.get("toc")),
                "density": "compact" if ctx.get("density") == "compact" else "normal",
                "accent_color": ctx.get("accent_color") or None,
                "design": ctx.get("design") or None,
                "blocks": list(ctx["blocks"]),
            }
        )
    sections = ctx.get("sections")
    if not sections:
        content = str(ctx.get("content", ""))
        sections = [{"type": "paragraph", "text": content}] if content else []
    return document_from_legacy(
        title, list(sections), subtitle=subtitle, theme=theme, design=ctx.get("design") or None
    )


def _invalid_spec_result(
    role: SpecialistAgentRole, filename: str, file_type: str, err: Exception, target: Path
) -> SpecialistAgentResult:
    """A precise failure for a document spec that does not validate (nothing is rendered)."""
    if isinstance(err, ValidationError):
        message = (
            f"{'; '.join(describe_validation_problems(err))} "
            f"(details: {format_validation_error(err)})"
        )
    else:
        message = str(err)
    return SpecialistAgentResult(
        agent_role=role,
        status=AgentExecutionStatus.FAILED,
        summary=f"Document '{filename}' has an invalid specification: {message}",
        verification_proof={
            "filename": filename,
            "file_type": file_type,
            "status": "failed",
            "checks_passed": [],
            "errors": [f"Invalid document specification: {message}"],
        },
        artifact_paths=[str(target)],
    )


def _stated_quantities(ctx: dict[str, Any]) -> dict[str, int]:
    """The user's stated quantities for this file: the planner's ``acceptance`` plus the slide
    count the document step's own description gave (``expected_slides``)."""
    acceptance = normalize_acceptance(ctx.get("acceptance"))
    expected = ctx.get("expected_slides")
    if expected and "slides" not in acceptance:
        try:
            acceptance["slides"] = int(expected)
        except (TypeError, ValueError):
            pass
    return acceptance


def _stated_ranges(ctx: dict[str, Any], acceptance: dict[str, int]) -> dict[str, tuple[int, int]]:
    """The ranges the user wrote ("8-10 pages", "about 10 slides") for the stated quantities.

    Read from the user's own words (the verbatim request carried with the step, else the step
    description); a range counts only when it contains the stated number, so a rewritten step
    description can never widen or move the requirement."""
    req = ctx.get("requirements")
    text = str(req.get("user_request") or "") if isinstance(req, dict) else ""
    spans = extract_ranges(text or str(ctx.get("description") or ""))
    return {
        key: span
        for key, span in spans.items()
        if key in acceptance and span[0] <= acceptance[key] <= span[1]
    }


def _count_reason(outcome: AcceptanceResult, proof: dict[str, Any], ctx: dict[str, Any]) -> str:
    """Why the count could not be matched, in one plain clause (follows the count sentence)."""
    if "slides" in outcome.requested:
        return "The deck could not be reshaped to that number without breaking its slides."
    if "rows" in outcome.requested:
        return "The sheet's columns did not allow the rows to be generated or cut to that number."
    if "sheets" in outcome.requested:
        return "The workbook's sheets could not be changed to that number."
    if ctx.get("assembly") is not None:
        return (
            "The expand, trim and layout repair rounds could not close the gap without "
            "dropping or padding real content."
        )
    if proof.get("page_fit"):
        return (
            "Changing the layout within readable limits and one final adjustment could not "
            "reach it without losing real content."
        )
    return "The page count comes from the layout and could not be matched."


def _design_summary(design: Any) -> dict[str, Any] | None:
    """The applied design for a proof: its base preset, mood and the palette that was set."""
    if not isinstance(design, dict) or not design:
        return None
    palette = design.get("palette")
    return {
        "name": "custom",
        "base": design.get("base") or None,
        "mood": design.get("mood") or None,
        "palette": {k: v for k, v in (palette or {}).items() if v},
    }


class DocumentAgent(BaseSpecialistAgent):
    """Specialist agent for structured document artifact generation and checksum verification."""

    role = SpecialistAgentRole.DOCUMENT
    permission_ceiling = ToolTier.WRITE
    # Headless-browser PDF rendering is the slow path; a hung render fails with a clear message.
    execution_timeout_seconds = 240.0
    assigned_tools = frozenset({"file_read", "file_write_artifact", "generate_document"})

    def __init__(
        self,
        artifacts_base_dir: Path | str = "/artifacts",
        permission_registry: ToolPermissionRegistry | None = None,
        client_manager: MCPClientManager | None = None,
    ) -> None:
        super().__init__(permission_registry=permission_registry, client_manager=client_manager)
        self.artifacts_base_dir = Path(artifacts_base_dir).resolve()

    def execution_timeout_for(self, ctx: dict[str, Any]) -> float | None:
        """Time budget for this input: 60 s plus 6 s per requested page (240 s to 10 minutes).

        ``None`` (use ``execution_timeout_seconds``) when the request states no length. The
        worker asks before it runs the agent; a long document needs rendering time for each page,
        two browser prints (contents page numbers) and a possible expand round.
        """
        pages = requested_pages(normalize_acceptance(ctx.get("acceptance")))
        return scaled_document_timeout(pages) if pages else None

    async def execute_subtask(
        self,
        job: SubtaskJob,
        context: dict[str, Any] | None = None,
    ) -> SpecialistAgentResult:
        """Generate document artifact inside workspace directory and calculate checksum."""
        ctx = dict(context or {})
        healer: AcceptanceHealer | None = ctx.pop("acceptance_healer", None)
        filename = str(ctx.get("filename", "document.md")).strip()
        doc_type = str(ctx.get("file_type") or ctx.get("doc_type") or "").lower()

        workspace_dir = self.artifacts_base_dir / str(job.workspace_id)
        try:
            filename = fit_filename(workspace_dir, filename)
        except ArtifactPathTooLongError as err:
            return SpecialistAgentResult(
                agent_role=self.role,
                status=AgentExecutionStatus.FAILED,
                summary=str(err),
                verification_proof={"error": "artifact_path_too_long", "status": "failed"},
            )
        workspace_dir.mkdir(parents=True, exist_ok=True)
        target_file = (workspace_dir / filename).resolve()
        # The filename can come from planner input (model output): it must stay inside the
        # workspace folder whatever it contains ("../", absolute paths, drive letters).
        if not filename or not target_file.is_relative_to(workspace_dir.resolve()):
            return SpecialistAgentResult(
                agent_role=self.role,
                status=AgentExecutionStatus.FAILED,
                summary=(
                    f"Document name '{filename[:80]}' is not allowed: a file name must stay "
                    "inside the workspace folder. Use a plain name such as report.pdf."
                ),
                verification_proof={"error": "path_traversal", "status": "failed"},
            )

        built = await self._produce(ctx, filename, doc_type, target_file, workspace_dir, job)
        if isinstance(built, SpecialistAgentResult):
            return built
        verification_proof, measured = built

        # The user's stated length is a requirement: measure, heal, fit deterministically, then
        # be honest. A count that is still off is a NOTE on a delivered file, never a rejection.
        acceptance = _stated_quantities(ctx)
        ranges = _stated_ranges(ctx, acceptance)
        exact = ctx.get("assembly") is None  # a long document keeps the page band
        outcome: AcceptanceResult | None = (
            check_acceptance(acceptance, measured, exact_pages=exact, ranges=ranges)
            if acceptance
            else None
        )
        heal_trail: list[dict[str, Any]] = []
        # A long document (it carries its assembly) gets up to three rounds (compact, expand,
        # trim in the order the healer decides); anything else one.
        rounds = 3 if ctx.get("assembly") is not None else 1
        for _ in range(rounds):
            if outcome is None or outcome.met or healer is None:
                break
            if self._deck_too_long(outcome, doc_type, filename):
                break  # too many slides: merged and trimmed in code below, no model call
            fields = await self._heal(healer, job, ctx, outcome, measured)
            if not fields:
                break
            round_trail = list(fields.pop("healing", []))
            retry_ctx = {**ctx, **fields}
            retried = await self._produce(
                retry_ctx, filename, doc_type, target_file, workspace_dir, job
            )
            heal_trail.extend(round_trail)
            if isinstance(retried, SpecialistAgentResult):
                heal_trail.append(
                    {
                        "attempt": "kept_first_version",
                        "reason": "the corrected version was not valid, so the first was kept",
                    }
                )
                break
            verification_proof, measured = retried
            ctx = retry_ctx
            outcome = check_acceptance(acceptance, measured, exact_pages=exact, ranges=ranges)

        # Count policy: a count that is still off is fitted in code (a deck is merged, split or
        # padded to the stated number; a long document is fitted to its pages); the file is kept
        # whatever the result.
        fit_actions: list[str] = []
        if outcome is not None and not outcome.met:
            fitted = await self._fit_counts(
                ctx,
                acceptance,
                ranges,
                outcome,
                measured,
                verification_proof,
                (filename, doc_type, target_file, workspace_dir, job),
            )
            if fitted is not None:
                ctx, verification_proof, measured, fit_actions, fit_trail = fitted
                heal_trail.extend(fit_trail)
                outcome = check_acceptance(acceptance, measured, exact_pages=exact, ranges=ranges)

        # What the spec self-healing did (see FleetSupport._document_context), kept visible.
        spec_healing = [*(ctx.get("spec_healing") or []), *heal_trail]
        spec_warnings = [str(w) for w in ctx.get("spec_warnings") or []]
        if spec_healing:
            verification_proof["healing"] = spec_healing
        if outcome is not None:
            verification_proof["acceptance"] = outcome.as_proof()
            if not outcome.met:
                spec_warnings.append(outcome.note)
        if spec_warnings:
            verification_proof["warnings"] = [
                *(verification_proof.get("warnings") or []),
                *spec_warnings,
            ]

        # A deliverable is only a success once its verifier passed.
        verifier_status = verification_proof.get("status")
        if verifier_status is not None and verifier_status != "verified":
            return SpecialistAgentResult(
                agent_role=self.role,
                status=AgentExecutionStatus.FAILED,
                summary=(
                    (f"{outcome.note} " if outcome is not None and not outcome.met else "")
                    + f"Document '{filename}' was generated but failed verification: "
                    f"{verification_proof.get('errors') or 'unknown error'}"
                ),
                verification_proof=verification_proof,
                artifact_paths=[str(target_file)],
            )
        # No "severe" rejection any more: a file far from the stated count (even under half) is
        # still delivered, with the true count in the result and the verdict recorded as a
        # warning (``verification_proof["acceptance"]["status"] == "warning"``).
        # ADR-0029: an advisory quality review. The deterministic checks above stay the authority;
        # a finding is added as a visible, honest note and never changes the status.
        review = await review_document(
            str(ctx.get("description") or ctx.get("title") or "")[:4_000],
            flatten(ctx),
            self._review_kind(filename, doc_type),
            pdf_path=target_file if filename.lower().endswith(".pdf") else None,
        )
        if review is not None:
            verification_proof["quality_review"] = review.as_dict()
            if review.flagged:
                spec_warnings.append(review.note())
        summary = [f"Document '{filename}' successfully generated and verified."]
        if outcome is not None and outcome.met:
            summary.append(self._length_text(outcome))
            if fit_actions and "slides" in outcome.requested:
                summary.append(
                    slide_count_note(
                        outcome.requested["slides"], outcome.actual["slides"], adjusted=fit_actions
                    )
                )
        elif outcome is not None:
            # Done with a note (never "failed"): say the true count plainly at the top.
            summary = [
                f"Document '{filename}' was generated and verified. {outcome.note} "
                f"{_count_reason(outcome, verification_proof, ctx)}"
            ]
            spec_warnings = [w for w in spec_warnings if w != outcome.note]
        summary.extend(spec_warnings)
        # Where the result goes (ADR-0022): the connected folder when there is one, else the
        # artifacts store. A failure here never loses the file.
        final_file, output_note = await self._deliver(job, target_file)
        if output_note:
            summary.append(output_note)
        elif final_file != target_file:
            summary.append(f"Saved in your connected folder as '{final_file.name}'.")
        return SpecialistAgentResult(
            agent_role=self.role,
            status=AgentExecutionStatus.SUCCESS,
            summary=" ".join(summary),
            verification_proof=verification_proof,
            artifact_paths=[str(final_file)],
        )

    @staticmethod
    def _deck_too_long(outcome: AcceptanceResult, doc_type: str, filename: str) -> bool:
        """A deck with MORE slides than stated: fitted in code (merge, trim), no model call."""
        is_deck = doc_type == "pptx" or filename.lower().endswith(".pptx")
        asked = outcome.requested.get("slides")
        got = outcome.actual.get("slides")
        return bool(is_deck and asked and got is not None and got > asked)

    async def _fit_counts(
        self,
        ctx: dict[str, Any],
        acceptance: dict[str, int],
        ranges: dict[str, tuple[int, int]],
        outcome: AcceptanceResult,
        measured: dict[str, int | None],
        proof: dict[str, Any],
        where: tuple[str, str, Path, Path, SubtaskJob],
    ) -> (
        tuple[
            dict[str, Any], dict[str, Any], dict[str, int | None], list[str], list[dict[str, Any]]
        ]
        | None
    ):
        """The deterministic last step of the count policy; ``None`` when nothing could change.

        Returns ``(ctx, proof, measured, actions, healing trail)`` of the best file written. A
        deck is merged / trimmed / split / padded to the stated slide count (the real file is
        measured each time, because the pptx engine can split an overfull slide itself). A long
        document that missed its page band is fitted by density and a final one-page adjustment.
        The file on disk is always a valid, verified one; a failed rebuild keeps the previous.
        """
        filename, doc_type, target_file, workspace_dir, job = where
        is_deck = doc_type == "pptx" or filename.lower().endswith(".pptx")
        if is_deck and "slides" in outcome.requested and ctx.get("slides"):
            return await self._fit_deck(ctx, acceptance, ranges, measured, proof, where)
        wants_pages = "pages" in outcome.requested and (
            doc_type in ("pdf", "docx") or filename.lower().endswith((".pdf", ".docx"))
        )
        if wants_pages and ctx.get("assembly") is not None:
            retried = await self._produce(
                ctx, filename, doc_type, target_file, workspace_dir, job, force_fit=True
            )
            if isinstance(retried, SpecialistAgentResult):
                return None
            new_proof, new_measured = retried
            note = (new_proof.get("page_fit") or {}).get("note")
            trail = [
                {
                    "attempt": "fit_pages",
                    "reason": str(note or "the long document was fitted to its page count"),
                }
            ]
            return ctx, new_proof, new_measured, [str(note)] if note else [], trail
        return None

    async def _fit_deck(
        self,
        ctx: dict[str, Any],
        acceptance: dict[str, int],
        ranges: dict[str, tuple[int, int]],
        measured: dict[str, int | None],
        proof: dict[str, Any],
        where: tuple[str, str, Path, Path, SubtaskJob],
    ) -> (
        tuple[
            dict[str, Any], dict[str, Any], dict[str, int | None], list[str], list[dict[str, Any]]
        ]
        | None
    ):
        filename, doc_type, target_file, workspace_dir, job = where
        want = acceptance["slides"]
        low, high = ranges.get("slides", (want, want))
        slides = [dict(s) for s in ctx["slides"]]
        got = measured.get("slides")
        actions: list[str] = []
        trail: list[dict[str, Any]] = []
        best: _Built | None = None
        for _ in range(_DECK_FIT_ROUNDS):
            if got is None or low <= got <= high:
                break
            # The engine may split an overfull slide, so the file's count is not len(slides):
            # aim the input at the stated number corrected by what the engine really made.
            aim = max(1, len(slides) + (want - got))
            fitted = fit_slides(slides, aim)
            if not fitted.changed:
                break
            retry_ctx = {**ctx, "slides": fitted.slides, "slide_plan": None}
            retried = await self._produce(
                retry_ctx, filename, doc_type, target_file, workspace_dir, job
            )
            if isinstance(retried, SpecialistAgentResult):
                break  # the fitted deck was not valid: the earlier file stays
            slides = fitted.slides
            ctx = retry_ctx
            actions.extend(fitted.actions)
            trail.append(
                {
                    "attempt": "fit_slide_count",
                    "reason": f"{got} slides against {want} asked for: "
                    + "; ".join(fitted.actions[:4]),
                }
            )
            best = retried
            _, new_measured = retried
            got = new_measured.get("slides")
        if best is None:
            return None
        return ctx, best[0], best[1], actions, trail

    @staticmethod
    def _review_kind(filename: str, doc_type: str) -> str:
        name = filename.lower()
        if doc_type == "pptx" or name.endswith(".pptx"):
            return "slide deck"
        if doc_type == "xlsx" or name.endswith(".xlsx"):
            return "spreadsheet"
        return "document"

    @staticmethod
    async def _deliver(job: SubtaskJob, target_file: Path) -> tuple[Path, str | None]:
        from app.files.services import deliver_output  # local: keeps agent imports light

        try:
            return await deliver_output(job.workspace_id, job.user_id, target_file)
        except Exception as error:  # the verified file already exists in the artifacts store
            logger.warning("document_output_routing_failed error=%s", type(error).__name__)
            return target_file, None

    @staticmethod
    def _length_text(outcome: AcceptanceResult) -> str:
        parts = [
            f"{key}: requested {want}, produced {outcome.actual[key]}"
            for key, want in outcome.requested.items()
        ]
        return "Length check passed (" + "; ".join(parts) + ")."

    @staticmethod
    async def _heal(
        healer: AcceptanceHealer,
        job: SubtaskJob,
        ctx: dict[str, Any],
        outcome: AcceptanceResult,
        measured: dict[str, int | None],
    ) -> dict[str, Any] | None:
        try:
            return await healer(job, ctx, outcome, measured)
        except Exception as err:  # a failed repair keeps the first (honestly reported) file
            logger.warning(
                "acceptance_heal_failed task=%s error=%s", job.task_id, type(err).__name__
            )
            return None

    async def _produce(
        self,
        ctx: dict[str, Any],
        filename: str,
        doc_type: str,
        target_file: Path,
        workspace_dir: Path,
        job: SubtaskJob,
        *,
        force_fit: bool = False,
    ) -> _Built | SpecialistAgentResult:
        """Render and verify one file. Returns ``(proof, measured)`` or a failure result.

        ``force_fit``: fit a long (assembled) document to its stated pages too (the count policy's
        last step; a long document is otherwise only checked against its page band)."""
        verification_proof: dict[str, Any] = {}
        measured: dict[str, int | None] = {}
        stated = _stated_quantities(ctx)
        spans = _stated_ranges(ctx, stated)

        if doc_type in ("pdf", "docx") or filename.lower().endswith((".pdf", ".docx")):
            kind = "docx" if (doc_type == "docx" or filename.lower().endswith(".docx")) else "pdf"
            try:
                document = _document_from_context(ctx)
                # A stated page count is fitted (render, count real pages, repair density, at most
                # three re-renders, then a one-page content adjustment). The renderers block, so
                # run them off the event loop.
                asked_pages = (
                    None
                    if ctx.get("assembly") is not None and not force_fit
                    else stated.get("pages")
                )
                with tempfile.TemporaryDirectory(prefix="maya-fit-") as fit_dir:
                    paged = await asyncio.to_thread(
                        build_paged_file,
                        document,
                        kind,
                        target_file,
                        assets_root=workspace_dir,
                        requested=asked_pages,
                        workdir=Path(fit_dir),
                        render_pdf=create_pdf_from_document,
                        render_docx=create_document_from_model,
                        accepted=spans.get("pages"),
                        adjust_content=True,
                    )
                out, renderer, warnings = paged.path, paged.renderer, list(paged.warnings)
                browser = paged.browser
                page_fit = paged.fit
            except (ValidationError, ValueError) as err:
                return _invalid_spec_result(self.role, filename, kind, err, target_file)
            report = verify_pdf(out) if kind == "pdf" else verify_docx(out)
            verification_proof = {
                "filename": filename,
                "file_type": kind,
                "status": report.status,
                "renderer": renderer,
                "theme": "custom" if ctx.get("design") else document.theme,
                "base_theme": document.theme,
                "design": _design_summary(ctx.get("design")),
                "warnings": warnings,
                "checks_passed": report.checks_passed,
                "errors": report.errors,
                "file_checksum": report.sha256,
                "size_bytes": report.size_bytes,
            }
            if kind == "pdf":
                verification_proof["total_pages"] = report.metadata.get("total_pages", 0)
                verification_proof["total_words"] = report.metadata.get("total_words", 0)
                verification_proof["fonts"] = report.metadata.get("fonts", [])
                verification_proof["browser"] = browser
                measured = {
                    "pages": int(report.metadata.get("total_pages", 0)),
                    "words": int(report.metadata.get("total_words", 0)),
                }
            else:
                # A DOCX has no fixed pages: its count exists only when LibreOffice laid it out
                # (page_fit). Without a converter the count is unmeasured, never guessed, and a
                # page requirement is neither checked nor claimed for it.
                measured = {"words": word_count([b.model_dump() for b in document.blocks])}
                if page_fit is not None and page_fit.measured is not None:
                    measured["pages"] = page_fit.measured
            if page_fit is not None:
                # The requested and measured counts, for the report and the verifier proof.
                verification_proof["page_fit"] = page_fit.as_dict()
                if not page_fit.exact:
                    verification_proof["warnings"] = [*warnings, page_fit.note]
        elif doc_type == "xlsx" or filename.lower().endswith(".xlsx"):
            sheets = ctx.get("sheets", [])
            try:
                if not sheets:
                    raise ValueError("the spreadsheet has no sheets")
                built_sheet = build_spreadsheet(
                    sheets,
                    target_file,
                    style=ctx.get("design") or None,
                    requested_rows=stated.get("rows"),
                    rows_range=spans.get("rows"),
                )
                out = built_sheet.path
            except (ValidationError, ValueError, TypeError, KeyError, IndexError) as err:
                return _invalid_spec_result(self.role, filename, "xlsx", err, target_file)
            report = verify_xlsx(out)
            verification_proof = {
                "filename": filename,
                "file_type": "xlsx",
                "status": report.status,
                "formulas_checked": report.metadata.get("formulas_checked", 0),
                "checks_passed": report.checks_passed,
                "errors": report.errors,
                "file_checksum": report.sha256,
                "size_bytes": report.size_bytes,
            }
            measured = {"sheets": len(sheets)}
            if built_sheet.file_rows is not None:
                # Counted on the written file: a stated row count is checked against it.
                measured["rows"] = built_sheet.file_rows
            if built_sheet.synthetic:
                verification_proof["synthetic"] = True
        elif doc_type == "pptx" or filename.lower().endswith(".pptx"):
            try:
                deck = deck_from_legacy_slides(
                    list(ctx.get("slides") or []),
                    theme=str(ctx.get("theme") or DEFAULT_THEME),
                    title=str(ctx["title"]) if ctx.get("title") else None,
                    lenient=False,
                    design=ctx.get("design") or None,
                )
                built = create_deck(deck, target_file, assets_root=workspace_dir)
            except (ValidationError, ValueError) as err:
                return _invalid_spec_result(self.role, filename, "pptx", err, target_file)
            # The slide count is NOT a verifier check any more (count policy): a mismatch is fitted
            # in code and, if it remains, delivered with a note (see ``execute_subtask``).
            report = verify_pptx(built.path)
            verification_proof = {
                "filename": filename,
                "file_type": "pptx",
                "status": report.status,
                "renderer": built.renderer,
                # The design that was really applied: 'custom' plus its palette (the preset name
                # alone, e.g. 'executive', says nothing about a custom design).
                "theme": "custom" if ctx.get("design") else built.theme,
                "base_theme": built.theme,
                "design": _design_summary(ctx.get("design")),
                "warnings": built.warnings,
                # Deterministic repairs made to the deck (splits, design values, fallbacks).
                "repairs": built.repairs,
                "total_slides": report.metadata.get("total_slides", 0),
                "slides_with_notes": report.metadata.get("slides_with_notes", 0),
                "checks_passed": report.checks_passed,
                "errors": report.errors,
                "file_checksum": report.sha256,
                "size_bytes": report.size_bytes,
            }
            if ctx.get("slide_plan"):
                # Slide-by-slide decks: the outline and each slide block, for the edit path.
                verification_proof["slide_plan"] = ctx["slide_plan"]
            measured = {"slides": int(report.metadata.get("total_slides", 0))}
        else:
            content = str(ctx.get("content", ""))
            fs = FilesystemConnector(
                base_dir=self.artifacts_base_dir, workspace_id=job.workspace_id
            )
            fs.write_file(filename, content)
            file_bytes = await asyncio.to_thread(target_file.read_bytes)
            checksum = hashlib.sha256(file_bytes).hexdigest()
            # Plain text has no format verifier; the honest check is "written and not empty".
            errors = [] if file_bytes.strip() else ["The document is empty"]
            verification_proof = {
                "filename": filename,
                "file_type": target_file.suffix.lstrip(".").lower() or "other",
                "status": "verified" if not errors else "failed",
                "checks_passed": ["file_written", "checksum_computed"] if not errors else [],
                "errors": errors,
                "file_checksum": checksum,
                "size_bytes": len(file_bytes),
            }
            measured = {"words": len(content.split())}
        return verification_proof, measured
