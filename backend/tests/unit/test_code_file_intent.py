"""Name, type and content of "put that code in a file" (services/code_file_intent.py)."""

import pytest

from app.services.code_file_intent import (
    code_blocks,
    delivered_text,
    plan_code_file,
    requested_code_name,
)

PY = "```python\nprint(1)\n```"


def test_blocks_are_extracted_without_fences() -> None:
    (block,) = code_blocks("intro\n```python\nprint(1)\n```\nbye")
    assert block.language == "python" and block.code == "print(1)"


def test_the_name_in_this_message_beats_the_name_in_history() -> None:
    plan = plan_code_file("give app.py file", ["make main.py"], [PY])
    assert plan is not None and plan.name == "app.py"


def test_the_name_from_an_earlier_user_message_is_used() -> None:
    plan = plan_code_file("give in a file", ["make a main.py having 4 bugs"], [PY])
    assert plan is not None and plan.name == "main.py" and plan.content == "print(1)\n"


def test_code_is_never_txt() -> None:
    plan = plan_code_file("give in a file", [], [PY])
    assert plan is not None and plan.name == "main.py"


def test_a_language_word_does_not_relabel_other_code() -> None:
    plan = plan_code_file("java me file de do", [], [PY])
    assert plan is not None and plan.name.endswith(".py")


@pytest.mark.parametrize(
    "request_text",
    ["give run.sh file", "give main.bat file", "give main.exe file", "make it a pdf file"],
)
def test_executable_and_other_deliverables_are_left_alone(request_text: str) -> None:
    assert plan_code_file(request_text, [], [PY]) is None


def test_no_code_means_not_this_module() -> None:
    assert plan_code_file("give in a file", [], ["just words, no code"]) is None


def test_a_request_with_its_own_topic_is_not_taken_over() -> None:
    assert plan_code_file("write a file about fruit and its prices", [], [PY]) is None


def test_the_reply_names_the_file_and_lines() -> None:
    plan = plan_code_file("give main.py file", [], [PY])
    assert plan is not None and "main.py (1 line)" in delivered_text(plan)


def test_a_named_code_file_overrides_a_text_build() -> None:
    assert requested_code_name("give main.py file", "txt") == ("main.py", "py")
    assert requested_code_name("give main.py file", "pdf") is None
    assert requested_code_name("give main.sh file", "txt") is None
    assert requested_code_name("give notes.txt", "txt") is None
