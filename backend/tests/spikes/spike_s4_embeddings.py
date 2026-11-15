"""Dependency-free offline S4 analysis; isolated SDK adapter is separate."""

import argparse
import json
import math
import struct
from dataclasses import asdict, dataclass

DIMENSIONS = 768
TEXTS = (
    "A cat is sleeping on a mat.",
    "A kitten is resting on a rug.",
    "A database index speeds up queries.",
)


@dataclass(frozen=True)
class EmbeddingMetrics:
    dimensions: list[int]
    raw_norms: list[float]
    normalized_norms: list[float]
    max_float32_roundoff: list[float]
    related_cosine: float
    unrelated_cosine: float


def build_request() -> dict[str, object]:
    # SDK keyword plan, not the REST API's camelCase JSON body.
    return {
        "model": "gemini-embedding-2",
        "contents": list(TEXTS),
        "config": {"output_dimensionality": DIMENSIONS},
    }


def fixture_response() -> dict[str, object]:
    return {
        "embeddings": [
            {"values": [2.0, *([0.0] * (DIMENSIONS - 1))]},
            {"values": [4.0, 3.0, *([0.0] * (DIMENSIONS - 2))]},
            {"values": [0.0, 0.0, 1.0, *([0.0] * (DIMENSIONS - 3))]},
        ]
    }


def _vector(embedding: object) -> list[float]:
    if not isinstance(embedding, dict):
        raise ValueError("Each embedding must be an object.")
    values = embedding.get("values")
    if not isinstance(values, list) or len(values) != DIMENSIONS:
        raise ValueError("Each embedding must contain exactly 768 values.")
    vector: list[float] = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("Embedding values must be finite numbers, not booleans.")
        try:
            number = float(value)
        except OverflowError as error:
            raise ValueError("Embedding value cannot be represented as a float.") from error
        if not math.isfinite(number):
            raise ValueError("Embedding values must be finite numbers.")
        vector.append(number)
    return vector


def analyze_response(payload: object) -> EmbeddingMetrics:
    if not isinstance(payload, dict):
        raise ValueError("Embedding response must be an object.")
    embeddings = payload.get("embeddings")
    if not isinstance(embeddings, list) or len(embeddings) != len(TEXTS):
        raise ValueError("Response must contain one embedding for each of the three inputs.")
    vectors = [_vector(embedding) for embedding in embeddings]
    norms = [math.hypot(*vector) for vector in vectors]
    if any(not math.isfinite(norm) or norm == 0 for norm in norms):
        raise ValueError("Embedding norms must be finite and nonzero.")
    normalized = [
        [value / norm for value in vector] for vector, norm in zip(vectors, norms, strict=True)
    ]
    roundoff: list[float] = []
    for vector in vectors:
        try:
            converted = struct.unpack(f"<{DIMENSIONS}f", struct.pack(f"<{DIMENSIONS}f", *vector))
        except (OverflowError, struct.error) as error:
            raise ValueError("Embedding values cannot be represented as float32.") from error
        roundoff.append(
            max(abs(value - rounded) for value, rounded in zip(vector, converted, strict=True))
        )
    return EmbeddingMetrics(
        dimensions=[len(vector) for vector in vectors],
        raw_norms=norms,
        normalized_norms=[math.hypot(*vector) for vector in normalized],
        max_float32_roundoff=roundoff,
        related_cosine=math.fsum(a * b for a, b in zip(normalized[0], normalized[1], strict=True)),
        unrelated_cosine=math.fsum(
            a * b for a, b in zip(normalized[0], normalized[2], strict=True)
        ),
    )


def offline_report() -> dict[str, object]:
    return {
        "status": "offline_fixture_only",
        "live_calls": 0,
        "live_acceptance_verified": False,
        "live_blocker": "Provider-side hard spending cap within INR 100 is not established.",
        "sdk_request_plan": build_request(),
        "synthetic_metrics": asdict(analyze_response(fixture_response())),
        "limitations": [
            "Vectors are hand-constructed fixtures, not model outputs.",
            "The isolated SDK adapter sends one text per request to avoid Embedding 2 aggregation.",
            "Parameter acceptance and returned dimensions require a future live run.",
            "Float32 roundoff measures local conversion, not the model's internal precision.",
            "This offline CLI never imports the separately locked SDK adapter.",
            "Similarity scores have no approved semantic pass/fail threshold.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--offline", action="store_true", help="Analyze generated vectors only.")
    mode.add_argument("--run", action="store_true", help="Blocked until live controls are ready.")
    args = parser.parse_args(argv)
    if args.run:
        parser.error("Live S4 blocked: enforceable INR 100 hard spending cap unverified.")
    print(json.dumps(offline_report(), indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
