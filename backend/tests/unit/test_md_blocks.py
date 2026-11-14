"""The Markdown -> blocks parser (``app.automation.md_blocks``): golden, lossless, lenient, valid.

Pure and offline. The property-style tests generate documents from a seeded random source (no
extra dependency) and check that the ordered words of the input are exactly the ordered words of
the blocks, and that whatever the parser returns passes the strict document model.
"""

from __future__ import annotations

import random
import re
from typing import Any

import pytest

from app.automation import model as doc_model
from app.automation.md_blocks import (
    parse_markdown,
    split_closing_sections,
    split_plan,
    split_text,
    unwrap_markdown_fence,
)

NL = "\n"

JAVA_CHAPTER = """\
## Streams ka basic idea

Java 8 mein **Stream API** aaya. Stream ek *pipeline* hai: data source se start hota hai,
phir intermediate operations aate hain, aur end mein ek terminal operation. Aaj hum `map` aur
`filter` ko [official docs](https://docs.oracle.com/en/java/) ke saath samjhenge.

### Common operations

- `filter(Predicate)` selects elements
  - keeps only the matching ones
  - lazy: nothing runs until a terminal call
- `map(Function)` transforms each element
- `collect(Collector)` is terminal

1. Create the stream
2. Chain the operations
3. Call a terminal operation

```java
List<String> names = List.of("Asha", "Ravi", "Meera");
List<String> longNames = names.stream()
    .filter(n -> n.length() > 3)
    .map(String::toUpperCase)
    .toList();
```

| Operation | Kind | Example |
|:--|:-:|---|
| filter | intermediate | `filter(x -> x > 1)` |
| map | intermediate | `map(String::length)` |
| collect | terminal | `collect(Collectors.toList())` |

> [!TIP]
> Since Java 16 `toList()` is shorter than `collect(Collectors.toList())`.

> [!WARNING] A stream cannot be reused after a terminal operation.

> Premature optimisation is the root of all evil.
> — Donald Knuth

---

## Key takeaways

- Streams are lazy
- One terminal operation per stream

## Exercises

1. Print the squares of the even numbers of a list.
2. Group the names by their first letter.
"""


def _types(blocks: list[dict[str, Any]]) -> list[str]:
    return [str(b["type"]) for b in blocks]


def _valid(blocks: list[dict[str, Any]]) -> None:
    if blocks:
        doc_model.document_from_dict({"title": "t", "blocks": blocks})


# --------------------------------------------------------------------------- golden
def test_a_java_streams_chapter_with_hinglish_maps_to_the_expected_blocks() -> None:
    parsed = parse_markdown(JAVA_CHAPTER)
    blocks = parsed.blocks
    assert _types(blocks) == [
        "heading",
        "paragraph",
        "heading",
        "bullets",
        "numbered",
        "code",
        "table",
        "callout",
        "callout",
        "quote",
        "heading",
        "bullets",
        "heading",
        "numbered",
    ]
    assert blocks[0] == {"type": "heading", "level": 2, "text": "Streams ka basic idea"}
    assert blocks[2]["level"] == 3
    # inline markup is kept for the renderers; soft-wrapped lines are one paragraph
    paragraph = blocks[1]["text"]
    assert "**Stream API**" in paragraph and "*pipeline*" in paragraph and "`map`" in paragraph
    assert "[official docs](https://docs.oracle.com/en/java/)" in paragraph
    assert "\n" not in paragraph and "Java 8 mein" in paragraph
    # nested items stay list items, marked as nested; the item order is the source order
    assert blocks[3]["items"] == [
        "`filter(Predicate)` selects elements",
        "– keeps only the matching ones",
        "– lazy: nothing runs until a terminal call",
        "`map(Function)` transforms each element",
        "`collect(Collector)` is terminal",
    ]
    assert blocks[4]["items"][0] == "Create the stream" and len(blocks[4]["items"]) == 3
    # real code with its language and indentation
    assert blocks[5]["language"] == "java"
    assert blocks[5]["text"].splitlines()[0].startswith('List<String> names = List.of("Asha"')
    assert "    .filter(n -> n.length() > 3)" in blocks[5]["text"]  # indentation survives
    # the table keeps its header row; alignment markers are consumed
    assert blocks[6]["headers"] == ["Operation", "Kind", "Example"]
    assert blocks[6]["rows"][2] == ["collect", "terminal", "`collect(Collectors.toList())`"]
    assert blocks[7] == {
        "type": "callout",
        "kind": "success",
        "text": "Since Java 16 `toList()` is shorter than `collect(Collectors.toList())`.",
    }
    assert blocks[8]["kind"] == "warning" and blocks[8]["text"].startswith("A stream cannot")
    assert blocks[9] == {
        "type": "quote",
        "text": "Premature optimisation is the root of all evil.",
        "by": "Donald Knuth",
    }
    _valid(blocks)
    assert parsed.notes == []


def test_the_closing_sections_become_takeaways_and_exercises() -> None:
    body, takeaways, exercises = split_closing_sections(parse_markdown(JAVA_CHAPTER).blocks)
    assert takeaways == ["Streams are lazy", "One terminal operation per stream"]
    assert exercises[0].startswith("Print the squares") and len(exercises) == 2
    assert body[-1]["type"] == "quote"  # nothing else was lifted out
    # a Key takeaways heading followed by prose is NOT lifted: no text is lost
    mixed = parse_markdown("## Key takeaways\n\nRemember this paragraph.\n").blocks
    assert split_closing_sections(mixed)[0] == mixed


def test_page_break_rule_and_hinglish_headings() -> None:
    blocks = parse_markdown(
        "# Shuruaat\n\n<!-- pagebreak -->\n\n---\n\n#### Chhota heading\n"
    ).blocks
    assert _types(blocks) == ["heading", "page_break", "heading"]  # the rule has no block
    assert blocks[0]["level"] == 1 and blocks[2]["level"] == 3  # level 4+ becomes 3


# --------------------------------------------------------------------------- repairs
def test_a_ragged_table_is_repaired_not_rejected() -> None:
    text = "| A | B | C |\n|---|---|---|\n| 1 | 2 |\n| 3 | 4 | 5 | 6 | 7 |\n| 8 | 9 | 10 |\n"
    parsed = parse_markdown(text)
    (table,) = parsed.blocks
    assert table["headers"] == ["A", "B", "C"]
    assert table["rows"] == [["1", "2", ""], ["3", "4", "5 | 6 | 7"], ["8", "9", "10"]]
    assert parsed.notes and "repaired" in parsed.notes[0]
    _valid(parsed.blocks)


def test_a_table_without_a_separator_row_stays_text_and_a_pipe_in_code_is_not_a_cell() -> None:
    plain = parse_markdown("a | b\nc | d\n").blocks
    assert _types(plain) == ["paragraph"]
    cells = parse_markdown("| Expr | Meaning |\n|--|--|\n| `a \\| b` | or |\n| `x|y` | pipe |\n")
    assert cells.blocks[0]["rows"] == [["`a | b`", "or"], ["`x|y`", "pipe"]]


def test_an_unclosed_code_fence_keeps_the_rest_as_code_and_says_so() -> None:
    parsed = parse_markdown("Intro text.\n\n```python\nprint('a')\nfor i in range(3):\n    pass\n")
    assert _types(parsed.blocks) == ["paragraph", "code"]
    assert parsed.blocks[1]["language"] == "python" and "range(3)" in parsed.blocks[1]["text"]
    assert any("not closed" in n for n in parsed.notes)
    _valid(parsed.blocks)


def test_the_whole_answer_in_a_markdown_fence_is_unwrapped_but_inner_code_is_kept() -> None:
    wrapped = "```markdown\n## Title\n\ntext\n\n```java\nint a;\n```\n```"
    assert unwrap_markdown_fence(wrapped).startswith("## Title")
    assert unwrap_markdown_fence("```java\nint a;\n```") == "```java\nint a;\n```"


def test_a_leading_json_plan_is_split_off_and_a_json_code_example_is_not() -> None:
    plan, body = split_plan(
        '```json\n{"title": "Java", "design": {"base": "notes"}}\n```\n## A\n\nx'
    )
    assert plan == {"title": "Java", "design": {"base": "notes"}}
    assert body.lstrip().startswith("## A")
    example = '```json\n{"name": "maya", "id": 3}\n```\n\ntext'
    assert split_plan(example) == (None, example)
    assert split_plan("```json\nnot json\n```\ntext")[0] is None
    assert split_plan("") == (None, "")


def test_limits_of_the_block_model_are_met_by_splitting_never_by_cutting() -> None:
    long_paragraph = " ".join(f"word{i}." for i in range(2500))  # about 17,000 characters
    blocks = parse_markdown(long_paragraph).blocks
    assert len(blocks) >= 4 and all(len(b["text"]) <= doc_model.MAX_PARAGRAPH_CHARS for b in blocks)
    assert " ".join(b["text"] for b in blocks).split() == long_paragraph.split()

    items = NL.join(f"- item{i}" for i in range(120))
    lists = parse_markdown(items).blocks
    assert [len(b["items"]) for b in lists] == [50, 50, 20]

    rows = NL.join(f"| r{i} | v{i} |" for i in range(450))
    tables = parse_markdown("| K | V |\n|--|--|\n" + rows).blocks
    assert [len(t["rows"]) for t in tables] == [200, 200, 50]
    assert all(t["headers"] == ["K", "V"] for t in tables)

    code = "```go\n" + NL.join(f"x{i} := {i}" for i in range(2000)) + "\n```"
    chunks = parse_markdown(code).blocks
    assert len(chunks) > 1 and all(len(c["text"]) <= doc_model.MAX_CODE_CHARS for c in chunks)
    assert NL.join(c["text"] for c in chunks).count(":=") == 2000

    wide = "| " + " | ".join(f"h{i}" for i in range(20)) + " |\n|" + "--|" * 20 + "\n"
    table = parse_markdown(wide + "| " + " | ".join(f"c{i}" for i in range(20)) + " |").blocks[0]
    assert len(table["headers"]) == doc_model.MAX_TABLE_COLS
    long_cell = parse_markdown("| A |\n|--|\n| " + "word " * 200 + "|").blocks
    assert _types(long_cell) == ["table", "paragraph"]  # the overflow of a cell is kept
    _valid(blocks + lists + tables + chunks + long_cell)


def test_split_text_cuts_at_sentences_then_words_then_inside_a_giant_word() -> None:
    assert split_text("", 10) == []
    pieces = split_text("One sentence here. Another sentence there. Third.", 25)
    assert all(len(p) <= 25 for p in pieces) and " ".join(pieces).count("sentence") == 2
    giant = split_text("x" * 100, 30)
    assert "".join(giant) == "x" * 100 and all(len(p) <= 30 for p in giant)


def test_windows_line_endings_and_a_bold_label_callout() -> None:
    blocks = parse_markdown(
        "## A\r\n\r\nline one\r\nline two\r\n\r\n> **Note:** careful here\r\n"
    ).blocks
    assert _types(blocks) == ["heading", "paragraph", "callout"]
    assert blocks[1]["text"] == "line one line two"
    assert blocks[2]["kind"] == "info" and "careful here" in blocks[2]["text"]


def test_an_image_line_degrades_to_text_when_its_source_is_not_allowed() -> None:
    blocks = parse_markdown("![diagram of a stream](https://example.com/x.png)\n").blocks
    assert blocks == [{"type": "paragraph", "text": "diagram of a stream"}]
    ok = parse_markdown("![diagram](assets/stream.png)\n").blocks
    assert ok[0]["type"] == "image"


# --------------------------------------------------------------------------- lossless (property)
_WORDS = (
    "stream map filter lambda collect reduce thread lock aaj hum seekhenge bilkul simple "
    "क्लास ऑब्जेक्ट "
    "यह café naïve data x1 y22 value_2 order"
).split()


def _words(rng: random.Random, low: int, high: int) -> list[str]:
    return [rng.choice(_WORDS) for _ in range(rng.randint(low, high))]


def _tokens(text: str) -> list[str]:
    return re.findall(r"\w+", text)


def _generate(rng: random.Random) -> tuple[str, list[str]]:
    """A random Markdown document and the ordered words it contains (syntax excluded)."""
    parts: list[str] = []
    expected: list[str] = []

    def add(markup: str, words: list[str]) -> None:
        parts.append(markup)
        expected.extend(words)

    for _ in range(rng.randint(1, 14)):
        kind = rng.choice(
            ["heading", "para", "para", "bullets", "numbered", "code", "table", "callout", "quote"]
        )
        if kind == "heading":
            w = _words(rng, 1, 6)
            add("#" * rng.randint(1, 4) + " " + " ".join(w), w)
        elif kind == "para":
            lines, w = [], []
            for _l in range(rng.randint(1, 4)):
                line = _words(rng, 2, 12)
                w += line
                lines.append(" ".join(line))
            add(NL.join(lines), w)
        elif kind in ("bullets", "numbered"):
            lines, w = [], []
            for n in range(rng.randint(1, 6)):
                item = _words(rng, 1, 8)
                w += item
                depth = "  " if n and rng.random() < 0.3 else ""
                marker = "-" if kind == "bullets" else f"{n + 1}."
                if depth:
                    marker = "-"
                lines.append(f"{depth}{marker} " + " ".join(item))
            add(NL.join(lines), w)
        elif kind == "code":
            body, w = [], []
            for _l in range(rng.randint(1, 8)):
                line = _words(rng, 1, 6)
                w += line
                body.append(rng.choice(["", "    ", "        "]) + "(" + " ".join(line) + ");")
            add("```java\n" + NL.join(body) + "\n```", w)
        elif kind == "table":
            width = rng.randint(1, 5)
            header = _words(rng, width, width)
            lines = ["| " + " | ".join(header) + " |", "|" + "---|" * width]
            w = list(header)
            for _r in range(rng.randint(0, 5)):
                row = _words(rng, 1, width + 2)
                w += row
                lines.append("| " + " | ".join(row) + " |")
            add(NL.join(lines), w)
        elif kind == "callout":
            w = _words(rng, 2, 15)
            tag = rng.choice(["NOTE", "TIP", "WARNING", "IMPORTANT"])
            add(f"> [!{tag}]\n> " + " ".join(w), w)
        else:
            w = _words(rng, 2, 15)
            add("> " + " ".join(w), w)
    # Devanagari vowel signs are not \w characters: compare the way the blocks are compared.
    return "\n\n".join(parts) + NL, _tokens(" ".join(expected))


def _block_tokens(blocks: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for b in blocks:
        kind = b["type"]
        if kind in ("heading", "paragraph", "callout", "quote", "code"):
            out += _tokens(b["text"])
        elif kind in ("bullets", "numbered"):
            out += [t for item in b["items"] for t in _tokens(item)]
        elif kind == "table":
            out += [
                t
                for cell in [*b["headers"], *(c for r in b["rows"] for c in r)]
                for t in _tokens(cell)
            ]
    return out


@pytest.mark.parametrize("seed", range(250))
def test_every_word_of_the_input_ends_up_in_a_block_in_order(seed: int) -> None:
    text, expected = _generate(random.Random(seed))
    parsed = parse_markdown(text)
    assert _block_tokens(parsed.blocks) == expected, text
    _valid(parsed.blocks)


@pytest.mark.parametrize("seed", range(120))
def test_garbage_never_raises_and_always_validates(seed: int) -> None:
    rng = random.Random(10_000 + seed)
    alphabet = ["#", "##", "*", "**", "-", "_", ">", "|", "`", "```", "~~~", "[", "]", "(", ")"]
    alphabet += ["!", "1.", "2)", "\t", "  ", NL, NL, NL, "word", "हिंदी"]
    alphabet += ["[!NOTE]", "|---|", "---", "<!-- pagebreak -->", "\r\n", "\\|", "![a](b.png)"]
    text = "".join(
        rng.choice(alphabet) + rng.choice(["", " ", " "]) for _ in range(rng.randint(0, 300))
    )
    parsed = parse_markdown(text)
    _valid(parsed.blocks)
    assert all(isinstance(b, dict) and "type" in b for b in parsed.blocks)


def test_none_and_empty_input_give_no_blocks() -> None:
    assert parse_markdown("").blocks == []
    assert parse_markdown("   \n\n\t\n").blocks == []
    assert parse_markdown(None).blocks == []  # type: ignore[arg-type]
