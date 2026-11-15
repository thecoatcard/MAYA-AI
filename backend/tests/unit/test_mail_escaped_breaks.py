"""A mail body whose line breaks arrived as the characters backslash-n is fixed before approval."""

from __future__ import annotations

import pytest

from app.mail.smtp import clean_subject, fix_escaped_text

BACKSLASH_N = "\\n"  # the two characters: backslash and the letter n


def test_a_doubly_escaped_body_gets_real_line_breaks() -> None:
    raw = (
        "Hi Anand,\\n\\nYeh email aapko batane ke liye hai.\\n\\n1. Documents\\n2. GitHub\\n\\n"
        "Best regards,\\nMAYA AI"
    )
    assert BACKSLASH_N in raw
    fixed = fix_escaped_text(raw)
    assert BACKSLASH_N not in fixed
    assert fixed.startswith("Hi Anand,\n\nYeh email")
    assert fixed.endswith("Best regards,\nMAYA AI")
    assert fixed.count("\n") == 8


@pytest.mark.parametrize(
    "text",
    [
        "Plain text with\nreal breaks\nand more",
        "A single escaped one stays: C:\\new\\folder",
        "Mixed: real\nbreaks plus one \\n escape",
        "",
    ],
)
def test_text_that_is_not_clearly_escaped_is_left_alone(text: str) -> None:
    assert fix_escaped_text(text) == text


def test_the_subject_is_fixed_and_flattened() -> None:
    assert clean_subject("Update\\n\\nReport") == "Update Report"
