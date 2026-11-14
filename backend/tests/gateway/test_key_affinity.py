"""KeyPool affinity: stable role -> key map, ring fallback, cooldown, bad keys, concurrency."""

from __future__ import annotations

import logging
import threading
from collections import Counter

import pytest
from google.genai.errors import APIError

from app.core.metrics import GEMINI_KEY_POOL_BAD, REGISTRY
from app.gateway.key_pool import ROLE_ORDER, KeyPool, affinity_slot


def _keys(n: int) -> list[str]:
    """Secret-like strings built at runtime so no scanner trips on them."""
    return ["-".join(["affinity", "test", "key", str(i)]) for i in range(1, n + 1)]


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _label(pool: KeyPool, affinity: str | None, exclude: tuple[str, ...] = ()) -> str:
    return pool.get_next(affinity, exclude)[1]


@pytest.mark.parametrize("n", [1, 2, 3, 5])
def test_role_to_key_map_is_stable_and_follows_the_slot_rule(n: int) -> None:
    pool = KeyPool(_keys(n))
    first = pool.role_map()
    assert first == pool.role_map()  # deterministic
    for index, role in enumerate(ROLE_ORDER):
        assert first[role] == f"key#{index % n + 1}"
        assert _label(pool, role) == first[role]  # healthy affinity key is always returned


def test_distinct_roles_get_distinct_keys_first_then_wrap() -> None:
    pool = KeyPool(_keys(3))
    mapping = pool.role_map()
    assert [mapping[r] for r in ROLE_ORDER[:3]] == ["key#1", "key#2", "key#3"]
    assert mapping[ROLE_ORDER[3]] == "key#1"  # wraps only after every key has a role


def test_with_17_keys_every_role_has_its_own_key_and_chapters_use_the_rest() -> None:
    pool = KeyPool(_keys(17))
    mapping = pool.role_map()
    assert len(set(mapping.values())) == len(ROLE_ORDER) == 8
    assert mapping["planner"] == "key#1" and mapping["longdoc"] == "key#8"
    chapter_labels = [_label(pool, f"chapter:{i}") for i in range(10)]
    assert chapter_labels[0] == "key#9" and chapter_labels[8] == "key#17"
    assert len(set(chapter_labels)) == 10  # a 10-chapter run uses 10 different keys
    assert chapter_labels[9] == "key#1"  # then the ring wraps


def test_unknown_affinity_is_stable_across_instances() -> None:
    a = KeyPool(_keys(5))
    b = KeyPool(_keys(5))
    assert _label(a, "custom-role") == _label(b, "custom-role")
    assert 0 <= affinity_slot("custom-role", 5) < 5


def test_cooling_affinity_key_is_skipped_in_ring_order_then_restored() -> None:
    clock = Clock()
    keys = _keys(3)
    pool = KeyPool(keys, clock=clock)
    assert _label(pool, "research") == "key#3"
    pool.report_failure(keys[2], "429", retry_after=30.0)
    assert _label(pool, "research") == "key#1"  # next healthy in ring order (3 -> 1)
    pool.report_failure(keys[0], "429", retry_after=60.0)
    assert _label(pool, "research") == "key#2"
    clock.now += 31.0
    assert _label(pool, "research") == "key#3"  # mapping restored once the cooldown ends


def test_all_cooling_returns_the_one_recovering_soonest() -> None:
    clock = Clock()
    keys = _keys(3)
    pool = KeyPool(keys, clock=clock)
    pool.report_failure(keys[0], "429", retry_after=50.0)
    pool.report_failure(keys[1], "429", retry_after=20.0)
    pool.report_failure(keys[2], "429", retry_after=90.0)
    selection = pool.select("planner")
    assert selection is not None
    assert selection.label == "key#2" and selection.healthy is False


def test_exclude_never_returns_a_tried_key_and_none_when_all_tried() -> None:
    pool = KeyPool(_keys(3))
    assert _label(pool, "planner", exclude=("key#1",)) == "key#2"
    assert pool.select("planner", exclude=("key#1", "key#2", "key#3")) is None
    with pytest.raises(ValueError):
        pool.get_next("planner", ("key#1", "key#2", "key#3"))


def test_no_affinity_is_still_round_robin() -> None:
    pool = KeyPool(_keys(3))
    assert [_label(pool, None) for _ in range(5)] == ["key#1", "key#2", "key#3", "key#1", "key#2"]


def test_key_text_never_appears_in_reprs_labels_or_metrics() -> None:
    keys = _keys(4)
    pool = KeyPool(keys)
    pool.report_failure(keys[0], "429")
    selection = pool.select("planner")
    rendered = "\n".join(
        [repr(pool), repr(selection), str(pool.role_map()), "\n".join(REGISTRY.render())]
        if hasattr(REGISTRY, "render")
        else [repr(pool), repr(selection), str(pool.role_map())]
    )
    for key in keys:
        assert key not in rendered


def test_startup_log_has_size_and_label_map_but_no_key_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    keys = _keys(17)
    pool = KeyPool(keys)
    with caplog.at_level(logging.DEBUG):
        pool.log_assignments()
        pool.report_failure(keys[3], APIError(401, "bad key"))
        pool.report_failure(keys[4], "429", retry_after=5.0)
    assert "size=17" in caplog.text
    assert "planner=key#1" in caplog.text and "longdoc=key#8" in caplog.text
    for key in keys:
        assert key not in caplog.text


def test_invalid_keys_are_marked_bad_once_and_the_pool_keeps_working(
    caplog: pytest.LogCaptureFixture,
) -> None:
    keys = _keys(17)
    pool = KeyPool(keys)
    dead = [
        (
            0,
            APIError(400, {"error": {"message": "API key not valid", "status": "API_KEY_INVALID"}}),
        ),
        (1, APIError(401, "unauthenticated")),
        (2, APIError(403, "permission denied")),
    ]
    with caplog.at_level(logging.WARNING):
        for index, error in dead:
            pool.report_failure(keys[index], error)
            pool.report_failure(keys[index], error)  # a repeat must not log again
    assert pool.bad_count == 3
    assert caplog.text.count("gemini_api_key_marked_bad") == 3
    assert "label=key#1" in caplog.text
    assert GEMINI_KEY_POOL_BAD._values[()] == 3.0
    # the planner's own key (key#1) is dead: the next healthy key in ring order serves it
    assert _label(pool, "planner") == "key#4"
    used = {_label(pool, None) for _ in range(40)}
    assert used.isdisjoint({"key#1", "key#2", "key#3"})
    assert len(used) == 14


def test_a_plain_400_does_not_mark_a_key_bad() -> None:
    keys = _keys(2)
    pool = KeyPool(keys)
    pool.report_failure(keys[0], APIError(400, "Request payload is too large"))
    assert pool.bad_count == 0


def test_concurrent_callers_get_valid_distinct_selections_without_corruption() -> None:
    keys = _keys(17)
    pool = KeyPool(keys)
    results: list[str] = []
    lock = threading.Lock()
    barrier = threading.Barrier(8)

    def worker(offset: int) -> None:
        barrier.wait()
        for i in range(200):
            label = _label(pool, f"chapter:{offset + i}" if i % 2 else None)
            if i % 7 == 0:
                pool.report_failure(keys[(offset + i) % 17], "429", retry_after=0.001)
            with lock:
                results.append(label)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(results) == 8 * 200
    assert set(results) <= {f"key#{i}" for i in range(1, 18)}
    assert len(Counter(results)) > 8  # spread over many keys, no crash, no stuck state
