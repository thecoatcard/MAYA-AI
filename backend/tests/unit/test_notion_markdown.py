"""Markdown -> Notion blocks (and back): a golden study note, a round trip and the edge cases.

Pure functions, no Notion, no network.
"""

from __future__ import annotations

import copy
import itertools
from typing import Any

from app.mcp.notion_markdown import (
    blocks_to_markdown,
    leftover_markdown,
    markdown_to_blocks,
    notion_language,
    rich_text_from_markdown,
)
from app.mcp.notion_write import block_view, compare_blocks, validate_requests

NOTE = """# Java Collections Framework

Short intro with **bold**, *italic*, ~~old~~ and `ArrayList` plus a [Oracle docs](https://docs.oracle.com/javase/).

> [!TIP] Prefer interfaces
> Declare variables as `List`, not `ArrayList`.

## Lists
- **ArrayList**: resizable array
  - fast random access
  - slow inserts in the middle
    - amortised O(1) append
- `LinkedList`: doubly linked
- [x] read the docs
- [ ] practise

### Steps to iterate
1. Create the list
2. Use a for-each loop
   ```java
   for (String s : names) {
       System.out.println(s);
   }
   ```
3. Done

```python
def total(xs):
    return sum(xs)
```

| Type | Ordered | Duplicates |
|------|:-------:|-----------|
| List | yes | **yes** |
| Set  | no | no |

---

> Plain quote line

<details>
<summary>More</summary>

Hidden **text**

</details>

$$ E = mc^2 $$

Hinglish: yeh *bahut* zaroori hai - कलेक्शन्स \U0001f642
"""


def compact(block: dict[str, Any]) -> dict[str, Any]:
    """type, plain text with its annotations/links and the type's own settings, children nested."""
    kind = block["type"]
    data = block[kind]
    out: dict[str, Any] = {"type": kind}
    if "rich_text" in data:
        out["text"] = [
            (
                i["text"]["content"],
                sorted(i.get("annotations", {})),
                i["text"].get("link", {}).get("url"),
            )
            for i in data["rich_text"]
        ]
    for key, value in data.items():
        if key not in ("rich_text", "children"):
            out[key] = value
    kids = data.get("children")
    if kind == "table":
        out["rows"] = [
            ["".join(p["text"]["content"] for p in cell) for cell in r["table_row"]["cells"]]
            for r in kids
        ]
    elif kids:
        out["children"] = [compact(k) for k in kids]
    return out


def plain(content: str) -> list[tuple[str, list[str], None]]:
    return [(content, [], None)]


GOLDEN: list[dict[str, Any]] = [
    {"type": "heading_1", "text": plain("Java Collections Framework")},
    {
        "type": "paragraph",
        "text": [
            ("Short intro with ", [], None),
            ("bold", ["bold"], None),
            (", ", [], None),
            ("italic", ["italic"], None),
            (", ", [], None),
            ("old", ["strikethrough"], None),
            (" and ", [], None),
            ("ArrayList", ["code"], None),
            (" plus a ", [], None),
            ("Oracle docs", [], "https://docs.oracle.com/javase/"),
            (".", [], None),
        ],
    },
    {
        "type": "callout",
        "text": [
            ("Prefer interfaces\nDeclare variables as ", [], None),
            ("List", ["code"], None),
            (", not ", [], None),
            ("ArrayList", ["code"], None),
            (".", [], None),
        ],
        "icon": {"type": "emoji", "emoji": "\U0001f4a1"},
        "color": "green_background",
    },
    {"type": "heading_2", "text": plain("Lists")},
    {
        "type": "bulleted_list_item",
        "text": [("ArrayList", ["bold"], None), (": resizable array", [], None)],
        "children": [
            {"type": "bulleted_list_item", "text": plain("fast random access")},
            {"type": "bulleted_list_item", "text": plain("slow inserts in the middle")},
            # the third level is flattened into the second, with indentation text
            {"type": "bulleted_list_item", "text": plain(" amortised O(1) append")},
        ],
    },
    {
        "type": "bulleted_list_item",
        "text": [("LinkedList", ["code"], None), (": doubly linked", [], None)],
    },
    {"type": "to_do", "text": plain("read the docs"), "checked": True},
    {"type": "to_do", "text": plain("practise"), "checked": False},
    {"type": "heading_3", "text": plain("Steps to iterate")},
    {"type": "numbered_list_item", "text": plain("Create the list")},
    {
        "type": "numbered_list_item",
        "text": plain("Use a for-each loop"),
        "children": [
            {
                "type": "code",
                "text": plain("for (String s : names) {\n    System.out.println(s);\n}"),
                "language": "java",
            }
        ],
    },
    {"type": "numbered_list_item", "text": plain("Done")},
    {
        "type": "code",
        "text": plain("def total(xs):\n    return sum(xs)"),
        "language": "python",
    },
    {
        "type": "table",
        "table_width": 3,
        "has_column_header": True,
        "has_row_header": False,
        "rows": [["Type", "Ordered", "Duplicates"], ["List", "yes", "yes"], ["Set", "no", "no"]],
    },
    {"type": "divider"},
    {"type": "quote", "text": plain("Plain quote line")},
    {
        "type": "toggle",
        "text": plain("More"),
        "children": [
            {
                "type": "paragraph",
                "text": [("Hidden ", [], None), ("text", ["bold"], None)],
            }
        ],
    },
    {"type": "equation", "expression": "E = mc^2"},
    {
        "type": "paragraph",
        "text": [
            ("Hinglish: yeh ", [], None),
            ("bahut", ["italic"], None),
            (" zaroori hai - कलेक्शन्स \U0001f642", [], None),
        ],
    },
]


def test_a_realistic_ai_study_note_converts_to_the_exact_blocks() -> None:
    blocks = markdown_to_blocks(NOTE)
    assert [compact(b) for b in blocks] == GOLDEN


def test_the_exact_json_of_formatted_text_has_annotations_and_links() -> None:
    assert rich_text_from_markdown("a **b** [c](https://x.io)") == [
        {"type": "text", "text": {"content": "a "}},
        {"type": "text", "text": {"content": "b"}, "annotations": {"bold": True}},
        {"type": "text", "text": {"content": " "}},
        {"type": "text", "text": {"content": "c", "link": {"url": "https://x.io"}}},
    ]


def test_no_raw_markers_are_left_in_converted_text() -> None:
    blocks = markdown_to_blocks(NOTE)
    assert leftover_markdown(blocks) == []
    text = " ".join(str(compact(b)) for b in blocks if b["type"] not in ("code", "equation"))
    assert "**" not in text and "~~" not in text and "](" not in text


def test_converted_blocks_pass_the_approval_gate_validation() -> None:
    blocks = markdown_to_blocks(NOTE)
    page = "a" * 32
    request = {"method": "PATCH", "path": f"/blocks/{page}/children", "body": {"children": blocks}}
    assert validate_requests("notion_append_blocks", [request])


# ----------------------------------------------------------------------------------- round trip


def _as_read(blocks: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The blocks as Notion returns them (ids, plain_text, nested children apart)."""
    counter = itertools.count(1)
    tree: dict[str, list[dict[str, Any]]] = {}

    def convert(block: dict[str, Any]) -> dict[str, Any]:
        out = copy.deepcopy(block)
        kind = out["type"]
        data = out[kind]
        out["id"] = f"{next(counter):032x}"
        for key in ("rich_text", "caption"):
            for item in data.get(key) or []:
                item["plain_text"] = item["text"]["content"]
                if item["text"].get("link"):
                    item["href"] = item["text"]["link"]["url"]
        kids = data.pop("children", None)
        if kids:
            out["has_children"] = True
            if kind == "table":
                tree[out["id"]] = [
                    {**row, "id": f"{next(counter):032x}"} for row in copy.deepcopy(kids)
                ]
                for row in tree[out["id"]]:
                    for cell in row["table_row"]["cells"]:
                        for item in cell:
                            item["plain_text"] = item["text"]["content"]
                            if item["text"].get("link"):
                                item["href"] = item["text"]["link"]["url"]
            else:
                tree[out["id"]] = [convert(k) for k in kids]
        return out

    return [convert(b) for b in blocks], tree


def test_blocks_to_markdown_and_back_gives_the_same_blocks() -> None:
    original = markdown_to_blocks(NOTE)
    read, tree = _as_read(original)
    markdown = "\n".join(blocks_to_markdown(read, lambda b: tree.get(str(b["id"]), [])))
    again = markdown_to_blocks(markdown)
    assert [compact(b) for b in again] == [compact(b) for b in original]


def test_read_markdown_has_no_unnamed_blocks_for_known_types() -> None:
    read, tree = _as_read(markdown_to_blocks(NOTE))
    text = "\n".join(blocks_to_markdown(read, lambda b: tree.get(str(b["id"]), [])))
    assert "not shown" not in text
    assert "- [x] read the docs" in text and "```java" in text and "> [!TIP]" in text


# ---------------------------------------------------------------------------------- edge cases


def test_empty_and_blank_input_give_no_blocks() -> None:
    assert markdown_to_blocks("") == []
    assert markdown_to_blocks("  \n\n \t\n") == []


def test_an_unclosed_fence_keeps_the_rest_as_code() -> None:
    blocks = markdown_to_blocks("intro\n\n```python\nx = 1\ny = 2")
    assert [b["type"] for b in blocks] == ["paragraph", "code"]
    assert blocks[1]["code"]["language"] == "python"
    assert blocks[1]["code"]["rich_text"][0]["text"]["content"] == "x = 1\ny = 2"


def test_a_ragged_table_gets_a_consistent_width() -> None:
    blocks = markdown_to_blocks("| A | B | C |\n|---|---|---|\n| 1 |\n| 1 | 2 | 3 | 4 |")
    table = blocks[0]["table"]
    assert table["table_width"] == 4
    assert all(len(r["table_row"]["cells"]) == 4 for r in table["children"])


def test_a_long_table_is_split_with_the_header_repeated() -> None:
    rows = "\n".join(f"| r{i} | v |" for i in range(150))
    blocks = markdown_to_blocks(f"| h | x |\n|---|---|\n{rows}")
    assert len(blocks) == 2
    assert all(len(b["table"]["children"]) <= 100 for b in blocks)
    assert compact(blocks[1])["rows"][0] == ["h", "x"]


def test_emoji_and_devanagari_survive_and_long_text_is_split_at_2000_units() -> None:
    long = ("क" * 1500) + ("\U0001f642" * 400)  # 1500 + 800 UTF-16 units
    items = rich_text_from_markdown(long)
    assert "".join(i["text"]["content"] for i in items) == long
    assert all(len(i["text"]["content"].encode("utf-16-le")) // 2 <= 2000 for i in items)
    assert len(items) == 2


def test_a_3000_character_paragraph_is_one_block_of_two_rich_text_items() -> None:
    para = "word " * 600  # 3000 characters
    blocks = markdown_to_blocks(para.strip())
    assert len(blocks) == 1 and len(blocks[0]["paragraph"]["rich_text"]) == 2
    joined = "".join(i["text"]["content"] for i in blocks[0]["paragraph"]["rich_text"])
    assert joined == para.strip()


def test_a_link_or_code_span_is_not_split_when_it_fits() -> None:
    text = ("a" * 1990) + " [short link](https://x.io) `code`"
    items = rich_text_from_markdown(text)
    links = [i for i in items if i["text"].get("link")]
    assert len(links) == 1 and links[0]["text"]["content"] == "short link"
    assert any(i.get("annotations") == {"code": True} for i in items)


def test_a_list_of_more_than_100_items_converts_in_full() -> None:
    blocks = markdown_to_blocks("\n".join(f"- item {i}" for i in range(150)))
    assert len(blocks) == 150 and all(b["type"] == "bulleted_list_item" for b in blocks)


def test_more_than_100_rich_text_items_split_the_block_instead_of_breaking_the_limit() -> None:
    text = " ".join(f"**w{i}** x" for i in range(120))  # 240+ items
    blocks = markdown_to_blocks(text)
    assert len(blocks) >= 3
    assert all(len(b["paragraph"]["rich_text"]) <= 100 for b in blocks)


def test_snake_case_and_literal_markers_are_not_formatting() -> None:
    items = rich_text_from_markdown("use my_var_name and 2 * 3 * 4 and `**x**`")
    assert items[0]["text"]["content"].startswith("use my_var_name and 2 * 3 * 4")
    assert {"annotations": {"code": True}}.items() <= {
        k: v for k, v in items[-1].items() if k == "annotations"
    }.items()
    assert items[-1]["text"]["content"] == "**x**"


def test_escaped_markers_stay_literal_and_headings_deeper_than_three_become_heading_3() -> None:
    assert rich_text_from_markdown(r"\*not italic\*")[0]["text"]["content"] == "*not italic*"
    blocks = markdown_to_blocks("##### Deep")
    assert blocks[0]["type"] == "heading_3"


def test_code_languages_map_to_notions_enum() -> None:
    assert notion_language("py") == "python"
    assert notion_language("JS") == "javascript"
    assert notion_language("c++") == "c++"
    assert notion_language("somethingnew") == "plain text"
    assert notion_language("") == "plain text"


def test_deep_nesting_is_flattened_to_two_levels() -> None:
    blocks = markdown_to_blocks("- a\n  - b\n    - c\n      - d")
    assert len(blocks) == 1
    level2 = blocks[0]["bulleted_list_item"]["children"]
    assert [b["bulleted_list_item"]["rich_text"][0]["text"]["content"].strip() for b in level2] == [
        "b",
        "c",
        "d",
    ]
    assert all("children" not in b["bulleted_list_item"] for b in level2)


def test_images_need_https_and_a_table_needs_its_separator_row() -> None:
    ok = markdown_to_blocks("![chart](https://example.org/c.png)")
    assert ok[0]["image"]["external"]["url"] == "https://example.org/c.png"
    bad = markdown_to_blocks("![chart](http://example.org/c.png)")
    assert bad[0]["type"] == "paragraph"
    assert markdown_to_blocks("a | b\nc | d")[0]["type"] == "paragraph"


def test_leftover_markdown_is_flagged_but_code_is_exempt() -> None:
    sent = [
        {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "see **this**"}]}},
        {"type": "code", "code": {"rich_text": [{"plain_text": "## not a heading"}]}},
        {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "## Heading text"}]}},
        {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "fine"}]}},
    ]
    found = leftover_markdown(sent)
    assert len(found) == 2 and "block 1" in found[0] and "block 3" in found[1]


def test_block_views_of_converted_blocks_compare_equal_to_themselves() -> None:
    read, _tree = _as_read(markdown_to_blocks(NOTE))
    assert compare_blocks(markdown_to_blocks(NOTE), read) == []
    assert block_view(read[0])["type"] == "heading_1"


def test_a_contents_marker_becomes_the_table_of_contents_block() -> None:
    blocks = markdown_to_blocks("Intro line\n\n[toc]\n\n# Chapter 1: A\n\n[TOC]\n")
    assert [b["type"] for b in blocks] == [
        "paragraph",
        "table_of_contents",
        "heading_1",
        "table_of_contents",
    ]
    assert blocks[1]["table_of_contents"] == {"color": "default"}
    assert markdown_to_blocks("a [toc] in a sentence")[0]["type"] == "paragraph"  # only a line
