"""Model ids derived from the default registry, so tests follow it instead of hard-coding ids.

The owner may change the default model ids in ``app/gateway/registry.py``; tests must express
"the orchestrator model" / "the lite model", never the literal id. Note that two aliases can share
one model id (today ``search`` and ``orchestrator``): a test whose point is that two aliases differ
must build its own registry with ``build_default_registry(overrides)``.
"""

from app.gateway.registry import build_default_registry

# A model id no default alias uses; the override that makes the last-resort (``search``) model
# differ from every model in the plain-generation chains.
DISTINCT_LAST_RESORT_MODEL = "gemini-test-last-resort"
LAST_RESORT_OVERRIDES: dict[str, str] = {"search": DISTINCT_LAST_RESORT_MODEL}


def model_id(alias: str) -> str:
    """The model id the default registry maps ``alias`` to."""
    return build_default_registry().get(alias).model_id


ORCH = model_id("orchestrator")
LITE = model_id("lite")
HEAVY = model_id("heavy")
SEARCH = model_id("search")
