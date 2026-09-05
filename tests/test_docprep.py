"""The stripping step is the load bearing decision, so it is tested hardest."""

from filingbench.docprep import (
    count_inline_xbrl_facts,
    detect_scale,
    detect_statement,
    parse_header_dates,
    parse_header_years,
    parse_number,
    prepare,
    row_values,
    strip_inline_xbrl,
)

IX_SNIPPET = """
<html><body>
<ix:header><ix:hidden><ix:nonFraction name="us-gaap:Assets" contextRef="c1"
 unitRef="usd" scale="6" decimals="-6">364980</ix:nonFraction></ix:hidden></ix:header>
<p>CONSOLIDATED BALANCE SHEETS (In millions)</p>
<table><tr><td>Total assets</td><td>$</td>
<td><ix:nonFraction name="us-gaap:Assets" contextRef="c2" unitRef="usd"
 scale="6" decimals="-6">364,980</ix:nonFraction></td></tr></table>
</body></html>
"""


def test_stripping_removes_every_concept_name():
    stripped = strip_inline_xbrl(IX_SNIPPET)
    assert "us-gaap:Assets" not in stripped
    assert "ix:nonFraction" not in stripped
    assert "364,980" in stripped, "the visible figure must survive"


def test_hidden_header_facts_are_dropped_entirely():
    stripped = strip_inline_xbrl(IX_SNIPPET)
    assert "364980" not in stripped, "the unformatted hidden fact must not leak"


def test_fact_count_is_reported_before_stripping():
    assert count_inline_xbrl_facts(IX_SNIPPET) == 2


def test_prepare_keeps_the_visible_table():
    doc = prepare(IX_SNIPPET, doc_id="t")
    assert len(doc.tables) == 1
    assert doc.tables[0].scale == 1_000_000
    assert doc.tables[0].statement == "balance_sheet"
    assert row_values(doc.tables[0].rows[0]) == [364980.0]


def test_parse_number_handles_statement_conventions():
    assert parse_number("391,035") == 391035.0
    assert parse_number("(565)") == -565.0
    assert parse_number("$1,234") == 1234.0
    assert parse_number("6.08") == 6.08
    assert parse_number("not a number") is None
    assert parse_number("") is None


def test_row_values_reassembles_figures_split_across_cells():
    cells = ["Other income/(expense), net", "$", "269", "(", "565", ")", "(", "334", ")"]
    assert row_values(cells) == [269.0, -565.0, -334.0]


def test_scale_ignores_a_share_count_qualifier():
    caption = ("(In millions, except number of shares, which are reflected in "
               "thousands, and per-share amounts)")
    assert detect_scale(caption)[0] == 1_000_000
    assert detect_scale("(shares in thousands)")[0] == 1
    assert detect_scale("no scale stated here")[0] == 1
    assert detect_scale("(dollars in thousands)")[0] == 1_000


def test_statement_detection():
    assert detect_statement("CONSOLIDATED STATEMENTS OF OPERATIONS") == "income_statement"
    assert detect_statement("CONSOLIDATED BALANCE SHEETS") == "balance_sheet"
    assert detect_statement("CONSOLIDATED STATEMENTS OF CASH FLOWS") == "cash_flow"
    assert detect_statement("Note 4 Income Taxes") is None


def test_header_dates_and_years():
    rows = [["", "September 28, 2024", "September 30, 2023", "September 24, 2022"]]
    assert parse_header_dates(rows) == [(0, "2024-09-28"), (1, "2023-09-30"), (2, "2022-09-24")]
    assert parse_header_years([["", "2024", "2023", "2022"]]) == [(0, 2024), (1, 2023), (2, 2022)]
