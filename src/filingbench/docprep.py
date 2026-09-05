"""Turn a filing's HTML into what a human reader sees.

The single most important step in this project happens here. Every 10-K in the
sample from 2019 onwards is inline XBRL, which means the filer already wrapped
each reportable number in a tag naming the exact accounting concept, for
example

    <ix:nonFraction name="us-gaap:Assets" scale="6" ...>364,980</ix:nonFraction>

That tag is the same information the ground truth comes from. If an extractor
could see it, a four line regular expression would score one hundred percent
and the benchmark would measure nothing at all. So the inline XBRL header is
removed and every ix: element is unwrapped down to its visible text before
either extractor sees the document. Both methods read the rendered page, the
way a person would.
"""

from __future__ import annotations

import re
import warnings
from dataclasses import dataclass, field

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

IX_HEADER_RE = re.compile(r"<ix:header\b.*?</ix:header>", re.IGNORECASE | re.DOTALL)
IX_HIDDEN_RE = re.compile(r"<ix:hidden\b.*?</ix:hidden>", re.IGNORECASE | re.DOTALL)
IX_TAG_RE = re.compile(r"</?ix:[a-zA-Z]+\b[^>]*>")
XBRL_TAG_RE = re.compile(r"</?(xbrli|xbrldi|link|xlink|iso4217|xbrl):[a-zA-Z]+\b[^>]*>")

SCALE_WORDS = {"thousand": 1_000, "thousands": 1_000,
               "million": 1_000_000, "millions": 1_000_000,
               "billion": 1_000_000_000, "billions": 1_000_000_000}

SCALE_RE = re.compile(r"(?P<qualifier>[a-z ]{0,24}?)\bin\s+(?P<word>thousands?|millions?|billions?)\b",
                      re.IGNORECASE)
# Inside a table the scale often appears as a bare caption cell, "(Millions,
# except per share amounts)", with no preposition at all. A pattern that loose
# would misfire on running prose, so it is only ever applied to caption cells.
CAPTION_SCALE_RE = re.compile(
    r"(?P<qualifier>[a-z ]{0,24}?)\b(?:in\s+)?(?P<word>thousands?|millions?|billions?)\b",
    re.IGNORECASE)
CAPTION_SCAN_ROWS = 4
NON_DOLLAR_QUALIFIER = re.compile(r"(share|unit|ounce|barrel|tonne|ton|gallon|pound|euro)s?\s*,?\s*$",
                                  re.IGNORECASE)

# Matched against the caption with every space removed. Filing HTML routinely
# splits a heading across elements mid word, so 3M's 2019 report renders its
# income statement title as "Consolidated Statement of Incom e". Matching on
# the squashed text reads that correctly and costs nothing, where a
# whitespace tolerant pattern for every heading would be unreadable.
STATEMENT_PATTERNS = {
    "income_statement": re.compile(
        r"consolidated(andcombined)?statements?of"
        r"(operations|income|earnings|comprehensiveincome)", re.IGNORECASE),
    "balance_sheet": re.compile(
        r"consolidated(andcombined)?(balancesheets?|statements?offinancialposition)",
        re.IGNORECASE),
    "cash_flow": re.compile(
        r"consolidated(andcombined)?statements?ofcashflows?", re.IGNORECASE),
}
SQUASH_RE = re.compile(r"\s+")
# The scale caption sits immediately above a table, but the statement title can
# be a page header several elements further up, so the two are read from
# different sized windows.
TITLE_WINDOW = 3000

MONTHS = ("january february march april may june july august september "
          "october november december").split()
MONTH_INDEX = {m: i + 1 for i, m in enumerate(MONTHS)}
MONTH_ABBR = {m[:3]: i + 1 for i, m in enumerate(MONTHS)}

FULL_DATE_RE = re.compile(
    r"\b(?P<month>[A-Za-z]{3,9})\.?\s+(?P<day>\d{1,2})\s*,?\s*(?P<year>(19|20)\d{2})\b")
YEAR_ONLY_RE = re.compile(r"^\(?\s*(?P<year>(19|20)\d{2})\s*\)?$")
PERIOD_PHRASE_RE = re.compile(
    r"(year|years|fiscal year|twelve months|52 weeks|53 weeks)\s+ended\s+"
    r"(?P<month>[A-Za-z]{3,9})\.?\s*(?P<day>\d{1,2})?", re.IGNORECASE)
AS_OF_PHRASE_RE = re.compile(
    r"(as of|at)\s+(?P<month>[A-Za-z]{3,9})\.?\s*(?P<day>\d{1,2})?", re.IGNORECASE)

NUMBER_RE = re.compile(r"^\(?-?\$?\s*\d[\d,]*(\.\d+)?\)?$")


def strip_inline_xbrl(html: str) -> str:
    """Remove every trace of the filer's machine tagging, keep the visible text."""
    out = IX_HEADER_RE.sub(" ", html)
    out = IX_HIDDEN_RE.sub(" ", out)
    out = IX_TAG_RE.sub("", out)
    out = XBRL_TAG_RE.sub("", out)
    return out


def count_inline_xbrl_facts(html: str) -> int:
    return len(re.findall(r"<ix:nonfraction\b", html, re.IGNORECASE))


@dataclass
class DocTable:
    index: int
    rows: list[list[str]]
    preamble: str
    statement: str | None
    scale: int
    scale_phrase: str | None

    @property
    def flat_text(self) -> str:
        return " ".join(" ".join(r) for r in self.rows)


@dataclass
class PreparedDocument:
    doc_id: str
    tables: list[DocTable] = field(default_factory=list)
    text: str = ""
    ix_facts_removed: int = 0
    raw_chars: int = 0
    stripped_chars: int = 0


def _clean_cell(text: str) -> str:
    text = text.replace("\xa0", " ").replace("’", "'").replace("‘", "'")
    # A statement prints a zero as a long dash, and a range with a short one.
    # Written as escapes so no such character appears literally in this repo.
    text = text.replace("\u2014", " ").replace("\u2013", "-").replace("\u2212", "-")
    return re.sub(r"\s+", " ", text).strip()


def detect_scale(preamble: str) -> tuple[int, str | None]:
    """Read the print scale from the caption above a table.

    A caption often reads "(In millions, except number of shares, which are
    reflected in thousands, and per-share amounts)". The first scale phrase
    that is not qualified by shares or another non currency unit is the one
    that applies to the dollar columns.
    """
    for match in SCALE_RE.finditer(preamble):
        qualifier = match.group("qualifier") or ""
        if NON_DOLLAR_QUALIFIER.search(qualifier):
            continue
        word = match.group("word").lower()
        return SCALE_WORDS[word], match.group(0).strip()
    return 1, None


def detect_scale_in_rows(rows: list[list[str]]) -> tuple[int, str | None]:
    """Read the print scale from a caption cell in the table's own first rows.

    Four of the twenty four companies in the sample put the caption inside the
    table rather than above it, and two of those write "(Millions)" with no
    preposition. Missing it turns every dollar figure in the filing into a
    value a thousand or a million times too small, which was the single largest
    source of error before this was handled.
    """
    for row in rows[:CAPTION_SCAN_ROWS]:
        for cell in row:
            if not cell or len(cell) > 160:
                continue
            for match in CAPTION_SCALE_RE.finditer(cell):
                qualifier = match.group("qualifier") or ""
                if NON_DOLLAR_QUALIFIER.search(qualifier):
                    continue
                return SCALE_WORDS[match.group("word").lower()], match.group(0).strip()
    return 1, None


def detect_statement(preamble: str) -> str | None:
    """The nearest statement title above a table, not merely the first found.

    The window reaches far enough up the page to catch a title separated from
    its table by a page header, which means it can also catch the title of the
    statement before. The balance sheet follows the income statement, so taking
    the first pattern that matches would label half the balance sheets as income
    statements. The last title in the text is the closest one to the table.
    """
    squashed = SQUASH_RE.sub("", preamble)
    best_name, best_position = None, -1
    for name, pattern in STATEMENT_PATTERNS.items():
        for match in pattern.finditer(squashed):
            if match.start() > best_position:
                best_name, best_position = name, match.start()
    return best_name


def _table_rows(table) -> list[list[str]]:
    rows: list[list[str]] = []
    for tr in table.find_all("tr"):
        if tr.find_parent("table") is not table:
            continue  # belongs to a nested table, handled on its own
        cells = []
        for cell in tr.find_all(["td", "th"]):
            if cell.find_parent("table") is not table:
                continue
            cells.append(_clean_cell(cell.get_text(" ", strip=True)))
        if any(c for c in cells):
            rows.append(cells)
    return rows


def _preamble_for(table, limit: int = 900) -> str:
    """The visible text immediately above a table, richest source of context."""
    chunks: list[str] = []
    total = 0
    node = table
    while total < limit:
        node = node.previous_element
        if node is None:
            break
        if getattr(node, "name", None) == "table":
            continue
        text = node.string if getattr(node, "string", None) else None
        if text is None and not hasattr(node, "name"):
            text = str(node)
        if not text:
            continue
        cleaned = _clean_cell(str(text))
        if not cleaned:
            continue
        chunks.append(cleaned)
        total += len(cleaned)
    return " ".join(reversed(chunks))[-limit:]


def prepare(html: str, doc_id: str = "") -> PreparedDocument:
    ix_facts = count_inline_xbrl_facts(html)
    stripped = strip_inline_xbrl(html)
    soup = BeautifulSoup(stripped, "lxml")

    for tag in soup.find_all(["script", "style", "sup"]):
        tag.decompose()

    doc = PreparedDocument(
        doc_id=doc_id,
        ix_facts_removed=ix_facts,
        raw_chars=len(html),
        stripped_chars=len(stripped),
    )

    for i, table in enumerate(soup.find_all("table")):
        rows = _table_rows(table)
        if not rows:
            continue
        preamble = _preamble_for(table)
        # The narrow window is tried first and almost always answers, because a
        # statement title normally sits directly above its table. The wide
        # window is a fallback for the filings that separate the two with a page
        # header, and it is only a fallback because reaching that far up can
        # instead find the title of the previous statement, or lend a note table
        # a caption it has not earned.
        statement = detect_statement(preamble)
        if statement is None:
            statement = detect_statement(_preamble_for(table, limit=TITLE_WINDOW))
        scale, phrase = detect_scale(preamble)
        if scale == 1:
            scale, phrase = detect_scale_in_rows(rows)
        doc.tables.append(
            DocTable(
                index=i,
                rows=rows,
                preamble=preamble,
                statement=statement,
                scale=scale,
                scale_phrase=phrase,
            )
        )

    text = soup.get_text("\n")
    text = re.sub(r"[ \t\xa0]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    doc.text = text.strip()
    return doc


def parse_number(token: str) -> float | None:
    """Parse one printed figure. Parentheses mean negative, as on a statement."""
    token = token.strip()
    if not token or not NUMBER_RE.match(token):
        return None
    negative = token.startswith("(") and token.endswith(")")
    cleaned = token.strip("()").replace("$", "").replace(",", "").strip()
    if not cleaned or cleaned in {"-", "."}:
        return None
    try:
        value = float(cleaned)
    except ValueError:
        return None
    return -value if negative else value


def row_values(cells: list[str]) -> list[float | None]:
    """Numbers in a statement row, in printed order.

    EDGAR splits a single printed figure across cells in several ways: a lone
    dollar sign in its own cell, an opening parenthesis apart from the digits,
    a closing parenthesis in the cell after. This reassembles them before
    parsing, which is the difference between reading a negative correctly and
    reading it as a positive.
    """
    tokens = [c for c in cells if c not in ("", "$")]
    merged: list[str] = []
    for token in tokens:
        if token == ")" and merged:
            merged[-1] = merged[-1] + ")"
            continue
        if token == "(":
            merged.append("(")
            continue
        if merged and merged[-1] == "(":
            merged[-1] = "(" + token
            continue
        merged.append(token)

    values: list[float | None] = []
    for token in merged:
        parsed = parse_number(token)
        if parsed is not None:
            values.append(parsed)
    return values


def parse_header_dates(rows: list[list[str]], max_rows: int = 6) -> list[tuple[int, str]]:
    """Map a column position to a full period end date where the header gives one."""
    found: list[tuple[int, str]] = []
    for row in rows[:max_rows]:
        position = 0
        for cell in row:
            if not cell or cell in ("$",):
                continue
            match = FULL_DATE_RE.search(cell)
            if match:
                iso = _to_iso(match.group("month"), match.group("day"), match.group("year"))
                if iso:
                    found.append((position, iso))
            position += 1
        if found:
            break
    return found


def parse_header_years(rows: list[list[str]], max_rows: int = 6) -> list[tuple[int, int]]:
    found: list[tuple[int, int]] = []
    for row in rows[:max_rows]:
        position = 0
        for cell in row:
            if not cell or cell == "$":
                continue
            match = YEAR_ONLY_RE.match(cell)
            if match:
                found.append((position, int(match.group("year"))))
            position += 1
        if found:
            break
    return found


def _to_iso(month: str, day: str | None, year: str | int) -> str | None:
    key = month.lower().rstrip(".")
    number = MONTH_INDEX.get(key) or MONTH_ABBR.get(key[:3])
    if not number or not day:
        return None
    try:
        return f"{int(year):04d}-{number:02d}-{int(day):02d}"
    except (TypeError, ValueError):
        return None
