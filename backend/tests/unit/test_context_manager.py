"""Context manager: history merging, token-budget trimming and invariants (Phase 04)."""

import dataclasses
import random
from collections.abc import Callable, Sequence
from typing import Any

import pytest

from app.services.context_manager import (
    CHAT_SYSTEM_INSTRUCTION,
    PER_TURN_OVERHEAD_TOKENS,
    ContextManager,
    ContextTooLarge,
    ContextWindow,
    HistoryTurn,
    conservative_token_estimate,
)

HUGE = 10_000_000


def _chars(text: str) -> int:
    """One token per character and no overhead: makes the budget arithmetic in tests exact."""
    return len(text)


def _user(text: str) -> HistoryTurn:
    return HistoryTurn("user", text)


def _assistant(text: str) -> HistoryTurn:
    return HistoryTurn("assistant", text)


def _build(
    history: Sequence[HistoryTurn],
    new_message: str = "new",
    *,
    system: str = "sys",
    budget: int = HUGE,
    ceiling: int | None = None,
    estimator: Callable[[str], int] = _chars,
) -> ContextWindow:
    return ContextManager(estimator).build(
        history,
        new_message,
        system_instruction=system,
        token_budget=budget,
        hard_ceiling=ceiling,
    )


def _roles(window: ContextWindow) -> list[str]:
    return [item["role"] for item in window.contents]


def _texts(window: ContextWindow) -> list[str]:
    return [item["parts"][0]["text"] for item in window.contents]


def _item(role: str, text: str) -> dict[str, Any]:
    return {"role": role, "parts": [{"text": text}]}


# --- gateway format, merging and structure ---------------------------------------------------


def test_contents_use_the_gateway_format_with_assistant_mapped_to_model() -> None:
    window = _build([_user("q1"), _assistant("a1")], "q2")
    assert window.contents == [_item("user", "q1"), _item("model", "a1"), _item("user", "q2")]


def test_empty_history_gives_only_the_new_message() -> None:
    window = _build([], "hello")
    assert window.contents == [_item("user", "hello")]
    assert window.dropped_turns == 0


def test_consecutive_same_role_turns_are_merged_with_a_blank_line() -> None:
    history = [_user("a"), _user("b"), _assistant("c"), _assistant("d")]
    window = _build(history, "q")
    assert window.contents == [
        _item("user", "a\n\nb"),
        _item("model", "c\n\nd"),
        _item("user", "q"),
    ]
    assert window.dropped_turns == 0


def test_roles_strictly_alternate_after_merging() -> None:
    history = [
        _user("1"),
        _user("2"),
        _assistant("3"),
        _user("4"),
        _assistant("5"),
        _assistant("6"),
    ]
    window = _build(history, "7")
    assert _roles(window) == ["user", "model", "user", "model", "user"]


def test_leading_assistant_turn_is_dropped_and_counted() -> None:
    window = _build([_assistant("orphan"), _user("q1"), _assistant("a1")], "q2")
    assert _texts(window) == ["q1", "a1", "q2"]
    assert _roles(window)[0] == "user"
    assert window.dropped_turns == 1


def test_a_run_of_leading_assistant_turns_is_dropped_as_one_merged_turn() -> None:
    window = _build([_assistant("x"), _assistant("y"), _user("q1")], "q2")
    assert _texts(window) == ["q1\n\nq2"]
    assert window.dropped_turns == 2


def test_history_of_only_assistant_turns_leaves_only_the_new_message() -> None:
    window = _build([_assistant("x"), _assistant("y")], "q")
    assert window.contents == [_item("user", "q")]
    assert window.dropped_turns == 2


def test_empty_and_whitespace_only_turns_are_skipped() -> None:
    history = [_user(""), _assistant("   "), _user("q1"), _assistant("\n\t "), _assistant("a1")]
    window = _build(history, "q2")
    assert _texts(window) == ["q1", "a1", "q2"]


def test_skipping_a_blank_turn_lets_its_neighbours_merge() -> None:
    window = _build([_user("a"), _assistant(""), _user("b")], "q")
    assert window.contents == [_item("user", "a\n\nb\n\nq")]


def test_blank_turns_are_not_counted_as_dropped() -> None:
    window = _build([_user(""), _assistant(" "), _user("q1")], "q2")
    assert window.dropped_turns == 0


def test_turn_text_is_preserved_without_stripping() -> None:
    window = _build([_user("  padded\n"), _assistant("reply  ")], "q")
    assert _texts(window) == ["  padded\n", "reply  ", "q"]


def test_trailing_user_turn_is_merged_with_the_new_message() -> None:
    window = _build([_user("q1"), _assistant("a1"), _user("unanswered")], "q2")
    assert _roles(window) == ["user", "model", "user"]
    assert _texts(window)[-1] == "unanswered\n\nq2"


def test_sequence_ends_with_the_new_message_when_history_ends_with_assistant() -> None:
    window = _build([_user("q1"), _assistant("a1")], "q2")
    assert _roles(window)[-1] == "user"
    assert _texts(window)[-1] == "q2"


def test_new_message_is_kept_verbatim() -> None:
    message = "  keep\n\n  this exactly \t "
    window = _build([_user("q"), _assistant("a")], message)
    assert _texts(window)[-1] == message


def test_system_instruction_is_returned_unchanged() -> None:
    window = _build([_user("q"), _assistant("a")], system="  custom  rules \n")
    assert window.system_instruction == "  custom  rules \n"


def test_default_system_instruction_is_the_chat_instruction() -> None:
    window = ContextManager().build([], "hi", token_budget=HUGE)
    assert window.system_instruction == CHAT_SYSTEM_INSTRUCTION


# --- estimates -------------------------------------------------------------------------------


def test_estimate_covers_system_turns_and_new_message() -> None:
    window = _build([_user("aaaa"), _assistant("bbbbbb")], "cc", system="sys")
    assert window.estimated_tokens == 3 + 4 + 6 + 2


def test_empty_system_instruction_costs_nothing() -> None:
    window = _build([], "cc", system="")
    assert window.estimated_tokens == 2
    assert window.system_instruction == ""


def test_estimate_of_a_merged_final_turn_uses_the_merged_text() -> None:
    window = _build([_user("aaaa")], "cc", system="sys")
    assert window.estimated_tokens == 3 + len("aaaa\n\ncc")


def test_injected_estimator_is_used_for_every_estimate() -> None:
    seen: list[str] = []

    def estimator(text: str) -> int:
        seen.append(text)
        return 10

    window = _build([_user("u1"), _assistant("a1")], "q", system="sys", estimator=estimator)
    assert window.estimated_tokens == 40
    assert {"sys", "u1", "a1", "q"} <= set(seen)


def test_default_estimator_is_ceil_of_bytes_over_three_plus_overhead() -> None:
    assert conservative_token_estimate("") == PER_TURN_OVERHEAD_TOKENS
    assert conservative_token_estimate("ab") == 1 + PER_TURN_OVERHEAD_TOKENS
    assert conservative_token_estimate("abc") == 1 + PER_TURN_OVERHEAD_TOKENS
    assert conservative_token_estimate("abcd") == 2 + PER_TURN_OVERHEAD_TOKENS
    assert conservative_token_estimate("abcdefghi") == 3 + PER_TURN_OVERHEAD_TOKENS


def test_default_estimator_is_used_when_none_is_injected() -> None:
    window = ContextManager().build([], "abcdefghi", system_instruction="", token_budget=HUGE)
    assert window.estimated_tokens == conservative_token_estimate("abcdefghi")


def test_cjk_estimate_is_higher_than_ascii_of_the_same_length() -> None:
    ascii_text = "ab" * 10
    cjk_text = "你好" * 10
    assert len(ascii_text) == len(cjk_text)
    assert conservative_token_estimate(cjk_text) > conservative_token_estimate(ascii_text)


def test_cjk_history_estimates_higher_than_ascii_history_through_the_manager() -> None:
    manager = ContextManager()
    ascii_window = manager.build(
        [_user("ab" * 50), _assistant("cd" * 50)], "q", system_instruction="", token_budget=HUGE
    )
    cjk_window = manager.build(
        [_user("你好" * 50), _assistant("世界" * 50)],
        "q",
        system_instruction="",
        token_budget=HUGE,
    )
    assert cjk_window.estimated_tokens > ascii_window.estimated_tokens


@pytest.mark.parametrize(
    "text",
    ["", "a", "hello world", "x" * 1000, "résumé", "日本語", "\U0001f600" * 20],
)
def test_default_estimator_is_never_below_the_gateway_estimate(text: str) -> None:
    assert conservative_token_estimate(text) >= max(1, len(text) // 4)


def test_default_estimator_does_not_raise_on_a_lone_surrogate() -> None:
    assert conservative_token_estimate("a\ud800b") >= PER_TURN_OVERHEAD_TOKENS + 1


# --- trimming --------------------------------------------------------------------------------

# With the one-token-per-character estimator and system "sys" and new message "new" the floor is 6.
_FOUR_TURNS = [_user("aaaa"), _assistant("bbbb"), _user("cccc"), _assistant("dddd")]
_FOUR_TURNS_TOTAL = 6 + 16


def test_nothing_is_dropped_when_everything_fits() -> None:
    window = _build(_FOUR_TURNS, budget=_FOUR_TURNS_TOTAL)
    assert window.dropped_turns == 0
    assert window.estimated_tokens == _FOUR_TURNS_TOTAL
    assert _texts(window) == ["aaaa", "bbbb", "cccc", "dddd", "new"]


def test_trimming_starts_with_the_oldest_turns() -> None:
    window = _build(_FOUR_TURNS, budget=_FOUR_TURNS_TOTAL - 1)
    assert _texts(window) == ["cccc", "dddd", "new"]
    assert window.dropped_turns == 2
    assert window.estimated_tokens == 6 + 8


def test_trimming_drops_only_as_much_as_needed() -> None:
    assert _build(_FOUR_TURNS, budget=14).dropped_turns == 2
    assert _build(_FOUR_TURNS, budget=13).dropped_turns == 4


def test_dropping_a_user_turn_also_drops_the_reply_that_would_lead() -> None:
    window = _build(_FOUR_TURNS, budget=_FOUR_TURNS_TOTAL - 1)
    assert _roles(window)[0] == "user"
    assert window.dropped_turns % 2 == 0


def test_a_turn_is_dropped_whole_never_cut() -> None:
    window = _build([_user("a" * 100), _assistant("b" * 100), _user("keep")], "new", budget=40)
    assert _texts(window) == ["keep\n\nnew"]
    assert window.dropped_turns == 2


def test_new_message_and_system_are_kept_when_all_history_is_dropped() -> None:
    window = _build(_FOUR_TURNS, budget=6)
    assert window.contents == [_item("user", "new")]
    assert window.system_instruction == "sys"
    assert window.dropped_turns == 4
    assert window.estimated_tokens == 6


def test_trailing_user_turn_is_dropped_last() -> None:
    history = [_user("aaaa"), _assistant("bbbb"), _user("cccc")]
    # system 3 + aaaa 4 + bbbb 4 + merged "cccc\n\nnew" 9
    assert _build(history, budget=20).dropped_turns == 0
    assert _texts(_build(history, budget=19)) == ["cccc\n\nnew"]
    assert _build(history, budget=19).dropped_turns == 2
    assert _build(history, budget=12).dropped_turns == 2
    last = _build(history, budget=11)
    assert last.contents == [_item("user", "new")]
    assert last.dropped_turns == 3
    assert last.estimated_tokens == 6


def test_merged_history_turns_are_dropped_together() -> None:
    history = [_user("aaaa"), _user("bbbb"), _assistant("cccc"), _user("dddd"), _assistant("eeee")]
    # first merged user turn "aaaa\n\nbbbb" is 10 tokens; it goes with the reply "cccc"
    window = _build(history, budget=6 + 4 + 4)
    assert _texts(window) == ["dddd", "eeee", "new"]
    assert window.dropped_turns == 3


def test_trimming_never_changes_the_stored_history_objects() -> None:
    history = list(_FOUR_TURNS)
    _build(history, budget=6)
    assert history == _FOUR_TURNS


def test_realistic_budget_trims_a_long_history_from_the_oldest_end() -> None:
    history: list[HistoryTurn] = []
    for index in range(400):
        history.append(_user(f"question {index} " + "q" * 290))
        history.append(_assistant(f"answer {index} " + "a" * 290))
    window = ContextManager().build(
        history, "latest", token_budget=32_000, hard_ceiling=1_048_576 - 65_536
    )
    assert window.dropped_turns > 0
    assert window.estimated_tokens <= 32_000
    assert _roles(window)[0] == "user"
    assert _texts(window)[-2].startswith("answer 399 ")
    assert _texts(window)[-1] == "latest"


# --- too large, ceiling and argument checks --------------------------------------------------


def test_context_too_large_when_system_and_message_exceed_the_budget() -> None:
    with pytest.raises(ContextTooLarge) as caught:
        _build([_user("hist")], "x" * 50, system="s" * 10, budget=59)
    assert caught.value.required_tokens == 60
    assert caught.value.budget == 59


def test_context_too_large_message_never_repeats_submitted_text() -> None:
    with pytest.raises(ContextTooLarge) as caught:
        _build([], "secret-message-text", system="secret-system-text", budget=5)
    assert "secret" not in str(caught.value)


def test_context_too_large_is_not_a_value_error() -> None:
    assert not issubclass(ContextTooLarge, ValueError)


def test_system_and_message_exactly_at_the_budget_are_accepted() -> None:
    window = _build([_user("hist")], "x" * 50, system="s" * 10, budget=60)
    assert window.contents == [_item("user", "x" * 50)]
    assert window.dropped_turns == 1


def test_hard_ceiling_lower_than_the_budget_wins() -> None:
    window = _build(_FOUR_TURNS, budget=1000, ceiling=14)
    assert window.estimated_tokens <= 14
    assert window.dropped_turns == 2


def test_hard_ceiling_higher_than_the_budget_is_ignored() -> None:
    window = _build(_FOUR_TURNS, budget=14, ceiling=1000)
    assert window.estimated_tokens <= 14
    assert window.dropped_turns == 2


def test_hard_ceiling_can_make_the_context_too_large() -> None:
    with pytest.raises(ContextTooLarge) as caught:
        _build([], "x" * 50, system="s" * 10, budget=1000, ceiling=59)
    assert caught.value.budget == 59


@pytest.mark.parametrize(("budget", "ceiling"), [(0, None), (-5, None), (100, 0), (100, -1)])
def test_non_positive_budget_or_ceiling_is_rejected(budget: int, ceiling: int | None) -> None:
    with pytest.raises(ValueError, match="positive"):
        _build([], "q", budget=budget, ceiling=ceiling)


@pytest.mark.parametrize("message", ["", " ", "\n\t  \n"])
def test_blank_new_message_is_rejected(message: str) -> None:
    with pytest.raises(ValueError, match="blank"):
        _build([_user("q")], message)


def test_unknown_history_role_is_rejected() -> None:
    with pytest.raises(ValueError, match="role"):
        HistoryTurn("system", "x")  # type: ignore[arg-type]


# --- purity ----------------------------------------------------------------------------------


def test_same_inputs_give_the_same_window() -> None:
    first = _build(_FOUR_TURNS, budget=13)
    assert _build(_FOUR_TURNS, budget=13) == first
    assert (
        ContextManager(_chars).build(_FOUR_TURNS, "new", system_instruction="sys", token_budget=13)
        == first
    )


def test_inputs_are_not_mutated() -> None:
    history = [_assistant("lead"), _user("a"), _user("b"), _assistant("c"), _user("")]
    snapshot = list(history)
    message = "question"
    _build(history, message, budget=12)
    assert history == snapshot
    assert message == "question"


def test_result_does_not_share_structure_with_later_results() -> None:
    manager = ContextManager(_chars)
    first = manager.build([_user("q")], "n", system_instruction="s", token_budget=HUGE)
    first.contents[0]["parts"][0]["text"] = "tampered"
    first.contents.append(_item("model", "extra"))
    second = manager.build([_user("q")], "n", system_instruction="s", token_budget=HUGE)
    assert second.contents == [_item("user", "q\n\nn")]


def test_any_sequence_type_is_accepted_as_history() -> None:
    as_tuple = _build(tuple(_FOUR_TURNS), budget=14)
    assert as_tuple == _build(_FOUR_TURNS, budget=14)


def test_window_and_turn_are_frozen() -> None:
    window = _build([], "q")
    with pytest.raises(dataclasses.FrozenInstanceError):
        window.dropped_turns = 3  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        _user("x").text = "y"  # type: ignore[misc]


# --- system instruction constant -------------------------------------------------------------


def test_chat_system_instruction_is_short_plain_ascii_text() -> None:
    assert CHAT_SYSTEM_INSTRUCTION.isascii()
    assert 100 < len(CHAT_SYSTEM_INSTRUCTION) < 1500
    assert "!" not in CHAT_SYSTEM_INSTRUCTION


def test_chat_system_instruction_states_the_no_tools_limit() -> None:
    lowered = CHAT_SYSTEM_INSTRUCTION.lower()
    assert "no tools" in lowered
    assert "language" in lowered
    assert "uncertain" in lowered


def test_chat_system_instruction_fits_a_minimal_budget() -> None:
    window = ContextManager().build([], "hi", token_budget=1_000)
    assert window.estimated_tokens < 1_000


# --- property-style invariants ---------------------------------------------------------------

_PIECES = [
    "",
    " ",
    "\n",
    "hello",
    "the quick brown fox",
    "你好世界",
    "日本語のテキスト",
    "\U0001f600",
    "résumé",
    "x" * 80,
    "line one\nline two",
]


def _random_text(rng: random.Random) -> str:
    return " ".join(rng.choice(_PIECES) for _ in range(rng.randint(0, 6)))


def _random_history(rng: random.Random) -> list[HistoryTurn]:
    roles: list[Any] = ["user", "assistant"]
    return [HistoryTurn(rng.choice(roles), _random_text(rng)) for _ in range(rng.randint(0, 12))]


def _reference(turns: list[HistoryTurn], dropped: int, new_message: str) -> list[dict[str, Any]]:
    """Independent model of the output: the non-blank turns after the first ``dropped``, merged."""
    out: list[dict[str, Any]] = []
    for turn in turns[dropped:]:
        role = "user" if turn.role == "user" else "model"
        if out and out[-1]["role"] == role:
            out[-1]["parts"][0]["text"] += "\n\n" + turn.text
        else:
            out.append(_item(role, turn.text))
    if out and out[-1]["role"] == "user":
        out[-1]["parts"][0]["text"] += "\n\n" + new_message
    else:
        out.append(_item("user", new_message))
    return out


def _estimate_contents(
    estimator: Callable[[str], int], system: str, contents: list[dict[str, Any]]
) -> int:
    system_tokens = estimator(system) if system else 0
    return system_tokens + sum(estimator(item["parts"][0]["text"]) for item in contents)


def _half_plus_one(text: str) -> int:
    return -(-len(text) // 2) + 1


def test_invariants_hold_over_many_generated_histories() -> None:
    rng = random.Random(20261105)
    estimators: list[Callable[[str], int]] = [_chars, conservative_token_estimate, _half_plus_one]
    counts = {"too_large": 0, "trimmed": 0, "untrimmed": 0}

    for case in range(600):
        estimator = estimators[case % len(estimators)]
        history = _random_history(rng)
        non_blank = [t for t in history if t.text.strip()]
        new_message = "q" + _random_text(rng)
        system = rng.choice(["", "sys", CHAT_SYSTEM_INSTRUCTION])

        floor = (estimator(system) if system else 0) + estimator(new_message)
        everything = floor + sum(estimator(t.text) for t in history)
        budget = rng.randint(max(1, floor - 3), everything + 20)
        ceiling = None if rng.random() < 0.5 else rng.randint(max(1, budget - 30), budget + 30)
        effective = budget if ceiling is None else min(budget, ceiling)

        manager = ContextManager(estimator)
        if floor > effective:
            with pytest.raises(ContextTooLarge):
                manager.build(
                    history,
                    new_message,
                    system_instruction=system,
                    token_budget=budget,
                    hard_ceiling=ceiling,
                )
            counts["too_large"] += 1
            continue

        window = manager.build(
            history,
            new_message,
            system_instruction=system,
            token_budget=budget,
            hard_ceiling=ceiling,
        )
        contents = window.contents
        roles = [item["role"] for item in contents]

        assert roles[0] == "user"
        assert roles[-1] == "user"
        assert all(a != b for a, b in zip(roles, roles[1:], strict=False))
        assert all(item["parts"][0]["text"].strip() for item in contents)
        assert contents[-1]["parts"][0]["text"].endswith(new_message)
        assert window.system_instruction == system
        assert window.estimated_tokens <= effective
        assert window.estimated_tokens == _estimate_contents(estimator, system, contents)
        assert 0 <= window.dropped_turns <= len(non_blank)
        assert contents == _reference(non_blank, window.dropped_turns, new_message)

        # Oldest-first and minimal: no earlier whole-turn boundary that starts with a user turn
        # would have fitted.
        for earlier in range(window.dropped_turns):
            if non_blank[earlier].role != "user":
                continue
            if earlier > 0 and non_blank[earlier - 1].role == "user":
                continue
            candidate = _reference(non_blank, earlier, new_message)
            assert _estimate_contents(estimator, system, candidate) > effective

        counts["trimmed" if window.dropped_turns else "untrimmed"] += 1

    assert counts["too_large"] > 10
    assert counts["trimmed"] > 50
    assert counts["untrimmed"] > 50
