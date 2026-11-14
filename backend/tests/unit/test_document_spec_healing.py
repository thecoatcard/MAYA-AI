"""Document spec self-healing: flat schema, normalisation, one repair retry, research fallback.

Owner report: the research nodes finished, then the document node failed with "The model
produced an invalid pdf specification: blocks.0: Unable to extract tag using discriminator
'type'; ..." because the model left out (or renamed) each block's ``type``. The text was fine;
only the shape was wrong.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from pptx import Presentation

from app.agents.specialized.document import DocumentAgent
from app.automation import model as doc_model
from app.automation.model import Document, document_from_dict
from app.automation.normalize import (
    BLOCK_TYPE_NAMES,
    SLIDE_LAYOUT_NAMES,
    describe_validation_problems,
    normalize_block,
    normalize_blocks,
    normalize_slides,
)
from app.tasks.dispatch import BlockSpec, DocumentSpec, FleetSupport, SlideSpec
from app.tasks.doc_healing import fallback_blocks
from app.tasks.queue import SubtaskJob
from app.tasks.worker import NonRetryableSubtaskError

OWNER_ERROR_FRAGMENT = "Unable to extract tag using discriminator"


def _job() -> SubtaskJob:
    return SubtaskJob(
        workspace_id=uuid4(),
        user_id=uuid4(),
        task_id=uuid4(),
        subtask_id=uuid4(),
        assigned_agent="document",
    )


def test_names_stay_in_step_with_the_strict_model() -> None:
    assert BLOCK_TYPE_NAMES == doc_model.BLOCK_TYPES
    assert SLIDE_LAYOUT_NAMES == doc_model.SLIDE_LAYOUTS


def test_the_model_facing_schema_is_flat_with_a_required_type_enum() -> None:
    schema = DocumentSpec.model_json_schema()
    block = schema["$defs"]["BlockSpec"]
    assert "type" in block["required"]
    assert block["properties"]["type"]["enum"] == list(doc_model.BLOCK_TYPES)
    text = str(schema)
    assert "discriminator" not in text and "oneOf" not in text
    assert {"text", "level", "items", "headers", "rows", "caption", "kind", "metrics"} <= set(
        block["properties"]
    )


# ------------------------------------------------------------------ layer 2: normalisation
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ({"text": "Intro"}, {"type": "paragraph", "text": "Intro"}),  # no type at all
        ({"kind": "paragraph", "text": "Intro"}, {"type": "paragraph", "text": "Intro"}),
        ({"block_type": "h2", "text": "Part"}, {"type": "heading", "level": 2, "text": "Part"}),
        ({"blockType": "p", "text": "Body"}, {"type": "paragraph", "text": "Body"}),
        ({"t": "ul", "items": ["a", "b"]}, {"type": "bullets", "items": ["a", "b"]}),
        ({"type": "ol", "items": ["a"]}, {"type": "numbered", "items": ["a"]}),
        ({"type": "h1", "text": "Top"}, {"type": "heading", "level": 1, "text": "Top"}),
        ({"type": "title", "text": "T"}, {"type": "heading", "level": 1, "text": "T"}),
        (
            {"type": "note", "text": "Careful"},
            {"type": "callout", "kind": "info", "text": "Careful"},
        ),
        (
            {"type": "alert", "text": "Careful"},
            {"type": "callout", "kind": "warning", "text": "Careful"},
        ),
        (
            {"headers": ["A", "B"], "rows": [["1", "2"]]},  # a table given as headers+rows only
            {"type": "table", "headers": ["A", "B"], "rows": [["1", "2"]]},
        ),
        ({"items": ["x", "y"]}, {"type": "bullets", "items": ["x", "y"]}),
        ({"level": 2, "text": "Sub"}, {"type": "heading", "level": 2, "text": "Sub"}),
        (
            {"items": [{"value": "5", "label": "Scooters"}]},
            {"type": "metrics", "items": [{"value": "5", "label": "Scooters"}]},
        ),
        ({"type": "paragraph", "text": ["a", "b"]}, {"type": "paragraph", "text": "a b"}),
        ("A bare string", {"type": "paragraph", "text": "A bare string"}),
    ],
)
def test_misshapen_blocks_are_reshaped_to_the_strict_schema(
    raw: Any, expected: dict[str, Any]
) -> None:
    block = normalize_block(raw)
    assert block == expected
    Document.model_validate({"title": "T", "blocks": [block]})  # and it validates strictly


def test_a_table_given_as_a_two_d_array_uses_its_first_row_as_headers_and_pads_rows() -> None:
    block = normalize_block({"type": "table", "rows": [["Name", "Range"], ["Ola"]]})
    assert block == {"type": "table", "headers": ["Name", "Range"], "rows": [["Ola", ""]]}


def test_a_garbage_block_is_dropped_never_invented_and_known_but_empty_is_kept_for_validation() -> (
    None
):
    blocks, notes = normalize_blocks([{"foo": 1}, 42, None, {"type": "paragraph", "text": "ok"}])
    assert blocks == [{"type": "paragraph", "text": "ok"}]
    assert any("2 unusable" in n or "3 unusable" in n for n in notes)
    # A stated known type with no content, and a stated unknown type, reach strict validation.
    assert normalize_block({"type": "paragraph", "text": ""}) == {"type": "paragraph", "text": ""}
    assert normalize_block({"type": "marquee", "text": "x"}) == {"type": "marquee", "text": "x"}


def test_document_from_dict_accepts_the_owner_shapes_without_a_type() -> None:
    doc = document_from_dict(
        {
            "title": "Scooters",
            "blocks": [
                {"text": "Top scooters"},
                {"kind": "p", "text": "Ola and Ather compared."},
                {"type": "ul", "items": ["Range", "Price"]},
                {"headers": ["Model", "Price"], "rows": [["Ola", "1.3L"]]},
                {"type": "h2", "text": "Verdict"},
            ],
        }
    )
    assert [b.type for b in doc.blocks] == ["paragraph", "paragraph", "bullets", "table", "heading"]


def test_slide_aliases_and_missing_layouts_are_reshaped() -> None:
    slides, notes = normalize_slides(
        [
            {"title": "Scooters", "subtitle": "India"},
            {"type": "bullet_points", "heading": "Range", "points": ["a", "b"]},
            {"layout": "comparison", "title": "Vs", "left": ["a"], "right": {"items": ["b"]}},
            {"title": "Facts", "content": "- one\n- two"},
            {"metrics": [{"value": "5", "label": "Models"}], "title": "Numbers"},
        ]
    )
    # "comparison" is its own layout now; "metrics" is the stat_callout layout.
    assert [s["layout"] for s in slides] == [
        "cover",
        "bullets",
        "comparison",
        "bullets",
        "stat_callout",
    ]
    assert slides[3]["bullets"] == ["one", "two"] and notes
    from app.automation.model import deck_from_dict

    assert len(deck_from_dict({"title": "D", "slides": slides}).slides) == 5


def test_an_unknown_stated_slide_layout_falls_back_and_is_reported() -> None:
    slides, notes = normalize_slides([{"layout": "carousel", "title": "x"}])
    assert slides[0]["layout"] == "cover"  # the first slide with only a title
    assert any("carousel" in note for note in notes)


# ------------------------------------------------------------------ layer 5: plain errors
def test_validation_problems_are_plain_words_not_discriminator_text() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError) as err:
        Document.model_validate({"title": "T", "blocks": [{"text": "no type"}, {"type": "x"}]})
    assert OWNER_ERROR_FRAGMENT in str(err.value)  # the raw pydantic text is the problem
    problems = " ".join(describe_validation_problems(err.value))
    assert OWNER_ERROR_FRAGMENT not in problems
    assert "block 1" in problems and "no recognisable type" in problems
    assert "block 2" in problems and "'x' is not supported" in problems


# ------------------------------------------------------------------ layers 1-4 in the dispatch
class _Gateway:
    client = None

    def __init__(self, *specs: DocumentSpec) -> None:
        self.specs = list(specs)
        self.calls: list[dict[str, Any]] = []

    async def generate_structured(self, **kwargs: Any) -> DocumentSpec:
        self.calls.append(kwargs)
        return self.specs.pop(0) if len(self.specs) > 1 else self.specs[0]


def _pdf(*blocks: BlockSpec, **kw: Any) -> DocumentSpec:
    return DocumentSpec(
        file_type="pdf", filename="r.pdf", title="Scooters", blocks=list(blocks), **kw
    )


BAD = BlockSpec(type="image", src="https://evil.example/a.png", alt="x")  # invalid after reshaping
RESEARCH_DEPS = {
    "r1": {
        "summary": (
            "# Findings\n\nThe Ola S1 Pro leads on range [1]. The Ather 450X is the quickest.\n\n"
            "- Ola S1 Pro: 195 km range\n- Ather 450X: 3.3 s to 40 km/h\n- TVS iQube: practical"
        ),
        "citations": [{"title": "Review", "url": "https://example.test/review", "snippet": ""}],
    },
    "r2": {
        "content": "## Pricing\n\n1. Ola from 1.3 lakh\n2. Ather from 1.5 lakh",
        "citations": [{"title": "Review", "url": "https://example.test/review", "snippet": ""}],
    },
}


async def _ctx(
    gateway: _Gateway, deps: dict[str, Any] | None = None, **extra: Any
) -> dict[str, Any]:
    support = FleetSupport(gateway=gateway, artifacts_dir=Path("."))  # type: ignore[arg-type]
    ctx: dict[str, Any] = {
        "description": "Create a 2-page PDF about scooters",
        "dependency_outputs": deps or {},
    }
    ctx.update(extra)
    return await support.build_context(_job(), ctx)


async def test_a_typeless_block_no_longer_fails_the_document_node() -> None:
    gateway = _Gateway(
        _pdf(BlockSpec(type="", text="Heading-ish"), BlockSpec(type="", items=["a"]))
    )
    out = await _ctx(gateway)
    assert [b["type"] for b in out["blocks"]] == ["paragraph", "bullets"]
    assert out["spec_healing"][0]["attempt"] == "normalise"
    assert "spec_warnings" not in out  # a silent reshape is recorded, not a warning
    assert len(gateway.calls) == 1  # no second model call was needed


async def test_one_repair_retry_with_the_problems_and_the_previous_spec() -> None:
    good = _pdf(BlockSpec(type="paragraph", text="Fixed text"))
    gateway = _Gateway(_pdf(BAD), good)
    out = await _ctx(gateway)
    assert out["blocks"] == [{"type": "paragraph", "text": "Fixed text"}]
    assert [h["attempt"] for h in out["spec_healing"]] == ["repair_retry"]
    assert "corrected by a second model call" in out["spec_warnings"][0]
    assert len(gateway.calls) == 2
    repair_prompt = str(gateway.calls[1]["contents"])
    assert "rejected by the validator" in repair_prompt and "https://evil.example" in repair_prompt
    assert "not a URL" in repair_prompt  # the problem list reaches the model


async def test_a_second_invalid_spec_falls_back_to_a_plain_report_from_the_research() -> None:
    gateway = _Gateway(_pdf(BAD))  # every call returns the same bad spec
    out = await _ctx(gateway, RESEARCH_DEPS)
    assert len(gateway.calls) == 2  # exactly one repair retry, then the fallback
    assert [h["attempt"] for h in out["spec_healing"]] == ["repair_retry", "fallback_from_research"]
    assert "plain structured report was generated from the research" in out["spec_warnings"][0]
    types = [b["type"] for b in out["blocks"]]
    assert types[0] == "heading" and "bullets" in types and types[-2:] == ["heading", "numbered"]
    assert out["blocks"][-2]["text"] == "Sources"
    document_from_dict({"title": "T", "blocks": out["blocks"]})  # valid
    assert [b for b in out["blocks"] if b["type"] == "heading" and b["level"] == 1] == [
        {"type": "heading", "level": 1, "text": "Scooters"}
    ]


async def test_without_any_research_text_it_fails_in_plain_words_with_the_details_after() -> None:
    gateway = _Gateway(_pdf(BAD))
    with pytest.raises(NonRetryableSubtaskError) as err:
        await _ctx(gateway)
    message = str(err.value)
    assert message.startswith("The model produced an invalid pdf specification: block 1")
    assert "not a URL" in message and "(details:" in message
    assert message.index("block 1") < message.index("(details:")
    assert not message.startswith(OWNER_ERROR_FRAGMENT)


async def test_a_missing_dependency_is_noted_for_the_model_and_skipped_by_the_fallback() -> None:
    deps = {"r1": {"missing": True, "reason": "failed"}, **{"r2": RESEARCH_DEPS["r2"]}}
    assert [b["type"] for b in fallback_blocks(deps, "T")][:2] == ["heading", "heading"]
    gateway = _Gateway(_pdf(BlockSpec(type="paragraph", text="x")))
    await _ctx(gateway, deps, missing_dependencies=["r1"])
    assert "NOTE: earlier steps r1 failed" in str(gateway.calls[0]["contents"])


async def test_slides_get_the_same_layers_with_a_deck_fallback_of_the_requested_size() -> None:
    bad_deck = DocumentSpec(
        file_type="pptx",
        filename="d.pptx",
        title="Scooters",
        slides=[SlideSpec(layout="bullets", title="No bullets here")],
    )
    deps = {
        "r1": {
            "summary": "# Scooters\n\n"
            + "\n".join(
                f"- Model {i} has a range of {100 + i} km and a price near {i} lakh."
                for i in range(12)
            )
        }
    }
    gateway = _Gateway(bad_deck)
    out = await _ctx(gateway, deps, description="Create an 8-slide deck comparing the scooters")
    assert [h["attempt"] for h in out["spec_healing"]] == ["repair_retry", "fallback_from_research"]
    assert len(out["slides"]) == 8 and out["expected_slides"] == 8
    assert out["slides"][0]["layout"] == "cover" and out["slides"][-1]["layout"] == "closing"


# ------------------------------------------------------------------ owner scenario end to end
async def test_scooter_task_shape_produces_a_verified_pdf_through_the_fallback(
    tmp_path: Path,
) -> None:
    """research -> research -> document, with a model that never returns a valid layout."""
    gateway = _Gateway(_pdf(BAD))
    support = FleetSupport(gateway=gateway, artifacts_dir=tmp_path)  # type: ignore[arg-type]
    agent = DocumentAgent(artifacts_base_dir=tmp_path)
    job = _job()
    ctx = await support.build_context(
        job,
        {
            "description": "Create a 2-page PDF comparing the scooters",
            "dependency_outputs": RESEARCH_DEPS,
        },
    )
    result = await agent.execute_subtask(job, ctx)
    assert result.status.value == "success", result.summary
    assert "plain structured report was generated from the research" in result.summary
    built = support.build_result(job, result)
    proof = built["proof"]
    assert proof["status"] == "verified" and proof["file_type"] == "pdf"
    assert [h["attempt"] for h in proof["healing"]] == ["repair_retry", "fallback_from_research"]
    assert proof["report"]["warnings"][-1].startswith("The model's layout spec was invalid")
    assert built["result"]["healing"] == proof["healing"]
    assert proof["size_bytes"] > 0 and proof["sha256"]


async def test_scooter_task_shape_with_typeless_blocks_renders_a_verified_pdf(
    tmp_path: Path,
) -> None:
    spec = _pdf(
        BlockSpec(type="", text="Top electric scooters in India"),
        BlockSpec(type="", level=2, text="Range"),
        BlockSpec(type="p", text="The Ola S1 Pro leads."),
        BlockSpec(type="", headers=["Model", "Range"], rows=[["Ola", "195 km"]]),
    )
    support = FleetSupport(gateway=_Gateway(spec), artifacts_dir=tmp_path)  # type: ignore[arg-type]
    job = _job()
    ctx = await support.build_context(job, {"description": "Create a PDF about scooters"})
    result = await DocumentAgent(artifacts_base_dir=tmp_path).execute_subtask(job, ctx)
    assert result.status.value == "success", result.summary
    assert support.build_result(job, result)["proof"]["healing"][0]["attempt"] == "normalise"


async def test_a_deck_with_an_aliased_layout_builds_a_verified_pptx(tmp_path: Path) -> None:
    spec = DocumentSpec(
        file_type="pptx",
        filename="d.pptx",
        title="Scooters",
        slides=[
            SlideSpec.model_validate({"layout": "cover", "title": "Scooters"}),
            *[
                SlideSpec.model_validate({"layout": "content", "title": f"P{i}", "bullets": ["a"]})
                for i in range(7)
            ],
        ],
    )
    support = FleetSupport(gateway=_Gateway(spec), artifacts_dir=tmp_path)  # type: ignore[arg-type]
    job = _job()
    ctx = await support.build_context(job, {"description": "Make an 8-slide deck about scooters"})
    result = await DocumentAgent(artifacts_base_dir=tmp_path).execute_subtask(job, ctx)
    assert result.status.value == "success", result.summary
    proof = support.build_result(job, result)["proof"]
    assert len(Presentation(proof["storage_uri"]).slides) == 8
