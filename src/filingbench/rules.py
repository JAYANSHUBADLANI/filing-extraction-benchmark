"""Phase 2: the rules extractor.

This is meant to be a fair opponent, not a straw man. A weak baseline would
make any language model look good, so the baseline here does the four things a
competent hand written pipeline does:

  1. find the consolidated statement among the sixty or so tables in a filing,
     scoring candidates on both the caption above the table and the anchor rows
     inside it, because captions alone are unreliable,
  2. resolve the printed label to a target field through an ordered list of
     patterns held in configuration rather than in code,
  3. resolve which printed column is the filing's own reporting period, since
     an annual report prints two or three periods side by side,
  4. apply the print scale from the caption, since a statement in millions and
     a fact in units differ by a factor a reader never sees.

Every failure is labelled with the stage it happened at, so the error profile
of the method is reportable rather than a single accuracy number.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .config import FieldSpec, load_fields
from .docprep import (
    DocTable,
    PreparedDocument,
    parse_header_dates,
    parse_header_years,
    row_values,
)

ANCHORS: dict[str, tuple[str, ...]] = {
    "income_statement": (
        "cost of", "gross margin", "gross profit", "operating expenses",
        "net income", "net earnings", "per share", "income tax", "revenue",
        "net sales",
    ),
    "balance_sheet": (
        "total assets", "total current liabilities", "total current assets",
        "retained earnings", "accumulated deficit", "goodwill",
        "total liabilities and", "common stock", "accounts payable",
    ),
    "cash_flow": (
        "operating activities", "investing activities", "financing activities",
        "depreciation", "amortization", "net cash",
    ),
}

MIN_STATEMENT_SCORE = 4
# A consolidated statement prints at least two periods side by side, so it has
# several rows carrying two or more figures. A table of contents carries one
# page number per row and fails this outright, which is what it is for: 3M's
# 2019 filing has a contents page listing every note by name, and on anchor
# words alone it outscores the statement it points to.
MIN_COMPARATIVE_ROWS = 3

# Language that introduces a note table rather than a statement. Oracle's
# segment margin reconciliation and Tesla's stock compensation note both read
# like an income statement on vocabulary alone, and both are announced this way.
NOTE_LANGUAGE = re.compile(
    r"(the following tables?|reconcil|summari[sz]|disaggregat|by reportable segment|"
    r"by segment|consisted of the following|were as follows)", re.IGNORECASE)

# An interim column set. Meta's 10-K carries a quarterly results table whose
# rows are labelled exactly like the annual statement, and picking it returns a
# fourth quarter figure in place of a full year one.
INTERIM_HEADER = re.compile(
    r"(three|six|nine) months ended|quarter ended|quarterly", re.IGNORECASE)
CAPTION_BONUS = 4
PER_SHARE_MAX_ABS = 1_000.0

# A caption states the print scale, a column header states the periods. Neither
# is a section heading, and treating one as such is actively harmful: the
# caption "in millions, except per share data" sits above every row of an
# income statement, so reading it as a heading would veto the whole table for
# any field that excludes per share rows.
CAPTION_LIKE = re.compile(
    r"(thousand|million|billion|except|par value|in dollars|unaudited)",
    re.IGNORECASE)
HEADER_LIKE = re.compile(
    r"((19|20)\d{2}|january|february|march|april|may|june|july|august|"
    r"september|october|november|december)", re.IGNORECASE)

LABEL_CLEANERS = (
    (re.compile(r"\(\s*\d{1,2}\s*\)\s*$"), ""),   # parenthesised footnote marker
    (re.compile(r"\s*\.{2,}\s*$"), ""),            # leader dots
    (re.compile(r"[:\u2022]\s*$"), ""),              # trailing colon or bullet
    (re.compile(r"\s+"), " "),
    # A bare trailing digit on a multi word label is a footnote reference, as in
    # "Diluted Net Income Per Share 1". Only stripped when other words remain,
    # so a label that is genuinely a number is left alone.
    (re.compile(r"(?<=[a-z\)])\s+\d{1,2}\s*$"), ""),
    (re.compile(r"\s+"), " "),
)


@dataclass
class Extraction:
    field: str
    value: float | None
    status: str
    table_index: int | None = None
    matched_label: str | None = None
    scale: int | None = None
    column_index: int | None = None
    column_period: str | None = None
    column_basis: str | None = None


def normalise_label(text: str) -> str:
    label = text.strip().lower().replace("'", "'").replace("`", "'")
    for pattern, replacement in LABEL_CLEANERS:
        label = pattern.sub(replacement, label)
    return label.strip()


def row_label(cells: list[str]) -> str:
    for cell in cells:
        cleaned = cell.strip()
        if not cleaned or cleaned in ("$", "(", ")", "%"):
            continue
        return normalise_label(cleaned)
    return ""


def comparative_rows(table: DocTable) -> int:
    return sum(1 for row in table.rows if len(row_values(row)) >= 2)


def is_interim(table: DocTable) -> bool:
    head = " ".join(" ".join(row) for row in table.rows[:4])
    return bool(INTERIM_HEADER.search(head))


def score_table(table: DocTable, statement: str) -> int:
    score = CAPTION_BONUS if table.statement == statement else 0
    labels = " | ".join(row_label(r) for r in table.rows)
    score += sum(1 for anchor in ANCHORS[statement] if anchor in labels)
    if NOTE_LANGUAGE.search(table.preamble):
        score -= CAPTION_BONUS
    return score


def find_statement_table(doc: PreparedDocument, statement: str) -> DocTable | None:
    """The caption decides, the anchor rows only break ties.

    A note table that reuses statement vocabulary is the main false positive: a
    segment margin reconciliation names revenue, research and development and
    income before taxes, and on anchor words alone it can outscore the real
    income statement. So a table whose caption names the statement always beats
    one that merely reads like it, and the anchor score only orders tables
    within a tier. Earliest on the page breaks a remaining tie, because the
    consolidated statements precede the notes.
    """
    best: tuple[int, int, int, DocTable] | None = None
    for table in doc.tables:
        if comparative_rows(table) < MIN_COMPARATIVE_ROWS or is_interim(table):
            continue
        score = score_table(table, statement)
        if score < MIN_STATEMENT_SCORE:
            continue
        captioned = 0 if table.statement == statement else 1
        candidate = (captioned, -score, table.index, table)
        if best is None or candidate[:3] < best[:3]:
            best = candidate
    return best[3] if best else None


def resolve_columns(table: DocTable, report_date: str) -> tuple[int | None, str | None, str]:
    """Which printed column holds the filing's own period.

    Columns are counted in printed numeric order rather than by cell index,
    because a statement column spans a variable number of table cells once the
    dollar signs and parentheses get their own cells.
    """
    dates = parse_header_dates(table.rows)
    if dates:
        ordered = [iso for _, iso in dates]
        if report_date in ordered:
            return ordered.index(report_date), report_date, "header_full_date"
        return None, None, "header_date_no_match"

    years = parse_header_years(table.rows)
    if years:
        ordered = [year for _, year in years]
        target = int(report_date[:4])
        if target in ordered:
            return ordered.index(target), report_date, "header_year"
        # A fiscal year label is not a calendar year. A retailer whose year ends
        # on 28 January 2024 heads that column "Fiscal 2023". Where the newest
        # label is within a year of the period end, that column is this filing's
        # own period. Anything further apart is a genuine mismatch, not a
        # labelling convention, and stays unresolved.
        newest = max(ordered)
        if abs(newest - target) <= 1:
            return ordered.index(newest), report_date, "header_fiscal_year_label"
        return None, None, "header_year_no_match"

    return 0, report_date, "assumed_first_column"


def section_headings(table: DocTable) -> list[str]:
    """For each row, the nearest heading above it.

    A heading is a row that carries a label and no figures, which on a statement
    is exactly how a grouping like "Earnings per common share, diluted" is
    printed before the rows it governs.
    """
    headings: list[str] = []
    current = ""
    for cells in table.rows:
        label = row_label(cells)
        if (label and not row_values(cells)
                and not CAPTION_LIKE.search(label)
                and not HEADER_LIKE.search(label)):
            current = label
        headings.append(current)
    return headings


def excluded(label: str, spec: FieldSpec, heading: str = "") -> bool:
    """A row is vetoed by its own label or by the heading it sits under.

    Pfizer prints three rows with the identical label "Net income attributable
    to Pfizer Inc. common shareholders": the earnings figure, basic earnings per
    share, and diluted earnings per share. Nothing on those rows tells them
    apart. Only the heading above each does, so the veto has to read it.
    """
    if any(re.match(pattern, label) for pattern in spec.exclude_patterns):
        return True
    if heading and any(re.match(pattern, heading)
                       for pattern in spec.section_exclude_patterns):
        return True
    return False


def match_rows(table: DocTable, spec: FieldSpec) -> list[tuple[int, int, str]]:
    """Rows matching the field, ordered by pattern priority then page order.

    Row labels are tried first. Only if no row label matches at all does the
    section heading get a say, and then only the first row of figures beneath
    it, because a heading governs the block under it rather than any one line.
    """
    headings = section_headings(table)
    hits: list[tuple[int, int, str]] = []
    for row_index, cells in enumerate(table.rows):
        label = row_label(cells)
        if not label or excluded(label, spec, headings[row_index]):
            continue
        # A row carrying a label and no figures is a heading. Counting it as a
        # match would both return nothing and suppress the section fallback that
        # exists precisely to read the rows underneath it.
        if not row_values(cells):
            continue
        for priority, pattern in enumerate(spec.row_patterns):
            if re.match(pattern, label):
                hits.append((priority, row_index, label))
                break
    if hits or not spec.section_patterns:
        return sorted(hits)

    claimed: set[str] = set()
    for row_index, cells in enumerate(table.rows):
        heading = headings[row_index]
        if not heading or heading in claimed or not row_values(cells):
            continue
        # The vetoes apply here too. Under a heading of "Earnings per common
        # share, diluted" Pfizer prints continuing operations, discontinued
        # operations, then the total, and only the total is the field wanted.
        if excluded(row_label(cells), spec, heading):
            continue
        for priority, pattern in enumerate(spec.section_patterns):
            if re.match(pattern, heading):
                claimed.add(heading)
                hits.append((len(spec.row_patterns) + priority, row_index,
                             f"[section] {heading}"))
                break
    return sorted(hits)


def plausible(value: float, spec: FieldSpec) -> bool:
    if spec.unit == "USD/shares":
        return abs(value) <= PER_SHARE_MAX_ABS
    if not spec.negative_ok and value < 0:
        return False
    return True


def extract_field(doc: PreparedDocument, spec: FieldSpec, report_date: str) -> Extraction:
    table = find_statement_table(doc, spec.statement)
    if table is None:
        return Extraction(spec.key, None, "statement_not_found")

    column, period, basis = resolve_columns(table, report_date)
    if column is None:
        return Extraction(spec.key, None, "column_unresolved", table.index,
                          column_basis=basis)

    hits = match_rows(table, spec)
    if not hits:
        return Extraction(spec.key, None, "row_not_found", table.index,
                          scale=table.scale, column_index=column,
                          column_period=period, column_basis=basis)

    for _, row_index, label in hits:
        values = row_values(table.rows[row_index])
        if len(values) <= column:
            continue
        value = values[column]
        if value is None:
            continue
        if spec.scaled:
            value = value * table.scale
        if not plausible(value, spec):
            continue
        return Extraction(spec.key, value, "ok", table.index, label,
                          table.scale, column, period, basis)

    return Extraction(spec.key, None, "no_value_in_row", table.index,
                      matched_label=hits[0][2], scale=table.scale,
                      column_index=column, column_period=period,
                      column_basis=basis)


def extract_document(doc: PreparedDocument, report_date: str) -> list[Extraction]:
    return [extract_field(doc, spec, report_date) for spec in load_fields().values()]
