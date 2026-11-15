import math
import random

import pytest

from tests.spikes import spike_s2_vector_filtering
from tests.spikes.spike_s2_vector_filtering import (
    ANN_QUERY,
    EXACT_QUERY,
    WORKSPACE_SIZES,
    _validate_hnsw_filter,
    assert_filter_confinement,
    main,
    percentile,
    random_unit_vector,
    recall_at_k,
    vector_literal,
    workload_cases,
)


def test_spike_module_has_canonical_package_namespace() -> None:
    assert spike_s2_vector_filtering.__name__ == "tests.spikes.spike_s2_vector_filtering"


def test_random_unit_vector_is_seeded_and_normalized() -> None:
    first = random_unit_vector(random.Random(81), dimensions=4)
    second = random_unit_vector(random.Random(81), dimensions=4)

    assert first == second
    assert math.isclose(math.sqrt(sum(value * value for value in first)), 1.0)


def test_random_unit_vector_rejects_non_positive_dimensions() -> None:
    with pytest.raises(ValueError, match="dimensions must be positive"):
        random_unit_vector(random.Random(1), dimensions=0)


def test_vector_literal_serializes_pgvector_input() -> None:
    assert vector_literal((1.0, -0.5, 0.25)) == "[1,-0.5,0.25]"


@pytest.mark.parametrize("values", [(), (math.inf,), (math.nan,)])
def test_vector_literal_rejects_empty_or_non_finite_input(
    values: tuple[float, ...],
) -> None:
    with pytest.raises(ValueError):
        vector_literal(values)


def test_recall_at_k_uses_exact_top_k_and_does_not_count_duplicates() -> None:
    assert recall_at_k([1, 2, 3], [3, 1, 1], 2) == 0.5


def test_recall_at_k_handles_short_ground_truth_and_empty_results() -> None:
    assert recall_at_k([7, 8], [8], 10) == 0.5
    assert recall_at_k([], [], 10) == 1.0
    assert recall_at_k([], [1], 10) == 0.0


@pytest.mark.parametrize("k", [0, -1])
def test_recall_at_k_rejects_non_positive_k(k: int) -> None:
    with pytest.raises(ValueError, match="k must be positive"):
        recall_at_k([1], [1], k)


def test_filter_confinement_accepts_only_matching_tenant_rows() -> None:
    assert_filter_confinement(
        [{"workspace_id": 2, "user_id": 208}],
        workspace_id=2,
        user_id=208,
    )


@pytest.mark.parametrize(
    "row",
    [
        {"workspace_id": 3, "user_id": 208},
        {"workspace_id": 2, "user_id": 209},
    ],
)
def test_filter_confinement_rejects_workspace_or_user_escape(
    row: dict[str, int],
) -> None:
    with pytest.raises(AssertionError, match="outside"):
        assert_filter_confinement([row], workspace_id=2, user_id=208)


def test_percentile_interpolates_and_handles_boundaries() -> None:
    assert percentile([0.0, 10.0], 25) == 2.5
    assert percentile([4.0, 9.0, 11.0], 0) == 4.0
    assert percentile([4.0, 9.0, 11.0], 100) == 11.0


@pytest.mark.parametrize("samples, quantile", [([], 50), ([1.0], -1), ([1.0], 101)])
def test_percentile_rejects_invalid_samples_or_quantile(
    samples: list[float], quantile: float
) -> None:
    with pytest.raises(ValueError):
        percentile(samples, quantile)


def test_workload_cases_are_bounded_and_cover_varied_filter_selectivity() -> None:
    cases = workload_cases()

    assert sum(WORKSPACE_SIZES) == 30_000
    assert len(cases) == 8
    assert len({case.workspace_selectivity for case in cases}) == 4
    assert len({case.user_selectivity_within_workspace for case in cases}) == 2
    assert all(case.matching_rows >= 10 for case in cases)
    assert all(case.workspace_id > 0 and case.user_id > 0 for case in cases)


def test_ann_query_always_applies_workspace_and_user_predicates() -> None:
    assert "workspace_id = $1" in ANN_QUERY
    assert "user_id = $2" in ANN_QUERY
    assert ANN_QUERY != EXACT_QUERY


def test_hnsw_plan_requires_both_post_index_tenant_filters() -> None:
    node = {
        "Node Type": "Index Scan",
        "Index Name": "spike_s2_items_embedding_hnsw",
        "Filter": "((workspace_id = 1) AND (user_id = 100))",
        "Actual Rows": 10,
    }

    assert _validate_hnsw_filter({"Plan": {"Node Type": "Limit", "Plans": [node]}}) is node


@pytest.mark.parametrize(
    "plan",
    [
        {"Plan": {"Node Type": "Seq Scan", "Plans": []}},
        {
            "Plan": {
                "Node Type": "Index Scan",
                "Index Name": "spike_s2_items_embedding_hnsw",
                "Filter": "(workspace_id = 1)",
            }
        },
        {
            "Plan": {
                "Node Type": "Index Scan",
                "Index Name": "spike_s2_items_embedding_hnsw",
                "Filter": "(user_id = 100)",
            }
        },
        {
            "Plan": {
                "Node Type": "Index Scan",
                "Index Name": "spike_s2_items_embedding_hnsw",
                "Filter": "(workspace_id = 1)",
                "Plans": [
                    {
                        "Node Type": "Index Scan",
                        "Index Name": "spike_s2_items_embedding_hnsw",
                        "Filter": "(user_id = 100)",
                    }
                ],
            }
        },
    ],
)
def test_hnsw_plan_rejects_missing_or_ambiguous_tenant_filter(
    plan: dict[str, object],
) -> None:
    with pytest.raises(RuntimeError):
        _validate_hnsw_filter(plan)


def test_benchmark_cli_requires_explicit_opt_in(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as error:
        main([])

    assert error.value.code == 2
    assert "pass --run to proceed" in capsys.readouterr().err
