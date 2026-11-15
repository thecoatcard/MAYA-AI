"""Deterministic row generation for spreadsheets: the model describes a table, this builds it.

A model cannot write 500 rows into a JSON tool call reliably (it truncates and the user gets 5 rows
instead of 500). A sheet spec may therefore carry ``row_count``, a ``seed`` and, per column, a
``generate`` object. No model call and no code execution is involved: every value comes from a
seeded ``random.Random`` per column, so the same spec always gives the same file.

Generator kinds (``GENERATOR_KINDS``):

* ``sequence``: ``{prefix, start, step, width, suffix}`` -> ``P-1001``, ``P-1002`` ... (a number
  when there is no prefix, suffix or width);
* ``choice``: ``{values: [...], weights?: [...]}``;
* ``range_int`` / ``range_float``: ``{min, max, decimals}``;
* ``date_range``: ``{start, end, step_days | random}`` (ISO dates);
* ``name_pool``: ``{pool, unique?, of?}`` from the small built-in pools (``POOL_NAMES``);
  ``product_categories`` with ``of`` names the column of products and gives each its category;
* ``formula``: ``{template}`` such as ``=D{row}*E{row}`` (``{row}`` is the sheet row, ``{Units}`` is
  the column letter of the column with that header). The value Python expects is computed too;
* ``copy_of``: ``{column}`` copies another column (header or letter).

Rows the model writes by hand (``rows``) are kept first. Everything here is pure (no I/O).
"""

from __future__ import annotations

import math
import random
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from openpyxl.utils import column_index_from_string, get_column_letter

MAX_ROWS = 50_000
MAX_COLUMNS = 50
MAX_CELLS = 1_000_000
DEFAULT_SEED = 20_240_601
GENERATOR_KINDS: tuple[str, ...] = (
    "sequence",
    "choice",
    "range_int",
    "range_float",
    "date_range",
    "name_pool",
    "formula",
    "copy_of",
)
CURRENCY_SYMBOLS: tuple[str, ...] = ("$", "₹", "€", "£")
TOTAL_MODES: tuple[str, ...] = ("sum", "average", "none")
MAX_CHOICES = 500
MAX_DATE_SPAN_DAYS = 36_500
# Generators whose values are invented sample content (an id counter or a formula is not).
SYNTHETIC_KINDS: frozenset[str] = frozenset(
    {"choice", "range_int", "range_float", "date_range", "name_pool"}
)

_BAD_FORMULA = re.compile(
    r"(?i)\b(WEBSERVICE|HYPERLINK|CALL|REGISTER|EXEC|FILTERXML|RTD|DDE|SHELL)\s*\("
    r"|\[|\||\\\\|https?:|file:"
)
_REF = re.compile(r"(?<![A-Za-z0-9_.$])\$?([A-Z]{1,3})\$?(\d+)(?![A-Za-z0-9_(])")
_PLACEHOLDER = re.compile(r"\{([^{}]+)\}")


class SheetSpecError(ValueError):
    """The sheet spec cannot be built. The message is plain words the model can act on."""


# ------------------------------------------------------------------------------------- pools
_PRODUCT_GROUPS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "Electronics": (
        (
            "Mouse",
            "Keyboard",
            "Headphones",
            "Speaker",
            "Power Bank",
            "Webcam",
            "USB Hub",
            "Smart Watch",
        ),
        (
            "Wireless",
            "Bluetooth",
            "Gaming",
            "Compact",
            "Portable",
            "Noise-Cancelling",
            "HD",
            "Slim",
        ),
    ),
    "Home & Kitchen": (
        (
            "Water Bottle",
            "Lunch Box",
            "Kettle",
            "Mixer Grinder",
            "Pressure Cooker",
            "Knife Set",
            "Storage Jar",
            "Dinner Set",
        ),
        (
            "Stainless Steel",
            "Insulated",
            "Non-Stick",
            "Glass",
            "Premium",
            "Family-Size",
            "Eco",
            "Classic",
        ),
    ),
    "Office": (
        (
            "Office Chair",
            "Desk Lamp",
            "Laptop Stand",
            "File Organizer",
            "Whiteboard",
            "Desk Mat",
            "Monitor Riser",
            "Footrest",
        ),
        ("Ergonomic", "Adjustable", "Foldable", "Wooden", "Mesh", "LED", "Heavy-Duty", "Compact"),
    ),
    "Fitness": (
        (
            "Yoga Mat",
            "Dumbbell Set",
            "Skipping Rope",
            "Resistance Bands",
            "Gym Bag",
            "Foam Roller",
            "Shaker Bottle",
            "Ab Wheel",
        ),
        ("Anti-Slip", "Adjustable", "Pro", "Lightweight", "Rubber", "Training", "Premium", "Home"),
    ),
    "Fashion": (
        (
            "Backpack",
            "Wallet",
            "Sunglasses",
            "Belt",
            "Cap",
            "Wrist Watch",
            "Scarf",
            "Laptop Sleeve",
        ),
        ("Leather", "Canvas", "Classic", "Urban", "Polarized", "Casual", "Travel", "Slim"),
    ),
    "Stationery": (
        (
            "Notebook",
            "Gel Pen Pack",
            "Sticky Notes",
            "Marker Set",
            "Planner",
            "Highlighter Set",
            "Sketchbook",
            "Desk Organizer",
        ),
        ("Ruled", "Hardcover", "Pastel", "Premium", "A5", "Spiral", "Recycled", "Student"),
    ),
    "Personal Care": (
        (
            "Face Wash",
            "Shampoo",
            "Body Lotion",
            "Hair Dryer",
            "Trimmer",
            "Sunscreen",
            "Lip Balm",
            "Toothbrush Set",
        ),
        (
            "Herbal",
            "Daily",
            "Gentle",
            "Rechargeable",
            "Natural",
            "Moisturising",
            "Travel",
            "Family",
        ),
    ),
    "Toys & Games": (
        (
            "Board Game",
            "Puzzle",
            "Building Blocks",
            "Remote Car",
            "Art Kit",
            "Chess Set",
            "Card Game",
            "Soft Toy",
        ),
        ("Educational", "Wooden", "Kids", "Family", "Classic", "Deluxe", "Travel", "Creative"),
    ),
}
_PRODUCT_EDITIONS: tuple[str, ...] = ("", "Pro", "Plus", "Lite", "Max", "Mini")
_FIRST_NAMES: tuple[str, ...] = tuple(
    "Aarav Vivaan Aditya Arjun Rohan Karan Rahul Amit Vikram Sanjay Rajesh Suresh Manish Nikhil "
    "Siddharth Harsh Pranav Kunal Deepak Varun Ishaan Yash Mohit Tarun Gaurav Ankit Naveen "
    "Ananya Diya Priya Neha Pooja Kavya Riya Sneha Isha Meera Nisha Divya Shreya Anjali Swati "
    "Aditi Tanvi Sakshi Pallavi Radhika Simran Jyoti Kiran Lakshmi Sunita Rekha Geeta Mansi "
    "James Oliver Henry Daniel Michael Thomas Samuel David Lucas Ethan Emma Sophia Olivia Grace "
    "Hannah Emily Chloe Lily Ava Zoe Mia Ruby Nora Claire".split()
)
_LAST_NAMES: tuple[str, ...] = tuple(
    "Sharma Verma Gupta Singh Kumar Patel Shah Mehta Joshi Reddy Nair Iyer Menon Rao Das Bose "
    "Chopra Kapoor Malhotra Agarwal Bansal Jain Saxena Mishra Pandey Yadav Chauhan Thakur Kulkarni "
    "Deshmukh Pillai Banerjee Mukherjee Ghosh Sinha Tiwari Dubey Khan Ahmed "
    "Smith Johnson Brown Taylor Wilson Clark Walker Hall Young King Wright Turner "
    "Hughes Carter".split()
)
_CITIES: tuple[str, ...] = tuple(
    name.replace("-", " ")
    for name in (
        "Mumbai Delhi Bengaluru Hyderabad Chennai Kolkata Pune Ahmedabad Jaipur Lucknow Kanpur "
        "Nagpur Indore Bhopal Patna Surat Vadodara Chandigarh Coimbatore Kochi Visakhapatnam "
        "Guwahati Bhubaneswar Ranchi Raipur Dehradun Mysuru Nashik Agra Varanasi Amritsar "
        "Ludhiana Noida Gurugram Thane Madurai Jodhpur Udaipur Vijayawada Mangaluru "
        "London Singapore Dubai Toronto Sydney New-York Berlin Paris Tokyo Seoul Chicago Dublin "
        "Melbourne Amsterdam Zurich Hong-Kong"
    ).split()
)
_COUNTRIES: tuple[str, ...] = (
    "India",
    "United States",
    "United Kingdom",
    "Canada",
    "Australia",
    "Germany",
    "France",
    "Singapore",
    "United Arab Emirates",
    "Japan",
    "South Korea",
    "Brazil",
    "South Africa",
    "Netherlands",
    "Italy",
    "Spain",
    "Sweden",
    "Switzerland",
    "Mexico",
    "Indonesia",
)
_COMPANY_PREFIXES: tuple[str, ...] = tuple(
    "Apex Bright Cobalt Delta Evergreen Falcon Global Horizon Indigo Jupiter Keystone Lotus "
    "Meridian Nova Orbit Pioneer Quantum Reliable Sterling Titan Unity Vertex Zenith Banyan "
    "Crescent Everest Harbor Lakshya Prime Summit".split()
)
_COMPANY_SUFFIXES: tuple[str, ...] = (
    "Technologies",
    "Industries",
    "Traders",
    "Solutions",
    "Enterprises",
    "Logistics",
    "Foods",
    "Textiles",
    "Systems",
    "Retail",
)
_DEPARTMENTS: tuple[str, ...] = (
    "Sales",
    "Marketing",
    "Finance",
    "Human Resources",
    "Operations",
    "Engineering",
    "Customer Support",
    "Procurement",
    "Legal",
    "IT",
    "Product",
    "Logistics",
)
_COLORS: tuple[str, ...] = (
    "Black",
    "White",
    "Blue",
    "Red",
    "Green",
    "Grey",
    "Navy",
    "Maroon",
    "Yellow",
    "Orange",
    "Pink",
    "Brown",
)


def _build_products() -> tuple[tuple[str, ...], dict[str, str]]:
    names: list[str] = []
    category_of_base: dict[str, str] = {}
    for category, (nouns, _adjectives) in _PRODUCT_GROUPS.items():
        for noun in nouns:
            category_of_base[noun] = category
    for edition in _PRODUCT_EDITIONS:
        for nouns, adjectives in _PRODUCT_GROUPS.values():
            for adjective in adjectives:
                for noun in nouns:
                    names.append(" ".join(p for p in (adjective, noun, edition) if p))
    # Deduplicate, keeping the first of each name.
    return tuple(dict.fromkeys(names)), category_of_base


_PRODUCTS, _PRODUCT_BASE_CATEGORY = _build_products()
_PRODUCT_CATEGORIES: tuple[str, ...] = tuple(_PRODUCT_GROUPS)
_FULL_NAME_SPACE = len(_FIRST_NAMES) * len(_LAST_NAMES)
_COMPANY_SPACE = len(_COMPANY_PREFIXES) * len(_COMPANY_SUFFIXES)

# pool -> (values or None for the product-derived ones, unique by default)
_FLAT_POOLS: dict[str, tuple[tuple[str, ...], bool]] = {
    "products": (_PRODUCTS, True),
    "product_categories": (_PRODUCT_CATEGORIES, False),
    "first_names": (_FIRST_NAMES, False),
    "last_names": (_LAST_NAMES, False),
    "cities": (_CITIES, False),
    "countries": (_COUNTRIES, False),
    "departments": (_DEPARTMENTS, False),
    "colors": (_COLORS, False),
}
POOL_NAMES: tuple[str, ...] = (*_FLAT_POOLS, "full_names", "companies")
"""The built-in pools of ``name_pool`` (the tool schema lists exactly these)."""
PRODUCT_POOL_SIZE = len(_PRODUCTS)


def pool_sizes() -> dict[str, int]:
    sizes = {name: len(values) for name, (values, _unique) in _FLAT_POOLS.items()}
    sizes["full_names"] = _FULL_NAME_SPACE
    sizes["companies"] = _COMPANY_SPACE
    return sizes


def _unique_values(pool: str, count: int, rng: random.Random) -> list[str]:
    """``count`` distinct values (a seeded permutation of the pool; past its size a numbered
    series continues it, so a long column never repeats awkwardly)."""
    if pool == "full_names":
        space = [f"{a} {b}" for a in _FIRST_NAMES for b in _LAST_NAMES]
    elif pool == "companies":
        space = [f"{a} {b}" for a in _COMPANY_PREFIXES for b in _COMPANY_SUFFIXES]
    else:
        space = list(_FLAT_POOLS[pool][0])
    order = list(range(len(space)))
    rng.shuffle(order)
    out: list[str] = []
    for i in range(count):
        cycle, position = divmod(i, len(space))
        base = space[order[position]]
        out.append(base if cycle == 0 else f"{base} Series {cycle + 1}")
    return out


def _random_values(pool: str, count: int, rng: random.Random) -> list[str]:
    values = _FLAT_POOLS[pool][0]
    return [rng.choice(values) for _ in range(count)]


def category_for_product(name: str) -> str:
    """The category of a generated product name (by its base noun); ``"Other"`` if unknown."""
    text = str(name)
    best = ""
    for base in _PRODUCT_BASE_CATEGORY:
        if base in text and len(base) > len(best):
            best = base
    return _PRODUCT_BASE_CATEGORY.get(best, "Other")


# ------------------------------------------------------------------- the row formula evaluator
_TOKEN = re.compile(
    r"\s*(?:(?P<num>\d+(?:\.\d+)?)|(?P<ref>\$?[A-Z]{1,3}\$?\d+)|(?P<func>[A-Z]+)\(|(?P<op>[-+*/^(),]))"
)


class _Unsupported(Exception):
    pass


def _round_half_up(value: float, digits: int) -> float:
    quantum = Decimal(1).scaleb(-digits)
    return float(Decimal(repr(value)).quantize(quantum, rounding=ROUND_HALF_UP))


def eval_row_formula(formula: str, row: int, cells: Mapping[str, Any]) -> float | None:
    """The value of a simple row formula (``=D2*E2``, ``=ROUND(D2*1.18,2)``) for the numbers in
    ``cells`` (column letter -> value of THIS row), or ``None`` when it uses anything else (another
    row, text, a function this evaluator does not know) or divides by zero."""
    text = formula.strip()
    if not text.startswith("="):
        return None
    source = text[1:]
    position = 0
    tokens: list[tuple[str, str]] = []
    while position < len(source):
        match = _TOKEN.match(source, position)
        if match is None:
            if source[position:].strip() == "":
                break
            return None
        position = match.end()
        for kind in ("num", "ref", "func", "op"):
            if match.group(kind) is not None:
                tokens.append((kind, match.group(kind)))
                break
    index = 0

    def peek() -> tuple[str, str] | None:
        return tokens[index] if index < len(tokens) else None

    def take() -> tuple[str, str]:
        nonlocal index
        if index >= len(tokens):
            raise _Unsupported
        index += 1
        return tokens[index - 1]

    def expr() -> float:
        value = term()
        while (t := peek()) in (("op", "+"), ("op", "-")):
            take()
            right = term()
            value = value + right if t == ("op", "+") else value - right
        return value

    def term() -> float:
        value = unary()
        while (t := peek()) in (("op", "*"), ("op", "/")):
            take()
            right = unary()
            if t == ("op", "/"):
                if right == 0:
                    raise _Unsupported
                value /= right
            else:
                value *= right
        return value

    def unary() -> float:
        if peek() == ("op", "-"):
            take()
            return -unary()
        if peek() == ("op", "+"):
            take()
            return unary()
        base = atom()
        if peek() == ("op", "^"):
            take()
            return float(base ** unary())
        return base

    def atom() -> float:
        kind, text_ = take()
        if kind == "num":
            return float(text_)
        if kind == "ref":
            match = re.fullmatch(r"\$?([A-Z]{1,3})\$?(\d+)", text_)
            assert match is not None
            if int(match.group(2)) != row:
                raise _Unsupported
            value = cells.get(match.group(1))
            if isinstance(value, bool) or not isinstance(value, int | float):
                raise _Unsupported
            return float(value)
        if kind == "func":
            args = [expr()]
            while peek() == ("op", ","):
                take()
                args.append(expr())
            if take() != ("op", ")"):
                raise _Unsupported
            return _apply_function(text_, args)
        if (kind, text_) == ("op", "("):
            value = expr()
            if take() != ("op", ")"):
                raise _Unsupported
            return value
        raise _Unsupported

    try:
        result = expr()
        if index != len(tokens):
            return None
    except (_Unsupported, OverflowError, ZeroDivisionError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _apply_function(name: str, args: list[float]) -> float:
    if name == "ROUND" and len(args) == 2:
        return _round_half_up(args[0], int(args[1]))
    if name == "ABS" and len(args) == 1:
        return abs(args[0])
    if name == "MIN" and args:
        return min(args)
    if name == "MAX" and args:
        return max(args)
    if name == "SUM" and args:
        return sum(args)
    if name == "AVERAGE" and args:
        return sum(args) / len(args)
    raise _Unsupported


# ------------------------------------------------------------------------------- the prepared sheet
@dataclass
class ColumnInfo:
    """What the engine needs to know about one column beyond its cells."""

    header: str
    kind: str = "given"  # a generator kind, or ``given`` (the model wrote the cells)
    synthetic: bool = False
    choices: list[str] | None = None  # a closed list of text values (for data validation)
    low: float | None = None
    high: float | None = None
    decimals: int | None = None
    fmt: str | None = None  # an explicit number format
    total: str | None = None  # ``sum`` | ``average`` | ``none`` | None (decide from the data)
    expected: list[Any] | None = None  # the Python value of every cell of a formula column
    unique: bool = False


@dataclass
class PreparedSheet:
    title: str
    headers: list[str]
    rows: list[list[Any]]
    columns: list[ColumnInfo]
    formats: dict[int, str] = field(default_factory=dict)  # 1-based column -> number format
    anchors: int = 0  # rows the model wrote by hand (kept first)
    generated: bool = False  # any generate object was used
    synthetic: bool = False  # invented sample content (not just ids and formulas)
    requested_rows: int | None = None
    notes: list[str] = field(default_factory=list)
    currency: str | None = None

    @property
    def row_count(self) -> int:
        return len(self.rows)

    def column_letter(self, index: int) -> str:
        return str(get_column_letter(index + 1))


def _num(value: Any, what: str, column: str) -> float:
    if isinstance(value, bool):
        raise SheetSpecError(f"Column '{column}': {what} must be a number.")
    try:
        number = float(str(value).replace(",", "")) if isinstance(value, str) else float(value)
    except (TypeError, ValueError):
        raise SheetSpecError(f"Column '{column}': {what} must be a number.") from None
    if not math.isfinite(number):
        raise SheetSpecError(f"Column '{column}': {what} must be a finite number.")
    return number


def _iso(value: Any, what: str, column: str) -> date:
    text = str(value or "").strip()[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise SheetSpecError(
            f"Column '{column}': {what} must be an ISO date such as 2025-01-31."
        ) from None


def _resolve_ref(raw: Any, headers: Sequence[str], column: str) -> int:
    """The 0-based column index a ``column`` / ``of`` reference names (header or letter)."""
    text = str(raw or "").strip()
    lowered = [h.strip().lower() for h in headers]
    if text.lower() in lowered:
        return lowered.index(text.lower())
    if re.fullmatch(r"[A-Za-z]{1,3}", text):
        index = column_index_from_string(text.upper()) - 1
        if 0 <= index < len(headers):
            return int(index)
    raise SheetSpecError(f"Column '{column}' refers to '{text}', which is not a column header.")


def _formula_template(template: str, headers: Sequence[str], column: str) -> str:
    """The template with ``{Header}`` swapped for the column letter (``{row}`` stays)."""
    text = template.strip()
    if not text.startswith("="):
        raise SheetSpecError(f"Column '{column}': a formula template must start with '='.")
    if len(text) > 500 or _BAD_FORMULA.search(text):
        raise SheetSpecError(f"Column '{column}': that formula is not allowed.")
    lowered = [h.strip().lower() for h in headers]

    def swap(match: re.Match[str]) -> str:
        name = match.group(1).strip()
        if name.lower() == "row":
            return "{row}"
        if name.lower() in lowered:
            return str(get_column_letter(lowered.index(name.lower()) + 1))
        raise SheetSpecError(
            f"Column '{column}': {{{name}}} in the formula is not a column header (use {{row}} "
            "for the row number)."
        )

    return _PLACEHOLDER.sub(swap, text)


def _deps(template: str) -> set[int]:
    return {
        column_index_from_string(m.group(1)) - 1
        for m in _REF.finditer(template.replace("{row}", "1"))
    }


def _generate(
    kind: str,
    spec: Mapping[str, Any],
    column: str,
    count: int,
    seed_text: str,
) -> tuple[list[Any], ColumnInfo]:
    """Values ``[0, count)`` for the independent generators (no reference to another column)."""
    rng = random.Random(seed_text)
    info = ColumnInfo(header=column, kind=kind, synthetic=kind in SYNTHETIC_KINDS)
    if kind == "sequence":
        prefix = str(spec.get("prefix") or "")
        suffix = str(spec.get("suffix") or "")
        start = _num(spec.get("start", 1), "start", column)
        step = _num(spec.get("step", 1), "step", column)
        width = int(_num(spec.get("width", 0), "width", column))
        if start != int(start) or step != int(step):
            raise SheetSpecError(f"Column '{column}': a sequence needs whole numbers.")
        numbers = [int(start) + int(step) * i for i in range(count)]
        info.unique = step != 0
        if not (prefix or suffix or width):
            info.fmt = "0"
            return list(numbers), info
        pad = max(0, min(width, 12))
        return [f"{prefix}{str(n).zfill(pad)}{suffix}" for n in numbers], info
    if kind == "choice":
        raw = spec.get("values")
        if not isinstance(raw, list) or not raw:
            raise SheetSpecError(f"Column '{column}': choice needs a 'values' list.")
        if len(raw) > MAX_CHOICES:
            raise SheetSpecError(f"Column '{column}': at most {MAX_CHOICES} choice values.")
        values = [
            v if isinstance(v, int | float) and not isinstance(v, bool) else str(v) for v in raw
        ]
        weights_raw = spec.get("weights")
        weights: list[float] | None = None
        if weights_raw is not None:
            if not isinstance(weights_raw, list) or len(weights_raw) != len(values):
                raise SheetSpecError(
                    f"Column '{column}': 'weights' must match 'values' one to one."
                )
            weights = [_num(w, "a weight", column) for w in weights_raw]
            if any(w < 0 for w in weights) or sum(weights) <= 0:
                raise SheetSpecError(f"Column '{column}': weights must be positive.")
        picked = rng.choices(values, weights=weights, k=count)
        if all(isinstance(v, str) for v in values):
            info.choices = list(dict.fromkeys(str(v) for v in values))
        return picked, info
    if kind in ("range_int", "range_float"):
        low = _num(spec.get("min", 0), "min", column)
        high = _num(spec.get("max", 100), "max", column)
        if low > high:
            raise SheetSpecError(f"Column '{column}': min is above max.")
        info.low, info.high = low, high
        if kind == "range_int":
            lo_i, hi_i = math.ceil(low), math.floor(high)
            if lo_i > hi_i:
                raise SheetSpecError(f"Column '{column}': no whole number between min and max.")
            return [rng.randint(lo_i, hi_i) for _ in range(count)], info
        decimals = int(_num(spec.get("decimals", 2), "decimals", column))
        decimals = max(0, min(decimals, 6))
        info.decimals = decimals
        return [round(rng.uniform(low, high), decimals) for _ in range(count)], info
    if kind == "date_range":
        first = _iso(spec.get("start"), "start", column)
        last = _iso(spec.get("end", spec.get("start")), "end", column)
        if last < first:
            raise SheetSpecError(f"Column '{column}': the end date is before the start date.")
        span = (last - first).days
        if span > MAX_DATE_SPAN_DAYS:
            raise SheetSpecError(f"Column '{column}': the date range is longer than 100 years.")
        step_days = spec.get("step_days")
        if step_days is not None and not spec.get("random"):
            step = int(_num(step_days, "step_days", column))
            if step < 0:
                raise SheetSpecError(f"Column '{column}': step_days must not be negative.")
            return [first + timedelta(days=(i * step) % (span + 1)) for i in range(count)], info
        return [first + timedelta(days=rng.randint(0, span)) for _ in range(count)], info
    if kind == "name_pool":
        pool = str(spec.get("pool") or "")
        if pool not in POOL_NAMES:
            raise SheetSpecError(
                f"Column '{column}': pool '{pool}' is unknown. Use one of: {', '.join(POOL_NAMES)}."
            )
        unique_default = (
            pool in ("full_names", "companies") or _FLAT_POOLS.get(pool, ((), False))[1]
        )
        unique = bool(spec.get("unique", unique_default))
        if pool in ("full_names", "companies") or (unique and pool in _FLAT_POOLS):
            names = _unique_values(pool, count, rng)
            info.unique = True
        elif pool in _FLAT_POOLS:
            names = _random_values(pool, count, rng)
        else:  # pragma: no cover - POOL_NAMES is exhaustive
            raise SheetSpecError(f"Column '{column}': pool '{pool}' is unknown.")
        if pool == "product_categories":
            info.choices = list(_PRODUCT_CATEGORIES)
        return list(names), info
    raise SheetSpecError(
        f"Column '{column}': generator '{kind}' is unknown. Use one of: "
        f"{', '.join(GENERATOR_KINDS)}."
    )


def _column_specs(sheet: Mapping[str, Any], headers: list[str]) -> list[dict[str, Any]]:
    """One option dict per header, from ``columns`` (matched by header, else by position) and the
    lenient ``generators`` map (header -> generate)."""
    specs: list[dict[str, Any]] = [{} for _ in headers]
    lowered = [h.strip().lower() for h in headers]
    raw_columns = sheet.get("columns") or []
    if isinstance(raw_columns, list):
        for position, entry in enumerate(raw_columns):
            if not isinstance(entry, Mapping):
                continue
            name = str(entry.get("header") or entry.get("name") or "").strip().lower()
            index = lowered.index(name) if name in lowered else position
            if index < len(specs):
                specs[index] = {**specs[index], **dict(entry)}
    generators = sheet.get("generators")
    if isinstance(generators, Mapping):
        for name, gen in generators.items():
            key = str(name).strip().lower()
            if key in lowered and isinstance(gen, Mapping):
                specs[lowered.index(key)]["generate"] = dict(gen)
    return specs


def _clean_number_format(value: Any) -> str | None:
    text = str(value or "").strip()
    return text[:60] if text else None


def prepare_sheet(
    sheet: Mapping[str, Any],
    *,
    requested_rows: int | None = None,
    default_seed: int = DEFAULT_SEED,
    rows_range: tuple[int, int] | None = None,
    allow_reduce: bool = False,
) -> PreparedSheet:
    """Expand a sheet spec into headers, rows and column facts. Raises :class:`SheetSpecError`.

    ``requested_rows`` is the row count the USER stated: when every column can be generated and the
    spec asks for fewer rows, the sheet is extended to it (the stated number is a requirement).
    ``allow_reduce`` (the main sheet): a sheet with MORE rows than asked is cut down to the stated
    count, so a count mismatch never leaves an over-long file. ``rows_range`` ("50-100 rows",
    "about 100 rows") is the span accepted as is; extending and cutting happen only outside it.
    """
    title = str(sheet.get("title") or "")
    headers = [str(h) for h in (sheet.get("headers") or [])]
    anchors = [list(r) for r in (sheet.get("rows") or []) if isinstance(r, list | tuple)]
    column_specs = _column_specs(sheet, headers)
    if not headers and column_specs and any(c.get("header") for c in column_specs):
        headers = [str(c.get("header") or "") for c in column_specs]
    width = max([len(headers), *[len(r) for r in anchors]], default=0)
    headers += [""] * (width - len(headers))
    column_specs += [{} for _ in range(width - len(column_specs))]
    if width > MAX_COLUMNS:
        raise SheetSpecError(
            f"Sheet '{title}' has {width} columns; at most {MAX_COLUMNS} are allowed."
        )
    for row in anchors:
        row.extend([None] * (width - len(row)))

    gens: dict[int, tuple[str, dict[str, Any]]] = {}
    for index, entry in enumerate(column_specs):
        raw = entry.get("generate")
        if raw is None:
            continue
        if not isinstance(raw, Mapping):
            raise SheetSpecError(f"Column '{headers[index]}': 'generate' must be an object.")
        kind = str(raw.get("kind") or "")
        if kind not in GENERATOR_KINDS:
            raise SheetSpecError(
                f"Column '{headers[index] or index + 1}': generator kind '{kind}' is unknown. Use "
                f"one of: {', '.join(GENERATOR_KINDS)}."
            )
        gens[index] = (kind, dict(raw))

    raw_count = sheet.get("row_count")
    count: int
    if raw_count is None or raw_count == "":
        if gens and not anchors:
            raise SheetSpecError(
                f"Sheet '{title}' describes columns with generators but gives no 'row_count'. "
                "Add 'row_count' (the number of data rows)."
            )
        count = len(anchors)
    else:
        count = int(_num(raw_count, "row_count", title))
        if count < 0:
            raise SheetSpecError("row_count must not be negative.")
    notes: list[str] = []
    low_rows, high_rows = rows_range or (requested_rows or 0, requested_rows or 0)
    if (
        requested_rows
        and gens
        and count < max(low_rows, 1)
        and all(i in gens for i in range(width))
    ):
        notes.append(
            f"row_count was {count}; extended to the {requested_rows} rows the user asked for"
        )
        count = requested_rows
    shown = count if gens else max(count, len(anchors))
    if requested_rows and allow_reduce and shown > max(high_rows, 1):
        # More rows than the user stated: cut to the stated count (rows are dropped, never the
        # file). Hand-written rows keep their order, so the first rows stay.
        notes.append(
            f"the sheet had {shown} rows; reduced to the {requested_rows} rows the user asked for"
        )
        count = requested_rows
        anchors = anchors[:requested_rows]
    if count > MAX_ROWS:
        raise SheetSpecError(
            f"Sheet '{title}' asks for {count:,} rows; at most {MAX_ROWS:,} rows are allowed."
        )
    if count * max(width, 1) > MAX_CELLS:
        raise SheetSpecError(
            f"Sheet '{title}' would have {count * width:,} cells; at most {MAX_CELLS:,} are "
            "allowed."
        )
    count = max(count, len(anchors)) if not gens else count
    if len(anchors) > count:
        anchors = anchors[:count]
        notes.append(f"only the first {count} hand-written rows were kept (row_count)")
    if count > len(anchors):
        missing = [headers[i] or f"column {i + 1}" for i in range(width) if i not in gens]
        if missing:
            raise SheetSpecError(
                f"Sheet '{title}' needs {count} rows but only {len(anchors)} are written and "
                f"these columns have no 'generate': {', '.join(missing[:6])}. Describe every "
                "column with a generator (sequence, choice, range_int, range_float, date_range, "
                "name_pool, formula or copy_of) instead of listing the rows."
            )
    seed_raw = sheet.get("seed")
    seed = int(_num(seed_raw, "seed", title)) if seed_raw not in (None, "") else default_seed

    start_row = 2 if any(headers) else 1
    columns = [ColumnInfo(header=headers[i]) for i in range(width)]
    values: dict[int, list[Any]] = {}
    # Stage 1: independent generators.
    for index in [i for i, (k, _s) in gens.items() if k not in ("formula", "copy_of", "name_pool")]:
        kind, spec = gens.pop(index)
        label = headers[index] or f"column {index + 1}"
        made, info = _generate(kind, spec, label, count, f"{seed}:{title}:{index}:{kind}")
        info.header = headers[index]
        columns[index] = info
        values[index] = made
    for index in [i for i, (k, s) in gens.items() if k == "name_pool" and not s.get("of")]:
        kind, spec = gens.pop(index)
        label = headers[index] or f"column {index + 1}"
        made, info = _generate(kind, spec, label, count, f"{seed}:{title}:{index}:{kind}")
        info.header = headers[index]
        columns[index] = info
        values[index] = made
    # Stage 2: columns that read another column (copy_of, name_pool of, formula), in order.
    templates: dict[int, str] = {}

    def current(c: int, i: int) -> Any:
        """The final value of column ``c`` in data row ``i`` (a hand-written cell wins)."""
        if i < len(anchors) and anchors[i][c] not in (None, "") and columns[c].kind != "formula":
            return anchors[i][c]
        if c in values:
            if columns[c].kind == "formula":
                return (columns[c].expected or [])[i] if columns[c].expected else None
            return values[c][i]
        return None

    for index, (kind, spec) in list(gens.items()):
        label = headers[index] or f"column {index + 1}"
        if kind == "formula":
            templates[index] = _formula_template(str(spec.get("template") or ""), headers, label)
    progress = True
    while gens and progress:
        progress = False
        for index, (kind, spec) in list(gens.items()):
            label = headers[index] or f"column {index + 1}"
            if kind == "copy_of":
                source = _resolve_ref(spec.get("column"), headers, label)
                if source == index:
                    raise SheetSpecError(f"Column '{label}' cannot be a copy of itself.")
                if source in gens:
                    continue
                made = list(values[source])
                values[index] = made
                columns[index] = ColumnInfo(
                    header=headers[index],
                    kind="copy_of",
                    choices=columns[source].choices,
                    low=columns[source].low,
                    high=columns[source].high,
                    decimals=columns[source].decimals,
                    expected=list(columns[source].expected or []) or None,
                    synthetic=columns[source].synthetic,
                )
            elif kind == "name_pool":
                source = _resolve_ref(spec.get("of"), headers, label)
                if source in gens:
                    continue
                pool = str(spec.get("pool") or "")
                if pool != "product_categories":
                    raise SheetSpecError(
                        f"Column '{label}': 'of' is only used with the pool 'product_categories'."
                    )
                values[index] = [category_for_product(v) for v in values[source]]
                columns[index] = ColumnInfo(
                    header=headers[index],
                    kind="name_pool",
                    synthetic=True,
                    choices=[*_PRODUCT_CATEGORIES, "Other"],
                )
            else:  # formula
                template = templates[index]
                if any(d in gens and d != index for d in _deps(template)):
                    continue
                if index in _deps(template):
                    raise SheetSpecError(f"Column '{label}': a formula cannot use its own column.")
                formulas: list[str] = []
                expected: list[Any] = []
                for i in range(count):
                    sheet_row = start_row + i
                    formulas.append(template.replace("{row}", str(sheet_row)))
                    cells = {
                        get_column_letter(c + 1): _as_number(current(c, i))
                        for c in range(width)
                        if c != index
                    }
                    expected.append(eval_row_formula(formulas[-1], sheet_row, cells))
                values[index] = formulas
                columns[index] = ColumnInfo(
                    header=headers[index],
                    kind="formula",
                    expected=expected,
                )
            gens.pop(index)
            progress = True
    if gens:
        names = ", ".join(headers[i] or f"column {i + 1}" for i in gens)
        raise SheetSpecError(f"Columns {names} refer to each other in a circle.")

    # Per-column options (explicit number format, total mode) and anchors before generated cells.
    formats: dict[int, str] = {}
    for index, entry in enumerate(column_specs):
        info = columns[index]
        fmt = _clean_number_format(entry.get("number_format") or entry.get("format"))
        if fmt:
            info.fmt = fmt
        total = str(entry.get("total") or "").strip().lower()
        if total in TOTAL_MODES:
            info.total = total
        if info.fmt:
            formats[index + 1] = info.fmt
    rows: list[list[Any]] = []
    for i in range(count):
        if i < len(anchors):
            row = list(anchors[i])
            for c, made in values.items():
                if columns[c].kind == "formula" or row[c] in (None, ""):
                    row[c] = made[i]
        else:
            row = [values[c][i] if c in values else None for c in range(width)]
        rows.append(row)
    explicit = {int(k): str(v) for k, v in (sheet.get("number_formats") or {}).items()}
    formats.update(explicit)
    currency = str(sheet.get("currency") or "").strip()
    prepared = PreparedSheet(
        title=title,
        headers=headers,
        rows=rows,
        columns=columns,
        formats=formats,
        anchors=len(anchors),
        generated=any(c.kind != "given" for c in columns),
        synthetic=any(c.synthetic for c in columns),
        requested_rows=requested_rows,
        notes=notes,
        currency=currency if currency in CURRENCY_SYMBOLS else None,
    )
    return prepared


def _as_number(value: Any) -> Any:
    """A numeric value for the formula evaluator: numbers pass, number-like text is converted."""
    if isinstance(value, str):
        try:
            return float(value.replace(",", ""))
        except ValueError:
            return value
    return value


def column_summary(prepared: PreparedSheet) -> list[dict[str, Any]]:
    """Compact facts of the generated columns (for tool results and tests)."""
    return [
        {"header": c.header, "kind": c.kind, "synthetic": c.synthetic}
        for c in prepared.columns
        if c.kind != "given"
    ]


GENERATOR_PROMPT = (
    "Describe a big table, never list hundreds of rows (a model cannot write them reliably and "
    "the file would be short). Set 'row_count' (at most "
    f"{MAX_ROWS:,}), optionally a 'seed', and per column in 'columns' (matched by 'header') a "
    f"'generate' object whose 'kind' is one of: {', '.join(GENERATOR_KINDS)}. sequence: prefix, "
    "start, step, width (ids like P-1001). choice: values, weights. range_int / range_float: min, "
    "max, decimals. date_range: start, end (ISO), step_days or random. name_pool: pool from "
    f"{', '.join(POOL_NAMES)} (product_categories with 'of' set to the products column gives each "
    "product its category). formula: template like =D{row}*E{row} ({row} is the sheet row). "
    "copy_of: column. A few hand-written 'rows' may be given as examples; they stay first. Every "
    "column needs a generator when row_count is larger than the rows you wrote. Add a category "
    "column (choice or product_categories) so the Summary sheet can group. Use a currency symbol "
    "(sheet 'currency': $, ₹, € or £) ONLY when the user named one. The engine adds a total row, "
    "an Excel table, validation and a Summary sheet with charts by itself. Generated content is "
    "synthetic sample data: say so, and say how many rows were generated."
)
