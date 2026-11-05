import json
import math

import pytest

from tests.spikes.spike_s4_embeddings import (
    DIMENSIONS,
    analyze_response,
    build_request,
    fixture_response,
    main,
    offline_report,
)


def test_request_uses_documented_sdk_dimension_parameter() -> None:
    request = build_request()
    assert request["model"] == "gemini-embedding-2"
    assert request["config"] == {"output_dimensionality": 768}
    contents = request["contents"]
    assert isinstance(contents, list)
    assert len(contents) == 3
    assert "task_type" not in request["config"]


def test_metrics_preserve_raw_norms_and_measure_float32_conversion() -> None:
    report = analyze_response(fixture_response())
    assert report.dimensions == [768, 768, 768]
    assert report.raw_norms == pytest.approx([2.0, 5.0, 1.0])
    assert report.related_cosine == pytest.approx(0.8)
    assert report.unrelated_cosine == pytest.approx(0.0)
    assert report.normalized_norms == pytest.approx([1.0, 1.0, 1.0])
    assert report.max_float32_roundoff == [0.0, 0.0, 0.0]


def test_returned_values_are_not_modified() -> None:
    payload = fixture_response()
    original = json.dumps(payload)
    analyze_response(payload)
    assert json.dumps(payload) == original


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"embeddings": []},
        {"embeddings": [{"values": [1.0] * DIMENSIONS}]},
        {"embeddings": [None] * 3},
        {"embeddings": [{"values": [1.0] * 767}] * 3},
        {"embeddings": [{"values": [1.0] * 769}] * 3},
        {"embeddings": [{"values": [0.0] * DIMENSIONS}] * 3},
        {"embeddings": [{"values": [True] * DIMENSIONS}] * 3},
        {"embeddings": [{"values": ["1.0"] * DIMENSIONS}] * 3},
        {"embeddings": [{"values": [math.nan] * DIMENSIONS}] * 3},
        {"embeddings": [{"values": [math.inf] * DIMENSIONS}] * 3},
        {"embeddings": [{"values": [1e100] * DIMENSIONS}] * 3},
    ],
)
def test_malformed_provider_responses_fail_explicitly(payload: object) -> None:
    with pytest.raises(ValueError):
        analyze_response(payload)


def test_roundoff_is_not_a_claim_about_provider_precision() -> None:
    values = [0.0] * DIMENSIONS
    values[0] = 0.1
    report = analyze_response({"embeddings": [{"values": values}] * 3})
    assert report.max_float32_roundoff[0] > 0
    assert report.related_cosine == pytest.approx(1.0)


def test_offline_cli_does_not_claim_embedding_verification(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["--offline"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report == offline_report()
    assert report["status"] == "offline_fixture_only"
    assert report["live_calls"] == 0
    assert report["live_acceptance_verified"] is False


def test_live_cli_is_blocked(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as error:
        main(["--run"])
    assert error.value.code == 2
    assert "hard spending cap" in capsys.readouterr().err
